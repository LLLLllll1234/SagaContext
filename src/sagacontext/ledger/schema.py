SCHEMA_VERSION = 1

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
