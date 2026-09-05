#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import subprocess
import tempfile
from datetime import date
from pathlib import Path


SOURCE_DECLARED_EVENTS = (
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
)
EVENTS = SOURCE_DECLARED_EVENTS
SESSION_START_CONTEXT = "G3_SESSION_START_CONTEXT"
SOURCE_REVISION = "728cb12fe5794b0c3a8e776fb4994b1650b973a8"
OFFICIAL_REFERENCES = (
    "https://developers.openai.com/codex/hooks",
    "https://developers.openai.com/codex/config-file/config-reference",
    "https://developers.openai.com/codex/cli/reference",
)


def _hooks_config(recorder: Path, log: Path) -> dict:
    command = f"python3 {shlex.quote(str(recorder))} --log {shlex.quote(str(log))}"
    hooks = {}
    for event in EVENTS:
        handler: dict[str, object] = {"type": "command", "command": command, "timeout": 10}
        if event == "SessionEnd":
            handler["timeout"] = 1
        hooks[event] = [{"hooks": [handler]}]
    return {"description": "SagaContext G3 synthetic capability probe", "hooks": hooks}


def _config_fingerprint(config: dict) -> str:
    normalized = json.loads(json.dumps(config))
    for groups in normalized["hooks"].values():
        for group in groups:
            for handler in group["hooks"]:
                handler["command"] = "python3 <recorder> --log <hook_log>"
    digest = hashlib.sha256(json.dumps(normalized, sort_keys=True).encode()).hexdigest()
    return f"sha256:{digest}"


def _runtime_feature_flags() -> dict[str, bool]:
    completed = subprocess.run(["codex", "features", "list"], check=True, text=True, capture_output=True)
    flags: dict[str, bool] = {}
    for line in completed.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[-1] in {"true", "false"}:
            flags[fields[0]] = fields[-1] == "true"
    return {"hooks": flags.get("hooks", False)}


def _read_records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _last_agent_message(events: list[dict]) -> str:
    for event in reversed(events):
        if event.get("type") == "item.completed":
            item = event.get("item") or {}
            if item.get("type") == "agent_message":
                return str(item.get("text", ""))
    return ""


def _classify_blocker(stderr: str, timed_out: bool) -> str:
    normalized = stderr.lower()
    if "401 unauthorized" in normalized or "invalid_api_key" in normalized:
        return "model_authentication_failed"
    if timed_out:
        return "model_request_timed_out"
    if "hook" in normalized and ("invalid" in normalized or "malformed" in normalized):
        return "hook_configuration_rejected"
    return "codex_exec_failed"


def run_probe(output: Path, timeout_seconds: int = 45) -> dict:
    recorder = Path(__file__).with_name("g3_hook_recorder.py").resolve()
    version = subprocess.run(["codex", "--version"], check=True, text=True, capture_output=True).stdout.strip()
    with tempfile.TemporaryDirectory(prefix="sagacontext-g3-") as directory:
        root = Path(directory)
        (root / ".codex").mkdir()
        (root / "synthetic.txt").write_text("synthetic probe only\n")
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        hook_log = root / "hook-events.jsonl"
        config = _hooks_config(recorder, hook_log)
        (root / ".codex" / "hooks.json").write_text(json.dumps(config, indent=2) + "\n")
        command = [
            "codex",
            "--dangerously-bypass-hook-trust",
            "--ask-for-approval",
            "never",
            "--sandbox",
            "read-only",
            "--cd",
            str(root),
            "--model",
            "gpt-5.6-luna",
            "exec",
            "--json",
            "--ephemeral",
            "--ignore-user-config",
            "Use a shell command to read synthetic.txt, then reply with the exact synthetic marker supplied by lifecycle context.",
        ]
        timed_out = False
        try:
            completed = subprocess.run(command, text=True, capture_output=True, timeout=timeout_seconds)
            stdout = completed.stdout
            stderr = completed.stderr
            exit_code = completed.returncode
        except subprocess.TimeoutExpired as error:
            timed_out = True
            stdout = error.stdout.decode(errors="replace") if isinstance(error.stdout, bytes) else (error.stdout or "")
            stderr = error.stderr.decode(errors="replace") if isinstance(error.stderr, bytes) else (error.stderr or "")
            exit_code = None
        cli_events = [json.loads(line) for line in stdout.splitlines() if line.startswith("{")]
        records = _read_records(hook_log)

    observed = {record.get("hook_event_name") for record in records}
    event_capabilities = [
        {
            "event": event,
            "status": "observed" if event in observed else "not_observed",
            "payload_keys": next(
                (record["payload_keys"] for record in records if record.get("hook_event_name") == event), []
            ),
        }
        for event in EVENTS
    ]
    blocker = None if exit_code == 0 else _classify_blocker(stderr, timed_out)
    status = "passed" if exit_code == 0 else (
        "blocked" if blocker in {"model_authentication_failed", "model_request_timed_out"} else "failed"
    )
    result = {
        "host_name": "codex",
        "host_form": "cli-exec",
        "executable_version": version,
        "adapter_version": "g3-probe-v1",
        "config_fingerprint": _config_fingerprint(config),
        "verified_events": event_capabilities,
        "injection_modes": ["SessionStart.additionalContext"]
        if SESSION_START_CONTEXT in _last_agent_message(cli_events)
        else [],
        "timeout_behavior": {
            "session_end_configured_seconds": 1,
            "session_end_observed": "SessionEnd" in observed,
            "long_hook_clamp": "not_probed",
        },
        "transcript_support": {
            "path_field_observed": any(record.get("has_transcript_path") for record in records),
            "content_saved": False,
        },
        "probe_date": date.today().isoformat(),
        "official_references": list(OFFICIAL_REFERENCES),
        "runtime_feature_flags": _runtime_feature_flags(),
        "source_declared_events": list(SOURCE_DECLARED_EVENTS),
        "source_revision": SOURCE_REVISION,
        "probe_result": {
            "status": status,
            "blocker": blocker,
            "exit_code": exit_code,
            "agent_received_injected_context": SESSION_START_CONTEXT in _last_agent_message(cli_events),
            "stderr_present": bool(stderr.strip()),
        },
        "payload_shapes": records,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=45)
    args = parser.parse_args()
    result = run_probe(args.output, timeout_seconds=args.timeout)
    print(json.dumps(result["probe_result"], sort_keys=True))
    return 0 if result["probe_result"]["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
