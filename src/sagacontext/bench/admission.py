from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from ..llm import JudgeError
from ..maintenance.judge import convert_deltas
from ..models import Delta
from .real_judge import (
    ReplayResult, canonical_body, case_digest, load_replay_dataset,
    score_observation, _proposal_signature,
)


FROZEN_PROMPT = "openai-judge-prompt-v6"
FROZEN_SCHEMA = "delta-v3"
FROZEN_CONVERTER = "delta-to-proposal-v2"
FROZEN_DATASET = "real-judge-v4"
FROZEN_DATASET_DIGEST = "c07572973024b3b5227ee09b4fa2540c8eae6e313cc9da7cb65d714c31a05138"
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


def frozen_dataset(contract: str = "v4"):
    if contract not in {"v3", "v4"}:
        raise ValueError("unknown admission contract")
    return load_replay_dataset(
        Path(__file__).resolve().parents[3] / f"bench/cases/real_judge/cases-{contract}.yaml"
    )


def admission_errors(
    results: list[ReplayResult], *, model: str | None = None, contract: str = "v4",
) -> list[str]:
    dataset = frozen_dataset(contract)
    prompt = FROZEN_PROMPT if contract == "v4" else "openai-judge-prompt-v4"
    frozen_digest = FROZEN_DATASET_DIGEST if contract == "v4" else (
        "8ebf785c8c579140236521e0b8e93164741d75f9356bd18b26924e7732dc8565"
    )
    errors: list[str] = []
    if dataset.dataset_digest != frozen_digest:
        errors.append("frozen_dataset_changed")
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
    expected_keys = {(repeat, case.id) for repeat in range(1, FROZEN_REPEATS + 1) for case in dataset.cases}
    if set(case_keys) != expected_keys:
        errors.append("case_matrix_mismatch")
    if len({result.model for result in results}) != 1:
        errors.append("mixed_or_missing_model")
    if len({result.run_id for result in results}) != 1:
        errors.append("mixed_or_missing_run")
    if len({result.endpoint_fingerprint for result in results}) != 1:
        errors.append("mixed_or_missing_endpoint")
    if model is not None and any(result.model != model for result in results):
        errors.append("model_mismatch")
    if any(result.dataset_id != dataset.dataset_id for result in results):
        errors.append("dataset_mismatch")
    if any(result.dataset_digest != frozen_digest for result in results):
        errors.append("dataset_digest_mismatch")
    if any(result.prompt_contract_version != prompt for result in results):
        errors.append("prompt_mismatch")
    if any(result.response_schema_version != FROZEN_SCHEMA for result in results):
        errors.append("schema_mismatch")
    if any(result.converter_version != FROZEN_CONVERTER for result in results):
        errors.append("converter_mismatch")
    for field, value in (
        ("dataset_schema_version", dataset.schema_version),
        ("body_schema_version", dataset.body_schema_version),
        ("body_normalizer_version", dataset.body_normalizer_version),
        ("judge_version", "openai-proposal-v1"),
        ("sampling", {"temperature": 0.0}),
    ):
        if any(getattr(result, field) != value for result in results):
            errors.append(f"{field}_mismatch")
    if any(result.request_timeout_seconds != FROZEN_TIMEOUT for result in results):
        errors.append("timeout_mismatch")
    if any(result.max_attempts != FROZEN_ATTEMPTS for result in results):
        errors.append("attempt_budget_mismatch")
    if any(result.status != "ok" for result in results):
        errors.append("unsuccessful_observation")
    if any(result.attempts_used < 1 or result.attempts_used > result.max_attempts for result in results):
        errors.append("invalid_attempt_count")
    if any(len(result.attempt_errors) != result.attempts_used - (result.status == "ok") for result in results):
        errors.append("attempt_history_mismatch")
    if any(result.status == "ok" and result.error_class is not None for result in results):
        errors.append("successful_observation_has_error")
    cases = {case.id: case for case in dataset.cases}
    for result in results:
        case = cases.get(result.case_id)
        if case is None:
            continue
        if result.case_digest != case_digest(case):
            errors.append("case_digest_mismatch")
        if result.expected_relation != case.expected_relation or result.should_ignore != case.should_ignore:
            errors.append("expected_annotation_mismatch")
        if result.status != "ok":
            continue
        try:
            proposals = convert_deltas(case.batch, [Delta.model_validate(delta) for delta in result.actual_deltas])
            if [_proposal_signature(item) for item in proposals] != result.actual_proposals:
                errors.append("proposal_replay_mismatch")
            scores = score_observation(case, result.actual_deltas, proposals)
        except (JudgeError, ValueError, TypeError, KeyError):
            errors.append("invalid_observation_payload")
            continue
        for field, expected in scores.items():
            if getattr(result, field) is not expected:
                errors.append(f"{field}_score_mismatch")
            if expected is False:
                errors.append(f"{field}_failure")
    for field in ("relation_correct", "memory_body_correct", "evidence_correct", "conversion_fidelity_correct", "ignore_correct", "proposal_semantic_correct"):
        values = [getattr(result, field) for result in results if getattr(result, field) is not None]
        if values and not all(values):
            errors.append(f"{field}_failure")
    return list(dict.fromkeys(errors))


def audit_models(
    pro_results: list[ReplayResult], flash_results: list[ReplayResult], *, contract: str = "v4",
) -> dict[str, Any]:
    dataset = frozen_dataset(contract)
    cases = {case.id: case for case in dataset.cases}
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
        equivalent = False
        case = cases.get(key[1])
        if case is not None and left is not None and right is not None:
            valid = all(
                item.status == "ok" and item.dataset_digest == dataset.dataset_digest
                and item.case_digest == case_digest(case)
                and item.body_normalizer_version == dataset.body_normalizer_version
                and item.proposal_semantic_correct is True
                and item.conversion_fidelity_correct is True
                for item in (left, right)
            )
            if valid:
                for item in (left, right):
                    try:
                        proposals = convert_deltas(case.batch, [Delta.model_validate(d) for d in item.actual_deltas])
                        scores = score_observation(case, item.actual_deltas, proposals)
                        valid = valid and [_proposal_signature(p) for p in proposals] == item.actual_proposals
                        valid = valid and all(
                            value is not False and getattr(item, name) is value for name, value in scores.items()
                        )
                    except (JudgeError, ValueError, TypeError, KeyError):
                        valid = False
            if valid:
                compared = []
                for item in (left, right):
                    proposal = dict(item.actual_proposals[0]) if item.actual_proposals else {}
                    proposal["payload"] = canonical_body(case, item.actual_deltas)
                    compared.append(proposal)
                equivalent = compared[0] == compared[1]
        rows.append({
            "repeat_index": key[0],
            "case_id": key[1],
            "pro_status": left.status if left else "missing",
            "flash_status": right.status if right else "missing",
            "pro_digests": left_digests,
            "flash_digests": right_digests,
            "differences": differences,
            "semantically_equivalent": equivalent,
        })
    return {
        "pro_observations": len(pro_results),
        "flash_observations": len(flash_results),
        "aligned_observations": sum(not row["differences"] for row in rows),
        "different_semantics": sum(bool(row["differences"]) for row in rows),
        "semantic_equivalent_observations": sum(row["semantically_equivalent"] for row in rows),
        "semantic_unresolved_observations": sum(not row["semantically_equivalent"] for row in rows),
        "comparison_normalizer": dataset.body_normalizer_version,
        "pro_admission_errors": admission_errors(pro_results, contract=contract),
        "flash_admission_errors": admission_errors(flash_results, contract=contract),
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
        f"- Aligned identical raw digests: {audit['aligned_observations']}",
        f"- Differing or missing raw digests: {audit['different_semantics']}",
        f"- Semantically equivalent observations: {audit['semantic_equivalent_observations']}",
        f"- Unresolved or non-equivalent observations: {audit['semantic_unresolved_observations']}",
        f"- Comparison normalizer: {audit['comparison_normalizer']}",
        f"- Pro admission: {'blocked' if audit['pro_admission_errors'] else 'passed'}",
        f"- Flash admission: {'blocked' if audit['flash_admission_errors'] else 'passed'}",
        f"- Difference dimensions: `{json.dumps(audit['difference_dimensions'], ensure_ascii=True, sort_keys=True)}`",
        "",
        "| Repeat | Case | Pro status | Flash status | Relation | Body | Evidence | Conversion | Semantic equivalent |",
        "|---:|---|---|---|---|---|---|---|---|",
    ]
    for row in audit["rows"]:
        marks = {name: "same" for name in ("relation", "body", "evidence", "conversion")}
        for name in row["differences"]:
            if name in marks:
                marks[name] = "different"
        lines.append(
            f"| {row['repeat_index']} | {row['case_id']} | {row['pro_status']} | {row['flash_status']} | "
            f"{marks['relation']} | {marks['body']} | {marks['evidence']} | {marks['conversion']} | "
            f"{'yes' if row['semantically_equivalent'] else 'unresolved'} |"
        )
    return "\n".join(lines) + "\n"
