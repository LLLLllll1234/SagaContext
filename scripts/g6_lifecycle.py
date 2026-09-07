"""Prepare and verify an explicitly authorized, synthetic G6 lifecycle."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from probe_codex_host import _prepare_isolated_codex_home, _last_agent_message
from sagacontext.bench.admission import admission_errors, load_results, FROZEN_PROMPT
from sagacontext.bench.real_judge import build_adapter
from sagacontext.config import Config
from sagacontext.hosts.codex_shadow import CodexShadowAdapter
from sagacontext.ledger import Ledger, Scope, TaskContext
from sagacontext.maintenance import BatchService, BatchWorker, CandidateInput
from sagacontext.projection import Projector
from sagacontext.recall_policy import RecallPolicy

REPO = Path(__file__).resolve().parents[1]
EVENTS = ("SessionStart", "UserPromptSubmit", "Stop", "SessionEnd")
HOST_VERSION = "codex-cli 0.153.4"
SOURCE = "For this project, use pytest as the repository verification command. Remember this decision. Reply ACK only."
UPDATE = "From now on, use python -m unittest instead of pytest as the repository verification command. Remember this replacement. Reply ACK only."
QUERY = 'What is the saved repository verification command? Do not infer a default or inspect files. Reply only JSON with key command; use null if no saved decision is available.'
G5 = REPO / "artifacts/probes/s3-1-20260906T173109Z-4d29eaf4/s3-1.json"
ACCEPTANCE = REPO / "artifacts/real-judge/20260906T165252Z-v6-authorized"
SHADOW = REPO / "artifacts/real-judge/20260906T165804Z-v6-authorized-shadow"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def source_digests():
    paths = sorted([*REPO.glob("src/sagacontext/**/*.py"), *REPO.glob("scripts/*.py")])
    return {str(p.relative_to(REPO)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def plan_body(run_id):
    if not run_id.startswith("g6-") or len(run_id) != 35:
        raise ValueError("invalid_g6_run_id")
    uuid.UUID(hex=run_id[3:])
    runtime = Path(tempfile.gettempdir()).resolve() / ("sagacontext-" + run_id)
    evidence = [G5, *[root / model / "replay.jsonl" for root in (ACCEPTANCE, SHADOW)
                            for model in ("deepseek-v4-pro", "deepseek-v4-flash")]]
    return {
        "version": "g6-lifecycle-v1", "run_id": run_id, "status": "prepared_not_authorized",
        "runtime_root": str(runtime), "workspace": str(runtime / "g6/workspace"),
        "ledger": str(runtime / "ledger.db"), "backend_user": "sagag6-" + run_id[3:],
        "namespace": f"viking://user/sagag6-{run_id[3:]}/memories/sagacontext/{run_id}",
        "events": list(EVENTS), "host_version": HOST_VERSION, "host_model": "gpt-5.6-terra",
        "judge_model": "deepseek-v4-pro", "prompt_contract": FROZEN_PROMPT,
        "payload_class": "synthetic", "normal_hooks": False, "private_data_import": False,
        "allowed_effects": ["temporary_backend_user", "isolated_ledger_write", "isolated_projection",
                            "isolated_recall", "next_session_context_injection", "test_resource_deletion"],
        "chains": ["new_then_consume", "supersede_then_consume", "forget_then_absent"],
        "fixtures": {"source": SOURCE, "update": UPDATE, "query": QUERY},
        "limits": {"host_calls": 6, "host_timeout_seconds": 180, "judge_attempts": 3,
                   "judge_timeout_seconds": 300, "search_timeout_seconds": 60, "bundle_budget": 2000},
        "sources": source_digests(),
        "prerequisites": {str(p.relative_to(REPO)): hashlib.sha256(p.read_bytes()).hexdigest() for p in evidence},
    }


def load_plan(path, execute_digest=None):
    plan = json.loads(Path(path).read_text())
    declared = plan.pop("digest")
    if declared != digest(plan) or plan != plan_body(plan["run_id"]):
        raise ValueError("g6_plan_or_sources_changed")
    if execute_digest is not None and execute_digest != declared:
        raise ValueError("g6_execution_digest_mismatch")
    g5 = json.loads(G5.read_text())
    if g5["status"] != "passed" or g5["g5"]["status"] != "passed" or g5["cleanup"]["status"] != "passed":
        raise ValueError("g5_not_admitted")
    if not g5["assertions"] or any(a["status"] != "pass" for a in g5["assertions"]):
        raise ValueError("g5_assertion_failure")
    for root in (ACCEPTANCE, SHADOW):
        for model in ("deepseek-v4-pro", "deepseek-v4-flash"):
            if admission_errors(load_results(root / model / "replay.jsonl"), model=model):
                raise ValueError("judge_not_admitted")
    plan["digest"] = declared
    return plan


def ensure_running(stop_file):
    if Path(stop_file).exists():
        raise RuntimeError("g6_kill_switch")


@contextmanager
def isolated_runtime(plan, cleanup):
    root = Path(plan["runtime_root"])
    root.mkdir(mode=0o700, exist_ok=False)
    try:
        yield str(root)
    finally:
        shutil.rmtree(root)
        cleanup["runtime_removed"] = not root.exists()
        cleanup["hooks_removed"] = not Path(plan["workspace"]).exists()
        cleanup["ledger_removed"] = not Path(plan["ledger"]).exists()


class LifecycleHost:
    def __init__(self, plan, stop_file):
        self.plan, self.stop_file = plan, Path(stop_file)
        self.workspace = Path(plan["workspace"])
        self.root = self.workspace.parent

    def run(self, name, prompt, ledger, context, hits):
        ensure_running(self.stop_file)
        scenario = self.root / name
        scenario.mkdir()
        home = scenario / "codex-home"
        runtime = _prepare_isolated_codex_home(home, self.plan["host_model"], self.workspace)
        request = {"workspace": str(self.workspace), "ledger": self.plan["ledger"],
                   "context": context.model_dump(mode="json"), "hits": [h.model_dump() for h in hits],
                   "budget": self.plan["limits"]["bundle_budget"], "stop_file": str(self.stop_file),
                   "run_id": self.plan["run_id"], "scenario": name}
        request_path = scenario / "request.json"
        request_path.write_text(json.dumps(request))
        command = shlex.join([sys.executable, str(REPO / "scripts/g6_lifecycle_hook.py"),
                              "--request", str(request_path)])
        config_dir = self.workspace / ".codex"
        config_dir.mkdir(exist_ok=True)
        hook_path = config_dir / "hooks.json"
        hook_path.write_text(json.dumps({"hooks": {
            event: [{"hooks": [{"type": "command", "command": command, "timeout": 10}]}]
            for event in EVENTS}}))
        env = {key: value for key, value in os.environ.items() if not key.startswith("SAGACONTEXT_LLM_")}
        env["CODEX_HOME"] = str(home)
        env["PYTHONPATH"] = str(REPO / "src")
        started = time.monotonic()
        try:
            with tempfile.TemporaryFile(mode="w+") as stdout, tempfile.TemporaryFile(mode="w+") as stderr:
                process = subprocess.Popen([
                    "codex", "--dangerously-bypass-hook-trust", "--ask-for-approval", "never",
                    "--sandbox", "workspace-write", "--cd", str(self.workspace),
                    "--model", self.plan["host_model"], "exec", "--json", "--ephemeral", "--ignore-rules", prompt,
                ], stdout=stdout, stderr=stderr, text=True, env=env, start_new_session=True)
                try:
                    while process.poll() is None:
                        ensure_running(self.stop_file)
                        if time.monotonic() - started > self.plan["limits"]["host_timeout_seconds"]:
                            raise TimeoutError("g6_host_timeout")
                        time.sleep(0.1)
                finally:
                    # Stop the process group, including pending hook children.
                    import signal
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait()
                stdout.seek(0)
                events = []
                for line in stdout:
                    try:
                        events.append(json.loads(line))
                    except ValueError:
                        pass
            completed = process.returncode == 0 and any(e.get("type") == "turn.completed" for e in events)
            records_path = scenario / "events.jsonl"
            records = [json.loads(line) for line in records_path.read_text().splitlines()] if records_path.exists() else []
            receipt_path = scenario / "injection-receipt.json"
            receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else None
            return {"completed": completed, "final": _last_agent_message(events), "records": records,
                    "receipt": receipt, "runtime": runtime, "elapsed_seconds": round(time.monotonic() - started, 3)}
        finally:
            hook_path.unlink(missing_ok=True)
            shutil.rmtree(home)


def verify_lifecycle(ledger, backend, check, report, root, plan, stop_file, *, host=None, judge=None):
    check("G6_lifecycle_requires_G5", report.get("g5", {}).get("status") == "passed")
    workspace = Path(root) / "g6/workspace"
    workspace.mkdir(parents=True)
    identity = ledger.register_project("g6-lifecycle-synthetic", workspace)
    context = TaskContext(owner_id=ledger.owner_id, **identity)
    if host is None:
        check("G6_lifecycle_pinned_host", subprocess.check_output(["codex", "--version"], text=True).strip() == HOST_VERSION)
        subprocess.run(["git", "init", "-q", str(workspace)], check=True, capture_output=True)
        host = LifecycleHost(plan, stop_file)
    if judge is None:
        config = Config.load()
        judge = build_adapter(config.llm_base_url, config.llm_api_key, plan["judge_model"], timeout=300)
    result = report["g6_lifecycle"] = {"status": "running", "plan_digest": plan["digest"],
        "mode": "real_host_real_judge" if isinstance(host, LifecycleHost) else "test_doubles",
        "judge_version": judge.version, "prompt_contract": plan.get("prompt_contract", "test-double"),
        "judge_model": plan.get("judge_model", "test-double"),
        "host_sessions": [], "chains": [], "judge_attempts": [],
        "normal_hooks_enabled": False, "private_data_imported": False}
    batches = BatchService(ledger, judge_version=judge.version)
    adapter = CodexShadowAdapter(ledger, host_version=HOST_VERSION, verified_events=set(EVENTS))
    projector = Projector(ledger)

    def session(name, prompt, hits, expected):
        ensure_running(stop_file)
        check("G6_host_call_budget", len(result["host_sessions"]) < 6)
        observed = host.run(name, prompt, ledger, context, hits)
        final = observed.pop("final")
        try:
            actual = json.loads(final) if expected != "ACK" else final.strip()
        except ValueError:
            actual = None
        observed.update(scenario=name, prompt=prompt, final_digest=digest(final), task_result_matches=actual == expected)
        # Persist only fixed expected results, never arbitrary host response text.
        if actual == expected:
            observed["verified_result"] = expected
        result["host_sessions"].append(observed)
        check("G6_" + name + "_completed", observed["completed"])
        names = {r["hook_event_name"] for r in observed["records"]}
        check("G6_" + name + "_four_events", names == set(EVENTS))
        receipt = observed["receipt"]
        fresh = RecallPolicy(ledger).assemble(hits, "g1", context, budget=plan["limits"]["bundle_budget"])
        check("G6_" + name + "_hook_output", receipt is not None and receipt.get("status") == "emitted"
              and receipt["bundle"] == fresh.model_dump(mode="json"))
        prior_sessions = [s["receipt"]["host_session_digest"] for s in result["host_sessions"][:-1]]
        check("G6_" + name + "_independent_session", receipt.get("host_session_digest")
              and receipt["host_session_digest"] not in prior_sessions)
        check("G6_" + name + "_task_result", actual == expected)
        sid = ledger.open_session("codex", name, context.workspace_id)
        source_events = []
        for record in observed["records"]:
            item = adapter.ingest(record, session_id=sid, workspace_id=context.workspace_id,
                                  synthetic_payload={"text": prompt} if record["hook_event_name"] == "UserPromptSubmit" else None)
            if record["hook_event_name"] == "UserPromptSubmit":
                source_events.append(item.event_id)
        check("G6_" + name + "_source_event", len(set(source_events)) == 1)
        return sid, source_events[0]

    def drain():
        for _ in range(20):
            ensure_running(stop_file)
            item = projector.drain_once(backend, worker_id="g6", now=datetime.now(timezone.utc),
                backend_timeout=timedelta(seconds=3), local_completion_margin=timedelta(seconds=2),
                lease_duration=timedelta(seconds=30), verification_timeout=timedelta(seconds=3))
            if item.status == "idle":
                return
            check("G6_projection_" + str(item.outbox_id), item.status in {"confirmed", "obsolete"})
        raise RuntimeError("g6_projection_bound")

    def maintain(name, prompt, operation, command, target=None):
        sid, event = session(name, prompt, [], "ACK")
        candidate = batches.create_candidate(CandidateInput(session_id=sid, kind="explicit_synthetic_decision",
            memory_type_hint="decision", scope_hint=Scope(kind="project", project_id=context.project_id),
            topic_key="verification-command", event_ids=(event,)))
        batch = batches.request_batch(sid, None, (target,) if target else ())
        for attempt in range(1, 4):
            ensure_running(stop_file)
            settled = BatchWorker(ledger).run_once(judge, worker_id="g6-judge", now=datetime.now(timezone.utc),
                lease_duration=timedelta(seconds=330), max_attempts=3)
            trace = asdict(judge.last_trace)
            trace.pop("error_detail", None)
            result["judge_attempts"].append({"batch_id": batch.batch_id, "attempt": attempt, "trace": trace,
                                             "result": settled.model_dump(mode="json")})
            if settled.status != "retry":
                break
        check("G6_" + name + "_settled", settled.status == "settled")
        row = ledger.db.execute("SELECT * FROM proposals WHERE batch_id=?", (batch.batch_id,)).fetchone()
        check("G6_" + name + "_proposal", row is not None and row["operation"] == operation
              and json.loads(row["payload_patch_json"]) == {"key": "verification-command", "command": command})
        memory = ledger.db.execute("SELECT re.memory_id,re.revision FROM revision_evidence re "
                                   "JOIN memories m ON m.memory_id=re.memory_id WHERE claim_key=? AND m.state='active'",
                                   (row["proposal_id"],)).fetchone()
        check("G6_" + name + "_memory", memory is not None)
        drain()
        started = time.monotonic()
        while True:
            ensure_running(stop_file)
            hits = [h for h in backend.search("verification-command", "g1", 50)
                    if h.memory_id == memory["memory_id"] and h.revision == memory["revision"]]
            if hits or time.monotonic() - started >= plan["limits"]["search_timeout_seconds"]:
                break
            time.sleep(0.2)
        check("G6_" + name + "_search", bool(hits))
        receipts = [dict(r) for r in ledger.db.execute(
            "SELECT operation_key,backend_locator,payload_digest FROM projection_receipts WHERE memory_id=? AND revision=? AND action='upsert'",
            (memory["memory_id"], memory["revision"]))]
        result["chains"].append({"kind": operation, "source_event_id": event, "candidate_id": candidate.candidate_id,
            "batch_id": batch.batch_id, "proposal_id": row["proposal_id"], **dict(memory),
            "input_digest": batches.batch_input(batch.batch_id).input_digest,
            "predecessor_memory_id": target, "projection_receipts": receipts})
        return memory["memory_id"], hits

    session("control", QUERY, [], {"command": None})
    memory, old_hits = maintain("source", SOURCE, "new", "pytest")
    session("consume-new", QUERY, old_hits, {"command": "pytest"})
    updated, new_hits = maintain("update", UPDATE, "supersede", "python -m unittest", memory)
    check("G6_successor_replaces_retired_memory", updated != memory and new_hits[0].revision == 1
          and ledger.db.execute("SELECT state FROM memories WHERE memory_id=?", (memory,)).fetchone()[0] == "retired")
    mixed = [*old_hits, *new_hits]
    bundle = RecallPolicy(ledger).assemble(mixed, "g1", context)
    check("G6_old_revision_denied", len(bundle.items) == 1 and any(o.reason == "inactive" for o in bundle.omissions))
    session("consume-update", QUERY, mixed, {"command": "python -m unittest"})
    ensure_running(stop_file)
    delete_receipt = plan["run_id"] + "-forget"
    deleted = ledger.forget(updated, delete_receipt)
    check("G6_forget_requested", deleted["status"] in {"remote_pending", "local_redacted"})
    # Exercise a stale backend/cache before physical deletion completes.
    bundle = RecallPolicy(ledger).assemble(mixed, "g1", context)
    check("G6_deleted_stale_hits_denied", not bundle.text and all(o.reason == "inactive" for o in bundle.omissions))
    session("consume-delete", QUERY, mixed, {"command": None})
    drain()
    locators = [h.backend_locator for h in mixed]
    check("G6_deleted_projections_absent", all(backend.inspect_projection(p) is None for p in locators))
    reopened = Ledger(Path(root) / "ledger.db", owner_id=ledger.owner_id)
    try:
        recovered = RecallPolicy(reopened).assemble(mixed, "g1", context)
        check("G6_deleted_after_reopen", not recovered.text and not recovered.items)
    finally:
        reopened.close()
    result["chains"].append({"kind": "forget", "memory_id": updated,
                             "delete_receipt": delete_receipt, "delete_result": deleted,
                             "stale_decision": bundle.model_dump(mode="json"), "reopened_decision": recovered.model_dump(mode="json")})
    result["status"] = "passed"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    plan = plan_body("g6-" + uuid.uuid4().hex)
    plan["digest"] = digest(plan)
    path = args.output / "plan.json"
    path.write_text(json.dumps(plan, indent=2) + "\n")
    load_plan(path)
    print(json.dumps({"status": "prepared_not_authorized", "plan": str(path.resolve()), "digest": plan["digest"],
                      "workspace": plan["workspace"], "kill_switch": str((args.output / "STOP").resolve())}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
