SCHEMA_VERSION = 4

MIGRATION_1 = """
CREATE TABLE owners(
    owner_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);
CREATE TABLE projects(
    project_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES owners(owner_id),
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE project_locations(
    location_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES owners(owner_id),
    project_id TEXT NOT NULL REFERENCES projects(project_id),
    workspace_id TEXT NOT NULL,
    realpath TEXT NOT NULL,
    git_common_dir TEXT,
    UNIQUE(owner_id, realpath)
);
CREATE TABLE tasks(
    task_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES owners(owner_id),
    project_id TEXT NOT NULL REFERENCES projects(project_id),
    goal TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active','paused','completed','abandoned')),
    created_at TEXT NOT NULL,
    last_active TEXT NOT NULL
);
CREATE TABLE sessions(
    session_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES owners(owner_id),
    host TEXT NOT NULL,
    host_session_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    UNIQUE(owner_id, host, host_session_id)
);
CREATE TABLE task_bindings(
    binding_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES owners(owner_id),
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    start_event_id TEXT NOT NULL,
    end_event_id TEXT,
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX one_open_task_per_session ON task_bindings(session_id) WHERE end_event_id IS NULL;
CREATE TABLE memories(
    memory_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES owners(owner_id),
    current_revision INTEGER NOT NULL,
    memory_type TEXT NOT NULL,
    scope_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('active','retired','deleted')),
    conflict_state TEXT NOT NULL CHECK(conflict_state IN ('none','unresolved')),
    ledger_sequence INTEGER NOT NULL
);
CREATE TABLE revisions(
    memory_id TEXT NOT NULL REFERENCES memories(memory_id),
    revision INTEGER NOT NULL,
    operation TEXT NOT NULL,
    payload_schema_version INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    PRIMARY KEY(memory_id, revision)
);
CREATE TABLE evidence(
    evidence_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES owners(owner_id),
    source_event_id TEXT NOT NULL,
    claim_key TEXT NOT NULL,
    evidence_kind TEXT NOT NULL,
    locator_json TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    verification_json TEXT,
    redacted_excerpt TEXT,
    UNIQUE(owner_id, source_event_id, claim_key)
);
CREATE TABLE revision_evidence(
    memory_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id),
    claim_key TEXT NOT NULL,
    PRIMARY KEY(memory_id, revision, evidence_id, claim_key),
    FOREIGN KEY(memory_id, revision) REFERENCES revisions(memory_id, revision)
);
CREATE TABLE backend_generations(
    backend TEXT NOT NULL,
    generation TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(backend, generation)
);
CREATE TABLE outbox(
    outbox_id INTEGER PRIMARY KEY AUTOINCREMENT,
    backend TEXT NOT NULL,
    generation TEXT NOT NULL,
    memory_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    action TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    UNIQUE(backend, generation, memory_id, revision, action)
);
CREATE TABLE commit_receipts(
    owner_id TEXT NOT NULL,
    receipt TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(owner_id, receipt)
);
CREATE TABLE deletion_jobs(
    job_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    memory_id TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE suppression_rules(
    suppression_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    memory_id TEXT NOT NULL,
    source_claim_digest TEXT,
    scope_json TEXT NOT NULL,
    topic_digest TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE ledger_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO ledger_meta(key, value) VALUES ('sequence', '0');
"""

MIGRATION_2 = """
CREATE TABLE events(
    event_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES owners(owner_id),
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    workspace_id TEXT NOT NULL,
    host TEXT NOT NULL,
    host_version TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    event_kind TEXT NOT NULL CHECK(event_kind IN (
        'session_opened','user_message','tool_started','tool_finished',
        'checkpoint_requested','compaction_observed','session_closed'
    )),
    occurred_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    trust_class TEXT NOT NULL,
    source_generation TEXT NOT NULL,
    source_event_key TEXT NOT NULL,
    source_locator_json TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    ingest_sequence INTEGER NOT NULL,
    UNIQUE(owner_id,host,session_id,source_generation,source_event_key)
);
CREATE TABLE source_cursors(
    owner_id TEXT NOT NULL REFERENCES owners(owner_id),
    host TEXT NOT NULL,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    source_locator TEXT NOT NULL,
    source_generation TEXT NOT NULL,
    byte_offset INTEGER NOT NULL,
    source_fingerprint TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(owner_id,host,session_id,source_locator,source_generation)
);
CREATE TABLE event_aliases(
    owner_id TEXT NOT NULL REFERENCES owners(owner_id),
    host TEXT NOT NULL,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    source_generation TEXT NOT NULL,
    alias_event_key TEXT NOT NULL,
    canonical_event_id TEXT NOT NULL REFERENCES events(event_id),
    alias_kind TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(owner_id,host,session_id,source_generation,alias_event_key)
);
CREATE TABLE event_quarantine(
    quarantine_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES owners(owner_id),
    host TEXT NOT NULL,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    source_generation TEXT NOT NULL,
    source_locator_digest TEXT NOT NULL,
    byte_start INTEGER NOT NULL,
    byte_end INTEGER NOT NULL,
    payload_digest TEXT NOT NULL,
    error_class TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(owner_id,source_generation,source_locator_digest,byte_start,byte_end,payload_digest)
);
CREATE TABLE batches(
    batch_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES owners(owner_id),
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    task_id TEXT REFERENCES tasks(task_id),
    event_upper_sequence INTEGER NOT NULL,
    policy_version TEXT NOT NULL,
    maintenance_schema_version INTEGER NOT NULL,
    judge_version TEXT NOT NULL,
    input_digest TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN (
        'pending','running','proposed','awaiting_review','review_committing',
        'retry','settled','blocked'
    )),
    lease_owner TEXT,
    lease_token TEXT,
    lease_until TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    last_error_class TEXT,
    created_at TEXT NOT NULL,
    settled_at TEXT
);
CREATE TABLE candidates(
    candidate_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES owners(owner_id),
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    task_id TEXT REFERENCES tasks(task_id),
    kind TEXT NOT NULL,
    memory_type_hint TEXT NOT NULL,
    scope_hint_json TEXT NOT NULL,
    topic_key TEXT NOT NULL,
    event_ids_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN (
        'pending','processing','awaiting_review','settled','quarantined','retry'
    )),
    result_ref TEXT,
    active_batch_id TEXT REFERENCES batches(batch_id),
    claim_token TEXT,
    created_sequence INTEGER NOT NULL
);
CREATE UNIQUE INDEX one_active_batch_per_candidate
    ON candidates(candidate_id) WHERE active_batch_id IS NOT NULL;
CREATE TABLE batch_events(
    batch_id TEXT NOT NULL REFERENCES batches(batch_id),
    event_id TEXT NOT NULL REFERENCES events(event_id),
    PRIMARY KEY(batch_id,event_id)
);
CREATE TABLE batch_candidates(
    batch_id TEXT NOT NULL REFERENCES batches(batch_id),
    candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id),
    candidate_claim_token TEXT NOT NULL,
    released_at TEXT,
    PRIMARY KEY(batch_id,candidate_id)
);
CREATE UNIQUE INDEX one_unreleased_batch_per_candidate
    ON batch_candidates(candidate_id) WHERE released_at IS NULL;
CREATE TABLE batch_anchors(
    batch_id TEXT NOT NULL REFERENCES batches(batch_id),
    memory_id TEXT NOT NULL REFERENCES memories(memory_id),
    revision INTEGER NOT NULL,
    PRIMARY KEY(batch_id,memory_id),
    FOREIGN KEY(memory_id,revision) REFERENCES revisions(memory_id,revision)
);
CREATE TABLE proposals(
    proposal_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES batches(batch_id),
    candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id),
    predecessor_id TEXT REFERENCES proposals(proposal_id),
    operation TEXT NOT NULL CHECK(operation IN (
        'new','confirm','refine','supersede','conflict','no_change'
    )),
    target_id TEXT,
    expected_revision INTEGER,
    memory_type TEXT,
    scope_json TEXT,
    payload_patch_json TEXT NOT NULL,
    evidence_ids_json TEXT NOT NULL,
    rationale_redacted TEXT,
    input_digest TEXT NOT NULL,
    output_digest TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN (
        'proposed','committed','no_change','awaiting_review',
        'rejected','invalidated','superseded'
    )),
    created_at TEXT NOT NULL
);
CREATE TABLE conflicts(
    conflict_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES batches(batch_id),
    candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id),
    proposal_id TEXT NOT NULL REFERENCES proposals(proposal_id),
    target_id TEXT,
    base_revision INTEGER,
    reason TEXT NOT NULL,
    status TEXT NOT NULL,
    resolution TEXT,
    resolved_by TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
CREATE TABLE review_receipts(
    owner_id TEXT NOT NULL REFERENCES owners(owner_id),
    receipt TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    conflict_id TEXT NOT NULL REFERENCES conflicts(conflict_id),
    decision TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(owner_id,receipt)
);
CREATE TABLE projection_attempts(
    attempt_id TEXT PRIMARY KEY,
    outbox_id INTEGER NOT NULL REFERENCES outbox(outbox_id),
    operation_key TEXT NOT NULL,
    attempt_no INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    call_started_at TEXT,
    call_finished_at TEXT,
    result_status TEXT,
    error_class TEXT,
    error_detail_redacted TEXT,
    observed_locator TEXT,
    lease_owner TEXT NOT NULL,
    lease_token TEXT NOT NULL,
    UNIQUE(outbox_id,attempt_no)
);
CREATE TABLE projection_receipts(
    receipt_id TEXT PRIMARY KEY,
    operation_key TEXT NOT NULL UNIQUE,
    action TEXT NOT NULL,
    backend TEXT NOT NULL,
    generation TEXT NOT NULL,
    memory_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    backend_locator TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    confirmed_at TEXT NOT NULL
);
ALTER TABLE outbox ADD COLUMN lease_owner TEXT;
ALTER TABLE outbox ADD COLUMN lease_token TEXT;
ALTER TABLE outbox ADD COLUMN lease_until TEXT;
ALTER TABLE outbox ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE outbox ADD COLUMN next_attempt_at TEXT;
ALTER TABLE outbox ADD COLUMN last_error_class TEXT;
ALTER TABLE outbox ADD COLUMN unknown_reason TEXT;
ALTER TABLE outbox ADD COLUMN confirmed_receipt_id TEXT;
ALTER TABLE outbox ADD COLUMN target_locator TEXT;
ALTER TABLE outbox ADD COLUMN updated_at TEXT;
"""

MIGRATION_3 = """
CREATE TABLE IF NOT EXISTS rollout_audit(
    receipt_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    workspace_id TEXT,
    session_id TEXT,
    digest TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
ALTER TABLE rollout_audit ADD COLUMN rollout_id TEXT;
CREATE TABLE rollout_runs(
    rollout_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES owners(owner_id),
    workspace_id TEXT NOT NULL,
    workspace_root TEXT NOT NULL,
    mode TEXT NOT NULL CHECK(mode IN ('shadow','guarded')),
    status TEXT NOT NULL CHECK(status IN (
        'running','draining','stopping','stopped','cleanup_required'
    )),
    approver TEXT NOT NULL,
    key_id TEXT NOT NULL,
    approval_receipt TEXT NOT NULL,
    approval_digest TEXT NOT NULL,
    started_at TEXT NOT NULL,
    deadline TEXT NOT NULL,
    stopped_at TEXT,
    stop_reason TEXT,
    max_sessions INTEGER NOT NULL CHECK(max_sessions > 0),
    max_candidates INTEGER NOT NULL CHECK(max_candidates > 0),
    generation TEXT NOT NULL,
    control_epoch INTEGER NOT NULL DEFAULT 1,
    rollback_plan_digest TEXT NOT NULL,
    rollback_plan_json TEXT NOT NULL DEFAULT '{}',
    plan_schema_version TEXT NOT NULL DEFAULT 'rollout-plan-v1',
    created_at TEXT NOT NULL,
    UNIQUE(owner_id, approval_receipt)
);
CREATE UNIQUE INDEX one_live_rollout_per_owner ON rollout_runs(owner_id)
    WHERE status IN ('running','draining','stopping','cleanup_required');
CREATE TABLE rollout_action_receipts(
    owner_id TEXT NOT NULL REFERENCES owners(owner_id),
    receipt TEXT NOT NULL,
    action TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    rollout_id TEXT,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(owner_id, receipt)
);
CREATE TABLE rollout_approval_grants(
    owner_id TEXT NOT NULL REFERENCES owners(owner_id),
    receipt TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    key_id TEXT NOT NULL,
    registered_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    consumed_at TEXT,
    PRIMARY KEY(owner_id, receipt)
);
CREATE TABLE rollout_sessions(
    rollout_id TEXT NOT NULL REFERENCES rollout_runs(rollout_id),
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    host_session_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('reserved','closed')),
    reserved_at TEXT NOT NULL,
    closed_at TEXT,
    PRIMARY KEY(rollout_id, session_id),
    UNIQUE(rollout_id, host_session_id)
);
CREATE TABLE rollout_events(
    rollout_id TEXT NOT NULL REFERENCES rollout_runs(rollout_id),
    event_id TEXT NOT NULL REFERENCES events(event_id),
    PRIMARY KEY(rollout_id, event_id)
);
CREATE TABLE rollout_candidate_reservations(
    rollout_id TEXT NOT NULL REFERENCES rollout_runs(rollout_id),
    candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id),
    event_id TEXT NOT NULL REFERENCES events(event_id),
    reserved_at TEXT NOT NULL,
    PRIMARY KEY(rollout_id, candidate_id)
);
CREATE TABLE rollout_candidates(
    rollout_id TEXT NOT NULL REFERENCES rollout_runs(rollout_id),
    candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id),
    PRIMARY KEY(rollout_id, candidate_id)
);
CREATE TABLE rollout_batches(
    rollout_id TEXT NOT NULL REFERENCES rollout_runs(rollout_id),
    batch_id TEXT NOT NULL REFERENCES batches(batch_id),
    PRIMARY KEY(rollout_id, batch_id)
);
CREATE TABLE rollout_review_receipts(
    owner_id TEXT NOT NULL REFERENCES owners(owner_id),
    receipt TEXT NOT NULL,
    rollout_id TEXT NOT NULL REFERENCES rollout_runs(rollout_id),
    batch_id TEXT NOT NULL REFERENCES batches(batch_id),
    decision TEXT NOT NULL CHECK(decision IN ('approve','reject')),
    reviewer TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(owner_id, receipt)
);
CREATE TABLE rollout_commits(
    rollout_id TEXT NOT NULL REFERENCES rollout_runs(rollout_id),
    proposal_id TEXT NOT NULL REFERENCES proposals(proposal_id),
    memory_id TEXT NOT NULL REFERENCES memories(memory_id),
    revision INTEGER NOT NULL,
    previous_revision INTEGER,
    previous_state TEXT,
    outbox_id INTEGER REFERENCES outbox(outbox_id),
    operation TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(rollout_id, proposal_id, memory_id, revision)
);
CREATE TABLE rollout_rollback_receipts(
    owner_id TEXT NOT NULL REFERENCES owners(owner_id),
    receipt TEXT NOT NULL,
    rollout_id TEXT NOT NULL REFERENCES rollout_runs(rollout_id),
    plan_digest TEXT NOT NULL,
    status TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(owner_id, receipt)
);
CREATE TABLE rollout_consumption_receipts(
    owner_id TEXT NOT NULL REFERENCES owners(owner_id),
    receipt_id TEXT PRIMARY KEY,
    rollout_id TEXT REFERENCES rollout_runs(rollout_id),
    session_digest TEXT NOT NULL,
    bundle_digest TEXT,
    result_digest TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


MIGRATION_4 = """
CREATE TABLE rollout_control_keys(
    owner_id TEXT NOT NULL REFERENCES owners(owner_id),
    key_id TEXT NOT NULL,
    token_digest TEXT NOT NULL,
    approver TEXT NOT NULL,
    registered_at TEXT NOT NULL,
    retired_at TEXT,
    PRIMARY KEY(owner_id,key_id)
);
CREATE UNIQUE INDEX one_current_control_key ON rollout_control_keys(owner_id)
    WHERE retired_at IS NULL;
CREATE TABLE rollback_runs(
    rollback_id TEXT PRIMARY KEY, rollout_id TEXT NOT NULL REFERENCES rollout_runs(rollout_id),
    owner_id TEXT NOT NULL, plan_digest TEXT NOT NULL, status TEXT NOT NULL,
    control_epoch INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE(rollout_id)
);
CREATE TABLE rollback_steps(
    rollback_id TEXT NOT NULL REFERENCES rollback_runs(rollback_id), step_no INTEGER NOT NULL,
    kind TEXT NOT NULL, status TEXT NOT NULL, target_json TEXT NOT NULL,
    receipt TEXT, error_class TEXT, started_at TEXT, finished_at TEXT,
    PRIMARY KEY(rollback_id,step_no)
);
"""
