"""Workspace-scoped stdin adapter. Never reads transcript files."""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import httpx

from .config import Config


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
    return record


def main() -> None:
    output = {}
    try:
        host, event = sys.argv[1:3]
        config = Config.load()
        record = prepare(json.load(sys.stdin), config, host, event)
        if record is not None:
            with httpx.Client(trust_env=False, timeout=7) as client:
                response = client.post(f"http://{config.host}:{config.port}/events",
                    params={"host": host, "event": event}, json=record,
                    headers={"X-SagaContext-Host-Version": config.rollout_host_version,
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
