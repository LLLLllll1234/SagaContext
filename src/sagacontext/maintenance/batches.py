from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

from ..ledger import Ledger, Scope
from .models import (
    BatchClaim,
    BatchInput,
    BatchReceipt,
    CandidateInput,
    CandidateReceipt,
    JudgeAnchor,
    JudgeCandidate,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


class BatchService:
    def __init__(
        self,
        ledger: Ledger,
        *,
        policy_version: str = "s2-policy-v1",
        maintenance_schema_version: int = 2,
        judge_version: str = "scripted-v1",
    ):
        self.ledger = ledger
        self.policy_version = policy_version
        self.maintenance_schema_version = maintenance_schema_version
        self.judge_version = judge_version

    def create_candidate(self, candidate: CandidateInput) -> CandidateReceipt:
        self._validate_candidate(candidate)
        candidate_id = str(uuid.uuid4())
        placeholders = ",".join("?" for _ in candidate.event_ids)
        sequence = self.ledger.db.execute(
            f"SELECT MAX(ingest_sequence) FROM events WHERE owner_id=? AND event_id IN ({placeholders})",
            (self.ledger.owner_id, *candidate.event_ids),
        ).fetchone()[0]
        with self.ledger._write_transaction():
            self.ledger.db.execute(
                "INSERT INTO candidates VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    candidate_id,
                    self.ledger.owner_id,
                    candidate.session_id,
                    candidate.task_id,
                    candidate.kind,
                    candidate.memory_type_hint,
                    _canonical(candidate.scope_hint.model_dump()),
                    candidate.topic_key,
                    _canonical(candidate.event_ids),
                    "pending",
                    None,
                    None,
                    None,
                    sequence,
                ),
            )
        return CandidateReceipt(candidate_id=candidate_id)

    def request_batch(
        self, session_id: str, task_id: str | None, anchor_ids: tuple[str, ...] = ()
    ) -> BatchReceipt:
        self._validate_session_task(session_id, task_id)
        batch_id = str(uuid.uuid4())
        with self.ledger._write_transaction():
            upper = self.ledger.db.execute(
                "SELECT COALESCE(MAX(ingest_sequence),0) FROM events WHERE owner_id=? AND session_id=?",
                (self.ledger.owner_id, session_id),
            ).fetchone()[0]
            candidates = self.ledger.db.execute(
                "SELECT candidate_id,event_ids_json FROM candidates WHERE owner_id=? AND session_id=? "
                "AND task_id IS ? AND status IN ('pending','retry') AND created_sequence<=? "
                "ORDER BY created_sequence,candidate_id",
                (self.ledger.owner_id, session_id, task_id, upper),
            ).fetchall()
            candidate_ids = tuple(row["candidate_id"] for row in candidates)
            event_ids = tuple(
                dict.fromkeys(
                    event_id
                    for row in candidates
                    for event_id in json.loads(row["event_ids_json"])
                )
            )
            anchors = []
            for memory_id in anchor_ids:
                row = self.ledger.db.execute(
                    "SELECT current_revision FROM memories WHERE memory_id=? AND owner_id=? "
                    "AND state='active'",
                    (memory_id, self.ledger.owner_id),
                ).fetchone()
                if not row:
                    raise ValueError("anchor_not_current")
                anchors.append((memory_id, row["current_revision"]))
            digest = hashlib.sha256(
                _canonical(
                    {
                        "session_id": session_id,
                        "task_id": task_id,
                        "event_upper_sequence": upper,
                        "policy_version": self.policy_version,
                        "maintenance_schema_version": self.maintenance_schema_version,
                        "judge_version": self.judge_version,
                        "events": event_ids,
                        "candidates": candidate_ids,
                        "anchors": anchors,
                    }
                ).encode()
            ).hexdigest()
            self.ledger.db.execute(
                "INSERT INTO batches(batch_id,owner_id,session_id,task_id,event_upper_sequence,"
                "policy_version,maintenance_schema_version,judge_version,input_digest,status,"
                "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    batch_id,
                    self.ledger.owner_id,
                    session_id,
                    task_id,
                    upper,
                    self.policy_version,
                    self.maintenance_schema_version,
                    self.judge_version,
                    digest,
                    "pending",
                    _now(),
                ),
            )
            for event_id in event_ids:
                self.ledger.db.execute(
                    "INSERT INTO batch_events VALUES (?,?)", (batch_id, event_id)
                )
            for memory_id, revision in anchors:
                self.ledger.db.execute(
                    "INSERT INTO batch_anchors VALUES (?,?,?)",
                    (batch_id, memory_id, revision),
                )
            claim_tokens = []
            for candidate_id in candidate_ids:
                token = str(uuid.uuid4())
                changed = self.ledger.db.execute(
                    "UPDATE candidates SET status='processing',active_batch_id=?,claim_token=? "
                    "WHERE candidate_id=? AND owner_id=? AND status IN ('pending','retry') "
                    "AND active_batch_id IS NULL",
                    (batch_id, token, candidate_id, self.ledger.owner_id),
                )
                if changed.rowcount != 1:
                    raise RuntimeError("candidate_claim_conflict")
                self.ledger.db.execute(
                    "INSERT INTO batch_candidates VALUES (?,?,?,NULL)",
                    (batch_id, candidate_id, token),
                )
                claim_tokens.append(token)
        return BatchReceipt(batch_id=batch_id, candidate_claim_tokens=tuple(claim_tokens))

    def claim_next(
        self, worker_id: str, now: datetime, lease_duration: timedelta
    ) -> BatchClaim | None:
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")
        now_text = now.astimezone(timezone.utc).isoformat()
        lease_until = now.astimezone(timezone.utc) + lease_duration
        with self.ledger._write_transaction():
            row = self.ledger.db.execute(
                "SELECT batch_id,status,lease_token FROM batches WHERE owner_id=? AND "
                "((status IN ('pending','retry') AND (next_attempt_at IS NULL OR next_attempt_at<=?)) "
                "OR (status IN ('running','proposed') AND lease_until<?)) "
                "ORDER BY created_at,batch_id LIMIT 1",
                (self.ledger.owner_id, now_text, now_text),
            ).fetchone()
            if not row:
                return None
            token = str(uuid.uuid4())
            claimed_status = "proposed" if row["status"] == "proposed" else "running"
            changed = self.ledger.db.execute(
                "UPDATE batches SET status=?,lease_owner=?,lease_token=?,lease_until=?,"
                "attempt_count=attempt_count+1 WHERE batch_id=? AND owner_id=? AND status=? "
                "AND (lease_token IS ? OR lease_token=?)",
                (
                    claimed_status,
                    worker_id,
                    token,
                    lease_until.isoformat(),
                    row["batch_id"],
                    self.ledger.owner_id,
                    row["status"],
                    row["lease_token"],
                    row["lease_token"],
                ),
            )
            if changed.rowcount != 1:
                return None
        return BatchClaim(
            batch_id=row["batch_id"],
            lease_owner=worker_id,
            lease_token=token,
            lease_until=lease_until,
        )

    def validate_claim(
        self,
        batch_id: str,
        worker_id: str,
        lease_token: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        now = now or datetime.now(timezone.utc)
        row = self.ledger.db.execute(
            "SELECT 1 FROM batches WHERE batch_id=? AND owner_id=? AND status='running' "
            "AND lease_owner=? AND lease_token=? AND lease_until>?",
            (
                batch_id,
                self.ledger.owner_id,
                worker_id,
                lease_token,
                now.astimezone(timezone.utc).isoformat(),
            ),
        ).fetchone()
        return row is not None

    def validate_candidate_claim(
        self, batch_id: str, candidate_id: str, claim_token: str
    ) -> bool:
        row = self.ledger.db.execute(
            "SELECT 1 FROM candidates c JOIN batch_candidates bc "
            "ON bc.batch_id=c.active_batch_id AND bc.candidate_id=c.candidate_id "
            "WHERE c.owner_id=? AND c.candidate_id=? AND c.active_batch_id=? "
            "AND c.claim_token=? AND bc.candidate_claim_token=? AND bc.released_at IS NULL "
            "AND c.status IN ('processing','awaiting_review')",
            (
                self.ledger.owner_id,
                candidate_id,
                batch_id,
                claim_token,
                claim_token,
            ),
        ).fetchone()
        return row is not None

    def batch_input(self, batch_id: str) -> BatchInput:
        batch = self.ledger.db.execute(
            "SELECT input_digest,policy_version,maintenance_schema_version,judge_version "
            "FROM batches WHERE batch_id=? AND owner_id=?",
            (batch_id, self.ledger.owner_id),
        ).fetchone()
        if not batch:
            raise ValueError("unknown_batch")
        events = self.ledger.db.execute(
            "SELECT event_id FROM batch_events WHERE batch_id=? ORDER BY rowid", (batch_id,)
        ).fetchall()
        candidates = self.ledger.db.execute(
            "SELECT c.candidate_id,c.kind,c.memory_type_hint,c.scope_hint_json,c.topic_key,c.event_ids_json "
            "FROM batch_candidates bc JOIN candidates c ON c.candidate_id=bc.candidate_id "
            "WHERE bc.batch_id=? ORDER BY bc.rowid",
            (batch_id,),
        ).fetchall()
        anchors = self.ledger.db.execute(
            "SELECT ba.memory_id,ba.revision,m.memory_type,m.scope_json,r.payload_json "
            "FROM batch_anchors ba JOIN memories m ON m.memory_id=ba.memory_id "
            "JOIN revisions r ON r.memory_id=ba.memory_id AND r.revision=ba.revision "
            "WHERE ba.batch_id=? ORDER BY ba.memory_id",
            (batch_id,),
        ).fetchall()
        event_rows = self.ledger.db.execute(
            "SELECT event_id,event_kind,payload_json FROM events "
            "WHERE event_id IN (SELECT event_id FROM batch_events WHERE batch_id=?) "
            "ORDER BY ingest_sequence,event_id",
            (batch_id,),
        ).fetchall()
        event_text: dict[str, str] = {}
        for event in event_rows:
            payload = json.loads(event["payload_json"])
            if isinstance(payload, dict):
                text = next(
                    (str(payload[key]) for key in ("text", "message", "content", "summary") if payload.get(key)),
                    "",
                )
            else:
                text = str(payload)
            event_text[event["event_id"]] = text or json.dumps(payload, ensure_ascii=True, sort_keys=True)
        judge_candidates = tuple(
            JudgeCandidate(
                candidate_id=row["candidate_id"],
                kind=row["kind"],
                memory_type_hint=row["memory_type_hint"],
                scope_hint=Scope.model_validate_json(row["scope_hint_json"]),
                topic_key=row["topic_key"],
                event_ids=tuple(json.loads(row["event_ids_json"])),
                text="\n".join(
                    event_text[event_id]
                    for event_id in json.loads(row["event_ids_json"])
                    if event_id in event_text
                ),
            )
            for row in candidates
        )
        judge_anchors = tuple(
            JudgeAnchor(
                memory_id=row["memory_id"],
                revision=row["revision"],
                memory_type=row["memory_type"],
                scope=Scope.model_validate_json(row["scope_json"]),
                payload=json.loads(row["payload_json"]),
            )
            for row in anchors
        )
        return BatchInput(
            batch_id=batch_id,
            input_digest=batch["input_digest"],
            policy_version=batch["policy_version"],
            maintenance_schema_version=batch["maintenance_schema_version"],
            judge_version=batch["judge_version"],
            event_ids=tuple(row["event_id"] for row in events),
            candidate_ids=tuple(row["candidate_id"] for row in candidates),
            anchor_revisions=tuple((row["memory_id"], row["revision"]) for row in anchors),
            judge_candidates=judge_candidates,
            judge_anchors=judge_anchors,
            summary="\n".join(
                event_text[row["event_id"]]
                for row in event_rows
                if event_text.get(row["event_id"])
            ),
        )

    def candidate_status(self, candidate_id: str) -> str | None:
        row = self.ledger.db.execute(
            "SELECT status FROM candidates WHERE candidate_id=? AND owner_id=?",
            (candidate_id, self.ledger.owner_id),
        ).fetchone()
        return row["status"] if row else None

    def input_is_current(self, batch_id: str) -> bool:
        batch = self.ledger.db.execute(
            "SELECT session_id,task_id,event_upper_sequence,policy_version,"
            "maintenance_schema_version,judge_version,input_digest FROM batches "
            "WHERE batch_id=? AND owner_id=?",
            (batch_id, self.ledger.owner_id),
        ).fetchone()
        if not batch:
            return False
        frozen = self.batch_input(batch_id)
        expected = hashlib.sha256(
            _canonical(
                {
                    "session_id": batch["session_id"],
                    "task_id": batch["task_id"],
                    "event_upper_sequence": batch["event_upper_sequence"],
                    "policy_version": batch["policy_version"],
                    "maintenance_schema_version": batch["maintenance_schema_version"],
                    "judge_version": batch["judge_version"],
                    "events": frozen.event_ids,
                    "candidates": frozen.candidate_ids,
                    "anchors": frozen.anchor_revisions,
                }
            ).encode()
        ).hexdigest()
        return expected == batch["input_digest"]

    def anchors_are_current(self, batch_id: str) -> bool:
        stale = self.ledger.db.execute(
            "SELECT 1 FROM batch_anchors ba LEFT JOIN memories m "
            "ON m.memory_id=ba.memory_id AND m.owner_id=? "
            "WHERE ba.batch_id=? AND (m.memory_id IS NULL OR m.state!='active' "
            "OR m.current_revision!=ba.revision) LIMIT 1",
            (self.ledger.owner_id, batch_id),
        ).fetchone()
        return stale is None

    def fail_batch(
        self,
        batch_id: str,
        worker_id: str,
        lease_token: str,
        error_class: str,
        *,
        now: datetime,
    ) -> bool:
        with self.ledger._write_transaction():
            changed = self.ledger.db.execute(
                "UPDATE batches SET status='retry',lease_owner=NULL,lease_token=NULL,"
                "lease_until=NULL,last_error_class=? WHERE batch_id=? AND owner_id=? "
                "AND lease_owner=? AND lease_token=? AND lease_until>? "
                "AND status IN ('running','proposed')",
                (
                    error_class,
                    batch_id,
                    self.ledger.owner_id,
                    worker_id,
                    lease_token,
                    now.astimezone(timezone.utc).isoformat(),
                ),
            )
        return changed.rowcount == 1

    def block_batch(
        self,
        batch_id: str,
        worker_id: str,
        lease_token: str,
        error_class: str,
        *,
        now: datetime,
    ) -> bool:
        with self.ledger._write_transaction():
            changed = self.ledger.db.execute(
                "UPDATE batches SET status='blocked',lease_owner=NULL,lease_token=NULL,"
                "lease_until=NULL,last_error_class=? WHERE batch_id=? AND owner_id=? "
                "AND lease_owner=? AND lease_token=? AND lease_until>? "
                "AND status IN ('running','proposed')",
                (
                    error_class,
                    batch_id,
                    self.ledger.owner_id,
                    worker_id,
                    lease_token,
                    now.astimezone(timezone.utc).isoformat(),
                ),
            )
        return changed.rowcount == 1

    def invalidate_and_release(
        self,
        batch_id: str,
        worker_id: str,
        lease_token: str,
        *,
        now: datetime,
    ) -> bool:
        with self.ledger._write_transaction():
            row = self.ledger.db.execute(
                "SELECT 1 FROM batches WHERE batch_id=? AND owner_id=? AND lease_owner=? "
                "AND lease_token=? AND lease_until>? AND status IN ('running','proposed')",
                (
                    batch_id,
                    self.ledger.owner_id,
                    worker_id,
                    lease_token,
                    now.astimezone(timezone.utc).isoformat(),
                ),
            ).fetchone()
            if not row:
                return False
            self.ledger.db.execute(
                "UPDATE proposals SET status='invalidated' WHERE batch_id=? AND status='proposed'",
                (batch_id,),
            )
            self.ledger.db.execute(
                "UPDATE candidates SET status='retry',active_batch_id=NULL,claim_token=NULL "
                "WHERE owner_id=? AND active_batch_id=? AND status='processing'",
                (self.ledger.owner_id, batch_id),
            )
            self.ledger.db.execute(
                "UPDATE batch_candidates SET released_at=? WHERE batch_id=? AND released_at IS NULL",
                (_now(), batch_id),
            )
            self.ledger.db.execute(
                "UPDATE batches SET status='settled',lease_owner=NULL,lease_token=NULL,"
                "lease_until=NULL,settled_at=? WHERE batch_id=?",
                (_now(), batch_id),
            )
        return True

    def _validate_candidate(self, candidate: CandidateInput) -> None:
        self._validate_session_task(candidate.session_id, candidate.task_id)
        if not candidate.event_ids:
            raise ValueError("candidate_requires_events")
        placeholders = ",".join("?" for _ in candidate.event_ids)
        count = self.ledger.db.execute(
            f"SELECT COUNT(*) FROM events WHERE owner_id=? AND session_id=? "
            f"AND event_id IN ({placeholders})",
            (self.ledger.owner_id, candidate.session_id, *candidate.event_ids),
        ).fetchone()[0]
        if count != len(set(candidate.event_ids)):
            raise ValueError("candidate_event_out_of_scope")

    def _validate_session_task(self, session_id: str, task_id: str | None) -> None:
        row = self.ledger.db.execute(
            "SELECT pl.project_id FROM sessions s JOIN project_locations pl "
            "ON pl.owner_id=s.owner_id AND pl.workspace_id=s.workspace_id "
            "WHERE s.session_id=? AND s.owner_id=?",
            (session_id, self.ledger.owner_id),
        ).fetchone()
        if not row:
            raise ValueError("unknown_session")
        if task_id is not None:
            task = self.ledger.db.execute(
                "SELECT project_id FROM tasks WHERE task_id=? AND owner_id=?",
                (task_id, self.ledger.owner_id),
            ).fetchone()
            if not task or task["project_id"] != row["project_id"]:
                raise ValueError("task_session_project_mismatch")
