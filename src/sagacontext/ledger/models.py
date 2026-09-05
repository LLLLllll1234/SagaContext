from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


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
