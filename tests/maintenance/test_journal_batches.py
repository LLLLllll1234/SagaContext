from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sagacontext.ledger import Ledger, Scope
from sagacontext.maintenance import (
    BatchService,
    CandidateInput,
    CursorUpdate,
    EventJournal,
    JournalEvent,
)


class JournalAndBatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ledger = Ledger(self.root / "ledger-v3.db", owner_id="owner-a")
        identity = self.ledger.register_project("project", self.root)
        self.project_id = identity["project_id"]
        self.workspace_id = identity["workspace_id"]
        self.session_id = self.ledger.open_session("synthetic", "session-a", self.workspace_id)
        self.task_id = self.ledger.create_task(self.project_id, "implement S2")
        self.ledger.bind_task(self.session_id, self.task_id, "binding-event")
        self.journal = EventJournal(self.ledger)
        self.batches = BatchService(self.ledger)

    def tearDown(self) -> None:
        self.ledger.close()
        self.temp.cleanup()

    def _event(
        self,
        key: str,
        *,
        generation: str = "gen-1",
        kind: str = "user_message",
    ) -> JournalEvent:
        return JournalEvent(
            host="synthetic",
            host_version="1",
            session_id=self.session_id,
            workspace_id=self.workspace_id,
            event_kind=kind,
            occurred_at=datetime(2026, 9, 5, tzinfo=timezone.utc),
            trust_class="synthetic",
            source_generation=generation,
            source_event_key=key,
            source_locator={"fixture": "session.jsonl"},
            payload={"text": key},
            parser_version="test-1",
        )

    def _cursor(self, offset: int, generation: str = "gen-1") -> CursorUpdate:
        return CursorUpdate(
            host="synthetic",
            session_id=self.session_id,
            source_locator="fixture/session.jsonl",
            source_generation=generation,
            byte_offset=offset,
            source_fingerprint=f"fingerprint-{generation}",
        )

    def _candidate(self, event_id: str, topic: str) -> CandidateInput:
        return CandidateInput(
            session_id=self.session_id,
            task_id=self.task_id,
            kind="explicit_instruction",
            memory_type_hint="decision",
            scope_hint=Scope(kind="project", project_id=self.project_id),
            topic_key=topic,
            event_ids=(event_id,),
        )

    def test_j1_event_and_cursor_commit_or_roll_back_together(self):
        self.ledger.db.executescript(
            "CREATE TRIGGER reject_cursor BEFORE INSERT ON source_cursors "
            "BEGIN SELECT RAISE(ABORT, 'cursor blocked'); END;"
        )
        with self.assertRaises(sqlite3.DatabaseError):
            self.journal.append(self._event("event-1"), self._cursor(20))
        self.assertEqual(self.ledger.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)
        self.assertEqual(
            self.ledger.db.execute("SELECT COUNT(*) FROM source_cursors").fetchone()[0], 0
        )

        self.ledger.db.execute("DROP TRIGGER reject_cursor")
        receipt = self.journal.append(self._event("event-1"), self._cursor(20))
        self.assertEqual(receipt.status, "accepted")
        self.assertEqual(self.journal.cursor(self._cursor(20)), 20)

    def test_j2_partial_does_not_advance_and_bad_line_replay_is_idempotent(self):
        partial = self.journal.record_invalid_line(
            cursor=self._cursor(9),
            byte_start=0,
            byte_end=9,
            payload=b'{"broken"',
            error_class="partial_json",
            complete=False,
        )
        self.assertEqual(partial.status, "partial")
        self.assertIsNone(self.journal.cursor(self._cursor(9)))

        first = self.journal.record_invalid_line(
            cursor=self._cursor(10),
            byte_start=0,
            byte_end=10,
            payload=b'{"broken"}',
            error_class="invalid_json",
            complete=True,
        )
        second = self.journal.record_invalid_line(
            cursor=self._cursor(10),
            byte_start=0,
            byte_end=10,
            payload=b'{"broken"}',
            error_class="invalid_json",
            complete=True,
        )
        self.assertEqual(first.quarantine_id, second.quarantine_id)
        self.assertEqual(
            self.ledger.db.execute("SELECT COUNT(*) FROM event_quarantine").fetchone()[0], 1
        )
        self.assertEqual(self.journal.cursor(self._cursor(10)), 10)

    def test_j3_alias_is_single_target_and_cannot_cross_generation_or_owner(self):
        first = self.journal.append(self._event("canonical-1"), self._cursor(20))
        other_generation = self.journal.append(
            self._event("canonical-2", generation="gen-2"), self._cursor(20, "gen-2")
        )
        alias_id = self.journal.add_alias(
            host="synthetic",
            session_id=self.session_id,
            source_generation="gen-1",
            alias_event_key="hook-1",
            canonical_event_id=first.event_id,
            alias_kind="hook_transcript",
        )
        self.assertEqual(
            self.journal.add_alias(
                host="synthetic",
                session_id=self.session_id,
                source_generation="gen-1",
                alias_event_key="hook-1",
                canonical_event_id=first.event_id,
                alias_kind="hook_transcript",
            ),
            alias_id,
        )
        with self.assertRaises(ValueError):
            self.journal.add_alias(
                host="synthetic",
                session_id=self.session_id,
                source_generation="gen-1",
                alias_event_key="hook-1",
                canonical_event_id=other_generation.event_id,
                alias_kind="hook_transcript",
            )

        other_root = self.root / "other"
        other_root.mkdir()
        other = Ledger(self.ledger.path, owner_id="owner-b")
        try:
            identity = other.register_project("other", other_root)
            session = other.open_session("synthetic", "session-b", identity["workspace_id"])
            foreign = EventJournal(other).append(
                JournalEvent(
                    host="synthetic",
                    host_version="1",
                    session_id=session,
                    workspace_id=identity["workspace_id"],
                    event_kind="user_message",
                    occurred_at=datetime(2026, 9, 5, tzinfo=timezone.utc),
                    trust_class="synthetic",
                    source_generation="gen-1",
                    source_event_key="foreign",
                    source_locator={},
                    payload={},
                    parser_version="test-1",
                )
            )
        finally:
            other.close()
        with self.assertRaises(ValueError):
            self.journal.add_alias(
                host="synthetic",
                session_id=self.session_id,
                source_generation="gen-1",
                alias_event_key="foreign-alias",
                canonical_event_id=foreign.event_id,
                alias_kind="hook_transcript",
            )

    def test_j4_alias_resolves_to_one_canonical_evidence_event(self):
        canonical = self.journal.append(self._event("transcript-1"), self._cursor(20))
        self.journal.add_alias(
            host="synthetic",
            session_id=self.session_id,
            source_generation="gen-1",
            alias_event_key="hook-1",
            canonical_event_id=canonical.event_id,
            alias_kind="hook_transcript",
        )
        resolved = {
            self.journal.resolve_event_key(
                "synthetic", self.session_id, "gen-1", key
            )
            for key in ("transcript-1", "hook-1")
        }
        self.assertEqual(resolved, {canonical.event_id})

    def test_b1_b2_batch_freezes_inputs_and_leaves_new_candidates_pending(self):
        event_1 = self.journal.append(self._event("event-1"), self._cursor(20))
        candidate_1 = self.batches.create_candidate(self._candidate(event_1.event_id, "first"))
        batch = self.batches.request_batch(self.session_id, self.task_id)

        event_2 = self.journal.append(self._event("event-2"), self._cursor(40))
        candidate_2 = self.batches.create_candidate(self._candidate(event_2.event_id, "second"))
        frozen = self.batches.batch_input(batch.batch_id)

        self.assertEqual(frozen.event_ids, (event_1.event_id,))
        self.assertEqual(frozen.candidate_ids, (candidate_1.candidate_id,))
        self.assertEqual(frozen.anchor_revisions, ())
        self.assertEqual(frozen.policy_version, "s2-policy-v1")
        self.assertEqual(frozen.maintenance_schema_version, 2)
        self.assertEqual(frozen.judge_version, "scripted-v1")
        self.assertEqual(frozen.judge_candidates[0].candidate_id, candidate_1.candidate_id)
        self.assertEqual(frozen.judge_candidates[0].text, "event-1")
        self.assertEqual(frozen.summary, "event-1")
        self.assertEqual(self.batches.candidate_status(candidate_2.candidate_id), "pending")

        self.ledger.db.execute(
            "UPDATE batches SET policy_version='tampered' WHERE batch_id=?",
            (batch.batch_id,),
        )
        self.assertFalse(self.batches.input_is_current(batch.batch_id))

    def test_b3_batch_and_candidate_tokens_fence_expired_worker(self):
        event = self.journal.append(self._event("event-1"), self._cursor(20))
        candidate = self.batches.create_candidate(self._candidate(event.event_id, "first"))
        batch = self.batches.request_batch(self.session_id, self.task_id)
        start = datetime(2026, 9, 5, tzinfo=timezone.utc)
        first = self.batches.claim_next("worker-a", start, timedelta(seconds=30))
        self.assertFalse(
            self.batches.validate_claim(
                batch.batch_id,
                "worker-a",
                first.lease_token,
                now=start + timedelta(seconds=31),
            )
        )
        second = self.batches.claim_next(
            "worker-b", start + timedelta(seconds=31), timedelta(seconds=30)
        )

        self.assertEqual(first.batch_id, batch.batch_id)
        self.assertEqual(second.batch_id, batch.batch_id)
        self.assertNotEqual(first.lease_token, second.lease_token)
        self.assertFalse(
            self.batches.validate_claim(batch.batch_id, "worker-a", first.lease_token)
        )
        self.assertTrue(
            self.batches.validate_claim(
                batch.batch_id,
                "worker-b",
                second.lease_token,
                now=start + timedelta(seconds=31),
            )
        )
        self.assertTrue(
            self.batches.validate_candidate_claim(
                batch.batch_id, candidate.candidate_id, batch.candidate_claim_tokens[0]
            )
        )


if __name__ == "__main__":
    unittest.main()
