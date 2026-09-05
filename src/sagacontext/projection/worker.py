from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict

from ..backends import (
    BackendAdapter,
    BackendDefiniteError,
    BackendHit,
    BackendUnknownError,
    BackendVerificationTimeout,
    Projection,
)
from ..ledger import Ledger


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


class ProjectionClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    outbox_id: int
    action: str
    operation_key: str
    attempt_no: int
    lease_owner: str
    lease_token: str
    lease_until: datetime
    source_status: Literal["pending", "retry", "unknown"]
    projection: Projection


class ProjectionRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal[
        "idle", "confirmed", "unknown", "retry", "obsolete", "blocked", "fenced"
    ]
    outbox_id: int | None = None
    recovered: bool = False


class Projector:
    def __init__(self, ledger: Ledger):
        self.ledger = ledger

    def drain_once(
        self,
        backend: BackendAdapter,
        *,
        worker_id: str,
        now: datetime,
        backend_timeout: timedelta,
        local_completion_margin: timedelta,
        lease_duration: timedelta,
        verification_timeout: timedelta,
        max_attempts: int = 3,
    ) -> ProjectionRunResult:
        if verification_timeout <= timedelta(0):
            raise ValueError("verification_timeout must be positive")
        started = time.monotonic()
        def observed_now():
            return now + timedelta(seconds=time.monotonic() - started)

        configured_timeout = getattr(backend, "timeout", None)
        if configured_timeout is not None and configured_timeout > min(
            backend_timeout.total_seconds(), verification_timeout.total_seconds()
        ):
            raise ValueError("adapter timeout exceeds worker timeout budget")
        claim = self.claim_next(
            backend,
            worker_id=worker_id,
            now=now,
            lease_duration=lease_duration,
            backend_timeout=backend_timeout,
            local_completion_margin=local_completion_margin,
        )
        if claim is None:
            return ProjectionRunResult(status="idle")
        receipt = self.ledger.db.execute(
            "SELECT backend_locator FROM projection_receipts WHERE operation_key=?",
            (claim.operation_key,),
        ).fetchone()
        if receipt and claim.action != "delete":
            return self.complete(
                claim, backend, receipt["backend_locator"], recovered=True, now=observed_now(), max_attempts=max_attempts
            )
        if claim.source_status == "unknown":
            return self._verify_unknown(claim, backend, max_attempts, observed_now())
        if claim.action == "upsert" and not self._is_current(claim.projection):
            return self._finish_without_locator(
                claim, "obsolete", "revision_not_current", now
            )
        try:
            locator = self.call_backend(claim, backend, now=observed_now())
        except BackendDefiniteError:
            status = "blocked" if claim.attempt_no >= max_attempts else "retry"
            return self._finish_without_locator(
                claim, status, "backend_definite_error", observed_now()
            )
        except BackendUnknownError:
            return self._finish_without_locator(
                claim, "unknown", "backend_result_unknown", observed_now()
            )
        except BackendVerificationTimeout:
            status = "blocked" if claim.attempt_no >= max_attempts else "unknown"
            return self._finish_without_locator(claim, status, "verification_timeout", observed_now())
        return self.complete(claim, backend, locator, now=observed_now(), max_attempts=max_attempts)

    def claim_next(
        self,
        backend: BackendAdapter,
        *,
        worker_id: str,
        now: datetime,
        lease_duration: timedelta,
        backend_timeout: timedelta,
        local_completion_margin: timedelta,
    ) -> ProjectionClaim | None:
        if lease_duration <= backend_timeout + local_completion_margin:
            raise ValueError("lease_duration must exceed backend_timeout plus completion margin")
        backend_name = backend.capabilities().backend
        now = now.astimezone(timezone.utc)
        now_text = now.isoformat()
        with self.ledger._write_transaction():
            expired = self.ledger.db.execute(
                "SELECT outbox_id FROM outbox WHERE backend=? AND status='running' "
                "AND lease_until<=? "
                "ORDER BY outbox_id",
                (backend_name, now_text),
            ).fetchall()
            for row in expired:
                attempt = self.ledger.db.execute(
                    "SELECT attempt_id,call_started_at FROM projection_attempts "
                    "WHERE outbox_id=? ORDER BY attempt_no DESC LIMIT 1",
                    (row["outbox_id"],),
                ).fetchone()
                status = "unknown" if attempt and attempt["call_started_at"] else "retry"
                result_status = (
                    "lease_expired_after_call" if status == "unknown" else "lease_expired_before_call"
                )
                if attempt:
                    self.ledger.db.execute(
                        "UPDATE projection_attempts SET result_status=? WHERE attempt_id=?",
                        (result_status, attempt["attempt_id"]),
                    )
                self.ledger.db.execute(
                    "UPDATE outbox SET status=?,lease_owner=NULL,lease_token=NULL,lease_until=NULL,"
                    "updated_at=? WHERE outbox_id=? AND status='running'",
                    (status, now_text, row["outbox_id"]),
                )
            row = self.ledger.db.execute(
                "SELECT * FROM outbox WHERE backend=? AND "
                "(status='unknown' OR (status IN ('pending','retry') "
                "AND (next_attempt_at IS NULL OR next_attempt_at<=?))) "
                "ORDER BY CASE status WHEN 'unknown' THEN 0 ELSE 1 END,outbox_id LIMIT 1",
                (backend_name, now_text),
            ).fetchone()
            if not row:
                return None
            projection = self._projection(row)
            operation_key = self._operation_key(row)
            token = str(uuid.uuid4())
            attempt_no = row["attempt_count"] + 1
            lease_until = now + lease_duration
            changed = self.ledger.db.execute(
                "UPDATE outbox SET status='running',lease_owner=?,lease_token=?,lease_until=?,"
                "attempt_count=?,updated_at=? WHERE outbox_id=? AND status=?",
                (
                    worker_id,
                    token,
                    lease_until.isoformat(),
                    attempt_no,
                    now_text,
                    row["outbox_id"],
                    row["status"],
                ),
            )
            if changed.rowcount != 1:
                return None
            self.ledger.db.execute(
                "INSERT INTO projection_attempts(attempt_id,outbox_id,operation_key,attempt_no,"
                "started_at,lease_owner,lease_token) VALUES (?,?,?,?,?,?,?)",
                (
                    str(uuid.uuid4()),
                    row["outbox_id"],
                    operation_key,
                    attempt_no,
                    now_text,
                    worker_id,
                    token,
                ),
            )
        return ProjectionClaim(
            outbox_id=row["outbox_id"],
            action=row["action"],
            operation_key=operation_key,
            attempt_no=attempt_no,
            lease_owner=worker_id,
            lease_token=token,
            lease_until=lease_until,
            source_status=row["status"],
            projection=projection,
        )

    def call_backend(
        self, claim: ProjectionClaim, backend: BackendAdapter, *, now: datetime
    ) -> str:
        with self.ledger._write_transaction():
            if not self._owns(claim, now):
                raise ValueError("projection_lease_fenced")
            self.ledger.db.execute(
                "UPDATE projection_attempts SET call_started_at=? WHERE outbox_id=? "
                "AND attempt_no=? AND lease_token=?",
                (_now(), claim.outbox_id, claim.attempt_no, claim.lease_token),
            )
        if claim.action == "delete":
            row = self.ledger.db.execute(
                "SELECT target_locator FROM outbox WHERE outbox_id=?", (claim.outbox_id,)
            ).fetchone()
            locator = row["target_locator"] if row else None
            if not locator:
                locator = backend.locate_projection(
                    claim.projection.memory_id, claim.projection.revision, claim.projection.generation
                )
            if locator:
                backend.remove_projection([locator])
            return locator or ""
        if claim.action != "upsert":
            raise BackendDefiniteError("unsupported projection action")
        return backend.materialize(claim.projection, claim.operation_key)

    def complete(
        self,
        claim: ProjectionClaim,
        backend: BackendAdapter,
        locator: str,
        *,
        recovered: bool = False,
        now: datetime,
        max_attempts: int = 3,
    ) -> ProjectionRunResult:
        if not self._owns(claim, now):
            return ProjectionRunResult(status="fenced", outbox_id=claim.outbox_id)
        started = time.monotonic()
        try:
            observed = backend.inspect_projection(locator) if locator else None
        except (BackendVerificationTimeout, BackendUnknownError):
            return self._finish_without_locator(
                claim, "blocked" if claim.attempt_no >= max_attempts else "unknown", "verification_timeout",
                now + timedelta(seconds=time.monotonic() - started),
            )
        except BackendDefiniteError:
            return self._finish_without_locator(
                claim, "blocked", "projection_identity_mismatch",
                now + timedelta(seconds=time.monotonic() - started),
            )
        now = now + timedelta(seconds=time.monotonic() - started)
        backend_name = backend.capabilities().backend
        with self.ledger._write_transaction():
            if not self._owns(claim, now):
                return ProjectionRunResult(status="fenced", outbox_id=claim.outbox_id)
            head = self.ledger.db.execute(
                "SELECT state FROM memories WHERE memory_id=? AND owner_id=?",
                (claim.projection.memory_id, self.ledger.owner_id),
            ).fetchone()
            # Deletion erased the Ledger body. Retain only a validated late projection's
            # identity for a cleanup receipt; it must never become current again.
            if claim.action == "upsert" and head and head["state"] == "deleted" and observed:
                fields = ("owner_id", "memory_id", "revision", "generation")
                if all(getattr(observed, key) == getattr(claim.projection, key) for key in fields):
                    claim = claim.model_copy(update={"projection": observed})
            projection_matches = (
                observed is None
                if claim.action == "delete"
                else self._matches(claim.projection, observed)
            )
            if not projection_matches:
                self._finish_owned(claim, "blocked", "projection_identity_mismatch")
                return ProjectionRunResult(status="blocked", outbox_id=claim.outbox_id)
            current = claim.action == "delete" or self._is_current(claim.projection)
            receipt = self.ledger.db.execute(
                "SELECT * FROM projection_receipts WHERE operation_key=?",
                (claim.operation_key,),
            ).fetchone()
            if receipt:
                if (
                    receipt["backend"] != backend_name
                    or receipt["generation"] != claim.projection.generation
                    or receipt["memory_id"] != claim.projection.memory_id
                    or receipt["revision"] != claim.projection.revision
                    or receipt["payload_digest"] != claim.projection.payload_digest
                    or receipt["backend_locator"] != locator
                ):
                    self._finish_owned(claim, "blocked", "receipt_identity_mismatch")
                    return ProjectionRunResult(status="blocked", outbox_id=claim.outbox_id)
                receipt_id = receipt["receipt_id"]
            else:
                receipt_id = str(uuid.uuid4())
                self.ledger.db.execute(
                    "INSERT INTO projection_receipts VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        receipt_id,
                        claim.operation_key,
                        claim.action,
                        backend_name,
                        claim.projection.generation,
                        claim.projection.memory_id,
                        claim.projection.revision,
                        locator,
                        claim.projection.payload_digest,
                        _now(),
                    ),
                )
            status = "confirmed" if current else "obsolete"
            if status == "obsolete" and claim.action == "upsert":
                self.ledger.db.execute(
                    "INSERT INTO outbox(backend,generation,memory_id,revision,action,"
                    "status,target_locator,created_at,updated_at) VALUES (?,?,?,?,?,'pending',?,?,?) "
                    "ON CONFLICT(backend,generation,memory_id,revision,action) DO UPDATE SET "
                    "status='pending',target_locator=excluded.target_locator,confirmed_receipt_id=NULL,"
                    "lease_owner=NULL,lease_token=NULL,lease_until=NULL",
                    (
                        backend_name,
                        claim.projection.generation,
                        claim.projection.memory_id,
                        claim.projection.revision,
                        "delete",
                        locator,
                        _now(),
                        _now(),
                    ),
                )
            self.ledger.db.execute(
                "UPDATE projection_attempts SET call_finished_at=?,result_status=?,"
                "observed_locator=? WHERE outbox_id=? AND attempt_no=? AND lease_token=?",
                (
                    _now(),
                    status,
                    locator,
                    claim.outbox_id,
                    claim.attempt_no,
                    claim.lease_token,
                ),
            )
            changed = self.ledger.db.execute(
                "UPDATE outbox SET status=?,confirmed_receipt_id=?,lease_owner=NULL,"
                "lease_token=NULL,lease_until=NULL,last_error_class=NULL,unknown_reason=NULL,"
                "updated_at=? WHERE outbox_id=? AND status='running' AND lease_owner=? "
                "AND lease_token=?",
                (
                    status,
                    receipt_id,
                    _now(),
                    claim.outbox_id,
                    claim.lease_owner,
                    claim.lease_token,
                ),
            )
            if changed.rowcount != 1:
                raise ValueError("projection_lease_fenced")
        return ProjectionRunResult(
            status=status, outbox_id=claim.outbox_id, recovered=recovered
        )

    def filter_current_hits(self, hits: list[BackendHit]) -> list[BackendHit]:
        result = []
        for hit in hits:
            row = self.ledger.db.execute(
                "SELECT current_revision,state FROM memories WHERE memory_id=? AND owner_id=?",
                (hit.memory_id, self.ledger.owner_id),
            ).fetchone()
            if row and row["state"] == "active" and row["current_revision"] == hit.revision:
                result.append(hit)
        return result

    def _verify_unknown(
        self,
        claim: ProjectionClaim,
        backend: BackendAdapter,
        max_attempts: int,
        now: datetime,
    ) -> ProjectionRunResult:
        started = time.monotonic()
        def observed_now():
            return now + timedelta(seconds=time.monotonic() - started)

        try:
            self._mark_call_started(claim, now)
            if claim.action == "delete":
                row = self.ledger.db.execute(
                    "SELECT target_locator FROM outbox WHERE outbox_id=?", (claim.outbox_id,)
                ).fetchone()
                locator = row["target_locator"] or backend.locate_projection(
                    claim.projection.memory_id, claim.projection.revision, claim.projection.generation
                )
                if not locator or backend.inspect_projection(locator) is None:
                    return self.complete(claim, backend, locator or "", recovered=True, now=observed_now(), max_attempts=max_attempts)
                return self._finish_without_locator(claim, "blocked" if claim.attempt_no >= max_attempts else "retry", "delete_still_present", observed_now())
            locator = backend.locate_projection(
                claim.projection.memory_id,
                claim.projection.revision,
                claim.projection.generation,
                claim.operation_key,
            )
        except (BackendVerificationTimeout, BackendUnknownError):
            status = "blocked" if claim.attempt_no >= max_attempts else "unknown"
            return self._finish_without_locator(
                claim, status, "verification_timeout", observed_now()
            )
        except BackendDefiniteError:
            return self._finish_without_locator(claim, "blocked", "verification_rejected", observed_now())
        if locator is None:
            capabilities = backend.capabilities()
            status = (
                "retry"
                if claim.attempt_no < max_attempts and capabilities.stable_id_mapping and capabilities.enumerable_managed_area
                else "blocked"
            )
            return self._finish_without_locator(
                claim, status, "projection_not_found", observed_now()
            )
        return self.complete(claim, backend, locator, recovered=True, now=observed_now(), max_attempts=max_attempts)

    def _mark_call_started(self, claim: ProjectionClaim, now: datetime) -> None:
        with self.ledger._write_transaction():
            if not self._owns(claim, now):
                raise ValueError("projection_lease_fenced")
            self.ledger.db.execute(
                "UPDATE projection_attempts SET call_started_at=? WHERE outbox_id=? "
                "AND attempt_no=? AND lease_token=?",
                (_now(), claim.outbox_id, claim.attempt_no, claim.lease_token),
            )

    def _finish_without_locator(
        self,
        claim: ProjectionClaim,
        status: str,
        error_class: str,
        now: datetime,
    ) -> ProjectionRunResult:
        with self.ledger._write_transaction():
            if not self._owns(claim, now):
                return ProjectionRunResult(status="fenced", outbox_id=claim.outbox_id)
            self._finish_owned(claim, status, error_class)
        return ProjectionRunResult(status=status, outbox_id=claim.outbox_id)

    def _finish_owned(self, claim: ProjectionClaim, status: str, error_class: str) -> None:
        self.ledger.db.execute(
            "UPDATE projection_attempts SET call_finished_at=?,result_status=?,error_class=? "
            "WHERE outbox_id=? AND attempt_no=? AND lease_token=?",
            (
                _now(),
                status,
                error_class,
                claim.outbox_id,
                claim.attempt_no,
                claim.lease_token,
            ),
        )
        changed = self.ledger.db.execute(
            "UPDATE outbox SET status=?,lease_owner=NULL,lease_token=NULL,lease_until=NULL,"
            "last_error_class=?,unknown_reason=?,updated_at=? WHERE outbox_id=? "
            "AND status='running' AND lease_owner=? AND lease_token=?",
            (
                status,
                error_class,
                error_class if status == "unknown" else None,
                _now(),
                claim.outbox_id,
                claim.lease_owner,
                claim.lease_token,
            ),
        )
        if changed.rowcount != 1:
            raise ValueError("projection_lease_fenced")

    def _owns(self, claim: ProjectionClaim, now: datetime) -> bool:
        row = self.ledger.db.execute(
            "SELECT 1 FROM outbox WHERE outbox_id=? AND status='running' "
            "AND lease_owner=? AND lease_token=? AND lease_until>?",
            (
                claim.outbox_id,
                claim.lease_owner,
                claim.lease_token,
                now.astimezone(timezone.utc).isoformat(),
            ),
        ).fetchone()
        return row is not None

    def _projection(self, outbox) -> Projection:
        revision = self.ledger.db.execute(
            "SELECT m.owner_id,m.memory_type,m.scope_json,r.payload_json FROM memories m "
            "JOIN revisions r ON r.memory_id=m.memory_id AND r.revision=? "
            "WHERE m.memory_id=? AND m.owner_id=?",
            (outbox["revision"], outbox["memory_id"], self.ledger.owner_id),
        ).fetchone()
        if not revision:
            raise ValueError("projection_revision_missing")
        scope = json.loads(revision["scope_json"])
        tags = [f"owner:{revision['owner_id']}", f"scope:{scope['kind']}"]
        for key in ("project_id", "task_id", "path_pattern"):
            if scope.get(key):
                tags.append(f"{key}:{scope[key]}")
        searchable_text = _canonical(json.loads(revision["payload_json"]))
        normalized = {
            "owner_id": revision["owner_id"],
            "memory_id": outbox["memory_id"],
            "revision": outbox["revision"],
            "generation": outbox["generation"],
            "memory_type": revision["memory_type"],
            "searchable_text": searchable_text,
            "scope_filter_tags": tags,
        }
        return Projection(
            **normalized,
            payload_digest=hashlib.sha256(_canonical(normalized).encode()).hexdigest(),
        )

    @staticmethod
    def _operation_key(outbox) -> str:
        identity = {
            "action": outbox["action"],
            "backend": outbox["backend"],
            "generation": outbox["generation"],
            "memory_id": outbox["memory_id"],
            "revision": outbox["revision"],
            "target_locator": outbox["target_locator"],
        }
        return hashlib.sha256(_canonical(identity).encode()).hexdigest()

    def _is_current(self, projection: Projection) -> bool:
        row = self.ledger.db.execute(
            "SELECT current_revision,state FROM memories WHERE memory_id=? AND owner_id=?",
            (projection.memory_id, self.ledger.owner_id),
        ).fetchone()
        return bool(
            row and row["state"] == "active" and row["current_revision"] == projection.revision
        )

    @staticmethod
    def _matches(expected: Projection, observed: Projection | None) -> bool:
        return bool(
            observed
            and observed.owner_id == expected.owner_id
            and observed.memory_id == expected.memory_id
            and observed.revision == expected.revision
            and observed.generation == expected.generation
            and observed.payload_digest == expected.payload_digest
        )
