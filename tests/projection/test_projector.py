from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sagacontext.backends import (
    BackendUnknownError, BackendVerificationTimeout,
    InMemoryBackend, InMemoryBackendState, Projection,
)
from sagacontext.ledger import CommitRequest, Ledger, Scope
from sagacontext.projection import Projector


NOW = datetime(2026, 9, 5, tzinfo=timezone.utc)


class TransactionCheckingBackend(InMemoryBackend):
    def __init__(self, ledger: Ledger):
        super().__init__(InMemoryBackendState())
        self.ledger = ledger

    def capabilities(self):
        assert not self.ledger.db.in_transaction
        return super().capabilities()

    def materialize(self, projection, operation_key):
        assert not self.ledger.db.in_transaction
        return super().materialize(projection, operation_key)

    def inspect_projection(self, locator):
        assert not self.ledger.db.in_transaction
        return super().inspect_projection(locator)


class ProjectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ledger = Ledger(self.root / "ledger-v3.db", owner_id="owner")
        identity = self.ledger.register_project("project", self.root)
        self.project_id = identity["project_id"]
        self.scope = Scope(kind="project", project_id=self.project_id)
        self.ledger.register_backend_generation("memory-test-double", "g1")
        self.projector = Projector(self.ledger)

    def tearDown(self) -> None:
        self.ledger.close()
        self.temp.cleanup()

    def _commit(
        self,
        receipt: str,
        *,
        memory_id: str | None = None,
        expected_revision: int | None = None,
        value: str = "v1",
    ):
        return self.ledger.commit(
            CommitRequest(
                receipt=receipt,
                operation="new" if memory_id is None else "refine",
                memory_id=memory_id,
                expected_revision=expected_revision,
                memory_type="decision",
                scope=self.scope,
                payload={"decision": value},
            )
        )

    def _drain(self, backend: InMemoryBackend, now: datetime = NOW, **kwargs):
        return self.projector.drain_once(
            backend,
            worker_id="worker",
            now=now,
            backend_timeout=timedelta(seconds=5),
            local_completion_margin=timedelta(seconds=2),
            lease_duration=timedelta(seconds=10),
            verification_timeout=timedelta(seconds=3),
            **kwargs,
        )

    def test_p1_committed_outbox_survives_until_explicit_drain(self):
        created = self._commit("create")
        state = InMemoryBackendState()

        self.assertEqual(state.items, {})
        result = self._drain(InMemoryBackend(state))

        self.assertEqual(result.status, "confirmed")
        self.assertEqual(len(state.items), 1)
        self.assertEqual(state.materialize_calls, 1)
        self.assertEqual(
            self.ledger.db.execute(
                "SELECT status FROM outbox WHERE memory_id=?", (created.memory_id,)
            ).fetchone()[0],
            "confirmed",
        )

    def test_p2_unknown_write_is_located_after_client_restart_without_duplicate(self):
        self._commit("create")
        state = InMemoryBackendState()
        first = self._drain(
            InMemoryBackend(state, materialize_fault="after_write_timeout")
        )
        self.assertEqual(first.status, "unknown")
        self.assertEqual((len(state.items), state.materialize_calls), (1, 1))
        self.assertEqual(
            self.ledger.db.execute("SELECT COUNT(*) FROM projection_receipts").fetchone()[0], 0
        )

        recovered = self._drain(InMemoryBackend(state), now=NOW + timedelta(seconds=1))

        self.assertEqual(recovered.status, "confirmed")
        self.assertTrue(recovered.recovered)
        self.assertEqual((len(state.items), state.materialize_calls), (1, 1))
        self.assertEqual(
            self.ledger.db.execute("SELECT COUNT(*) FROM projection_receipts").fetchone()[0], 1
        )

    def test_locate_result_requires_matching_identity_generation_and_digest(self):
        created = self._commit("create")
        state = InMemoryBackendState()
        self._drain(InMemoryBackend(state, materialize_fault="after_write_timeout"))
        locator = next(iter(state.items))
        state.items[locator] = Projection(
            owner_id="owner",
            memory_id=created.memory_id,
            revision=1,
            generation="g1",
            memory_type="decision",
            searchable_text="tampered",
            scope_filter_tags=[f"project:{self.project_id}"],
            payload_digest="wrong",
        )

        result = self._drain(InMemoryBackend(state), now=NOW + timedelta(seconds=1))

        self.assertEqual(result.status, "blocked")
        self.assertEqual(
            self.ledger.db.execute("SELECT COUNT(*) FROM projection_receipts").fetchone()[0], 0
        )

    def test_p3_late_old_revision_becomes_obsolete_after_new_revision(self):
        first = self._commit("create")
        state = InMemoryBackendState()
        backend = InMemoryBackend(state)
        old_claim = self.projector.claim_next(
            backend,
            worker_id="worker-old",
            now=NOW,
            lease_duration=timedelta(minutes=5),
            backend_timeout=timedelta(seconds=5),
            local_completion_margin=timedelta(seconds=2),
        )
        old_locator = self.projector.call_backend(old_claim, backend, now=NOW)

        self._commit(
            "refine", memory_id=first.memory_id, expected_revision=1, value="v2"
        )
        current = self._drain(InMemoryBackend(state), now=NOW + timedelta(seconds=1))
        late = self.projector.complete(
            old_claim, backend, old_locator, now=NOW + timedelta(seconds=2)
        )
        cleanup = self._drain(InMemoryBackend(state), now=NOW + timedelta(seconds=2))

        self.assertEqual(
            (current.status, late.status, cleanup.status),
            ("confirmed", "obsolete", "confirmed"),
        )
        self.assertNotIn(old_locator, state.items)
        hits = backend.search("decision", "g1", limit=10)
        current_hits = self.projector.filter_current_hits(hits)
        self.assertEqual([(hit.memory_id, hit.revision) for hit in current_hits], [(first.memory_id, 2)])

    def test_p4_expired_lease_fences_old_worker_completion(self):
        self._commit("create")
        backend = InMemoryBackend(InMemoryBackendState())
        old = self.projector.claim_next(
            backend,
            worker_id="worker-old",
            now=NOW,
            lease_duration=timedelta(seconds=10),
            backend_timeout=timedelta(seconds=5),
            local_completion_margin=timedelta(seconds=2),
        )
        fenced = self.projector.complete(
            old, backend, "memory://late", now=NOW + timedelta(seconds=11)
        )
        new = self.projector.claim_next(
            backend,
            worker_id="worker-new",
            now=NOW + timedelta(seconds=11),
            lease_duration=timedelta(seconds=10),
            backend_timeout=timedelta(seconds=5),
            local_completion_margin=timedelta(seconds=2),
        )

        locator = self.projector.call_backend(
            new, backend, now=NOW + timedelta(seconds=11)
        )
        confirmed = self.projector.complete(
            new, backend, locator, now=NOW + timedelta(seconds=11)
        )

        self.assertEqual((fenced.status, confirmed.status), ("fenced", "confirmed"))
        row = self.ledger.db.execute(
            "SELECT result_status FROM projection_attempts WHERE attempt_no=1"
        ).fetchone()
        self.assertEqual(row[0], "lease_expired_before_call")

    def test_p5_unknown_verification_timeout_is_bounded_and_blocks(self):
        self._commit("create")
        state = InMemoryBackendState()
        self._drain(InMemoryBackend(state, materialize_fault="after_write_timeout"))

        result = self._drain(
            InMemoryBackend(state, locate_fault="timeout"),
            now=NOW + timedelta(seconds=1),
            max_attempts=2,
        )

        self.assertEqual(result.status, "blocked")
        row = self.ledger.db.execute(
            "SELECT status,last_error_class FROM outbox"
        ).fetchone()
        self.assertEqual(tuple(row), ("blocked", "verification_timeout"))

    def test_p6_duplicate_confirmation_reuses_receipt_and_attempt_numbers_are_unique(self):
        self._commit("create")
        state = InMemoryBackendState()
        first = self._drain(InMemoryBackend(state))
        self.ledger.db.execute(
            "UPDATE outbox SET status='pending',confirmed_receipt_id=NULL"
        )

        replay = self._drain(
            InMemoryBackend(state, materialize_fault="before_write"),
            now=NOW + timedelta(seconds=1),
        )

        self.assertEqual((first.status, replay.status), ("confirmed", "confirmed"))
        self.assertEqual(state.materialize_calls, 1)
        self.assertEqual(
            self.ledger.db.execute("SELECT COUNT(*) FROM projection_receipts").fetchone()[0], 1
        )
        attempts = self.ledger.db.execute(
            "SELECT attempt_no FROM projection_attempts ORDER BY attempt_no"
        ).fetchall()
        self.assertEqual([row[0] for row in attempts], [1, 2])

    def test_lease_configuration_must_exceed_backend_timeout_and_margin(self):
        self._commit("create")
        with self.assertRaises(ValueError):
            self.projector.claim_next(
                InMemoryBackend(InMemoryBackendState()),
                worker_id="worker",
                now=NOW,
                lease_duration=timedelta(seconds=7),
                backend_timeout=timedelta(seconds=5),
                local_completion_margin=timedelta(seconds=2),
            )

    def test_backend_adapter_calls_never_run_inside_ledger_write_transaction(self):
        self._commit("create")
        result = self._drain(TransactionCheckingBackend(self.ledger))
        self.assertEqual(result.status, "confirmed")

    def test_elapsed_backend_call_fences_completion(self):
        self._commit("create")
        backend = InMemoryBackend()
        with patch("sagacontext.projection.worker.time.monotonic", side_effect=[0, 0, 11]):
            result = self._drain(backend)
        self.assertEqual(result.status, "fenced")
        self.assertEqual(self.ledger.db.execute("SELECT COUNT(*) FROM projection_receipts").fetchone()[0], 0)

    def test_inspection_timeout_leaves_unknown_for_recovery(self):
        self._commit("create")
        backend = InMemoryBackend()
        with patch.object(backend, "inspect_projection", side_effect=BackendVerificationTimeout):
            self.assertEqual(self._drain(backend).status, "unknown")
        self.assertEqual(self._drain(backend).status, "confirmed")

    def test_repeated_completion_inspection_timeout_is_bounded(self):
        self._commit("create")
        backend = InMemoryBackend()
        with patch.object(backend, "inspect_projection", side_effect=BackendVerificationTimeout):
            results = [self._drain(backend, max_attempts=2).status for _ in range(2)]
        self.assertEqual(results, ["unknown", "blocked"])

    def test_unknown_delete_recovers_by_absence_at_target_locator(self):
        created = self._commit("create")
        backend = InMemoryBackend()
        old = self.projector.claim_next(
            backend, worker_id="old", now=NOW, lease_duration=timedelta(seconds=10),
            backend_timeout=timedelta(seconds=5), local_completion_margin=timedelta(seconds=2),
        )
        locator = self.projector.call_backend(old, backend, now=NOW)
        self._commit("new", memory_id=created.memory_id, expected_revision=1, value="v2")
        self.projector.complete(old, backend, locator, now=NOW)
        self._drain(backend)
        remove = backend.remove_projection
        def lost_response(locators):
            remove(locators)
            raise BackendUnknownError()
        with patch.object(backend, "remove_projection", side_effect=lost_response):
            self.assertEqual(self._drain(backend).status, "unknown")
        result = self._drain(backend)
        self.assertEqual(result.status, "confirmed")
        self.assertTrue(result.recovered)


if __name__ == "__main__":
    unittest.main()
