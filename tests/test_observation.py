import hashlib
import json
from datetime import timedelta

import pytest

from sagacontext.application import Application
from sagacontext.backends import InMemoryBackend
from sagacontext.config import Config
from sagacontext.ledger import Scope
from sagacontext.maintenance import DeltaProposal, ScriptedJudge
from sagacontext.observation import PLAN, auth, finish_window, snapshot, start_window, tick, utcnow
from sagacontext.scheduler import DailyScheduler


class Backend(InMemoryBackend):
    fail_delete = False
    def close(self):pass
    def remove_projection(self, locators):
        if self.fail_delete:raise RuntimeError('injected cleanup failure')
        return super().remove_projection(locators)


@pytest.fixture
def window(tmp_path):
    config = Config(state_path=tmp_path/'state',ledger_path=tmp_path/'ledger',
        rollout_workspaces=(str(tmp_path),),rollout_mode='guarded',rollout_stop_file=tmp_path/'STOP',
        rollout_approver='ops',rollout_key_id='k',rollout_token_digest=hashlib.sha256(b'test').hexdigest(),
        llm_base_url='http://judge.invalid',llm_api_key='synthetic',llm_model='scripted')
    config.rollout_stop_file.touch()
    app = Application(config)
    identity = app.ledger.register_project('test',tmp_path)
    backend = Backend()
    app.rollout_backend = app.rollout.backend = backend
    run = start_window(app,'test',hours=1)
    def event(kind,key,session='session',**extra):
        return app.rollout.ingest(dict(host='codex',host_version=config.rollout_host_version,
            source_generation='g1',session_id=session,cwd=str(tmp_path),hook_event_name=kind,source_event_ref=key,**extra))
    def proposal():
        source = event('UserPromptSubmit','prompt',prompt='Remember to run pytest for this project.')
        event('Stop','checkpoint')
        candidate = app.ledger.db.execute('SELECT candidate_id FROM rollout_candidate_reservations').fetchone()[0]
        judge = ScriptedJudge((DeltaProposal(candidate_id=candidate,operation='new',memory_type='decision',
            scope=Scope(kind='project',project_id=identity['project_id']),payload={'key':'verify','value':'private-body-test'},evidence_ids=(source.event_id,)),))
        scheduler = DailyScheduler(app,judge)
        return scheduler,scheduler.tick()['batch_id']
    yield app,config,run,event,proposal,backend
    app.close()


def test_window_enrollment_is_bound_to_activation_and_starts_empty(window):
    app,c,run,event,proposal,backend=window
    row=app.ledger.db.execute('SELECT rollback_plan_json FROM rollout_runs').fetchone()
    assert json.loads(row[0])==PLAN
    assert app.rollout.mode.value=='guarded'
    assert not c.rollout_stop_file.exists()
    assert tick(app,lambda:'test')==[{'rollout_id':run['rollout_id'],'status':'running'}]
    data=snapshot(app,run['rollout_id'])
    assert data['report']['candidate_reservations']==0
    assert data['report']['consumption_rate'] is None
    assert data['automatic_review'] is False
    with pytest.raises(ValueError,match='rollout_already_live'):start_window(app,'test')
    assert app.rollout.mode.value=='guarded'


def test_deadline_without_events_cleans_once(window):
    app,c,run,event,proposal,backend=window
    future=utcnow()+timedelta(hours=2)
    results=tick(app,lambda:'test',now=future)
    assert results[0]['status']=='completed'
    assert c.rollout_stop_file.exists()
    assert app.rollout.mode.value=='off'
    assert tick(app,lambda:'test',now=future)==[]
    assert app.ledger.db.execute("SELECT COUNT(*) FROM rollout_audit WHERE kind='observation_closed'").fetchone()[0]==1
    final=c.ledger_path.parent/'observations'/run['rollout_id']/'final.json'
    assert final.exists()
    assert final.stat().st_mode & 0o777 == 0o600
    assert json.loads(final.read_text())['report']['candidate_reservations']==0


def test_pending_is_never_approved_and_snapshot_contains_no_body(window):
    app,c,run,event,proposal,backend=window
    scheduler,batch=proposal()
    result=tick(app,lambda:'test')
    assert result[0]['status']=='running'
    data=snapshot(app,run['rollout_id'])
    assert data['pending_batch_ids']==[batch]
    assert 'private-body-test' not in json.dumps(data)
    assert app.ledger.db.execute('SELECT COUNT(*) FROM memories').fetchone()[0]==0
    assert app.ledger.db.execute('SELECT COUNT(*) FROM rollout_review_receipts').fetchone()[0]==0
    result=tick(app,lambda:'test',healthy=False)
    assert result[0]['status']=='completed'
    assert result[0]['reason']=='daemon_unavailable'


@pytest.mark.parametrize('change', ['refine','global','other_project'])
def test_observation_rejects_non_undoable_reviews_atomically(window,change):
    app,c,run,event,proposal,backend=window
    scheduler,batch=proposal()
    if change=='refine':
        app.ledger.db.execute("UPDATE proposals SET operation='refine' WHERE batch_id=?",(batch,))
    else:
        scope={'kind':'global'} if change=='global' else {'kind':'project','project_id':'other'}
        app.ledger.db.execute('UPDATE proposals SET scope_json=? WHERE batch_id=?',(json.dumps(scope),batch))
    with pytest.raises(ValueError,match='observation_requires_new_project_memory'):
        app.rollout.review_batch(batch,'approve',reviewer='ops',receipt='invalid-review',**auth(c,'test'))
    assert app.ledger.db.execute('SELECT COUNT(*) FROM memories').fetchone()[0]==0
    assert app.ledger.db.execute('SELECT COUNT(*) FROM rollout_review_receipts').fetchone()[0]==0
    assert app.ledger.db.execute('SELECT status FROM batches').fetchone()[0]=='awaiting_review'
    assert app.rollout.review_batch(batch,'reject',reviewer='ops',receipt='reject',**auth(c,'test'))['status']=='rejected'


def test_projection_cleanup_failure_recovers_after_restart(window):
    app,c,run,event,proposal,backend=window
    scheduler,batch=proposal()
    app.rollout.review_batch(batch,'approve',reviewer='ops',receipt='review',**auth(c,'test'))
    assert scheduler.tick()['projection_status']=='confirmed'
    backend.fail_delete=True
    result=tick(app,lambda:'test',finish_id=run['rollout_id'])
    assert result[0]['status']=='cleanup_required'
    assert backend.items
    app.close()
    with Application(c) as restarted:
        backend.fail_delete=False
        restarted.rollout_backend=restarted.rollout.backend=backend
        result=tick(restarted,lambda:'test')
        assert result[0]['status']=='completed'
        assert not backend.items
        # Failed and successful compensation attempts both retain their receipts.
        assert result[0]['audit']['compensation_receipts']==2
        assert tick(restarted,lambda:'test')==[]
        assert restarted.ledger.db.execute('SELECT COUNT(*) FROM rollout_rollback_receipts').fetchone()[0]==2


@pytest.mark.parametrize('token',['bad',None])
def test_invalid_or_missing_key_freezes_before_cleanup(window,token):
    app,c,run,event,proposal,backend=window
    def provider():
        if token is None:raise FileNotFoundError()
        return token
    result=tick(app,provider,finish_id=run['rollout_id'])
    assert result[0]['status']=='cleanup_required'
    assert c.rollout_stop_file.exists()
    assert app.ledger.db.execute('SELECT status FROM rollout_runs').fetchone()[0]=='stopping'
    assert app.ledger.db.execute('SELECT COUNT(*) FROM rollout_rollback_receipts').fetchone()[0]==0
    assert tick(app,lambda:'test')[0]['status']=='completed'


def test_watchdog_ignores_unenrolled_rollouts(window):
    app,c,run,event,proposal,backend=window
    finish_window(app,run['rollout_id'],'test',reason='test')
    c.rollout_stop_file.unlink()
    regular=app.rollout.activate(mode='guarded',workspace=c.rollout_workspaces[0],approval_receipt='regular',deadline=utcnow()+timedelta(hours=1),**auth(c,'test'))
    assert tick(app,lambda:'test',healthy=False)==[]
    with pytest.raises(ValueError,match='observation_plan_mismatch'):finish_window(app,regular['rollout_id'],'test',reason='test')
    assert app.rollout.mode.value=='guarded'
    assert not c.rollout_stop_file.exists()


def test_tenth_session_drains_only_after_all_end(window):
    app,c,run,event,proposal,backend=window
    for i in range(10):assert event('SessionStart',f'start-{i}',session=str(i)).status=='accepted'
    assert app.rollout.mode.value=='guarded'
    assert tick(app,lambda:'test')[0]['status']=='draining'
    assert event('SessionStart','eleven',session='eleven').reason=='session_limit'
    for i in range(10):event('SessionEnd',f'end-{i}',session=str(i))
    result=tick(app,lambda:'test')
    assert result[0]['status']=='completed'
    assert result[0]['reason']=='quota_drained'


def test_candidate_quota_waits_for_existing_review(window):
    app,c,run,event,proposal,backend=window
    scheduler,batch=proposal()
    app.ledger.db.execute('UPDATE rollout_runs SET max_candidates=1')
    event('SessionEnd','end')
    assert tick(app,lambda:'test')[0]['status']=='running'
    app.rollout.review_batch(batch,'reject',reviewer='ops',receipt='reject',**auth(c,'test'))
    assert tick(app,lambda:'test')[0]['reason']=='quota_drained'


def test_wrong_recall_label_stops_window(window):
    from sagacontext.daily_report import annotate
    app,c,run,event,proposal,backend=window
    rid=run['rollout_id']
    app.ledger.record_rollout_receipt('injection',{'status':'emitted','memory_ids':['synthetic']},rollout_id=rid,receipt_id='injection')
    annotate(app.ledger,rid,receipt_id='label',kind='wrong_recall',target_id='injection',value='yes')
    assert tick(app,lambda:'test')[0]['reason']=='wrong_recall_observed'


def test_activation_bad_auth_restores_stop(window):
    app,c,run,event,proposal,backend=window
    finish_window(app,run['rollout_id'],'test',reason='test')
    with pytest.raises(ValueError,match='invalid_operator_token'):start_window(app,'bad')
    assert c.rollout_stop_file.exists()
    assert app.rollout.mode.value=='off'
    assert app.ledger.db.execute("SELECT COUNT(*) FROM rollout_audit WHERE kind='observation_opened'").fetchone()[0]==1


def test_final_export_failure_does_not_mark_closed(window,monkeypatch):
    import sagacontext.observation as observation
    app,c,run,event,proposal,backend=window
    write=observation.atomic_json
    def fail_final(path,value):
        if path.name=='final.json':raise OSError('injected export failure')
        return write(path,value)
    monkeypatch.setattr(observation,'atomic_json',fail_final)
    assert tick(app,lambda:'test',finish_id=run['rollout_id'])[0]['status']=='cleanup_required'
    assert app.ledger.db.execute("SELECT COUNT(*) FROM rollout_audit WHERE kind='observation_closed'").fetchone()[0]==0
    monkeypatch.setattr(observation,'atomic_json',write)
    assert tick(app,lambda:'test')[0]['status']=='completed'


def test_quota_drain_waits_for_outbox_projection(window):
    app,c,run,event,proposal,backend=window
    scheduler,batch=proposal()
    app.ledger.db.execute('UPDATE rollout_runs SET max_candidates=1')
    event('SessionEnd','end')
    app.rollout.review_batch(batch,'approve',reviewer='ops',receipt='review',**auth(c,'test'))
    assert tick(app,lambda:'test')[0]['status']=='running'
    assert scheduler.tick()['projection_status']=='confirmed'
    assert tick(app,lambda:'test')[0]['reason']=='quota_drained'


def test_independent_process_finishes_without_daemon(tmp_path,monkeypatch):
    import os
    from pathlib import Path
    import subprocess
    import sys
    import tomli_w
    for name in list(os.environ):
        if name.startswith('SAGACONTEXT_'):monkeypatch.delenv(name)
    monkeypatch.setenv('SAGACONTEXT_HOME',str(tmp_path))
    token=tmp_path/'token'
    token.write_text('test')
    (tmp_path/'config.toml').write_text(tomli_w.dumps({'daemon':{'port':1},'rollout':{
        'mode':'guarded','workspace':str(tmp_path),'approver':'ops','key_id':'k','token_file':str(token)}}))
    c=Config.load()
    with Application(c) as app:
        app.ledger.register_project('isolated',tmp_path)
        run=start_window(app,'test',hours=1)
    env=os.environ.copy()
    env['PYTHONPATH']=str(Path(__file__).resolve().parents[1]/'src')
    first=subprocess.run([sys.executable,'-m','sagacontext.observation','tick'],env=env,capture_output=True,text=True,timeout=15)
    assert first.returncode==0
    assert json.loads(first.stdout)[0]['status']=='completed'
    assert json.loads(first.stdout)[0]['reason']=='daemon_unavailable'
    second=subprocess.run([sys.executable,'-m','sagacontext.observation','tick'],env=env,capture_output=True,text=True,timeout=15)
    assert second.returncode==0
    assert json.loads(second.stdout)==[]
    assert c.rollout_stop_file.exists()
    assert (tmp_path/'observations'/run['rollout_id']/'final.json').exists()


def test_launcher_contains_no_credentials(window,monkeypatch,tmp_path):
    import plistlib
    from pathlib import Path
    import sagacontext.observation as observation
    app,c,run,event,proposal,backend=window
    monkeypatch.setattr(Path,'home',classmethod(lambda cls:tmp_path))
    monkeypatch.setattr(observation.sys,'platform','darwin')
    calls=[]
    class Result:
        returncode=1
    def launch(*args,**kwargs):
        calls.append(args[0]);return Result()
    monkeypatch.setattr(observation.subprocess,'run',launch)
    assert observation.install_watchdog(c)['interval_seconds']==60
    plist=plistlib.loads((tmp_path/'Library/LaunchAgents/com.sagacontext.observation.plist').read_bytes())
    assert plist['ProgramArguments'][-2:]==['sagacontext.observation','tick']
    assert set(plist['EnvironmentVariables'])=={'SAGACONTEXT_HOME','PYTHONPATH'}
    assert not any('test'==arg for arg in plist['ProgramArguments'])
    assert any('bootstrap' in call for call in calls)
