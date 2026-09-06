from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from ..llm import JudgeError, OpenAIJudge
from ..models import Candidate, Delta
from .models import BatchInput, DeltaProposal, JudgeCandidate


CONVERTER_VERSION = "delta-to-proposal-v2"
ALLOWED_MEMORY_TYPES = {
    "profile",
    "taste",
    "convention",
    "decision",
    "project_map",
    "gotcha",
    "task_checkpoint",
}
LAYER_BY_MEMORY_TYPE = {
    "profile": "user",
    "taste": "preference",
    "convention": "preference",
    "decision": "project",
    "project_map": "project",
    "gotcha": "project",
    "task_checkpoint": "task",
}


@dataclass(frozen=True, slots=True)
class JudgeTrace:
    status: str
    latency_ms: int
    response_digest: str | None = None
    error_class: str | None = None
    timeout_phase: str | None = None
    status_code: int | None = None
    error_detail: str | None = None
    auxiliary: tuple[dict[str, Any], ...] = ()
    deltas: tuple[dict[str, Any], ...] = ()


def _digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _candidate_model(candidate: JudgeCandidate) -> Candidate:
    layer = LAYER_BY_MEMORY_TYPE.get(candidate.memory_type_hint, "project")
    return Candidate(
        level="L0",
        layer_guess=layer,
        kind=candidate.kind,
        candidate_id=candidate.candidate_id,
        memory_type_hint=candidate.memory_type_hint,
        scope_hint=candidate.scope_hint.model_dump(mode="json"),
        event_ids=list(candidate.event_ids),
        topic_key=candidate.topic_key,
        text=candidate.text or candidate.topic_key,
    )


def _conversion_error(detail: str) -> JudgeError:
    return JudgeError("judge_conversion_error", False, detail=detail)


def convert_deltas(batch: BatchInput, deltas: list[Delta]) -> tuple[DeltaProposal, ...]:
    candidates = {candidate.candidate_id: candidate for candidate in batch.judge_candidates}
    if set(batch.candidate_ids) != set(candidates):
        raise _conversion_error("candidate context does not match batch")
    if not candidates:
        raise _conversion_error("batch has no candidate context")

    if not deltas:
        if len(candidates) != 1:
            raise _conversion_error("empty delta result requires one candidate")
        candidate = next(iter(candidates.values()))
        return (
            DeltaProposal(
                candidate_id=candidate.candidate_id,
                operation="no_change",
                memory_type=candidate.memory_type_hint,
                scope=candidate.scope_hint,
                evidence_ids=candidate.event_ids,
                rationale="validated_empty_delta",
            ),
        )

    anchors = {anchor.memory_id: anchor for anchor in batch.judge_anchors}
    proposals: list[DeltaProposal] = []
    seen: set[str] = set()
    for delta in deltas:
        candidate_id = delta.candidate_id
        if not candidate_id or candidate_id not in candidates:
            raise _conversion_error("delta references unknown candidate")
        if candidate_id in seen:
            raise _conversion_error("candidate has duplicate deltas")
        seen.add(candidate_id)
        candidate = candidates[candidate_id]
        if delta.layer == "l0":
            delta = delta.model_copy(update={
                "layer": LAYER_BY_MEMORY_TYPE.get(candidate.memory_type_hint, "project")
            })
        if delta.type not in ALLOWED_MEMORY_TYPES or delta.type != candidate.memory_type_hint:
            raise _conversion_error("delta memory type is outside candidate hint")
        if not delta.key.strip():
            raise _conversion_error("delta key is empty")
        if not isinstance(delta.fields, dict):
            raise _conversion_error("delta fields must be an object")
        if "key" in delta.fields:
            if delta.fields["key"] != delta.key:
                raise _conversion_error("delta fields cannot overwrite key")
            delta = delta.model_copy(update={
                "fields": {name: value for name, value in delta.fields.items() if name != "key"}
            })
        try:
            json.dumps(delta.fields, ensure_ascii=True)
        except (TypeError, ValueError) as error:
            raise _conversion_error("delta fields are not JSON serializable") from error

        target_id: str | None = None
        expected_revision: int | None = None
        if delta.relation == "new":
            if delta.anchor_uri is not None:
                raise _conversion_error("new delta cannot reference an anchor")
        else:
            if not delta.anchor_uri or delta.anchor_uri not in anchors:
                raise _conversion_error("delta anchor is not in frozen context")
            anchor = anchors[delta.anchor_uri]
            target_id = anchor.memory_id
            expected_revision = anchor.revision
            # Ledger stores a replacement revision, so a patch would drop old facts.
            if delta.relation == "refine" and not set(anchor.payload).issubset({"key", *delta.fields}):
                raise _conversion_error("refine body omits anchor fields")

        evidence_ids = tuple(delta.evidence_ids)
        if len(evidence_ids) != len(set(evidence_ids)):
            raise _conversion_error("delta evidence contains duplicates")
        if not evidence_ids or not set(evidence_ids).issubset(set(candidate.event_ids)):
            raise _conversion_error("delta evidence is outside candidate events")
        proposals.append(
            DeltaProposal(
                candidate_id=candidate_id,
                operation=delta.relation,
                target_id=target_id,
                expected_revision=expected_revision,
                memory_type=delta.type,
                scope=candidate.scope_hint,
                payload={"key": delta.key, **delta.fields},
                evidence_ids=evidence_ids,
                rationale=delta.rationale[:500],
            )
        )

    if seen != set(candidates):
        raise _conversion_error("non-empty delta result does not cover every candidate")
    return tuple(proposals)


class OpenAIProposalJudge:
    """Synchronous ProposalJudge facade over one async OpenAI-compatible call."""

    version = "openai-proposal-v1"
    converter_version = CONVERTER_VERSION

    def __init__(self, judge: OpenAIJudge):
        self.judge_client = judge
        self.last_trace = JudgeTrace(status="not_run", latency_ms=0)

    def judge(self, batch: BatchInput) -> tuple[DeltaProposal, ...]:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            error = JudgeError("judge_event_loop_error", False, detail="sync facade called inside running loop")
            self.last_trace = JudgeTrace(status="error", latency_ms=0, error_class=error.class_name)
            raise error

        started = perf_counter()
        deltas: list[Delta] | None = None
        try:
            deltas = asyncio.run(self._call(batch))
            proposals = convert_deltas(batch, deltas)
        except JudgeError as error:
            auxiliary = tuple(getattr(self.judge_client, "last_auxiliary", ()))
            self.last_trace = JudgeTrace(
                status="error",
                latency_ms=round((perf_counter() - started) * 1000),
                error_class=error.class_name,
                timeout_phase=error.timeout_phase,
                status_code=error.status_code,
                error_detail=error.detail,
                auxiliary=auxiliary,
                response_digest=error.response_digest or (None if deltas is None else _digest(
                    [delta.model_dump(mode="json") for delta in deltas]
                )),
                deltas=tuple(delta.model_dump(mode="json") for delta in deltas or []),
            )
            raise
        except Exception as error:
            detail = type(error).__name__
            if isinstance(error, (AttributeError, ValueError, KeyError, TypeError)):
                detail += ": " + " ".join(str(error).split())[:160]
            wrapped = JudgeError("judge_error", True, detail=detail)
            self.last_trace = JudgeTrace(
                status="error",
                latency_ms=round((perf_counter() - started) * 1000),
                error_class=wrapped.class_name,
                error_detail=wrapped.detail,
            )
            raise wrapped from error

        self.last_trace = JudgeTrace(
            status="ok",
            latency_ms=round((perf_counter() - started) * 1000),
            status_code=getattr(self.judge_client, "last_status_code", None),
            auxiliary=tuple(getattr(self.judge_client, "last_auxiliary", ())),
            response_digest=_digest([delta.model_dump(mode="json") for delta in deltas]),
            deltas=tuple(delta.model_dump(mode="json") for delta in deltas),
        )
        return proposals

    async def _call(self, batch: BatchInput) -> list[Delta]:
        anchors = []
        for anchor in batch.judge_anchors:
            payload = anchor.model_dump(mode="json")
            payload["anchor_uri"] = anchor.memory_id
            anchors.append(payload)
        candidates = [_candidate_model(candidate) for candidate in batch.judge_candidates]
        return await self.judge_client.judge(anchors, candidates, batch.summary)
