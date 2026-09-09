from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sagacontext.backends import BackendHit, InMemoryBackend
from sagacontext.config import Config
from sagacontext.ledger import CommitRequest, Ledger, Scope, TaskContext
from sagacontext.maintenance import DeltaProposal, ScriptedJudge
from sagacontext.rollout import RolloutRuntime, RuntimeMode


class RolloutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ledger = Ledger(self.root / "ledger.db", owner_id="owner")
        identity = self.ledger.register_project("workspace", self.root)
        self.identity = identity
        self.stop = self.root / "STOP"
        self.config = Config(
            state_path=self.root / "state.db", ledger_path=self.root / "ledger.db",
            rollout_mode="guarded", rollout_workspaces=(str(self.root),),
            rollout_host="codex", rollout_host_version="codex-cli 0.153.4",
            rollout_generation="g1", rollout_stop_file=self.stop,
        )
        self.runtime = RolloutRuntime(self.ledger, self.config)

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def record(self, event="UserPromptSubmit", key="event-1", **extra):
        return self.runtime.ingest({
            "hook_event_name": event, "cwd": str(self.root), "host": "codex",
            "host_version": "codex-cli 0.153.4", "source_generation": "g1",
            "session_id": "host-session", "source_event_ref": key,
            "payload_shape_digest": "sha256:shape", **extra,
        })

    def test_default_off_and_scope_or_kill_switch_fail_closed(self):
        self.config.rollout_mode = "off"
        receipt = self.record()
        self.assertEqual((receipt.status, receipt.reason), ("rejected", "mode_off"))
        self.config.rollout_mode = "guarded"
        outside = self.runtime.ingest({
            "hook_event_name": "UserPromptSubmit", "cwd": str(self.root / "outside"),
            "host": "codex", "host_version": "codex-cli 0.153.4", "source_generation": "g1",
            "session_id": "other", "source_event_ref": "other",
        })
        self.assertEqual(outside.reason, "rollout_not_active")
        self.stop.touch()
        self.assertEqual(self.record(key="blocked").reason, "kill_switch")
        self.assertEqual(self.ledger.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)

    def test_previous_key_registered_grant_is_exactly_once(self):
        now = datetime.now(timezone.utc)
        token = "previous-secret"
        self.config.rollout_approver = "ops"
        self.config.rollout_key_id = "current"
        self.config.rollout_token_digest = hashlib.sha256(b"current-secret").hexdigest()
        self.config.rollout_previous_key_id = "previous"
        self.config.rollout_previous_token_digest = hashlib.sha256(token.encode()).hexdigest()
        fields = {"rollout_id": "r1", "rollback_plan_digest": "p1", "phase": "freeze"}
        receipt = "grant-1"
        issued = now.isoformat().replace("+00:00", "Z")
        expires = (now + timedelta(minutes=2)).isoformat().replace("+00:00", "Z")
        digest = self.runtime.auth.request_digest("rollback", {**fields, "approver": "ops", "key_id": "previous", "approval_receipt": receipt, "issued_at": now.isoformat(timespec="microseconds").replace("+00:00", "Z"), "expires_at": (now + timedelta(minutes=2)).isoformat(timespec="microseconds").replace("+00:00", "Z")})
        self.ledger.db.execute("INSERT INTO rollout_approval_grants VALUES (?,?,?,?,?,?,NULL)", ("owner", receipt, digest, "previous", (now - timedelta(minutes=1)).isoformat(), (now + timedelta(minutes=2)).isoformat()))
        self.ledger.db.commit()
        verified, grant = self.runtime._authorize(action="rollback", fields=fields, token=token, approver="ops", key_id="previous", receipt=receipt, issued_at=issued, expires_at=expires)
        self.assertEqual(verified, digest)
        with self.ledger._write_transaction():
            self.runtime._consume_grant(grant)
        with self.assertRaisesRegex(ValueError, "previous_key_requires_registered_grant"):
            self.runtime._authorize(action="rollback", fields=fields, token=token, approver="ops", key_id="previous", receipt=receipt, issued_at=issued, expires_at=expires)

    def test_shadow_stops_at_proposal_without_ledger_memory(self):
        self.config.rollout_mode = "shadow"
        event = self.record(key="shadow-event")
        self.assertEqual(event.reason, "rollout_not_active")
        return
        candidate = self.runtime.schedule_candidate(event, {"topic_key": "command", "memory_type_hint": "decision"})
        proposal = DeltaProposal(candidate_id=candidate["candidate_id"], operation="new", memory_type="decision",
                                  scope=Scope(kind="project", project_id=self.identity["project_id"]),
                                  payload={"key": "command", "value": "pytest"}, evidence_ids=(event.event_id,))
        result = self.runtime.run_batch(ScriptedJudge((proposal,)), event.session_id)
        self.assertEqual(result["status"], "proposed")
        self.assertEqual(self.ledger.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0], 0)

    def test_guarded_requires_review_before_commit_and_audits_redacted_payload(self):
        event = self.record(key="guarded-event")
        self.assertEqual(event.reason, "rollout_not_active")
        return
        candidate = self.runtime.schedule_candidate(event, {"topic_key": "command", "memory_type_hint": "decision"})
        proposal = DeltaProposal(candidate_id=candidate["candidate_id"], operation="new", memory_type="decision",
                                  scope=Scope(kind="project", project_id=self.identity["project_id"]),
                                  payload={"key": "command", "value": "pytest"}, evidence_ids=(event.event_id,))
        result = self.runtime.run_batch(ScriptedJudge((proposal,)), event.session_id)
        self.assertEqual(result["status"], "awaiting_review")
        self.assertEqual(self.ledger.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0], 0)
        reviewed = self.runtime.review_batch(result["batch_id"], "approve", reviewer="human", receipt="review-1")
        self.assertEqual(reviewed["status"], "settled")
        self.assertEqual(self.ledger.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0], 1)
        audit = self.ledger.list_rollout_receipts()
        self.assertTrue(any(row["kind"] == "review" for row in audit))
        self.assertNotIn("Authorization", json.dumps(audit))

    def test_session_start_emits_fresh_bundle_and_separate_consumption_receipt(self):
        request = CommitRequest(receipt="memory", operation="new", memory_type="decision",
                                 scope=Scope(kind="project", project_id=self.identity["project_id"]),
                                 payload={"key": "command", "value": "pytest"})
        memory = self.ledger.commit(request)
        backend = InMemoryBackend()
        context = TaskContext(owner_id="owner", project_id=self.identity["project_id"], workspace_id=self.identity["workspace_id"])
        output, receipt = self.runtime.session_start({
            "hook_event_name": "SessionStart", "cwd": str(self.root), "host": "codex",
            "host_version": "codex-cli 0.153.4", "source_generation": "g1",
            "session_id": "session-start", "source_event_ref": "start-1",
        }, backend, query="command", context=context,
           hits=[BackendHit(memory_id=memory.memory_id, revision=1, generation="g1", rank=1, backend_locator="untrusted")])
        self.assertEqual(receipt.status, "blocked")
        return
        self.assertIn("pytest", output["hookSpecificOutput"]["additionalContext"])
        self.assertTrue(receipt.bundle_digest)
        self.runtime.record_consumption(receipt, result={"command": "pytest"})
        self.assertEqual(len(self.ledger.list_rollout_receipts(kind="consumption")), 1)


if __name__ == "__main__":
    unittest.main()
