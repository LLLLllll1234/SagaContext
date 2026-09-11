import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone

import pytest

from sagacontext.application import Application
from sagacontext.config import Config
from sagacontext.backends import InMemoryBackend as BaseBackend

class InMemoryBackend(BaseBackend):
    def close(self):pass
from sagacontext.maintenance import DeltaProposal, ScriptedJudge
from sagacontext.llm import JudgeError
from sagacontext.ledger import Scope
from sagacontext.scheduler import DailyScheduler, now
from sagacontext.daily_report import pending, report


@pytest.fixture
def setup(tmp_path):
    c=Config(state_path=tmp_path/'state',ledger_path=tmp_path/'ledger',rollout_workspaces=(str(tmp_path),),
        rollout_mode='guarded',rollout_stop_file=tmp_path/'STOP',rollout_approver='ops',rollout_key_id='k',
        rollout_token_digest=hashlib.sha256(b'test').hexdigest(),llm_base_url='http://judge.invalid',llm_api_key='test',llm_model='test')
    a=Application(c)
    ident=a.ledger.register_project('test',tmp_path)
    def auth():
        return dict(token='test',key_id='k',approver='ops',issued_at=now().isoformat(),expires_at=(now()+timedelta(minutes=3)).isoformat())
    run=a.rollout.activate(mode='guarded',workspace=str(tmp_path),approval_receipt='activation',deadline=now()+timedelta(hours=1),**auth())
    def event(kind,key,**extra):
        return a.rollout.ingest({'host':'codex','host_version':c.rollout_host_version,'source_generation':'g1','session_id':'session','cwd':str(tmp_path),'hook_event_name':kind,'source_event_ref':key,**extra})
    def judge():
        row=a.ledger.db.execute('SELECT candidate_id,event_id FROM rollout_candidate_reservations LIMIT 1').fetchone()
        return ScriptedJudge((DeltaProposal(candidate_id=row[0],operation='new',memory_type='decision',scope=Scope(kind='project',project_id=ident['project_id']),payload={'key':'verify','value':'pytest'},evidence_ids=(row[1],)),))
    yield a,c,run,event,judge,auth
    a.close()


def test_checkpoint_proposal_restart_review_then_projection(setup):
    a,c,run,event,judge,auth=setup
    event('UserPromptSubmit','one',prompt='Remember to use pytest for this project.')
    s=DailyScheduler(a,judge())
    assert s.tick()['status']=='idle'
    event('Stop','stop')
    result=s.tick()
    assert result['status']=='proposed'
    assert len(pending(a.ledger))==1
    assert a.ledger.db.execute('SELECT COUNT(*) FROM memories').fetchone()[0]==0
    a.close()
    with Application(c) as restarted:
        backend=InMemoryBackend()
        restarted.rollout_backend=backend
        scheduler=DailyScheduler(restarted,ScriptedJudge(()))
        assert scheduler.tick()['status']=='idle'
        restarted.ledger.register_backend_generation(backend.capabilities().backend,'g1')
        approved=restarted.rollout.review_batch(result['batch_id'],'approve',reviewer='ops',receipt='review',**auth())
        assert len(approved['memory_ids'])==1
        assert scheduler.tick()['projection_status']=='confirmed'
        assert scheduler.tick()['projection_status']=='idle'
        assert report(restarted.ledger,run['rollout_id'])['reviews']=={'approve':1}


def test_expired_proposed_lease_recovery_never_commits(setup):
    a,c,run,event,judge,auth=setup
    event('UserPromptSubmit','one',prompt='Remember to use pytest.')
    event('Stop','stop')
    s=DailyScheduler(a,judge())
    bid=s._batch(a.rollout._run())
    result=a.rollout.worker.run_once(judge(),worker_id='crashed',now=now(),lease_duration=timedelta(seconds=30),stop_after_proposals=True,target_batch_id=bid)
    assert result.status=='proposed'
    a.ledger.db.execute('UPDATE batches SET lease_until=? WHERE batch_id=?',((now()-timedelta(seconds=1)).isoformat(),bid))
    with Application(c) as other:
        recovered=DailyScheduler(other,judge()).tick()
        assert recovered['status']=='proposed'
        assert other.ledger.db.execute('SELECT COUNT(*) FROM memories').fetchone()[0]==0
        assert len(pending(other.ledger))==1


def test_concurrent_workers_make_one_batch_and_one_review(setup):
    a,c,run,event,judge,auth=setup
    event('UserPromptSubmit','one',prompt='Remember to use pytest.')
    event('Stop','stop')
    j=judge()
    def tick():
        with Application(c) as instance:return DailyScheduler(instance,j).tick()
    with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda _:tick(),range(2)))
    assert a.ledger.db.execute('SELECT COUNT(*) FROM batches').fetchone()[0]==1
    assert len(pending(a.ledger))==1
    assert a.ledger.db.execute('SELECT COUNT(*) FROM memories').fetchone()[0]==0


def test_stop_during_judge_prevents_proposal_and_next_tick(setup):
    a,c,run,event,judge,auth=setup
    event('UserPromptSubmit','one',prompt='Remember to use pytest.')
    event('Stop','stop')
    inner=judge()
    class StopJudge:
        version=inner.version
        def judge(self,batch):
            c.rollout_stop_file.touch()
            return inner.judge(batch)
    s=DailyScheduler(a,StopJudge())
    assert s.tick()['status']=='blocked'
    assert a.ledger.db.execute('SELECT COUNT(*) FROM proposals').fetchone()[0]==0
    assert s.tick()['status']=='off'


def test_retry_keeps_batch_and_enforces_due_time(setup):
    a,c,run,event,judge,auth=setup
    event('UserPromptSubmit','one',prompt='Remember to use pytest.')
    event('Stop','stop')
    inner=judge()
    class Fail:
        version=inner.version
        def judge(self,batch):raise JudgeError('judge_service_unavailable',True)
    s=DailyScheduler(a,Fail())
    result=s.tick()
    assert result['status']=='retry'
    assert s.tick()['status']=='idle'
    a.ledger.db.execute('UPDATE batches SET next_attempt_at=?',((now()-timedelta(seconds=1)).isoformat(),))
    s.judge=inner
    assert s.tick()['batch_id']==result['batch_id']
    assert len(pending(a.ledger))==1


def test_stop_during_materialize_compensates(setup):
    a,c,run,event,judge,auth=setup
    event('UserPromptSubmit','one',prompt='Remember to use pytest.')
    event('Stop','stop')
    s=DailyScheduler(a,judge()); result=s.tick()
    class StopBackend(InMemoryBackend):
        def materialize(self,*args):
            locator=super().materialize(*args)
            c.rollout_stop_file.touch()
            return locator
    backend=StopBackend();a.rollout_backend=backend
    a.ledger.register_backend_generation(backend.capabilities().backend,'g1')
    a.rollout.review_batch(result['batch_id'],'approve',reviewer='ops',receipt='review',**auth())
    assert s.tick()['projection_status']=='fenced'
    op=a.ledger.db.execute('SELECT state FROM projection_operations').fetchone()
    assert op[0]=='compensated'


def test_pending_batch_survives_restart_and_deadline_prevents_work(setup):
    a,c,run,event,judge,auth=setup
    event('UserPromptSubmit','one',prompt='Remember pytest.')
    event('Stop','stop')
    inner=judge()
    bid=DailyScheduler(a,inner)._batch(a.rollout._run())
    with Application(c) as restarted:
        assert DailyScheduler(restarted,inner).tick()['batch_id']==bid
    a.ledger.db.execute('UPDATE rollout_runs SET deadline=? WHERE rollout_id=?',((now()-timedelta(seconds=1)).isoformat(),run['rollout_id']))
    assert DailyScheduler(a,inner).tick()['status']=='off'
    with pytest.raises(ValueError):a.rollout.review_batch(bid,'approve',reviewer='ops',receipt='late',**auth())


def test_draining_rejects_eleventh_session_but_processes_reserved(setup):
    a,c,run,event,judge,auth=setup
    event('UserPromptSubmit','one',prompt='Remember pytest.')
    for i in range(9):
        accepted=a.rollout.ingest({'host':'codex','host_version':c.rollout_host_version,'source_generation':'g1','session_id':str(i),'cwd':c.rollout_workspaces[0],'hook_event_name':'SessionStart','source_event_ref':str(i)})
        assert accepted.status=='accepted'
    assert a.rollout._run()['status']=='draining'
    denied=a.rollout.ingest({'host':'codex','host_version':c.rollout_host_version,'source_generation':'g1','session_id':'eleven','cwd':c.rollout_workspaces[0],'hook_event_name':'SessionStart','source_event_ref':'eleven'})
    assert denied.reason=='session_limit'
    event('Stop','stop')
    assert DailyScheduler(a,judge()).tick()['status']=='proposed'


def test_no_candidate_over_quota_and_no_fake_quality_rate(setup):
    a,c,run,event,judge,auth=setup
    a.ledger.db.execute('UPDATE rollout_runs SET max_candidates=1 WHERE rollout_id=?',(run['rollout_id'],))
    event('UserPromptSubmit','one',prompt='Remember pytest.')
    event('UserPromptSubmit','two',prompt='Remember unittest.')
    assert a.ledger.db.execute('SELECT COUNT(*) FROM rollout_candidate_reservations').fetchone()[0]==1
    metrics=report(a.ledger,run['rollout_id'])
    assert metrics['wrong_recall_rate'] is None
    assert metrics['consumption_rate'] is None


def test_compensation_failure_is_persisted(setup):
    a,c,run,event,judge,auth=setup
    event('UserPromptSubmit','one',prompt='Remember pytest.')
    event('Stop','stop')
    s=DailyScheduler(a,judge()); result=s.tick()
    class FailureBackend(InMemoryBackend):
        def materialize(self,*args):
            locator=super().materialize(*args);c.rollout_stop_file.touch();return locator
        def remove_projection(self,*args):raise RuntimeError('synthetic deletion failure')
    a.rollout_backend=FailureBackend()
    a.ledger.register_backend_generation(a.rollout_backend.capabilities().backend,'g1')
    a.rollout.review_batch(result['batch_id'],'approve',reviewer='ops',receipt='review',**auth())
    assert s.tick()['projection_status']=='blocked'
    assert a.ledger.db.execute('SELECT state FROM projection_operations').fetchone()[0]=='cleanup_required'


def test_failed_compensation_recovers_after_restart_and_receipts_are_idempotent(setup):
    a,c,run,event,judge,auth=setup
    event('UserPromptSubmit','one',prompt='Remember pytest.')
    event('Stop','stop')
    result=DailyScheduler(a,judge()).tick()
    class FailureBackend(InMemoryBackend):
        def materialize(self,*args):
            locator=super().materialize(*args)
            c.rollout_stop_file.touch()
            return locator
        def remove_projection(self,*args):raise RuntimeError('synthetic deletion failure')
    backend=FailureBackend()
    a.rollout_backend=a.rollout.backend=backend
    a.ledger.register_backend_generation(backend.capabilities().backend,'g1')
    a.rollout.review_batch(result['batch_id'],'approve',reviewer='ops',receipt='review',**auth())
    assert DailyScheduler(a,judge()).tick()['projection_status']=='blocked'
    plan=a.ledger.db.execute('SELECT rollback_plan_digest FROM rollout_runs').fetchone()[0]
    request=dict(rollout_id=run['rollout_id'],plan_digest=plan,phase='run',receipt='failed',**auth())
    failed=a.rollout.rollback(**request)
    assert failed['status']=='cleanup_required'
    assert a.rollout.rollback(**request)==failed
    assert len(backend.items)==1
    a.close()
    with Application(c) as restarted:
        recovered_backend=InMemoryBackend(backend.state)
        restarted.rollout_backend=restarted.rollout.backend=recovered_backend
        recovered_request={**request,'receipt':'recover',**auth()}
        recovered=restarted.rollout.rollback(**recovered_request)
        assert recovered['status']=='completed'
        assert restarted.rollout.rollback(**recovered_request)==recovered
        assert not recovered_backend.items
        audit=restarted.rollout.rollback_runner.audit(run['rollout_id'])
        assert all(value==0 for key,value in audit.items() if key.startswith('residual_'))
        assert restarted.rollout.mode.value=='off'


def test_quality_labels_have_explicit_denominators_and_no_host_receipts(setup):
    from sagacontext.daily_report import annotate
    a,c,run,event,judge,auth=setup
    rid=run['rollout_id']
    event('UserPromptSubmit','one',prompt='Remember pytest.')
    candidate=a.ledger.db.execute('SELECT candidate_id FROM rollout_candidate_reservations').fetchone()[0]
    label=dict(receipt_id='valid',kind='candidate_valid',target_id=candidate,value='yes')
    assert annotate(a.ledger,rid,**label)['status']=='recorded'
    assert annotate(a.ledger,rid,**label)['status']=='duplicate'
    assert annotate(a.ledger,rid,**{**label,'receipt_id':'another'})['status']=='duplicate'
    with pytest.raises(ValueError,match='observation_conflict'):
        annotate(a.ledger,rid,**{**label,'receipt_id':'conflict','value':'no'})
    for index,value in enumerate(('yes','no','unknown')):
        target=f'injection-{index}'
        a.ledger.record_rollout_receipt('injection',{'status':'emitted','memory_ids':['synthetic']},rollout_id=rid,receipt_id=target)
        annotate(a.ledger,rid,receipt_id=f'label-{index}',kind='consumed',target_id=target,value=value)
    metrics=report(a.ledger,rid)
    assert metrics['candidate_effectiveness']==1
    assert metrics['consumption_rate']==.5
    assert metrics['observations']['consumed']==dict(yes=1,no=1,unknown=1,labeled_samples=3,evaluated_samples=2,yes_rate=.5)
    assert metrics['consumption_reports']==0
    assert metrics['observation_targets']['consumed']==['injection-0','injection-1','injection-2']
    assert metrics['observation_targets']['candidate_valid']==[candidate]
    assert metrics['wrong_recall_rate'] is None
    assert metrics['scope_expansion_admitted'] is False


@pytest.mark.parametrize('payload',[
    {'status':'blocked','memory_ids':['synthetic']},
    {'status':'emitted','memory_ids':[]},
])
def test_quality_rejects_unusable_injection_targets(setup,payload):
    from sagacontext.daily_report import annotate
    a,c,run,event,judge,auth=setup
    a.ledger.record_rollout_receipt('injection',payload,rollout_id=run['rollout_id'],receipt_id='unusable')
    with pytest.raises(ValueError,match='observation_target_mismatch'):
        annotate(a.ledger,run['rollout_id'],receipt_id='label',kind='consumed',target_id='unusable',value='yes')
    assert report(a.ledger,run['rollout_id'])['observation_targets']['consumed']==[]


def test_quality_rejects_receipt_collision_and_invalid_targets(setup):
    from sagacontext.daily_report import annotate
    a,c,run,event,judge,auth=setup
    rid=run['rollout_id']
    event('UserPromptSubmit','one',prompt='Remember pytest.')
    candidate=a.ledger.db.execute('SELECT candidate_id FROM rollout_candidate_reservations').fetchone()[0]
    label=dict(receipt_id='collision',kind='candidate_valid',target_id=candidate,value='yes')
    a.ledger.record_rollout_receipt('review',{},rollout_id=rid,receipt_id='collision')
    with pytest.raises(ValueError,match='observation_receipt_conflict'):annotate(a.ledger,rid,**label)
    for field in label:
        with pytest.raises(ValueError,match='invalid_observation'):annotate(a.ledger,rid,**{**label,field:[]})
    with pytest.raises(ValueError,match='observation_target_mismatch'):
        annotate(a.ledger,rid,**{**label,'target_id':'another-rollout-candidate'})
    with pytest.raises(ValueError,match='rollout_not_found'):annotate(a.ledger,'missing',**label)
    assert report(a.ledger,rid)['candidate_effectiveness'] is None


def test_operator_pending_review_annotate_report(setup,monkeypatch,capsys,tmp_path):
    from fastapi.testclient import TestClient
    from sagacontext import operator
    from sagacontext.daemon import create_app
    a,c,run,event,judge,auth=setup
    event('UserPromptSubmit','one',prompt='Remember pytest.')
    event('Stop','stop')
    batch=DailyScheduler(a,judge()).tick()['batch_id']
    monkeypatch.setattr(Config,'load',classmethod(lambda cls:c))
    def invoke(*args):
        monkeypatch.setattr('sys.argv',['operator',*args])
        operator.main()
        return json.loads(capsys.readouterr().out)
    assert invoke('pending')[0]['batch_id']==batch
    token_file=tmp_path/'token'
    token_file.write_text('test')
    (tmp_path/'config.toml').write_text('[rollout]\ntoken_file='+json.dumps(str(token_file))+'\n')
    with TestClient(create_app(c)) as client:
        # Exercise the real HTTP review route without a socket or background worker.
        class LocalClient:
            def __enter__(self):return client
            def __exit__(self,*args):pass
        monkeypatch.setattr(operator.httpx,'Client',lambda **kwargs:LocalClient())
        assert invoke('review','--batch-id',batch,'--decision','reject')['status']=='rejected'
    assert invoke('pending')==[]
    observation=tmp_path/'observation.json'
    observation.write_text(json.dumps(dict(receipt_id='edit',kind='review_modified',target_id=batch,value='no')))
    assert invoke('annotate','--rollout-id',run['rollout_id'],'--observation-file',str(observation))['status']=='recorded'
    metrics=invoke('report','--rollout-id',run['rollout_id'])
    assert metrics['reviews']=={'reject':1}
    assert metrics['review_modification_rate']==0
    assert metrics['observation_targets']['review_modified']==[batch]
    with pytest.raises(SystemExit) as error:invoke('report')
    assert error.value.code==2
    observation.write_text('[]')
    with pytest.raises(ValueError,match='invalid_observation_fields'):
        invoke('annotate','--rollout-id',run['rollout_id'],'--observation-file',str(observation))


def test_unreviewed_batch_cannot_be_labeled_as_reviewed(setup):
    from sagacontext.daily_report import annotate
    a,c,run,event,judge,auth=setup
    event('UserPromptSubmit','one',prompt='Remember pytest.')
    event('Stop','stop')
    batch=DailyScheduler(a,judge()).tick()['batch_id']
    with pytest.raises(ValueError,match='observation_target_mismatch'):
        annotate(a.ledger,run['rollout_id'],receipt_id='unreviewed',kind='review_modified',target_id=batch,value='no')


def test_concurrent_observations_count_target_once(setup):
    from sagacontext.daily_report import annotate
    a,c,run,event,judge,auth=setup
    event('UserPromptSubmit','one',prompt='Remember pytest.')
    candidate=a.ledger.db.execute('SELECT candidate_id FROM rollout_candidate_reservations').fetchone()[0]
    def label(index):
        with Application(c) as instance:
            return annotate(instance.ledger,run['rollout_id'],receipt_id=f'label-{index}',kind='candidate_valid',target_id=candidate,value='yes')['status']
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(label,range(2)))==['duplicate','recorded']
    metrics=report(a.ledger,run['rollout_id'])
    assert metrics['observations']['candidate_valid']['labeled_samples']==1
    assert metrics['rollback']['audit']['candidates']==1


def test_offline_acceptance_covers_shadow_and_guarded_without_network(tmp_path,monkeypatch):
    import socket
    from scripts.audit_daily_scheduler import run
    def forbidden(*args,**kwargs):
        raise AssertionError('offline acceptance must not use network or local configuration')
    monkeypatch.setattr(socket.socket,'connect',forbidden)
    monkeypatch.setattr(Config,'load',classmethod(forbidden))
    artifact=tmp_path/'acceptance.json'
    result=run(artifact)
    assert result['passed']
    assert result['provider_calls']==result['daily_quality_samples']==0
    assert result['real_host_consumption']=='not_exercised'
    assert {s['mode'] for s in result['scenarios']}=={'shadow','guarded'}
    assert all(s['temporary_root_removed'] for s in result['scenarios'])
    assert all(s['final_report']['rollback']['states']=={'completed':1} for s in result['scenarios'])
    assert json.loads(artifact.read_text())==result
    with pytest.raises(ValueError,match='refusing_to_overwrite_audit'):run(artifact)
