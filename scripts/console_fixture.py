"""Synthetic console data. Only creates a new database below the given root."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sagacontext.ledger import CommitRequest, EvidenceInput, Ledger, Scope
from sagacontext.maintenance import BatchService, CandidateInput, EventJournal, JournalEvent


@dataclass
class ConsoleFixture:
    path: Path
    owner_a: str = "console-demo"
    owner_b: str = "other-owner"
    project_a: str = ""
    project_b: str = ""
    workspace_a: str = ""
    workspace_b: str = ""
    workspace_other: str = ""
    session_a: str = ""
    session_b: str = ""
    task_id: str = ""
    event_id: str = ""
    memory_id: str = ""
    batch_id: str = ""
    rollout_id: str = "demo-rollout"
    start: datetime = field(default_factory=lambda: datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0))
    end: datetime = field(default_factory=lambda: datetime.now(timezone.utc) + timedelta(days=1))


def create_fixture(root: Path, *, populated: bool = True) -> ConsoleFixture:
    root.mkdir(parents=True, exist_ok=True)
    case = ConsoleFixture(root / "console-demo.db")
    if case.path.exists():
        raise ValueError("fixture_database_exists")
    ledger = Ledger(case.path, owner_id=case.owner_a)
    try:
        a, b, other = (root / name for name in ("main", "worktree", "other"))
        for path in (a, b, other):
            path.mkdir()
        identity = ledger.register_project("SagaContext · 示例项目", a)
        case.project_a, case.workspace_a = identity["project_id"], identity["workspace_id"]
        case.workspace_b = ledger.bind_location(case.project_a, b)
        identity = ledger.register_project("独立项目", other)
        case.project_b, case.workspace_other = identity["project_id"], identity["workspace_id"]
        if not populated:
            return case
        case.task_id = ledger.create_task(case.project_a, "完善错误处理约定")
        case.session_a = ledger.open_session("codex", "demo-session-01", case.workspace_a)
        case.session_b = ledger.open_session("codex", "demo-session-02", case.workspace_b)
        journal = EventJournal(ledger)
        now = datetime.now(timezone.utc)
        case.event_id = journal.append(JournalEvent(
            host="codex", host_version="fixture", session_id=case.session_a,
            workspace_id=case.workspace_a, event_kind="user_message", occurred_at=now,
            trust_class="synthetic", source_generation="fixture", source_event_key="demo-1",
            payload={"text": "本项目记录失败原因与可重试类别"}, parser_version="fixture",
        )).event_id
        ledger.bind_task(case.session_a, case.task_id, case.event_id)
        journal.append(JournalEvent(
            host="codex", host_version="fixture", session_id=case.session_a,
            workspace_id=case.workspace_a, event_kind="checkpoint_requested", occurred_at=now,
            trust_class="synthetic", source_generation="fixture", source_event_key="checkpoint",
            payload={}, parser_version="fixture",
        ))
        scope = Scope(kind="project", project_id=case.project_a)
        committed = ledger.commit(CommitRequest(
            receipt="demo-memory", operation="new", memory_type="convention", scope=scope,
            payload={"rule": "记录失败原因"}, source_kind="synthetic",
            evidence=[EvidenceInput(evidence_id="demo-evidence", source_event_id=case.event_id,
                claim_key="errors", evidence_kind="user_statement", locator={}, observed_at=now,
                redacted_excerpt="本项目记录失败原因与可重试类别")],
        ))
        case.memory_id = committed.memory_id
        batches = BatchService(ledger)
        candidate = batches.create_candidate(CandidateInput(
            session_id=case.session_a, task_id=case.task_id, kind="explicit_instruction",
            memory_type_hint="convention", scope_hint=scope, topic_key="errors", event_ids=(case.event_id,),
        ))
        case.batch_id = batches.request_batch(case.session_a, case.task_id, (case.memory_id,)).batch_id
        def insert(table, **values):
            columns = ",".join(values)
            placeholders = ",".join("?" for _ in values)
            ledger.db.execute(f"INSERT INTO {table}({columns}) VALUES ({placeholders})", tuple(values.values()))
        stamp = now.isoformat()
        insert("rollout_runs", rollout_id=case.rollout_id, owner_id=case.owner_a,
            workspace_id=case.workspace_a, workspace_root=str(a), mode="guarded", status="running",
            approver="fixture", key_id="fixture", approval_receipt="fixture", approval_digest="fixture",
            started_at=stamp, deadline=(now+timedelta(hours=4)).isoformat(), max_sessions=10,
            max_candidates=20, generation="fixture", rollback_plan_digest="fixture", created_at=stamp)
        insert("rollout_sessions", rollout_id=case.rollout_id, session_id=case.session_a,
            host_session_id="demo-session-01", status="reserved", reserved_at=stamp)
        insert("rollout_events", rollout_id=case.rollout_id, event_id=case.event_id)
        insert("rollout_batches", rollout_id=case.rollout_id, batch_id=case.batch_id)
        insert("rollout_candidates", rollout_id=case.rollout_id, candidate_id=candidate.candidate_id)
        insert("rollout_candidate_reservations", rollout_id=case.rollout_id,
            candidate_id=candidate.candidate_id, event_id=case.event_id, reserved_at=stamp)
        insert("proposals", proposal_id="demo-proposal", batch_id=case.batch_id,
            candidate_id=candidate.candidate_id, operation="refine", target_id=case.memory_id,
            expected_revision=1, memory_type="convention", scope_json=scope.model_dump_json(),
            payload_patch_json=json.dumps({"rule": "记录失败原因与可重试类别"}, ensure_ascii=False),
            evidence_ids_json=json.dumps(["demo-evidence"]), rationale_redacted="补全明确约定",
            input_digest="fixture", output_digest="fixture", source_kind="synthetic",
            status="awaiting_review", created_at=stamp)
        ledger.db.execute("UPDATE batches SET status='awaiting_review' WHERE batch_id=?", (case.batch_id,))
        ledger.db.execute("UPDATE candidates SET status='awaiting_review' WHERE candidate_id=?", (candidate.candidate_id,))
        # Separate historical committed operation; the awaiting proposal above remains uncommitted.
        insert("proposals", proposal_id="demo-committed", batch_id=case.batch_id,
            candidate_id=candidate.candidate_id, operation="new", target_id=None,
            memory_type="convention", scope_json=scope.model_dump_json(),
            payload_patch_json=json.dumps({"rule": "记录失败原因"}, ensure_ascii=False),
            evidence_ids_json=json.dumps(["demo-evidence"]), input_digest="old", output_digest="old",
            source_kind="synthetic", status="committed", created_at=stamp)
        insert("rollout_commits", rollout_id=case.rollout_id, proposal_id="demo-committed",
            memory_id=case.memory_id, revision=1, operation="new", created_at=stamp)
        ledger.record_rollout_receipt("injection", {
            "status":"emitted", "mode":"guarded", "memory_ids":[case.memory_id], "revisions":[1],
            "generation":"fixture", "ledger_sequence":ledger.sequence, "session_digest":"fixture-session",
            "bundle_digest":"fixture-bundle", "omissions":[],
        }, rollout_id=case.rollout_id, workspace_id=case.workspace_a, session_id=case.session_a,
            receipt_id="demo-injection")
        # Another owner shares the SQLite file, not any project identity.
        ledger.db.execute("INSERT INTO owners VALUES (?,?)", (case.owner_b, stamp))
        ledger.db.execute("INSERT INTO projects VALUES (?,?,?,?)", ("foreign-project", case.owner_b, "不可见项目", stamp))
        return case
    finally:
        ledger.close()
