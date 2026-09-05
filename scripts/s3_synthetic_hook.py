#!/usr/bin/env python3
"""Hook used only by the isolated, synthetic S3 acceptance runner."""
import argparse
import json
import sys
from pathlib import Path

from g3_hook_recorder import _append_receipt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--started-ns", type=int, required=True)
    args = parser.parse_args()
    payload = json.load(sys.stdin)
    _append_receipt(log=args.root / "events.jsonl", state_path=args.root / "state.json",
        payload=payload, probe_id=args.run_id, scenario=args.scenario, started_ns=args.started_ns,
        behavior="normal", handler_ref="primary")
    output = {}
    bundle_path = args.root / "bundle.json"
    if payload.get("hook_event_name") == "SessionStart" and bundle_path.exists():
        text = json.loads(bundle_path.read_text())["text"]
        output = {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}
        (args.root / "injection-receipt.json").write_text(json.dumps({"text": text, "event": "SessionStart"}))
    print(json.dumps(output))


if __name__ == "__main__":
    main()
