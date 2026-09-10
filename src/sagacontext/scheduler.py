"""Persistent batches/outbox drive daily work; no in-memory job queue."""
from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

from .application import Application
from .llm import JudgeError, OpenAIJudge
from .maintenance.judge import OpenAIProposalJudge
from .backends import BackendDefiniteError


def now():
    return datetime.now(timezone.utc)


class FencedJudge:
    def __init__(self, judge, runtime, run):
        self.inner, self.runtime, self.run = judge, runtime, run
        self.version = judge.version

    def judge(self, batch):
        self.check()
        result = self.inner.judge(batch)
        self.check()
        return result

    def check(self):
        current = self.runtime._run()
        if not current or any(current[k] != self.run[k] for k in ('rollout_id', 'control_epoch')):
            raise JudgeError('rollout_stopped', False)


class FencedBackend:
    """Propagate STOP to control epoch before/after external calls.

    Return a late locator to Projector so its durable compensation protocol can
    delete it; do not discard an uncertain/late backend result.
    """
    def __init__(self, backend, runtime):
        self.inner, self.runtime = backend, runtime
        self.timeout = getattr(backend, 'timeout', 5)

    def __getattr__(self, name):
        value = getattr(self.inner, name)
        if name not in {'materialize', 'locate_projection', 'inspect_projection', 'search', 'remove_projection'}:
            return value
        def call(*args, **kwargs):
            run = self.runtime._run()
            if name == 'materialize' and not run:
                raise BackendDefiniteError('rollout_stopped')
            try:
                return value(*args, **kwargs)
            finally:
                self.runtime._run()
        return call


class DailyScheduler:
    def __init__(self, application, judge=None):
        self.app = application
        self.runtime = application.rollout
        self.ledger = application.ledger
        c = application.config
        self.judge = judge or OpenAIProposalJudge(OpenAIJudge(c.llm_base_url, c.llm_api_key, c.llm_model, timeout=30, temperature=0))
        self.worker_id = 'daily-' + uuid.uuid4().hex

    def _batch(self, run):
        """Freeze candidates and rollout association under one write lock."""
        with self.ledger._write_transaction():
            current = self.runtime._run()
            if not current or current['rollout_id'] != run['rollout_id']:
                return None
            # Resume durable claims first. Active leases are never stolen.
            row = self.ledger.db.execute(
                "SELECT b.batch_id FROM batches b JOIN rollout_batches rb USING(batch_id) "
                "WHERE rb.rollout_id=? AND ((b.status IN ('pending','retry') AND "
                "(b.next_attempt_at IS NULL OR b.next_attempt_at<=?)) OR "
                "(b.status IN ('running','proposed') AND b.lease_until<?)) ORDER BY b.created_at LIMIT 1",
                (run['rollout_id'], now().isoformat(), now().isoformat())).fetchone()
            if row:
                return row[0]
            # Batch at a host checkpoint, not on every prompt. Recovery reads
            # journal state; no volatile wakeup is required to preserve work.
            row = self.ledger.db.execute(
                "SELECT c.session_id,c.task_id,c.scope_hint_json FROM candidates c JOIN rollout_candidates rc USING(candidate_id) "
                "WHERE rc.rollout_id=? AND c.status='pending' AND c.active_batch_id IS NULL "
                "AND EXISTS (SELECT 1 FROM events e JOIN rollout_events re USING(event_id) "
                "WHERE re.rollout_id=rc.rollout_id AND e.session_id=c.session_id AND "
                "e.event_kind IN ('checkpoint_requested','session_closed') AND e.ingest_sequence>c.created_sequence) "
                "ORDER BY c.created_sequence LIMIT 1", (run['rollout_id'],)).fetchone()
            if not row:
                return None
            # Await the outstanding review before creating the next batch in
            # this session; otherwise its newly accepted memory is not an anchor.
            outstanding = self.ledger.db.execute(
                "SELECT 1 FROM batches b JOIN rollout_batches rb USING(batch_id) WHERE rb.rollout_id=? "
                "AND b.session_id=? AND b.status IN ('pending','running','retry','proposed','awaiting_review')",
                (run['rollout_id'], row['session_id'])).fetchone()
            if outstanding:
                return None
            import json
            project_id = json.loads(row['scope_hint_json']).get('project_id')
            # Select readable current memories using the existing scope policy.
            from .ledger import TaskContext
            ids = [r[0] for r in self.ledger.db.execute("SELECT memory_id FROM memories WHERE owner_id=? AND state='active'", (self.ledger.owner_id,))]
            readable = self.ledger.get_current(ids, TaskContext(owner_id=self.ledger.owner_id, project_id=project_id, workspace_id=run['workspace_id']))
            anchors = tuple(item.memory_id for item in readable)[:20]
            self.runtime.batches.judge_version = self.judge.version
            batch = self.runtime.batches.request_batch(row['session_id'], row['task_id'], anchors)
            self.ledger.db.execute('INSERT INTO rollout_batches VALUES (?,?)', (run['rollout_id'], batch.batch_id))
            return batch.batch_id

    def tick(self):
        run = self.runtime._run()
        if not run:
            return {'status': 'off'}
        config = self.app.config
        if not all((config.llm_base_url, config.llm_api_key, config.llm_model)):
            return {'status': 'blocked_configuration'}
        if run['mode'] == 'guarded' and self.app.rollout_backend:
            with self.ledger._write_transaction():
                if self.runtime._run():
                    self.ledger.register_backend_generation(self.app.rollout_backend.capabilities().backend, run['generation'])
        batch_id = self._batch(run)
        result = {'status': 'idle'}
        if batch_id:
            started = time.monotonic()
            outcome = self.runtime.worker.run_once(FencedJudge(self.judge, self.runtime, run),
                worker_id=self.worker_id, now=now(), lease_duration=timedelta(seconds=45),
                stop_after_proposals=True, max_attempts=3, target_batch_id=batch_id)
            with self.ledger._write_transaction():
                current = self.runtime._run()
                if outcome.status == 'retry':
                    self.ledger.db.execute("UPDATE batches SET next_attempt_at=? WHERE batch_id=? AND status='retry'", ((now()+timedelta(seconds=10)).isoformat(),batch_id))
                if outcome.status == 'proposed' and current and current['rollout_id'] == run['rollout_id'] and current['control_epoch'] == run['control_epoch']:
                    if run['mode'] == 'guarded':
                        self.runtime._hold_for_review(batch_id)
                if outcome.status != 'idle':
                    self.ledger.record_rollout_receipt('judge_scheduled', {'batch_id':batch_id,'status':outcome.status,
                        'latency_ms':round((time.monotonic()-started)*1000)}, rollout_id=run['rollout_id'])
            result = {'status':outcome.status, 'batch_id':batch_id}
        current = self.runtime._run()
        if current and current['rollout_id'] == run['rollout_id'] and run['mode']=='guarded' and self.app.rollout_backend:
            projected = self.app.projector.drain_once(FencedBackend(self.app.rollout_backend, self.runtime),
                worker_id=self.worker_id, now=now(), backend_timeout=timedelta(seconds=5),
                local_completion_margin=timedelta(seconds=2), lease_duration=timedelta(seconds=45),
                verification_timeout=timedelta(seconds=10), rollout_id=run['rollout_id'])
            result['projection_status'] = projected.status
        return result


class SchedulerThread:
    def __init__(self, config):
        self.config = config
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, name='sagacontext-daily', daemon=True)
        self.error_class = None

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=50)

    def run(self):
        # Independent connection; do not share the HTTP connection/transaction.
        try:
            with Application(self.config) as app:
                scheduler = DailyScheduler(app)
                while not self.stop_event.is_set():
                    try:
                        scheduler.tick()
                        self.error_class = None
                    except Exception as error:
                        # No exception messages: provider payloads may be sensitive.
                        if self.error_class != type(error).__name__:
                            self.error_class = type(error).__name__
                            app.ledger.record_rollout_receipt('scheduler_error', {'error_class':self.error_class})
                    self.stop_event.wait(2)
        except Exception as error:
            self.error_class = type(error).__name__
