#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


SESSION_START_CONTEXT = "G3_SESSION_START_CONTEXT"
SAFE_ENUMS = {
    "hook_event_name": {
        "PreToolUse",
        "PermissionRequest",
        "PostToolUse",
        "PreCompact",
        "PostCompact",
        "SessionStart",
        "SessionEnd",
        "UserPromptSubmit",
        "SubagentStart",
        "SubagentStop",
        "Stop",
        "Interrupt",
    },
    "permission_mode": {"default", "read-only", "workspace-write", "danger-full-access"},
    "source": {"startup", "resume", "clear", "compact"},
    "trigger": {"manual", "auto"},
    "tool_name": {"shell", "Bash", "apply_patch"},
}
REFERENCE_FIELDS = {
    "session": ("session_id",),
    "workspace": ("cwd", "workspace"),
    "task": ("turn_id", "task_id", "thread_id"),
    "tool_call": ("tool_use_id", "tool_call_id", "call_id"),
}


def _first_string(payload: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _shape_entries(value: Any, prefix: str = "$") -> list[str]:
    if isinstance(value, dict):
        entries = [f"{prefix}:object"]
        for key in sorted(value):
            entries.extend(_shape_entries(value[key], f"{prefix}.{key}"))
        return entries
    if isinstance(value, list):
        entries = [f"{prefix}:array"]
        for item in value:
            entries.extend(_shape_entries(item, f"{prefix}[]"))
        return sorted(set(entries))
    if value is None:
        kind = "null"
    elif isinstance(value, bool):
        kind = "boolean"
    elif isinstance(value, (int, float)):
        kind = "number"
    elif isinstance(value, str):
        kind = "string"
    else:
        kind = type(value).__name__
    return [f"{prefix}:{kind}"]


def _safe_enums(payload: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, allowed in SAFE_ENUMS.items():
        value = payload.get(key)
        if isinstance(value, str):
            result[key] = value if value in allowed else "other"
    return result


def _alias(state: dict[str, Any], kind: str, raw: str | None) -> str | None:
    if raw is None:
        return None
    mappings = state.setdefault("reference_mappings", {}).setdefault(kind, {})
    if raw not in mappings:
        mappings[raw] = f"{kind}-{len(mappings) + 1}"
    return str(mappings[raw])


def _append_receipt(
    *,
    log: Path,
    state_path: Path,
    payload: dict[str, Any],
    probe_id: str,
    scenario: str,
    started_ns: int,
    behavior: str,
    handler_ref: str,
) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_fd = os.open(state_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        with os.fdopen(state_fd, "r+", encoding="utf-8", closefd=False) as state_file:
            fcntl.flock(state_file, fcntl.LOCK_EX)
            raw_state = state_file.read()
            state = json.loads(raw_state) if raw_state.strip() else {"next_occurrence": 1}
            refs = {
                kind: _alias(state, kind, _first_string(payload, keys))
                for kind, keys in REFERENCE_FIELDS.items()
            }
            event = str(payload.get("hook_event_name") or "unknown")
            source_identity = json.dumps(
                {
                    "event": event,
                    "session": _first_string(payload, REFERENCE_FIELDS["session"]),
                    "task": _first_string(payload, REFERENCE_FIELDS["task"]),
                    "tool_call": _first_string(payload, REFERENCE_FIELDS["tool_call"]),
                },
                sort_keys=True,
            )
            source_ref = _alias(state, "source-event", source_identity)
            duplicate_ref = _alias(state, "duplicate-group", source_identity)
            occurrence_no = int(state.get("next_occurrence", 1))
            state["next_occurrence"] = occurrence_no + 1
            enums = _safe_enums(payload)
            digest_input = {
                "shape": _shape_entries(payload),
                "refs": refs,
                "safe_enums": enums,
            }
            receipt = {
                "probe_id": probe_id,
                "scenario": scenario,
                "occurrence_no": occurrence_no,
                "hook_event_name": event,
                "monotonic_offset_ms": round((time.monotonic_ns() - started_ns) / 1_000_000, 3),
                "payload_shape_digest": "sha256:"
                + hashlib.sha256(
                    json.dumps(digest_input, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
                "session_ref": refs["session"],
                "workspace_ref": refs["workspace"],
                "task_ref": refs["task"],
                "tool_call_ref": refs["tool_call"],
                "source_event_ref": source_ref,
                "duplicate_group_ref": duplicate_ref,
                "exit_class": "scheduled_nonzero_7" if behavior == "exit" else "normal",
                "timeout_class": "configured_timeout_target" if behavior == "sleep" else "none",
                "payload_keys": sorted(payload),
                "safe_enums": enums,
                "handler_ref": handler_ref,
                "receipt_status": "written_before_handler_action",
                "has_transcript_path": payload.get("transcript_path") is not None,
            }
            state_file.seek(0)
            state_file.truncate()
            state_file.write(json.dumps(state, sort_keys=True))
            state_file.flush()
            os.fsync(state_file.fileno())
            log.parent.mkdir(parents=True, exist_ok=True)
            log_fd = os.open(log, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
            try:
                os.write(log_fd, (json.dumps(receipt, sort_keys=True) + "\n").encode())
                os.fsync(log_fd)
            finally:
                os.close(log_fd)
    finally:
        os.close(state_fd)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--probe-id", required=True)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--started-ns", type=int, required=True)
    parser.add_argument("--behavior", choices=("normal", "exit", "sleep"), default="normal")
    parser.add_argument("--handler-ref", default="primary")
    parser.add_argument("--sleep", type=float, default=0)
    args = parser.parse_args()
    payload = json.load(sys.stdin)
    _append_receipt(
        log=args.log,
        state_path=args.state,
        payload=payload,
        probe_id=args.probe_id,
        scenario=args.scenario,
        started_ns=args.started_ns,
        behavior=args.behavior,
        handler_ref=args.handler_ref,
    )
    if args.behavior == "exit":
        return 7
    if args.behavior == "sleep":
        time.sleep(args.sleep)
    event = payload.get("hook_event_name")
    if event == "SessionStart":
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": event,
                        "additionalContext": SESSION_START_CONTEXT,
                    }
                }
            )
        )
    else:
        print("{}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
