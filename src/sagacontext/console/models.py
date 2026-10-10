from datetime import datetime
from typing import Generic, TypeVar, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


T = TypeVar("T")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Page(_StrictModel, Generic[T]):
    items: list[T]
    next_cursor: str | None = None


class SnapshotMeta(_StrictModel):
    observed_at: datetime
    schema_version: int
    ledger_sequence: int
    request_id: str


class Envelope(_StrictModel, Generic[T]):
    meta: SnapshotMeta
    data: T


class DeploymentStatus(_StrictModel):
    product: Literal['sagacontext']
    version: Literal['1.0.0']
    instance_id: str = Field(min_length=1)
    rollout_mode: Literal['off', 'shadow', 'guarded']
    worker_enabled: bool
    scheduler: Literal['disabled', 'running', 'error', 'unknown']
    stop_active: bool
    openviking_configured: bool
    llm_configured: bool
    console_assets_available: bool


class Module(_StrictModel, Generic[T]):
    availability: Literal['available','unavailable']
    value: T | None = None
    reason: str | None = None


class Workspace(_StrictModel):
    workspace_id: str
    name: str


class Project(_StrictModel):
    project_id: str
    name: str
    workspaces: list[Workspace]


class ProjectDirectory(_StrictModel):
    items: list[Project]


class SessionRow(_StrictModel):
    session_id: str
    host: str
    opened_at: str
    closed_at: str | None = None
    task_id: str | None = None
    last_event_at: str | None = None


class TaskRow(_StrictModel):
    task_id: str
    project_id: str
    goal: str
    status: str
    created_at: str
    last_active: str
    session_id: str | None = None
    checkpoint_at: str | None = None


class BatchRow(_StrictModel):
    batch_id: str
    session_id: str
    task_id: str | None = None
    status: str
    created_at: str
    settled_at: str | None = None
    attempt_count: int = 0
    next_attempt_at: str | None = None
    last_error_class: str | None = None
    rollout_id: str | None = None
    proposal_count: int = 0


class ActivityRow(_StrictModel):
    object_id: str
    kind: str
    recorded_at: str
    occurred_at: str | None = None
    object_type: str
    session_id: str | None = None
    batch_id: str | None = None
    memory_id: str | None = None
    rollout_id: str | None = None
    revision: int | None = None
    status: str


class Observation(_StrictModel):
    yes: int
    no: int
    unknown: int
    labeled_samples: int
    evaluated_samples: int
    yes_rate: float | None


class Latency(_StrictModel):
    samples: int
    p50: float | None
    p95: float | None


class Rollback(_StrictModel):
    states: dict[str,int]
    audit: dict[str,int]


class RolloutDetail(_StrictModel):
    rollout_id: str
    mode: str
    status: str
    started_at: str
    deadline: str
    stop_reason: str | None
    generation: str
    session_reservations: int
    session_limit: int
    candidate_reservations: int
    candidate_limit: int
    batches: dict[str,int]
    reviews: dict[str,int]
    proposals: dict[str,int]
    judge_latency_ms: Latency
    consumption_reports: int
    observations: dict[str,Observation]
    projection_states: dict[str,int]
    rollback: Rollback
    quality_status: str


class RuntimeStatus(_StrictModel):
    configured_mode: str
    persisted_status: str | None
    effective_mode: str
    block_reason: str | None
    scheduler: str
    other_workspace_id: str | None


class Changes(_StrictModel):
    operations: dict[str,int]
    projection_states: dict[str,int]
    start: str
    end: str


class WorkspaceOverview(_StrictModel):
    workspace_id: str
    project_id: str
    runtime: Module[RuntimeStatus]
    tasks: Module[Page[TaskRow]]
    sessions: Module[Page[SessionRow]]
    rollout: Module[RolloutDetail]
    memory_changes: Module[Changes]
    activity: Module[Page[ActivityRow]]


class Source(_StrictModel):
    event_id: str
    session_id: str
    event_kind: str
    received_at: str


class Evidence(_StrictModel):
    evidence_id: str
    kind: str
    excerpt: str | None
    source: Source | None


class MemoryScope(_StrictModel):
    kind: str
    project_id: str | None = None
    path_pattern: str | None = None
    task_id: str | None = None


class Revision(_StrictModel):
    revision: int
    operation: str
    created_at: str
    payload: JsonValue
    evidence: list[Evidence]


class MemoryRow(_StrictModel):
    memory_id: str
    current_revision: int
    memory_type: str
    state: str
    conflict_state: str
    scope_kind: str
    created_at: str
    payload: JsonValue


class MemoryRelation(_StrictModel):
    memory_id: str
    relation: Literal['replaces','replaced_by']


class MemoryDetail(_StrictModel):
    memory_id: str
    current_revision: int
    memory_type: str
    state: str
    conflict_state: str
    scope: MemoryScope
    relations: list[MemoryRelation]
    revisions: list[Revision]


class Proposal(_StrictModel):
    proposal_id: str
    candidate_id: str
    operation: str
    target_id: str | None
    expected_revision: int | None
    status: str
    scope: MemoryScope | None
    old_payload: JsonValue
    new_payload: JsonValue
    rationale: str | None
    evidence: list[Evidence]
    availability: str


class BatchDetail(BatchRow):
    proposals: list[Proposal]


class EventRow(_StrictModel):
    event_id: str
    event_kind: str
    occurred_at: str
    received_at: str


class Candidate(_StrictModel):
    candidate_id: str
    kind: str
    status: str
    active_batch_id: str | None


class BatchLink(_StrictModel):
    batch_id: str
    status: str
    created_at: str


class Consumption(_StrictModel):
    receipt_id: str
    status: str
    created_at: str


class Omission(_StrictModel):
    memory_id: str
    revision: int
    reason: str


class Injection(_StrictModel):
    receipt_id: str
    created_at: str
    status: str | None
    reason: str | None
    memory_ids: list[str]
    revisions: list[int]
    generation: str | None
    ledger_sequence: int | None
    omissions: list[Omission]
    bundle_digest: str | None
    session_digest: str | None
    consumption_records: list[Consumption]


class Binding(_StrictModel):
    task_id: str
    start_event_id: str
    end_event_id: str | None


class SessionDetail(SessionRow):
    events: list[EventRow]
    batches: list[BatchLink]
    candidates: list[Candidate]
    injections: list[Injection]
    bindings: list[Binding]
    truncated: list[str]


SessionPage = Page[SessionRow]
TaskPage = Page[TaskRow]
ActivityPage = Page[ActivityRow]
BatchPage = Page[BatchRow]
MemoryPage = Page[MemoryRow]
