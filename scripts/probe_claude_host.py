#!/usr/bin/env python3
"""G3-CC: Claude Code host event admission probe.

Runs the pinned Claude Code executable against a temporary Git workspace with an
isolated CLAUDE_CONFIG_DIR. Only probe hooks are loaded (project-local settings in
the temporary workspace); user/global hooks are excluded by --setting-sources.
Provider variables are passed to the child process only and never saved.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import uuid
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

SOURCE_DECLARED_EVENTS = (
    "SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop",
    "SessionEnd", "PreCompact", "Notification", "SubagentStop",
)
REQUIRED_EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop", "SessionEnd")
PINNED_EXECUTABLE_VERSION = "2.1.285 (Claude Code)"
PINNED_MODEL = "sonnet"
SCENARIOS = (
    "no_context_control",
    "baseline_with_duplicate",
    "hook_nonzero_exit",
    "hook_timeout",
    "restart_recovery",
)
# Scenario for SessionStart source shapes; runs after the marker scenarios.
SOURCE_SCENARIO = "session_start_sources"
OFFICIAL_REFERENCES = (
    "https://code.claude.com/docs/en/hooks",
    "https://code.claude.com/docs/en/settings",
    "https://code.claude.com/docs/en/cli-reference",
)
# Only these variables from the user's settings env reach the child; values never persist.
PROVIDER_ENV_PREFIXES = ("ANTHROPIC_",)
BASE_ENV = ("PATH", "HOME", "TMPDIR", "LANG", "USER", "SHELL", "LOGNAME")
PROMPT = ("Use the Read tool to read synthetic.txt. If lifecycle context supplies an opaque "
          "uppercase marker, reply with that marker verbatim and nothing else; otherwise reply MISSING.")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _executable() -> str:
    """Frozen absolute path, as the observation watchdog uses; never PATH lookup."""
    return str((Path.home() / ".local/bin/claude").resolve())


def _recorder_command(*, recorder: Path, log: Path, state: Path, probe_id: str, scenario: str,
                      started_ns: int, behavior: str, handler_ref: str, marker: str) -> str:
    args = [sys.executable, str(recorder), "--log", str(log), "--state", str(state),
            "--probe-id", probe_id, "--scenario", scenario, "--started-ns", str(started_ns),
            "--behavior", behavior, "--handler-ref", handler_ref, "--marker", marker]
    if behavior == "sleep":
        args.extend(("--sleep", "2.5"))
    return shlex.join(args)


def _hooks_config(*, recorder: Path, log: Path, state: Path, probe_id: str, scenario: str,
                  started_ns: int, marker: str) -> dict[str, Any]:
    hooks: dict[str, list[dict[str, Any]]] = {}
    for event in SOURCE_DECLARED_EVENTS:
        behavior, timeout = "normal", 10
        if event == "PreToolUse" and scenario == "hook_nonzero_exit":
            behavior = "exit"
        elif event == "PreToolUse" and scenario == "hook_timeout":
            behavior, timeout = "sleep", 1
        handler = lambda ref, b=behavior: {"type": "command", "timeout": timeout, "command": _recorder_command(
            recorder=recorder, log=log, state=state, probe_id=probe_id, scenario=scenario,
            started_ns=started_ns, behavior=b, handler_ref=ref, marker=marker)}
        handlers = [handler("primary")]
        if event == "PostToolUse" and scenario == "baseline_with_duplicate":
            handlers.append(handler("duplicate", "normal"))
        hooks[event] = [{"hooks": handlers}]
    return {"hooks": hooks}


def _config_fingerprint(configs: dict[str, dict[str, Any]]) -> str:
    normalized = json.loads(json.dumps(configs))
    for config in normalized.values():
        for groups in config["hooks"].values():
            for group in groups:
                for handler in group["hooks"]:
                    handler["command"] = "<python> <recorder> <redacted-probe-args>"
    digest = hashlib.sha256(json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return f"sha256:{digest}"


def _read_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _child_env(config_dir: Path, *, meta_flag: bool = False) -> tuple[dict[str, str], dict[str, Any]]:
    """Minimal child environment. Provider values come from user settings env, never saved."""
    env = {key: os.environ[key] for key in BASE_ENV if key in os.environ}
    settings = Path.home() / ".claude/settings.json"
    provider_keys: list[str] = []
    if settings.exists():
        user_env = json.loads(settings.read_text()).get("env", {})
        for key, value in user_env.items():
            if key.startswith(PROVIDER_ENV_PREFIXES) and isinstance(value, str):
                env[key] = value
                provider_keys.append(key)
    env["CLAUDE_CONFIG_DIR"] = str(config_dir)
    env["DISABLE_AUTOUPDATER"] = "1"
    if meta_flag:
        env["SKILL_EVOLUTION_REVIEW"] = "1"
    return env, {
        "mode": "temporary_claude_config_dir",
        "setting_sources": "project,local",
        "provider_variable_count": len(provider_keys),
        "provider_value_saved": False,
        "provider_endpoint_saved": False,
        "auth_material_saved": False,
        "user_settings_loaded": False,
    }


def _last_result(events: list[dict[str, Any]]) -> dict[str, Any]:
    return next((e for e in reversed(events) if e.get("type") == "result"), {})


def _classify_blocker(result: dict[str, Any], stderr: str, timed_out: bool) -> str | None:
    text = (str(result.get("result", "")) + "\n" + stderr).lower()
    if timed_out:
        return "model_request_timed_out"
    if result.get("api_error_status") in (401, 403) or any(t in text for t in ("invalid api key", "authentication_error", "401")):
        return "model_authentication_failed"
    if any(t in text for t in ("model_not_found", "not_found_error", "model is not available")):
        return "model_unavailable"
    if result.get("api_error_status"):
        return "model_request_failed"
    return None


def _run_claude(*, workspace: Path, env: dict[str, str], timeout_seconds: int, model: str,
                extra: list[str] | None = None, prompt: str = PROMPT) -> dict[str, Any]:
    command = [_executable(), "-p", prompt, "--model", model, "--output-format", "stream-json",
               "--verbose", "--include-hook-events", "--setting-sources", "project,local",
               "--tools", "Read", "--allowedTools", "Read", *(extra or [])]
    started = time.monotonic()
    timed_out = False
    try:
        completed = subprocess.run(command, cwd=workspace, env=env, text=True, capture_output=True,
                                   timeout=timeout_seconds, stdin=subprocess.DEVNULL)
        stdout, stderr, exit_code = completed.stdout, completed.stderr, completed.returncode
    except subprocess.TimeoutExpired as error:
        timed_out = True
        stdout = error.stdout.decode(errors="replace") if isinstance(error.stdout, bytes) else (error.stdout or "")
        stderr = error.stderr.decode(errors="replace") if isinstance(error.stderr, bytes) else (error.stderr or "")
        exit_code = None
    events = []
    for line in stdout.splitlines():
        if line.startswith("{"):
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return {"events": events, "stderr": stderr, "exit_code": exit_code, "timed_out": timed_out,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 3)}


def _scenario_result(name: str, run: dict[str, Any], marker: str) -> dict[str, Any]:
    events = run["events"]
    result = _last_result(events)
    final = str(result.get("result", "")).strip()
    blocker = None
    if run["timed_out"] or run["exit_code"] != 0 or not result or result.get("is_error"):
        blocker = _classify_blocker(result, run["stderr"], run["timed_out"]) or "host_execution_failed"
    hook_responses = [e for e in events if e.get("type") == "system" and e.get("subtype") == "hook_response"]
    assistant_blocks = [json.dumps(e.get("message", {}).get("content", []), sort_keys=True)
                        for e in events if e.get("type") in {"assistant", "user"}]
    return {
        "name": name,
        "elapsed_ms": run["elapsed_ms"],
        "exit_code": run["exit_code"],
        "outer_timeout": run["timed_out"],
        "blocker": blocker,
        "stderr_summary": {"present": bool(run["stderr"].strip()),
                           "hook_timeout_signal": "timed out" in run["stderr"].lower() or "timeout" in run["stderr"].lower()},
        "cli_event_types": dict(sorted(Counter(f"{e.get('type')}:{e.get('subtype')}" for e in events).items())),
        "hook_outcomes": dict(sorted(Counter(f"{e.get('hook_event')}:{e.get('outcome')}" for e in hook_responses).items())),
        "agent_received_injected_context": bool(marker) and final == marker,
        "consumption_evidence": {
            "expected_marker_digest": hashlib.sha256(marker.encode()).hexdigest(),
            "final_message_digest": hashlib.sha256(final.encode()).hexdigest(),
            "final_message_present": bool(final),
            "final_message_is_missing": final == "MISSING",
            # Tool output or earlier turns carrying the marker would not prove consumption.
            "marker_in_non_message_item": any(marker in block for block in assistant_blocks[:-1]) if marker else False,
        },
        "session_ref_present": bool(result.get("session_id")),
    }


def _session_id(run: dict[str, Any]) -> str | None:
    return _last_result(run["events"]).get("session_id")


def run_probe(output: Path, timeout_seconds: int = 120, model: str = PINNED_MODEL) -> dict[str, Any]:
    if output.exists():
        raise ValueError("refusing_to_overwrite_capture")
    recorder = Path(__file__).with_name("g3_hook_recorder.py").resolve()
    executable = _executable()
    version = subprocess.run([executable, "--version"], check=True, text=True, capture_output=True).stdout.strip()
    if version != PINNED_EXECUTABLE_VERSION:
        raise ValueError("host_version_not_pinned")
    probe_id = f"g3cc-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    started_at, started_ns = _utc_now(), time.monotonic_ns()
    temporary = tempfile.TemporaryDirectory(prefix="sagacontext-g3cc-")
    root = Path(temporary.name)
    try:
        workspace, config_dir = root / "workspace", root / "claude-config"
        (workspace / ".claude").mkdir(parents=True)
        config_dir.mkdir(mode=0o700)
        (workspace / "synthetic.txt").write_text("synthetic probe only\n")
        subprocess.run(["git", "init", "-q", str(workspace)], check=True)
        hook_log, state = root / "hook-events.jsonl", root / "recorder-state.json"
        settings_file = workspace / ".claude/settings.local.json"
        env, model_runtime = _child_env(config_dir)
        configs: dict[str, dict[str, Any]] = {}
        scenarios: list[dict[str, Any]] = []
        session_ids: dict[str, str | None] = {}

        def write_hooks(scenario: str, marker: str) -> None:
            configs[scenario] = _hooks_config(recorder=recorder, log=hook_log, state=state, probe_id=probe_id,
                                              scenario=scenario, started_ns=started_ns, marker=marker)
            settings_file.write_text(json.dumps(configs[scenario], indent=2) + "\n")

        def attach_records(result: dict[str, Any], scenario: str) -> None:
            records = [r for r in _read_records(hook_log) if r.get("scenario") == scenario]
            result["event_counts"] = dict(sorted(Counter(str(r.get("hook_event_name")) for r in records).items()))
            result["session_start_sources"] = sorted({(r.get("safe_enums") or {}).get("source", "absent")
                                                      for r in records if r.get("hook_event_name") == "SessionStart"})

        for scenario in SCENARIOS:
            marker = "" if scenario == "no_context_control" else "G3_" + uuid.uuid4().hex.upper()
            write_hooks(scenario, marker)
            extra = []
            if scenario == "restart_recovery" and session_ids.get("baseline_with_duplicate"):
                # A new process resuming the baseline session; hooks now carry a new marker.
                extra = ["--resume", session_ids["baseline_with_duplicate"]]
            run = _run_claude(workspace=workspace, env=env, timeout_seconds=timeout_seconds, model=model, extra=extra)
            session_ids[scenario] = _session_id(run)
            result = _scenario_result(scenario, run, marker)
            result["resumed_from_baseline"] = bool(extra)
            result["session_id_continuous"] = bool(extra) and session_ids[scenario] == session_ids.get("baseline_with_duplicate")
            attach_records(result, scenario)
            scenarios.append(result)

        # Environment inheritance for the skill-evolution exclusion: the hook process
        # must see SKILL_EVOLUTION_REVIEW when the host was started with it.
        write_hooks("env_inheritance", "")
        env_log = root / "env-inheritance.json"
        probe_hook = {"type": "command", "timeout": 10, "command": shlex.join([
            sys.executable, "-c",
            "import json,os,sys;json.load(sys.stdin);"
            f"open({str(env_log)!r},'w').write(json.dumps({{'SKILL_EVOLUTION_REVIEW':os.environ.get('SKILL_EVOLUTION_REVIEW')}}));"
            "print('{}')"])}
        settings_file.write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": [probe_hook]}]}}) + "\n")
        meta_env, _ = _child_env(config_dir, meta_flag=True)
        _run_claude(workspace=workspace, env=meta_env, timeout_seconds=timeout_seconds, model=model,
                    prompt="Reply with exactly OK.")
        hook_env_inherited = env_log.exists() and json.loads(env_log.read_text()).get("SKILL_EVOLUTION_REVIEW") == "1"

        # SessionStart source shapes. -p only exposes startup and resume; compact and
        # clear need the interactive smoke and are recorded as not probed here.
        write_hooks(SOURCE_SCENARIO, "")
        first = _run_claude(workspace=workspace, env=env, timeout_seconds=timeout_seconds, model=model,
                            prompt="Reply with exactly OK.")
        resumed = _run_claude(workspace=workspace, env=env, timeout_seconds=timeout_seconds, model=model,
                              prompt="Reply with exactly OK.", extra=["--resume", _session_id(first) or ""])
        source_records = [r for r in _read_records(hook_log) if r.get("scenario") == SOURCE_SCENARIO
                          and r.get("hook_event_name") == "SessionStart"]
        source_shapes = {
            "observed_sources": sorted({(r.get("safe_enums") or {}).get("source", "absent") for r in source_records}),
            "resume_keeps_session_id": bool(_session_id(first)) and _session_id(first) == _session_id(resumed),
            "not_probed_in_print_mode": ["compact", "clear"],
            "exit_codes": [first["exit_code"], resumed["exit_code"]],
        }

        records = _read_records(hook_log)
        observed = {str(r.get("hook_event_name")) for r in records}
        event_capabilities = [{"event": e, "status": "observed" if e in observed else "not_observed",
                               "payload_keys": next((r["payload_keys"] for r in records if r.get("hook_event_name") == e), [])}
                              for e in SOURCE_DECLARED_EVENTS]
        env_blockers = {"model_authentication_failed", "model_request_timed_out", "model_unavailable", "model_request_failed"}
        environment_blocker = next((s["blocker"] for s in scenarios if s["blocker"] in env_blockers), None)
        hard_failure = next((s["blocker"] for s in scenarios if s["blocker"] not in env_blockers | {None}), None)
        result = {
            "probe_id": probe_id,
            "host_name": "claude-code",
            "host_form": "cli-print",
            "executable_version": version,
            "executable_path": executable,
            "pinned_executable_version": PINNED_EXECUTABLE_VERSION,
            "requested_model": model,
            "adapter_version": "g3cc-probe-v1",
            "config_fingerprint": _config_fingerprint(configs),
            "verified_events": event_capabilities,
            "injection_modes": ["SessionStart.additionalContext"]
            if any(s["agent_received_injected_context"] for s in scenarios) else [],
            "timeout_behavior": {"pre_tool_timeout_configured_seconds": 1, "pre_tool_sleep_seconds": 2.5},
            "transcript_support": {"path_field_observed": any(r.get("has_transcript_path") for r in records),
                                   "content_saved": False},
            "hook_env_inherited": hook_env_inherited,
            "session_start_sources": source_shapes,
            "probe_date": date.today().isoformat(),
            "started_at": started_at,
            "finished_at": _utc_now(),
            "official_references": list(OFFICIAL_REFERENCES),
            "source_declared_events": list(SOURCE_DECLARED_EVENTS),
            "required_runtime_events": list(REQUIRED_EVENTS),
            "payload_policy": "synthetic_only_redacted_receipts",
            "model_runtime": model_runtime,
            "scenarios": scenarios,
            "interactive_smoke": {"status": "not_run"},
            "probe_result": {
                "status": "blocked_environment" if environment_blocker else "failed" if hard_failure else "completed",
                "blocker": environment_blocker or hard_failure,
                "agent_received_injected_context": any(s["agent_received_injected_context"] for s in scenarios),
                "stderr_present": any(s["stderr_summary"]["present"] for s in scenarios),
            },
            "payload_shapes": records,
            "cleanup": {"temporary_workspace_policy": "delete_after_capture", "global_config_modified": False,
                        "private_transcript_read": False, "raw_reference_mapping_saved": False,
                        "temporary_root_removed": False},
        }
    finally:
        temporary.cleanup()
    result["cleanup"]["temporary_root_removed"] = not root.exists()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--model", default=PINNED_MODEL)
    args = parser.parse_args()
    result = run_probe(args.output, timeout_seconds=args.timeout, model=args.model)
    print(json.dumps({"probe_id": result["probe_id"], "capture_status": result["probe_result"]["status"],
                      "blocker": result["probe_result"]["blocker"],
                      "observed_required_events": sorted(i["event"] for i in result["verified_events"]
                                                         if i["event"] in REQUIRED_EVENTS and i["status"] == "observed")},
                     sort_keys=True))
    return 0 if result["probe_result"]["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
