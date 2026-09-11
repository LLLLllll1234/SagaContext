from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from sagacontext.backends import InMemoryBackend, Projection
from sagacontext.ledger import CommitRequest, EvidenceInput, Ledger, Scope, TaskContext


class LedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.ledger = Ledger(root / "ledger-v3.db", owner_id="owner-a")
        identity = self.ledger.register_project("project-a", root)
        self.project_id = identity["project_id"]
        self.workspace_id = identity["workspace_id"]
        self.context = TaskContext(
            owner_id="owner-a", project_id=self.project_id, workspace_id=self.workspace_id
        )

    def tearDown(self) -> None:
        self.ledger.close()
        self.temp.cleanup()

    def _new(self, receipt: str = "receipt-1", scope: Scope | None = None) -> CommitRequest:
        return CommitRequest(
            receipt=receipt,
            operation="new",
            memory_type="convention",
            scope=scope or Scope(kind="project", project_id=self.project_id),
            payload={"rule": "fixtures live in conftest.py"},
            evidence=[
                EvidenceInput(
                    evidence_id="evidence-1",
                    source_event_id="event-1",
                    claim_key="fixture_location",
                    evidence_kind="user_statement",
                    locator={"session": "s1", "event": 1},
                    observed_at=datetime.now(timezone.utc),
                    redacted_excerpt="fixture convention",
                )
            ],
        )

    def test_scope_schema_rejects_ambiguous_shapes(self):
        with self.assertRaises(ValidationError):
            Scope(kind="global", project_id=self.project_id)
        with self.assertRaises(ValidationError):
            Scope(kind="path", project_id=self.project_id)

    def test_projects_and_tasks_use_stable_explicit_identity(self):
        same = self.ledger.register_project("renamed", Path(self.temp.name))
        self.assertEqual(same["project_id"], self.project_id)
        first = self.ledger.create_task(self.project_id, "same goal")
        second = self.ledger.create_task(self.project_id, "same goal")
        self.assertNotEqual(first, second)

    def test_project_location_resolution_and_explicit_rebind(self):
        nested = Path(self.temp.name) / "packages" / "app"
        nested.mkdir(parents=True)
        self.assertEqual(self.ledger.resolve_project(nested)["project_id"], self.project_id)

        clone = Path(self.temp.name).parent / f"{Path(self.temp.name).name}-clone"
        clone.mkdir()
        try:
            self.assertIsNone(self.ledger.resolve_project(clone))
            clone_workspace = self.ledger.bind_location(self.project_id, clone)
            self.assertEqual(self.ledger.resolve_project(clone)["workspace_id"], clone_workspace)
            moved = clone.with_name(f"{clone.name}-moved")
            shutil.move(clone, moved)
            self.assertIsNone(self.ledger.resolve_project(moved))
            self.assertEqual(self.ledger.rebind_location(self.project_id, clone, moved), clone_workspace)
            self.assertEqual(self.ledger.resolve_project(moved)["workspace_id"], clone_workspace)
        finally:
            if clone.exists():
                shutil.rmtree(clone)
            moved = clone.with_name(f"{clone.name}-moved")
            if moved.exists():
                shutil.rmtree(moved)

    def test_task_switch_has_explicit_boundaries_and_does_not_pause_other_task(self):
        first = self.ledger.create_task(self.project_id, "first")
        second = self.ledger.create_task(self.project_id, "second")
        session = self.ledger.open_session("codex-cli", "host-session", self.workspace_id)
        self.ledger.bind_task(session, first, "event-1")
        self.assertEqual(self.ledger.current_task(session), first)
        self.ledger.bind_task(session, second, "event-2")
        self.assertEqual(self.ledger.current_task(session), second)
        old_binding = self.ledger.db.execute(
            "SELECT end_event_id FROM task_bindings WHERE session_id=? AND task_id=?", (session, first)
        ).fetchone()
        self.assertEqual(old_binding["end_event_id"], "event-2")
        statuses = self.ledger.db.execute(
            "SELECT status FROM tasks WHERE task_id IN (?,?)", (first, second)
        ).fetchall()
        self.assertEqual([row["status"] for row in statuses], ["active", "active"])

    def test_commit_is_idempotent_and_receipt_cannot_change_meaning(self):
        request = self._new()
        first = self.ledger.commit(request)
        second = self.ledger.commit(request)
        self.assertEqual(first, second)
        self.assertEqual(self.ledger.sequence, 1)
        changed = request.model_copy(update={"payload": {"rule": "different"}})
        self.assertEqual(self.ledger.commit(changed).reason, "receipt_reused")

    def test_i07_stale_revision_conflicts_without_overwrite(self):
        created = self.ledger.commit(self._new())
        refine = CommitRequest(
            receipt="refine-1",
            operation="refine",
            memory_id=created.memory_id,
            expected_revision=1,
            memory_type="convention",
            scope=Scope(kind="project", project_id=self.project_id),
            payload={"rule": "small modules may use local fixtures"},
        )
        self.assertEqual(self.ledger.commit(refine).revision, 2)
        stale = refine.model_copy(update={"receipt": "refine-stale", "payload": {"rule": "stale"}})
        result = self.ledger.commit(stale)
        self.assertEqual((result.status, result.reason), ("conflict", "revision_changed"))
        current = self.ledger.get_current([created.memory_id], self.context)[0]
        self.assertEqual(current.payload["rule"], "small modules may use local fixtures")

    def test_confirm_requires_evidence_and_cannot_change_payload(self):
        request = self._new()
        created = self.ledger.commit(request)
        base = {
            "operation": "confirm",
            "memory_id": created.memory_id,
            "expected_revision": 1,
            "memory_type": "convention",
            "scope": request.scope,
        }
        no_evidence = CommitRequest(receipt="confirm-empty", payload=request.payload, **base)
        self.assertEqual(self.ledger.commit(no_evidence).reason, "confirmation_requires_evidence")
        changed = CommitRequest(
            receipt="confirm-changed", payload={"rule": "changed"}, evidence=request.evidence, **base
        )
        self.assertEqual(self.ledger.commit(changed).reason, "confirmation_cannot_change_payload")

    def test_authoritative_read_rechecks_project_task_and_path_scope(self):
        task_id = self.ledger.create_task(self.project_id, "task")
        cases = [
            (Scope(kind="project", project_id=self.project_id), self.context, True),
            (Scope(kind="task", project_id=self.project_id, task_id=task_id), self.context, False),
            (
                Scope(kind="task", project_id=self.project_id, task_id=task_id),
                self.context.model_copy(update={"task_id": task_id}),
                True,
            ),
            (
                Scope(kind="path", project_id=self.project_id, path_pattern="tests/*.py"),
                self.context.model_copy(update={"touched_paths": ["src/a.py"]}),
                False,
            ),
            (
                Scope(kind="path", project_id=self.project_id, path_pattern="tests/*.py"),
                self.context.model_copy(update={"touched_paths": ["tests/a.py"]}),
                True,
            ),
        ]
        for index, (scope, context, expected) in enumerate(cases):
            created = self.ledger.commit(self._new(f"scope-{index}", scope))
            self.assertEqual(bool(self.ledger.get_current([created.memory_id], context)), expected)
        foreign = self.context.model_copy(update={"owner_id": "owner-b"})
        self.assertEqual(self.ledger.get_current([created.memory_id], foreign), [])

    def test_i03_revision_evidence_and_outbox_share_commit(self):
        self.ledger.register_backend_generation("test", "g1")
        result = self.ledger.commit(self._new())
        self.assertEqual(result.status, "committed_pending_projection")
        counts = {
            table: self.ledger.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("memories", "revisions", "evidence", "revision_evidence", "outbox")
        }
        self.assertEqual(set(counts.values()), {1})

    def test_forget_blocks_current_and_history_and_is_idempotent(self):
        created = self.ledger.commit(self._new())
        first = self.ledger.forget(created.memory_id, "delete-1")
        second = self.ledger.forget(created.memory_id, "delete-1")
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "local_redacted")
        self.assertEqual(self.ledger.get_current([created.memory_id], self.context), [])
        self.assertEqual(self.ledger.read_history(created.memory_id, self.context), [])
        payload = self.ledger.db.execute(
            "SELECT payload_json FROM revisions WHERE memory_id=?", (created.memory_id,)
        ).fetchone()[0]
        self.assertEqual(payload, "{}")
        replay = self.ledger.commit(self._new(receipt="replay"))
        self.assertEqual((replay.status, replay.reason), ("rejected", "suppressed_after_deletion"))

    def test_forget_receipt_cannot_be_reused_for_another_memory(self):
        first = self.ledger.commit(self._new("first"))
        second = self.ledger.commit(
            self._new("second", Scope(kind="global"))
        )
        self.ledger.forget(first.memory_id, "one-delete-request")
        reused = self.ledger.forget(second.memory_id, "one-delete-request")
        self.assertEqual(reused, {"status": "needs_action", "reason": "receipt_reused"})
        self.assertEqual(self.ledger.get_current([second.memory_id], self.context)[0].state, "active")

    def test_forget_blocks_replay_of_evidence_from_an_older_revision(self):
        created = self.ledger.commit(self._new())
        refined = CommitRequest(
            receipt="refine-before-delete",
            operation="refine",
            memory_id=created.memory_id,
            expected_revision=1,
            memory_type="convention",
            scope=Scope(kind="project", project_id=self.project_id),
            payload={"rule": "new wording"},
        )
        self.ledger.commit(refined)
        self.ledger.forget(created.memory_id, "delete-refined")
        replay = self.ledger.commit(self._new(receipt="replay-old-evidence"))
        self.assertEqual((replay.status, replay.reason), ("rejected", "suppressed_after_deletion"))

    def test_i03_commit_failure_rolls_back_head_revision_and_outbox(self):
        request = self._new()
        request.evidence.append(
            request.evidence[0].model_copy(
                update={"source_event_id": "event-with-reused-evidence-id", "claim_key": "another-claim"}
            )
        )
        with self.assertRaisesRegex(ValueError, "evidence_id_collision"):
            self.ledger.commit(request)
        for table in ("memories", "revisions", "evidence", "revision_evidence", "outbox"):
            count = self.ledger.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            self.assertEqual(count, 0, table)
        self.assertEqual(self.ledger.sequence, 0)


class BackendContractTests(unittest.TestCase):
    def test_memory_backend_has_stable_idempotent_mapping(self):
        backend = InMemoryBackend()
        projection = Projection(
            owner_id="owner",
            memory_id="memory",
            revision=1,
            generation="g1",
            memory_type="gotcha",
            searchable_text="pytest hangs on shutdown",
            scope_filter_tags=["project:p1"],
            payload_digest="digest",
        )
        first = backend.materialize(projection, "operation-1")
        self.assertEqual(backend.materialize(projection, "operation-1"), first)
        self.assertEqual(backend.locate_projection("memory", 1, "g1"), first)
        self.assertEqual(backend.search("pytest", "g1")[0].memory_id, "memory")
        self.assertEqual(backend.remove_projection([first]), 1)


if __name__ == "__main__":
    unittest.main()
