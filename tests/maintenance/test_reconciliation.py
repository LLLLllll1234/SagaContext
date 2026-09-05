from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sagacontext.ledger import CommitRequest, EvidenceInput, Ledger, Scope, TaskContext
from sagacontext.maintenance import (
    BatchService,
    BatchWorker,
    CandidateInput,
    CursorUpdate,
    DeltaProposal,
    EventJournal,
    JournalEvent,
    ReviewService,
    ScriptedJudge,
)


NOW = datetime(2026, 9, 5, tzinfo=timezone.utc)


class ReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ledger = Ledger(self.root / "ledger-v3.db", owner_id="owner")
        identity = self.ledger.register_project("project", self.root)
        self.project_id = identity["project_id"]
        self.workspace_id = identity["workspace_id"]
        self.task_id = self.ledger.create_task(self.project_id, "S2")
        self.session_id = self.ledger.open_session("synthetic", "session", self.workspace_id)
        self.ledger.bind_task(self.session_id, self.task_id, "bind")
        self.journal = EventJournal(self.ledger)
        self.batches = BatchService(self.ledger)
        self.worker = BatchWorker(self.ledger)
        self.event = self.journal.append(
            JournalEvent(
                host="synthetic",
                host_version="1",
                session_id=self.session_id,
                workspace_id=self.workspace_id,
                event_kind="checkpoint_requested",
                occurred_at=NOW,
                trust_class="synthetic",
                source_generation="gen-1",
                source_event_key="event-1",
                source_locator={"fixture": "synthetic"},
                payload={"text": "checkpoint"},
                parser_version="test-1",
            ),
            CursorUpdate(
                host="synthetic",
                session_id=self.session_id,
                source_locator="fixture",
                source_generation="gen-1",
                byte_offset=10,
                source_fingerprint="fixture-1",
            ),
        )

    def tearDown(self) -> None:
        self.ledger.close()
        self.temp.cleanup()

    def _candidate(self, topic: str = "checkpoint") -> str:
        return self.batches.create_candidate(
            CandidateInput(
                session_id=self.session_id,
                task_id=self.task_id,
                kind="checkpoint",
                memory_type_hint="task_checkpoint",
                scope_hint=Scope(
                    kind="task", project_id=self.project_id, task_id=self.task_id
                ),
                topic_key=topic,
                event_ids=(self.event.event_id,),
            )
        ).candidate_id

    def _proposal(
        self,
        candidate_id: str,
        operation: str = "new",
        *,
        target_id: str | None = None,
        expected_revision: int | None = None,
        memory_type: str = "task_checkpoint",
        payload: dict | None = None,
    ) -> DeltaProposal:
        return DeltaProposal(
            candidate_id=candidate_id,
            operation=operation,
            target_id=target_id,
            expected_revision=expected_revision,
            memory_type=memory_type,
            scope=Scope(kind="task", project_id=self.project_id, task_id=self.task_id),
            payload=payload
            or {
                "goal": "S2",
                "done": [],
                "open": ["projector"],
                "next": "implement projector",
                "touched_paths": ["src/sagacontext"],
                "outcome": "in_progress",
            },
            evidence_ids=(self.event.event_id,),
            rationale="synthetic",
        )

    def _run(self, judge: ScriptedJudge, **kwargs):
        return self.worker.run_once(
            judge,
            worker_id="worker",
            now=kwargs.pop("now", NOW),
            lease_duration=timedelta(seconds=30),
            **kwargs,
        )

    def _seed_memory(self) -> str:
        result = self.ledger.commit(
            CommitRequest(
                receipt="seed",
                operation="new",
                memory_type="task_checkpoint",
                scope=Scope(kind="task", project_id=self.project_id, task_id=self.task_id),
                payload={"goal": "S2", "next": "old"},
                evidence=[
                    EvidenceInput(
                        evidence_id="seed-evidence",
                        source_event_id="seed-event",
                        claim_key="seed",
                        evidence_kind="synthetic",
                        locator={"fixture": "seed"},
                        observed_at=NOW,
                    )
                ],
            )
        )
        return result.memory_id

    def test_b4_judge_failure_is_retry_not_no_change(self):
        self._candidate()
        batch = self.batches.request_batch(self.session_id, self.task_id)
        result = self._run(ScriptedJudge(error=RuntimeError("model unavailable")))

        self.assertEqual(result.status, "retry")
        row = self.ledger.db.execute(
            "SELECT status,last_error_class FROM batches WHERE batch_id=?", (batch.batch_id,)
        ).fetchone()
        self.assertEqual(tuple(row), ("retry", "judge_error"))
        self.assertEqual(self.ledger.db.execute("SELECT COUNT(*) FROM proposals").fetchone()[0], 0)

    def test_b4_repeated_judge_failure_stops_at_bounded_blocked_state(self):
        self._candidate()
        batch = self.batches.request_batch(self.session_id, self.task_id)
        judge = ScriptedJudge(error=RuntimeError("model unavailable"))

        statuses = [
            self._run(judge, now=NOW + timedelta(seconds=index)).status
            for index in range(3)
        ]

        self.assertEqual(statuses, ["retry", "retry", "blocked"])
        self.assertEqual(judge.calls, 3)
        self.assertEqual(
            self.ledger.db.execute(
                "SELECT status FROM batches WHERE batch_id=?", (batch.batch_id,)
            ).fetchone()[0],
            "blocked",
        )

    def test_b5_no_change_is_persisted_and_settled_once(self):
        candidate_id = self._candidate()
        batch = self.batches.request_batch(self.session_id, self.task_id)
        judge = ScriptedJudge(proposals=(self._proposal(candidate_id, "no_change"),))

        result = self._run(judge)
        again = self._run(judge, now=NOW + timedelta(minutes=1))

        self.assertEqual((result.status, again.status), ("settled", "idle"))
        self.assertEqual(judge.calls, 1)
        proposal = self.ledger.db.execute(
            "SELECT status FROM proposals WHERE batch_id=?", (batch.batch_id,)
        ).fetchone()
        self.assertEqual(proposal["status"], "no_change")
        self.assertEqual(self.batches.candidate_status(candidate_id), "settled")

    def test_r1_persisted_proposal_resumes_without_calling_judge_again(self):
        candidate_id = self._candidate()
        batch = self.batches.request_batch(self.session_id, self.task_id)
        first_judge = ScriptedJudge(proposals=(self._proposal(candidate_id),))
        stopped = self._run(first_judge, stop_after_proposals=True)
        self.assertEqual(stopped.status, "proposed")
        self.ledger.db.execute(
            "UPDATE batches SET lease_until=? WHERE batch_id=?",
            ((NOW - timedelta(seconds=1)).isoformat(), batch.batch_id),
        )

        recovery_judge = ScriptedJudge(error=AssertionError("judge must not be called"))
        recovered = self._run(recovery_judge, now=NOW + timedelta(seconds=1))

        self.assertEqual(recovered.status, "settled")
        self.assertEqual(recovery_judge.calls, 0)
        self.assertEqual(self.ledger.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0], 1)

    def test_r1_judge_version_drift_blocks_fixed_batch_before_judge_call(self):
        self._candidate()
        batch = self.batches.request_batch(self.session_id, self.task_id)
        incompatible = ScriptedJudge(version="scripted-v2")

        result = self._run(incompatible)

        self.assertEqual(result.status, "blocked")
        self.assertEqual(incompatible.calls, 0)
        self.assertEqual(
            tuple(
                self.ledger.db.execute(
                    "SELECT status,last_error_class FROM batches WHERE batch_id=?",
                    (batch.batch_id,),
                ).fetchone()
            ),
            ("blocked", "judge_version_changed"),
        )

    def test_r2_stale_anchor_invalidates_proposal_before_rejudging(self):
        memory_id = self._seed_memory()
        candidate_id = self._candidate()
        first_batch = self.batches.request_batch(
            self.session_id, self.task_id, anchor_ids=(memory_id,)
        )
        first = self._proposal(
            candidate_id,
            "refine",
            target_id=memory_id,
            expected_revision=1,
            payload={"goal": "S2", "next": "stale"},
        )
        self._run(ScriptedJudge(proposals=(first,)), stop_after_proposals=True)
        self.ledger.commit(
            CommitRequest(
                receipt="concurrent",
                operation="refine",
                memory_id=memory_id,
                expected_revision=1,
                memory_type="task_checkpoint",
                scope=first.scope,
                payload={"goal": "S2", "next": "current"},
            )
        )
        self.ledger.db.execute(
            "UPDATE batches SET lease_until=? WHERE batch_id=?",
            ((NOW - timedelta(seconds=1)).isoformat(), first_batch.batch_id),
        )

        invalidated = self._run(ScriptedJudge(), now=NOW + timedelta(seconds=1))
        self.assertEqual(invalidated.status, "invalidated")
        self.assertEqual(
            self.ledger.db.execute(
                "SELECT status FROM proposals WHERE batch_id=?", (first_batch.batch_id,)
            ).fetchone()[0],
            "invalidated",
        )
        second_batch = self.batches.request_batch(
            self.session_id, self.task_id, anchor_ids=(memory_id,)
        )
        fresh = self._proposal(
            candidate_id,
            "refine",
            target_id=memory_id,
            expected_revision=2,
            payload={"goal": "S2", "next": "fresh"},
        )
        result = self._run(ScriptedJudge(proposals=(fresh,)), now=NOW + timedelta(seconds=2))
        self.assertEqual((second_batch.batch_id, result.status), (result.batch_id, "settled"))
        self.assertEqual(
            self.ledger.read_history(
                memory_id,
                TaskContext(
                    owner_id="owner",
                    project_id=self.project_id,
                    workspace_id=self.workspace_id,
                    task_id=self.task_id,
                ),
            )[-1].payload["next"],
            "fresh",
        )

    def test_r3_unknown_non_new_target_is_rejected_not_created(self):
        candidate_id = self._candidate()
        self.batches.request_batch(self.session_id, self.task_id)
        proposal = self._proposal(
            candidate_id,
            "refine",
            target_id="unknown-memory",
            expected_revision=1,
        )
        result = self._run(ScriptedJudge(proposals=(proposal,)))

        self.assertEqual(result.status, "rejected")
        self.assertEqual(self.ledger.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0], 0)
        self.assertEqual(self.batches.candidate_status(candidate_id), "quarantined")
        self.assertEqual(self.ledger.db.execute("SELECT status FROM proposals").fetchone()[0], "rejected")

    def test_r4_conflict_releases_batch_lease_but_freezes_candidate_until_review(self):
        memory_id = self._seed_memory()
        candidate_id = self._candidate()
        batch = self.batches.request_batch(
            self.session_id, self.task_id, anchor_ids=(memory_id,)
        )
        conflict = self._proposal(
            candidate_id,
            "conflict",
            target_id=memory_id,
            expected_revision=1,
            payload={"goal": "S2", "next": "needs review"},
        )
        result = self._run(ScriptedJudge(proposals=(conflict,)))

        row = self.ledger.db.execute(
            "SELECT status,lease_owner,lease_token FROM batches WHERE batch_id=?",
            (batch.batch_id,),
        ).fetchone()
        self.assertEqual(result.status, "awaiting_review")
        self.assertEqual(tuple(row), ("awaiting_review", None, None))
        self.assertEqual(self.batches.candidate_status(candidate_id), "awaiting_review")
        empty = self.batches.request_batch(self.session_id, self.task_id)
        self.assertEqual(self.batches.batch_input(empty.batch_id).candidate_ids, ())

        conflict_id = self.ledger.db.execute(
            "SELECT conflict_id FROM conflicts WHERE batch_id=?", (batch.batch_id,)
        ).fetchone()[0]
        reviewed = ReviewService(self.ledger).resolve(
            conflict_id, "accept_old", "review-receipt", reviewer="user"
        )
        replay = ReviewService(self.ledger).resolve(
            conflict_id, "accept_old", "review-receipt", reviewer="user"
        )
        self.assertEqual(reviewed, replay)
        self.assertEqual(self.batches.candidate_status(candidate_id), "settled")
        statuses = self.ledger.db.execute(
            "SELECT status FROM proposals WHERE batch_id=? ORDER BY created_at", (batch.batch_id,)
        ).fetchall()
        self.assertEqual([row[0] for row in statuses], ["superseded", "no_change"])

    def test_r4_accept_new_rechecks_head_and_commits_with_idempotent_receipt(self):
        memory_id = self._seed_memory()
        candidate_id = self._candidate()
        batch = self.batches.request_batch(
            self.session_id, self.task_id, anchor_ids=(memory_id,)
        )
        conflict = self._proposal(
            candidate_id,
            "conflict",
            target_id=memory_id,
            expected_revision=1,
            payload={"goal": "S2", "next": "approved"},
        )
        self._run(ScriptedJudge(proposals=(conflict,)))
        conflict_id = self.ledger.db.execute(
            "SELECT conflict_id FROM conflicts WHERE batch_id=?", (batch.batch_id,)
        ).fetchone()[0]

        service = ReviewService(self.ledger)
        result = service.resolve(
            conflict_id, "accept_new", "accept-new-receipt", reviewer="user"
        )
        replay = service.resolve(
            conflict_id, "accept_new", "accept-new-receipt", reviewer="user"
        )

        self.assertEqual(result, replay)
        self.assertEqual(result["status"], "settled")
        current = self.ledger.db.execute(
            "SELECT current_revision FROM memories WHERE memory_id=?", (memory_id,)
        ).fetchone()[0]
        payload = self.ledger.db.execute(
            "SELECT payload_json FROM revisions WHERE memory_id=? AND revision=?",
            (memory_id, current),
        ).fetchone()[0]
        self.assertEqual((current, json.loads(payload)["next"]), (2, "approved"))
        self.assertEqual(
            self.ledger.db.execute(
                "SELECT status,resolution FROM conflicts WHERE conflict_id=?", (conflict_id,)
            ).fetchone()[:],
            ("resolved", "accept_new"),
        )

    def test_a1_c1_commit_batch_rolls_back_memory_checkpoint_and_candidate(self):
        self.ledger.register_backend_generation("test", "g1")
        candidate_id = self._candidate()
        batch = self.batches.request_batch(self.session_id, self.task_id)
        before_task = self.ledger.db.execute(
            "SELECT last_active FROM tasks WHERE task_id=?", (self.task_id,)
        ).fetchone()[0]
        self.ledger.db.executescript(
            "CREATE TRIGGER reject_s2_outbox BEFORE INSERT ON outbox "
            "BEGIN SELECT RAISE(ABORT, 'outbox blocked'); END;"
        )
        result = self._run(ScriptedJudge(proposals=(self._proposal(candidate_id),)))

        self.assertEqual(result.status, "retry")
        self.assertEqual(self.ledger.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0], 0)
        self.assertEqual(self.ledger.db.execute("SELECT COUNT(*) FROM revisions").fetchone()[0], 0)
        self.assertEqual(self.ledger.db.execute("SELECT COUNT(*) FROM evidence").fetchone()[0], 0)
        self.assertEqual(self.ledger.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0], 0)
        self.assertEqual(self.batches.candidate_status(candidate_id), "processing")
        self.assertEqual(
            self.ledger.db.execute(
                "SELECT last_active FROM tasks WHERE task_id=?", (self.task_id,)
            ).fetchone()[0],
            before_task,
        )
        self.assertEqual(
            self.ledger.db.execute(
                "SELECT status FROM batches WHERE batch_id=?", (batch.batch_id,)
            ).fetchone()[0],
            "retry",
        )

    def test_a2_stale_expected_revision_never_partially_commits(self):
        memory_id = self._seed_memory()
        candidate_id = self._candidate()
        batch = self.batches.request_batch(
            self.session_id, self.task_id, anchor_ids=(memory_id,)
        )
        stale = self._proposal(
            candidate_id,
            "refine",
            target_id=memory_id,
            expected_revision=1,
            payload={"goal": "S2", "next": "stale"},
        )
        self._run(ScriptedJudge(proposals=(stale,)), stop_after_proposals=True)
        self.ledger.commit(
            CommitRequest(
                receipt="winner",
                operation="refine",
                memory_id=memory_id,
                expected_revision=1,
                memory_type="task_checkpoint",
                scope=stale.scope,
                payload={"goal": "S2", "next": "winner"},
            )
        )
        self.ledger.db.execute(
            "UPDATE batches SET lease_until=? WHERE batch_id=?",
            ((NOW - timedelta(seconds=1)).isoformat(), batch.batch_id),
        )

        result = self._run(ScriptedJudge(), now=NOW + timedelta(seconds=1))
        self.assertEqual(result.status, "invalidated")
        history = self.ledger.db.execute(
            "SELECT payload_json FROM revisions WHERE memory_id=? ORDER BY revision", (memory_id,)
        ).fetchall()
        self.assertEqual([json.loads(row[0])["next"] for row in history], ["old", "winner"])

    def test_a2_batch_confirm_cannot_change_payload_or_omit_evidence(self):
        memory_id = self._seed_memory()
        candidate_id = self._candidate()
        self.batches.request_batch(
            self.session_id, self.task_id, anchor_ids=(memory_id,)
        )
        invalid_confirm = self._proposal(
            candidate_id,
            "confirm",
            target_id=memory_id,
            expected_revision=1,
            payload={"goal": "S2", "next": "changed by confirm"},
        ).model_copy(update={"evidence_ids": ()})

        result = self._run(ScriptedJudge(proposals=(invalid_confirm,)))

        self.assertEqual(result.status, "retry")
        self.assertEqual(
            self.ledger.db.execute(
                "SELECT COUNT(*) FROM revisions WHERE memory_id=?", (memory_id,)
            ).fetchone()[0],
            1,
        )


if __name__ == "__main__":
    unittest.main()
