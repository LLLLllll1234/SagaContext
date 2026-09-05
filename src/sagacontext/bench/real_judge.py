from __future__ import annotations

import hashlib
import json
from pathlib import Path
from time import perf_counter
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from ..llm import JudgeError, OpenAIJudge
from ..maintenance.judge import OpenAIProposalJudge
from ..maintenance.models import BatchInput, DeltaProposal


class ReplayCase(BaseModel):
    id: str
    category: Literal["new", "confirm", "refine", "supersede", "conflict", "no_change"]
    batch: BatchInput
    expected_relation: str
    expected_proposal: dict[str, Any] = Field(default_factory=dict)


class ReplayResult(BaseModel):
    case_id: str
    category: str
    judge_version: str
    prompt_contract_version: str
    schema_version: str
    converter_version: str
    model: str
    sampling: dict[str, float]
    case_digest: str
    latency_ms: int
    status: Literal["ok", "error", "blocked_configuration"]
    error_class: str | None = None
    response_digest: str | None = None
    actual_deltas: list[dict[str, Any]] = Field(default_factory=list)
    actual_proposals: list[dict[str, Any]] = Field(default_factory=list)
    expected_relation: str
    relation_correct: bool = False
    conversion_correct: bool = False


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def load_replay_cases(path: Path) -> list[ReplayCase]:
    payload = yaml.safe_load(path.read_text()) or {}
    entries = payload if isinstance(payload, list) else payload.get("cases", [])
    cases = [ReplayCase.model_validate(entry) for entry in entries]
    if not cases:
        raise ValueError("replay dataset is empty")
    if len({case.id for case in cases}) != len(cases):
        raise ValueError("replay case ids must be unique")
    frozen_digest = _digest([case.model_dump(mode="json") for case in cases])
    if isinstance(payload, dict) and payload.get("dataset_digest") and payload["dataset_digest"] != frozen_digest:
        raise ValueError("replay dataset digest mismatch")
    return cases


def _proposal_signature(proposal: DeltaProposal) -> dict[str, Any]:
    return {
        "operation": proposal.operation,
        "target_id": proposal.target_id,
        "expected_revision": proposal.expected_revision,
        "memory_type": proposal.memory_type,
        "scope": proposal.scope.model_dump(mode="json"),
        "payload": proposal.payload,
        "evidence_ids": list(proposal.evidence_ids),
    }


def _conversion_matches(actual: tuple[DeltaProposal, ...], expected: dict[str, Any]) -> bool:
    return len(actual) == 1 and _proposal_signature(actual[0]) == expected


def run_replay(cases: list[ReplayCase], adapter: OpenAIProposalJudge) -> list[ReplayResult]:
    results: list[ReplayResult] = []
    for case in cases:
        started = perf_counter()
        case_digest = _digest(case.model_dump(mode="json"))
        actual: tuple[DeltaProposal, ...] = ()
        error_class: str | None = None
        status: Literal["ok", "error", "blocked_configuration"] = "ok"
        try:
            actual = adapter.judge(case.batch)
        except JudgeError as error:
            error_class = error.class_name
            status = "blocked_configuration" if error.class_name == "judge_configuration_error" else "error"
        actual_relation = actual[0].operation if len(actual) == 1 else None
        results.append(
            ReplayResult(
                case_id=case.id,
                category=case.category,
                judge_version=adapter.version,
                prompt_contract_version=adapter.judge_client.prompt_contract_version,
                schema_version=adapter.judge_client.response_schema_version,
                converter_version=adapter.converter_version,
                model=adapter.judge_client.model or "unconfigured",
                sampling={"temperature": 0.1},
                case_digest=case_digest,
                latency_ms=round((perf_counter() - started) * 1000),
                status=status,
                error_class=error_class,
                response_digest=adapter.last_trace.response_digest,
                actual_deltas=list(adapter.last_trace.deltas),
                actual_proposals=[_proposal_signature(item) for item in actual],
                expected_relation=case.expected_relation,
                relation_correct=status == "ok" and actual_relation == case.expected_relation,
                conversion_correct=status == "ok" and _conversion_matches(actual, case.expected_proposal),
            )
        )
    return results


def write_results(results: list[ReplayResult], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(result.model_dump(mode="json"), ensure_ascii=True) + "\n" for result in results))


def markdown_report(results: list[ReplayResult]) -> str:
    total = len(results)
    ok = sum(result.status == "ok" for result in results)
    evaluated = [result for result in results if result.status == "ok"]
    relation = sum(result.relation_correct for result in evaluated)
    conversion = sum(result.conversion_correct for result in evaluated)
    relation_text = "n/a" if not evaluated else f"{relation}/{len(evaluated)}"
    conversion_text = "n/a" if not evaluated else f"{conversion}/{len(evaluated)}"
    lines = [
        "# Real Judge Replay Report",
        "",
        "> This report measures a frozen synthetic/de-identified replay set. It is not an arbitrary-session quality metric.",
        "",
        f"- Cases: {total}",
        f"- Judge calls successful: {ok}/{total}",
        f"- Relation accuracy (successful calls only): {relation_text}",
        f"- Conversion accuracy (successful calls only): {conversion_text}",
        "",
        "| Case | Category | Status | Error | Relation | Conversion | Latency (ms) |",
        "|---|---|---|---|---:|---:|---:|",
    ]
    for result in results:
        relation_cell = str(int(result.relation_correct)) if result.status == "ok" else "-"
        conversion_cell = str(int(result.conversion_correct)) if result.status == "ok" else "-"
        lines.append(
            f"| {result.case_id} | {result.category} | {result.status} | "
            f"{result.error_class or ''} | {relation_cell} | "
            f"{conversion_cell} | {result.latency_ms} |"
        )
    return "\n".join(lines) + "\n"


def build_adapter(base_url: str, api_key: str, model: str, timeout: float = 5.0) -> OpenAIProposalJudge:
    return OpenAIProposalJudge(OpenAIJudge(base_url, api_key, model, timeout=timeout))
