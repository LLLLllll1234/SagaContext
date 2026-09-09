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
            rollout_approver="ops", rollout_key_id="k1",
            rollout_token_digest=hashlib.sha256(b"test-key-one").hexdigest(),
        )
        self.runtime = RolloutRuntime(self.ledger, self.config)

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def auth(self):
        now = datetime.now(timezone.utc)
        return dict(token="test-key-one", approver="ops", key_id="k1",
                    issued_at=now.isoformat(), expires_at=(now + timedelta(minutes=3)).isoformat())

    def activate(self, mode="guarded"):
        return self.runtime.activate(mode=mode, workspace=str(self.root), approval_receipt="activation",
            deadline=datetime.now(timezone.utc) + timedelta(hours=1), **self.auth())

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

    def test_shadow_stops_at_proposal_without_ledger_memory(self):
        self.config.rollout_mode = "shadow"
        self.activate("shadow")
        event = self.record(key="shadow-event")
        self.assertEqual(event.status, "accepted")
        candidate = self.runtime.schedule_candidate(event, {"topic_key": "command", "memory_type_hint": "decision"})
        proposal = DeltaProposal(candidate_id=candidate["candidate_id"], operation="new", memory_type="decision",
                                  scope=Scope(kind="project", project_id=self.identity["project_id"]),
                                  payload={"key": "command", "value": "pytest"}, evidence_ids=(event.event_id,))
        result = self.runtime.run_batch(ScriptedJudge((proposal,)), event.session_id)
        self.assertEqual(result["status"], "proposed")
        self.assertEqual(self.ledger.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0], 0)

    def test_guarded_requires_review_before_commit_and_audits_redacted_payload(self):
        self.activate()
        event = self.record(key="guarded-event")
        self.assertEqual(event.status, "accepted")
        candidate = self.runtime.schedule_candidate(event, {"topic_key": "command", "memory_type_hint": "decision"})
        proposal = DeltaProposal(candidate_id=candidate["candidate_id"], operation="new", memory_type="decision",
                                  scope=Scope(kind="project", project_id=self.identity["project_id"]),
                                  payload={"key": "command", "value": "pytest"}, evidence_ids=(event.event_id,))
        result = self.runtime.run_batch(ScriptedJudge((proposal,)), event.session_id)
        self.assertEqual(result["status"], "awaiting_review")
        self.assertEqual(self.ledger.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0], 0)
        reviewed = self.runtime.review_batch(result["batch_id"], "approve", reviewer="ops", receipt="review-1", **self.auth())
        self.assertEqual(reviewed["status"], "settled")
        self.assertEqual(self.ledger.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0], 1)
        audit = self.ledger.list_rollout_receipts()
        self.assertTrue(any(row["kind"] == "review" for row in audit))
        self.assertNotIn("Authorization", json.dumps(audit))

    def test_session_start_emits_fresh_bundle_and_separate_consumption_receipt(self):
        self.activate()
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
        self.assertEqual(receipt.status, "emitted")
        self.assertIn("pytest", output["hookSpecificOutput"]["additionalContext"])
        self.assertTrue(receipt.bundle_digest)
        self.runtime.record_consumption(receipt, result={"command": "pytest"})
        self.assertEqual(self.ledger.db.execute("SELECT COUNT(*) FROM rollout_consumption_receipts").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
