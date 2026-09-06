from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from .real_judge import ReplayResult


FROZEN_PROMPT = "openai-judge-prompt-v4"
FROZEN_SCHEMA = "delta-v3"
FROZEN_CONVERTER = "delta-to-proposal-v2"
FROZEN_DATASET = "real-judge-v3"
FROZEN_DATASET_DIGEST = "8ebf785c8c579140236521e0b8e93164741d75f9356bd18b26924e7732dc8565"
FROZEN_TIMEOUT = 300.0
FROZEN_ATTEMPTS = 3
FROZEN_OBSERVATIONS = 42
FROZEN_CASES = 14
FROZEN_REPEATS = 3


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _body(result: ReplayResult) -> dict[str, Any]:
    if not result.actual_deltas:
        return {}
    delta = result.actual_deltas[0]
    return {"key": delta.get("key"), **dict(delta.get("fields") or {})}


def semantic_digests(result: ReplayResult) -> dict[str, str]:
    delta = result.actual_deltas[0] if result.actual_deltas else {}
    proposal = result.actual_proposals[0] if result.actual_proposals else {}
    return {
        "relation": _digest(delta.get("relation")),
        "body": _digest(_body(result)),
        "evidence": _digest(sorted(delta.get("evidence_ids") or [])),
        "conversion": _digest(proposal),
    }


def load_results(path: Path) -> list[ReplayResult]:
    return [ReplayResult.model_validate_json(line) for line in path.read_text().splitlines() if line.strip()]


def admission_errors(results: list[ReplayResult], *, model: str | None = None) -> list[str]:
    errors: list[str] = []
    if len(results) != FROZEN_OBSERVATIONS:
        errors.append(f"observation_count={len(results)} expected={FROZEN_OBSERVATIONS}")
    repeat_counts = Counter(result.repeat_index for result in results)
    case_keys = [(result.repeat_index, result.case_id) for result in results]
    if set(repeat_counts) != set(range(1, FROZEN_REPEATS + 1)):
        errors.append("repeat_set_mismatch")
    if any(count != FROZEN_CASES for count in repeat_counts.values()):
        errors.append("case_count_per_repeat_mismatch")
    if len(set(case_keys)) != len(case_keys):
        errors.append("duplicate_observation_key")
    if model is not None and any(result.model != model for result in results):
        errors.append("model_mismatch")
    if any(result.dataset_id != FROZEN_DATASET for result in results):
        errors.append("dataset_mismatch")
    if any(result.dataset_digest != FROZEN_DATASET_DIGEST for result in results):
        errors.append("dataset_digest_mismatch")
    if any(result.prompt_contract_version != FROZEN_PROMPT for result in results):
        errors.append("prompt_mismatch")
    if any(result.response_schema_version != FROZEN_SCHEMA for result in results):
        errors.append("schema_mismatch")
    if any(result.converter_version != FROZEN_CONVERTER for result in results):
        errors.append("converter_mismatch")
    if any(result.request_timeout_seconds != FROZEN_TIMEOUT for result in results):
        errors.append("timeout_mismatch")
    if any(result.max_attempts != FROZEN_ATTEMPTS for result in results):
        errors.append("attempt_budget_mismatch")
    if any(result.status != "ok" for result in results):
        errors.append("unsuccessful_observation")
    if any(result.attempts_used < 1 or result.attempts_used > result.max_attempts for result in results):
        errors.append("invalid_attempt_count")
    for field in ("relation_correct", "memory_body_correct", "evidence_correct", "conversion_fidelity_correct", "ignore_correct"):
        values = [getattr(result, field) for result in results if getattr(result, field) is not None]
        if values and not all(values):
            errors.append(f"{field}_failure")
    return list(dict.fromkeys(errors))


def audit_models(pro_results: list[ReplayResult], flash_results: list[ReplayResult]) -> dict[str, Any]:
    pro = {(item.repeat_index, item.case_id): item for item in pro_results}
    flash = {(item.repeat_index, item.case_id): item for item in flash_results}
    rows: list[dict[str, Any]] = []
    for key in sorted(set(pro) | set(flash)):
        left, right = pro.get(key), flash.get(key)
        left_digests = semantic_digests(left) if left else None
        right_digests = semantic_digests(right) if right else None
        differences = []
        if left is None or right is None:
            differences.append("missing_observation")
        else:
            differences = [name for name in ("relation", "body", "evidence", "conversion")
                           if left_digests[name] != right_digests[name]]
        rows.append({
            "repeat_index": key[0],
            "case_id": key[1],
            "pro_status": left.status if left else "missing",
            "flash_status": right.status if right else "missing",
            "pro_digests": left_digests,
            "flash_digests": right_digests,
            "differences": differences,
        })
    return {
        "pro_observations": len(pro_results),
        "flash_observations": len(flash_results),
        "aligned_observations": sum(not row["differences"] for row in rows),
        "different_semantics": sum(bool(row["differences"]) for row in rows),
        "difference_dimensions": dict(Counter(
            dimension for row in rows for dimension in row["differences"]
            if dimension != "missing_observation"
        )),
        "rows": rows,
    }


def markdown_audit(audit: dict[str, Any]) -> str:
    lines = [
        "# Pro/Flash Judge Cross-Model Consistency Audit",
        "",
        "> Each model remains an independent evidence stream. Results are compared by aligned case/repeat and never merged.",
        "",
        f"- Pro observations: {audit['pro_observations']}",
        f"- Flash observations: {audit['flash_observations']}",
        f"- Aligned identical semantic digests: {audit['aligned_observations']}",
        f"- Differing or missing observations: {audit['different_semantics']}",
        f"- Difference dimensions: `{json.dumps(audit['difference_dimensions'], ensure_ascii=True, sort_keys=True)}`",
        "",
        "| Repeat | Case | Pro status | Flash status | Relation | Body | Evidence | Conversion |",
        "|---:|---|---|---|---|---|---|---|",
    ]
    for row in audit["rows"]:
        marks = {name: "same" for name in ("relation", "body", "evidence", "conversion")}
        for name in row["differences"]:
            if name in marks:
                marks[name] = "different"
        lines.append(
            f"| {row['repeat_index']} | {row['case_id']} | {row['pro_status']} | {row['flash_status']} | "
            f"{marks['relation']} | {marks['body']} | {marks['evidence']} | {marks['conversion']} |"
        )
    return "\n".join(lines) + "\n"
