"""Exercise a nonempty isolated shadow and export counts before/after rollback."""
from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sagacontext.config import Config
from sagacontext.ledger import Ledger, Scope
from sagacontext.maintenance import DeltaProposal, ScriptedJudge
from sagacontext.rollout import RolloutRuntime


def run(output: Path):
    if output.exists():
        raise ValueError('refusing_to_overwrite_audit')
    with tempfile.TemporaryDirectory(prefix='sagacontext-rollout-audit-') as tmp:
        root = Path(tmp)
        ledger = Ledger(root / 'ledger.db', owner_id='synthetic-audit-owner')
        try:
            identity = ledger.register_project('isolated-shadow',root)
            config = Config(ledger_path=root/'ledger.db',state_path=root/'state.db',rollout_mode='shadow',
                rollout_workspaces=(str(root),),rollout_host='codex',rollout_host_version='codex-cli 0.153.4',
                rollout_generation='audit-g1',rollout_approver='synthetic-auditor',rollout_key_id='synthetic-key',
                rollout_token_digest=hashlib.sha256(b'synthetic-audit-token').hexdigest())
            runtime = RolloutRuntime(ledger,config)
            current = datetime.now(timezone.utc)
            auth=dict(token='synthetic-audit-token',approver='synthetic-auditor',key_id='synthetic-key',
                issued_at=current.isoformat(),expires_at=(current+timedelta(minutes=3)).isoformat())
            run=runtime.activate(mode='shadow',workspace=str(root),approval_receipt='audit-activate',
                deadline=current+timedelta(hours=1),**auth)
            rid=run['rollout_id']
            for index in range(3):
                event=runtime.ingest(dict(hook_event_name='UserPromptSubmit',cwd=str(root),host='codex',
                    host_version='codex-cli 0.153.4',source_generation='audit-g1',session_id=f'session-{index}',
                    source_event_ref=f'event-{index}',payload_shape_digest='sha256:synthetic'))
                candidate=runtime.schedule_candidate(event,{'topic_key':f'topic-{index}','memory_type_hint':'decision'})
                proposal=DeltaProposal(candidate_id=candidate['candidate_id'],operation='new',memory_type='decision',
                    scope=Scope(kind='project',project_id=identity['project_id']),payload={'key':f'topic-{index}','value':'synthetic'},evidence_ids=(event.event_id,))
                runtime.run_batch(ScriptedJudge((proposal,)),event.session_id)
            before=runtime.rollback_runner.audit(rid)
            plan=ledger.db.execute('SELECT rollback_plan_digest FROM rollout_runs WHERE rollout_id=?',(rid,)).fetchone()[0]
            result=runtime.rollback(rollout_id=rid,plan_digest=plan,phase='run',receipt='audit-rollback',**auth)
            report={'schema':'rollout-shadow-audit-v1','environment':'temporary-ledger-scripted-judge',
                'rollout_id':rid,'formal_workspace_mode':'off','formal_write':False,'recall':False,'injection':False,
                'before':before,'after':runtime.rollback_runner.audit(rid),'rollback_status':result['status'],
                'total_ledger_memories':ledger.db.execute('SELECT COUNT(*) FROM memories').fetchone()[0],
                'retention_policy':'quarantined candidates and settled batches retained with event/control evidence until temporary workspace removal'}
        finally:
            ledger.close()
    report['temporary_root_removed']=not root.exists()
    report['temporary_ledger_removed']=not (root/'ledger.db').exists()
    report['residual_files']=len(list(root.rglob('*'))) if root.exists() else 0
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,indent=2)+'\n')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=run(args.output)
    print(json.dumps(result,sort_keys=True))
    raise SystemExit(0 if result['rollback_status']=='completed' and result['residual_files']==0 else 1)
