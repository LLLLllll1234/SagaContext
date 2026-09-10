"""Local operator entry point; secrets never appear in process arguments."""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from .application import Application
from .config import Config



def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['start','status','stop','rollback'])
    parser.add_argument('--rollout-id')
    args=parser.parse_args()
    config=Config.load()
    root=config.ledger_path.parent
    root.mkdir(parents=True,exist_ok=True)
    pidfile=root/'daemon.pid'
    if args.action=='rollback':
        if not args.rollout_id:parser.error('--rollout-id required')
        now=datetime.now(timezone.utc)
        # Use the configured operator token file, not the backend credential.
        import tomllib
        settings=tomllib.loads((root/'config.toml').read_text())
        token_path=Path(settings['rollout']['token_file']).expanduser()
        with Application(config) as a:
            row=a.ledger.db.execute('SELECT rollback_plan_digest FROM rollout_runs WHERE rollout_id=?',(args.rollout_id,)).fetchone()
            if not row:raise ValueError('rollout_not_found')
            result=a.rollout.rollback(rollout_id=args.rollout_id,plan_digest=row[0],phase='run',
                receipt=str(uuid.uuid4()),approver=config.rollout_approver,key_id=config.rollout_key_id,
                token=token_path.read_text().strip(),issued_at=now.isoformat(),expires_at=(now+timedelta(minutes=4)).isoformat())
        print(json.dumps(result));return
    if args.action=='stop':
        config.rollout_stop_file.touch(mode=0o600)
        with Application(config) as a:a.rollout.stop(reason='operator_stop')
        if pidfile.exists():
            pid=int(pidfile.read_text())
            observed=subprocess.run(['ps','-p',str(pid),'-o','args='],capture_output=True,text=True).stdout
            if '-m sagacontext.daemon' in observed:
                os.kill(pid,15)
            pidfile.unlink(missing_ok=True)
        print(json.dumps({'status':'stopped','stop_switch':True}));return
    if args.action=='start':
        try:
            with httpx.Client(trust_env=False,timeout=1) as c:
                response=c.get(f'http://{config.host}:{config.port}/health')
                already_running=response.status_code==200
        except httpx.HTTPError:
            already_running=False
        if not already_running:
            repo=Path(__file__).resolve().parents[2]
            env=os.environ.copy();env['PYTHONPATH']=str(repo/'src');env['SAGACONTEXT_HOME']=str(root)
            log=root/'daemon.log';log.touch(mode=0o600);log.chmod(0o600)
            with log.open('a') as output:
                process=subprocess.Popen([str(repo/'.venv/bin/python'),'-m','sagacontext.daemon'],
                    cwd=repo,env=env,stdin=subprocess.DEVNULL,stdout=output,stderr=output,
                    start_new_session=True,close_fds=True)
            pidfile.write_text(str(process.pid));pidfile.chmod(0o600)
    for _ in range(30 if args.action=='start' else 1):
        try:
            with httpx.Client(trust_env=False,timeout=1) as c:
                response=c.get(f'http://{config.host}:{config.port}/health')
                if response.status_code==200:
                    with Application(config) as a:mode=a.rollout.mode.value
                    print(json.dumps({'status':'running','mode':mode,'stop_switch':config.rollout_stop_file.exists()}));return
        except httpx.HTTPError:pass
        time.sleep(.2)
    print(json.dumps({'status':'unavailable'}));raise SystemExit(1)

if __name__=='__main__':
    try:main()
    except (OSError,ValueError,KeyError) as error:
        print(json.dumps({'status':'failed','error_class':type(error).__name__}));raise SystemExit(1)
