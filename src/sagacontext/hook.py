"""Workspace-scoped stdin adapter. Never reads transcript files."""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

import httpx

from .config import Config

# Background meta sessions (skill-evolution reviewer) and explicit opt-outs never sample.
EXCLUDE_ENV = ("SKILL_EVOLUTION_REVIEW", "SAGACONTEXT_HOOK_DISABLE")


def excluded(environ=os.environ) -> bool:
    return any(environ.get(name) == "1" for name in EXCLUDE_ENV)


def prepare(payload: dict, config: Config, host: str, event: str) -> dict | None:
    if event not in {"SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"}:
        return None
    if not payload.get("session_id") or not payload.get("cwd"):
        return None
    if str(Path(payload["cwd"]).resolve()) not in {str(Path(w).resolve()) for w in config.rollout_workspaces}:
        return None
    # One ID per invocation; HTTP retries reuse this exact prepared object.
    # Equal prompts in different turns must not collapse into one event.
    record = {"session_id": payload["session_id"], "cwd": payload["cwd"],
              "host": host, "hook_event_name": event,
              "source_event_ref": payload.get("event_id") or str(uuid.uuid4())}
    if event == "UserPromptSubmit":
        record["prompt"] = payload.get("prompt", "")
    if event == "SessionStart" and isinstance(payload.get("source"), str):
        record["source"] = payload["source"]
    return record


def main() -> None:
    output = {}
    try:
        host, event = sys.argv[1:3]
        if excluded():
            print(json.dumps(output))
            return
        config = Config.load()
        record = prepare(json.load(sys.stdin), config, host, event)
        if record is not None:
            with httpx.Client(trust_env=False, timeout=7) as client:
                response = client.post(f"http://{config.host}:{config.port}/events",
                    params={"host": host, "event": event}, json=record,
                    headers={"X-SagaContext-Host-Version": config.host_versions().get(host, ""),
                             "X-SagaContext-Generation": config.rollout_generation})
                if response.status_code == 200 and event == "SessionStart":
                    result = response.json()
                    if isinstance(result, dict) and "hookSpecificOutput" in result:
                        output = {"hookSpecificOutput": result["hookSpecificOutput"]}
    except (OSError, ValueError, TypeError, httpx.HTTPError):
        pass  # Host continues; unsuccessful delivery is never a consumption receipt.
    print(json.dumps(output))


if __name__ == "__main__":
    main()
