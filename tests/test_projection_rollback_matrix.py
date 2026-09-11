"""Independent fault matrix: public review -> projector -> authenticated rollback."""
import hashlib
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx

from sagacontext.backends import InMemoryBackend, OpenVikingBackendAdapter
from sagacontext.config import Config
from sagacontext.ledger import Ledger, CommitRequest, Scope
from sagacontext.maintenance import DeltaProposal, ScriptedJudge
from sagacontext.projection import Projector
from sagacontext.projection.protocol import ProjectionProtocol
from sagacontext.rollout import RolloutRuntime
from sagacontext.rollback import RollbackRunner


def now():
    return datetime.now(timezone.utc)


class ProjectionRollbackMatrix(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'ledger.db'
        self.ledger = Ledger(self.path, owner_id='owner')
        self.addCleanup(lambda: self.ledger.close())
        self.identity = self.ledger.register_project('workspace', self.root)
        self.config = Config(ledger_path=self.path, state_path=self.root / 'state.db',
            rollout_mode='guarded', rollout_workspaces=(str(self.root),),
            rollout_approver='ops', rollout_key_id='key',
            rollout_token_digest=hashlib.sha256(b'synthetic').hexdigest(),
            rollout_host='codex', rollout_host_version='codex-cli 0.153.4', rollout_generation='g1')
        self.runtime = RolloutRuntime(self.ledger, self.config)
        self.backend = InMemoryBackend()
        self.runtime.backend = self.backend
        self.ledger.register_backend_generation(self.backend.capabilities().backend, 'g1')
        activation = self.runtime.activate(mode='guarded', workspace=str(self.root), approval_receipt='activate', deadline=now() + timedelta(hours=1), **self.auth())
        self.rollout_id = activation['rollout_id']
        self.plan = self.ledger.db.execute('SELECT rollback_plan_digest FROM rollout_runs WHERE rollout_id=?', (self.rollout_id,)).fetchone()[0]
        self.projector = Projector(self.ledger)
        self.rollback_auth = {}

    def auth(self):
        return dict(token='synthetic', approver='ops', key_id='key', issued_at=now().isoformat(), expires_at=(now()+timedelta(minutes=3)).isoformat())

    def commit(self):
        event = self.runtime.ingest(dict(hook_event_name='UserPromptSubmit', cwd=str(self.root), host='codex',
            host_version='codex-cli 0.153.4', source_generation='g1', session_id='session', source_event_ref='event', payload_shape_digest='shape'))
        candidate = self.runtime.schedule_candidate(event, {'topic_key':'command', 'memory_type_hint':'decision'})
        proposal = DeltaProposal(candidate_id=candidate['candidate_id'], operation='new', memory_type='decision',
            scope=Scope(kind='project', project_id=self.identity['project_id']), payload={'key':'command','value':'pytest'}, evidence_ids=(event.event_id,))
        batch = self.runtime.run_batch(ScriptedJudge((proposal,)), event.session_id)
        result = self.runtime.review_batch(batch['batch_id'], 'approve', reviewer='ops', receipt='review', **self.auth())
        self.memory_id = result['memory_ids'][0]
        return result

    def claim(self):
        return self.projector.claim_next(self.backend, worker_id='worker', now=now(),
            lease_duration=timedelta(seconds=10), backend_timeout=timedelta(seconds=3), local_completion_margin=timedelta(seconds=1))

    def project(self):
        claim = self.claim()
        locator = self.projector.call_backend(claim, self.backend, now=now())
        self.assertEqual(self.projector.complete(claim, self.backend, locator, now=now()).status, 'confirmed')
        return claim, locator

    def rollback(self, receipt='rollback', phase='run'):
        return self.runtime.rollback(rollout_id=self.rollout_id, plan_digest=self.plan, phase=phase, receipt=receipt, **self.rollback_auth.setdefault(receipt, self.auth()))

    def test_nonempty_success_preserves_unrelated_memory_and_stable_receipts(self):
        self.commit(); claim, locator = self.project()
        unrelated = self.ledger.commit(CommitRequest(receipt='foreign', operation='new', memory_type='decision',
            scope=Scope(kind='project',project_id=self.identity['project_id']), payload={'key':'other','value':'keep'}))
        original_remove = self.backend.remove_projection
        def remove(locators):
            self.assertFalse(self.ledger.db.in_transaction)
            self.assertEqual(locators, [locator])
            return original_remove(locators)
        with patch.object(self.backend, 'remove_projection', side_effect=remove):
            result = self.rollback()
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(self.rollback(), result)
        self.assertEqual(self.ledger.db.execute('SELECT state FROM memories WHERE memory_id=?', (unrelated.memory_id,)).fetchone()[0], 'active')
        self.assertIsNone(self.backend.inspect_projection(locator))
        receipt = dict(self.ledger.db.execute('SELECT * FROM rollout_compensation_receipts').fetchone())
        self.assertEqual(receipt['operation_key'], claim.operation_key)
        self.assertEqual(receipt['claim_token'], claim.lease_token)
        self.assertEqual(receipt['authorization_receipt'], 'review')
        self.assertGreater(receipt['stop_epoch'], receipt['control_epoch'])
        self.assertEqual(receipt['status'], 'compensated')
        self.assertEqual(self.rollback('repeat')['status'], 'completed')
        self.assertEqual(self.ledger.db.execute('SELECT COUNT(*) FROM rollout_compensation_receipts').fetchone()[0], 1)

    def test_failure_restart_retry_preserves_failure_receipt(self):
        self.commit(); claim, locator = self.project()
        remove = self.backend.remove_projection
        def lost_response(locators):
            remove(locators)
            raise RuntimeError('lost response')
        with patch.object(self.backend, 'remove_projection', side_effect=lost_response):
            result = self.rollback()
        self.assertEqual(result['status'], 'cleanup_required')
        self.ledger.close()
        self.ledger = Ledger(self.path, owner_id='owner')
        self.runtime = RolloutRuntime(self.ledger, self.config); self.runtime.backend = self.backend
        self.assertEqual(self.rollback('retry')['status'], 'completed')
        statuses = [r[0] for r in self.ledger.db.execute('SELECT status FROM rollout_compensation_receipts ORDER BY created_at')]
        self.assertEqual(statuses, ['cleanup_required','compensated'])

    def test_late_upsert_compensates_without_forgetting_memory(self):
        self.commit(); claim = self.claim()
        locator = self.projector.call_backend(claim, self.backend, now=now())
        self.runtime.stop()
        result = self.projector.complete(claim, self.backend, locator, now=now())
        self.assertEqual(result.status, 'fenced')
        self.assertEqual(self.ledger.db.execute('SELECT state FROM memories').fetchone()[0], 'active')
        self.assertIsNone(self.backend.inspect_projection(locator))
        self.assertEqual(self.ledger.db.execute('SELECT status FROM rollout_compensation_receipts').fetchone()[0], 'compensated')

    def test_live_call_blocks_rollback_then_late_return_recovers(self):
        self.commit(); claim = self.claim()
        locator = self.projector.call_backend(claim, self.backend, now=now())
        self.assertEqual(self.rollback()['status'], 'cleanup_required')
        self.assertEqual(self.projector.complete(claim, self.backend, locator, now=now()).status, 'fenced')
        self.assertEqual(self.rollback('retry')['status'], 'completed')

    def test_compensation_failure_is_durable_and_retryable(self):
        self.commit(); claim = self.claim()
        locator = self.projector.call_backend(claim, self.backend, now=now()); self.runtime.stop()
        with patch.object(self.backend, 'remove_projection', side_effect=RuntimeError('unavailable')):
            self.assertEqual(self.projector.complete(claim, self.backend, locator, now=now()).status, 'blocked')
        self.assertEqual(self.ledger.db.execute('SELECT state FROM projection_operations').fetchone()[0], 'cleanup_required')
        self.assertEqual(self.rollback()['status'], 'completed')

    def test_wrong_locator_identity_is_not_deleted(self):
        self.commit(); claim, locator = self.project()
        self.backend.state.items[locator] = claim.projection.model_copy(update={'owner_id':'someone-else'})
        self.assertEqual(self.rollback()['status'], 'cleanup_required')
        self.assertIn(locator, self.backend.state.items)

    def test_concurrent_rollback_has_one_delete_and_no_transaction_around_backend(self):
        self.commit(); self.project()
        entered, release = threading.Event(), threading.Event()
        remove = self.backend.remove_projection
        def blocking_remove(locators):
            entered.set()
            if not release.wait(10):
                raise RuntimeError('test synchronization timeout')
            return remove(locators)
        def execute(receipt):
            ledger = Ledger(self.path, owner_id='owner')
            try:
                runtime = RolloutRuntime(ledger, self.config); runtime.backend = self.backend
                return runtime.rollback(rollout_id=self.rollout_id, plan_digest=self.plan, phase='run',receipt=receipt,**self.auth())
            finally:
                ledger.close()
        with patch.object(self.backend, 'remove_projection', side_effect=blocking_remove) as mocked, ThreadPoolExecutor(2) as pool:
            first = pool.submit(execute, 'first')
            self.assertTrue(entered.wait(5))
            try:
                second = pool.submit(execute, 'second').result(timeout=5)
                self.assertEqual(second['status'], 'in_progress')
            finally:
                release.set()
            self.assertEqual(first.result(timeout=5)['status'], 'completed')
            self.assertEqual(mocked.call_count, 1)

    def test_stop_between_claim_and_call_prevents_materialize(self):
        self.commit(); claim = self.claim(); self.runtime.stop()
        with self.assertRaisesRegex(ValueError, 'fenced'):
            self.projector.call_backend(claim, self.backend, now=now())
        self.assertEqual(self.backend.state.materialize_calls, 0)
        self.assertIsNone(self.claim())

    def test_confirmed_projection_cannot_use_automatic_compensation(self):
        self.commit(); claim, locator = self.project(); self.runtime.stop()
        with self.assertRaisesRegex(ValueError, 'requires_rollback'):
            ProjectionProtocol(self.ledger).compensate(claim.outbox_id, self.backend)
        self.assertIsNotNone(self.backend.inspect_projection(locator))

    def test_missing_backend_is_not_success(self):
        self.commit(); self.project(); self.runtime.backend = None
        self.assertEqual(self.rollback()['status'], 'cleanup_required')

    def test_openviking_adapter_exact_delete_through_public_rollback(self):
        items, deletions = {}, []
        def transport(request):
            self.assertFalse(self.ledger.db.in_transaction)
            if request.url.path.endswith('/write'):
                body = json.loads(request.content); items[body['uri']] = body['content']; result = {}
            elif request.url.path.endswith('/read'):
                uri = request.url.params['uri']
                if uri not in items:
                    return httpx.Response(404)
                result = items[uri]
            elif request.method == 'DELETE':
                uri = request.url.params['uri']; deletions.append(uri); items.pop(uri, None); result = {}
                self.assertEqual(request.url.params['recursive'], 'false')
            else:
                raise AssertionError('unexpected backend operation')
            return httpx.Response(200, json={'status':'ok','result':result})
        backend = OpenVikingBackendAdapter('http://backend.invalid','synthetic',owner_id='owner',
            namespace='viking://user/test/memories/sagacontext/fault-matrix', transport=httpx.MockTransport(transport))
        self.addCleanup(backend.close)
        self.ledger.db.execute('DELETE FROM backend_generations'); self.ledger.db.commit()
        self.backend = backend; self.runtime.backend = backend
        self.ledger.register_backend_generation(backend.capabilities().backend, 'g1')
        self.commit(); claim, locator = self.project()
        self.assertEqual(self.rollback()['status'], 'completed')
        self.assertEqual(deletions, [locator]); self.assertEqual(items, {})

    def test_restart_after_materialize_before_local_receipt(self):
        self.commit(); claim = self.claim()
        locator = self.projector.call_backend(claim, self.backend, now=now())
        self.runtime.stop()
        # Simulated crash: only the persisted call intent and remote object survive.
        self.ledger.close(); self.ledger = Ledger(self.path, owner_id='owner')
        self.runtime = RolloutRuntime(self.ledger, self.config); self.runtime.backend = self.backend
        with self.ledger._write_transaction():
            self.ledger.db.execute("UPDATE projection_operations SET claim_until=?", ((now()-timedelta(seconds=1)).isoformat(),))
        self.assertEqual(self.rollback()['status'], 'completed')
        self.assertIsNone(self.backend.inspect_projection(locator))
        self.assertEqual(self.ledger.db.execute('SELECT COUNT(*) FROM rollout_compensation_receipts').fetchone()[0], 1)

    def test_stop_during_inspect_cannot_confirm(self):
        self.commit(); claim = self.claim()
        locator = self.projector.call_backend(claim, self.backend, now=now())
        inspect = self.backend.inspect_projection
        def stop_and_inspect(value):
            self.runtime.stop()
            return inspect(value)
        with patch.object(self.backend, 'inspect_projection', side_effect=stop_and_inspect):
            self.assertEqual(self.projector.complete(claim,self.backend,locator,now=now()).status,'blocked')
        self.assertEqual(self.ledger.db.execute('SELECT COUNT(*) FROM projection_receipts').fetchone()[0],0)
        self.assertEqual(self.rollback()['status'],'completed')

    def test_phase_receipt_cannot_execute_later_stages(self):
        self.commit(); claim, locator = self.project()
        self.assertEqual(self.rollback(phase='memory')['status'],'phase_completed')
        self.assertIsNotNone(self.backend.inspect_projection(locator))
        self.assertEqual(self.ledger.db.execute('SELECT status FROM candidates').fetchone()[0], 'settled')
        with self.assertRaisesRegex(ValueError,'receipt_conflict'):
            self.rollback(phase='run')
        self.assertEqual(self.rollback('finish')['status'],'completed')

    def test_changed_head_blocks_forget(self):
        self.commit(); self.project()
        self.ledger.commit(CommitRequest(receipt='later',operation='refine',memory_id=self.memory_id,
            expected_revision=1,memory_type='decision',scope=Scope(kind='project',project_id=self.identity['project_id']),payload={'key':'later','value':'keep'}))
        self.assertEqual(self.rollback()['status'],'cleanup_required')
        self.assertEqual(self.ledger.db.execute('SELECT state FROM memories').fetchone()[0],'active')

    def test_crashed_rollback_lease_can_be_reclaimed(self):
        self.commit(); self.project()
        self.assertEqual(self.rollback(phase='memory')['status'],'phase_completed')
        with self.ledger._write_transaction():
            self.ledger.db.execute("UPDATE rollback_runs SET lease_token='dead-worker',lease_until=?", ((now()-timedelta(seconds=1)).isoformat(),))
        self.assertEqual(self.rollback('recover')['status'],'completed')

    def test_shadow_nonempty_quarantines_without_memory_write(self):
        # Guarded before review has the same isolated-data cleanup boundary as shadow.
        event=self.runtime.ingest(dict(hook_event_name='UserPromptSubmit',cwd=str(self.root),host='codex',host_version='codex-cli 0.153.4',source_generation='g1',session_id='shadow',source_event_ref='shadow',payload_shape_digest='shape'))
        self.runtime.schedule_candidate(event,{'topic_key':'shadow','memory_type_hint':'decision'})
        result=self.rollback()
        self.assertEqual(result['status'],'completed')
        self.assertEqual(result['audit']['candidates'],1)
        self.assertEqual(result['audit']['memory_targets'],0)
        self.assertEqual(result['audit']['residual_candidates'],0)
