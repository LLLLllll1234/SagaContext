"""Fail-closed normal-workspace rollout control plane."""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict

from .backends import BackendAdapter, BackendHit
from .config import Config
from .ledger import Ledger, Scope, TaskContext
from .maintenance import BatchService, BatchWorker, CandidateInput, DeltaProposal, EventJournal, JournalEvent
from .recall_policy import RecallPolicy


class ProposalJudge(Protocol):
    version: str
    def judge(self, batch: Any) -> tuple[DeltaProposal, ...]: ...


EVENT_KIND = {"SessionStart": "session_opened", "UserPromptSubmit": "user_message", "Stop": "checkpoint_requested", "SessionEnd": "session_closed"}
EVENTS = frozenset(EVENT_KIND)


class RuntimeMode(StrEnum):
    OFF = "off"
    SHADOW = "shadow"
    GUARDED = "guarded"
    ACTIVE = "active"


class GateReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: Literal["accepted", "rejected"]
    reason: str | None = None
    event_id: str | None = None
    session_id: str | None = None
    workspace_id: str | None = None
    audit_receipt_id: str | None = None


class InjectionReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: Literal["emitted", "blocked"]
    bundle_digest: str | None = None
    ledger_sequence: int
    owner_id: str
    generation: str
    session_digest: str
    memory_ids: tuple[str, ...] = ()
    revisions: tuple[int, ...] = ()
    omissions: tuple[dict[str, Any], ...] = ()
    mode: RuntimeMode
    reason: str | None = None


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _utc(value: str | datetime) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc)


class ControlAuth:
    """Header-level authentication and canonical request binding."""
    def __init__(self, config: Config):
        self.config = config

    def request_digest(self, action: str, fields: dict[str, Any]) -> str:
        return _digest({"digest_schema_version": "rollout-action-v1", "action": action, **fields})

    def verify(self, *, action: str, fields: dict[str, Any], token: str | None, approver: str | None,
               key_id: str | None, receipt: str | None, issued_at: str | None, expires_at: str | None,
               registered_grant: dict[str, Any] | None = None) -> str:
        if not token or not approver or not key_id or not receipt or not issued_at or not expires_at:
            raise ValueError("authorization_required")
        if approver != self.config.rollout_approver:
            raise ValueError("approver_mismatch")
        digest = hashlib.sha256(token.encode()).hexdigest()
        is_previous = key_id == self.config.rollout_previous_key_id
        expected = self.config.rollout_previous_token_digest if is_previous else self.config.rollout_token_digest if key_id == self.config.rollout_key_id else ""
        if is_previous and (not registered_grant or registered_grant.get("receipt") != receipt or registered_grant.get("key_id") != key_id):
            raise ValueError("previous_key_requires_registered_grant")
        if not expected or not hmac.compare_digest(digest, expected):
            raise ValueError("invalid_operator_token")
        now = datetime.now(timezone.utc)
        start, end = _utc(issued_at), _utc(expires_at)
        if end < start or end - start > timedelta(minutes=5) or start - timedelta(seconds=60) > now or end + timedelta(seconds=60) < now:
            raise ValueError("authorization_expired")
        digest = self.request_digest(action, {**fields, "approver": approver, "key_id": key_id, "approval_receipt": receipt,
                                            "issued_at": start.isoformat(timespec="microseconds").replace("+00:00", "Z"),
                                            "expires_at": end.isoformat(timespec="microseconds").replace("+00:00", "Z")})
        if is_previous and registered_grant.get("request_digest") != digest:
            raise ValueError("registered_grant_mismatch")
        return digest


class NormalHostAdapter:
    def __init__(self, ledger: Ledger, config: Config):
        self.ledger, self.config, self.journal = ledger, config, EventJournal(ledger)

    def allowed_workspace(self) -> Path | None:
        return _path(self.config.rollout_workspaces[0]) if self.config.rollout_workspaces else None

    def kill_switch_active(self) -> bool:
        return bool(self.config.rollout_stop_file and self.config.rollout_stop_file.exists())

    def ingest(self, record: dict[str, Any], *, rollout: dict[str, Any] | None = None) -> GateReceipt:
        event = str(record.get("hook_event_name", "")); workspace = _path(record.get("cwd")); mode = _safe_mode(rollout["mode"] if rollout else self.config.rollout_mode)
        def reject(reason: str) -> GateReceipt:
            audit = self.ledger.record_rollout_receipt("event_rejection", {"event": event, "reason": reason, "mode": mode.value})
            return GateReceipt(status="rejected", reason=reason, audit_receipt_id=audit["receipt_id"])
        if mode is RuntimeMode.OFF: return reject("mode_off")
        if self.kill_switch_active(): return reject("kill_switch")
        if not rollout or rollout["status"] not in {"running", "draining"}: return reject("rollout_not_active")
        if _utc(rollout["deadline"]) <= datetime.now(timezone.utc): return reject("deadline_exceeded")
        if event not in EVENTS or not workspace: return reject("unverified_host_event" if event not in EVENTS else "missing_workspace")
        if self.allowed_workspace() is None or workspace != self.allowed_workspace(): return reject("workspace_not_allowed")
        if str(record.get("host", self.config.rollout_host)) != self.config.rollout_host: return reject("host_not_allowed")
        if str(record.get("host_version", "")) != self.config.rollout_host_version: return reject("host_version_not_verified")
        generation = str(record.get("source_generation", record.get("generation", "")))
        if generation != rollout["generation"]: return reject("generation_not_verified")
        source_key = str(record.get("source_event_ref", record.get("source_event_key", ""))); host_session = str(record.get("session_id", record.get("host_session_id", "")))
        if not source_key or not host_session: return reject("missing_event_identity")
        try: identity = self.ledger.resolve_project(workspace)
        except (OSError, ValueError): return reject("workspace_identity_mismatch")
        if not identity or (record.get("owner_id") and record["owner_id"] != self.ledger.owner_id): return reject("workspace_identity_mismatch")
        with self.ledger._write_transaction():
            existing = self.ledger.db.execute("SELECT session_id FROM sessions WHERE owner_id=? AND host=? AND host_session_id=?", (self.ledger.owner_id, self.config.rollout_host, host_session)).fetchone()
            if existing:
                session_id = existing["session_id"]
                reservation = self.ledger.db.execute("SELECT 1 FROM rollout_sessions WHERE rollout_id=? AND host_session_id=?", (rollout["rollout_id"], host_session)).fetchone()
                if not reservation:
                    count = self.ledger.db.execute("SELECT COUNT(*) FROM rollout_sessions WHERE rollout_id=?", (rollout["rollout_id"],)).fetchone()[0]
                    if count >= rollout["max_sessions"]: return reject("session_limit")
                    self.ledger.db.execute("INSERT INTO rollout_sessions VALUES (?,?,?,?,?,NULL)", (rollout["rollout_id"], session_id, host_session, "reserved", datetime.now(timezone.utc).isoformat()))
                    if count + 1 >= rollout["max_sessions"]:
                        self.ledger.db.execute("UPDATE rollout_runs SET status='draining' WHERE rollout_id=? AND status='running'", (rollout["rollout_id"],))
            else:
                count = self.ledger.db.execute("SELECT COUNT(*) FROM rollout_sessions WHERE rollout_id=?", (rollout["rollout_id"],)).fetchone()[0]
                if count >= rollout["max_sessions"]: return reject("session_limit")
                session_id = self.ledger.open_session(self.config.rollout_host, host_session, identity["workspace_id"])
                self.ledger.db.execute("INSERT INTO rollout_sessions VALUES (?,?,?,?,?,NULL)", (rollout["rollout_id"], session_id, host_session, "reserved", datetime.now(timezone.utc).isoformat()))
                if count + 1 >= rollout["max_sessions"]:
                    self.ledger.db.execute("UPDATE rollout_runs SET status='draining' WHERE rollout_id=? AND status='running'", (rollout["rollout_id"],))
            receipt = self.journal.append(JournalEvent(host=self.config.rollout_host, host_version=self.config.rollout_host_version, session_id=session_id, workspace_id=identity["workspace_id"], event_kind=EVENT_KIND[event], occurred_at=datetime.now(timezone.utc), trust_class="codex_verified_receipt", source_generation=generation, source_event_key=source_key, source_locator={"source_event_ref": source_key, "session_ref": host_session}, payload={"hook_event_name": event}, parser_version="normal-rollout-v1"))
            self.ledger.db.execute("INSERT OR IGNORE INTO rollout_events VALUES (?,?)", (rollout["rollout_id"], receipt.event_id))
        audit = self.ledger.record_rollout_receipt("event", {"event": event, "event_id": receipt.event_id}, workspace_id=identity["workspace_id"], session_id=session_id, rollout_id=rollout["rollout_id"])
        return GateReceipt(status="accepted", event_id=receipt.event_id, session_id=session_id, workspace_id=identity["workspace_id"], audit_receipt_id=audit["receipt_id"])


class RolloutRuntime:
    def __init__(self, ledger: Ledger, config: Config):
        self.ledger, self.config = ledger, config
        self.host, self.auth = NormalHostAdapter(ledger, config), ControlAuth(config)
        self.batches, self.worker, self.policy = BatchService(ledger, judge_version="openai-judge-prompt-v6"), BatchWorker(ledger), RecallPolicy(ledger)

    def _registered_grant(self, receipt: str, request_digest: str) -> dict[str, Any] | None:
        row = self.ledger.db.execute(
            "SELECT receipt,request_digest,key_id,registered_at,expires_at,consumed_at "
            "FROM rollout_approval_grants WHERE owner_id=? AND receipt=?",
            (self.ledger.owner_id, receipt),
        ).fetchone()
        if not row or row["request_digest"] != request_digest or row["consumed_at"]:
            return None
        if self.config.rollout_previous_rotated_at and _utc(row["registered_at"]) >= _utc(self.config.rollout_previous_rotated_at):
            return None
        if _utc(row["expires_at"]) <= datetime.now(timezone.utc):
            return None
        return dict(row)

    def _consume_grant(self, grant: dict[str, Any] | None) -> None:
        if not grant:
            return
        changed = self.ledger.db.execute(
            "UPDATE rollout_approval_grants SET consumed_at=? "
            "WHERE owner_id=? AND receipt=? AND consumed_at IS NULL",
            (datetime.now(timezone.utc).isoformat(), self.ledger.owner_id, grant["receipt"]),
        )
        if changed.rowcount != 1:
            raise ValueError("grant_already_consumed")

    def _authorize(self, *, action: str, fields: dict[str, Any], token: str | None,
                   approver: str | None, key_id: str | None, receipt: str,
                   issued_at: str | None, expires_at: str | None) -> tuple[str, dict[str, Any] | None]:
        if issued_at and expires_at:
            start, end = _utc(issued_at), _utc(expires_at)
            digest_fields = {**fields, "approver": approver, "key_id": key_id,
                             "approval_receipt": receipt,
                             "issued_at": start.isoformat(timespec="microseconds").replace("+00:00", "Z"),
                             "expires_at": end.isoformat(timespec="microseconds").replace("+00:00", "Z")}
            expected_digest = self.auth.request_digest(action, digest_fields)
        else:
            expected_digest = ""
        grant = self._registered_grant(receipt, expected_digest)
        digest = self.auth.verify(action=action, fields=fields, token=token, approver=approver,
                                  key_id=key_id, receipt=receipt, issued_at=issued_at,
                                  expires_at=expires_at, registered_grant=grant)
        return digest, grant

    @property
    def mode(self) -> RuntimeMode:
        run = self._run()
        return _safe_mode(run["mode"] if run else "off")

    def _run(self) -> dict[str, Any] | None:
        row = self.ledger.db.execute("SELECT * FROM rollout_runs WHERE owner_id=? AND status IN ('running','draining') ORDER BY created_at DESC LIMIT 1", (self.ledger.owner_id,)).fetchone()
        if row and (self.host.kill_switch_active() or _utc(row["deadline"]) <= datetime.now(timezone.utc)):
            reason = "kill_switch" if self.host.kill_switch_active() else "deadline"
            with self.ledger._write_transaction(): self.ledger.db.execute("UPDATE rollout_runs SET status='stopping',stopped_at=?,stop_reason=? WHERE rollout_id=?", (datetime.now(timezone.utc).isoformat(), reason, row["rollout_id"]))
            return None
        return dict(row) if row else None

    def activate(self, *, mode: RuntimeMode | str, workspace: str, approver: str, approval_receipt: str,
                 deadline: datetime, max_sessions: int = 10, max_candidates: int = 20, generation: str | None = None,
                 rollback_plan: dict[str, Any] | None = None, token: str | None = None, key_id: str | None = None,
                 issued_at: str | None = None, expires_at: str | None = None) -> dict[str, Any]:
        new_mode = _safe_mode(mode)
        if new_mode not in {RuntimeMode.SHADOW, RuntimeMode.GUARDED}: raise ValueError("active_unreachable")
        workspace_path = _path(workspace)
        if workspace_path is None or self.host.allowed_workspace() != workspace_path: raise ValueError("workspace_not_allowed")
        plan = rollback_plan or {"schema_version": "rollout-plan-v1", "resources": "rollout-scoped"}; plan_digest = _digest(plan)
        fields = {"mode": new_mode.value, "workspace": str(workspace_path), "deadline": _utc(deadline).isoformat(timespec="microseconds").replace("+00:00", "Z"), "max_sessions": max_sessions, "max_candidates": max_candidates, "generation": generation or self.config.rollout_generation, "rollback_plan_digest": plan_digest}
        request_digest, grant = self._authorize(action="activation", fields=fields, token=token, approver=approver, key_id=key_id, receipt=approval_receipt, issued_at=issued_at, expires_at=expires_at)
        if _utc(deadline) > datetime.now(timezone.utc) + timedelta(hours=24): raise ValueError("deadline_too_long")
        now = datetime.now(timezone.utc); rollout_id = str(uuid.uuid4())
        with self.ledger._write_transaction():
            self._consume_grant(grant)
            if self.ledger.db.execute("SELECT 1 FROM rollout_runs WHERE owner_id=? AND status IN ('running','draining','stopping','cleanup_required')", (self.ledger.owner_id,)).fetchone(): raise ValueError("rollout_already_live")
            existing = self.ledger.db.execute("SELECT result_json,request_digest,action FROM rollout_action_receipts WHERE owner_id=? AND receipt=?", (self.ledger.owner_id, approval_receipt)).fetchone()
            if existing:
                if existing["action"] == "activation" and existing["request_digest"] == request_digest: return json.loads(existing["result_json"])
                raise ValueError("receipt_conflict")
            result = {"rollout_id": rollout_id, "mode": new_mode.value, "status": "running", "deadline": _utc(deadline).isoformat()}
            self.ledger.db.execute("INSERT INTO rollout_runs(rollout_id,owner_id,workspace_id,workspace_root,mode,status,approver,key_id,approval_receipt,approval_digest,started_at,deadline,max_sessions,max_candidates,generation,control_epoch,rollback_plan_digest,rollback_plan_json,plan_schema_version,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (rollout_id,self.ledger.owner_id,self.ledger.resolve_project(workspace_path)["workspace_id"],str(workspace_path),new_mode.value,"running",approver,key_id,approval_receipt,request_digest,now.isoformat(),_utc(deadline).isoformat(),max_sessions,max_candidates,generation or self.config.rollout_generation,1,plan_digest,_canonical(plan),"rollout-plan-v1",now.isoformat()))
            self.ledger.db.execute("INSERT INTO rollout_action_receipts VALUES (?,?,?,?,?,?,?)", (self.ledger.owner_id,approval_receipt,"activation",request_digest,rollout_id,_canonical(result),now.isoformat()))
        return result

    def register_grant(self, *, action: str, target_receipt: str, request_digest: str,
                       expires_at: datetime, token: str | None, approver: str | None,
                       key_id: str | None, receipt: str, issued_at: str | None,
                       auth_expires_at: str | None) -> dict[str, Any]:
        """Register a pre-rotation grant; previous keys can never mint one."""
        if key_id != self.config.rollout_key_id:
            raise ValueError("grant_requires_current_key")
        digest = self.auth.verify(action="grant-registration", fields={"target_action": action, "target_receipt": target_receipt, "target_request_digest": request_digest, "grant_expires_at": _utc(expires_at).isoformat()}, token=token, approver=approver, key_id=key_id, receipt=receipt, issued_at=issued_at, expires_at=auth_expires_at)
        with self.ledger._write_transaction():
            self.ledger.db.execute("INSERT INTO rollout_approval_grants(owner_id,receipt,request_digest,key_id,registered_at,expires_at,consumed_at) VALUES (?,?,?,?,?,?,NULL)", (self.ledger.owner_id, target_receipt, request_digest, key_id, datetime.now(timezone.utc).isoformat(), _utc(expires_at).isoformat()))
        return {"receipt": target_receipt, "request_digest": request_digest, "registration_digest": digest}

    def set_mode(self, mode: RuntimeMode | str, *, operator: str, reason: str, **kwargs: Any) -> dict[str, Any]:
        if _safe_mode(mode) is RuntimeMode.OFF:
            return self.stop(reason=reason)
        raise ValueError("authenticated_activation_required")

    def stop(self, *, reason: str = "operator") -> dict[str, Any]:
        with self.ledger._write_transaction():
            row = self.ledger.db.execute("SELECT rollout_id,status,control_epoch FROM rollout_runs WHERE owner_id=? AND status IN ('running','draining') ORDER BY created_at DESC LIMIT 1", (self.ledger.owner_id,)).fetchone()
            if not row: return {"status": "off"}
            self.ledger.db.execute("UPDATE rollout_runs SET status='stopping',stopped_at=?,stop_reason=?,control_epoch=control_epoch+1 WHERE rollout_id=?", (datetime.now(timezone.utc).isoformat(), reason, row["rollout_id"]))
            return {"rollout_id": row["rollout_id"], "status": "stopping", "control_epoch": row["control_epoch"] + 1}

    def rollback(self, *, rollout_id: str, plan_digest: str, phase: str, receipt: str,
                 token: str | None, approver: str | None, key_id: str | None,
                 issued_at: str | None, expires_at: str | None) -> dict[str, Any]:
        if key_id != self.config.rollout_key_id:
            raise ValueError("rollback_requires_current_key")
        digest, grant = self._authorize(action="rollback", fields={"rollout_id": rollout_id, "rollback_plan_digest": plan_digest, "phase": phase}, token=token, approver=approver, key_id=key_id, receipt=receipt, issued_at=issued_at, expires_at=expires_at)
        with self.ledger._write_transaction():
            self._consume_grant(grant)
            row = self.ledger.db.execute("SELECT rollback_plan_digest,status FROM rollout_runs WHERE rollout_id=? AND owner_id=?", (rollout_id,self.ledger.owner_id)).fetchone()
            if not row or row["rollback_plan_digest"] != plan_digest: raise ValueError("rollback_plan_mismatch")
            old = self.ledger.db.execute("SELECT result_json,request_digest FROM rollout_action_receipts WHERE owner_id=? AND receipt=?", (self.ledger.owner_id,receipt)).fetchone()
            if old:
                if old["request_digest"] == digest: return json.loads(old["result_json"])
                raise ValueError("receipt_conflict")
            # Only resources explicitly recorded by this rollout may be touched.
            memory_ids = [r[0] for r in self.ledger.db.execute("SELECT DISTINCT memory_id FROM rollout_commits WHERE rollout_id=?", (rollout_id,)).fetchall()]
            for memory_id in memory_ids:
                self.ledger.forget(memory_id, f"rollback:{rollout_id}:{memory_id}")
            self.ledger.db.execute("UPDATE candidates SET status='quarantined',active_batch_id=NULL,claim_token=NULL WHERE candidate_id IN (SELECT candidate_id FROM rollout_candidates WHERE rollout_id=?) AND status NOT IN ('settled','quarantined')", (rollout_id,))
            self.ledger.db.execute("UPDATE batches SET status='blocked',last_error_class='rollout_rollback',settled_at=? WHERE batch_id IN (SELECT batch_id FROM rollout_batches WHERE rollout_id=?) AND status NOT IN ('settled','blocked')", (datetime.now(timezone.utc).isoformat(), rollout_id))
            self.ledger.db.execute("UPDATE rollout_runs SET status='stopped',stopped_at=?,stop_reason=? WHERE rollout_id=?", (datetime.now(timezone.utc).isoformat(), "rollback:"+phase, rollout_id))
            result={"rollout_id":rollout_id,"phase":phase,"status":"completed"}
            self.ledger.db.execute("INSERT INTO rollout_action_receipts VALUES (?,?,?,?,?,?,?)", (self.ledger.owner_id,receipt,"rollback",digest,rollout_id,_canonical(result),datetime.now(timezone.utc).isoformat()))
            self.ledger.db.execute("INSERT INTO rollout_rollback_receipts VALUES (?,?,?,?,?,?,?)", (self.ledger.owner_id,receipt,rollout_id,plan_digest,"completed",_canonical(result),datetime.now(timezone.utc).isoformat()))
        return result

    def ingest(self, record: dict[str, Any]) -> GateReceipt:
        run = self._run(); receipt = self.host.ingest(record, rollout=run)
        if receipt.status == "accepted" and receipt.event_id and receipt.session_id and isinstance(record.get("candidate"), dict) and record.get("hook_event_name") == "UserPromptSubmit":
            try:
                self.schedule_candidate(receipt, record["candidate"], rollout=run)
            except ValueError as error:
                self.ledger.record_rollout_receipt("candidate_error", {"event_id": receipt.event_id, "reason": str(error)}, rollout_id=run["rollout_id"], session_id=receipt.session_id)
        return receipt

    def schedule_candidate(self, receipt: GateReceipt, data: dict[str, Any], *, rollout: dict[str, Any] | None = None) -> dict[str, str]:
        rollout = rollout or self._run()
        if not rollout: raise ValueError("rollout_not_active")
        with self.ledger._write_transaction():
            count = self.ledger.db.execute("SELECT COUNT(*) FROM rollout_candidate_reservations WHERE rollout_id=?", (rollout["rollout_id"],)).fetchone()[0]
            if count >= rollout["max_candidates"]:
                self.ledger.record_rollout_receipt("candidate_quarantine", {"reason": "candidate_limit", "event_id": receipt.event_id}, rollout_id=rollout["rollout_id"])
                return {"status": "quarantined"}
            identity = self.ledger.resolve_project(self.host.allowed_workspace() or Path(".")); scope = data.get("scope_hint") or {"kind": "project", "project_id": identity["project_id"]}
            candidate = self.batches.create_candidate(CandidateInput(session_id=receipt.session_id, task_id=data.get("task_id"), kind=str(data.get("kind", "normal_event")), memory_type_hint=str(data.get("memory_type_hint", "decision")), scope_hint=Scope.model_validate(scope), topic_key=str(data.get("topic_key", "event")), event_ids=(receipt.event_id,)))
            self.ledger.db.execute("INSERT INTO rollout_candidates VALUES (?,?)", (rollout["rollout_id"], candidate.candidate_id)); self.ledger.db.execute("INSERT INTO rollout_candidate_reservations VALUES (?,?,?,?)", (rollout["rollout_id"],candidate.candidate_id,receipt.event_id,datetime.now(timezone.utc).isoformat()))
        return {"candidate_id": candidate.candidate_id}

    def run_batch(self, judge: ProposalJudge, session_id: str, *, task_id: str | None = None, anchor_ids: tuple[str, ...] = ()) -> dict[str, Any]:
        run = self._run()
        if not run or self.mode is RuntimeMode.OFF: raise ValueError("rollout_not_active")
        if not self.ledger.db.execute("SELECT 1 FROM rollout_sessions WHERE rollout_id=? AND session_id=?", (run["rollout_id"], session_id)).fetchone(): raise ValueError("session_not_reserved")
        self.batches.judge_version = judge.version; batch = self.batches.request_batch(session_id, task_id, anchor_ids); self.ledger.db.execute("INSERT INTO rollout_batches VALUES (?,?)", (run["rollout_id"], batch.batch_id))
        result = self.worker.run_once(judge, worker_id="rollout", now=datetime.now(timezone.utc), lease_duration=timedelta(seconds=330), stop_after_proposals=True, max_attempts=3, target_batch_id=batch.batch_id)
        if result.status == "proposed" and run["mode"] == "guarded": self._hold_for_review(batch.batch_id); status = "awaiting_review"
        else: status = result.status
        return {"batch_id": batch.batch_id, "status": status}

    def _hold_for_review(self, batch_id: str) -> None:
        with self.ledger._write_transaction():
            self.ledger.db.execute("UPDATE proposals SET status='awaiting_review' WHERE batch_id=? AND status='proposed'", (batch_id,)); self.ledger.db.execute("UPDATE candidates SET status='awaiting_review' WHERE active_batch_id=? AND status='processing'", (batch_id,)); self.ledger.db.execute("UPDATE batches SET status='awaiting_review',lease_owner=NULL,lease_token=NULL,lease_until=NULL WHERE batch_id=?", (batch_id,))

    def review_batch(self, batch_id: str, decision: Literal["approve", "reject"], *, reviewer: str, receipt: str, approver: str | None = None, **auth: Any) -> dict[str, Any]:
        run = self._run()
        if not run or run["mode"] != "guarded": raise ValueError("review_requires_guarded_mode")
        digest, grant = self._authorize(action="review", fields={"rollout_id": run["rollout_id"], "batch": batch_id, "decision": decision, "reviewer": reviewer}, receipt=receipt, approver=approver, **auth)
        with self.ledger._write_transaction():
            self._consume_grant(grant)
            old = self.ledger.db.execute("SELECT action,request_digest,result_json FROM rollout_action_receipts WHERE owner_id=? AND receipt=?", (self.ledger.owner_id, receipt)).fetchone()
            if old:
                if old["action"] == "review" and old["request_digest"] == digest: return json.loads(old["result_json"])
                raise ValueError("receipt_conflict")
            row = self.ledger.db.execute("SELECT status FROM batches WHERE batch_id=? AND owner_id=?", (batch_id,self.ledger.owner_id)).fetchone()
            if not row or row["status"] != "awaiting_review": raise ValueError("batch_not_pending_review")
            now = datetime.now(timezone.utc)
            if decision == "reject":
                self.ledger.db.execute("UPDATE proposals SET status='rejected' WHERE batch_id=? AND status='awaiting_review'", (batch_id,)); self.ledger.db.execute("UPDATE candidates SET status='quarantined',active_batch_id=NULL,claim_token=NULL WHERE active_batch_id=?", (batch_id,)); self.ledger.db.execute("UPDATE batches SET status='settled',settled_at=? WHERE batch_id=?", (now.isoformat(),batch_id)); result={"status":"rejected","batch_id":batch_id}
            else:
                if self.host.kill_switch_active() or _utc(run["deadline"]) <= datetime.now(timezone.utc):
                    raise ValueError("kill_switch" if self.host.kill_switch_active() else "deadline_exceeded")
                token=str(uuid.uuid4()); self.ledger.db.execute("UPDATE proposals SET status='proposed' WHERE batch_id=? AND status='awaiting_review'", (batch_id,)); self.ledger.db.execute("UPDATE candidates SET status='processing' WHERE active_batch_id=?", (batch_id,)); self.ledger.db.execute("UPDATE batches SET status='review_committing',lease_owner=?,lease_token=?,lease_until=? WHERE batch_id=?", (reviewer,token,(now+timedelta(seconds=30)).isoformat(),batch_id)); plan=self.worker._plan(batch_id,self.worker._proposed(batch_id)); committed=self.ledger.commit_batch(plan,token,now=now,rollout_authorization=(run["rollout_id"],run["control_epoch"])); result={"status":committed.status,"batch_id":batch_id,"memory_ids":list(committed.memory_ids)}
            self.ledger.db.execute("INSERT INTO rollout_action_receipts VALUES (?,?,?,?,?,?,?)", (self.ledger.owner_id,receipt,"review",digest,run["rollout_id"],_canonical(result),now.isoformat()))
        return result

    def verify_bundle(self, receipt: InjectionReceipt) -> bool:
        run = self._run()
        if not run or receipt.mode != self.mode or receipt.generation != run["generation"] or receipt.status != "emitted":
            return False
        for memory_id, revision in zip(receipt.memory_ids, receipt.revisions):
            row = self.ledger.db.execute("SELECT current_revision,state FROM memories WHERE memory_id=? AND owner_id=?", (memory_id, self.ledger.owner_id)).fetchone()
            if not row or row["state"] != "active" or row["current_revision"] != revision:
                return False
        return True

    def session_start(self, record: dict[str, Any], backend: BackendAdapter | None, *, query: str, context: TaskContext | None = None, hits: list[BackendHit] | None = None) -> tuple[dict[str, Any], InjectionReceipt]:
        gate=self.ingest({**record,"hook_event_name":"SessionStart"}); session_digest=_digest({"host":record.get("host",self.config.rollout_host),"session_id":record.get("session_id", ""),"workspace":record.get("cwd", "")})
        if gate.status != "accepted" or self.mode is not RuntimeMode.GUARDED: return self._blocked_injection(gate.reason or "injection_disabled",session_digest,gate)
        identity=self.ledger.resolve_project(self.host.allowed_workspace() or Path(".")); context=context or TaskContext(owner_id=self.ledger.owner_id,project_id=identity["project_id"],workspace_id=gate.workspace_id)
        if hits is None:
            if backend is None: return self._blocked_injection("backend_not_configured",session_digest,gate)
            hits=backend.search(query,self.config.rollout_generation,30)
        bundle=self.policy.assemble(hits,self.config.rollout_generation,context,budget=self.config.recall_budget_tokens); digest=_digest(bundle.model_dump(mode="json")); receipt=InjectionReceipt(status="emitted",bundle_digest=digest,ledger_sequence=bundle.ledger_sequence,owner_id=bundle.owner_id,generation=bundle.generation,session_digest=session_digest,memory_ids=tuple(i.memory_id for i in bundle.items),revisions=tuple(i.revision for i in bundle.items),omissions=tuple(i.model_dump(mode="json") for i in bundle.omissions),mode=self.mode)
        if not self.verify_bundle(receipt): return self._blocked_injection("bundle_verification_failed",session_digest,gate)
        self.ledger.record_rollout_receipt("injection",receipt.model_dump(mode="json"),rollout_id=(self._run() or {}).get("rollout_id"),workspace_id=gate.workspace_id,session_id=gate.session_id)
        return ({"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":bundle.text}} if bundle.text else {}),receipt

    def _blocked_injection(self, reason: str, session_digest: str, gate: GateReceipt) -> tuple[dict[str, Any], InjectionReceipt]:
        r=InjectionReceipt(status="blocked",ledger_sequence=self.ledger.sequence,owner_id=self.ledger.owner_id,generation=self.config.rollout_generation,session_digest=session_digest,mode=self.mode,reason=reason); self.ledger.record_rollout_receipt("injection",r.model_dump(mode="json"),workspace_id=gate.workspace_id,session_id=gate.session_id); return {},r

    def record_consumption(self, receipt: InjectionReceipt, *, result: dict[str, Any]) -> dict[str, str]:
        rid=str(uuid.uuid4()); run=self._run(); digest=_digest(result)
        with self.ledger._write_transaction(): self.ledger.db.execute("INSERT INTO rollout_consumption_receipts VALUES (?,?,?,?,?,?,?,?)", (self.ledger.owner_id,rid,(run or {}).get("rollout_id"),receipt.session_digest,receipt.bundle_digest,digest,"consumed",datetime.now(timezone.utc).isoformat()))
        return {"receipt_id":rid,"digest":digest}


def _safe_mode(value: RuntimeMode | str) -> RuntimeMode:
    try: return RuntimeMode(str(value)) if str(value) in {"off","shadow","guarded"} else RuntimeMode.OFF
    except ValueError: return RuntimeMode.OFF


def _path(value: object) -> Path | None:
    if value is None or not str(value): return None
    return Path(str(value)).expanduser().resolve(strict=False)


__all__ = ["ControlAuth", "GateReceipt", "InjectionReceipt", "NormalHostAdapter", "RolloutRuntime", "RuntimeMode"]
