from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..ledger import Scope


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class JournalEvent(FrozenModel):
    host: str
    host_version: str
    session_id: str
    workspace_id: str
    event_kind: Literal[
        "session_opened",
        "user_message",
        "tool_started",
        "tool_finished",
        "checkpoint_requested",
        "compaction_observed",
        "session_closed",
    ]
    occurred_at: datetime
    trust_class: str
    source_generation: str
    source_event_key: str
    source_locator: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)
    parser_version: str
    schema_version: int = 1


class CursorUpdate(FrozenModel):
    host: str
    session_id: str
    source_locator: str
    source_generation: str
    byte_offset: int
    source_fingerprint: str


class EventReceipt(FrozenModel):
    status: Literal["accepted", "duplicate"]
    event_id: str
    ingest_sequence: int


class QuarantineReceipt(FrozenModel):
    status: Literal["partial", "quarantined"]
    quarantine_id: str | None = None


class CandidateInput(FrozenModel):
    session_id: str
    task_id: str | None = None
    kind: str
    memory_type_hint: str
    scope_hint: Scope
    topic_key: str
    event_ids: tuple[str, ...]


class CandidateReceipt(FrozenModel):
    candidate_id: str


class JudgeCandidate(FrozenModel):
    candidate_id: str
    kind: str
    memory_type_hint: str
    scope_hint: Scope
    topic_key: str
    event_ids: tuple[str, ...] = ()
    text: str = ""


class JudgeAnchor(FrozenModel):
    memory_id: str
    revision: int
    memory_type: str
    scope: Scope
    payload: dict[str, Any] = Field(default_factory=dict)


class BatchReceipt(FrozenModel):
    batch_id: str
    candidate_claim_tokens: tuple[str, ...]


class BatchClaim(FrozenModel):
    batch_id: str
    lease_owner: str
    lease_token: str
    lease_until: datetime


class BatchInput(FrozenModel):
    batch_id: str
    input_digest: str
    policy_version: str
    maintenance_schema_version: int
    judge_version: str
    event_ids: tuple[str, ...]
    candidate_ids: tuple[str, ...]
    anchor_revisions: tuple[tuple[str, int], ...]
    judge_candidates: tuple[JudgeCandidate, ...] = ()
    judge_anchors: tuple[JudgeAnchor, ...] = ()
    summary: str = ""


class DeltaProposal(FrozenModel):
    candidate_id: str
    operation: Literal["new", "confirm", "refine", "supersede", "conflict", "no_change"]
    target_id: str | None = None
    expected_revision: int | None = None
    memory_type: str
    scope: Scope
    payload: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: tuple[str, ...] = ()
    rationale: str = ""


class BatchRunResult(FrozenModel):
    status: Literal[
        "idle",
        "retry",
        "proposed",
        "settled",
        "awaiting_review",
        "invalidated",
        "rejected",
        "blocked",
    ]
    batch_id: str | None = None
