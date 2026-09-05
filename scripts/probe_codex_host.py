#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import uuid
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import tomli_w


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
REQUIRED_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "Stop",
    "SessionEnd",
)
SESSION_START_CONTEXT = "G3_SESSION_START_CONTEXT"
SOURCE_REVISION = "728cb12fe5794b0c3a8e776fb4994b1650b973a8"
PINNED_EXECUTABLE_VERSION = "codex-cli 0.153.4"
PINNED_MODEL = "gpt-5.6-luna"
OFFICIAL_REFERENCES = (
    "https://developers.openai.com/codex/hooks",
    "https://developers.openai.com/codex/config-file/config-reference",
    "https://developers.openai.com/codex/cli/reference",
)
SCENARIOS = (
    "baseline_with_duplicate",
    "hook_nonzero_exit",
    "hook_timeout",
    "restart_recovery",
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _recorder_command(
    *,
    recorder: Path,
    log: Path,
    state: Path,
    probe_id: str,
    scenario: str,
    started_ns: int,
    behavior: str,
    handler_ref: str,
) -> str:
    args = [
        sys.executable,
        str(recorder),
        "--log",
        str(log),
        "--state",
        str(state),
        "--probe-id",
        probe_id,
        "--scenario",
        scenario,
        "--started-ns",
        str(started_ns),
        "--behavior",
        behavior,
        "--handler-ref",
        handler_ref,
    ]
    if behavior == "sleep":
        args.extend(("--sleep", "2.5"))
    return shlex.join(args)


def _hooks_config(
    *,
    recorder: Path,
    log: Path,
    state: Path,
    probe_id: str,
    scenario: str,
    started_ns: int,
) -> dict[str, Any]:
    hooks: dict[str, list[dict[str, Any]]] = {}
    for event in SOURCE_DECLARED_EVENTS:
        behavior = "normal"
        timeout = 1 if event == "SessionEnd" else 10
        if event == "PreToolUse" and scenario == "hook_nonzero_exit":
            behavior = "exit"
        elif event == "PreToolUse" and scenario == "hook_timeout":
            behavior = "sleep"
            timeout = 1
        handlers = [
            {
                "type": "command",
                "command": _recorder_command(
                    recorder=recorder,
                    log=log,
                    state=state,
                    probe_id=probe_id,
                    scenario=scenario,
                    started_ns=started_ns,
                    behavior=behavior,
                    handler_ref="primary",
                ),
                "timeout": timeout,
            }
        ]
        if event == "PostToolUse" and scenario == "baseline_with_duplicate":
            handlers.append(
                {
                    "type": "command",
                    "command": _recorder_command(
                        recorder=recorder,
                        log=log,
                        state=state,
                        probe_id=probe_id,
                        scenario=scenario,
                        started_ns=started_ns,
                        behavior="normal",
                        handler_ref="duplicate",
                    ),
                    "timeout": timeout,
                }
            )
        hooks[event] = [{"hooks": handlers}]
    return {"description": "SagaContext G3 synthetic capability probe", "hooks": hooks}


def _config_fingerprint(configs: dict[str, dict[str, Any]]) -> str:
    normalized = json.loads(json.dumps(configs))
    for config in normalized.values():
        for groups in config["hooks"].values():
            for group in groups:
                for handler in group["hooks"]:
                    handler["command"] = "<python> <recorder> <redacted-probe-args>"
    digest = hashlib.sha256(
        json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"sha256:{digest}"


def _runtime_feature_flags() -> dict[str, bool]:
    completed = subprocess.run(
        ["codex", "features", "list"], check=True, text=True, capture_output=True
    )
    flags: dict[str, bool] = {}
    for line in completed.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[-1] in {"true", "false"}:
            flags[fields[0]] = fields[-1] == "true"
    return {"hooks": flags.get("hooks", False)}


def _read_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _last_agent_message(events: list[dict[str, Any]]) -> str:
    for event in reversed(events):
        if event.get("type") == "item.completed":
            item = event.get("item") or {}
            if item.get("type") == "agent_message":
                return str(item.get("text", ""))
    return ""


def _agent_received_marker(events: list[dict[str, Any]]) -> bool:
    return any(
        event.get("type") == "item.completed"
        and SESSION_START_CONTEXT in json.dumps(event, ensure_ascii=True)
        for event in events
    )


def _classify_blocker(stderr: str, timed_out: bool) -> str | None:
    normalized = stderr.lower()
    if timed_out:
        return "model_request_timed_out"
    if any(token in normalized for token in ("model_not_found", "is not supported by any configured account", "model is not available")):
        return "model_unavailable"
    if any(token in normalized for token in ("401 unauthorized", "invalid_api_key", "authentication failed", "authentication_failed")):
        return "model_authentication_failed"
    if "hook" in normalized and ("invalid" in normalized or "malformed" in normalized):
        return "hook_configuration_rejected"
    return None


def _stderr_summary(stderr: str) -> dict[str, bool]:
    normalized = stderr.lower()
    return {
        "present": bool(stderr.strip()),
        "authentication_signal": any(
            token in normalized for token in ("401 unauthorized", "invalid_api_key", "authentication failed", "authentication_failed")
        ),
        "hook_failure_signal": "hook" in normalized
        and any(token in normalized for token in ("failed", "exit", "timed out", "timeout")),
        "hook_timeout_signal": "hook" in normalized
        and any(token in normalized for token in ("timed out", "timeout")),
    }


def _prepare_isolated_codex_home(
    destination: Path, model: str = PINNED_MODEL, workspace: Path | None = None,
) -> dict[str, Any]:
    source_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    source_config = source_home / "config.toml"
    source_auth = source_home / "auth.json"
    destination.mkdir(mode=0o700, parents=True)
    config: dict[str, Any] = {"features": {"hooks": True}}
    provider_configured = False
    if source_config.exists():
        loaded = tomllib.loads(source_config.read_text())
        provider = loaded.get("model_provider")
        providers = loaded.get("model_providers")
        if isinstance(provider, str) and isinstance(providers, dict) and provider in providers:
            config["model_provider"] = provider
            config["model_providers"] = {provider: providers[provider]}
            provider_configured = True
        if "disable_response_storage" in loaded:
            config["disable_response_storage"] = bool(loaded["disable_response_storage"])
    config["model"] = model
    if workspace is not None:
        config["projects"] = {str(workspace.resolve()): {"trust_level": "trusted"}}
    (destination / "config.toml").write_text(tomli_w.dumps(config))
    os.chmod(destination / "config.toml", 0o600)
    auth_copied = False
    if source_auth.exists():
        shutil.copyfile(source_auth, destination / "auth.json")
        os.chmod(destination / "auth.json", 0o600)
        auth_copied = True
    return {
        "mode": "temporary_minimal_codex_home",
        "provider_configured": provider_configured,
        "auth_material_available": auth_copied,
        "provider_value_saved": False,
        "provider_endpoint_saved": False,
        "auth_material_saved": False,
        "temporary_project_trusted": workspace is not None,
    }


def _scenario_result(
    *,
    name: str,
    workspace: Path,
    codex_home: Path,
    timeout_seconds: int,
    model: str = PINNED_MODEL,
) -> dict[str, Any]:
    command = [
        "codex",
        "--dangerously-bypass-hook-trust",
        "--ask-for-approval",
        "never",
        "--sandbox",
        "read-only",
        "--cd",
        str(workspace),
        "--model",
        model,
        "exec",
        "--json",
        "--ephemeral",
        "--ignore-rules",
        "Use a shell command to read synthetic.txt. If lifecycle context supplies an opaque uppercase marker, reply with that marker verbatim; otherwise reply MISSING.",
    ]
    env = os.environ.copy()
    env["CODEX_HOME"] = str(codex_home)
    started_at = _utc_now()
    started = time.monotonic()
    timed_out = False
    try:
        completed = subprocess.run(
            command, text=True, capture_output=True, timeout=timeout_seconds, env=env
        )
        stdout = completed.stdout
        stderr = completed.stderr
        exit_code = completed.returncode
    except subprocess.TimeoutExpired as error:
        timed_out = True
        stdout = error.stdout.decode(errors="replace") if isinstance(error.stdout, bytes) else (error.stdout or "")
        stderr = error.stderr.decode(errors="replace") if isinstance(error.stderr, bytes) else (error.stderr or "")
        exit_code = None
    elapsed_ms = round((time.monotonic() - started) * 1000, 3)
    cli_events = []
    for line in stdout.splitlines():
        if not line.startswith("{"):
            continue
        try:
            cli_events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    failures = [
        str((event.get("error") or {}).get("message", ""))
        for event in cli_events
        if event.get("type") == "turn.failed" and isinstance(event.get("error"), dict)
    ]
    turn_completed = any(event.get("type") == "turn.completed" for event in cli_events)
    blocker = None
    if timed_out or exit_code != 0 or failures or not turn_completed:
        blocker = _classify_blocker("\n".join(failures), False) or _classify_blocker(stderr, timed_out)
        blocker = blocker or "host_execution_failed"
    return {
        "name": name,
        "started_at": started_at,
        "finished_at": _utc_now(),
        "elapsed_ms": elapsed_ms,
        "exit_code": exit_code,
        "outer_timeout": timed_out,
        "blocker": blocker,
        "stderr_summary": _stderr_summary(stderr),
        "cli_event_types": dict(sorted(Counter(str(item.get("type", "unknown")) for item in cli_events).items())),
        "agent_received_injected_context": _agent_received_marker(cli_events),
    }


def run_probe(output: Path, timeout_seconds: int = 90, model: str = PINNED_MODEL) -> dict[str, Any]:
    recorder = Path(__file__).with_name("g3_hook_recorder.py").resolve()
    version = subprocess.run(
        ["codex", "--version"], check=True, text=True, capture_output=True
    ).stdout.strip()
    probe_id = f"g3-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    started_at = _utc_now()
    started_ns = time.monotonic_ns()
    temporary = tempfile.TemporaryDirectory(prefix="sagacontext-g3-")
    temporary_root = Path(temporary.name)
    try:
        workspace = temporary_root / "workspace"
        codex_home = temporary_root / "codex-home"
        workspace.mkdir()
        (workspace / ".codex").mkdir()
        (workspace / "synthetic.txt").write_text("synthetic probe only\n")
        subprocess.run(["git", "init", "-q", str(workspace)], check=True)
        hook_log = temporary_root / "hook-events.jsonl"
        state = temporary_root / "recorder-state.json"
        model_runtime = _prepare_isolated_codex_home(codex_home, model, workspace)
        configs: dict[str, dict[str, Any]] = {}
        scenarios: list[dict[str, Any]] = []
        for scenario in SCENARIOS:
            config = _hooks_config(
                recorder=recorder,
                log=hook_log,
                state=state,
                probe_id=probe_id,
                scenario=scenario,
                started_ns=started_ns,
            )
            configs[scenario] = config
            (workspace / ".codex" / "hooks.json").write_text(
                json.dumps(config, indent=2) + "\n"
            )
            scenario_result = _scenario_result(
                name=scenario,
                workspace=workspace,
                codex_home=codex_home,
                timeout_seconds=timeout_seconds,
                model=model,
            )
            current_records = _read_records(hook_log)
            scenario_records = [item for item in current_records if item.get("scenario") == scenario]
            scenario_result["event_counts"] = dict(
                sorted(Counter(str(item.get("hook_event_name")) for item in scenario_records).items())
            )
            scenario_result["receipt_occurrences"] = [
                item["occurrence_no"] for item in scenario_records
            ]
            scenarios.append(scenario_result)
        records = _read_records(hook_log)
        observed = {str(record.get("hook_event_name")) for record in records}
        event_capabilities = [
            {
                "event": event,
                "status": "observed" if event in observed else "not_observed",
                "payload_keys": next(
                    (
                        record["payload_keys"]
                        for record in records
                        if record.get("hook_event_name") == event
                    ),
                    [],
                ),
            }
            for event in SOURCE_DECLARED_EVENTS
        ]
        environment_blocker = next(
            (
                item["blocker"]
                for item in scenarios
                if item["blocker"] in {"model_authentication_failed", "model_request_timed_out", "model_unavailable"}
            ),
            None,
        )
        hard_failure = next(
            (
                item["blocker"]
                for item in scenarios
                if item["blocker"] not in {None, "model_authentication_failed", "model_request_timed_out", "model_unavailable"}
            ),
            None,
        )
        capture_status = (
            "blocked_environment"
            if environment_blocker
            else "failed"
            if hard_failure
            else "completed"
        )
        result = {
            "probe_id": probe_id,
            "host_name": "codex",
            "host_form": "cli-exec",
            "executable_version": version,
            "pinned_executable_version": PINNED_EXECUTABLE_VERSION,
            "requested_model": model,
            "adapter_version": "g3-probe-v4",
            "config_fingerprint": _config_fingerprint(configs),
            "verified_events": event_capabilities,
            "injection_modes": ["SessionStart.additionalContext"]
            if any(item["agent_received_injected_context"] for item in scenarios)
            else [],
            "timeout_behavior": {
                "session_end_configured_seconds": 1,
                "pre_tool_timeout_configured_seconds": 1,
                "pre_tool_sleep_seconds": 2.5,
                "host_timeout_signal_observed": next(
                    (
                        item["stderr_summary"]["hook_timeout_signal"]
                        for item in scenarios
                        if item["name"] == "hook_timeout"
                    ),
                    False,
                ),
            },
            "transcript_support": {
                "path_field_observed": any(record.get("has_transcript_path") for record in records),
                "content_saved": False,
            },
            "probe_date": date.today().isoformat(),
            "started_at": started_at,
            "finished_at": _utc_now(),
            "official_references": list(OFFICIAL_REFERENCES),
            "runtime_feature_flags": _runtime_feature_flags(),
            "source_declared_events": list(SOURCE_DECLARED_EVENTS),
            "required_runtime_events": list(REQUIRED_EVENTS),
            "source_revision": SOURCE_REVISION,
            "payload_policy": "synthetic_only_redacted_receipts",
            "model_runtime": model_runtime,
            "scenarios": scenarios,
            "probe_result": {
                "status": capture_status,
                "blocker": environment_blocker or hard_failure,
                "exit_code": next((item["exit_code"] for item in scenarios if item["exit_code"]), 0),
                "agent_received_injected_context": any(
                    item["agent_received_injected_context"] for item in scenarios
                ),
                "stderr_present": any(item["stderr_summary"]["present"] for item in scenarios),
            },
            "payload_shapes": records,
            "cleanup": {
                "temporary_workspace_policy": "delete_after_capture",
                "global_config_modified": False,
                "private_transcript_read": False,
                "raw_reference_mapping_saved": False,
                "temporary_root_removed": False,
            },
        }
    finally:
        temporary.cleanup()
    result["cleanup"]["temporary_root_removed"] = not temporary_root.exists()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--model", default=PINNED_MODEL)
    args = parser.parse_args()
    result = run_probe(args.output, timeout_seconds=args.timeout, model=args.model)
    print(
        json.dumps(
            {
                "probe_id": result["probe_id"],
                "capture_status": result["probe_result"]["status"],
                "blocker": result["probe_result"]["blocker"],
                "observed_required_events": sorted(
                    item["event"]
                    for item in result["verified_events"]
                    if item["event"] in REQUIRED_EVENTS and item["status"] == "observed"
                ),
            },
            sort_keys=True,
        )
    )
    return 0 if result["probe_result"]["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
