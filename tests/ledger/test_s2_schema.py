from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from sagacontext.ledger import (
    BatchMemoryOperation,
    CommitBatchPlan,
    Ledger,
    Scope,
    TaskContext,
)
from sagacontext.ledger.schema import MIGRATION_1, SCHEMA_VERSION


class S2SchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ledger_path = self.root / "ledger-v3.db"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _create_v1_database(self) -> None:
        db = sqlite3.connect(self.ledger_path)
        try:
            db.execute(
                "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            db.executescript(MIGRATION_1)
            db.execute(
                "INSERT INTO schema_migrations(version,applied_at) VALUES (1,'2026-09-05T00:00:00Z')"
            )
            db.execute("INSERT INTO owners VALUES ('owner-v1','2026-09-05T00:00:00Z')")
            db.execute(
                "INSERT INTO memories VALUES "
                "('memory-v1','owner-v1',1,'decision','{\"kind\":\"global\","
                "\"path_pattern\":null,\"project_id\":null,\"task_id\":null}',"
                "'active','none',1)"
            )
            db.execute(
                "INSERT INTO revisions VALUES "
                "('memory-v1',1,'new',1,'{\"decision\":\"preserve S1\"}',"
                "'2026-09-05T00:00:00Z','system')"
            )
            db.commit()
        finally:
            db.close()

    def test_schema_v2_migrates_v1_and_preserves_s1_data(self):
        self._create_v1_database()

        ledger = Ledger(self.ledger_path)
        try:
            self.assertEqual(SCHEMA_VERSION, 2)
            self.assertEqual(ledger.owner_id, "owner-v1")
            versions = {
                row[0] for row in ledger.db.execute("SELECT version FROM schema_migrations")
            }
            self.assertEqual(versions, {1, 2})
            required = {
                "events",
                "source_cursors",
                "event_aliases",
                "event_quarantine",
                "candidates",
                "batches",
                "batch_events",
                "batch_candidates",
                "batch_anchors",
                "proposals",
                "conflicts",
                "review_receipts",
                "projection_attempts",
                "projection_receipts",
            }
            tables = {
                row[0]
                for row in ledger.db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            self.assertTrue(required <= tables)
            current = ledger.get_current(
                ["memory-v1"], TaskContext(owner_id="owner-v1")
            )
            self.assertEqual(current[0].payload, {"decision": "preserve S1"})
        finally:
            ledger.close()

    def test_schema_v2_failure_rolls_back_all_v2_changes(self):
        self._create_v1_database()
        broken = "CREATE TABLE half_v2(id TEXT);"

        with patch("sagacontext.ledger.service.MIGRATION_2", broken):
            with self.assertRaises(RuntimeError):
                Ledger(self.ledger_path)

        db = sqlite3.connect(self.ledger_path)
        try:
            self.assertIsNone(
                db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='half_v2'"
                ).fetchone()
            )
            self.assertEqual(
                db.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall(),
                [(1,)],
            )
            self.assertEqual(db.execute("SELECT owner_id FROM owners").fetchone()[0], "owner-v1")
            self.assertEqual(
                db.execute(
                    "SELECT payload_json FROM revisions WHERE memory_id='memory-v1'"
                ).fetchone()[0],
                '{"decision":"preserve S1"}',
            )
        finally:
            db.close()

    def test_schema_v2_marker_without_complete_schema_is_rejected(self):
        self._create_v1_database()
        db = sqlite3.connect(self.ledger_path)
        try:
            db.execute(
                "INSERT INTO schema_migrations(version,applied_at) "
                "VALUES (2,'2026-09-05T00:00:00Z')"
            )
            db.commit()
        finally:
            db.close()

        with self.assertRaisesRegex(RuntimeError, "incomplete schema v2"):
            Ledger(self.ledger_path)

    def test_schema_v2_never_reads_or_changes_legacy_state_database(self):
        legacy = self.root / "state.db"
        legacy.write_bytes(b"legacy-not-sql")
        before = (hashlib.sha256(legacy.read_bytes()).hexdigest(), legacy.stat().st_mtime_ns)

        ledger = Ledger(self.ledger_path, owner_id="owner-v2")
        ledger.close()

        after = (hashlib.sha256(legacy.read_bytes()).hexdigest(), legacy.stat().st_mtime_ns)
        self.assertEqual(after, before)

    def test_commit_batch_plan_is_frozen_and_forbids_executable_fields(self):
        operation = BatchMemoryOperation(
            proposal_id="proposal-1",
            operation="new",
            memory_type="decision",
            scope=Scope(kind="global"),
            payload_json='{"decision":"ledger stays authoritative"}',
        )
        plan = CommitBatchPlan(
            batch_id="batch-1",
            proposal_ids=("proposal-1",),
            memory_operations=(operation,),
        )

        with self.assertRaises(ValidationError):
            plan.memory_operations = ()
        with self.assertRaises(ValidationError):
            CommitBatchPlan(
                batch_id="batch-1",
                proposal_ids=("proposal-1",),
                memory_operations=(operation,),
                sql="DROP TABLE memories",
            )
        with self.assertRaises(ValidationError):
            BatchMemoryOperation(
                proposal_id="proposal-2",
                operation="new",
                memory_type="decision",
                scope=Scope(kind="global"),
                payload_json="not-json",
            )


if __name__ == "__main__":
    unittest.main()
