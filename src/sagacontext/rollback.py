"""Durable, rollout-scoped rollback execution with resumable phase receipts."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

from .ledger import Ledger
from .projection.protocol import ProjectionProtocol, now_text

PHASES = ("freeze", "memory", "shadow", "locator", "verify")


class RollbackRunner:
    def __init__(self, ledger: Ledger):
        self.ledger = ledger
        self.protocol = ProjectionProtocol(ledger)

    def run(self, rollout_id: str, plan_digest: str, *, fault: str | None = None,
            backend=None, through: str = "verify") -> dict:
        if through not in PHASES:
            raise ValueError('rollback_phase_not_implemented')
        if self.ledger.db.in_transaction:
            raise ValueError('rollback_requires_durable_transaction_boundary')
        now = datetime.now(timezone.utc)
        token = str(uuid.uuid4())
        with self.ledger._write_transaction():
            run = self.ledger.db.execute(
                'SELECT * FROM rollout_runs WHERE rollout_id=? AND owner_id=?',
                (rollout_id, self.ledger.owner_id),
            ).fetchone()
            if not run or run['rollback_plan_digest'] != plan_digest:
                raise ValueError('rollback_plan_mismatch')
            existing = self.ledger.db.execute('SELECT * FROM rollback_runs WHERE rollout_id=?', (rollout_id,)).fetchone()
            if existing and existing['lease_token'] and existing['lease_until'] > now.isoformat():
                return {'rollout_id': rollout_id, 'rollback_id': existing['rollback_id'], 'status': 'in_progress'}
            rollback_id = existing['rollback_id'] if existing else str(uuid.uuid4())
            if not existing:
                self.ledger.db.execute(
                    'INSERT INTO rollback_runs(rollback_id,rollout_id,owner_id,plan_digest,status,control_epoch,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)',
                    (rollback_id, rollout_id, self.ledger.owner_id, plan_digest, 'running', run['control_epoch'] + 1, now.isoformat(), now.isoformat()),
                )
                self.ledger.db.execute(
                    "UPDATE rollout_runs SET status='stopping',control_epoch=control_epoch+1,stopped_at=?,stop_reason='rollback' WHERE rollout_id=?",
                    (now.isoformat(), rollout_id),
                )
            self.ledger.db.execute(
                "UPDATE rollback_runs SET status='running',lease_token=?,lease_until=?,updated_at=? WHERE rollback_id=?",
                (token, (now + timedelta(seconds=120)).isoformat(), now.isoformat(), rollback_id),
            )
        try:
            for index, phase in enumerate(PHASES[:PHASES.index(through) + 1], 1):
                with self.ledger._write_transaction():
                    self._owns(rollback_id, token)
                    step = self.ledger.db.execute('SELECT * FROM rollback_steps WHERE rollback_id=? AND step_no=?', (rollback_id, index)).fetchone()
                    # Reverify locators on repeated runs: a late remote write may have
                    # completed after the previous absence check.
                    if step and step['status'] == 'done' and phase not in {'locator', 'verify'}:
                        continue
                    target = self._targets(rollout_id, phase)
                    if not step:
                        self.ledger.db.execute(
                            'INSERT INTO rollback_steps VALUES (?,?,?,?,?,?,?,?,?)',
                            (rollback_id, index, phase, 'running', json.dumps(target, sort_keys=True), str(uuid.uuid4()), None, now_text(), None),
                        )
                    else:
                        self.ledger.db.execute("UPDATE rollback_steps SET status='running' WHERE rollback_id=? AND step_no=?", (rollback_id, index))
                try:
                    if fault == phase:
                        raise RuntimeError('injected_compensation_failure')
                    self._apply(rollout_id, phase, target, backend=backend)
                    status, error = 'done', None
                except Exception as exc:
                    status, error = 'cleanup_required', type(exc).__name__
                with self.ledger._write_transaction():
                    self._owns(rollback_id, token)
                    self.ledger.db.execute(
                        'UPDATE rollback_steps SET status=?,error_class=?,finished_at=? WHERE rollback_id=? AND step_no=?',
                        (status, error, now_text(), rollback_id, index),
                    )
                    if status == 'cleanup_required':
                        self.ledger.db.execute("UPDATE rollback_runs SET status='cleanup_required',updated_at=? WHERE rollback_id=?", (now_text(), rollback_id))
                        return {'rollout_id': rollout_id, 'rollback_id': rollback_id, 'status': status, 'phase': phase, 'error_class': error, 'audit': self.audit(rollout_id)}
            status = 'completed' if through == 'verify' else 'phase_completed'
            with self.ledger._write_transaction():
                self._owns(rollback_id, token)
                self.ledger.db.execute('UPDATE rollback_runs SET status=?,updated_at=? WHERE rollback_id=?', (status, now_text(), rollback_id))
                if status == 'completed':
                    # Verify again under the same lock as finalization.
                    self._verify(rollout_id)
                    self.ledger.db.execute("UPDATE rollout_runs SET status='stopped' WHERE rollout_id=?", (rollout_id,))
            return {'rollout_id': rollout_id, 'rollback_id': rollback_id, 'status': status, 'phase': through, 'audit': self.audit(rollout_id)}
        finally:
            with self.ledger._write_transaction():
                self.ledger.db.execute('UPDATE rollback_runs SET lease_token=NULL,lease_until=NULL WHERE rollback_id=? AND lease_token=?', (rollback_id, token))

    def _owns(self, rollback_id, token):
        row = self.ledger.db.execute('SELECT lease_token,lease_until FROM rollback_runs WHERE rollback_id=?', (rollback_id,)).fetchone()
        if not row or row['lease_token'] != token or row['lease_until'] <= now_text():
            raise ValueError('rollback_lease_fenced')
        self.ledger.db.execute('UPDATE rollback_runs SET lease_until=? WHERE rollback_id=?', ((datetime.now(timezone.utc) + timedelta(seconds=120)).isoformat(), rollback_id))

    def _targets(self, rollout_id, phase):
        if phase == 'memory':
            return [dict(r) for r in self.ledger.db.execute('SELECT memory_id,revision,operation FROM rollout_commits WHERE rollout_id=?', (rollout_id,))]
        if phase == 'locator':
            return [dict(r) for r in self.ledger.db.execute('SELECT DISTINCT o.* FROM outbox o JOIN rollout_commits c ON c.outbox_id=o.outbox_id WHERE c.rollout_id=?', (rollout_id,))]
        if phase == 'shadow':
            return [dict(r) for r in self.ledger.db.execute('SELECT candidate_id FROM rollout_candidates WHERE rollout_id=?', (rollout_id,))]
        return []

    def _apply(self, rollout_id, phase, targets, *, backend=None):
        if phase == 'memory':
            with self.ledger._write_transaction():
                for item in targets:
                    # Forget is safe only for a memory created exclusively by this
                    # rollout; previous revisions require a separately designed undo.
                    head = self.ledger.db.execute('SELECT current_revision,state FROM memories WHERE memory_id=? AND owner_id=?', (item['memory_id'], self.ledger.owner_id)).fetchone()
                    other = self.ledger.db.execute('SELECT 1 FROM rollout_commits WHERE memory_id=? AND rollout_id!=?', (item['memory_id'], rollout_id)).fetchone()
                    if not head or other or item['operation'] != 'new' or head['current_revision'] != item['revision']:
                        raise ValueError('rollback_memory_not_exclusively_owned')
                    result = self.ledger.forget(item['memory_id'], f"rollback:{rollout_id}:{item['memory_id']}")
                    if result['status'] == 'needs_action':
                        raise ValueError('rollback_forget_failed')
        elif phase == 'shadow':
            with self.ledger._write_transaction():
                shared_batch = self.ledger.db.execute(
                    "SELECT 1 FROM rollout_batches mine JOIN rollout_batches other USING(batch_id) "
                    "WHERE mine.rollout_id=? AND other.rollout_id!=mine.rollout_id UNION ALL "
                    "SELECT 1 FROM batch_candidates bc JOIN rollout_batches b USING(batch_id) WHERE b.rollout_id=? "
                    "AND NOT EXISTS (SELECT 1 FROM rollout_candidates c WHERE c.rollout_id=b.rollout_id AND c.candidate_id=bc.candidate_id)",
                    (rollout_id, rollout_id),
                ).fetchone()
                if shared_batch:
                    raise ValueError('rollback_shared_batch')
                for item in targets:
                    if self.ledger.db.execute('SELECT 1 FROM rollout_candidates WHERE candidate_id=? AND rollout_id!=?', (item['candidate_id'], rollout_id)).fetchone():
                        raise ValueError('rollback_shared_candidate')
                self.ledger.db.execute("UPDATE candidates SET status='quarantined',claim_token=NULL,active_batch_id=NULL WHERE candidate_id IN (SELECT candidate_id FROM rollout_candidates WHERE rollout_id=?)", (rollout_id,))
                self.ledger.db.execute("UPDATE proposals SET status='rejected' WHERE batch_id IN (SELECT batch_id FROM rollout_batches WHERE rollout_id=?) AND status IN ('proposed','awaiting_review')", (rollout_id,))
                self.ledger.db.execute("UPDATE batches SET status='settled',lease_owner=NULL,lease_token=NULL,lease_until=NULL WHERE batch_id IN (SELECT batch_id FROM rollout_batches WHERE rollout_id=?)", (rollout_id,))
                self.ledger.db.execute("UPDATE batch_candidates SET released_at=COALESCE(released_at,?) WHERE batch_id IN (SELECT batch_id FROM rollout_batches WHERE rollout_id=?)", (now_text(), rollout_id))
        elif phase == 'locator':
            for item in targets:
                if 'outbox_id' not in item:
                    raise RuntimeError('locator_backend_required')
                with self.ledger._write_transaction():
                    run = self.protocol.ownership(item['outbox_id'])
                    if not run or run['rollout_id'] != rollout_id:
                        raise ValueError('rollback_projection_authorization_missing')
                    receipt = self.ledger.db.execute('SELECT * FROM projection_receipts WHERE operation_key=?', (self.protocol.register(item, run),)).fetchone()
                    if receipt:
                        self.ledger.db.execute('UPDATE projection_operations SET locator=COALESCE(locator,?),payload_digest=COALESCE(payload_digest,?) WHERE outbox_id=?', (receipt['backend_locator'], receipt['payload_digest'], item['outbox_id']))
                if self.protocol.compensate(item['outbox_id'], backend, rollback=True) != 'compensated':
                    raise RuntimeError('rollback_compensation_pending')
            with self.ledger._write_transaction():
                # Settle forget-generated deletes only for these exact identities.
                for item in targets:
                    self.ledger.db.execute("UPDATE outbox SET status='compensated',lease_owner=NULL,lease_token=NULL,lease_until=NULL WHERE action='delete' AND backend=? AND generation=? AND memory_id=? AND revision=?", (item['backend'], item['generation'], item['memory_id'], item['revision']))
                    self.ledger.db.execute("UPDATE deletion_jobs SET status='completed' WHERE memory_id=? AND owner_id=?", (item['memory_id'], self.ledger.owner_id))
        elif phase == 'verify':
            self._verify(rollout_id)

    def _verify(self, rollout_id):
        audit = self.audit(rollout_id)
        if any(audit[key] for key in ('residual_memories', 'residual_candidates', 'residual_operations', 'residual_outbox')):
            raise RuntimeError('rollback_residual_resources')

    def audit(self, rollout_id):
        def count(query):
            return self.ledger.db.execute(query, (rollout_id,)).fetchone()[0]
        return {
            'candidates': count('SELECT COUNT(*) FROM rollout_candidates WHERE rollout_id=?'),
            'events_retained_for_audit': count('SELECT COUNT(*) FROM rollout_events WHERE rollout_id=?'),
            'batches_retained_for_audit': count('SELECT COUNT(*) FROM rollout_batches WHERE rollout_id=?'),
            'memory_targets': count('SELECT COUNT(DISTINCT memory_id) FROM rollout_commits WHERE rollout_id=?'),
            'projection_targets': count('SELECT COUNT(DISTINCT outbox_id) FROM rollout_commits WHERE rollout_id=?'),
            'compensation_receipts': count('SELECT COUNT(*) FROM rollout_compensation_receipts WHERE rollout_id=?'),
            'residual_memories': count("SELECT COUNT(*) FROM memories m WHERE state!='deleted' AND EXISTS (SELECT 1 FROM rollout_commits c WHERE c.memory_id=m.memory_id AND c.rollout_id=?)"),
            'residual_candidates': count("SELECT COUNT(*) FROM candidates c JOIN rollout_candidates r USING(candidate_id) WHERE r.rollout_id=? AND c.status!='quarantined'"),
            'residual_operations': count("SELECT COUNT(*) FROM projection_operations WHERE rollout_id=? AND state NOT IN ('compensated','cancelled')"),
            'residual_outbox': count("SELECT COUNT(*) FROM outbox o WHERE status NOT IN ('compensated','cancelled') AND EXISTS (SELECT 1 FROM rollout_commits c WHERE c.memory_id=o.memory_id AND c.rollout_id=?)"),
        }
