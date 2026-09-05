#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


REQUIRED_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "Stop",
    "SessionEnd",
)
FORBIDDEN_SAVED_KEYS = {
    "prompt",
    "tool_input",
    "tool_response",
    "cwd",
    "transcript_path",
    "last_assistant_message",
    "base_url",
    "api_key",
}


def _artifact_ref_for_event(records: list[dict[str, Any]], event: str) -> list[str]:
    return [
        f"payload_shapes[{index}]"
        for index, record in enumerate(records)
        if record.get("hook_event_name") == event
    ][:3]


def _forbidden_paths(value: Any, prefix: str = "$") -> list[str]:
    findings: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}"
            if key.lower() in FORBIDDEN_SAVED_KEYS:
                findings.append(path)
            findings.extend(_forbidden_paths(item, path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            findings.extend(_forbidden_paths(item, f"{prefix}[{index}]"))
    return findings


def evaluate(capture: dict[str, Any], capture_digest: str) -> dict[str, Any]:
    assertions: list[dict[str, Any]] = []

    def check(name: str, status: str, evidence: dict[str, Any]) -> None:
        assertions.append({"name": name, "status": status, "evidence": evidence})

    records = list(capture.get("payload_shapes") or [])
    scenarios = {item.get("name"): item for item in capture.get("scenarios") or []}
    capture_status = (capture.get("probe_result") or {}).get("status")
    check(
        "executable_version_pinned",
        "pass"
        if capture.get("executable_version") == capture.get("pinned_executable_version")
        else "fail",
        {
            "observed": capture.get("executable_version"),
            "expected": capture.get("pinned_executable_version"),
        },
    )
    fingerprint = str(capture.get("config_fingerprint") or "")
    check(
        "config_fingerprint_recorded",
        "pass" if fingerprint.startswith("sha256:") and len(fingerprint) == 71 else "fail",
        {"config_fingerprint": fingerprint},
    )
    check(
        "hooks_runtime_enabled",
        "pass" if (capture.get("runtime_feature_flags") or {}).get("hooks") is True else "fail",
        {"hooks": (capture.get("runtime_feature_flags") or {}).get("hooks")},
    )
    isolation = capture.get("model_runtime") or {}
    cleanup = capture.get("cleanup") or {}
    isolation_ok = (
        capture.get("payload_policy") == "synthetic_only_redacted_receipts"
        and isolation.get("mode") == "temporary_minimal_codex_home"
        and isolation.get("provider_endpoint_saved") is False
        and isolation.get("auth_material_saved") is False
        and cleanup.get("global_config_modified") is False
        and cleanup.get("private_transcript_read") is False
    )
    check(
        "synthetic_isolation",
        "pass" if isolation_ok else "fail",
        {"payload_policy": capture.get("payload_policy"), "model_runtime": isolation, "cleanup": cleanup},
    )
    observed = {
        item.get("event")
        for item in capture.get("verified_events") or []
        if item.get("status") == "observed"
    }
    for event in REQUIRED_EVENTS:
        check(
            f"event_{event}",
            "pass" if event in observed else "not_observed",
            {"artifact_refs": _artifact_ref_for_event(records, event)},
        )
    linked_records = [
        item
        for item in records
        if item.get("session_ref") and item.get("workspace_ref") and item.get("task_ref")
    ]
    tool_records = [
        item
        for item in records
        if item.get("hook_event_name") in {"PreToolUse", "PostToolUse"}
    ]
    linkage_ok = bool(linked_records) and bool(tool_records) and all(
        item.get("tool_call_ref") for item in tool_records
    )
    check(
        "stable_event_linkage",
        "pass" if linkage_ok else "not_observed",
        {
            "linked_receipts": [item.get("occurrence_no") for item in linked_records[:5]],
            "tool_receipts": [item.get("occurrence_no") for item in tool_records[:5]],
        },
    )
    duplicate_counts = Counter(
        item.get("duplicate_group_ref") for item in records if item.get("duplicate_group_ref")
    )
    repeated = sorted(ref for ref, count in duplicate_counts.items() if count >= 2)
    check(
        "duplicate_event_observed",
        "pass" if repeated else "not_observed",
        {"duplicate_group_refs": repeated[:5]},
    )
    abnormal = scenarios.get("hook_nonzero_exit") or {}
    abnormal_receipts = [
        item for item in records if item.get("exit_class") == "scheduled_nonzero_7"
    ]
    abnormal_ok = (
        bool(abnormal_receipts)
        and abnormal.get("exit_code") == 0
        and (abnormal.get("event_counts") or {}).get("PostToolUse", 0) >= 1
    )
    check(
        "hook_nonzero_exit_observed",
        "pass" if abnormal_ok else "not_observed",
        {
            "scenario": abnormal,
            "receipt_occurrences": [item.get("occurrence_no") for item in abnormal_receipts],
        },
    )
    timeout = scenarios.get("hook_timeout") or {}
    timeout_receipts = [
        item for item in records if item.get("timeout_class") == "configured_timeout_target"
    ]
    timeout_ok = (
        bool(timeout_receipts)
        and timeout.get("exit_code") == 0
        and (timeout.get("event_counts") or {}).get("PostToolUse", 0) >= 1
    )
    check(
        "hook_timeout_observed",
        "pass" if timeout_ok else "not_observed",
        {
            "scenario": timeout,
            "receipt_occurrences": [item.get("occurrence_no") for item in timeout_receipts],
            "configured_seconds": (capture.get("timeout_behavior") or {}).get(
                "pre_tool_timeout_configured_seconds"
            ),
        },
    )
    recovery = scenarios.get("restart_recovery") or {}
    recovery_counts = recovery.get("event_counts") or {}
    recovery_ok = (
        recovery.get("exit_code") == 0
        and recovery.get("agent_received_injected_context") is True
        and all(recovery_counts.get(event, 0) >= 1 for event in REQUIRED_EVENTS)
    )
    check(
        "restart_recovery_observed",
        "pass" if recovery_ok else "not_observed",
        {"scenario": recovery},
    )
    baseline = scenarios.get("baseline_with_duplicate") or {}
    injection_ok = (
        baseline.get("agent_received_injected_context") is True
        and (baseline.get("event_counts") or {}).get("SessionStart", 0) >= 1
    )
    check(
        "marker_context_injection",
        "pass" if injection_ok else "not_observed",
        {
            "mode": capture.get("injection_modes"),
            "baseline_agent_received": baseline.get("agent_received_injected_context"),
        },
    )
    safe_degradation_ok = (
        abnormal.get("exit_code") == 0
        and timeout.get("exit_code") == 0
        and recovery.get("exit_code") == 0
    )
    check(
        "failure_degrades_and_recovers",
        "pass" if safe_degradation_ok else "not_observed",
        {
            "nonzero_hook_host_exit": abnormal.get("exit_code"),
            "timeout_hook_host_exit": timeout.get("exit_code"),
            "recovery_host_exit": recovery.get("exit_code"),
        },
    )
    forbidden = _forbidden_paths(capture)
    receipts_valid = bool(records) and all(
        item.get("receipt_status") == "written_before_handler_action"
        and str(item.get("payload_shape_digest", "")).startswith("sha256:")
        for item in records
    )
    redaction_status = (
        "not_observed"
        if not records and not forbidden
        else "pass"
        if receipts_valid and not forbidden
        else "fail"
    )
    check(
        "redacted_receipts",
        redaction_status,
        {
            "receipt_count": len(records),
            "forbidden_saved_key_paths": forbidden,
            "raw_reference_mapping_saved": cleanup.get("raw_reference_mapping_saved"),
        },
    )
    check(
        "temporary_cleanup",
        "pass" if cleanup.get("temporary_root_removed") is True else "fail",
        {"cleanup": cleanup},
    )
    if capture_status == "blocked_environment":
        status = "blocked_environment"
    elif capture_status != "completed":
        status = "failed_contract"
    elif all(item["status"] == "pass" for item in assertions):
        status = "passed"
    elif any(item["status"] == "fail" for item in assertions):
        status = "failed_contract"
    else:
        status = "inconclusive"
    return {
        "gate": "G3",
        "probe_id": capture.get("probe_id"),
        "status": status,
        "capture_status": capture_status,
        "capture_artifact_digest": capture_digest,
        "required_assertions": len(assertions),
        "passed_assertions": sum(item["status"] == "pass" for item in assertions),
        "assertions": assertions,
        "decision_rule": "passed only when every required assertion is pass",
    }


def _write_report(path: Path, capture: dict[str, Any], evaluation: dict[str, Any]) -> None:
    rows = "\n".join(
        f"| `{item['name']}` | **{item['status']}** |" for item in evaluation["assertions"]
    )
    scenario_rows = "\n".join(
        "| `{name}` | {exit_code} | {elapsed:.3f}s | `{blocker}` |".format(
            name=item.get("name"),
            exit_code=item.get("exit_code"),
            elapsed=float(item.get("elapsed_ms", 0)) / 1000,
            blocker=item.get("blocker") or "none",
        )
        for item in capture.get("scenarios") or []
    )
    cleanup = capture.get("cleanup") or {}
    report = f"""# Codex CLI G3 宿主事件准入探针

**Probe ID：** `{evaluation.get('probe_id')}`
**G3 状态：** `{evaluation.get('status')}`
**断言：** {evaluation.get('passed_assertions')}/{evaluation.get('required_assertions')} `pass`

## 固定环境与证据边界

- CLI：`{capture.get('executable_version')}`（固定期望 `{capture.get('pinned_executable_version')}`）
- 模型：`{capture.get('requested_model')}`
- 配置 digest：`{capture.get('config_fingerprint')}`
- Capture digest：`{evaluation.get('capture_artifact_digest')}`
- Payload：仅合成 prompt、固定 marker 和临时 Git 仓库；仅保存字段形状、稳定 probe 内引用、枚举、摘要与耗时。
- 模型运行时：临时最小 `CODEX_HOME`；不保存 provider 地址或认证材料，不加载用户全局 hooks/插件。

## 场景与等待时间

| 场景 | Codex 退出码 | 耗时 | 阻塞分类 |
|---|---:|---:|---|
{scenario_rows}

## 必需断言

| 断言 | 结果 |
|---|---|
{rows}

## 清理

- 临时根目录删除：`{cleanup.get('temporary_root_removed')}`
- 全局配置修改：`{cleanup.get('global_config_modified')}`
- 私人 transcript 读取：`{cleanup.get('private_transcript_read')}`
- 原始 ID 映射保存：`{cleanup.get('raw_reference_mapping_saved')}`

Runner 只生成 capture；本结论由独立 evaluator 读取 capture 后产生。只有全部必需断言为 `pass`，G3 才为 `passed`；模型认证阻塞单独归类为 `blocked_environment`。
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    raw = args.input.read_bytes()
    capture = json.loads(raw)
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    result = evaluate(capture, digest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    if args.report:
        _write_report(args.report, capture, result)
    print(
        json.dumps(
            {
                "probe_id": result["probe_id"],
                "status": result["status"],
                "passed": result["passed_assertions"],
                "required": result["required_assertions"],
            },
            sort_keys=True,
        )
    )
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
