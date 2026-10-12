#!/usr/bin/env python3
"""Independent G3-CC evaluator. Reads only the saved capture; passed needs every assertion."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

REQUIRED_EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop", "SessionEnd")
FORBIDDEN_SAVED_KEYS = {"prompt", "tool_input", "tool_response", "last_assistant_message", "transcript_path",
                        "cwd", "session_id", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"}
POSITIVE = ("baseline_with_duplicate", "hook_nonzero_exit", "hook_timeout", "restart_recovery")
# Collection-only admission (已拍板 2026-09-30): injection is recorded, never required.
INJECTION_ASSERTIONS = {"session_start_context_received"}


def _forbidden_paths(value: Any, prefix: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}"
            if key in FORBIDDEN_SAVED_KEYS:
                found.append(path)
            found.extend(_forbidden_paths(item, path))
    elif isinstance(value, list):
        for item in value:
            found.extend(_forbidden_paths(item, f"{prefix}[]"))
    return found


def evaluate(capture: dict[str, Any], capture_digest: str, *, scope: str = "full") -> dict[str, Any]:
    assertions: list[dict[str, Any]] = []

    def check(name: str, ok: bool | None, evidence: dict[str, Any], *, fail_status: str = "not_observed") -> None:
        assertions.append({"name": name, "status": "pass" if ok else fail_status, "evidence": evidence})

    records = list(capture.get("payload_shapes") or [])
    scenarios = {s.get("name"): s for s in capture.get("scenarios") or []}
    capture_status = (capture.get("probe_result") or {}).get("status")
    runtime, cleanup = capture.get("model_runtime") or {}, capture.get("cleanup") or {}

    check("executable_version_pinned", capture.get("executable_version") == capture.get("pinned_executable_version"),
          {"observed": capture.get("executable_version"), "expected": capture.get("pinned_executable_version")}, fail_status="fail")
    path = str(capture.get("executable_path") or "")
    check("executable_path_pinned", path.startswith("/") and "/versions/" in path, {"executable_path": path}, fail_status="fail")
    fingerprint = str(capture.get("config_fingerprint") or "")
    check("config_fingerprint_recorded", fingerprint.startswith("sha256:") and len(fingerprint) == 71,
          {"config_fingerprint": fingerprint}, fail_status="fail")
    check("synthetic_isolation", capture.get("payload_policy") == "synthetic_only_redacted_receipts"
          and runtime.get("mode") == "temporary_claude_config_dir" and runtime.get("user_settings_loaded") is False
          and runtime.get("provider_value_saved") is False and runtime.get("auth_material_saved") is False
          and cleanup.get("global_config_modified") is False and cleanup.get("private_transcript_read") is False,
          {"model_runtime": runtime, "cleanup": cleanup}, fail_status="fail")
    observed = {i.get("event") for i in capture.get("verified_events") or [] if i.get("status") == "observed"}
    check("hooks_runtime_enabled", bool(observed), {"observed_events": sorted(observed)}, fail_status="fail")
    for event in REQUIRED_EVENTS:
        check(f"event_{event}", event in observed,
              {"occurrences": [r.get("occurrence_no") for r in records if r.get("hook_event_name") == event][:3]})

    control = scenarios.get("no_context_control") or {}
    check("no_context_negative_control", control.get("exit_code") == 0 and control.get("blocker") is None
          and (control.get("consumption_evidence") or {}).get("final_message_is_missing") is True, {"scenario": control})
    positives = [scenarios.get(n) or {} for n in POSITIVE]
    digests = [(s.get("consumption_evidence") or {}).get("expected_marker_digest", "") for s in positives]
    check("session_start_context_received", len(set(digests)) == 4 and all(
        s.get("exit_code") == 0 and s.get("blocker") is None and s.get("agent_received_injected_context") is True
        and (s.get("consumption_evidence") or {}).get("marker_in_non_message_item") is False for s in positives),
        {"per_scenario": {n: s.get("agent_received_injected_context") for n, s in zip(POSITIVE, positives)}})

    linked = [r for r in records if r.get("session_ref") and r.get("workspace_ref")]
    tools = [r for r in records if r.get("hook_event_name") in {"PreToolUse", "PostToolUse"}]
    turn = [r for r in records if r.get("hook_event_name") in {"UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"}]
    check("stable_event_linkage", bool(linked) and bool(tools) and all(r.get("tool_call_ref") for r in tools)
          and all(r.get("task_ref") for r in turn),
          {"linked": len(linked), "tool_receipts": len(tools), "turn_receipts_with_task_ref": sum(bool(r.get("task_ref")) for r in turn)})
    repeated = sorted(ref for ref, n in Counter(r.get("duplicate_group_ref") for r in records if r.get("duplicate_group_ref")).items() if n >= 2)
    check("duplicate_event_observed", bool(repeated), {"duplicate_group_refs": repeated[:5]})

    abnormal = scenarios.get("hook_nonzero_exit") or {}
    check("hook_nonzero_exit_observed", any(r.get("exit_class") == "scheduled_nonzero_7" for r in records)
          and abnormal.get("exit_code") == 0 and (abnormal.get("event_counts") or {}).get("PostToolUse", 0) >= 1,
          {"exit_code": abnormal.get("exit_code"), "event_counts": abnormal.get("event_counts"), "hook_outcomes": abnormal.get("hook_outcomes")})
    timeout = scenarios.get("hook_timeout") or {}
    check("hook_timeout_observed", any(r.get("timeout_class") == "configured_timeout_target" for r in records)
          and timeout.get("exit_code") == 0 and (timeout.get("event_counts") or {}).get("PostToolUse", 0) >= 1,
          {"exit_code": timeout.get("exit_code"), "elapsed_ms": timeout.get("elapsed_ms"), "hook_outcomes": timeout.get("hook_outcomes")})
    recovery = scenarios.get("restart_recovery") or {}
    check("restart_recovery_observed", recovery.get("exit_code") == 0 and recovery.get("resumed_from_baseline") is True
          and recovery.get("session_id_continuous") is True
          and (scope == "collection" or recovery.get("agent_received_injected_context") is True)
          and "resume" in (recovery.get("session_start_sources") or []),
          {"scenario": {k: recovery.get(k) for k in ("exit_code", "resumed_from_baseline", "session_id_continuous",
                                                     "agent_received_injected_context", "session_start_sources")}})

    check("hook_env_inherited", capture.get("hook_env_inherited") is True, {"hook_env_inherited": capture.get("hook_env_inherited")},
          fail_status="fail")
    sources = capture.get("session_start_sources") or {}
    check("session_start_source_recorded", {"startup", "resume"} <= set(sources.get("observed_sources") or [])
          and sources.get("resume_keeps_session_id") is True, {"session_start_sources": sources})

    forbidden = _forbidden_paths(records) + _forbidden_paths(runtime)
    check("redacted_receipts", bool(records) and not forbidden and all(
        r.get("receipt_status") == "written_before_handler_action" for r in records),
        {"receipt_count": len(records), "forbidden_saved_key_paths": forbidden}, fail_status="fail")
    check("temporary_cleanup", cleanup.get("temporary_root_removed") is True, {"cleanup": cleanup}, fail_status="fail")

    smoke = capture.get("interactive_smoke") or {}
    check("interactive_smoke", smoke.get("status") == "pass", {"interactive_smoke": smoke})

    if scope == "collection":
        for item in assertions:
            if item["name"] in INJECTION_ASSERTIONS:
                item["required"] = False
    required = [a for a in assertions if a.get("required", True)]
    if capture_status == "blocked_environment":
        status = "blocked_environment"
    elif capture_status != "completed" or any(a["status"] == "fail" for a in required):
        status = "failed_contract"
    elif all(a["status"] == "pass" for a in required):
        status = "passed"
    else:
        status = "inconclusive"
    return {"gate": "G3-CC", "probe_id": capture.get("probe_id"), "status": status, "capture_status": capture_status,
            "scope": scope, "capture_artifact_digest": capture_digest, "required_assertions": len(required),
            "passed_assertions": sum(a["status"] == "pass" for a in required), "assertions": assertions,
            "injection_admitted": scope == "full" and all(a["status"] == "pass" for a in assertions if a["name"] in INJECTION_ASSERTIONS),
            "decision_rule": "passed only when every required assertion is pass; interactive smoke is required; "
                             "collection scope records injection assertions without requiring them"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scope", choices=("full", "collection"), default="full")
    args = parser.parse_args()
    raw = args.capture.read_bytes()
    result = evaluate(json.loads(raw), "sha256:" + hashlib.sha256(raw).hexdigest(), scope=args.scope)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": result["status"], "passed": result["passed_assertions"],
                      "required": result["required_assertions"],
                      "not_passed": [a["name"] for a in result["assertions"] if a["status"] != "pass" and a.get("required", True)],
                      "recorded_not_required": [a["name"] for a in result["assertions"] if not a.get("required", True)]}))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
