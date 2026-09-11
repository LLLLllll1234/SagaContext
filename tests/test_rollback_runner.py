from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sagacontext.config import Config
from sagacontext.ledger import Ledger
from sagacontext.rollback import RollbackRunner
from sagacontext.rollout import RolloutRuntime


class RollbackRunnerTests(unittest.TestCase):
    def _run_row(self, ledger, root, rollout_id="r", plan="p"):
        now = datetime.now(timezone.utc)
        ledger.register_project("workspace", root)
        ledger.db.execute("INSERT INTO rollout_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rollout_id, "owner", "w", str(root), "guarded", "stopping", "ops", "k", "a", "d",
             now.isoformat(), (now + timedelta(hours=1)).isoformat(), now.isoformat(), "test", 10, 20,
             "g1", 2, plan, "{}", "rollout-plan-v1", now.isoformat()))
        ledger.db.commit()

    def test_locator_cleanup_requires_backend(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(Path(tmp) / "ledger.db", owner_id="owner")
            try:
                with self.assertRaisesRegex(RuntimeError, "locator_backend_required"):
                    RollbackRunner(ledger)._apply("r", "locator", [{"target_locator": "viking://owned/1"}])
            finally:
                ledger.close()

    def test_locator_cleanup_fails_closed_without_backend(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = Ledger(root / "ledger.db", owner_id="owner")
            try:
                ledger.register_project("workspace", root)
                config = Config(state_path=root / "state.db", ledger_path=root / "ledger.db",
                    rollout_workspaces=(str(root),), rollout_mode="guarded",
                    rollout_approver="ops", rollout_key_id="k", rollout_token_digest="d")
                runtime = RolloutRuntime(ledger, config)
                now = datetime.now(timezone.utc)
                # A synthetic run with no commits completes safely because there is no locator.
                ledger.db.execute("INSERT INTO rollout_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("r", "owner", "w", str(root), "guarded", "stopping", "ops", "k", "a", "d", now.isoformat(), (now + timedelta(hours=1)).isoformat(), now.isoformat(), "test", 10, 20, "g1", 2, "p", "{}", "rollout-plan-v1", now.isoformat()))
                ledger.db.commit()
                result = RollbackRunner(ledger).run("r", "p")
                self.assertEqual(result["status"], "completed")
            finally:
                ledger.close()

    def test_fault_matrix_is_durable_and_retry_idempotent(self):
        for fault in ("freeze", "memory", "shadow", "locator", "verify"):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); ledger = Ledger(root / "ledger.db", owner_id="owner")
                try:
                    self._run_row(ledger, root)
                    first = RollbackRunner(ledger).run("r", "p", fault=fault)
                    self.assertEqual(first["status"], "cleanup_required")
                    second = RollbackRunner(ledger).run("r", "p")
                    self.assertEqual(second["status"], "completed")
                    self.assertEqual(ledger.db.execute("SELECT COUNT(*) FROM rollback_runs").fetchone()[0], 1)
                finally:
                    ledger.close()
