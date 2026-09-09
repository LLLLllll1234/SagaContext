"""Durable rollout projection identity, fencing and compensating deletion.

Backend calls never run inside a Ledger transaction. A compensation lease and
CAS protect each operation; receipts are append-only evidence, not work queues.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone


def now_text():
    return datetime.now(timezone.utc).isoformat()


def operation_key(row):
    identity = {key: row[key] for key in (
        "action", "backend", "generation", "memory_id", "revision", "target_locator"
    )}
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class ProjectionProtocol:
    def __init__(self, ledger):
        self.ledger = ledger

    def ownership(self, outbox_id):
        return self.ledger.db.execute(
            "SELECT r.*, rr.receipt AS review_receipt FROM rollout_commits c "
            "JOIN rollout_runs r ON r.rollout_id=c.rollout_id "
            "JOIN proposals p ON p.proposal_id=c.proposal_id "
            "JOIN rollout_review_receipts rr ON rr.batch_id=p.batch_id "
            "AND rr.rollout_id=r.rollout_id AND rr.decision='approve' "
            "WHERE c.outbox_id=? AND r.owner_id=?",
            (outbox_id, self.ledger.owner_id),
        ).fetchone()

    def register(self, row, run, *, token=None, until=None, payload_digest=None):
        key = operation_key(row)
        self.ledger.db.execute(
            "INSERT INTO projection_operations(outbox_id,operation_key,rollout_id,authorization_receipt,"
            "control_epoch,claim_token,claim_until,state,payload_digest,updated_at) "
            "VALUES (?,?,?,?,?,?,?,'registered',?,?) ON CONFLICT(outbox_id) DO NOTHING",
            (row['outbox_id'], key, run['rollout_id'], run['review_receipt'],
             run['control_epoch'], token, until, payload_digest, now_text()),
        )
        op = self.ledger.db.execute("SELECT * FROM projection_operations WHERE outbox_id=?", (row['outbox_id'],)).fetchone()
        if op['operation_key'] != key or op['rollout_id'] != run['rollout_id']:
            raise ValueError('projection_operation_conflict')
        if token:
            if op['state'] in {'compensating', 'compensated', 'cancelled', 'cleanup_required'}:
                raise ValueError('projection_operation_fenced')
            self.ledger.db.execute(
                "UPDATE projection_operations SET claim_token=?,claim_until=?,control_epoch=?,"
                "payload_digest=?,state='claimed',updated_at=? WHERE outbox_id=?",
                (token, until, run['control_epoch'], payload_digest, now_text(), row['outbox_id']),
            )
        return key

    def record_return(self, claim, locator):
        """Record a late result before attempting any compensation, even after lease loss."""
        with self.ledger._write_transaction():
            attempt = self.ledger.db.execute(
                "SELECT * FROM projection_attempts WHERE outbox_id=? AND lease_token=? AND operation_key=?",
                (claim.outbox_id, claim.lease_token, claim.operation_key),
            ).fetchone()
            if not attempt or attempt['rollout_id'] != claim.rollout_id or attempt['control_epoch'] != claim.control_epoch:
                raise ValueError('projection_claim_missing')
            self.ledger.db.execute(
                "UPDATE projection_attempts SET call_finished_at=?,observed_locator=? WHERE attempt_id=?",
                (now_text(), locator, attempt['attempt_id']),
            )
            self.ledger.db.execute(
                "UPDATE projection_operations SET locator=?,updated_at=? WHERE outbox_id=?",
                (locator or None, now_text(), claim.outbox_id),
            )

    def compensate(self, outbox_id, backend, *, rollback=False, now=None):
        now = now or datetime.now(timezone.utc)
        token = str(uuid.uuid4())
        with self.ledger._write_transaction():
            op = self.ledger.db.execute(
                "SELECT p.*,o.backend,o.generation,o.memory_id,o.revision,o.action,r.control_epoch AS stop_epoch,"
                "r.status AS rollout_status,r.rollback_plan_json FROM projection_operations p JOIN outbox o USING(outbox_id) "
                "JOIN rollout_runs r ON r.rollout_id=p.rollout_id WHERE p.outbox_id=? AND r.owner_id=?",
                (outbox_id, self.ledger.owner_id),
            ).fetchone()
            if not op or op['action'] != 'upsert' or op['rollout_status'] not in {'stopping', 'stopped'}:
                raise ValueError('compensation_not_authorized')
            if not rollback:
                plan = json.loads(op['rollback_plan_json'])
                evidence = self.ledger.db.execute(
                    "SELECT 1 FROM projection_attempts WHERE outbox_id=? AND lease_token=? "
                    "AND rollout_id=? AND control_epoch=? AND call_started_at IS NOT NULL",
                    (outbox_id, op['claim_token'], op['rollout_id'], op['control_epoch']),
                ).fetchone()
                if not evidence or op['stop_epoch'] <= op['control_epoch'] or plan.get('compensate_inflight') is not True:
                    raise ValueError('compensation_claim_or_plan_missing')
            if not rollback and self.ledger.db.execute(
                "SELECT 1 FROM projection_receipts WHERE operation_key=?", (op['operation_key'],)
            ).fetchone():
                raise ValueError('confirmed_projection_requires_rollback')
            if op['compensation_token'] and op['compensation_until'] > now.isoformat():
                return 'in_progress'
            # A live external call cannot be declared absent. Its callback will record
            # the locator and release this barrier; restart recovery waits for expiry.
            pending = self.ledger.db.execute(
                "SELECT 1 FROM projection_attempts WHERE outbox_id=? AND call_started_at IS NOT NULL "
                "AND call_finished_at IS NULL", (outbox_id,)
            ).fetchone()
            if pending and op['claim_until'] and op['claim_until'] > now.isoformat():
                return 'in_progress'
            self.ledger.db.execute(
                "UPDATE projection_operations SET compensation_token=?,compensation_until=?,"
                "state='compensating',updated_at=? WHERE outbox_id=?",
                (token, (now + timedelta(seconds=60)).isoformat(), now.isoformat(), outbox_id),
            )
        locator = op['locator']
        error = None
        try:
            if backend is None:
                raise ValueError('compensation_backend_required')
            capabilities = backend.capabilities()
            if capabilities.backend != op['backend'] or not capabilities.stable_id_mapping or not capabilities.visibility_check:
                raise ValueError('compensation_backend_unverifiable')
            if getattr(backend, 'timeout', 5) >= 15:
                raise ValueError('compensation_timeout_exceeds_lease_budget')
            exact = backend.locate_projection(op['memory_id'], op['revision'], op['generation'], op['operation_key'])
            if locator and exact and locator != exact:
                raise ValueError('compensation_locator_conflict')
            locator = exact or locator
            if locator:
                shared = self.ledger.db.execute(
                    "SELECT 1 FROM projection_operations WHERE locator=? AND outbox_id!=? "
                    "UNION ALL SELECT 1 FROM projection_receipts WHERE backend_locator=? AND operation_key!=?",
                    (locator, outbox_id, locator, op['operation_key']),
                ).fetchone()
                if shared:
                    raise ValueError('compensation_locator_shared')
                observed = backend.inspect_projection(locator)
                if observed:
                    if any(getattr(observed, k) != v for k, v in {
                        'owner_id': self.ledger.owner_id, 'memory_id': op['memory_id'],
                        'revision': op['revision'], 'generation': op['generation'],
                    }.items()) or (op['payload_digest'] and observed.payload_digest != op['payload_digest']):
                        raise ValueError('compensation_identity_mismatch')
                    if exact != locator:
                        raise ValueError('compensation_operation_unverified')
                    backend.remove_projection([locator])
                if backend.inspect_projection(locator) is not None:
                    raise ValueError('compensation_removal_unconfirmed')
            status = 'compensated'
        except Exception as exc:
            status, error = 'cleanup_required', type(exc).__name__
        with self.ledger._write_transaction():
            current = self.ledger.db.execute('SELECT compensation_token FROM projection_operations WHERE outbox_id=?', (outbox_id,)).fetchone()
            if current['compensation_token'] != token:
                return 'in_progress'
            locator_digest = hashlib.sha256((locator or '').encode()).hexdigest()
            # Successful replay verifies absence again, without duplicating evidence.
            old = self.ledger.db.execute(
                "SELECT 1 FROM rollout_compensation_receipts WHERE outbox_id=? AND status=? "
                "AND locator_digest=? AND control_epoch=? AND stop_epoch=?",
                (outbox_id, status, locator_digest, op['control_epoch'], op['stop_epoch']),
            ).fetchone()
            if not old or status != 'compensated':
                self.ledger.db.execute(
                    "INSERT INTO rollout_compensation_receipts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), op['rollout_id'], outbox_id, op['operation_key'], op['authorization_receipt'],
                     op['claim_token'], op['control_epoch'], op['stop_epoch'], op['generation'],
                     locator_digest, status, error, now_text()),
                )
            self.ledger.db.execute(
                "UPDATE projection_operations SET state=?,locator=?,compensation_token=NULL,"
                "compensation_until=NULL,updated_at=? WHERE outbox_id=?", (status, locator, now_text(), outbox_id),
            )
            self.ledger.db.execute(
                "UPDATE outbox SET status=?,last_error_class=?,lease_owner=NULL,lease_token=NULL,lease_until=NULL "
                "WHERE outbox_id=?", (status, error, outbox_id),
            )
            if status == 'compensated':
                self.ledger.db.execute(
                    "UPDATE projection_attempts SET call_finished_at=COALESCE(call_finished_at,?),"
                    "result_status='compensated' WHERE outbox_id=?", (now_text(), outbox_id),
                )
            else:
                self.ledger.db.execute("UPDATE rollback_runs SET status='cleanup_required' WHERE rollout_id=?", (op['rollout_id'],))
                self.ledger.db.execute("UPDATE rollout_runs SET status='stopping' WHERE rollout_id=?", (op['rollout_id'],))
        return status
