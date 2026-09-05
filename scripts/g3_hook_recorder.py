#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


SESSION_START_CONTEXT = "G3_SESSION_START_CONTEXT"


def _shape(payload: dict) -> dict:
    shaped: dict[str, object] = {
        "hook_event_name": payload.get("hook_event_name"),
        "payload_keys": sorted(payload),
    }
    for key in ("session_id", "turn_id", "agent_id", "transcript_path", "cwd", "model"):
        if key in payload:
            shaped[f"has_{key}"] = payload[key] is not None
    for key in ("permission_mode", "source", "trigger", "tool_name"):
        value = payload.get(key)
        if isinstance(value, str):
            shaped[key] = value
    for key in ("tool_input", "tool_response"):
        value = payload.get(key)
        if isinstance(value, dict):
            shaped[f"{key}_keys"] = sorted(value)
    return shaped


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--sleep", type=float, default=0)
    args = parser.parse_args()
    payload = json.load(sys.stdin)
    args.log.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(_shape(payload), sort_keys=True, ensure_ascii=True)
    fd = os.open(args.log, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(fd, (line + "\n").encode())
    finally:
        os.close(fd)
    if args.sleep:
        time.sleep(args.sleep)
    event = payload.get("hook_event_name")
    if event == "SessionStart":
        print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": SESSION_START_CONTEXT}}))
    else:
        print("{}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
