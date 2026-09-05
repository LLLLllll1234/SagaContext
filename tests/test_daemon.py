from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from sagacontext.config import Config
from sagacontext.daemon import create_app


def _fingerprint(path: Path) -> tuple[str, int, int]:
    stat = path.stat()
    return hashlib.sha256(path.read_bytes()).hexdigest(), stat.st_size, stat.st_mtime_ns


def _table_counts(client: TestClient) -> tuple[int, dict[str, int]]:
    ledger = client.app.state.runtime.ledger
    tables = [
        row[0]
        for row in ledger.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    return ledger.sequence, {
        table: ledger.db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        for table in tables
    }


class DaemonIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = Config(
            state_path=self.root / "state.db",
            ledger_path=self.root / "ledger-v3.db",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_events_is_501_and_has_no_database_side_effects(self):
        self.config.state_path.write_bytes(b"legacy-state-must-not-change")
        legacy_before = _fingerprint(self.config.state_path)
        api = create_app(self.config)
        with patch("sagacontext.store.Store", side_effect=AssertionError("legacy Store constructed")), patch(
            "sagacontext.ov_client.OpenVikingClient",
            side_effect=AssertionError("OpenViking client constructed"),
        ):
            with TestClient(api) as client:
                before = _table_counts(client)
                response = client.post("/events", content=b"not-json")
                after = _table_counts(client)
                runtime = client.app.state.runtime

        self.assertEqual(response.status_code, 501)
        self.assertEqual(response.json(), {"status": "host_ingestion_disabled", "stage": "S1"})
        self.assertEqual(after, before)
        self.assertEqual(_fingerprint(self.config.state_path), legacy_before)
        with self.assertRaises(sqlite3.ProgrammingError):
            runtime.ledger.db.execute("SELECT 1")

    def test_events_does_not_create_missing_legacy_database(self):
        with TestClient(create_app(self.config)) as client:
            self.assertEqual(client.post("/events", json={"private": "ignored"}).status_code, 501)
        self.assertFalse(self.config.state_path.exists())

    def test_local_api_closes_project_task_memory_and_deletion_flow(self):
        project_root = self.root / "project"
        project_root.mkdir()
        with TestClient(create_app(self.config)) as client:
            health = client.get("/health").json()
            self.assertEqual(health["host_ingestion"], "disabled")
            self.assertEqual(health["ledger_path"], str(self.config.ledger_path))

            identity = client.post(
                "/projects/register", json={"name": "project", "location": str(project_root)}
            ).json()
            task = client.post(
                "/tasks", json={"project_id": identity["project_id"], "goal": "close S1"}
            ).json()
            session = client.post(
                "/sessions",
                json={
                    "host": "synthetic",
                    "host_session_id": "host-session-1",
                    "workspace_id": identity["workspace_id"],
                },
            ).json()
            binding = client.post(
                f'/sessions/{session["session_id"]}/tasks/{task["task_id"]}',
                json={"start_event_id": "event-1"},
            )
            self.assertEqual(binding.status_code, 200)

            client.app.state.runtime.ledger.register_backend_generation("test", "g1")
            commit = client.post(
                "/memories/commit",
                json={
                    "receipt": "commit-1",
                    "operation": "new",
                    "memory_type": "decision",
                    "scope": {"kind": "project", "project_id": identity["project_id"]},
                    "payload": {"decision": "Ledger is authoritative"},
                },
            ).json()
            self.assertEqual(commit["status"], "committed_pending_projection")
            context = {
                "project_id": identity["project_id"],
                "workspace_id": identity["workspace_id"],
                "task_id": task["task_id"],
                "touched_paths": [],
                "stage": "verify",
            }
            forged_owner = client.post(
                "/memories/current",
                json={
                    "memory_ids": [commit["memory_id"]],
                    "context": {**context, "owner_id": "request-controlled-owner"},
                },
            )
            self.assertEqual(forged_owner.status_code, 422)
            current = client.post(
                "/memories/current",
                json={"memory_ids": [commit["memory_id"]], "context": context},
            ).json()
            history = client.post(
                f'/memories/{commit["memory_id"]}/history', json=context
            ).json()
            self.assertEqual(current[0]["payload"], {"decision": "Ledger is authoritative"})
            self.assertEqual(len(history), 1)

            forgotten = client.post(
                f'/memories/{commit["memory_id"]}/forget', json={"receipt": "forget-1"}
            ).json()
            self.assertEqual(forgotten["status"], "remote_pending")
            deletion = client.get(f'/deletions/{forgotten["job_id"]}').json()
            self.assertEqual(deletion["pending_outbox"], 1)
            self.assertEqual(deletion["status"], "remote_pending")
            self.assertEqual(len(client.get("/outbox").json()), 2)
            self.assertEqual(
                client.post(
                    "/memories/current",
                    json={"memory_ids": [commit["memory_id"]], "context": context},
                ).json(),
                [],
            )
            self.assertEqual(
                client.post(f'/memories/{commit["memory_id"]}/history', json=context).json(), []
            )


if __name__ == "__main__":
    unittest.main()
