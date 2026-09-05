from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .models import (
    BatchCommitResult,
    CommitBatchPlan,
    CommitRequest,
    CommitResult,
    MemoryView,
    Scope,
    TaskContext,
)
from .schema import MIGRATION_1, MIGRATION_2, SCHEMA_VERSION


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


_V1_TABLES = {
    "backend_generations",
    "commit_receipts",
    "deletion_jobs",
    "evidence",
    "ledger_meta",
    "memories",
    "outbox",
    "owners",
    "project_locations",
    "projects",
    "revision_evidence",
    "revisions",
    "sessions",
    "suppression_rules",
    "task_bindings",
    "tasks",
}

_V2_TABLES = {
    "batch_anchors",
    "batch_candidates",
    "batch_events",
    "batches",
    "candidates",
    "conflicts",
    "event_aliases",
    "event_quarantine",
    "events",
    "projection_attempts",
    "projection_receipts",
    "proposals",
    "review_receipts",
    "source_cursors",
}

_V2_COLUMNS = {
    "batches": {
        "event_upper_sequence",
        "policy_version",
        "maintenance_schema_version",
        "judge_version",
        "input_digest",
        "lease_token",
        "lease_until",
    },
    "outbox": {
        "lease_owner",
        "lease_token",
        "lease_until",
        "attempt_count",
        "next_attempt_at",
        "last_error_class",
        "unknown_reason",
        "confirmed_receipt_id",
        "target_locator",
        "updated_at",
    },
    "projection_attempts": {"outbox_id", "operation_key", "attempt_no", "lease_token"},
    "projection_receipts": {
        "operation_key",
        "generation",
        "backend_locator",
        "payload_digest",
    },
    "proposals": {"input_digest", "output_digest", "source_kind", "status"},
}


class Ledger:
    def __init__(self, path: Path, owner_id: str | None = None):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=2000")
        try:
            self._migrate()
        except Exception:
            self.db.close()
            raise
        row = self.db.execute("SELECT owner_id FROM owners ORDER BY created_at LIMIT 1").fetchone()
        self.owner_id = owner_id or (row[0] if row else str(uuid.uuid4()))
        self.db.execute("INSERT OR IGNORE INTO owners(owner_id,created_at) VALUES (?,?)", (self.owner_id, _now()))

    def close(self) -> None:
        self.db.close()

    def _migrate(self) -> None:
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations("
            "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        applied = {row[0] for row in self.db.execute("SELECT version FROM schema_migrations")}
        if applied:
            latest = max(applied)
            if latest > SCHEMA_VERSION or applied != set(range(1, latest + 1)):
                raise RuntimeError("unsupported schema migration history")
            self._validate_schema(latest)
        else:
            tables = self._user_tables()
            if tables != {"schema_migrations"}:
                raise RuntimeError("incomplete schema v0")
        for version, migration in ((1, MIGRATION_1), (2, MIGRATION_2)):
            if version in applied:
                continue
            try:
                self.db.execute("BEGIN IMMEDIATE")
                for statement in self._migration_statements(migration):
                    self.db.execute(statement)
                self._validate_schema(version)
                self.db.execute(
                    "INSERT INTO schema_migrations(version,applied_at) VALUES (?,?)",
                    (version, _now()),
                )
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise
            applied.add(version)
        if applied != set(range(1, SCHEMA_VERSION + 1)):
            raise RuntimeError("unsupported schema version")
        self._validate_schema(SCHEMA_VERSION)

    @staticmethod
    def _migration_statements(script: str) -> Iterator[str]:
        pending = ""
        for line in script.splitlines(keepends=True):
            pending += line
            if sqlite3.complete_statement(pending):
                statement = pending.strip()
                if statement:
                    yield statement
                pending = ""
        if pending.strip():
            raise sqlite3.DatabaseError("incomplete migration statement")

    def _user_tables(self) -> set[str]:
        return {
            row[0]
            for row in self.db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }

    def _validate_schema(self, version: int) -> None:
        required_tables = set(_V1_TABLES)
        if version >= 2:
            required_tables.update(_V2_TABLES)
        missing_tables = required_tables - self._user_tables()
        if missing_tables:
            raise RuntimeError(f"incomplete schema v{version}: missing tables")
        if version >= 2:
            for table, required_columns in _V2_COLUMNS.items():
                columns = {
                    row[1] for row in self.db.execute(f"PRAGMA table_info({table})")
                }
                if required_columns - columns:
                    raise RuntimeError(
                        f"incomplete schema v{version}: missing columns in {table}"
                    )

    @contextmanager
    def _write_transaction(self) -> Iterator[None]:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except Exception:
            self.db.rollback()
            raise
        else:
            self.db.commit()

    def register_project(self, name: str, location: Path) -> dict[str, str]:
        realpath = str(location.resolve(strict=True))
        existing = self.db.execute(
            "SELECT project_id,workspace_id FROM project_locations WHERE owner_id=? AND realpath=?",
            (self.owner_id, realpath),
        ).fetchone()
        if existing:
            return dict(existing)
        project_id, workspace_id = str(uuid.uuid4()), str(uuid.uuid4())
        with self._write_transaction():
            self.db.execute(
                "INSERT INTO projects(project_id,owner_id,name,created_at) VALUES (?,?,?,?)",
                (project_id, self.owner_id, name, _now()),
            )
            self.db.execute(
                "INSERT INTO project_locations(location_id,owner_id,project_id,workspace_id,realpath) VALUES (?,?,?,?,?)",
                (str(uuid.uuid4()), self.owner_id, project_id, workspace_id, realpath),
            )
        return {"project_id": project_id, "workspace_id": workspace_id}

    def bind_location(self, project_id: str, location: Path) -> str:
        self._require_project(project_id)
        realpath = str(location.resolve(strict=True))
        existing = self.db.execute(
            "SELECT project_id,workspace_id FROM project_locations WHERE owner_id=? AND realpath=?",
            (self.owner_id, realpath),
        ).fetchone()
        if existing:
            if existing["project_id"] != project_id:
                raise ValueError("location already belongs to another project")
            return existing["workspace_id"]
        workspace_id = str(uuid.uuid4())
        with self._write_transaction():
            self.db.execute(
                "INSERT INTO project_locations(location_id,owner_id,project_id,workspace_id,realpath) VALUES (?,?,?,?,?)",
                (str(uuid.uuid4()), self.owner_id, project_id, workspace_id, realpath),
            )
        return workspace_id

    def resolve_project(self, location: Path) -> dict[str, str] | None:
        realpath = location.resolve(strict=True)
        matches = []
        for row in self.db.execute(
            "SELECT project_id,workspace_id,realpath FROM project_locations WHERE owner_id=?", (self.owner_id,)
        ):
            root = Path(row["realpath"])
            if realpath == root or realpath.is_relative_to(root):
                matches.append((len(root.parts), row))
        if not matches:
            return None
        row = max(matches, key=lambda item: item[0])[1]
        return {"project_id": row["project_id"], "workspace_id": row["workspace_id"], "root": row["realpath"]}

    def rebind_location(self, project_id: str, old_location: Path, new_location: Path) -> str:
        old_path = str(old_location.resolve(strict=False))
        new_path = str(new_location.resolve(strict=True))
        with self._write_transaction():
            row = self.db.execute(
                "SELECT workspace_id FROM project_locations WHERE owner_id=? AND project_id=? AND realpath=?",
                (self.owner_id, project_id, old_path),
            ).fetchone()
            if not row:
                raise ValueError("old location is not bound to project")
            self.db.execute(
                "UPDATE project_locations SET realpath=? WHERE owner_id=? AND project_id=? AND realpath=?",
                (new_path, self.owner_id, project_id, old_path),
            )
        return row["workspace_id"]

    def create_task(self, project_id: str, goal: str) -> str:
        self._require_project(project_id)
        task_id, now = str(uuid.uuid4()), _now()
        with self._write_transaction():
            self.db.execute(
                "INSERT INTO tasks(task_id,owner_id,project_id,goal,status,created_at,last_active) VALUES (?,?,?,?,?,?,?)",
                (task_id, self.owner_id, project_id, goal, "active", now, now),
            )
        return task_id

    def open_session(self, host: str, host_session_id: str, workspace_id: str) -> str:
        location = self.db.execute(
            "SELECT 1 FROM project_locations WHERE owner_id=? AND workspace_id=?",
            (self.owner_id, workspace_id),
        ).fetchone()
        if not location:
            raise ValueError("unknown workspace")
        existing = self.db.execute(
            "SELECT session_id,workspace_id FROM sessions WHERE owner_id=? AND host=? AND host_session_id=?",
            (self.owner_id, host, host_session_id),
        ).fetchone()
        if existing:
            if existing["workspace_id"] != workspace_id:
                raise ValueError("host session is already bound to another workspace")
            return existing["session_id"]
        session_id = str(uuid.uuid4())
        with self._write_transaction():
            self.db.execute(
                "INSERT INTO sessions(session_id,owner_id,host,host_session_id,workspace_id,opened_at) VALUES (?,?,?,?,?,?)",
                (session_id, self.owner_id, host, host_session_id, workspace_id, _now()),
            )
        return session_id

    def bind_task(self, session_id: str, task_id: str, start_event_id: str) -> str:
        row = self.db.execute(
            "SELECT t.project_id,pl.project_id AS workspace_project FROM sessions s "
            "JOIN project_locations pl ON pl.workspace_id=s.workspace_id AND pl.owner_id=s.owner_id "
            "JOIN tasks t ON t.task_id=? AND t.owner_id=s.owner_id "
            "WHERE s.session_id=? AND s.owner_id=?",
            (task_id, session_id, self.owner_id),
        ).fetchone()
        if not row or row["project_id"] != row["workspace_project"]:
            raise ValueError("task and session must belong to the same project")
        binding_id = str(uuid.uuid4())
        with self._write_transaction():
            self.db.execute(
                "UPDATE task_bindings SET end_event_id=? WHERE session_id=? AND end_event_id IS NULL",
                (start_event_id, session_id),
            )
            self.db.execute(
                "INSERT INTO task_bindings VALUES (?,?,?,?,?,?,?)",
                (binding_id, self.owner_id, session_id, task_id, start_event_id, None, _now()),
            )
        return binding_id

    def current_task(self, session_id: str) -> str | None:
        row = self.db.execute(
            "SELECT task_id FROM task_bindings WHERE owner_id=? AND session_id=? AND end_event_id IS NULL",
            (self.owner_id, session_id),
        ).fetchone()
        return row["task_id"] if row else None

    def commit(self, request: CommitRequest) -> CommitResult:
        digest = hashlib.sha256(_canonical(request.model_dump(mode="json")).encode()).hexdigest()
        old_receipt = self.db.execute(
            "SELECT request_digest,result_json FROM commit_receipts WHERE owner_id=? AND receipt=?",
            (self.owner_id, request.receipt),
        ).fetchone()
        if old_receipt:
            if old_receipt["request_digest"] != digest:
                return CommitResult(status="rejected", ledger_sequence=self.sequence, reason="receipt_reused")
            return CommitResult.model_validate_json(old_receipt["result_json"])

        self._validate_scope(request.scope)
        memory_id = request.memory_id or str(uuid.uuid4())
        with self._write_transaction():
            scope_json = _canonical(request.scope.model_dump())
            topic_digest = self._topic_digest(request.memory_type, scope_json, _canonical(request.payload))
            source_ids = sorted(
                {self._source_claim_digest(item.source_event_id, item.claim_key) for item in request.evidence}
            )
            source_clause = ""
            source_values: list[str] = []
            if source_ids:
                source_clause = f" OR source_claim_digest IN ({','.join('?' for _ in source_ids)})"
                source_values = source_ids
            suppressed = self.db.execute(
                f"SELECT 1 FROM suppression_rules WHERE owner_id=? AND (topic_digest=?{source_clause})",
                (self.owner_id, topic_digest, *source_values),
            ).fetchone()
            if request.operation == "new" and suppressed:
                result = CommitResult(
                    status="rejected", ledger_sequence=self.sequence, reason="suppressed_after_deletion"
                )
                self._save_receipt(request.receipt, digest, result)
                return result
            current = self.db.execute(
                "SELECT * FROM memories WHERE memory_id=? AND owner_id=?", (memory_id, self.owner_id)
            ).fetchone()
            if request.operation == "new":
                if current:
                    return self._store_conflict_receipt(request, digest, "memory_exists")
                revision = 1
            else:
                if not current or current["state"] != "active":
                    return self._store_conflict_receipt(request, digest, "missing_or_inactive")
                if current["current_revision"] != request.expected_revision:
                    return self._store_conflict_receipt(request, digest, "revision_changed")
                if current["memory_type"] != request.memory_type or current["scope_json"] != _canonical(request.scope.model_dump()):
                    return self._store_conflict_receipt(request, digest, "identity_changed")
                if request.operation == "confirm":
                    previous = self.db.execute(
                        "SELECT payload_json FROM revisions WHERE memory_id=? AND revision=?",
                        (memory_id, current["current_revision"]),
                    ).fetchone()
                    if not request.evidence:
                        return self._store_conflict_receipt(request, digest, "confirmation_requires_evidence")
                    if previous["payload_json"] != _canonical(request.payload):
                        return self._store_conflict_receipt(request, digest, "confirmation_cannot_change_payload")
                revision = current["current_revision"] + 1

            sequence = self._next_sequence()
            if current:
                changed = self.db.execute(
                    "UPDATE memories SET current_revision=?,state=?,ledger_sequence=? "
                    "WHERE memory_id=? AND current_revision=?",
                    (
                        revision,
                        "retired" if request.operation == "supersede" else "active",
                        sequence,
                        memory_id,
                        request.expected_revision,
                    ),
                )
                if changed.rowcount != 1:
                    raise RuntimeError("concurrent head update")
            else:
                self.db.execute(
                    "INSERT INTO memories VALUES (?,?,?,?,?,'active','none',?)",
                    (memory_id, self.owner_id, revision, request.memory_type, scope_json, sequence),
                )
            self.db.execute(
                "INSERT INTO revisions VALUES (?,?,?,?,?,?,?)",
                (memory_id, revision, request.operation, request.payload_schema_version, _canonical(request.payload), _now(), request.source_kind),
            )
            self._attach_evidence(memory_id, revision, request)
            action = "delete" if request.operation == "supersede" else "upsert"
            projected = self._enqueue_projection(memory_id, revision, action)
            result = CommitResult(
                status="committed_pending_projection" if projected else "committed_local_only",
                memory_id=memory_id,
                revision=revision,
                ledger_sequence=sequence,
            )
            self._save_receipt(request.receipt, digest, result)
        return result

    def commit_batch(
        self,
        plan: CommitBatchPlan,
        lease_token: str,
        *,
        now: datetime | None = None,
    ) -> BatchCommitResult:
        now = now or datetime.now(timezone.utc)
        memory_ids: list[str] = []
        with self._write_transaction():
            batch = self.db.execute(
                "SELECT * FROM batches WHERE batch_id=? AND owner_id=?",
                (plan.batch_id, self.owner_id),
            ).fetchone()
            if (
                not batch
                or batch["lease_token"] != lease_token
                or batch["status"] not in {"proposed", "review_committing"}
                or not batch["lease_until"]
                or batch["lease_until"] <= now.astimezone(timezone.utc).isoformat()
            ):
                raise ValueError("batch_lease_fenced")
            proposal_ids = set(plan.proposal_ids)
            if len(proposal_ids) != len(plan.proposal_ids):
                raise ValueError("duplicate_proposal")
            if any(
                operation.proposal_id not in proposal_ids
                for operation in plan.memory_operations
            ):
                raise ValueError("operation_without_proposal")
            for proposal_id in plan.proposal_ids:
                proposal = self.db.execute(
                    "SELECT status,input_digest FROM proposals WHERE proposal_id=? AND batch_id=?",
                    (proposal_id, plan.batch_id),
                ).fetchone()
                if (
                    not proposal
                    or proposal["status"] != "proposed"
                    or proposal["input_digest"] != batch["input_digest"]
                ):
                    raise ValueError("proposal_not_committable")
            for result in plan.candidate_results:
                candidate = self.db.execute(
                    "SELECT status,active_batch_id,claim_token FROM candidates "
                    "WHERE candidate_id=? AND owner_id=?",
                    (result.candidate_id, self.owner_id),
                ).fetchone()
                if (
                    not candidate
                    or candidate["active_batch_id"] != plan.batch_id
                    or candidate["claim_token"] != result.claim_token
                    or candidate["status"] not in {"processing", "awaiting_review"}
                ):
                    raise ValueError("candidate_claim_fenced")
            for expected in plan.expected_heads:
                head = self.db.execute(
                    "SELECT current_revision,state FROM memories WHERE memory_id=? AND owner_id=?",
                    (expected.memory_id, self.owner_id),
                ).fetchone()
                if not head or head["state"] != "active" or head["current_revision"] != expected.revision:
                    raise ValueError("expected_head_changed")

            evidence_by_proposal = {
                link.proposal_id: link.evidence_ids for link in plan.evidence_links
            }
            for operation in plan.memory_operations:
                scope_json = _canonical(operation.scope.model_dump())
                self._validate_scope(operation.scope)
                if operation.operation == "new":
                    memory_id = str(
                        uuid.uuid5(uuid.NAMESPACE_URL, f"{self.owner_id}:{operation.proposal_id}")
                    )
                    current = None
                    revision = 1
                else:
                    memory_id = operation.memory_id or ""
                    current = self.db.execute(
                        "SELECT * FROM memories WHERE memory_id=? AND owner_id=?",
                        (memory_id, self.owner_id),
                    ).fetchone()
                    if (
                        not current
                        or current["state"] != "active"
                        or current["current_revision"] != operation.expected_revision
                        or current["memory_type"] != operation.memory_type
                        or current["scope_json"] != scope_json
                    ):
                        raise ValueError("operation_head_changed")
                    if operation.operation == "confirm":
                        previous = self.db.execute(
                            "SELECT payload_json FROM revisions WHERE memory_id=? AND revision=?",
                            (memory_id, current["current_revision"]),
                        ).fetchone()
                        if not evidence_by_proposal.get(operation.proposal_id):
                            raise ValueError("confirmation_requires_evidence")
                        if previous["payload_json"] != operation.payload_json:
                            raise ValueError("confirmation_cannot_change_payload")
                    revision = current["current_revision"] + 1
                sequence = self._next_sequence()
                if current:
                    changed = self.db.execute(
                        "UPDATE memories SET current_revision=?,state=?,ledger_sequence=? "
                        "WHERE memory_id=? AND current_revision=?",
                        (
                            revision,
                            "retired" if operation.operation == "supersede" else "active",
                            sequence,
                            memory_id,
                            operation.expected_revision,
                        ),
                    )
                    if changed.rowcount != 1:
                        raise ValueError("operation_head_changed")
                else:
                    self.db.execute(
                        "INSERT INTO memories VALUES (?,?,?,?,?,'active','none',?)",
                        (
                            memory_id,
                            self.owner_id,
                            revision,
                            operation.memory_type,
                            scope_json,
                            sequence,
                        ),
                    )
                self.db.execute(
                    "INSERT INTO revisions VALUES (?,?,?,?,?,?,?)",
                    (
                        memory_id,
                        revision,
                        operation.operation,
                        operation.payload_schema_version,
                        operation.payload_json,
                        _now(),
                        operation.source_kind,
                    ),
                )
                for event_id in evidence_by_proposal.get(operation.proposal_id, ()):
                    event = self.db.execute(
                        "SELECT event_id,event_kind,occurred_at FROM events "
                        "WHERE event_id=? AND owner_id=?",
                        (event_id, self.owner_id),
                    ).fetchone()
                    if not event:
                        raise ValueError("evidence_event_missing")
                    claim_key = operation.proposal_id
                    evidence_id = str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            f"{self.owner_id}:{event_id}:{claim_key}",
                        )
                    )
                    self.db.execute(
                        "INSERT OR IGNORE INTO evidence VALUES (?,?,?,?,?,?,?,?,?)",
                        (
                            evidence_id,
                            self.owner_id,
                            event_id,
                            claim_key,
                            event["event_kind"],
                            _canonical({"event_id": event_id}),
                            event["occurred_at"],
                            None,
                            None,
                        ),
                    )
                    self.db.execute(
                        "INSERT OR IGNORE INTO revision_evidence VALUES (?,?,?,?)",
                        (memory_id, revision, evidence_id, claim_key),
                    )
                self._enqueue_projection(
                    memory_id,
                    revision,
                    "delete" if operation.operation == "supersede" else "upsert",
                )
                memory_ids.append(memory_id)

            for conflict in plan.conflict_records:
                self.db.execute(
                    "INSERT INTO conflicts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        conflict.conflict_id,
                        plan.batch_id,
                        conflict.candidate_id,
                        conflict.proposal_id,
                        conflict.target_id,
                        conflict.base_revision,
                        conflict.reason,
                        "open",
                        None,
                        None,
                        _now(),
                        None,
                    ),
                )
            for result in plan.candidate_results:
                if result.status == "awaiting_review":
                    self.db.execute(
                        "UPDATE candidates SET status='awaiting_review',result_ref=? "
                        "WHERE candidate_id=?",
                        (result.result_ref, result.candidate_id),
                    )
                else:
                    changed = self.db.execute(
                        "UPDATE candidates SET status=?,result_ref=?,active_batch_id=NULL,"
                        "claim_token=NULL WHERE candidate_id=? AND active_batch_id=? "
                        "AND claim_token=?",
                        (
                            result.status,
                            result.result_ref,
                            result.candidate_id,
                            plan.batch_id,
                            result.claim_token,
                        ),
                    )
                    if changed.rowcount != 1:
                        raise ValueError("candidate_claim_fenced")
                    self.db.execute(
                        "UPDATE batch_candidates SET released_at=? WHERE batch_id=? "
                        "AND candidate_id=? AND released_at IS NULL",
                        (_now(), plan.batch_id, result.candidate_id),
                    )
            for proposal_id in plan.proposal_ids:
                proposal = self.db.execute(
                    "SELECT operation FROM proposals WHERE proposal_id=? AND batch_id=?",
                    (proposal_id, plan.batch_id),
                ).fetchone()
                if not proposal:
                    raise ValueError("proposal_missing")
                status = {
                    "no_change": "no_change",
                    "conflict": "awaiting_review",
                }.get(proposal["operation"], "committed")
                self.db.execute(
                    "UPDATE proposals SET status=? WHERE proposal_id=? AND status='proposed'",
                    (status, proposal_id),
                )
            if plan.task_update and plan.task_update.touch_last_active:
                if plan.task_update.task_id != batch["task_id"]:
                    raise ValueError("task_update_out_of_scope")
                self.db.execute(
                    "UPDATE tasks SET last_active=? WHERE task_id=? AND owner_id=?",
                    (_now(), plan.task_update.task_id, self.owner_id),
                )
            final_status = "awaiting_review" if plan.conflict_records else "settled"
            self.db.execute(
                "UPDATE batches SET status=?,lease_owner=NULL,lease_token=NULL,lease_until=NULL,"
                "settled_at=? WHERE batch_id=?",
                (
                    final_status,
                    None if final_status == "awaiting_review" else _now(),
                    plan.batch_id,
                ),
            )
        return BatchCommitResult(
            status=final_status, batch_id=plan.batch_id, memory_ids=tuple(memory_ids)
        )

    def _store_conflict_receipt(self, request: CommitRequest, digest: str, reason: str) -> CommitResult:
        result = CommitResult(status="conflict", memory_id=request.memory_id, ledger_sequence=self.sequence, reason=reason)
        self._save_receipt(request.receipt, digest, result)
        return result

    def _save_receipt(self, receipt: str, digest: str, result: CommitResult) -> None:
        self.db.execute(
            "INSERT INTO commit_receipts VALUES (?,?,?,?,?)",
            (self.owner_id, receipt, digest, result.model_dump_json(), _now()),
        )

    def _attach_evidence(self, memory_id: str, revision: int, request: CommitRequest) -> None:
        for item in request.evidence:
            self.db.execute(
                "INSERT OR IGNORE INTO evidence VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    item.evidence_id,
                    self.owner_id,
                    item.source_event_id,
                    item.claim_key,
                    item.evidence_kind,
                    _canonical(item.locator),
                    item.observed_at.isoformat(),
                    _canonical(item.verification.model_dump(mode="json")) if item.verification is not None else None,
                    item.redacted_excerpt,
                ),
            )
            row = self.db.execute(
                "SELECT evidence_id FROM evidence WHERE owner_id=? AND source_event_id=? AND claim_key=?",
                (self.owner_id, item.source_event_id, item.claim_key),
            ).fetchone()
            if row is None:
                raise ValueError("evidence_id_collision")
            self.db.execute(
                "INSERT OR IGNORE INTO revision_evidence VALUES (?,?,?,?)",
                (memory_id, revision, row["evidence_id"], item.claim_key),
            )

    def get_current(self, memory_ids: list[str], context: TaskContext) -> list[MemoryView]:
        if not self._context_is_valid(context) or not memory_ids:
            return []
        placeholders = ",".join("?" for _ in memory_ids)
        rows = self.db.execute(
            f"SELECT m.*,r.payload_json FROM memories m JOIN revisions r ON r.memory_id=m.memory_id AND r.revision=m.current_revision "
            f"WHERE m.owner_id=? AND m.state='active' AND m.memory_id IN ({placeholders})",
            (self.owner_id, *memory_ids),
        ).fetchall()
        result = []
        for row in rows:
            scope = Scope.model_validate_json(row["scope_json"])
            if self._scope_allows(scope, context):
                result.append(self._view(row, scope))
        return result

    def read_history(self, memory_id: str, context: TaskContext) -> list[MemoryView]:
        if not self._context_is_valid(context):
            return []
        head = self.db.execute(
            "SELECT * FROM memories WHERE memory_id=? AND owner_id=? AND state!='deleted'", (memory_id, self.owner_id)
        ).fetchone()
        if not head:
            return []
        scope = Scope.model_validate_json(head["scope_json"])
        if not self._scope_allows(scope, context):
            return []
        rows = self.db.execute("SELECT * FROM revisions WHERE memory_id=? ORDER BY revision", (memory_id,)).fetchall()
        return [
            MemoryView(
                owner_id=self.owner_id,
                memory_id=memory_id,
                revision=row["revision"],
                memory_type=head["memory_type"],
                scope=scope,
                state=head["state"],
                conflict_state=head["conflict_state"],
                payload=json.loads(row["payload_json"]),
                ledger_sequence=head["ledger_sequence"],
            )
            for row in rows
        ]

    def forget(self, memory_id: str, receipt: str) -> dict[str, str]:
        receipt_key = f"forget:{receipt}"
        request_digest = hashlib.sha256(memory_id.encode()).hexdigest()
        prior = self.db.execute(
            "SELECT request_digest,result_json FROM commit_receipts WHERE owner_id=? AND receipt=?",
            (self.owner_id, receipt_key),
        ).fetchone()
        if prior:
            if prior["request_digest"] != request_digest:
                return {"status": "needs_action", "reason": "receipt_reused"}
            return json.loads(prior["result_json"])
        with self._write_transaction():
            head = self.db.execute(
                "SELECT m.*,r.payload_json FROM memories m JOIN revisions r "
                "ON r.memory_id=m.memory_id AND r.revision=m.current_revision "
                "WHERE m.memory_id=? AND m.owner_id=?",
                (memory_id, self.owner_id),
            ).fetchone()
            if not head:
                result = {"status": "needs_action", "reason": "not_found"}
            elif head["state"] == "deleted":
                job = self.db.execute(
                    "SELECT job_id,status FROM deletion_jobs WHERE owner_id=? AND memory_id=? "
                    "ORDER BY created_at DESC LIMIT 1",
                    (self.owner_id, memory_id),
                ).fetchone()
                result = {
                    "status": job["status"] if job else "local_redacted",
                    "job_id": job["job_id"] if job else "",
                    "memory_id": memory_id,
                }
            else:
                job_id = str(uuid.uuid4())
                sequence = self._next_sequence()
                self._suppress_memory(memory_id)
                self.db.execute(
                    "UPDATE memories SET state='deleted',ledger_sequence=? WHERE memory_id=?", (sequence, memory_id)
                )
                self.db.execute("UPDATE revisions SET payload_json='{}' WHERE memory_id=?", (memory_id,))
                self.db.execute(
                    "UPDATE evidence SET redacted_excerpt=NULL WHERE evidence_id IN (SELECT evidence_id FROM revision_evidence WHERE memory_id=?)",
                    (memory_id,),
                )
                projected = self._enqueue_projection(memory_id, head["current_revision"], "delete")
                status = "remote_pending" if projected else "local_redacted"
                self.db.execute(
                    "INSERT INTO deletion_jobs VALUES (?,?,?,?,?)", (job_id, self.owner_id, memory_id, status, _now())
                )
                result = {"status": status, "job_id": job_id, "memory_id": memory_id}
            self.db.execute(
                "INSERT INTO commit_receipts VALUES (?,?,?,?,?)",
                (self.owner_id, receipt_key, request_digest, _canonical(result), _now()),
            )
        return result

    def deletion_status(self, job_id: str) -> dict[str, str | int] | None:
        row = self.db.execute(
            "SELECT job_id,memory_id,status,created_at FROM deletion_jobs WHERE owner_id=? AND job_id=?",
            (self.owner_id, job_id),
        ).fetchone()
        if not row:
            return None
        pending = self.db.execute(
            "SELECT COUNT(*) FROM outbox WHERE memory_id=? AND action='delete' AND status='pending'",
            (row["memory_id"],),
        ).fetchone()[0]
        return {**dict(row), "pending_outbox": pending}

    def list_outbox(self, status: str = "pending") -> list[dict[str, object]]:
        rows = self.db.execute(
            "SELECT outbox_id,backend,generation,memory_id,revision,action,status,"
            "target_locator,created_at "
            "FROM outbox WHERE status=? ORDER BY outbox_id",
            (status,),
        ).fetchall()
        return [dict(row) for row in rows]

    def register_backend_generation(self, backend: str, generation: str, active: bool = True) -> None:
        with self._write_transaction():
            if active:
                self.db.execute("UPDATE backend_generations SET active=0 WHERE backend=?", (backend,))
            self.db.execute(
                "INSERT INTO backend_generations VALUES (?,?,?) ON CONFLICT(backend,generation) DO UPDATE SET active=excluded.active",
                (backend, generation, int(active)),
            )

    @property
    def sequence(self) -> int:
        return int(self.db.execute("SELECT value FROM ledger_meta WHERE key='sequence'").fetchone()[0])

    def _next_sequence(self) -> int:
        value = self.sequence + 1
        self.db.execute("UPDATE ledger_meta SET value=? WHERE key='sequence'", (str(value),))
        return value

    def _enqueue_projection(self, memory_id: str, revision: int, action: str) -> bool:
        if action == "delete":
            state = self.db.execute("SELECT state FROM memories WHERE memory_id=?", (memory_id,)).fetchone()
            if state and state["state"] == "retired":
                self._suppress_memory(memory_id)
            # Cover older revisions and inactive generations as well as the current head.
            rows = self.db.execute(
                "SELECT DISTINCT backend,generation,revision FROM outbox WHERE memory_id=? AND action='upsert'",
                (memory_id,),
            ).fetchall()
            for row in rows:
                self.db.execute(
                    "INSERT OR IGNORE INTO outbox(backend,generation,memory_id,revision,action,created_at) VALUES (?,?,?,?,?,?)",
                    (row["backend"], row["generation"], memory_id, row["revision"], action, _now()),
                )
            return bool(rows)
        rows = self.db.execute("SELECT backend,generation FROM backend_generations WHERE active=1").fetchall()
        for row in rows:
            self.db.execute(
                "INSERT OR IGNORE INTO outbox(backend,generation,memory_id,revision,action,created_at) VALUES (?,?,?,?,?,?)",
                (row["backend"], row["generation"], memory_id, revision, action, _now()),
            )
        return bool(rows)

    def _suppress_memory(self, memory_id: str) -> None:
        head = self.db.execute("SELECT memory_type,scope_json FROM memories WHERE memory_id=? AND owner_id=?",
                               (memory_id, self.owner_id)).fetchone()
        payloads = self.db.execute("SELECT payload_json FROM revisions WHERE memory_id=?", (memory_id,)).fetchall()
        sources = self.db.execute(
            "SELECT DISTINCT e.source_event_id,e.claim_key FROM evidence e JOIN revision_evidence re "
            "ON re.evidence_id=e.evidence_id WHERE re.memory_id=?", (memory_id,),
        ).fetchall()
        source_ids = [self._source_claim_digest(row["source_event_id"], row["claim_key"]) for row in sources] or [None]
        for payload in payloads:
            topic = self._topic_digest(head["memory_type"], head["scope_json"], payload["payload_json"])
            for source_id in source_ids:
                self.db.execute("INSERT INTO suppression_rules VALUES (?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), self.owner_id, memory_id, source_id, head["scope_json"], topic, _now()))

    @staticmethod
    def _topic_digest(memory_type: str, scope_json: str, payload_json: str) -> str:
        return hashlib.sha256(f"{memory_type}\0{scope_json}\0{payload_json}".encode()).hexdigest()

    @staticmethod
    def _source_claim_digest(source_event_id: str, claim_key: str) -> str:
        return hashlib.sha256(f"{source_event_id}\0{claim_key}".encode()).hexdigest()

    def _validate_scope(self, scope: Scope) -> None:
        if scope.project_id:
            self._require_project(scope.project_id)
        if scope.task_id:
            task = self.db.execute(
                "SELECT project_id FROM tasks WHERE task_id=? AND owner_id=?", (scope.task_id, self.owner_id)
            ).fetchone()
            if not task or task["project_id"] != scope.project_id:
                raise ValueError("task scope is not owned by the project")

    def _require_project(self, project_id: str) -> None:
        row = self.db.execute(
            "SELECT 1 FROM projects WHERE project_id=? AND owner_id=?", (project_id, self.owner_id)
        ).fetchone()
        if not row:
            raise ValueError("unknown project")

    def _context_is_valid(self, context: TaskContext) -> bool:
        if context.owner_id != self.owner_id:
            return False
        if context.project_id:
            project = self.db.execute(
                "SELECT 1 FROM projects WHERE owner_id=? AND project_id=?",
                (self.owner_id, context.project_id),
            ).fetchone()
            if not project:
                return False
        if context.workspace_id:
            workspace = self.db.execute(
                "SELECT project_id FROM project_locations WHERE owner_id=? AND workspace_id=?",
                (self.owner_id, context.workspace_id),
            ).fetchone()
            if not workspace or workspace["project_id"] != context.project_id:
                return False
        if context.task_id:
            task = self.db.execute(
                "SELECT project_id FROM tasks WHERE owner_id=? AND task_id=?",
                (self.owner_id, context.task_id),
            ).fetchone()
            if not task or task["project_id"] != context.project_id:
                return False
        return True

    @staticmethod
    def _scope_allows(scope: Scope, context: TaskContext) -> bool:
        if scope.kind == "global":
            return True
        if scope.project_id != context.project_id:
            return False
        if scope.kind == "project":
            return True
        if scope.kind == "task":
            return scope.task_id == context.task_id
        root = Path("/")
        pattern = scope.path_pattern or ""
        return any((root / path).match(pattern) for path in context.touched_paths if not Path(path).is_absolute())

    def _view(self, row: sqlite3.Row, scope: Scope) -> MemoryView:
        return MemoryView(
            owner_id=row["owner_id"],
            memory_id=row["memory_id"],
            revision=row["current_revision"],
            memory_type=row["memory_type"],
            scope=scope,
            state=row["state"],
            conflict_state=row["conflict_state"],
            payload=json.loads(row["payload_json"]),
            ledger_sequence=row["ledger_sequence"],
        )
