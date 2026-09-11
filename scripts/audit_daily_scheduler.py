"""Offline scheduler acceptance; synthetic events, ScriptedJudge, in-memory backend.

Never loads local configuration, contacts a provider, or enables the real workspace.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from datetime import timedelta
from pathlib import Path

from sagacontext.application import Application
from sagacontext.backends import InMemoryBackend
from sagacontext.config import Config
from sagacontext.daily_report import annotate, pending, report
from sagacontext.ledger import Scope
from sagacontext.maintenance import DeltaProposal, ScriptedJudge
from sagacontext.scheduler import DailyScheduler, now


class OfflineBackend(InMemoryBackend):
    def close(self):
        pass


def scenario(mode: str) -> dict:
    result = {'mode': mode, 'checks': {}, 'environment': 'synthetic-isolated'}
    def check(name, condition):
        result['checks'][name] = bool(condition)
        if not condition:
            raise AssertionError(name)
    with tempfile.TemporaryDirectory(prefix='sagacontext-scheduler-audit-') as temporary:
        root = Path(temporary).resolve()
        config = Config(state_path=root/'state.db', ledger_path=root/'ledger.db',
            rollout_workspaces=(str(root),), rollout_mode=mode, rollout_stop_file=root/'STOP',
            rollout_approver='synthetic-operator', rollout_key_id='synthetic-key',
            rollout_token_digest=hashlib.sha256(b'synthetic-token').hexdigest(),
            llm_base_url='http://judge.invalid', llm_api_key='synthetic', llm_model='scripted')
        backend = OfflineBackend()
        app = Application(config)
        rid = None
        rollback_requests = {}
        def attach():
            app.rollout_backend = app.rollout.backend = backend
        def auth():
            return dict(token='synthetic-token', approver=config.rollout_approver,
                key_id=config.rollout_key_id, issued_at=now().isoformat(),
                expires_at=(now()+timedelta(minutes=3)).isoformat())
        def record(kind, key, session='source', **extra):
            return dict(host='codex', host_version=config.rollout_host_version,
                source_generation='g1', session_id=session, cwd=str(root),
                hook_event_name=kind, source_event_ref=key, **extra)
        def rollback(receipt):
            plan = app.ledger.db.execute('SELECT rollback_plan_digest FROM rollout_runs WHERE rollout_id=?', (rid,)).fetchone()[0]
            if receipt not in rollback_requests:
                rollback_requests[receipt] = dict(rollout_id=rid, plan_digest=plan, phase='run', receipt=receipt, **auth())
            return app.rollout.rollback(**rollback_requests[receipt])
        try:
            attach()
            identity = app.ledger.register_project('offline-scheduler', root)
            check('initially_off', app.rollout.mode.value == 'off')
            rid = app.rollout.activate(mode=mode, workspace=str(root), approval_receipt='activate',
                deadline=now()+timedelta(hours=1), **auth())['rollout_id']
            result['rollout_id'] = rid
            event = app.rollout.ingest(record('UserPromptSubmit', 'prompt', prompt='Remember to run pytest.'))
            candidate = app.ledger.db.execute('SELECT candidate_id FROM rollout_candidate_reservations').fetchone()[0]
            proposal = DeltaProposal(candidate_id=candidate, operation='new', memory_type='decision',
                scope=Scope(kind='project', project_id=identity['project_id']),
                payload={'key':'verification', 'value':'Run pytest.'}, evidence_ids=(event.event_id,))
            scheduler = DailyScheduler(app, ScriptedJudge((proposal,)))
            check('waits_for_checkpoint', scheduler.tick()['status'] == 'idle')
            app.rollout.ingest(record('Stop', 'checkpoint'))
            proposed = scheduler.tick()
            check('creates_proposal', proposed['status'] == 'proposed')
            check('no_memory_before_review', app.ledger.db.execute('SELECT COUNT(*) FROM memories').fetchone()[0] == 0)
            app.close()
            app = Application(config)
            attach()
            scheduler = DailyScheduler(app, ScriptedJudge(()))
            check('restart_waits', scheduler.tick()['status'] == 'idle')
            check('restart_does_not_commit', app.ledger.db.execute('SELECT COUNT(*) FROM memories').fetchone()[0] == 0)
            if mode == 'guarded':
                check('pending_queue', len(pending(app.ledger)) == 1)
                approved = app.rollout.review_batch(proposed['batch_id'], 'approve',
                    reviewer=config.rollout_approver, receipt='synthetic-review', **auth())
                check('review_commits_once', len(approved['memory_ids']) == 1)
                check('scheduler_projects', scheduler.tick()['projection_status'] == 'confirmed')
                check('projection_not_repeated', scheduler.tick()['projection_status'] == 'idle' and backend.state.materialize_calls == 1)
                bundle, injection = app.rollout.session_start(record('SessionStart', 'recall', 'next'), backend, query='pytest')
                check('simulated_recall', bool(bundle) and tuple(approved['memory_ids']) == injection.memory_ids)
                target = app.ledger.db.execute("SELECT receipt_id FROM rollout_audit WHERE rollout_id=? AND kind='injection'", (rid,)).fetchone()[0]
                for receipt, kind, target_id, value in (
                    ('valid', 'candidate_valid', candidate, 'yes'),
                    ('modified', 'review_modified', proposed['batch_id'], 'no'),
                    ('recall-label', 'wrong_recall', target, 'no'),
                    ('consumption-label', 'consumed', target, 'unknown'),
                ):
                    annotate(app.ledger, rid, receipt_id=receipt, kind=kind, target_id=target_id, value=value)
                metrics = report(app.ledger, rid)
                check('unknown_is_not_consumed', metrics['consumption_rate'] is None and metrics['consumption_reports'] == 0)
                check('labels_do_not_admit_expansion', metrics['scope_expansion_admitted'] is False)
            else:
                check('shadow_has_no_review_queue', pending(app.ledger) == [])
                check('shadow_has_no_projections', not backend.items)
                bundle, injection = app.rollout.session_start(record('SessionStart', 'recall', 'next'), backend, query='pytest')
                check('shadow_cannot_inject', not bundle and injection.status == 'blocked')
            result['quality_report'] = report(app.ledger, rid)
            result['before_cleanup'] = app.rollout.rollback_runner.audit(rid)
        except Exception as error:
            result['error_class'] = type(error).__name__
        finally:
            try:
                if rid:
                    cleaned = rollback('cleanup')
                    result['rollback_status'] = cleaned['status']
                    check('rollback_completed', cleaned['status'] == 'completed')
                    check('duplicate_rollback_receipt', rollback('cleanup') == cleaned)
                    check('new_receipt_reverifies', rollback('reverify')['status'] == 'completed')
                    result['after_cleanup'] = app.rollout.rollback_runner.audit(rid)
                    result['final_report'] = report(app.ledger, rid)
                    check('zero_ledger_residuals', all(v == 0 for k,v in result['after_cleanup'].items() if k.startswith('residual_')))
                    check('zero_backend_residuals', not backend.items)
                    check('ends_off', app.rollout.mode.value == 'off')
            except Exception as error:
                result['cleanup_error_class'] = type(error).__name__
            finally:
                app.close()
    result['temporary_root_removed'] = not root.exists()
    result['passed'] = (all(result['checks'].values()) and result['temporary_root_removed']
        and 'error_class' not in result and 'cleanup_error_class' not in result)
    return result


def run(output: Path) -> dict:
    if output.exists():
        raise ValueError('refusing_to_overwrite_audit')
    scenarios = [scenario(mode) for mode in ('shadow', 'guarded')]
    result = {'schema':'daily-scheduler-offline-v1', 'created_at':now().isoformat(),
        'evidence':'synthetic-isolated-only', 'judge':'ScriptedJudge', 'backend':'InMemoryBackend',
        'provider_calls':0, 'daily_quality_samples':0, 'real_host_consumption':'not_exercised',
        'real_workspace_configuration_touched':False, 'scope_expansion_admitted':False,
        'review_source':'explicit synthetic operator action',
        'retention':'control/event evidence and quarantined rows retained until temporary directory removal',
        'scenarios':scenarios, 'passed':all(s['passed'] for s in scenarios)}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2)+'\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    outcome = run(args.output)
    print(json.dumps({'passed':outcome['passed'], 'scenarios':[
        {'mode':s['mode'], 'passed':s['passed'], 'checks':s['checks'],
         'error_class':s.get('error_class'), 'cleanup_error_class':s.get('cleanup_error_class')}
        for s in outcome['scenarios']]}))
    raise SystemExit(0 if outcome['passed'] else 1)
