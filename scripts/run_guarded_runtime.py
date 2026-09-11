"""Controlled real-host/Judge/backend acceptance, scoped to configured workspace.

Creates an authorized guarded rollout and always rolls it back. This verifies
plumbing with an explicit test decision, not everyday extraction quality.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import signal
import shlex
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from probe_codex_host import _prepare_isolated_codex_home, _last_agent_message, PINNED_MODEL, _classify_blocker, _stderr_summary
from sagacontext.application import Application
from sagacontext.config import Config

REPO = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--model', default=PINNED_MODEL)
    parser.add_argument('--scheduled', action='store_true')
    args = parser.parse_args()
    if not args.execute:
        parser.error('--execute is required')
    config = Config.load()
    workspace = Path(config.rollout_workspaces[0]).resolve()
    if subprocess.check_output(['codex', '--version'], text=True).strip() != config.rollout_host_version:
        raise ValueError('host_version_changed')
    token = (Path.home()/'.sagacontext/operator-token').read_text().strip()
    run_id = None
    report = {'schema':'guarded-runtime-v1', 'payload_class':'controlled_test_decision',
              'status':'running', 'host_version':config.rollout_host_version,
              'host_model':args.model, 'scheduler_driven':args.scheduled, 'host_sessions':[], 'steps':[], 'quality_population':'not_daily_traffic',
              'review_actor':'user_delegated_operator', 'active_enabled':False}
    marker = 'uv run --locked pytest -q'
    report['command_digest'] = hashlib.sha256(marker.encode()).hexdigest()
    def step(name, passed, **details):
        report['steps'].append({'name':name,'passed':bool(passed),**details})
        print(name, 'passed' if passed else 'FAILED', flush=True)
        if not passed:
            raise ValueError(name)
    def headers(receipt):
        now = datetime.now(timezone.utc)
        return {'Authorization':'Bearer '+token,'X-SagaContext-Approver':config.rollout_approver,
                'X-SagaContext-Key-Id':config.rollout_key_id,'X-SagaContext-Approval-Receipt':receipt,
                'X-SagaContext-Issued-At':now.isoformat(),'X-SagaContext-Expires-At':(now+timedelta(minutes=4)).isoformat()}
    with httpx.Client(base_url=f'http://{config.host}:{config.port}',timeout=320,trust_env=False) as client:
        def post(path, payload, authenticated=False):
            response=client.post(path,json=payload,headers=headers(str(uuid.uuid4())) if authenticated else {})
            if response.status_code != 200:
                raise ValueError('http_'+str(response.status_code))
            return response.json()
        def host(name,prompt,expected):
            with tempfile.TemporaryDirectory(prefix='saga-runtime-host-') as tmp:
                home=Path(tmp)/'home'
                _prepare_isolated_codex_home(home, model=args.model, workspace=workspace)
                command=shlex.join([sys.executable,'-m','sagacontext.hook','codex'])
                (home/'hooks.json').write_text(json.dumps({'hooks':{e:[{'hooks':[{'type':'command','command':command+' '+e,'timeout':10}]}] for e in ('SessionStart','UserPromptSubmit','Stop','SessionEnd')}}))
                env=os.environ.copy();env['CODEX_HOME']=str(home);env['PYTHONPATH']=str(REPO/'src')
                started=time.monotonic()
                proc=subprocess.Popen(['codex','--dangerously-bypass-hook-trust','--ask-for-approval','never','--sandbox','read-only','--cd',str(workspace),'--model',args.model,'exec','--json','--ephemeral','--ignore-rules',prompt],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
                timed_out=False
                try:
                    stdout,stderr=proc.communicate(timeout=120)
                except subprocess.TimeoutExpired:
                    timed_out=True
                    os.killpg(proc.pid,signal.SIGKILL)
                    stdout,stderr=proc.communicate()
                try:
                    os.killpg(proc.pid,signal.SIGKILL)
                except ProcessLookupError:
                    pass
                blocker=_classify_blocker(stderr,timed_out) if proc.returncode != 0 else None

                events=[]
                for line in stdout.splitlines():
                    try:events.append(json.loads(line))
                    except ValueError:pass
                final=_last_agent_message(events).strip()
                report['host_sessions'].append({'name':name,'exit_code':proc.returncode,'blocker':blocker,'stderr_summary':_stderr_summary(stderr),'latency_ms':round((time.monotonic()-started)*1000),'final_digest':hashlib.sha256(final.encode()).hexdigest(),'expected_match':final==expected})
                step(name,proc.returncode==0 and final==expected)
        query='What is the saved repository verification command? Use only supplied memory context; do not inspect files, use tools, or infer a value. Reply only the exact command as plain text without backticks, or MISSING if none was supplied.'
        try:
            with Application(config) as a:
                step('authenticated_backend',a.rollout_backend.search('workspace',config.rollout_generation,1)==[])
                # Retire any unfinished prior test before starting a new one.
                pending=a.ledger.db.execute("SELECT rollout_id,rollback_plan_digest FROM rollout_runs WHERE status IN ('running','draining','stopping','cleanup_required')").fetchall()
            for row in pending:
                cleaned=post('/rollout/rollback',{'rollout_id':row[0],'rollback_plan_digest':row[1],'phase':'run'},True)
                step('prior_cleanup',cleaned['status']=='completed')
            run=post('/rollout/mode',{'mode':'guarded','workspace':str(workspace),'deadline':(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),'max_sessions':10,'max_candidates':20,'generation':config.rollout_generation},True)
            run_id=run['rollout_id'];report['rollout_id']=run_id
            host('no_memory_control',query,'MISSING')
            host('source_event',f'For this project, remember this repository decision: use {marker} as the project verification command. Reply ACK only, without tools.', 'ACK')
            with Application(config) as a:
                candidates=a.ledger.db.execute('SELECT c.* FROM candidates c JOIN rollout_candidates rc USING(candidate_id) WHERE rc.rollout_id=?',(run_id,)).fetchall()
                step('real_host_candidate',len(candidates)==1)
                session_id=candidates[0]['session_id']
                if not args.scheduled:
                    a.ledger.register_backend_generation('openviking',config.rollout_generation)
            started=time.monotonic()
            if args.scheduled:
                until=time.monotonic()+100
                batch={}
                while time.monotonic()<until:
                    with Application(config) as a:
                        row=a.ledger.db.execute("SELECT b.batch_id,b.status FROM batches b JOIN rollout_batches rb USING(batch_id) WHERE rb.rollout_id=? ORDER BY b.created_at DESC LIMIT 1",(run_id,)).fetchone()
                    if row:
                        batch=dict(row)
                        if row['status'] in {'awaiting_review','blocked'}:break
                    time.sleep(1)
            else:
                batch=post('/rollout/batches/run',{'session_id':session_id})
            report['proposal_wait_ms' if args.scheduled else 'judge_latency_ms']=round((time.monotonic()-started)*1000)
            report['batch_id']=batch['batch_id']
            step('judge_proposal',batch['status']=='awaiting_review')
            with Application(config) as a:
                rows=a.ledger.db.execute('SELECT * FROM proposals WHERE batch_id=?',(batch['batch_id'],)).fetchall()
                # Delegated review checks the generated proposal before committing.
                proposal_text=json.dumps([dict(row) for row in rows])
                step('delegated_content_review',marker in proposal_text)
            commit=post('/rollout/batches/'+batch['batch_id']+'/review',{'decision':'approve','reviewer':config.rollout_approver},True)
            step('guarded_commit',len(commit.get('memory_ids',[]))==1)
            report['memory_ids']=commit['memory_ids']
            with Application(config) as a:
                started=time.monotonic()
                if args.scheduled:
                    until=time.monotonic()+60
                    confirmed=False
                    while time.monotonic()<until:
                        row=a.ledger.db.execute("SELECT state FROM projection_operations WHERE rollout_id=?",(run_id,)).fetchone()
                        if row and row['state']=='confirmed':
                            confirmed=True;break
                        time.sleep(1)
                    step('projection',confirmed)
                else:
                    projection=a.projector.drain_once(a.rollout_backend,worker_id='guarded-acceptance',now=datetime.now(timezone.utc),backend_timeout=timedelta(seconds=5),local_completion_margin=timedelta(seconds=2),lease_duration=timedelta(seconds=90),verification_timeout=timedelta(seconds=30),rollout_id=run_id)
                    step('projection',projection.status=='confirmed',status=projection.status)
                report['projection_latency_ms']=round((time.monotonic()-started)*1000)
                until=time.monotonic()+45
                hits=[]
                while time.monotonic()<until:
                    hits=a.rollout_backend.search('workspace',config.rollout_generation,30)
                    if any(h.memory_id in commit['memory_ids'] for h in hits):break
                    time.sleep(1)
                step('search_visibility',any(h.memory_id in commit['memory_ids'] for h in hits))
            host('next_session_consumption',query,marker)
            with Application(config) as a:
                rows=a.ledger.db.execute("SELECT payload_json FROM rollout_audit WHERE rollout_id=? AND kind='injection' ORDER BY created_at DESC",(run_id,)).fetchall()
                injection=next(json.loads(r[0]) for r in rows if commit['memory_ids'][0] in json.loads(r[0]).get('memory_ids',[]))
            receipt=post('/rollout/consumption',{'injection_receipt':injection,'result':{'final_command_matches':True,'command_digest':report['command_digest'],'source':'codex_final_agent_message'}})
            report['consumption_receipt_id']=receipt['receipt_id']
            report['status']='passed'
            report['quality']={'candidates':1,'accepted_candidates':1,'review_modified':0,'consumed_injections':1,'unexpected_recall_controls':0,'daily_quality_sample_size':0}
        except Exception as error:
            report['status']='failed';report['error_class']=type(error).__name__
            report['error_errno']=getattr(error,'errno',None)
            import traceback
            report['error_frames']=[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name} for f in traceback.extract_tb(error.__traceback__)]
            print('runtime_failed',type(error).__name__,flush=True)
        finally:
            if run_id:
                with Application(config) as a:
                    plan=a.ledger.db.execute('SELECT rollback_plan_digest FROM rollout_runs WHERE rollout_id=?',(run_id,)).fetchone()[0]
                try:
                    report['rollback']=post('/rollout/rollback',{'rollout_id':run_id,'rollback_plan_digest':plan,'phase':'run'},True)
                    if report['rollback']['status']!='completed':report['status']='cleanup_required'
                except Exception as error:
                    report['status']='cleanup_required';report['cleanup_error_class']=type(error).__name__
            if report['status']=='passed':
                try:host('post_rollback_control',query,'MISSING')
                except Exception:report['status']='failed'
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(json.dumps(report,indent=2)+'\n')
            print('report_status',report['status'],flush=True)
    return 0 if report['status']=='passed' else 1

if __name__=='__main__':raise SystemExit(main())
