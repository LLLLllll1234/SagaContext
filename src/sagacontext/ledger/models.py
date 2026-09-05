from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Scope(BaseModel):
    kind: Literal["global", "project", "path", "task"]
    project_id: str | None = None
    path_pattern: str | None = None
    task_id: str | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> "Scope":
        expected = {
            "global": (False, False, False),
            "project": (True, False, False),
            "path": (True, True, False),
            "task": (True, False, True),
        }[self.kind]
        actual = (self.project_id is not None, self.path_pattern is not None, self.task_id is not None)
        if actual != expected:
            raise ValueError(f"invalid fields for {self.kind} scope")
        return self


class TaskContext(BaseModel):
    owner_id: str
    project_id: str | None = None
    workspace_id: str | None = None
    task_id: str | None = None
    touched_paths: list[str] = Field(default_factory=list)
    stage: Literal["orient", "investigate", "implement", "verify"] = "orient"


class Verification(BaseModel):
    verifier_kind: str
    claim_key: str
    input_fingerprint: str
    environment_fingerprint: str
    expected: dict[str, Any]
    observed: dict[str, Any]
    outcome: Literal["pass", "fail", "inconclusive"]


class EvidenceInput(BaseModel):
    evidence_id: str
    source_event_id: str
    claim_key: str
    evidence_kind: str
    locator: dict[str, Any]
    observed_at: datetime
    verification: Verification | None = None
    redacted_excerpt: str | None = None

    @model_validator(mode="after")
    def validate_verification_claim(self) -> "EvidenceInput":
        if self.verification and self.verification.claim_key != self.claim_key:
            raise ValueError("verification claim_key must match evidence claim_key")
        return self


class CommitRequest(BaseModel):
    receipt: str
    operation: Literal["new", "confirm", "refine", "supersede"]
    memory_type: Literal["profile", "taste", "convention", "decision", "project_map", "gotcha", "task_checkpoint"]
    scope: Scope
    payload: dict[str, Any]
    evidence: list[EvidenceInput] = Field(default_factory=list)
    memory_id: str | None = None
    expected_revision: int | None = None
    source_kind: str = "system"
    payload_schema_version: int = 1

    @model_validator(mode="after")
    def validate_operation(self) -> "CommitRequest":
        if self.operation == "new":
            if self.expected_revision is not None:
                raise ValueError("new operations cannot have expected_revision")
        elif not self.memory_id or self.expected_revision is None:
            raise ValueError("non-new operations require memory_id and expected_revision")
        return self


class MemoryView(BaseModel):
    owner_id: str
    memory_id: str
    revision: int
    memory_type: str
    scope: Scope
    state: Literal["active", "retired", "deleted"]
    conflict_state: Literal["none", "unresolved"]
    payload: dict[str, Any]
    ledger_sequence: int


class CommitResult(BaseModel):
    status: Literal["committed_pending_projection", "committed_local_only", "conflict", "rejected"]
    memory_id: str | None = None
    revision: int | None = None
    ledger_sequence: int
    reason: str | None = None


class BatchMemoryOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    proposal_id: str
    operation: Literal["new", "confirm", "refine", "supersede"]
    memory_type: Literal["profile", "taste", "convention", "decision", "project_map", "gotcha", "task_checkpoint"]
    scope: Scope
    payload_json: str
    memory_id: str | None = None
    expected_revision: int | None = None
    source_kind: str = "reconcile"
    payload_schema_version: int = 1

    @model_validator(mode="after")
    def validate_operation(self) -> "BatchMemoryOperation":
        try:
            payload = json.loads(self.payload_json)
        except json.JSONDecodeError as error:
            raise ValueError("payload_json must be valid JSON") from error
        if not isinstance(payload, dict):
            raise ValueError("payload_json must contain a JSON object")
        if self.operation == "new":
            if self.memory_id is not None or self.expected_revision is not None:
                raise ValueError("new operations cannot target an existing revision")
        elif self.memory_id is None or self.expected_revision is None:
            raise ValueError("non-new operations require memory_id and expected_revision")
        return self


class ExpectedHead(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    memory_id: str
    revision: int


class BatchEvidenceLink(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    proposal_id: str
    evidence_ids: tuple[str, ...]


class BatchCandidateResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str
    claim_token: str
    status: Literal["settled", "awaiting_review", "quarantined"]
    result_ref: str | None = None


class BatchConflictRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    conflict_id: str
    candidate_id: str
    proposal_id: str
    reason: str
    target_id: str | None = None
    base_revision: int | None = None


class BatchTaskUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    touch_last_active: bool = True


class CommitBatchPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    batch_id: str
    proposal_ids: tuple[str, ...]
    expected_heads: tuple[ExpectedHead, ...] = ()
    memory_operations: tuple[BatchMemoryOperation, ...] = ()
    evidence_links: tuple[BatchEvidenceLink, ...] = ()
    candidate_results: tuple[BatchCandidateResult, ...] = ()
    conflict_records: tuple[BatchConflictRecord, ...] = ()
    task_update: BatchTaskUpdate | None = None


class BatchCommitResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["settled", "awaiting_review"]
    batch_id: str
    memory_ids: tuple[str, ...] = ()
