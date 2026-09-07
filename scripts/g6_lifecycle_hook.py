"""Fresh Ledger read at SessionStart, restricted to the approved G6 workspace."""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

from g3_hook_recorder import _append_receipt
from sagacontext.backends import BackendHit
from sagacontext.ledger import Ledger, TaskContext
from sagacontext.recall_policy import RecallPolicy

EVENTS = {"SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"}


def handle(request, payload, root):
    workspace = Path(request["workspace"]).resolve()
    if Path(request["stop_file"]).exists():
        return {}, {"status": "blocked_kill_switch"}
    if payload.get("hook_event_name") not in EVENTS or not payload.get("cwd") or Path(payload["cwd"]).resolve() != workspace:
        return {}, {"status": "blocked_scope"}
    if not isinstance(payload.get("session_id"), str) or not payload["session_id"]:
        return {}, {"status": "blocked_session_identity"}
    _append_receipt(log=root / "events.jsonl", state_path=root / "state.json", payload=payload,
                    probe_id=request["run_id"], scenario=request["scenario"], started_ns=time.monotonic_ns(),
                    behavior="normal", handler_ref="g6-lifecycle")
    if payload["hook_event_name"] != "SessionStart":
        return {}, None
    if not Path(request["ledger"]).is_file():
        return {}, {"status": "blocked_missing_ledger"}
    context = TaskContext.model_validate(request["context"])
    ledger = Ledger(Path(request["ledger"]), owner_id=context.owner_id)
    try:
        identity = ledger.resolve_project(workspace)
        if identity is None or identity["workspace_id"] != context.workspace_id or identity["project_id"] != context.project_id:
            return {}, {"status": "blocked_identity"}
        bundle = RecallPolicy(ledger).assemble([BackendHit.model_validate(h) for h in request["hits"]],
                                               "g1", context, budget=request["budget"])
    finally:
        ledger.close()
    output = {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": bundle.text}}
    return output, {"status": "emitted", "event": "SessionStart", "bundle": bundle.model_dump(mode="json"),
                    "host_session_digest": hashlib.sha256(payload["session_id"].encode()).hexdigest()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True, type=Path)
    args = parser.parse_args()
    try:
        output, receipt = handle(json.loads(args.request.read_text()), json.load(sys.stdin), args.request.parent)
    except Exception as error:
        output, receipt = {}, {"status": "blocked_error", "error_class": type(error).__name__}
    if receipt is not None:
        (args.request.parent / "injection-receipt.json").write_text(json.dumps(receipt))
    print(json.dumps(output))


if __name__ == "__main__":
    main()
