"""Bounded daily observation with independent, authenticated cleanup.

Only the immutable activation plan opts a rollout into this local watchdog.
No proposal approvals or synthetic quality/consumption labels are produced here.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import plistlib
import shlex
import subprocess
import sys
import tempfile
import tomllib
import uuid

import httpx

from .application import Application
from .config import Config
from .daily_report import report

SCHEMA = 'daily-observation-v1'
LABEL = 'com.sagacontext.observation'
PLAN = {'schema_version':'rollout-plan-v1', 'resources':'rollout-scoped', 'compensate_inflight':True,
        'observation':{'schema':SCHEMA, 'review_operations':['new'], 'scope':'current-project',
                       'cleanup':'local-current-key-watchdog', 'source':'new-workspace-events'}}


def utcnow():
    return datetime.now(timezone.utc)


def auth(config, token):
    now = utcnow()
    return dict(token=token, approver=config.rollout_approver, key_id=config.rollout_key_id,
                issued_at=now.isoformat(), expires_at=(now+timedelta(minutes=4)).isoformat())


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.snapshot-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def local_lock(config):
    root = config.ledger_path.parent / 'observations'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(root/'watchdog.lock', os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, 'w') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def local_token(config):
    settings = tomllib.loads((config.ledger_path.parent/'config.toml').read_text())
    return Path(settings['rollout']['token_file']).expanduser().read_text().strip()


def _window(app, rollout_id):
    row = app.ledger.db.execute('SELECT * FROM rollout_runs WHERE rollout_id=? AND owner_id=?',
                               (rollout_id, app.owner_id)).fetchone()
    if not row or json.loads(row['rollback_plan_json']) != PLAN:
        raise ValueError('observation_plan_mismatch')
    if row['workspace_root'] != str(app.rollout.host.allowed_workspace()):
        raise ValueError('observation_workspace_mismatch')
    return dict(row)


def start_window(app, token, *, hours=24):
    if type(hours) is not int or not 1 <= hours <= 24:
        raise ValueError('invalid_observation_hours')
    config = app.config
    if (config.rollout_stop_file != config.ledger_path.parent/'STOP'
            or config.rollout_mode != 'guarded'):
        raise ValueError('observation_configuration_required')
    # Activation and enrollment are durable together. The caller holds local_lock.
    with app.ledger._write_transaction():
        if app.ledger.db.execute("SELECT 1 FROM rollout_runs WHERE status IN ('running','draining','stopping','cleanup_required')").fetchone():
            raise ValueError('rollout_already_live')
        if app.ledger.db.execute("SELECT 1 FROM rollout_audit a WHERE a.kind='observation_opened' AND NOT EXISTS (SELECT 1 FROM rollout_audit c WHERE c.kind='observation_closed' AND c.rollout_id=a.rollout_id)").fetchone():
            raise ValueError('observation_cleanup_pending')
        try:
            config.rollout_stop_file.unlink(missing_ok=True)
            run = app.rollout.activate(mode='guarded', workspace=str(app.rollout.host.allowed_workspace()),
                approval_receipt=str(uuid.uuid4()), deadline=utcnow()+timedelta(hours=hours),
                max_sessions=10, max_candidates=20, rollback_plan=PLAN, **auth(config, token))
            app.ledger.record_rollout_receipt('observation_opened',
                {'schema':SCHEMA, 'deadline':run['deadline'], 'review_actor':'user_delegated_operator',
                 'session_limit':10, 'candidate_limit':20}, rollout_id=run['rollout_id'])
        except BaseException:
            config.rollout_stop_file.touch(mode=0o600)
            raise
    return run


def snapshot(app, rollout_id):
    run = _window(app, rollout_id)
    result = report(app.ledger, rollout_id)
    pending = [r[0] for r in app.ledger.db.execute("SELECT b.batch_id FROM batches b JOIN rollout_batches rb USING(batch_id) WHERE rb.rollout_id=? AND b.status='awaiting_review'", (rollout_id,))]
    return {'schema':SCHEMA, 'updated_at':utcnow().isoformat(), 'rollout_id':rollout_id,
            'deadline':run['deadline'], 'source':'new-workspace-events',
            'pending_batch_ids':pending, 'review_actor':'user_delegated_operator',
            'automatic_review':False, 'report':result}


def stop_reason(app, run, *, healthy=True, now=None):
    now = now or utcnow()
    db, rid = app.ledger.db, run['rollout_id']
    if datetime.fromisoformat(run['deadline']) <= now:
        return 'deadline'
    if app.config.rollout_stop_file.exists() or app.config.rollout_mode == 'off':
        return 'stop_switch'
    if run['status'] not in {'running','draining'}:
        return 'rollout_stopped'
    if not healthy:
        return 'daemon_unavailable'
    metrics = report(app.ledger, rid)
    if metrics['session_reservations'] > run['max_sessions'] or metrics['candidate_reservations'] > run['max_candidates']:
        return 'quota_violation'
    if metrics['projection_states'].get('cleanup_required') or metrics['batches'].get('blocked'):
        return 'processing_failure'
    if metrics['observations']['wrong_recall']['yes']:
        return 'wrong_recall_observed'
    if db.execute("SELECT 1 FROM rollout_commits c JOIN proposals p USING(proposal_id) WHERE c.rollout_id=? AND NOT EXISTS (SELECT 1 FROM rollout_review_receipts r WHERE r.rollout_id=c.rollout_id AND r.batch_id=p.batch_id AND r.decision='approve')", (rid,)).fetchone():
        return 'unreviewed_commit'
    quota_reached = metrics['session_reservations'] >= run['max_sessions'] or metrics['candidate_reservations'] >= run['max_candidates']
    if quota_reached:
        open_sessions = db.execute("SELECT 1 FROM rollout_sessions s WHERE s.rollout_id=? AND NOT EXISTS (SELECT 1 FROM events e JOIN rollout_events re USING(event_id) WHERE re.rollout_id=s.rollout_id AND e.session_id=s.session_id AND e.event_kind='session_closed')", (rid,)).fetchone()
        outstanding = any(count for status,count in metrics['batches'].items() if status != 'settled')
        pending_candidates = db.execute("SELECT 1 FROM candidates c JOIN rollout_candidates rc USING(candidate_id) WHERE rc.rollout_id=? AND c.status IN ('pending','processing','awaiting_review')", (rid,)).fetchone()
        projecting = any(count for status,count in metrics['projection_states'].items() if status not in {'confirmed','compensated','cancelled'})
        outbox_pending = db.execute("SELECT 1 FROM outbox o JOIN rollout_commits c USING(outbox_id) WHERE c.rollout_id=? AND o.status NOT IN ('confirmed','compensated','cancelled')", (rid,)).fetchone()
        if not open_sessions and not outstanding and not pending_candidates and not projecting and not outbox_pending:
            return 'quota_drained'
    return None


def freeze_window(app, rollout_id, reason):
    _window(app, rollout_id)
    app.config.rollout_stop_file.touch(mode=0o600)
    with app.ledger._write_transaction():
        app.ledger.db.execute("UPDATE rollout_runs SET status='stopping',control_epoch=control_epoch+1,stopped_at=?,stop_reason=? WHERE rollout_id=? AND status IN ('running','draining')", (utcnow().isoformat(),reason,rollout_id))
        app.ledger.record_rollout_receipt('observation_stopping', {'reason':reason}, rollout_id=rollout_id,
                                         receipt_id='observation-stop:'+rollout_id)


def finish_window(app, rollout_id, token, *, reason):
    run = _window(app, rollout_id)
    db = app.ledger.db
    closed = db.execute("SELECT payload_json FROM rollout_audit WHERE rollout_id=? AND kind='observation_closed'", (rollout_id,)).fetchone()
    if closed:
        return json.loads(closed[0])
    root = app.config.ledger_path.parent/'observations'/rollout_id
    # STOP must persist even if exporting, authentication, or backend cleanup fails.
    freeze_window(app, rollout_id, reason)
    if not (root/'before-cleanup.json').exists():
        atomic_json(root/'before-cleanup.json', snapshot(app, rollout_id))
    result = app.rollout.rollback(rollout_id=rollout_id, plan_digest=run['rollback_plan_digest'], phase='run',
                                 receipt=str(uuid.uuid4()), **auth(app.config, token))
    if result['status'] == 'completed':
        operations = db.execute('SELECT locator FROM projection_operations WHERE rollout_id=?', (rollout_id,)).fetchall()
        if any(o['locator'] and (app.rollout_backend is None or app.rollout_backend.inspect_projection(o['locator']) is not None) for o in operations):
            raise ValueError('observation_remote_residual')
        completed = {'rollout_id':rollout_id, 'status':'completed', 'reason':reason,
                     'remote_exact_absence':True, 'audit':result['audit']}
        atomic_json(root/'final.json', {**snapshot(app, rollout_id), 'closure':completed})
        app.ledger.record_rollout_receipt('observation_closed', completed, rollout_id=rollout_id,
                                         receipt_id='observation-close:'+rollout_id)
        return completed
    return result


def tick(app, token_provider, *, healthy=True, now=None, finish_id=None):
    results = []
    rows = app.ledger.db.execute("SELECT r.rollout_id FROM rollout_runs r WHERE r.owner_id=? AND EXISTS (SELECT 1 FROM rollout_audit a WHERE a.rollout_id=r.rollout_id AND a.kind='observation_opened') AND NOT EXISTS (SELECT 1 FROM rollout_audit c WHERE c.rollout_id=r.rollout_id AND c.kind='observation_closed')", (app.owner_id,)).fetchall()
    for row in rows:
        rid = row['rollout_id']
        try:
            run = _window(app, rid)
            reason = 'operator_finish' if finish_id == rid else stop_reason(app, run, healthy=healthy, now=now)
            if reason:
                # Freeze before loading the credential, so missing keys fail closed.
                freeze_window(app, rid, reason)
                result = finish_window(app, rid, token_provider(), reason=reason)
            else:
                result = {'rollout_id':rid, 'status':run['status']}
            atomic_json(app.config.ledger_path.parent/'observations'/rid/'latest.json', snapshot(app, rid))
            results.append(result)
        except Exception as error:
            app.config.rollout_stop_file.touch(mode=0o600)
            # A failed export/credential/inspection never reports a completed window.
            failure = {'rollout_id':rid, 'status':'cleanup_required', 'error_class':type(error).__name__}
            app.ledger.record_rollout_receipt('observation_error', failure, rollout_id=rid)
            results.append(failure)
    return results


def daemon_healthy(config):
    try:
        with httpx.Client(trust_env=False, timeout=3) as client:
            response = client.get(f'http://{config.host}:{config.port}/health')
            body = response.json()
            return response.status_code == 200 and body.get('scheduler') == 'running' and Path(body.get('ledger_path','')).resolve() == config.ledger_path.resolve()
    except (httpx.HTTPError, ValueError):
        return False


def install_watchdog(config):
    if sys.platform != 'darwin':
        raise ValueError('launchd_required')
    root = config.ledger_path.parent
    destination = Path.home()/'Library/LaunchAgents'/f'{LABEL}.plist'
    log = root/'observation-watchdog.log'
    log.touch(mode=0o600)
    log.chmod(0o600)
    plist = {'Label':LABEL, 'ProgramArguments':[sys.executable, '-m', 'sagacontext.observation', 'tick'],
             'WorkingDirectory':str(Path(__file__).resolve().parents[2]),
             'EnvironmentVariables':{'SAGACONTEXT_HOME':str(root), 'PYTHONPATH':str(Path(__file__).resolve().parents[1])},
             'StartInterval':60, 'RunAtLoad':True, 'StandardOutPath':str(log), 'StandardErrorPath':str(log)}
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and plistlib.loads(destination.read_bytes()) != plist:
        raise ValueError('watchdog_configuration_conflict')
    destination.write_bytes(plistlib.dumps(plist))
    destination.chmod(0o600)
    domain = f'gui/{os.getuid()}'
    if subprocess.run(['launchctl','print',f'{domain}/{LABEL}'],capture_output=True).returncode != 0:
        subprocess.run(['launchctl','bootstrap',domain,str(destination)],check=True,capture_output=True)
    subprocess.run(['launchctl','kickstart',f'{domain}/{LABEL}'],check=True,capture_output=True)
    return {'status':'installed', 'interval_seconds':60}


def preflight(config):
    if not daemon_healthy(config):
        raise ValueError('daemon_scheduler_not_ready')
    if subprocess.check_output(['codex','--version'],text=True).strip() != config.rollout_host_version:
        raise ValueError('host_version_changed')
    features = subprocess.run(['codex','features','list'],capture_output=True,text=True,check=True).stdout
    if not any(line.split() == ['hooks','stable','true'] for line in features.splitlines()):
        raise ValueError('host_hooks_disabled')
    # Require installed ordinary hooks; never manufacture events to satisfy this check.
    home = Path(os.environ.get('CODEX_HOME', Path.home()/'.codex'))
    hooks = json.loads((home/'hooks.json').read_text())['hooks']
    for event in ('SessionStart','UserPromptSubmit','Stop','SessionEnd'):
        commands = [h.get('command','') for group in hooks.get(event,[]) for h in group.get('hooks',[])]
        expected = str(Path(__file__).resolve().parents[2]/'bin/sagactl-hook')
        if not any(shlex.split(command) == [expected,'codex',event] for command in commands):
            raise ValueError('workspace_hook_missing')
    if not all((config.llm_base_url,config.llm_api_key,config.llm_model,config.ov_api_key,config.rollout_backend_namespace)):
        raise ValueError('judge_backend_configuration_missing')
    status = json.loads((config.ledger_path.parent/'observations/watchdog.json').read_text())
    if (utcnow()-datetime.fromisoformat(status['checked_at'])).total_seconds() > 120:
        raise ValueError('watchdog_stale')
    if status['status'] != 'ok':
        raise ValueError('watchdog_not_ready')
    if sys.platform != 'darwin' or subprocess.run(['launchctl','print',f'gui/{os.getuid()}/{LABEL}'],capture_output=True).returncode:
        raise ValueError('watchdog_not_installed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['install-watchdog','start','tick','status','finish'])
    parser.add_argument('--hours',type=int,default=24)
    parser.add_argument('--rollout-id')
    args = parser.parse_args()
    config = Config.load()
    if args.action == 'install-watchdog':
        print(json.dumps(install_watchdog(config))); return
    if args.action == 'start':
        preflight(config)
    if args.action == 'finish' and not args.rollout_id:
        parser.error('--rollout-id required')
    with local_lock(config), Application(config) as app:
        if args.action == 'start':
            if app.rollout_backend is None:
                raise ValueError('backend_required')
            # Read-only connectivity check. Never activate against an unusable backend.
            app.rollout_backend.search('workspace',config.rollout_generation,1)
            result = start_window(app, local_token(config), hours=args.hours)
            atomic_json(config.ledger_path.parent/'observations'/result['rollout_id']/'latest.json',snapshot(app,result['rollout_id']))
        elif args.action == 'status':
            result = [snapshot(app,r[0]) for r in app.ledger.db.execute("SELECT rollout_id FROM rollout_audit WHERE kind='observation_opened' AND owner_id=? ORDER BY created_at DESC",(app.owner_id,))]
        else:
            if args.action == 'finish':
                _window(app,args.rollout_id)
            result = tick(app,lambda:local_token(config),healthy=daemon_healthy(config),finish_id=args.rollout_id)
            atomic_json(config.ledger_path.parent/'observations/watchdog.json',
                        {'checked_at':utcnow().isoformat(), 'status':'error' if any(r['status']=='cleanup_required' for r in result) else 'ok','windows':result})
        print(json.dumps(result))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Includes errors loading config, where the normal Application cannot start.
        root = Path(os.environ.get('SAGACONTEXT_HOME',Path.home()/'.sagacontext'))
        root.mkdir(parents=True,exist_ok=True)
        # Default STOP is a fallback only. Config errors must be visible to operators.
        (root/'STOP').touch(mode=0o600)
        print(json.dumps({'status':'failed','error_class':type(error).__name__}))
        raise SystemExit(1)
