from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from sagacontext.config import Config
from sagacontext.daemon import create_app
from sagacontext.ledger import Ledger, Scope
from sagacontext.maintenance import DeltaProposal, ScriptedJudge
from sagacontext.rollout import RolloutRuntime


def canonical_digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def stamp(value):
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


class RolloutAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.ledger = Ledger(self.root / "ledger.db", owner_id="owner")
        self.identity = self.ledger.register_project("test", self.root)
        self.config = Config(state_path=self.root / "state.db", ledger_path=self.ledger.path,
            rollout_workspaces=(str(self.root),), rollout_mode="guarded",
            rollout_approver="ops", rollout_key_id="k1",
            rollout_token_digest=hashlib.sha256(b"synthetic-key-one").hexdigest(),
            rollout_stop_file=self.root / "STOP")
        self.runtime = RolloutRuntime(self.ledger, self.config)
        now = datetime.now(timezone.utc)
        self.auth = dict(token="synthetic-key-one", approver="ops", key_id="k1",
            issued_at=stamp(now - timedelta(seconds=1)), expires_at=stamp(now + timedelta(minutes=3)))
        self.plan = {"schema_version": "rollout-plan-v1", "resources": "rollout-scoped"}
        self.activation = dict(mode="guarded", workspace=str(self.root), approval_receipt="activate",
            deadline=now + timedelta(hours=1), rollback_plan=self.plan, **self.auth)

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def digest(self, action, receipt, fields):
        return canonical_digest({"digest_schema_version": "rollout-action-v1", "action": action,
            "owner": "owner", "workspace": str(self.root), **fields,
            **{k: v for k, v in self.auth.items() if k != "token"}, "approval_receipt": receipt})

    def activation_digest(self):
        return self.digest("activation", "activate", dict(mode="guarded",
            deadline=stamp(self.activation["deadline"]), max_sessions=10, max_candidates=20,
            generation="g1", rollback_plan_digest=canonical_digest(self.plan)))

    def register(self, action="activation", target="activate", digest=None, **overrides):
        args = dict(action=action, target_receipt=target,
            request_digest=digest or self.activation_digest(), receipt="register-" + target,
            expires_at=datetime.fromisoformat(self.auth["expires_at"]),
            auth_expires_at=self.auth["expires_at"],
            **{k: v for k, v in self.auth.items() if k != "expires_at"})
        args.update(overrides)
        return self.runtime.register_grant(**args)

    def rotate(self):
        self.config = replace(self.config, rollout_key_id="k2",
            rollout_token_digest=hashlib.sha256(b"synthetic-key-two").hexdigest(),
            rollout_previous_key_id="k1", rollout_previous_token_digest=self.config.rollout_token_digest)
        self.runtime = RolloutRuntime(self.ledger, self.config)

    def current_auth(self):
        return {**self.auth, "key_id": "k2", "token": "synthetic-key-two"}

    def count(self, table):
        return self.ledger.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def consumed(self, receipt="activate"):
        return self.ledger.db.execute("SELECT consumed_at FROM rollout_approval_grants WHERE receipt=?",
                                      (receipt,)).fetchone()[0]

    def pending_batch(self):
        event = self.runtime.ingest(dict(hook_event_name="UserPromptSubmit", cwd=str(self.root),
            host="codex", host_version=self.config.rollout_host_version, source_generation="g1",
            session_id="session", source_event_ref="event"))
        self.assertEqual(event.status, "accepted")
        candidate = self.runtime.schedule_candidate(event, {"topic_key": "test"})
        proposal = DeltaProposal(candidate_id=candidate["candidate_id"], operation="new",
            memory_type="decision", scope=Scope(kind="project", project_id=self.identity["project_id"]),
            payload={"key": "command", "value": "pytest"}, evidence_ids=(event.event_id,))
        result = self.runtime.run_batch(ScriptedJudge((proposal,)), event.session_id)
        self.assertEqual(result["status"], "awaiting_review")
        return result["batch_id"]

    def prepare_review(self):
        run = self.runtime.activate(**self.activation)
        batch = self.pending_batch()
        fields = dict(rollout_id=run["rollout_id"], batch=batch, decision="approve", reviewer="ops")
        self.register("review", "review", self.digest("review", "review", fields))
        self.rotate()
        return batch

    def test_public_activation_consumes_grant_and_retries_after_restart_and_stop(self):
        self.register()
        self.rotate()
        result = self.runtime.activate(**self.activation)
        consumed = self.consumed()
        self.assertTrue(consumed)
        self.runtime.stop(reason="test")
        self.runtime = RolloutRuntime(self.ledger, self.config)
        self.assertEqual(self.runtime.activate(**self.activation), result)
        self.assertEqual(self.consumed(), consumed)
        self.assertEqual(self.count("rollout_runs"), 1)
        self.assertEqual(self.count("rollout_control_keys"), 2)

    def test_current_activation_is_idempotent_while_live(self):
        first = self.runtime.activate(**self.activation)
        self.assertEqual(self.runtime.activate(**self.activation), first)
        with self.assertRaisesRegex(ValueError, "receipt_conflict"):
            self.runtime.activate(**{**self.activation, "max_sessions": 9})

    def test_previous_key_without_grant_and_wrong_token_are_rejected(self):
        self.rotate()
        for changes, reason in [({}, "registered_grant"), ({"token": "wrong"}, "invalid_operator_token")]:
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, reason):
                self.runtime.activate(**{**self.activation, **changes})
        self.assertEqual(self.count("rollout_runs"), 0)

    def test_grant_tampering_does_not_consume_or_create_rollout(self):
        self.register()
        self.rotate()
        for change in ({"max_sessions": 9}, {"rollback_plan": {**self.plan, "resources": "other"}},
                       {"deadline": self.activation["deadline"] + timedelta(minutes=1)}):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "registered_grant_mismatch"):
                self.runtime.activate(**{**self.activation, **change})
            self.assertIsNone(self.consumed())
            self.assertEqual(self.count("rollout_runs"), 0)

    def test_expired_grant_and_registration_at_rotation_are_rejected(self):
        self.register()
        self.rotate()
        original = self.ledger.db.execute("SELECT * FROM rollout_approval_grants").fetchone()
        retired = self.ledger.db.execute("SELECT retired_at FROM rollout_control_keys WHERE key_id='k1'").fetchone()[0]
        for column, value, reason in (
            ("expires_at", stamp(datetime.now(timezone.utc) - timedelta(seconds=1)), "grant_expired"),
            ("registered_at", retired, "grant_registered_after_rotation"),
        ):
            with self.subTest(column=column):
                self.ledger.db.execute(f"UPDATE rollout_approval_grants SET {column}=?", (value,))
                with self.assertRaisesRegex(ValueError, reason):
                    self.runtime.activate(**self.activation)
                self.assertIsNone(self.consumed())
                self.ledger.db.execute(f"UPDATE rollout_approval_grants SET {column}=?", (original[column],))

    def test_registration_idempotency_conflicts_and_expiry_limit(self):
        result = self.register()
        self.assertEqual(self.register(), result)
        with self.assertRaisesRegex(ValueError, "receipt_conflict"):
            self.register(request_digest="a" * 64)
        for kwargs in ({"target_receipt": "register-activate"},
                       {"expires_at": datetime.now(timezone.utc) + timedelta(hours=1)},
                       {"action": "rollback"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.register(receipt="another", **kwargs)
        self.assertEqual(self.count("rollout_approval_grants"), 1)
        self.assertEqual(self.count("rollout_action_receipts"), 1)

    def test_previous_key_and_stale_process_cannot_register_after_rotation(self):
        stale = self.runtime
        self.rotate()
        with self.assertRaisesRegex(ValueError, "grant_requires_current_key"):
            self.register()
        self.runtime = stale
        with self.assertRaisesRegex(ValueError, "configuration_stale"):
            self.register()
        self.assertEqual(self.count("rollout_approval_grants"), 0)

    def test_missing_persistent_rotation_evidence_rejects_previous_key(self):
        # Config alone cannot attest that k0 ever existed or was retired on this server.
        self.config.rollout_previous_key_id = "k0"
        self.config.rollout_previous_token_digest = hashlib.sha256(b"synthetic-key-zero").hexdigest()
        with self.assertRaisesRegex(ValueError, "invalid_operator_token"):
            self.runtime.activate(**{**self.activation, "key_id": "k0", "token": "synthetic-key-zero"})

    def test_public_review_commits_once_and_retry_after_stop(self):
        batch = self.prepare_review()
        result = self.runtime.review_batch(batch, "approve", reviewer="ops", receipt="review", **self.auth)
        self.assertEqual(result["status"], "settled")
        self.assertEqual(self.count("memories"), 1)
        self.assertEqual(self.count("rollout_review_receipts"), 1)
        self.assertTrue(self.consumed("review"))
        self.runtime.stop()
        self.assertEqual(self.runtime.review_batch(batch, "approve", reviewer="ops", receipt="review", **self.auth), result)
        with self.assertRaisesRegex(ValueError, "receipt_conflict"):
            self.runtime.review_batch(batch, "reject", reviewer="ops", receipt="review", **self.auth)
        self.assertEqual(self.count("memories"), 1)

    def test_failed_commit_rolls_back_grant_and_all_review_states(self):
        batch = self.prepare_review()
        with patch.object(self.ledger, "commit_batch", side_effect=ValueError("expected_head_changed")):
            with self.assertRaisesRegex(ValueError, "expected_head_changed"):
                self.runtime.review_batch(batch, "approve", reviewer="ops", receipt="review", **self.auth)
        self.assertIsNone(self.consumed("review"))
        for table in ("batches", "proposals", "candidates"):
            self.assertEqual(self.ledger.db.execute(f"SELECT status FROM {table}").fetchone()[0], "awaiting_review")
        self.assertEqual(self.count("memories"), 0)
        self.assertEqual(self.count("rollout_review_receipts"), 0)
        self.runtime.review_batch(batch, "approve", reviewer="ops", receipt="review", **self.auth)
        self.assertEqual(self.count("memories"), 1)

    def test_grant_expires_during_commit_rolls_back_memory_and_consumption(self):
        batch = self.prepare_review()
        real_commit = self.ledger.commit_batch
        future = datetime.now(timezone.utc) + timedelta(minutes=4)
        class Future(datetime):
            @classmethod
            def now(cls, tz=None):
                return future
        def advance(*args, **kwargs):
            result = real_commit(*args, **kwargs)
            clock_patch.start()
            return result
        clock_patch = patch("sagacontext.rollout.datetime", Future)
        try:
            with patch.object(self.ledger, "commit_batch", side_effect=advance):
                with self.assertRaisesRegex(ValueError, "authorization_expired"):
                    self.runtime.review_batch(batch, "approve", reviewer="ops", receipt="review", **self.auth)
        finally:
            clock_patch.stop()
        self.assertIsNone(self.consumed("review"))
        self.assertEqual(self.count("memories"), 0)

    def test_concurrent_activation_returns_one_stable_result(self):
        self.register()
        self.rotate()
        barrier = threading.Barrier(2)
        def invoke():
            ledger = Ledger(self.ledger.path, owner_id="owner")
            try:
                runtime = RolloutRuntime(ledger, self.config)
                barrier.wait(timeout=5)
                return runtime.activate(**self.activation)
            finally:
                ledger.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = list(pool.map(lambda _: invoke(), range(2)))
        self.assertEqual(a, b)
        self.assertEqual(self.count("rollout_runs"), 1)
        self.assertTrue(self.consumed())

    def test_concurrent_review_commits_memory_once(self):
        batch = self.prepare_review()
        barrier = threading.Barrier(2)
        def invoke():
            ledger = Ledger(self.ledger.path, owner_id="owner")
            try:
                runtime = RolloutRuntime(ledger, self.config)
                barrier.wait(timeout=5)
                return runtime.review_batch(batch, "approve", reviewer="ops", receipt="review", **self.auth)
            finally:
                ledger.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = list(pool.map(lambda _: invoke(), range(2)))
        self.assertEqual(a, b)
        self.assertEqual(self.count("memories"), 1)
        self.assertEqual(self.count("rollout_review_receipts"), 1)

    def test_rollback_target_plan_phase_and_current_key_boundary(self):
        run = self.runtime.activate(**self.activation)
        self.rotate()
        args = dict(rollout_id=run["rollout_id"], plan_digest=canonical_digest(self.plan),
                    phase="freeze", receipt="rollback")
        with self.assertRaisesRegex(ValueError, "rollback_requires_current_key"):
            self.runtime.rollback(**args, **self.auth)
        for changes in ({"rollout_id": "other"}, {"plan_digest": "wrong"}, {"phase": "delete"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.runtime.rollback(**{**args, **changes}, **self.current_auth())
        self.assertEqual(self.ledger.db.execute("SELECT status FROM rollout_runs").fetchone()[0], "running")
        result = self.runtime.rollback(**args, **self.current_auth())
        self.assertEqual(result["status"], "frozen")
        self.assertEqual(self.runtime.rollback(**args, **self.current_auth()), result)
        for changes in ({"rollout_id": "other"}, {"plan_digest": "wrong"}, {"phase": "delete"}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "receipt_conflict"):
                self.runtime.rollback(**{**args, **changes}, **self.current_auth())

    def test_activation_off_active_quotas_and_deadline_rejected(self):
        for changes in ({"mode": "active"}, {"max_sessions": 11}, {"max_candidates": 21},
                       {"deadline": datetime.now(timezone.utc) - timedelta(seconds=1)},
                       {"generation": "other"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.runtime.activate(**{**self.activation, **changes})
        self.config.rollout_mode = "off"
        with self.assertRaisesRegex(ValueError, "configuration_off"):
            self.runtime.activate(**self.activation)
        self.assertEqual(self.count("rollout_runs"), 0)

    def test_expired_or_naive_auth_rejected(self):
        for changes in ({"expires_at": stamp(datetime.now(timezone.utc) - timedelta(milliseconds=1))},
                       {"issued_at": "2026-09-09T00:00:00"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.runtime.activate(**{**self.activation, **changes})
        self.assertEqual(self.count("rollout_runs"), 0)

    def test_http_grant_registration_rotation_activation_and_retry(self):
        headers = {"Authorization": "Bearer synthetic-key-one", "X-SagaContext-Approver": "ops",
            "X-SagaContext-Key-Id": "k1", "X-SagaContext-Approval-Receipt": "register-activate",
            "X-SagaContext-Issued-At": self.auth["issued_at"],
            "X-SagaContext-Expires-At": self.auth["expires_at"]}
        body = dict(action="activation", target_receipt="activate",
                    request_digest=self.activation_digest(), expires_at=self.auth["expires_at"])
        with TestClient(create_app(self.config)) as client:
            self.assertEqual(client.post("/rollout/grants", json=body).status_code, 400)
            registered = client.post("/rollout/grants", json=body, headers=headers)
            self.assertEqual(registered.status_code, 200, registered.text)
            self.assertEqual(client.post("/rollout/grants", json=body, headers=headers).json(), registered.json())
        self.rotate()
        headers["X-SagaContext-Approval-Receipt"] = "activate"
        body = dict(mode="guarded", workspace=str(self.root), deadline=stamp(self.activation["deadline"]), rollback_plan=self.plan)
        with TestClient(create_app(self.config)) as client:
            result = client.post("/rollout/mode", json=body, headers=headers)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(client.post("/rollout/mode", json=body, headers=headers).json(), result.json())
            self.assertEqual(client.post("/rollout/mode", json={**body, "max_sessions": 9}, headers=headers).status_code, 400)
        self.assertTrue(self.consumed())
        self.assertEqual(self.count("rollout_runs"), 1)

    def test_review_in_draining_and_eleventh_session_rejected(self):
        self.runtime.activate(**self.activation)
        batch = self.pending_batch()
        for i in range(9):
            result = self.runtime.ingest(dict(hook_event_name="SessionStart", cwd=str(self.root),
                host_version=self.config.rollout_host_version, source_generation="g1",
                session_id=f"extra-{i}", source_event_ref=f"extra-{i}"))
            self.assertEqual(result.status, "accepted")
        self.assertEqual(self.ledger.db.execute("SELECT status FROM rollout_runs").fetchone()[0], "draining")
        blocked = self.runtime.ingest(dict(hook_event_name="SessionStart", cwd=str(self.root),
            host_version=self.config.rollout_host_version, source_generation="g1",
            session_id="eleventh", source_event_ref="eleventh"))
        self.assertEqual(blocked.reason, "session_limit")
        self.runtime.review_batch(batch, "approve", reviewer="ops", receipt="review", **self.auth)
        self.assertEqual(self.count("memories"), 1)
        self.assertEqual(self.count("rollout_sessions"), 10)

    def test_stop_during_review_rolls_back_memory_and_grant(self):
        batch = self.prepare_review()
        real_commit = self.ledger.commit_batch
        def stop_after_commit(*args, **kwargs):
            result = real_commit(*args, **kwargs)
            self.config.rollout_stop_file.touch()
            return result
        with patch.object(self.ledger, "commit_batch", side_effect=stop_after_commit):
            with self.assertRaisesRegex(ValueError, "stopped_during_review"):
                self.runtime.review_batch(batch, "approve", reviewer="ops", receipt="review", **self.auth)
        self.assertEqual(self.count("memories"), 0)
        self.assertIsNone(self.consumed("review"))
        self.assertEqual(self.runtime.mode.value, "off")
        self.assertEqual(self.ledger.db.execute("SELECT control_epoch FROM rollout_runs").fetchone()[0], 2)
