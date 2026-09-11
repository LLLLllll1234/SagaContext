"""Three actual synthetic CLI next-session consumption chains after G5."""
import json
import os
import shlex
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from probe_codex_host import _prepare_isolated_codex_home, _last_agent_message
from sagacontext.hosts.codex_shadow import CodexShadowAdapter, EVENTS
from sagacontext.ledger import Ledger, Scope, TaskContext
from sagacontext.maintenance import BatchService, BatchWorker, CandidateInput, DeltaProposal, ScriptedJudge
from sagacontext.projection import Projector
from sagacontext.recall_policy import RecallPolicy


def verify_longitudinal(ledger, backend, check, report, root):
    check("G6_requires_G5", report.get("g5", {}).get("status") == "passed")
    version = subprocess.check_output(["codex", "--version"], text=True).strip()
    check("G6_pinned_host", version == "codex-cli 0.153.4")
    root = Path(root) / "g6"
    root.mkdir()
    workspace = root / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q", str(workspace)], check=True, capture_output=True)
    identity = ledger.register_project("g6-synthetic", workspace)
    task = ledger.create_task(identity["project_id"], "Finish synthetic checkpoint")
    context = TaskContext(owner_id=ledger.owner_id, project_id=identity["project_id"],
                          workspace_id=identity["workspace_id"], task_id=task)
    home = root / "codex-home"
    runtime = _prepare_isolated_codex_home(home, "gpt-5.6-terra", workspace)
    fixture = {
        "preference": {"type": "convention", "scope": Scope(kind="global"),
            "payload": {"rule": "For arithmetic questions, reply only with JSON containing numeric answer and unit; use unit=items."},
            "prompt": "There are 8 items and another 7 items arrive. How many are there now?",
            "expected": {"answer": 15, "unit": "items"}},
        "project": {"type": "decision", "scope": Scope(kind="project", project_id=context.project_id),
            "payload": {"decision": "Synthetic project NOVA uses 7 slots per rack, with 2 reserved slots per rack. Capacity counts usable slots only."},
            "prompt": "For project NOVA, compute the usable capacity of 6 racks. Reply only JSON with keys capacity and unit (slots).",
            "expected": {"capacity": 30, "unit": "slots"}},
        "checkpoint": {"type": "task_checkpoint", "scope": Scope(kind="task", project_id=context.project_id, task_id=task),
            "payload": {"goal": "Finish the synthetic aggregation and write checkpoint_result.json",
                "done": ["Validated the first batch; subtotal is 17"], "open": ["Add pending amounts 4, 9, 6"],
                "next": "Compute the final sum and write checkpoint_result.json with total and status=complete",
                "touched_paths": ["checkpoint_result.json"], "outcome": "in_progress"},
            "prompt": "Resume the current task from its saved checkpoint and finish the next step. Reply only JSON with total and status.",
            "expected": {"total": 36, "status": "complete"}},
    }
    source_prompt = "Remember these explicit synthetic facts for later sessions. Acknowledge only with ACK.\n" + json.dumps(
        {key: item["payload"] for key, item in fixture.items()}, sort_keys=True)
    hook = Path(__file__).with_name("s3_synthetic_hook.py").resolve()

    def run(name, prompt, bundle=None):
        scenario = root / name
        scenario.mkdir()
        if bundle is not None:
            (scenario / "bundle.json").write_text(bundle.model_dump_json())
        command = shlex.join([sys.executable, str(hook), "--root", str(scenario), "--run-id", report["run_id"],
                              "--scenario", name, "--started-ns", str(time.monotonic_ns())])
        hooks = {"hooks": {event: [{"hooks": [{"type": "command", "command": command, "timeout": 10}]}] for event in EVENTS}}
        config_dir = workspace / ".codex"
        config_dir.mkdir(exist_ok=True)
        (config_dir / "hooks.json").write_text(json.dumps(hooks))
        env = os.environ.copy()
        env["CODEX_HOME"] = str(home)
        started = time.monotonic()
        completed = subprocess.run(["codex", "--dangerously-bypass-hook-trust", "--ask-for-approval", "never",
            "--sandbox", "workspace-write", "--cd", str(workspace), "--model", "gpt-5.6-terra",
            "exec", "--json", "--ephemeral", "--ignore-rules", prompt],
            text=True, capture_output=True, timeout=180, env=env)
        events = []
        for line in completed.stdout.splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                pass
        records = [json.loads(line) for line in (scenario / "events.jsonl").read_text().splitlines()]
        final = _last_agent_message(events)
        check("G6_" + name + "_host_completed", completed.returncode == 0 and any(e.get("type") == "turn.completed" for e in events))
        result = {"scenario": name, "prompt": prompt, "final": final, "events": records,
                  "elapsed_seconds": round(time.monotonic() - started, 3), "exit_code": completed.returncode}
        if bundle is not None:
            receipt = json.loads((scenario / "injection-receipt.json").read_text())
            check("G6_" + name + "_actual_input_matches_bundle", receipt["text"] == bundle.text)
            result["actual_next_session_input"] = receipt
        return result

    source = run("source", source_prompt)
    report["g6"] = {"status": "running", "host_version": version, "model": "gpt-5.6-terra",
                    "runtime": runtime, "source": source, "chains": []}
    session = ledger.open_session("codex", "g6-source", context.workspace_id)
    ledger.bind_task(session, task, "g6-bind")
    adapter = CodexShadowAdapter(ledger, host_version=version, verified_events=set(EVENTS))
    source_event = None
    for record in source["events"]:
        receipt = adapter.ingest(record, session_id=session, workspace_id=context.workspace_id,
            synthetic_payload={"text": source_prompt} if record["hook_event_name"] == "UserPromptSubmit" else None)
        if record["hook_event_name"] == "UserPromptSubmit":
            source_event = receipt.event_id
    check("G6_source_event_observed", source_event is not None)
    batches = BatchService(ledger)
    projector = Projector(ledger)
    for name, item in fixture.items():
        candidate = batches.create_candidate(CandidateInput(session_id=session, task_id=task, kind="explicit_user_fixture",
            memory_type_hint=item["type"], scope_hint=item["scope"], topic_key="g6-" + name, event_ids=(source_event,)))
        batch = batches.request_batch(session, task)
        proposal = DeltaProposal(candidate_id=candidate.candidate_id, operation="new", memory_type=item["type"],
            scope=item["scope"], payload=item["payload"], evidence_ids=(source_event,), rationale="explicit synthetic source prompt")
        settled = BatchWorker(ledger).run_once(ScriptedJudge(proposals=(proposal,)), worker_id="g6", now=datetime.now(timezone.utc), lease_duration=timedelta(seconds=30))
        check("G6_" + name + "_settled", settled.status == "settled")
        result = projector.drain_once(backend, worker_id="g6", now=datetime.now(timezone.utc),
            backend_timeout=timedelta(seconds=3), local_completion_margin=timedelta(seconds=2),
            lease_duration=timedelta(seconds=30), verification_timeout=timedelta(seconds=3))
        check("G6_" + name + "_projected", result.status == "confirmed")
        proposal_row = ledger.db.execute("SELECT proposal_id FROM proposals WHERE batch_id=?", (batch.batch_id,)).fetchone()
        memory = ledger.db.execute("SELECT memory_id FROM revision_evidence WHERE claim_key=?", (proposal_row["proposal_id"],)).fetchone()[0]
        started = time.monotonic()
        hits = []
        while time.monotonic() - started < 60:
            hits = [hit for hit in backend.search(json.dumps(item["payload"]), "g1", 50) if hit.memory_id == memory]
            if hits:
                break
            time.sleep(0.5)
        bundle = RecallPolicy(ledger).assemble(hits, "g1", context, budget=3500)
        check("G6_" + name + "_recall", len(bundle.items) == 1)
        consumed = run(name, item["prompt"], bundle)
        try:
            actual = json.loads(consumed["final"])
        except ValueError:
            actual = None
        check("G6_" + name + "_task_result", actual == item["expected"], {"expected": item["expected"], "actual": actual})
        if name == "checkpoint":
            artifact = workspace / "checkpoint_result.json"
            check("G6_checkpoint_file_result", artifact.exists() and json.loads(artifact.read_text()) == item["expected"])
            consumed["task_file"] = json.loads(artifact.read_text())
        receipt = ledger.db.execute("SELECT operation_key,backend_locator,payload_digest FROM projection_receipts WHERE memory_id=? AND action='upsert'", (memory,)).fetchone()
        report["g6"]["chains"].append({"kind": name, "source_event_id": source_event,
            "candidate_id": candidate.candidate_id, "batch_id": batch.batch_id, "proposal_id": proposal_row["proposal_id"],
            "memory_id": memory, "revision": 1, "projection_receipt": dict(receipt),
            "recall_decision": bundle.model_dump(mode="json"), "consumption": consumed})
        ledger.forget(memory, "g6-cleanup-" + name)
        cleanup = projector.drain_once(backend, worker_id="g6-cleanup", now=datetime.now(timezone.utc),
            backend_timeout=timedelta(seconds=3), local_completion_margin=timedelta(seconds=2),
            lease_duration=timedelta(seconds=30), verification_timeout=timedelta(seconds=3))
        check("G6_" + name + "_cleanup", cleanup.status == "confirmed" and backend.inspect_projection(receipt["backend_locator"]) is None)
        reopened = Ledger(Path(root).parent / "ledger.db", owner_id=ledger.owner_id)
        try:
            rejected = RecallPolicy(reopened).assemble(hits, "g1", context)
            check("G6_" + name + "_reopen_no_reactivation", not rejected.text)
        finally:
            reopened.close()
        report["g6"]["chains"][-1]["cleanup_recovery"] = {"projection": cleanup.model_dump(), "reopened_decision": rejected.model_dump(mode="json")}
    report["g6"]["status"] = "passed"
