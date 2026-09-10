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
