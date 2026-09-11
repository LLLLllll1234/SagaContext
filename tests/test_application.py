from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sagacontext.application import Application
from sagacontext.config import Config
from sagacontext.maintenance import BatchService, BatchWorker, EventJournal, ReviewService
from sagacontext.projection import Projector


class ApplicationTests(unittest.TestCase):
    def test_config_uses_one_ledger_path_under_sagacontext_home(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            with patch.dict(os.environ, {"SAGACONTEXT_HOME": str(home)}):
                config = Config.load(home / "missing.toml")
            self.assertEqual(config.ledger_path, home / "ledger-v3.db")
            self.assertEqual(config.state_path, home / "state.db")

    def test_application_reuses_owner_and_closes_idempotently(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger-v3.db"
            with Application(Config(state_path=Path(directory) / "state.db", ledger_path=path)) as first:
                owner_id = first.owner_id
                self.assertEqual(first.ledger.path, path)
            first.close()
            with Application(Config(state_path=Path(directory) / "state.db", ledger_path=path)) as second:
                self.assertEqual(second.owner_id, owner_id)

    def test_application_composes_s2_services_without_starting_workers(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Config(
                state_path=Path(directory) / "state.db",
                ledger_path=Path(directory) / "ledger-v3.db",
            )
            with Application(config) as application:
                self.assertIsInstance(application.event_journal, EventJournal)
                self.assertIsInstance(application.batches, BatchService)
                self.assertIsInstance(application.batch_worker, BatchWorker)
                self.assertIsInstance(application.reviews, ReviewService)
                self.assertIsInstance(application.projector, Projector)
                self.assertFalse(hasattr(application, "worker_task"))
                self.assertFalse(config.state_path.exists())

    def test_daemon_import_has_no_filesystem_side_effect(self):
        with tempfile.TemporaryDirectory() as directory:
            env = os.environ.copy()
            env["SAGACONTEXT_HOME"] = directory
            subprocess.run(
                [sys.executable, "-c", "import sagacontext.daemon"],
                cwd=Path(__file__).parents[1],
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
