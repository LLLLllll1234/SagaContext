"""Durable, rollout-scoped rollback execution."""
from __future__ import annotations
import json, uuid
from datetime import datetime, timezone
from .ledger import Ledger

PHASES = ("freeze", "memory", "shadow", "locator", "verify")

class RollbackRunner:
    def __init__(self, ledger: Ledger): self.ledger = ledger

    def run(self, rollout_id: str, plan_digest: str, *, fault: str | None = None, backend=None) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        with self.ledger._write_transaction():
            run = self.ledger.db.execute("SELECT * FROM rollout_runs WHERE rollout_id=? AND owner_id=?", (rollout_id, self.ledger.owner_id)).fetchone()
            if not run or run["rollback_plan_digest"] != plan_digest: raise ValueError("rollback_plan_mismatch")
            existing = self.ledger.db.execute("SELECT * FROM rollback_runs WHERE rollout_id=?", (rollout_id,)).fetchone()
            if existing and existing["status"] == "completed": return {"rollout_id": rollout_id, "status": "completed", "rollback_id": existing["rollback_id"]}
            rollback_id = existing["rollback_id"] if existing else str(uuid.uuid4())
            if not existing:
                self.ledger.db.execute("INSERT INTO rollback_runs VALUES (?,?,?,?,?,?,?,?)", (rollback_id, rollout_id, self.ledger.owner_id, plan_digest, "running", run["control_epoch"] + 1, now, now))
                self.ledger.db.execute("UPDATE rollout_runs SET status='stopping',control_epoch=control_epoch+1,stopped_at=?,stop_reason='rollback' WHERE rollout_id=?", (now, rollout_id))
        for index, phase in enumerate(PHASES, 1):
            with self.ledger._write_transaction():
                step = self.ledger.db.execute("SELECT * FROM rollback_steps WHERE rollback_id=? AND step_no=?", (rollback_id,index)).fetchone()
                if step and step["status"] == "done": continue
                target = self._targets(rollout_id, phase)
                self.ledger.db.execute("INSERT OR REPLACE INTO rollback_steps VALUES (?,?,?,?,?,?,?,?,?)", (rollback_id,index,phase,"running",json.dumps(target, sort_keys=True),str(uuid.uuid4()),None,now,None))
            try:
                if fault == phase: raise RuntimeError("injected_compensation_failure")
                self._apply(rollout_id, phase, target, backend=backend)
                status, error = "done", None
            except Exception as exc:
                status, error = "cleanup_required", type(exc).__name__
            with self.ledger._write_transaction():
                self.ledger.db.execute("UPDATE rollback_steps SET status=?,error_class=?,finished_at=? WHERE rollback_id=? AND step_no=?", (status,error,datetime.now(timezone.utc).isoformat(),rollback_id,index))
                if status == "cleanup_required":
                    self.ledger.db.execute("UPDATE rollback_runs SET status='cleanup_required',updated_at=? WHERE rollback_id=?", (datetime.now(timezone.utc).isoformat(),rollback_id))
                    return {"rollout_id": rollout_id, "rollback_id": rollback_id, "status": status, "phase": phase, "error_class": error}
        with self.ledger._write_transaction():
            self.ledger.db.execute("UPDATE rollback_runs SET status='completed',updated_at=? WHERE rollback_id=?", (datetime.now(timezone.utc).isoformat(),rollback_id))
            self.ledger.db.execute("UPDATE rollout_runs SET status='stopped' WHERE rollout_id=?", (rollout_id,))
        return {"rollout_id": rollout_id, "rollback_id": rollback_id, "status": "completed"}

    def _targets(self, rollout_id, phase):
        if phase == "memory": return [dict(r) for r in self.ledger.db.execute("SELECT memory_id,revision FROM rollout_commits WHERE rollout_id=?", (rollout_id,))]
        if phase == "locator": return [dict(r) for r in self.ledger.db.execute("SELECT o.outbox_id,o.target_locator FROM outbox o JOIN rollout_commits c ON c.outbox_id=o.outbox_id WHERE c.rollout_id=?", (rollout_id,))]
        if phase == "shadow": return [dict(r) for r in self.ledger.db.execute("SELECT candidate_id FROM rollout_candidates WHERE rollout_id=?", (rollout_id,))]
        return []

    def _apply(self, rollout_id, phase, targets, *, backend=None):
        if phase == "memory":
            for item in targets: self.ledger.forget(item["memory_id"], f"rollback:{rollout_id}:{item['memory_id']}")
        elif phase == "shadow":
            with self.ledger._write_transaction():
                self.ledger.db.execute("UPDATE candidates SET status='quarantined' WHERE candidate_id IN (SELECT candidate_id FROM rollout_candidates WHERE rollout_id=?)", (rollout_id,))
        elif phase == "locator":
            locators = [item["target_locator"] for item in targets if item.get("target_locator")]
            if not locators:
                return
            if backend is None or not hasattr(backend, "remove_projection"):
                raise RuntimeError("locator_backend_required")
            result = backend.remove_projection(locators)
            if result is None:
                raise RuntimeError("locator_removal_unconfirmed")
        elif phase == "verify":
            pending = self.ledger.db.execute(
                "SELECT COUNT(*) FROM outbox o JOIN rollout_commits c ON c.outbox_id=o.outbox_id "
                "WHERE c.rollout_id=? AND o.status NOT IN ('confirmed','compensated')", (rollout_id,)
            ).fetchone()[0]
            if pending:
                raise RuntimeError("rollback_outbox_pending")
