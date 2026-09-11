import tempfile
import unittest
from pathlib import Path

from sagacontext.backends import BackendHit
from sagacontext.ledger import CommitRequest, Ledger, Scope, TaskContext
from sagacontext.recall_policy import RecallPolicy


class RecallPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.ledger = Ledger(Path(self.temp.name) / "ledger.db", owner_id="owner")
        identity = self.ledger.register_project("project", Path(self.temp.name))
        self.context = TaskContext(owner_id="owner", project_id=identity["project_id"])
        self.scope = Scope(kind="project", project_id=identity["project_id"])
        self.policy = RecallPolicy(self.ledger)

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def create(self, receipt="a", value="Ledger body"):
        return self.ledger.commit(CommitRequest(receipt=receipt, operation="new", memory_type="decision",
                                              scope=self.scope, payload={"decision": value}))

    def hit(self, memory_id, **kwargs):
        return BackendHit(memory_id=memory_id, revision=kwargs.get("revision", 1),
                          generation=kwargs.get("generation", "g1"), rank=1, backend_locator="untrusted")

    def test_body_comes_from_ledger_and_budget_covers_serialized_bundle(self):
        a = self.create(value="合成正文")
        bundle = self.policy.assemble([self.hit(a.memory_id)], "g1", self.context)
        self.assertEqual(bundle.items[0].payload, {"decision": "合成正文"})
        self.assertEqual(bundle.used, len(bundle.text.encode()))
        omitted = self.policy.assemble([self.hit(a.memory_id)], "g1", self.context, budget=bundle.used - 1)
        self.assertEqual((omitted.text, omitted.omissions[0].reason), ("", "budget"))

    def test_owner_scope_revision_generation_and_deletion_filter(self):
        a = self.create()
        hit = self.hit(a.memory_id)
        for context, candidate, reason in [
            (TaskContext(owner_id="other"), hit, "owner"),
            (TaskContext(owner_id="owner"), hit, "scope"),
            (self.context, self.hit(a.memory_id, revision=2), "revision"),
            (self.context, self.hit(a.memory_id, generation="old"), "generation"),
        ]:
            bundle = self.policy.assemble([candidate], "g1", context)
            self.assertEqual(bundle.omissions[0].reason, reason)
            self.assertEqual(bundle.text, "")
        self.ledger.forget(a.memory_id, "forget")
        self.assertEqual(self.policy.assemble([hit], "g1", self.context).omissions[0].reason, "inactive")

    def test_superseded_revision_and_rescan_cannot_reactivate(self):
        a = self.create()
        old = self.hit(a.memory_id)
        self.ledger.commit(CommitRequest(receipt="replace", operation="refine", memory_id=a.memory_id,
            expected_revision=1, memory_type="decision", scope=self.scope, payload={"decision": "new"}))
        bundle = self.policy.assemble([old, self.hit(a.memory_id, revision=2)], "g1", self.context)
        self.assertEqual(bundle.omissions[0].reason, "revision")
        self.assertEqual([item.payload for item in bundle.items], [{"decision": "new"}])

    def test_large_candidate_does_not_hide_smaller_candidate(self):
        a = self.create("large", "x" * 5000)
        b = self.create("small", "small")
        bundle = self.policy.assemble([self.hit(a.memory_id), self.hit(b.memory_id)], "g1", self.context, budget=1000)
        self.assertEqual([item.memory_id for item in bundle.items], [b.memory_id])
