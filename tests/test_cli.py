from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from sagacontext.cli import app
from sagacontext.config import Config
from sagacontext.ledger import Ledger


class CliIntegrationTests(unittest.TestCase):
    def test_cli_and_daemon_config_share_ledger_and_leave_legacy_state_untouched(self):
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            legacy = home / "state.db"
            legacy.write_bytes(b"legacy")
            before = (legacy.read_bytes(), legacy.stat().st_mtime_ns)
            with patch.dict(os.environ, {"SAGACONTEXT_HOME": str(home)}), patch(
                "sagacontext.store.Store", side_effect=AssertionError("legacy Store constructed")
            ), patch(
                "sagacontext.ov_client.OpenVikingClient",
                side_effect=AssertionError("OpenViking client constructed"),
            ):
                project_root = home / "project"
                project_root.mkdir()
                registered = runner.invoke(
                    app, ["project-register", "project", str(project_root)]
                )
                self.assertEqual(registered.exit_code, 0, registered.output)
                identity = json.loads(registered.output)

                extra = home / "extra-workspace"
                extra.mkdir()
                bound = runner.invoke(
                    app, ["project-bind", identity["project_id"], str(extra)]
                )
                self.assertEqual(bound.exit_code, 0, bound.output)
                bound_workspace = json.loads(bound.output)["workspace_id"]
                moved = home / "moved-workspace"
                extra.rename(moved)
                rebound = runner.invoke(
                    app,
                    [
                        "project-rebind",
                        identity["project_id"],
                        str(extra),
                        str(moved),
                    ],
                )
                self.assertEqual(rebound.exit_code, 0, rebound.output)
                self.assertEqual(json.loads(rebound.output)["workspace_id"], bound_workspace)

                task = runner.invoke(
                    app, ["task-create", identity["project_id"], "close S1"]
                )
                self.assertEqual(task.exit_code, 0, task.output)
                task_id = json.loads(task.output)["task_id"]
                session = runner.invoke(
                    app, ["session-open", "synthetic", "cli-session", identity["workspace_id"]]
                )
                self.assertEqual(session.exit_code, 0, session.output)
                session_id = json.loads(session.output)["session_id"]
                binding = runner.invoke(
                    app, ["task-bind", session_id, task_id, "event-1"]
                )
                self.assertEqual(binding.exit_code, 0, binding.output)

                commit_input = home / "commit.json"
                commit_input.write_text(
                    json.dumps(
                        {
                            "receipt": "cli-commit",
                            "operation": "new",
                            "memory_type": "convention",
                            "scope": {"kind": "project", "project_id": identity["project_id"]},
                            "payload": {"rule": "use the local ledger"},
                        }
                    )
                )
                committed = runner.invoke(app, ["memory-commit", "--input", str(commit_input)])
                self.assertEqual(committed.exit_code, 0, committed.output)
                memory_id = json.loads(committed.output)["memory_id"]

                current_input = home / "current.json"
                current_input.write_text(
                    json.dumps(
                        {
                            "memory_ids": [memory_id],
                            "context": {
                                "project_id": identity["project_id"],
                                "workspace_id": identity["workspace_id"],
                                "task_id": task_id,
                            },
                        }
                    )
                )
                current = runner.invoke(app, ["memory-current", "--input", str(current_input)])
                self.assertEqual(current.exit_code, 0, current.output)
                self.assertEqual(json.loads(current.output)[0]["memory_id"], memory_id)
                context_input = home / "context.json"
                context_input.write_text(json.dumps(json.loads(current_input.read_text())["context"]))
                history = runner.invoke(
                    app,
                    ["memory-history", memory_id, "--input", str(context_input)],
                )
                self.assertEqual(history.exit_code, 0, history.output)
                self.assertEqual(len(json.loads(history.output)), 1)

                forgotten = runner.invoke(app, ["memory-forget", memory_id, "cli-forget"])
                self.assertEqual(forgotten.exit_code, 0, forgotten.output)
                deletion = json.loads(forgotten.output)
                self.assertEqual(deletion["status"], "local_redacted")
                deletion_status = runner.invoke(
                    app, ["deletion-status", deletion["job_id"]]
                )
                self.assertEqual(deletion_status.exit_code, 0, deletion_status.output)
                self.assertEqual(json.loads(deletion_status.output)["pending_outbox"], 0)
                outbox = runner.invoke(app, ["outbox-list"])
                self.assertEqual(outbox.exit_code, 0, outbox.output)
                self.assertEqual(json.loads(outbox.output), [])

                config = Config.load()
                self.assertEqual(config.ledger_path, home / "ledger-v3.db")
                ledger = Ledger(config.ledger_path)
                try:
                    self.assertEqual(ledger.owner_id, json.loads(current.output)[0]["owner_id"])
                finally:
                    ledger.close()

            self.assertEqual((legacy.read_bytes(), legacy.stat().st_mtime_ns), before)

    def test_old_runtime_commands_are_not_registered(self):
        names = {command.name for command in app.registered_commands}
        self.assertTrue(
            {"add", "show", "pending", "review", "tasks", "coldstart", "bench"}.isdisjoint(names)
        )


if __name__ == "__main__":
    unittest.main()
