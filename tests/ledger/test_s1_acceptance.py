from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sagacontext.backends import InMemoryBackend, Projection
from sagacontext.ledger import CommitRequest, EvidenceInput, Ledger, Scope, TaskContext
from sagacontext.ledger.models import Verification


FIXTURE = Path(__file__).parents[1] / "fixtures" / "identity" / "g4.json"
G4 = {item["id"]: item for item in json.loads(FIXTURE.read_text())["scenarios"]}


class S1AcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "root"
        self.root.mkdir()
        self.ledger = Ledger(self.base / "ledger-v3.db", owner_id="owner")
        identity = self.ledger.register_project("root", self.root)
        self.project_id = identity["project_id"]
        self.workspace_id = identity["workspace_id"]
        self.context = TaskContext(
            owner_id="owner", project_id=self.project_id, workspace_id=self.workspace_id
        )

    def tearDown(self) -> None:
        self.ledger.close()
        self.temp.cleanup()

    def _request(
        self,
        receipt: str,
        *,
        operation: str = "new",
        memory_id: str | None = None,
        expected_revision: int | None = None,
        scope: Scope | None = None,
        evidence: list[EvidenceInput] | None = None,
    ) -> CommitRequest:
        return CommitRequest(
            receipt=receipt,
            operation=operation,
            memory_id=memory_id,
            expected_revision=expected_revision,
            memory_type="decision",
            scope=scope or Scope(kind="project", project_id=self.project_id),
            payload={"decision": "ledger is authoritative"},
            evidence=evidence or [],
        )

    @staticmethod
    def _evidence(evidence_id: str = "evidence-1") -> EvidenceInput:
        return EvidenceInput(
            evidence_id=evidence_id,
            source_event_id="event-1",
            claim_key="authority",
            evidence_kind="user_statement",
            locator={"event": 1},
            observed_at=datetime.now(timezone.utc),
            redacted_excerpt="ledger authority",
        )

    def test_i01_i04_i05_backend_cannot_override_or_run_during_local_commit(self):
        backend = InMemoryBackend()
        self.ledger.register_backend_generation("test", "g1")
        created = self.ledger.commit(self._request("new", evidence=[self._evidence()]))
        self.assertEqual(backend.items, {})
        backend.materialize(
            Projection(
                owner_id="owner",
                memory_id=created.memory_id,
                revision=999,
                generation="g1",
                memory_type="decision",
                searchable_text="backend-only replacement",
                scope_filter_tags=[f"project:{self.project_id}"],
                payload_digest="untrusted",
            ),
            "fake-newer-revision",
        )
        self.assertEqual(created.status, "committed_pending_projection")
        self.assertEqual(self.ledger.get_current([created.memory_id], self.context)[0].payload,
                         {"decision": "ledger is authoritative"})
        self.assertEqual(len(self.ledger.list_outbox()), 1)
        self.assertEqual(self.ledger.db.execute("SELECT COUNT(*) FROM evidence").fetchone()[0], 1)

    def test_i02_read_rejects_forged_workspace_and_task_context(self):
        created = self.ledger.commit(self._request("new"))
        forged_workspace = self.context.model_copy(update={"workspace_id": "foreign"})
        forged_task = self.context.model_copy(update={"task_id": "foreign"})
        self.assertEqual(self.ledger.get_current([created.memory_id], forged_workspace), [])
        self.assertEqual(self.ledger.read_history(created.memory_id, forged_task), [])

    def test_i06_replayed_source_claim_is_one_independent_evidence(self):
        created = self.ledger.commit(self._request("new", evidence=[self._evidence()]))
        confirmed = self.ledger.commit(
            self._request(
                "confirm",
                operation="confirm",
                memory_id=created.memory_id,
                expected_revision=1,
                evidence=[self._evidence("different-request-id")],
            )
        )
        self.assertEqual(confirmed.revision, 2)
        self.assertEqual(self.ledger.db.execute("SELECT COUNT(*) FROM evidence").fetchone()[0], 1)

    def test_i08_i09_retired_memory_is_not_current_and_scope_cannot_expand(self):
        task_id = self.ledger.create_task(self.project_id, "isolated")
        task_scope = Scope(kind="task", project_id=self.project_id, task_id=task_id)
        created = self.ledger.commit(self._request("task-new", scope=task_scope))
        expanded = self._request(
            "expand",
            operation="refine",
            memory_id=created.memory_id,
            expected_revision=1,
            scope=Scope(kind="project", project_id=self.project_id),
        )
        self.assertEqual(self.ledger.commit(expanded).reason, "identity_changed")
        retired = self.ledger.commit(
            self._request(
                "retire",
                operation="supersede",
                memory_id=created.memory_id,
                expected_revision=1,
                scope=task_scope,
            )
        )
        task_context = self.context.model_copy(update={"task_id": task_id})
        self.assertEqual(retired.revision, 2)
        self.assertEqual(self.ledger.get_current([created.memory_id], task_context), [])
        self.assertEqual(len(self.ledger.read_history(created.memory_id, task_context)), 2)

    def test_i10_i11_verification_is_independent_and_forget_differs_from_retire(self):
        evidence = self._evidence().model_copy(
            update={
                "verification": Verification(
                    verifier_kind="synthetic",
                    claim_key="authority",
                    input_fingerprint="input",
                    environment_fingerprint="environment",
                    expected={"exit": 0},
                    observed={"exit": 1},
                    outcome="fail",
                )
            }
        )
        created = self.ledger.commit(self._request("new", evidence=[evidence]))
        stored = self.ledger.db.execute("SELECT verification_json FROM evidence").fetchone()[0]
        self.assertEqual(json.loads(stored)["outcome"], "fail")
        self.assertEqual(created.status, "committed_local_only")
        forgotten = self.ledger.forget(created.memory_id, "forget")
        self.assertEqual(forgotten["status"], "local_redacted")
        self.assertEqual(self.ledger.read_history(created.memory_id, self.context), [])
        self.assertEqual(
            self.ledger.commit(self._request("replay", evidence=[self._evidence("replay")])).reason,
            "suppressed_after_deletion",
        )

    def test_g4_fixture_declares_all_identity_scenarios(self):
        scenario_ids = set(G4)
        self.assertEqual(
            scenario_ids,
            {
                "worktree_explicit_binding",
                "fork_unconfirmed",
                "monorepo_root_only",
                "monorepo_explicit_subproject",
                "moved_directory_rebind",
                "same_branch_parallel_tasks",
                "clone_unconfirmed",
            },
        )

    def test_g4_worktree_fork_and_clone_boundaries(self):
        worktree = self.base / "worktree"
        fork = self.base / "fork"
        clone = self.base / "clone"
        for path in (worktree, fork, clone):
            path.mkdir()
            self.assertIsNone(self.ledger.resolve_project(path))
        worktree_workspace = self.ledger.bind_location(self.project_id, worktree)
        fork_identity = self.ledger.register_project("fork", fork)
        clone_identity = self.ledger.register_project("clone", clone)
        worktree_task = self.ledger.create_task(self.project_id, "shared explicitly")
        worktree_session = self.ledger.open_session("synthetic", "worktree", worktree_workspace)
        self.ledger.bind_task(worktree_session, worktree_task, "event-worktree")
        self.assertEqual(
            self.ledger.current_task(worktree_session) == worktree_task,
            G4["worktree_explicit_binding"]["same_task"],
        )
        self.assertEqual(
            worktree_workspace == self.workspace_id,
            G4["worktree_explicit_binding"]["same_workspace"],
        )
        self.assertEqual(
            fork_identity["project_id"] == self.project_id,
            G4["fork_unconfirmed"]["same_project"],
        )
        self.assertEqual(
            clone_identity["project_id"] == self.project_id,
            G4["clone_unconfirmed"]["same_project"],
        )

    def test_g4_monorepo_longest_location_and_explicit_subproject(self):
        packages = self.root / "packages"
        child = packages / "child"
        child.mkdir(parents=True)
        root_resolution = self.ledger.resolve_project(child)
        self.assertEqual(
            root_resolution["project_id"] == self.project_id,
            G4["monorepo_root_only"]["same_project"],
        )
        self.assertEqual(
            root_resolution["workspace_id"] == self.workspace_id,
            G4["monorepo_root_only"]["same_workspace"],
        )
        subproject = self.ledger.register_project("packages", packages)
        resolved = self.ledger.resolve_project(child)
        self.assertEqual(resolved["project_id"], subproject["project_id"])
        self.assertEqual(
            resolved["project_id"] == self.project_id,
            G4["monorepo_explicit_subproject"]["same_project"],
        )
        self.assertEqual(
            resolved["workspace_id"] == self.workspace_id,
            G4["monorepo_explicit_subproject"]["same_workspace"],
        )

    def test_g4_move_preserves_workspace_and_parallel_tasks_stay_active(self):
        old = self.root / "old"
        old.mkdir()
        workspace_id = self.ledger.bind_location(self.project_id, old)
        moved = self.root / "moved"
        old.rename(moved)
        rebound = self.ledger.rebind_location(self.project_id, old, moved)
        self.assertEqual(
            rebound == workspace_id,
            G4["moved_directory_rebind"]["same_workspace"],
        )
        first = self.ledger.create_task(self.project_id, "first")
        second = self.ledger.create_task(self.project_id, "second")
        session = self.ledger.open_session("synthetic", "session", workspace_id)
        self.ledger.bind_task(session, first, "event-1")
        self.ledger.bind_task(session, second, "event-2")
        statuses = self.ledger.db.execute(
            "SELECT status FROM tasks WHERE task_id IN (?,?)", (first, second)
        ).fetchall()
        self.assertEqual([row["status"] for row in statuses], ["active", "active"])
        self.assertEqual(self.ledger.current_task(session), second)
        self.assertEqual(first == second, G4["same_branch_parallel_tasks"]["same_task"])


if __name__ == "__main__":
    unittest.main()
