"""S3-2/3/4 assertions shared by the explicit real-backend runner."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from evaluate_codex_host_probe import evaluate
from probe_openviking_backend import _digest
from sagacontext.backends import BackendHit
from sagacontext.hosts.codex_shadow import CodexShadowAdapter, EVENTS
from sagacontext.ledger import CommitRequest, Scope, TaskContext
from sagacontext.maintenance import BatchService, BatchWorker, CandidateInput, DeltaProposal, ScriptedJudge
from sagacontext.projection import Projector
from sagacontext.recall_policy import RecallPolicy


def verify_policy_stages(ledger, backend, check, report, root):
    now = lambda: datetime.now(timezone.utc)
    projector = Projector(ledger)
    policy = RecallPolicy(ledger)
    identity = ledger.register_project("s3-synthetic", Path(root))
    context = TaskContext(owner_id=ledger.owner_id, project_id=identity["project_id"], workspace_id=identity["workspace_id"])
    scope = Scope(kind="project", project_id=context.project_id)

    def drain():
        return projector.drain_once(backend, worker_id="s3-policy", now=now(),
            backend_timeout=timedelta(seconds=3), local_completion_margin=timedelta(seconds=2),
            lease_duration=timedelta(seconds=30), verification_timeout=timedelta(seconds=3))

    def hit(memory_id, revision=1):
        locator = backend.locate_projection(memory_id, revision, "g1")
        return BackendHit(memory_id=memory_id, revision=revision, generation="g1", rank=1, backend_locator=locator or "absent")

    capture_path = Path("artifacts/probes/g3-20260905-terra-final/g3-codex-host-capture.json")
    capture = json.loads(capture_path.read_text())
    evaluation = evaluate(capture, _digest(capture_path.read_text()))
    check("S3_3_G3_admission_rechecked", evaluation["status"] == "passed")
    shadow = CodexShadowAdapter(ledger, host_version=capture["executable_version"], verified_events=set(EVENTS))
    session = ledger.open_session("codex", "s3-shadow-replay", identity["workspace_id"])
    receipts = []
    event_id = None
    for record in capture["payload_shapes"]:
        if record["scenario"] != "baseline_with_duplicate":
            continue
        receipt = shadow.ingest(record, session_id=session, workspace_id=identity["workspace_id"])
        receipts.append(receipt.model_dump())
        if record["hook_event_name"] == "UserPromptSubmit":
            event_id = receipt.event_id
    check("S3_3_event_replay_deduplicates", any(item["status"] == "duplicate" for item in receipts))
    # G3 receipts contain no text. This declared fixture tests the maintenance route,
    # not extraction of semantics from a redacted source event.
    payload = {"decision": "Synthetic shadow fixture: tests use unittest."}
    batches = BatchService(ledger)
    candidate = batches.create_candidate(CandidateInput(session_id=session, kind="explicit_fixture",
        memory_type_hint="decision", scope_hint=scope, topic_key="s3-shadow", event_ids=(event_id,)))
    batch = batches.request_batch(session, None)
    proposal = DeltaProposal(candidate_id=candidate.candidate_id, operation="new", memory_type="decision",
        scope=scope, payload=payload, evidence_ids=(event_id,), rationale="explicit synthetic fixture attached to G3 receipt")
    result = BatchWorker(ledger).run_once(ScriptedJudge(proposals=(proposal,)), worker_id="shadow",
        now=now(), lease_duration=timedelta(seconds=30))
    check("S3_3_batch_settled", result.status == "settled")
    check("S3_3_real_projection", drain().status == "confirmed")
    row = ledger.db.execute("SELECT re.memory_id FROM revision_evidence re JOIN evidence e "
                            "ON e.evidence_id=re.evidence_id WHERE e.source_event_id=?", (event_id,)).fetchone()
    memory_id = row["memory_id"]
    bundle = policy.assemble([hit(memory_id)], "g1", context, budget=2000)
    check("S3_2_authoritative_bundle", len(bundle.items) == 1 and bundle.items[0].payload == payload and bundle.used <= bundle.budget)
    wrong = policy.assemble([hit(memory_id)], "g1", TaskContext(owner_id="other"))
    check("S3_2_owner_omission", not wrong.text and wrong.omissions[0].reason == "owner")
    report["shadow"] = {"mode": "redacted_runtime_receipt_replay", "capture_digest": _digest(capture_path.read_text()),
        "receipts": receipts, "candidate_id": candidate.candidate_id, "batch_id": batch.batch_id,
        "proposal": proposal.model_dump(mode="json"), "memory_id": memory_id,
        "would_inject": bundle.model_dump(mode="json"), "actual_context_modified": False}

    traces = []
    for kind in ("delete", "supersede"):
        request = CommitRequest(receipt="g5-" + kind, operation="new", memory_type="decision", scope=scope,
                                payload={"decision": "Synthetic G5 " + kind})
        created = ledger.commit(request)
        old = projector.claim_next(backend, worker_id="in-flight", now=now(),
            backend_timeout=timedelta(seconds=3), local_completion_margin=timedelta(seconds=2), lease_duration=timedelta(seconds=30))
        locator = projector.call_backend(old, backend, now=now())
        cached_hit = BackendHit(memory_id=created.memory_id, revision=1, generation="g1", rank=1, backend_locator=locator)
        if kind == "delete":
            ledger.forget(created.memory_id, "g5-forget")
        else:
            ledger.commit(CommitRequest(receipt="g5-retire", operation="supersede", memory_id=created.memory_id,
                expected_revision=1, memory_type="decision", scope=scope, payload={"decision": "replaced"}))
        stale = policy.assemble([cached_hit], "g1", context)
        check("G5_" + kind + "_stale_index_denied", not stale.text and stale.omissions[0].reason == "inactive")
        check("G5_" + kind + "_early_cleanup", drain().status == "confirmed" and backend.inspect_projection(locator) is None)
        # An already issued request arrives after cleanup. The worker must enqueue
        # cleanup again even if a prior delete receipt already exists.
        backend.materialize(old.projection, old.operation_key)
        late = projector.complete(old, backend, locator, now=now())
        check("G5_" + kind + "_late_write_obsolete", late.status == "obsolete")
        check("G5_" + kind + "_late_cleanup", drain().status == "confirmed" and backend.inspect_projection(locator) is None)
        replay = ledger.commit(request.model_copy(update={"receipt": request.receipt + "-rescan"}))
        check("G5_" + kind + "_rescan_suppressed", replay.status == "rejected" and replay.reason == "suppressed_after_deletion")
        check("G5_" + kind + "_fresh_policy_denies", not RecallPolicy(ledger).assemble([cached_hit], "g1", context).text)
        traces.append({"kind": kind, "memory_id": created.memory_id,
            "stale_decision": stale.model_dump(mode="json"), "late_result": late.model_dump(),
            "rescan_result": replay.model_dump(),
            "outbox": [dict(row) for row in ledger.db.execute(
                "SELECT outbox_id,revision,action,status,target_locator FROM outbox WHERE memory_id=?", (created.memory_id,))]})
    report["g5"] = {"status": "passed", "stale_index_mode": "cached real backend identity replay", "traces": traces}
