from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from ..ledger import (
    BatchCandidateResult,
    BatchEvidenceLink,
    BatchMemoryOperation,
    BatchTaskUpdate,
    CommitBatchPlan,
    ExpectedHead,
    Ledger,
    Scope,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


class ReviewService:
    def __init__(self, ledger: Ledger):
        self.ledger = ledger

    def resolve(
        self,
        conflict_id: str,
        decision: Literal["accept_old", "accept_new", "defer"],
        receipt: str,
        *,
        reviewer: str,
    ) -> dict[str, str]:
        digest = hashlib.sha256(
            _canonical({"conflict_id": conflict_id, "decision": decision}).encode()
        ).hexdigest()
        prior = self.ledger.db.execute(
            "SELECT request_digest,result_json FROM review_receipts WHERE owner_id=? AND receipt=?",
            (self.ledger.owner_id, receipt),
        ).fetchone()
        if prior:
            if prior["request_digest"] != digest:
                return {"status": "rejected", "reason": "receipt_reused"}
            return json.loads(prior["result_json"])
        conflict = self.ledger.db.execute(
            "SELECT c.*,p.payload_patch_json,p.scope_json,p.memory_type,p.evidence_ids_json,"
            "b.status AS batch_status,b.input_digest,b.task_id,b.lease_token,"
            "ca.claim_token FROM conflicts c JOIN proposals p ON p.proposal_id=c.proposal_id "
            "JOIN batches b ON b.batch_id=c.batch_id "
            "JOIN candidates ca ON ca.candidate_id=c.candidate_id "
            "WHERE c.conflict_id=? AND b.owner_id=?",
            (conflict_id, self.ledger.owner_id),
        ).fetchone()
        successor_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"review:{self.ledger.owner_id}:{receipt}"))
        if conflict and conflict["batch_status"] == "settled":
            successor = self.ledger.db.execute(
                "SELECT status FROM proposals WHERE proposal_id=? AND predecessor_id=?",
                (successor_id, conflict["proposal_id"]),
            ).fetchone()
            if successor and successor["status"] == "committed":
                result = {"status": "settled", "conflict_id": conflict_id}
                with self.ledger._write_transaction():
                    self.ledger.db.execute(
                        "UPDATE conflicts SET status='resolved',resolution=?,resolved_by=?,resolved_at=? "
                        "WHERE conflict_id=? AND status='open'",
                        (decision, reviewer, _now(), conflict_id),
                    )
                    self._insert_receipt(receipt, digest, conflict_id, decision, result)
                return result
        if (
            not conflict
            or conflict["status"] != "open"
            or conflict["batch_status"] not in {"awaiting_review", "review_committing"}
        ):
            result = {"status": "rejected", "reason": "conflict_not_open"}
            self._save_receipt(receipt, digest, conflict_id, decision, result)
            return result
        if decision == "defer":
            result = {"status": "awaiting_review", "conflict_id": conflict_id}
            self._save_receipt(receipt, digest, conflict_id, decision, result)
            return result
        if decision == "accept_new":
            head = self.ledger.db.execute(
                "SELECT current_revision,state FROM memories WHERE memory_id=? AND owner_id=?",
                (conflict["target_id"], self.ledger.owner_id),
            ).fetchone()
            if (
                not head
                or head["state"] != "active"
                or head["current_revision"] != conflict["base_revision"]
            ):
                with self.ledger._write_transaction():
                    self.ledger.db.execute(
                        "UPDATE proposals SET status='invalidated' WHERE proposal_id=? "
                        "OR (predecessor_id=? AND status='proposed')",
                        (conflict["proposal_id"], conflict["proposal_id"]),
                    )
                    self.ledger.db.execute(
                        "UPDATE batches SET status='awaiting_review',lease_owner=NULL,"
                        "lease_token=NULL,lease_until=NULL WHERE batch_id=?",
                        (conflict["batch_id"],),
                    )
                result = {"status": "stale_review", "conflict_id": conflict_id}
                self._save_receipt(receipt, digest, conflict_id, decision, result)
                return result
            lease_token = conflict["lease_token"]
            review_now = datetime.now(timezone.utc)
            if conflict["batch_status"] == "awaiting_review":
                lease_token = str(uuid.uuid4())
                lease_until = review_now + timedelta(seconds=30)
                with self.ledger._write_transaction():
                    self.ledger.db.execute(
                        "INSERT INTO proposals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            successor_id,
                            conflict["batch_id"],
                            conflict["candidate_id"],
                            conflict["proposal_id"],
                            "refine",
                            conflict["target_id"],
                            conflict["base_revision"],
                            conflict["memory_type"],
                            conflict["scope_json"],
                            conflict["payload_patch_json"],
                            conflict["evidence_ids_json"],
                            "human accepted proposed memory",
                            conflict["input_digest"],
                            digest,
                            "human_review",
                            "proposed",
                            _now(),
                        ),
                    )
                    self.ledger.db.execute(
                        "UPDATE proposals SET status='superseded' WHERE proposal_id=?",
                        (conflict["proposal_id"],),
                    )
                    changed = self.ledger.db.execute(
                        "UPDATE batches SET status='review_committing',lease_owner=?,lease_token=?,"
                        "lease_until=? "
                        "WHERE batch_id=? AND status='awaiting_review'",
                        (
                            reviewer,
                            lease_token,
                            lease_until.isoformat(),
                            conflict["batch_id"],
                        ),
                    )
                    if changed.rowcount != 1:
                        raise ValueError("review_batch_fenced")
            plan = CommitBatchPlan(
                batch_id=conflict["batch_id"],
                proposal_ids=(successor_id,),
                expected_heads=(
                    ExpectedHead(
                        memory_id=conflict["target_id"],
                        revision=conflict["base_revision"],
                    ),
                ),
                memory_operations=(
                    BatchMemoryOperation(
                        proposal_id=successor_id,
                        operation="refine",
                        memory_id=conflict["target_id"],
                        expected_revision=conflict["base_revision"],
                        memory_type=conflict["memory_type"],
                        scope=Scope.model_validate_json(conflict["scope_json"]),
                        payload_json=conflict["payload_patch_json"],
                        source_kind="human_review",
                    ),
                ),
                evidence_links=(
                    BatchEvidenceLink(
                        proposal_id=successor_id,
                        evidence_ids=tuple(json.loads(conflict["evidence_ids_json"])),
                    ),
                ),
                candidate_results=(
                    BatchCandidateResult(
                        candidate_id=conflict["candidate_id"],
                        claim_token=conflict["claim_token"],
                        status="settled",
                        result_ref=successor_id,
                    ),
                ),
                task_update=BatchTaskUpdate(task_id=conflict["task_id"])
                if conflict["task_id"]
                else None,
            )
            self.ledger.commit_batch(plan, lease_token, now=review_now)
            result = {"status": "settled", "conflict_id": conflict_id}
            with self.ledger._write_transaction():
                self.ledger.db.execute(
                    "UPDATE conflicts SET status='resolved',resolution=?,resolved_by=?,resolved_at=? "
                    "WHERE conflict_id=? AND status='open'",
                    (decision, reviewer, _now(), conflict_id),
                )
                self._insert_receipt(receipt, digest, conflict_id, decision, result)
            return result

        with self.ledger._write_transaction():
            self.ledger.db.execute(
                "INSERT INTO proposals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    successor_id,
                    conflict["batch_id"],
                    conflict["candidate_id"],
                    conflict["proposal_id"],
                    "no_change",
                    conflict["target_id"],
                    conflict["base_revision"],
                    conflict["memory_type"],
                    conflict["scope_json"],
                    conflict["payload_patch_json"],
                    conflict["evidence_ids_json"],
                    "human accepted current memory",
                    "human-review",
                    digest,
                    "human_review",
                    "no_change",
                    _now(),
                ),
            )
            self.ledger.db.execute(
                "UPDATE proposals SET status='superseded' WHERE proposal_id=?",
                (conflict["proposal_id"],),
            )
            self.ledger.db.execute(
                "UPDATE conflicts SET status='resolved',resolution=?,resolved_by=?,resolved_at=? "
                "WHERE conflict_id=?",
                (decision, reviewer, _now(), conflict_id),
            )
            self.ledger.db.execute(
                "UPDATE candidates SET status='settled',active_batch_id=NULL,claim_token=NULL "
                "WHERE candidate_id=? AND active_batch_id=? AND status='awaiting_review'",
                (conflict["candidate_id"], conflict["batch_id"]),
            )
            self.ledger.db.execute(
                "UPDATE batch_candidates SET released_at=? WHERE batch_id=? AND candidate_id=? "
                "AND released_at IS NULL",
                (_now(), conflict["batch_id"], conflict["candidate_id"]),
            )
            self.ledger.db.execute(
                "UPDATE batches SET status='settled',settled_at=? WHERE batch_id=? "
                "AND status='awaiting_review'",
                (_now(), conflict["batch_id"]),
            )
            result = {"status": "settled", "conflict_id": conflict_id}
            self._insert_receipt(receipt, digest, conflict_id, decision, result)
        return result

    def _save_receipt(
        self,
        receipt: str,
        digest: str,
        conflict_id: str,
        decision: str,
        result: dict[str, str],
    ) -> None:
        with self.ledger._write_transaction():
            self._insert_receipt(receipt, digest, conflict_id, decision, result)

    def _insert_receipt(
        self,
        receipt: str,
        digest: str,
        conflict_id: str,
        decision: str,
        result: dict[str, str],
    ) -> None:
        self.ledger.db.execute(
            "INSERT INTO review_receipts VALUES (?,?,?,?,?,?,?)",
            (
                self.ledger.owner_id,
                receipt,
                digest,
                conflict_id,
                decision,
                _canonical(result),
                _now(),
            ),
        )
