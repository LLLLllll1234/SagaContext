from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Protocol

from ..ledger import (
    BatchCandidateResult,
    BatchConflictRecord,
    BatchEvidenceLink,
    BatchMemoryOperation,
    BatchTaskUpdate,
    CommitBatchPlan,
    ExpectedHead,
    Ledger,
    Scope,
)
from .batches import BatchService
from .models import BatchInput, BatchRunResult, DeltaProposal


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


class ProposalJudge(Protocol):
    version: str

    def judge(self, batch: BatchInput) -> tuple[DeltaProposal, ...]: ...


class ScriptedJudge:
    def __init__(
        self,
        proposals: tuple[DeltaProposal, ...] = (),
        error: Exception | None = None,
        version: str = "scripted-v1",
    ) -> None:
        self.proposals = proposals
        self.error = error
        self.version = version
        self.calls = 0

    def judge(self, batch: BatchInput) -> tuple[DeltaProposal, ...]:
        self.calls += 1
        if self.error:
            raise self.error
        return self.proposals


class BatchWorker:
    def __init__(self, ledger: Ledger):
        self.ledger = ledger
        self.batches = BatchService(ledger)

    def run_once(
        self,
        judge: ProposalJudge,
        *,
        worker_id: str,
        now: datetime,
        lease_duration: timedelta,
        stop_after_proposals: bool = False,
        max_attempts: int = 3,
    ) -> BatchRunResult:
        claim = self.batches.claim_next(worker_id, now, lease_duration)
        if claim is None:
            return BatchRunResult(status="idle")
        batch_id = claim.batch_id
        if not self.batches.input_is_current(batch_id):
            self.batches.block_batch(
                batch_id,
                worker_id,
                claim.lease_token,
                "input_digest_changed",
                now=now,
            )
            return BatchRunResult(status="blocked", batch_id=batch_id)

        batch_input = self.batches.batch_input(batch_id)
        if batch_input.judge_version != judge.version:
            self.batches.block_batch(
                batch_id,
                worker_id,
                claim.lease_token,
                "judge_version_changed",
                now=now,
            )
            return BatchRunResult(status="blocked", batch_id=batch_id)

        existing = self._proposed(batch_id)
        if existing and not self.batches.anchors_are_current(batch_id):
            self.batches.invalidate_and_release(
                batch_id, worker_id, claim.lease_token, now=now
            )
            return BatchRunResult(status="invalidated", batch_id=batch_id)
        if not existing:
            try:
                proposals = judge.judge(batch_input)
            except Exception:
                return self._retry_or_block(
                    batch_id, worker_id, claim.lease_token, "judge_error", now, max_attempts
                )
            rejected = self._validate_targets(batch_id, proposals)
            if rejected:
                self._persist_rejected(
                    batch_id, worker_id, claim.lease_token, proposals, now
                )
                return BatchRunResult(status="rejected", batch_id=batch_id)
            self._persist(batch_id, worker_id, claim.lease_token, proposals, now)
            if stop_after_proposals:
                return BatchRunResult(status="proposed", batch_id=batch_id)
            existing = self._proposed(batch_id)

        plan = self._plan(batch_id, existing)
        try:
            result = self.ledger.commit_batch(plan, claim.lease_token, now=now)
        except (sqlite3.DatabaseError, ValueError):
            return self._retry_or_block(
                batch_id, worker_id, claim.lease_token, "commit_error", now, max_attempts
            )
        return BatchRunResult(status=result.status, batch_id=batch_id)

    def _retry_or_block(
        self,
        batch_id: str,
        worker_id: str,
        lease_token: str,
        error_class: str,
        now: datetime,
        max_attempts: int,
    ) -> BatchRunResult:
        attempts = self.ledger.db.execute(
            "SELECT attempt_count FROM batches WHERE batch_id=?", (batch_id,)
        ).fetchone()[0]
        if attempts >= max_attempts:
            self.batches.block_batch(
                batch_id,
                worker_id,
                lease_token,
                error_class,
                now=now,
            )
            return BatchRunResult(status="blocked", batch_id=batch_id)
        self.batches.fail_batch(
            batch_id,
            worker_id,
            lease_token,
            error_class,
            now=now,
        )
        return BatchRunResult(status="retry", batch_id=batch_id)

    def _validate_targets(
        self, batch_id: str, proposals: tuple[DeltaProposal, ...]
    ) -> bool:
        frozen = self.batches.batch_input(batch_id)
        candidates = set(frozen.candidate_ids)
        anchors = {memory_id: revision for memory_id, revision in frozen.anchor_revisions}
        for proposal in proposals:
            if proposal.candidate_id not in candidates:
                return True
            if proposal.operation not in {"new", "no_change"}:
                if (
                    not proposal.target_id
                    or proposal.target_id not in anchors
                    or anchors[proposal.target_id] != proposal.expected_revision
                ):
                    return True
        return False

    def _persist(
        self,
        batch_id: str,
        worker_id: str,
        lease_token: str,
        proposals: tuple[DeltaProposal, ...],
        now: datetime,
    ) -> None:
        batch = self.ledger.db.execute(
            "SELECT input_digest FROM batches WHERE batch_id=?", (batch_id,)
        ).fetchone()
        with self.ledger._write_transaction():
            if not self.batches.validate_claim(
                batch_id, worker_id, lease_token, now=now
            ):
                raise ValueError("batch_lease_fenced")
            for proposal in proposals:
                predecessor = self.ledger.db.execute(
                    "SELECT proposal_id FROM proposals WHERE candidate_id=? "
                    "AND status='invalidated' ORDER BY created_at DESC LIMIT 1",
                    (proposal.candidate_id,),
                ).fetchone()
                proposal_id = str(uuid.uuid4())
                output = proposal.model_dump(mode="json")
                self.ledger.db.execute(
                    "INSERT INTO proposals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        proposal_id,
                        batch_id,
                        proposal.candidate_id,
                        predecessor["proposal_id"] if predecessor else None,
                        proposal.operation,
                        proposal.target_id,
                        proposal.expected_revision,
                        proposal.memory_type,
                        _canonical(proposal.scope.model_dump()),
                        _canonical(proposal.payload),
                        _canonical(proposal.evidence_ids),
                        proposal.rationale[:500],
                        batch["input_digest"],
                        hashlib.sha256(_canonical(output).encode()).hexdigest(),
                        "judge",
                        "proposed",
                        _now(),
                    ),
                )
                if predecessor:
                    self.ledger.db.execute(
                        "UPDATE proposals SET status='superseded' WHERE proposal_id=? "
                        "AND status='invalidated'",
                        (predecessor["proposal_id"],),
                    )
            changed = self.ledger.db.execute(
                "UPDATE batches SET status='proposed' WHERE batch_id=? AND owner_id=? "
                "AND lease_owner=? AND lease_token=? AND status='running'",
                (batch_id, self.ledger.owner_id, worker_id, lease_token),
            )
            if changed.rowcount != 1:
                raise ValueError("batch_lease_fenced")

    def _persist_rejected(
        self,
        batch_id: str,
        worker_id: str,
        lease_token: str,
        proposals: tuple[DeltaProposal, ...],
        now: datetime,
    ) -> None:
        batch = self.ledger.db.execute(
            "SELECT input_digest FROM batches WHERE batch_id=?", (batch_id,)
        ).fetchone()
        with self.ledger._write_transaction():
            if not self.batches.validate_claim(
                batch_id, worker_id, lease_token, now=now
            ):
                raise ValueError("batch_lease_fenced")
            for proposal in proposals:
                proposal_id = str(uuid.uuid4())
                self.ledger.db.execute(
                    "INSERT INTO proposals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        proposal_id,
                        batch_id,
                        proposal.candidate_id,
                        None,
                        proposal.operation,
                        proposal.target_id,
                        proposal.expected_revision,
                        proposal.memory_type,
                        _canonical(proposal.scope.model_dump()),
                        _canonical(proposal.payload),
                        _canonical(proposal.evidence_ids),
                        proposal.rationale[:500],
                        batch["input_digest"],
                        hashlib.sha256(_canonical(proposal.model_dump(mode="json")).encode()).hexdigest(),
                        "judge",
                        "rejected",
                        _now(),
                    ),
                )
            self.ledger.db.execute(
                "UPDATE candidates SET status='quarantined',active_batch_id=NULL,claim_token=NULL "
                "WHERE owner_id=? AND active_batch_id=?",
                (self.ledger.owner_id, batch_id),
            )
            self.ledger.db.execute(
                "UPDATE batch_candidates SET released_at=? WHERE batch_id=? AND released_at IS NULL",
                (_now(), batch_id),
            )
            self.ledger.db.execute(
                "UPDATE batches SET status='settled',lease_owner=NULL,lease_token=NULL,"
                "lease_until=NULL,settled_at=? WHERE batch_id=? AND lease_owner=? AND lease_token=?",
                (_now(), batch_id, worker_id, lease_token),
            )

    def _proposed(self, batch_id: str):
        return self.ledger.db.execute(
            "SELECT * FROM proposals WHERE batch_id=? AND status='proposed' ORDER BY created_at,proposal_id",
            (batch_id,),
        ).fetchall()

    def _plan(self, batch_id: str, proposals) -> CommitBatchPlan:
        memory_operations = []
        expected_heads = []
        evidence_links = []
        candidate_results = []
        conflicts = []
        task_id = self.ledger.db.execute(
            "SELECT task_id FROM batches WHERE batch_id=?", (batch_id,)
        ).fetchone()[0]
        for row in proposals:
            candidate = self.ledger.db.execute(
                "SELECT claim_token FROM candidates WHERE candidate_id=?", (row["candidate_id"],)
            ).fetchone()
            if row["operation"] in {"new", "confirm", "refine", "supersede"}:
                memory_operations.append(
                    BatchMemoryOperation(
                        proposal_id=row["proposal_id"],
                        operation=row["operation"],
                        memory_type=row["memory_type"],
                        scope=Scope.model_validate_json(row["scope_json"]),
                        payload_json=row["payload_patch_json"],
                        memory_id=row["target_id"],
                        expected_revision=row["expected_revision"],
                    )
                )
                if row["target_id"]:
                    expected_heads.append(
                        ExpectedHead(
                            memory_id=row["target_id"], revision=row["expected_revision"]
                        )
                    )
                evidence_links.append(
                    BatchEvidenceLink(
                        proposal_id=row["proposal_id"],
                        evidence_ids=tuple(json.loads(row["evidence_ids_json"])),
                    )
                )
                status = "settled"
                result_ref = row["proposal_id"]
            elif row["operation"] == "conflict":
                conflict_id = str(
                    uuid.uuid5(uuid.NAMESPACE_URL, f"conflict:{row['proposal_id']}")
                )
                conflicts.append(
                    BatchConflictRecord(
                        conflict_id=conflict_id,
                        candidate_id=row["candidate_id"],
                        proposal_id=row["proposal_id"],
                        reason="judge_conflict",
                        target_id=row["target_id"],
                        base_revision=row["expected_revision"],
                    )
                )
                status = "awaiting_review"
                result_ref = conflict_id
            else:
                status = "settled"
                result_ref = row["proposal_id"]
            candidate_results.append(
                BatchCandidateResult(
                    candidate_id=row["candidate_id"],
                    claim_token=candidate["claim_token"],
                    status=status,
                    result_ref=result_ref,
                )
            )
        return CommitBatchPlan(
            batch_id=batch_id,
            proposal_ids=tuple(row["proposal_id"] for row in proposals),
            expected_heads=tuple(expected_heads),
            memory_operations=tuple(memory_operations),
            evidence_links=tuple(evidence_links),
            candidate_results=tuple(candidate_results),
            conflict_records=tuple(conflicts),
            task_update=BatchTaskUpdate(task_id=task_id) if task_id and memory_operations else None,
        )
