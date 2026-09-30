import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sagacontext.ledger import Ledger
from sagacontext.ledger.schema import MIGRATION_1, MIGRATION_2, MIGRATION_3, MIGRATION_4, MIGRATION_5, MIGRATION_6


class RolloutMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "ledger.db"
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,applied_at TEXT NOT NULL)")
            for i, migration in enumerate((MIGRATION_1, MIGRATION_2, MIGRATION_3), 1):
                db.executescript(migration)
                db.execute("INSERT INTO schema_migrations VALUES (?, '2026-09-09')", (i,))
            db.execute("INSERT INTO owners VALUES ('owner','2026-09-09')")
            db.execute("INSERT INTO rollout_approval_grants VALUES ('owner','grant','digest','k1','2026-09-09','2026-09-10',NULL)")
        self.addCleanup(self.temp.cleanup)

    def test_v3_to_v4_preserves_grants_and_is_repeatable(self):
        for _ in range(2):
            ledger = Ledger(self.path)
            try:
                self.assertEqual(ledger.db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0], 7)
                self.assertEqual(tuple(ledger.db.execute("SELECT receipt,request_digest FROM rollout_approval_grants").fetchone()), ("grant", "digest"))
                self.assertEqual(ledger.db.execute("SELECT COUNT(*) FROM rollout_control_keys").fetchone()[0], 0)
            finally:
                ledger.close()

    def test_failed_v4_migration_does_not_leave_partial_tables_or_marker(self):
        with patch("sagacontext.ledger.service.MIGRATION_4", "CREATE TABLE partial_v4(id INTEGER);\n"):
            with self.assertRaisesRegex(RuntimeError, "incomplete schema v4"):
                Ledger(self.path)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0], 3)
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='partial_v4'").fetchone())
            self.assertEqual(db.execute("SELECT COUNT(*) FROM rollout_approval_grants").fetchone()[0], 1)

    def test_existing_v4_migrates_to_v5_and_preserves_old_tables(self):
        with sqlite3.connect(self.path) as db:
            db.executescript(MIGRATION_4)
            db.execute("INSERT INTO schema_migrations VALUES (4,'2026-09-09')")
        ledger = Ledger(self.path)
        try:
            self.assertEqual(ledger.db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0], 7)
            self.assertEqual(ledger.db.execute("SELECT COUNT(*) FROM rollout_approval_grants").fetchone()[0], 1)
            self.assertEqual(ledger.db.execute("SELECT COUNT(*) FROM projection_operations").fetchone()[0], 0)
        finally:
            ledger.close()

    def test_failed_v5_does_not_leave_partial_migration(self):
        with sqlite3.connect(self.path) as db:
            db.executescript(MIGRATION_4)
            db.execute("INSERT INTO schema_migrations VALUES (4,'2026-09-09')")
        with patch("sagacontext.ledger.service.MIGRATION_5", "CREATE TABLE partial_v5(id INTEGER);\n"):
            with self.assertRaisesRegex(RuntimeError, "incomplete schema v5"):
                Ledger(self.path)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0], 4)
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='partial_v5'").fetchone())

    def test_existing_v5_run_migrates_through_v7(self):
        with sqlite3.connect(self.path) as db:
            db.executescript(MIGRATION_4 + MIGRATION_5)
            db.executemany("INSERT INTO schema_migrations VALUES (?,'2026-09-09')", ((4,), (5,)))
            db.execute("INSERT INTO rollout_runs(rollout_id,owner_id,workspace_id,workspace_root,mode,status,approver,key_id,approval_receipt,approval_digest,started_at,deadline,max_sessions,max_candidates,generation,rollback_plan_digest,created_at) VALUES ('old','owner','w','/w','guarded','stopped','ops','k1','r','d','2026-09-11','2026-09-12',10,20,'g1','p','2026-09-11')")
            db.execute("INSERT INTO sessions VALUES ('sess','owner','codex','host-1','w','2026-09-11',NULL)")
            db.execute("INSERT INTO rollout_sessions VALUES ('old','sess','host-1','closed','2026-09-11','2026-09-12')")
        ledger = Ledger(self.path)
        try:
            self.assertEqual(ledger.db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0], 7)
            self.assertIsNone(ledger.db.execute("SELECT hosts_json FROM rollout_runs WHERE rollout_id='old'").fetchone()[0])
            self.assertEqual(tuple(ledger.db.execute("SELECT * FROM rollout_sessions").fetchone()),
                             ("old", "sess", "host-1", "closed", "2026-09-11", "2026-09-12"))
            # Two hosts may reuse one host session id inside a rollout.
            ledger.db.execute("INSERT INTO sessions VALUES ('sess2','owner','claude-code','host-1','w','2026-09-11',NULL)")
            ledger.db.execute("INSERT INTO rollout_sessions VALUES ('old','sess2','host-1','reserved','2026-09-11',NULL)")
        finally:
            ledger.close()

    def test_failed_v6_does_not_leave_partial_migration(self):
        with sqlite3.connect(self.path) as db:
            db.executescript(MIGRATION_4 + MIGRATION_5)
            db.executemany("INSERT INTO schema_migrations VALUES (?,'2026-09-09')", ((4,), (5,)))
        with patch("sagacontext.ledger.service.MIGRATION_6", "CREATE TABLE partial_v6(id INTEGER);\n"):
            with self.assertRaisesRegex(RuntimeError, "incomplete schema v6"):
                Ledger(self.path)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0], 5)
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='partial_v6'").fetchone())

    def test_applied_v6_upgrades_to_v7_and_keeps_reservations(self):
        # Matches the real ledger: v6 applied as the ALTER alone, before v7 existed.
        with sqlite3.connect(self.path) as db:
            db.executescript(MIGRATION_4 + MIGRATION_5 + MIGRATION_6)
            db.executemany("INSERT INTO schema_migrations VALUES (?,'2026-09-30')", ((4,), (5,), (6,)))
            db.execute("INSERT INTO rollout_runs(rollout_id,owner_id,workspace_id,workspace_root,mode,status,approver,key_id,approval_receipt,approval_digest,started_at,deadline,max_sessions,max_candidates,generation,rollback_plan_digest,created_at) VALUES ('old','owner','w','/w','guarded','stopped','ops','k1','r','d','2026-09-11','2026-09-12',10,20,'g1','p','2026-09-11')")
            db.execute("INSERT INTO sessions VALUES ('sess','owner','codex','host-1','w','2026-09-11',NULL)")
            db.execute("INSERT INTO rollout_sessions VALUES ('old','sess','host-1','closed','2026-09-11','2026-09-12')")
        ledger = Ledger(self.path)
        try:
            self.assertEqual(ledger.db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0], 7)
            self.assertEqual(ledger.db.execute("SELECT COUNT(*) FROM rollout_sessions").fetchone()[0], 1)
            self.assertEqual(ledger.db.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            ledger.close()

    def test_v7_validation_rejects_leftover_unique_constraint(self):
        with sqlite3.connect(self.path) as db:
            db.executescript(MIGRATION_4 + MIGRATION_5 + MIGRATION_6)
            db.executemany("INSERT INTO schema_migrations VALUES (?,'2026-09-30')", ((4,), (5,), (6,)))
        with patch("sagacontext.ledger.service.MIGRATION_7", "CREATE TABLE noop_v7(id INTEGER);\n"):
            with self.assertRaisesRegex(RuntimeError, "incomplete schema v7"):
                Ledger(self.path)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0], 6)
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='noop_v7'").fetchone())
