from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from time import perf_counter
from typing import Any, Literal
from uuid import uuid4

import yaml
from pydantic import BaseModel, ConfigDict, Field

from ..llm import JudgeError, OpenAIJudge
from ..maintenance.judge import OpenAIProposalJudge
from ..maintenance.models import BatchInput, DeltaProposal


Relation = Literal["new", "confirm", "refine", "supersede", "conflict", "no_change"]
BODY_SCHEMA_VERSION = "body-schema-v1"
BODY_NORMALIZER_VERSION = "body-normalizer-v1"

BODY_FIELDS: dict[str, frozenset[str]] = {
    "decision": frozenset({"key", "command"}),
    "convention": frozenset({"key", "command"}),
    "gotcha": frozenset({"key", "symptom", "fix", "applies_when"}),
    "taste": frozenset({"key", "format"}),
    "project_map": frozenset({"key", "path"}),
}


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FieldEvidence(FrozenModel):
    source: str
    kind: Literal["quote", "value"]
    expected: Any
    equivalence: str | None = None


class CaseBodySchema(FrozenModel):
    required: tuple[str, ...]
    optional: tuple[str, ...] = ()


class ExpectedContext(FrozenModel):
    target_id: str | None
    expected_revision: int | None
    memory_type: str
    scope: dict[str, Any]


class ReplayCaseV2(FrozenModel):
    id: str
    group: Literal[
        "smoke",
        "duplicate_expression",
        "preference_reversal",
        "insufficient_information",
        "irrelevant_content",
    ]
    category: Relation
    batch: BatchInput
    expected_relation: Relation
    should_ignore: bool
    body_schema: CaseBodySchema
    expected_body: dict[str, Any] | None = None
    acceptable_bodies: tuple[dict[str, Any], ...] = ()
    expected_evidence_ids: tuple[str, ...] = ()
    expected_context: ExpectedContext
    field_evidence: dict[str, tuple[FieldEvidence, ...]] = Field(default_factory=dict)


class LegacyReplayCase(FrozenModel):
    id: str
    category: Relation
    batch: BatchInput
    expected_relation: Relation
    expected_proposal: dict[str, Any] = Field(default_factory=dict)


class ReplayCase(FrozenModel):
    id: str
    group: str
    category: str
    batch: BatchInput
    expected_relation: Relation
    should_ignore: bool
    body_schema: CaseBodySchema
    expected_body: dict[str, Any] | None
    acceptable_bodies: tuple[dict[str, Any], ...]
    expected_evidence_ids: tuple[str, ...]
    expected_context: ExpectedContext
    field_evidence: dict[str, tuple[FieldEvidence, ...]]


class ReplayDataset(FrozenModel):
    dataset_id: str
    schema_version: str
    body_schema_version: str
    body_normalizer_version: str
    dataset_digest: str
    cases: tuple[ReplayCase, ...]


class ReplayResult(BaseModel):
    run_id: str
    repeat_index: int
    dataset_id: str
    dataset_schema_version: str
    dataset_digest: str
    body_schema_version: str
    body_normalizer_version: str
    case_id: str
    group: str
    category: str
    judge_version: str
    prompt_contract_version: str
    response_schema_version: str
    converter_version: str
    model: str
    endpoint_fingerprint: str
    request_timeout_seconds: float
    max_attempts: int
    attempts_used: int = 1
    attempt_errors: list[str] = Field(default_factory=list)
    sampling: dict[str, float]
    case_digest: str
    latency_ms: int
    status: Literal["ok", "error", "blocked_configuration"]
    error_class: str | None = None
    timeout_phase: Literal["connect", "read", "write", "pool", "unknown"] | None = None
    status_code: int | None = None
    error_detail: str | None = None
    auxiliary: list[dict[str, Any]] = Field(default_factory=list)
    response_digest: str | None = None
    actual_deltas: list[dict[str, Any]] = Field(default_factory=list)
    actual_proposals: list[dict[str, Any]] = Field(default_factory=list)
    expected_relation: str
    should_ignore: bool
    relation_correct: bool | None = None
    memory_body_correct: bool | None = None
    evidence_correct: bool | None = None
    conversion_fidelity_correct: bool | None = None
    ignore_correct: bool | None = None
    proposal_semantic_correct: bool | None = None


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _dataset_digest(cases: list[BaseModel]) -> str:
    def strip_compatibility_metadata(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: strip_compatibility_metadata(item)
                for key, item in value.items()
                if not (key == "equivalence" and item is None)
            }
        if isinstance(value, list):
            return [strip_compatibility_metadata(item) for item in value]
        return value

    return _digest(strip_compatibility_metadata([
        case.model_dump(mode="json") for case in cases
    ]))


def _endpoint_fingerprint(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if not normalized:
        return "unconfigured"
    return f"sha256:{hashlib.sha256(normalized.encode()).hexdigest()}"


def _normalize(value: Any) -> Any:
    if isinstance(value, str):
        return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip()
    if isinstance(value, dict):
        return {key: _normalize(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    return value


def _scope_signature(scope: Any) -> dict[str, Any]:
    return scope.model_dump(mode="json", exclude_none=True)


def _resolve_pointer(value: Any, pointer: str) -> Any:
    if not pointer.startswith("/"):
        raise ValueError(f"field evidence pointer must start with '/': {pointer}")
    current = value
    for raw_part in pointer[1:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError) as error:
                raise ValueError(f"field evidence pointer does not exist: {pointer}") from error
        elif isinstance(current, dict) and part in current:
            current = current[part]
        else:
            raise ValueError(f"field evidence pointer does not exist: {pointer}")
    return current


def _validate_body_shape(case: ReplayCaseV2, body: dict[str, Any]) -> None:
    allowed_by_type = BODY_FIELDS.get(case.expected_context.memory_type)
    if allowed_by_type is None:
        raise ValueError(f"no body schema for memory type: {case.expected_context.memory_type}")
    required = set(case.body_schema.required)
    optional = set(case.body_schema.optional)
    if required & optional:
        raise ValueError(f"body schema fields overlap for case: {case.id}")
    if not required.issubset(body) or not set(body).issubset(required | optional):
        raise ValueError(f"body does not match case schema: {case.id}")
    if not (required | optional).issubset(allowed_by_type):
        raise ValueError(f"case schema exceeds memory type schema: {case.id}")
    if not _body_values_valid(case.expected_context.memory_type, body):
        raise ValueError(f"body contains invalid typed values: {case.id}")


def _body_values_valid(memory_type: str, body: dict[str, Any]) -> bool:
    if not body or any(not isinstance(value, str) or not value.strip() for value in body.values()):
        return False
    if memory_type == "taste" and body.get("format") not in {"json", "prose"}:
        return False
    if memory_type == "project_map":
        path = body.get("path")
        if not isinstance(path, str) or "\\" in path:
            return False
        parsed = PurePosixPath(path)
        if parsed.is_absolute() or ".." in parsed.parts:
            return False
    return True


def _validate_v2_case(case: ReplayCaseV2) -> None:
    root = case.model_dump(mode="json")
    if len(case.batch.judge_candidates) != 1:
        raise ValueError(f"v2 replay case must contain one candidate: {case.id}")
    candidate = case.batch.judge_candidates[0]
    if case.expected_context.memory_type != candidate.memory_type_hint:
        raise ValueError(f"expected memory type differs from candidate: {case.id}")
    if case.expected_context.scope != _scope_signature(candidate.scope_hint):
        raise ValueError(f"expected scope differs from candidate: {case.id}")

    if case.expected_relation == "no_change":
        if not case.should_ignore:
            raise ValueError(f"no_change case must set should_ignore: {case.id}")
        if case.expected_body is not None or case.acceptable_bodies or case.expected_evidence_ids:
            raise ValueError(f"no_change case cannot define body or model evidence: {case.id}")
        if case.field_evidence:
            raise ValueError(f"no_change case cannot define field evidence: {case.id}")
    else:
        if case.should_ignore or case.expected_body is None or not case.expected_evidence_ids:
            raise ValueError(f"write case has incomplete annotation: {case.id}")
        bodies = (case.expected_body, *case.acceptable_bodies)
        for body in bodies:
            _validate_body_shape(case, body)
        annotated_fields = set().union(*(set(body) for body in bodies))
        if set(case.field_evidence) != annotated_fields:
            raise ValueError(f"field evidence does not cover annotated body: {case.id}")

    for field_name, entries in case.field_evidence.items():
        if not entries:
            raise ValueError(f"field evidence is empty for {case.id}:{field_name}")
        for entry in entries:
            actual = _resolve_pointer(root, entry.source)
            if entry.kind == "quote":
                if not isinstance(actual, str) or not isinstance(entry.expected, str):
                    raise ValueError(f"quote evidence must point to text: {case.id}:{field_name}")
                if _normalize(entry.expected) not in _normalize(actual):
                    raise ValueError(f"field evidence quote is absent: {case.id}:{field_name}")
            elif _normalize(actual) != _normalize(entry.expected):
                raise ValueError(f"field evidence value differs: {case.id}:{field_name}")

    anchors = {anchor.memory_id: anchor for anchor in case.batch.judge_anchors}
    if case.expected_relation == "new":
        if case.expected_context.target_id is not None or case.expected_context.expected_revision is not None:
            raise ValueError(f"new case cannot target an anchor: {case.id}")
    elif case.expected_relation != "no_change":
        target = anchors.get(case.expected_context.target_id or "")
        if target is None or target.revision != case.expected_context.expected_revision:
            raise ValueError(f"expected target is not a frozen anchor: {case.id}")


def _normalize_v2(case: ReplayCaseV2) -> ReplayCase:
    return ReplayCase(**case.model_dump(mode="python"))


def _validate_v3_case(case: ReplayCaseV2) -> None:
    _validate_v2_case(case)
    if case.category == "refine":
        entries = case.field_evidence.get("applies_when", ())
        if not any(entry.equivalence for entry in entries):
            raise ValueError(f"v3 refine case lacks semantic equivalence evidence: {case.id}")


def _normalize_legacy(case: LegacyReplayCase) -> ReplayCase:
    proposal = case.expected_proposal
    body = proposal.get("payload") if case.expected_relation != "no_change" else None
    body = body if isinstance(body, dict) else None
    required = tuple(body) if body else ()
    return ReplayCase(
        id=case.id,
        group="smoke",
        category=case.category,
        batch=case.batch,
        expected_relation=case.expected_relation,
        should_ignore=case.expected_relation == "no_change",
        body_schema=CaseBodySchema(required=required),
        expected_body=body,
        acceptable_bodies=(),
        expected_evidence_ids=tuple(proposal.get("evidence_ids", ())),
        expected_context=ExpectedContext(
            target_id=proposal.get("target_id"),
            expected_revision=proposal.get("expected_revision"),
            memory_type=str(proposal.get("memory_type", case.batch.judge_candidates[0].memory_type_hint)),
            scope=dict(proposal.get("scope", _scope_signature(case.batch.judge_candidates[0].scope_hint))),
        ),
        field_evidence={},
    )


def load_replay_dataset(path: Path) -> ReplayDataset:
    payload = yaml.safe_load(path.read_text()) or {}
    if not isinstance(payload, dict):
        raise ValueError("replay dataset must be an object")
    entries = payload.get("cases", [])
    if not entries:
        raise ValueError("replay dataset is empty")

    if payload.get("dataset_id"):
        raw_cases = [ReplayCaseV2.model_validate(entry) for entry in entries]
        schema_version = str(payload.get("schema_version", ""))
        for case in raw_cases:
            (_validate_v3_case if schema_version == "real-judge-dataset-v3" else _validate_v2_case)(case)
        frozen_digest = _dataset_digest(raw_cases)
        dataset = ReplayDataset(
            dataset_id=str(payload["dataset_id"]),
            schema_version=str(payload.get("schema_version", "")),
            body_schema_version=str(payload.get("body_schema_version", "")),
            body_normalizer_version=str(payload.get("body_normalizer_version", "")),
            dataset_digest=frozen_digest,
            cases=tuple(_normalize_v2(case) for case in raw_cases),
        )
        if dataset.schema_version not in {"real-judge-dataset-v2", "real-judge-dataset-v3"}:
            raise ValueError("unsupported replay schema version")
        expected_dataset_id = {
            "real-judge-dataset-v2": "real-judge-v2",
            "real-judge-dataset-v3": "real-judge-v3",
        }[dataset.schema_version]
        if dataset.dataset_id != expected_dataset_id:
            raise ValueError("dataset id does not match replay schema version")
        if dataset.body_schema_version != BODY_SCHEMA_VERSION:
            raise ValueError("unsupported body schema version")
        if dataset.body_normalizer_version != BODY_NORMALIZER_VERSION:
            raise ValueError("unsupported body normalizer version")
    else:
        legacy_cases = [LegacyReplayCase.model_validate(entry) for entry in entries]
        frozen_digest = _dataset_digest(legacy_cases)
        dataset = ReplayDataset(
            dataset_id="real-judge-v1",
            schema_version="real-judge-dataset-v1",
            body_schema_version="unversioned",
            body_normalizer_version="unversioned",
            dataset_digest=frozen_digest,
            cases=tuple(_normalize_legacy(case) for case in legacy_cases),
        )

    if len({case.id for case in dataset.cases}) != len(dataset.cases):
        raise ValueError("replay case ids must be unique")
    declared_digest = payload.get("dataset_digest")
    if declared_digest and declared_digest != frozen_digest:
        raise ValueError("replay dataset digest mismatch")
    return dataset


def load_replay_cases(path: Path) -> list[ReplayCase]:
    return list(load_replay_dataset(path).cases)


def _proposal_signature(proposal: DeltaProposal) -> dict[str, Any]:
    return {
        "operation": proposal.operation,
        "target_id": proposal.target_id,
        "expected_revision": proposal.expected_revision,
        "memory_type": proposal.memory_type,
        "scope": _scope_signature(proposal.scope),
        "payload": proposal.payload,
        "evidence_ids": list(proposal.evidence_ids),
    }


def _relation(deltas: list[dict[str, Any]]) -> str | None:
    if not deltas:
        return "no_change"
    return deltas[0].get("relation") if len(deltas) == 1 else None


def _body_matches(case: ReplayCase, deltas: list[dict[str, Any]]) -> bool | None:
    if case.expected_relation == "no_change":
        return None
    if len(deltas) != 1:
        return False
    delta = deltas[0]
    fields = delta.get("fields")
    if not isinstance(fields, dict):
        return False
    actual = {"key": delta.get("key"), **fields}
    required = set(case.body_schema.required)
    optional = set(case.body_schema.optional)
    if not required.issubset(actual) or not set(actual).issubset(required | optional):
        return False
    if not _body_values_valid(case.expected_context.memory_type, actual):
        return False
    expected = (case.expected_body, *case.acceptable_bodies)
    return any(body is not None and _normalize(actual) == _normalize(body) for body in expected)


def _evidence_matches(case: ReplayCase, deltas: list[dict[str, Any]]) -> bool | None:
    if case.expected_relation == "no_change":
        return None
    if len(deltas) != 1:
        return False
    evidence = deltas[0].get("evidence_ids")
    return (
        isinstance(evidence, list)
        and set(evidence) == set(case.expected_evidence_ids)
        and len(evidence) == len(set(evidence))
    )


def _conversion_fidelity(
    case: ReplayCase,
    deltas: list[dict[str, Any]],
    proposals: tuple[DeltaProposal, ...],
) -> bool:
    if not deltas:
        candidate = case.batch.judge_candidates[0]
        expected = {
            "operation": "no_change",
            "target_id": None,
            "expected_revision": None,
            "memory_type": candidate.memory_type_hint,
            "scope": _scope_signature(candidate.scope_hint),
            "payload": {},
            "evidence_ids": list(candidate.event_ids),
        }
    elif len(deltas) == 1:
        delta = deltas[0]
        target_id = None
        expected_revision = None
        if delta.get("relation") != "new":
            anchors = {anchor.memory_id: anchor for anchor in case.batch.judge_anchors}
            anchor = anchors.get(str(delta.get("anchor_uri")))
            if anchor is None:
                return False
            target_id = anchor.memory_id
            expected_revision = anchor.revision
        candidate = case.batch.judge_candidates[0]
        expected = {
            "operation": delta.get("relation"),
            "target_id": target_id,
            "expected_revision": expected_revision,
            "memory_type": delta.get("type"),
            "scope": _scope_signature(candidate.scope_hint),
            "payload": {"key": delta.get("key"), **dict(delta.get("fields", {}))},
            "evidence_ids": list(delta.get("evidence_ids", [])),
        }
    else:
        return False
    return len(proposals) == 1 and _proposal_signature(proposals[0]) == expected


def _context_matches(case: ReplayCase, proposals: tuple[DeltaProposal, ...]) -> bool:
    if len(proposals) != 1:
        return False
    proposal = proposals[0]
    expected = case.expected_context
    return (
        proposal.target_id == expected.target_id
        and proposal.expected_revision == expected.expected_revision
        and proposal.memory_type == expected.memory_type
        and _scope_signature(proposal.scope) == expected.scope
    )


def run_replay(
    dataset: ReplayDataset,
    adapter: OpenAIProposalJudge,
    *,
    repeats: int = 3,
    max_attempts: int = 1,
    run_id: str | None = None,
) -> list[ReplayResult]:
    if repeats < 1:
        raise ValueError("repeats must be positive")
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    run_id = run_id or f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    endpoint_fingerprint = _endpoint_fingerprint(
        str(getattr(adapter.judge_client, "base_url", ""))
    )
    request_timeout_seconds = float(getattr(adapter.judge_client, "timeout", 0.0))
    results: list[ReplayResult] = []
    for repeat_index in range(1, repeats + 1):
        for case in dataset.cases:
            started = perf_counter()
            actual: tuple[DeltaProposal, ...] = ()
            error_class: str | None = None
            status: Literal["ok", "error", "blocked_configuration"] = "ok"
            attempt_errors: list[str] = []
            attempts_used = 0
            for attempt in range(1, max_attempts + 1):
                attempts_used = attempt
                try:
                    actual = adapter.judge(case.batch)
                    status = "ok"
                    error_class = None
                    break
                except JudgeError as error:
                    error_class = error.class_name
                    status = "blocked_configuration" if error.class_name == "judge_configuration_error" else "error"
                    attempt_errors.append(error.class_name)
                    if not error.retryable or attempt == max_attempts:
                        break

            deltas = list(adapter.last_trace.deltas)
            relation_correct = None
            body_correct = None
            evidence_correct = None
            conversion_correct = None
            ignore_correct = None
            proposal_correct = None
            if status == "ok":
                relation_correct = _relation(deltas) == case.expected_relation
                body_correct = _body_matches(case, deltas)
                evidence_correct = _evidence_matches(case, deltas)
                conversion_correct = _conversion_fidelity(case, deltas, actual)
                ignore_correct = not deltas if case.should_ignore else None
                applicable = [relation_correct, body_correct, evidence_correct]
                proposal_correct = all(
                    item for item in applicable if item is not None
                ) and _context_matches(case, actual)

            results.append(
                ReplayResult(
                    run_id=run_id,
                    repeat_index=repeat_index,
                    dataset_id=dataset.dataset_id,
                    dataset_schema_version=dataset.schema_version,
                    dataset_digest=dataset.dataset_digest,
                    body_schema_version=dataset.body_schema_version,
                    body_normalizer_version=dataset.body_normalizer_version,
                    case_id=case.id,
                    group=case.group,
                    category=case.category,
                    judge_version=adapter.version,
                    prompt_contract_version=adapter.judge_client.prompt_contract_version,
                    response_schema_version=adapter.judge_client.response_schema_version,
                    converter_version=adapter.converter_version,
                    model=adapter.judge_client.model or "unconfigured",
                    endpoint_fingerprint=endpoint_fingerprint,
                    request_timeout_seconds=request_timeout_seconds,
                    max_attempts=max_attempts,
                    attempts_used=attempts_used,
                    attempt_errors=attempt_errors,
                    sampling={"temperature": float(getattr(adapter.judge_client, "temperature", 0.0))},
                    case_digest=_digest(case.model_dump(mode="json")),
                    latency_ms=round((perf_counter() - started) * 1000),
                    status=status,
                    error_class=error_class,
                    timeout_phase=adapter.last_trace.timeout_phase,
                    status_code=adapter.last_trace.status_code,
                    error_detail=adapter.last_trace.error_detail,
                    auxiliary=list(adapter.last_trace.auxiliary),
                    response_digest=adapter.last_trace.response_digest,
                    actual_deltas=deltas,
                    actual_proposals=[_proposal_signature(item) for item in actual],
                    expected_relation=case.expected_relation,
                    should_ignore=case.should_ignore,
                    relation_correct=relation_correct,
                    memory_body_correct=body_correct,
                    evidence_correct=evidence_correct,
                    conversion_fidelity_correct=conversion_correct,
                    ignore_correct=ignore_correct,
                    proposal_semantic_correct=proposal_correct,
                )
            )
    return results


def write_results(results: list[ReplayResult], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = (
        json.dumps(result.model_dump(mode="json"), ensure_ascii=True) + "\n"
        for result in results
    )
    path.write_text("".join(lines))


def _score(results: list[ReplayResult], field: str) -> str:
    values = [getattr(result, field) for result in results if getattr(result, field) is not None]
    return "n/a" if not values else f"{sum(values)}/{len(values)}"


def _acceptance_status(results: list[ReplayResult]) -> str:
    if not results or any(result.status != "ok" for result in results):
        return "blocked"
    by_case: dict[str, list[ReplayResult]] = defaultdict(list)
    by_repeat: dict[int, list[ReplayResult]] = defaultdict(list)
    for result in results:
        by_case[result.case_id].append(result)
        by_repeat[result.repeat_index].append(result)
    if any(len(case_results) != 3 for case_results in by_case.values()):
        return "incomplete"
    for case_results in by_case.values():
        for field in ("relation_correct", "memory_body_correct", "evidence_correct"):
            values = [getattr(item, field) for item in case_results if getattr(item, field) is not None]
            if values and sum(values) < 2:
                return "failed"
        for field in ("conversion_fidelity_correct", "ignore_correct"):
            values = [getattr(item, field) for item in case_results if getattr(item, field) is not None]
            if values and not all(values):
                return "failed"
    for repeat_results in by_repeat.values():
        for field, threshold in (
            ("relation_correct", 0.9),
            ("memory_body_correct", 0.9),
            ("evidence_correct", 1.0),
            ("conversion_fidelity_correct", 1.0),
            ("ignore_correct", 1.0),
        ):
            values = [getattr(item, field) for item in repeat_results if getattr(item, field) is not None]
            if values and sum(values) / len(values) < threshold:
                return "failed"
    return "passed"


def markdown_report(results: list[ReplayResult]) -> str:
    if not results:
        raise ValueError("cannot report an empty replay")
    case_ids = list(dict.fromkeys(result.case_id for result in results))
    repeats = sorted({result.repeat_index for result in results})
    ok = sum(result.status == "ok" for result in results)
    lines = [
        "# Real Judge Semantic Acceptance Report",
        "",
        "> This report measures a frozen synthetic/de-identified replay set. "
        "It is not an arbitrary-session quality metric.",
        "",
        f"- Run ID: {results[0].run_id}",
        f"- Dataset: {results[0].dataset_id} ({results[0].dataset_digest})",
        f"- Cases: {len(case_ids)}",
        f"- Repeats: {len(repeats)}",
        f"- Model: {results[0].model}",
        f"- Prompt contract: {results[0].prompt_contract_version}",
        f"- Response schema: {results[0].response_schema_version}",
        f"- Converter: {results[0].converter_version}",
        f"- Endpoint fingerprint: {results[0].endpoint_fingerprint}",
        f"- Request timeout: {results[0].request_timeout_seconds:g}s",
        f"- Max attempts per observation: {results[0].max_attempts}",
        f"- Attempts used: {sum(result.attempts_used for result in results)}/{len(results)} observations recorded",
        "- Token usage: unavailable",
        "- Cost: unavailable",
        f"- Judge calls successful: {ok}/{len(results)}",
        f"- Acceptance: {_acceptance_status(results)}",
        "",
        "## Per-repeat metrics",
        "",
        "| Repeat | Relation | Body | Evidence | Conversion fidelity | Ignore |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for repeat_index in repeats:
        selected = [result for result in results if result.repeat_index == repeat_index]
        lines.append(
            f"| {repeat_index} | {_score(selected, 'relation_correct')} | "
            f"{_score(selected, 'memory_body_correct')} | {_score(selected, 'evidence_correct')} | "
            f"{_score(selected, 'conversion_fidelity_correct')} | {_score(selected, 'ignore_correct')} |"
        )
    lines.extend(
        [
            "",
            "## Per-case stability",
            "",
            "| Case | Group | Status | Relation | Body | Evidence | "
            "Conversion fidelity | Ignore | Proposal semantic |",
            "|---|---|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for case_id in case_ids:
        selected = [result for result in results if result.case_id == case_id]
        status = "ok" if all(item.status == "ok" for item in selected) else ",".join(
            sorted({item.error_class or item.status for item in selected if item.status != "ok"})
        )
        lines.append(
            f"| {case_id} | {selected[0].group} | {status} | "
            f"{_score(selected, 'relation_correct')} | {_score(selected, 'memory_body_correct')} | "
            f"{_score(selected, 'evidence_correct')} | {_score(selected, 'conversion_fidelity_correct')} | "
            f"{_score(selected, 'ignore_correct')} | {_score(selected, 'proposal_semantic_correct')} |"
        )
    failures = [result for result in results if result.status != "ok"]
    if failures:
        lines.extend([
            "", "## Call failures", "",
            "| Repeat | Case | Error | HTTP status | Detail | Timeout phase | Latency (ms) |",
            "|---:|---|---|---:|---|---|---:|",
        ])
        for result in failures:
            lines.append(
                f"| {result.repeat_index} | {result.case_id} | {result.error_class} | "
                f"{result.status_code or '-'} | {result.error_detail or 'unavailable'} | "
                f"{result.timeout_phase or 'unavailable'} | {result.latency_ms} |"
            )
    return "\n".join(lines) + "\n"


def build_adapter(base_url: str, api_key: str, model: str, timeout: float = 5.0) -> OpenAIProposalJudge:
    return OpenAIProposalJudge(OpenAIJudge(base_url, api_key, model, timeout=timeout, temperature=0.0))
