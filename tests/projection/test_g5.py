import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sagacontext.backends import BackendHit, InMemoryBackend
from sagacontext.ledger import CommitRequest, Ledger, Scope, TaskContext
from sagacontext.projection import Projector
from sagacontext.recall_policy import RecallPolicy


class G5Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.ledger = Ledger(Path(self.temp.name) / "ledger.db", owner_id="owner")
        self.ledger.register_backend_generation("memory-test-double", "g1")
        self.backend = InMemoryBackend()
        self.projector = Projector(self.ledger)
        self.request = CommitRequest(receipt="create", operation="new", memory_type="decision",
                                     scope=Scope(kind="global"), payload={"decision": "old"})
        self.context = TaskContext(owner_id="owner")
        self.now = datetime.now(timezone.utc)

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def drain(self, **kwargs):
        return self.projector.drain_once(self.backend, worker_id="worker", now=self.now,
            backend_timeout=timedelta(seconds=3), local_completion_margin=timedelta(seconds=2),
            lease_duration=timedelta(seconds=30), verification_timeout=timedelta(seconds=3), **kwargs)

    def test_deleted_unknown_write_recovers_and_cleans_after_body_erasure(self):
        created = self.ledger.commit(self.request)
        self.backend.materialize_fault = "after_write_timeout"
        self.assertEqual(self.drain().status, "unknown")
        self.ledger.forget(created.memory_id, "forget")
        self.assertEqual(self.drain().status, "obsolete")
        self.assertEqual(self.drain().status, "confirmed")
        self.assertEqual(self.backend.items, {})
        self.assertEqual(self.ledger.commit(self.request.model_copy(update={"receipt": "replay"})).status, "rejected")

    def test_delete_cleans_older_revision_and_inactive_generation(self):
        created = self.ledger.commit(self.request)
        self.drain()
        self.ledger.register_backend_generation("memory-test-double", "g2")
        self.ledger.commit(self.request.model_copy(update={"receipt": "refine", "operation": "refine",
            "memory_id": created.memory_id, "expected_revision": 1, "payload": {"decision": "new"}}))
        self.drain()
        self.assertEqual(len(self.backend.items), 2)
        self.ledger.forget(created.memory_id, "forget")
        self.assertEqual(self.drain().status, "confirmed")
        self.assertEqual(self.drain().status, "confirmed")
        self.assertEqual(self.backend.items, {})
        self.assertEqual(self.ledger.commit(self.request.model_copy(update={"receipt": "old-rescan"})).status, "rejected")

    def test_supersede_suppresses_rescan_and_stale_hit(self):
        created = self.ledger.commit(self.request)
        self.drain()
        old = BackendHit(memory_id=created.memory_id, revision=1, generation="g1", rank=1,
                         backend_locator=next(iter(self.backend.items)))
        self.ledger.commit(self.request.model_copy(update={"receipt": "retire", "operation": "supersede",
            "memory_id": created.memory_id, "expected_revision": 1}))
        bundle = RecallPolicy(self.ledger).assemble([old], "g1", self.context)
        self.assertEqual((bundle.text, bundle.omissions[0].reason), ("", "inactive"))
        self.assertEqual(self.drain().status, "confirmed")
        self.assertEqual(self.ledger.commit(self.request.model_copy(update={"receipt": "rescan"})).status, "rejected")
        reopened = Ledger(Path(self.temp.name) / "ledger.db", owner_id="owner")
        try:
            self.assertEqual(RecallPolicy(reopened).assemble([old], "g1", self.context).text, "")
        finally:
            reopened.close()
