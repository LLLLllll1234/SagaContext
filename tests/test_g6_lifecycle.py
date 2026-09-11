import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import g6_lifecycle as g6
import g6_lifecycle_hook as hook
import verify_openviking_projector as runner

from sagacontext.backends import InMemoryBackend
from sagacontext.ledger import CommitRequest, Ledger, Scope, TaskContext
from sagacontext.maintenance import BatchService, BatchWorker, CandidateInput
from sagacontext.maintenance.judge import OpenAIProposalJudge
from sagacontext.models import Delta
from datetime import datetime, timedelta, timezone


class FakeClient:
    def __init__(self):
        self.seen = []

    async def judge(self, anchors, candidates, summary):
        self.seen.append((anchors, candidates, summary))
        candidate, = candidates
        return [Delta(candidate_id=candidate.candidate_id, layer="project", type="decision",
            relation="supersede" if anchors else "new", anchor_uri=anchors[0]["memory_id"] if anchors else None,
            key=candidate.topic_key, fields={"command": "python -m unittest" if anchors else "pytest"},
            evidence_ids=candidate.event_ids)]


class FakeHost:
    def __init__(self, root, stop_file, *, fail=None):
        self.root, self.stop_file, self.fail = root, stop_file, fail

    def run(self, name, prompt, ledger, context, hits):
        scenario = self.root / "g6" / name
        scenario.mkdir()
        request = {"workspace": str(self.root / "g6/workspace"), "ledger": str(self.root / "ledger.db"),
                   "context": context.model_dump(mode="json"), "hits": [h.model_dump() for h in hits],
                   "budget": 2000, "stop_file": str(self.stop_file), "run_id": "fixture", "scenario": name}
        receipt = None
        for event in g6.EVENTS:
            output, emitted = hook.handle(request, {"hook_event_name": event, "cwd": request["workspace"],
                                                   "session_id": name, "turn_id": "turn"}, scenario)
            if emitted:
                receipt = emitted
        if prompt in (g6.SOURCE, g6.UPDATE):
            final = "ACK"
        else:
            items = receipt["bundle"]["items"]
            final = json.dumps({"command": items[0]["payload"]["command"] if items else None})
        if self.fail == name:
            final = '{"command":"stale"}'
        if self.fail == "reused-session":
            receipt["host_session_digest"] = "same-session"
        records = [json.loads(line) for line in (scenario / "events.jsonl").read_text().splitlines()]
        return {"completed": True, "final": final, "records": records, "receipt": receipt}


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ledger = Ledger(self.root / "ledger.db", owner_id="synthetic-owner")
        self.backend = InMemoryBackend()
        self.ledger.register_backend_generation(self.backend.capabilities().backend, "g1")
        self.report = {"g5": {"status": "passed"}, "assertions": []}
        self.plan = {"digest": "fixture", "run_id": "fixture", "limits": {"bundle_budget": 2000, "search_timeout_seconds": 0}}
        self.stop = self.root / "STOP"

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def check(self, name, ok, evidence=None):
        self.report["assertions"].append({"name": name, "status": "pass" if ok else "fail"})
        if not ok:
            raise AssertionError(name)

    def run_lifecycle(self, fail=None):
        client = FakeClient()
        g6.verify_lifecycle(self.ledger, self.backend, self.check, self.report, self.root, self.plan, self.stop,
                            host=FakeHost(self.root, self.stop, fail=fail), judge=OpenAIProposalJudge(client))
        return client

    def test_three_chains_use_actual_ledger_worker_projector_and_fresh_hook_read(self):
        client = self.run_lifecycle()
        result = self.report["g6_lifecycle"]
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["mode"], "test_doubles")
        self.assertEqual([c["kind"] for c in result["chains"]], ["new", "supersede", "forget"])
        self.assertEqual(len(result["host_sessions"]), 6)
        self.assertEqual(len(client.seen), 2)
        self.assertIn(g6.SOURCE, client.seen[0][2])
        self.assertIn(g6.UPDATE, client.seen[1][2])
        self.assertTrue(client.seen[1][0])
        self.assertFalse(self.backend.items)
        self.assertEqual([r[0] for r in self.ledger.db.execute("SELECT state FROM memories ORDER BY rowid")], ["retired", "deleted"])
        self.assertEqual(result["host_sessions"][-1]["verified_result"], {"command": None})
        self.assertTrue(all(a["status"] == "pass" for a in self.report["assertions"]))

    def test_emitting_a_hook_bundle_is_not_sufficient_for_task_success(self):
        with self.assertRaisesRegex(AssertionError, "consume-new_task_result"):
            self.run_lifecycle(fail="consume-new")
        self.assertEqual(self.report["g6_lifecycle"]["status"], "running")
        self.assertNotIn("verified_result", self.report["g6_lifecycle"]["host_sessions"][-1])

    def test_reused_session_is_not_cross_session_evidence(self):
        with self.assertRaisesRegex(AssertionError, "source_independent_session"):
            self.run_lifecycle(fail="reused-session")

    def test_g5_failure_and_stop_prevent_host_and_judge_calls(self):
        self.report["g5"]["status"] = "failed"
        with self.assertRaisesRegex(AssertionError, "requires_G5"):
            self.run_lifecycle()
        self.report["g5"]["status"] = "passed"
        self.stop.touch()
        with self.assertRaisesRegex(RuntimeError, "kill_switch"):
            self.run_lifecycle()
        self.assertEqual(self.report["g6_lifecycle"]["host_sessions"], [])

    def test_supersede_and_successor_roll_back_together(self):
        identity = self.ledger.register_project("fixture", self.root)
        sid = self.ledger.open_session("codex", "fixture", identity["workspace_id"])
        from sagacontext.maintenance import EventJournal, JournalEvent
        event = EventJournal(self.ledger).append(JournalEvent(host="codex", host_version=g6.HOST_VERSION,
            session_id=sid, workspace_id=identity["workspace_id"], event_kind="user_message",
            occurred_at=datetime.now(timezone.utc), trust_class="synthetic", source_generation="test",
            source_event_key="update", source_locator={}, payload={"text": g6.UPDATE}, parser_version="test"))
        scope = Scope(kind="project", project_id=identity["project_id"])
        old = self.ledger.commit(CommitRequest(receipt="old", operation="new", memory_type="decision", scope=scope,
            payload={"key": "verification-command", "command": "pytest"}))
        judge = OpenAIProposalJudge(FakeClient())
        batches = BatchService(self.ledger, judge_version=judge.version)
        batches.create_candidate(CandidateInput(session_id=sid, kind="explicit", memory_type_hint="decision",
            scope_hint=scope, topic_key="verification-command", event_ids=(event.event_id,)))
        receipt = batches.request_batch(sid, None, (old.memory_id,))
        worker = BatchWorker(self.ledger)
        worker.run_once(judge, worker_id="test", now=datetime.now(timezone.utc), lease_duration=timedelta(seconds=30),
                        stop_after_proposals=True)
        claim = self.ledger.db.execute("SELECT lease_token FROM batches WHERE batch_id=?", (receipt.batch_id,)).fetchone()[0]
        plan = worker._plan(receipt.batch_id, worker._proposed(receipt.batch_id))
        self.assertEqual([op.operation for op in plan.memory_operations], ["supersede", "new"])
        original = self.ledger._enqueue_projection
        calls = []
        def fail_second(*args):
            calls.append(args)
            if len(calls) == 2:
                raise sqlite3.OperationalError("synthetic failure")
            return original(*args)
        with patch.object(self.ledger, "_enqueue_projection", side_effect=fail_second):
            with self.assertRaises(sqlite3.OperationalError):
                self.ledger.commit_batch(plan, claim)
        row = self.ledger.db.execute("SELECT state,current_revision FROM memories WHERE memory_id=?", (old.memory_id,)).fetchone()
        self.assertEqual(tuple(row), ("active", 1))
        self.assertEqual(self.ledger.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0], 1)
        self.ledger.commit_batch(plan, claim)
        self.assertEqual(self.ledger.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0], 2)
        links = self.ledger.db.execute("SELECT DISTINCT memory_id FROM revision_evidence WHERE claim_key=?", (plan.proposal_ids[0],)).fetchall()
        self.assertEqual(len(links), 2)


class HookScopeTests(unittest.TestCase):
    def test_workspace_and_kill_switch_deny_before_ledger_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            request = {"workspace": str(root / "workspace"), "stop_file": str(root / "STOP")}
            output, receipt = hook.handle(request, {"hook_event_name": "SessionStart", "cwd": str(root)}, root)
            self.assertEqual(output, {})
            self.assertEqual(receipt["status"], "blocked_scope")
            (root / "STOP").touch()
            output, receipt = hook.handle(request, {}, root)
            self.assertEqual(output, {})
            self.assertEqual(receipt["status"], "blocked_kill_switch")
            self.assertFalse((root / "events.jsonl").exists())


class PlanTests(unittest.TestCase):
    def test_prepare_has_no_runtime_or_external_side_effects_and_is_bound_to_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "plan"
            with patch("sys.argv", ["prepare", "--output", str(output)]), patch("subprocess.Popen") as popen:
                self.assertEqual(g6.main(), 0)
                popen.assert_not_called()
            path = output / "plan.json"
            plan = g6.load_plan(path)
            self.assertFalse(Path(plan["runtime_root"]).exists())
            self.assertEqual(plan["events"], list(g6.EVENTS))
            with self.assertRaisesRegex(ValueError, "digest_mismatch"):
                g6.load_plan(path, "wrong")
            with patch.object(g6, "source_digests", return_value={}):
                with self.assertRaisesRegex(ValueError, "sources_changed"):
                    g6.load_plan(path)
            changed = dict(plan, workspace="/unapproved")
            changed.pop("digest")
            changed["digest"] = g6.digest(changed)
            path.write_text(json.dumps(changed))
            with self.assertRaises(ValueError):
                g6.load_plan(path)

    def test_runner_rejects_missing_execution_digest_before_backend_setup(self):
        with patch("sys.argv", ["runner", "--g6-plan", "unused.json"]), patch.object(runner, "RecordingClient") as client:
            with self.assertRaises(SystemExit) as error:
                runner.main()
            self.assertEqual(error.exception.code, 2)
            client.assert_not_called()

    def test_runtime_cleanup_after_exception_does_not_remove_preexisting_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "runtime"
            plan = {"runtime_root": str(root), "workspace": str(root / "g6/workspace"), "ledger": str(root / "ledger.db")}
            cleanup = {}
            with self.assertRaises(RuntimeError):
                with g6.isolated_runtime(plan, cleanup):
                    Path(plan["workspace"]).mkdir(parents=True)
                    Path(plan["ledger"]).touch()
                    raise RuntimeError("fixture")
            self.assertTrue(all(cleanup.values()))
            root.mkdir()
            marker = root / "keep"
            marker.touch()
            with self.assertRaises(FileExistsError):
                with g6.isolated_runtime(plan, {}):
                    pass
            self.assertTrue(marker.exists())


if __name__ == "__main__":
    unittest.main()
