"""Scoped evidence/detail projections. No raw events or source locators leave here."""
from __future__ import annotations
import json
from pathlib import PurePosixPath

from sagacontext.ledger import Scope, TaskContext
from sagacontext.ledger.access import scope_allows
from . import queries
from .db import ConsoleReadError
from .serialize import safe_payload, safe_text


def context_for(db,owner_id,workspace_id,context=None):
    identity=queries.workspace(db,owner_id,workspace_id)
    context=context or TaskContext(owner_id=owner_id,project_id=identity['project_id'],workspace_id=workspace_id)
    if (context.owner_id != owner_id or context.project_id != identity['project_id'] or context.workspace_id != workspace_id):
        raise ConsoleReadError('not_found')
    if context.task_id and not db.execute('SELECT 1 FROM tasks WHERE task_id=? AND owner_id=? AND project_id=?',(context.task_id,owner_id,context.project_id)).fetchone():
        raise ConsoleReadError('not_found')
    for path in context.touched_paths:
        if not path or PurePosixPath(path).is_absolute() or '..' in PurePosixPath(path).parts or '\\' in path:
            raise ConsoleReadError('invalid_request')
    return context


def evidence(db,owner_id,workspace_id,evidence_ids):
    result=[]
    for eid in dict.fromkeys(evidence_ids):
        row=db.execute('SELECT evidence_id,source_event_id,evidence_kind,redacted_excerpt FROM evidence WHERE evidence_id=? AND owner_id=?',(eid,owner_id)).fetchone()
        event_id=row['source_event_id'] if row else eid
        source=db.execute('SELECT event_id,session_id,event_kind,received_at FROM events WHERE event_id=? AND owner_id=? AND workspace_id=?',(event_id,owner_id,workspace_id)).fetchone()
        # Cross-workspace provenance is not authorized by a caller's current workspace.
        if row and not source:
            result.append({'evidence_id':eid,'kind':row['evidence_kind'],'excerpt':None,'source':None})
        elif source:
            result.append({'evidence_id':eid,'kind':row['evidence_kind'] if row else 'source_event',
                'excerpt':safe_text(row['redacted_excerpt']) if row and row['redacted_excerpt'] else None,'source':dict(source)})
    return result


def memory(db,owner_id,workspace_id,memory_id,context=None):
    context=context_for(db,owner_id,workspace_id,context)
    row=db.execute("SELECT memory_id,current_revision,memory_type,scope_json,state,conflict_state,ledger_sequence FROM memories WHERE memory_id=? AND owner_id=? AND state!='deleted'",(memory_id,owner_id)).fetchone()
    if not row or not scope_allows(Scope.model_validate_json(row['scope_json']),context):
        raise ConsoleReadError('not_found')
    revisions=[]
    for rev in db.execute('SELECT revision,operation,payload_json,created_at FROM revisions WHERE memory_id=? ORDER BY revision DESC',(memory_id,)):
        eids=[r[0] for r in db.execute('SELECT evidence_id FROM revision_evidence WHERE memory_id=? AND revision=?',(memory_id,rev['revision']))]
        revisions.append({'revision':rev['revision'],'operation':rev['operation'],'created_at':rev['created_at'],
            'payload':safe_payload(json.loads(rev['payload_json'])),'evidence':evidence(db,owner_id,workspace_id,eids)})
    relations=[]
    related=db.execute("""SELECT DISTINCT other.memory_id,
        CASE WHEN own.operation='supersede' THEN 'replaced_by' ELSE 'replaces' END AS relation,
        m.scope_json FROM rollout_commits own
        JOIN rollout_commits other ON other.rollout_id=own.rollout_id AND other.proposal_id=own.proposal_id
          AND other.memory_id!=own.memory_id
        JOIN memories m ON m.memory_id=other.memory_id AND m.owner_id=? AND m.state!='deleted'
        WHERE own.memory_id=? AND ((own.operation='supersede' AND other.operation='new')
          OR (own.operation='new' AND other.operation='supersede'))
        ORDER BY other.memory_id""",(owner_id,memory_id)).fetchall()
    for item in related:
        if scope_allows(Scope.model_validate_json(item['scope_json']),context):
            relations.append({'memory_id':safe_text(item['memory_id']),'relation':item['relation']})
    return {'memory_id':memory_id,'current_revision':row['current_revision'],'memory_type':row['memory_type'],
        'scope':safe_payload(json.loads(row['scope_json'])),'state':row['state'],'conflict_state':row['conflict_state'],
        'relations':relations,'revisions':revisions}


def memories(db,owner_id,project_id,workspace_id,context=None,cursor=None,limit=50,view='applicable'):
    if view not in {'applicable','global','changes'}:
        raise ConsoleReadError('invalid_request')
    context=context_for(db,owner_id,workspace_id,context)
    if context.project_id!=project_id:
        raise ConsoleReadError('not_found')
    visible=[]
    for row in db.execute("SELECT memory_id,scope_json FROM memories WHERE owner_id=? AND state!='deleted'",(owner_id,)):
        scope=Scope.model_validate_json(row['scope_json'])
        if scope_allows(scope,context) and (view!='global' or scope.kind=='global'):
            visible.append(row['memory_id'])
    if not visible:
        # Still validate cursor and limit in the shared paging path.
        visible=['']
    sql="""SELECT m.memory_id,m.current_revision,m.memory_type,m.state,m.conflict_state,
        json_extract(m.scope_json,'$.kind') AS scope_kind,r.created_at,r.payload_json
        FROM memories m JOIN revisions r ON m.memory_id=r.memory_id AND m.current_revision=r.revision
        WHERE m.owner_id=? AND m.memory_id IN ("""+','.join('?' for _ in visible)+')'
    params=(owner_id,*visible)
    if view=='changes':
        sql+=' AND EXISTS (SELECT 1 FROM rollout_commits c JOIN rollout_runs u USING(rollout_id) WHERE c.memory_id=m.memory_id AND u.owner_id=? AND u.workspace_id=?)'
        params+=(owner_id,workspace_id)
    result=queries.page(db,sql,params,keys=('created_at','memory_id'),scope=('memories',context.model_dump(),view),cursor=cursor,limit=limit,sanitize=False)
    for item in result['items']:
        item.update(safe_payload({key:value for key,value in item.items() if key!='payload_json'}))
        item['payload']=safe_payload(json.loads(item.pop('payload_json')))
    return result


def batch(db,owner_id,workspace_id,batch_id,context=None):
    context=context_for(db,owner_id,workspace_id,context)
    row=db.execute('SELECT b.batch_id,b.session_id,b.task_id,b.status,b.created_at,b.attempt_count,b.next_attempt_at,b.last_error_class FROM batches b JOIN sessions s USING(session_id) WHERE b.batch_id=? AND b.owner_id=? AND s.owner_id=b.owner_id AND s.workspace_id=?',(batch_id,owner_id,workspace_id)).fetchone()
    if not row:
        raise ConsoleReadError('not_found')
    proposals=[]
    for p in db.execute('SELECT proposal_id,candidate_id,operation,target_id,expected_revision,memory_type,scope_json,payload_patch_json,evidence_ids_json,rationale_redacted,status FROM proposals WHERE batch_id=? ORDER BY created_at,proposal_id',(batch_id,)):
        scope=Scope.model_validate_json(p['scope_json'])
        allowed=scope_allows(scope,context)
        old=None
        if p['target_id'] and allowed:
            head=db.execute("SELECT state,scope_json FROM memories WHERE memory_id=? AND owner_id=?",(p['target_id'],owner_id)).fetchone()
            if head and head['state']!='deleted' and scope_allows(Scope.model_validate_json(head['scope_json']),context):
                target=db.execute('SELECT payload_json FROM revisions WHERE memory_id=? AND revision=?',(p['target_id'],p['expected_revision'])).fetchone()
                old=safe_payload(json.loads(target[0])) if target else None
            else:
                allowed=False
        outputs=db.execute("""SELECT c.memory_id,m.state,m.scope_json FROM rollout_commits c
            JOIN rollout_runs r ON r.rollout_id=c.rollout_id
            JOIN memories m ON m.memory_id=c.memory_id AND m.owner_id=r.owner_id
            WHERE c.proposal_id=? AND r.owner_id=? AND r.workspace_id=?""",
            (p['proposal_id'],owner_id,workspace_id)).fetchall()
        if p['status']=='committed' and not outputs:
            allowed=False
        for output in outputs:
            if output['state']=='deleted' or not scope_allows(Scope.model_validate_json(output['scope_json']),context):
                allowed=False
                break
        if not allowed:
            old=None
        proposals.append({'proposal_id':p['proposal_id'],'candidate_id':p['candidate_id'],'operation':p['operation'],
            'target_id':p['target_id'] if allowed else None,'expected_revision':p['expected_revision'] if allowed else None,
            'status':p['status'],'scope':scope.model_dump() if allowed else None,'old_payload':old,
            'new_payload':safe_payload(json.loads(p['payload_patch_json'])) if allowed else None,
            'rationale':safe_text(p['rationale_redacted'] or '') if allowed else None,
            'evidence':evidence(db,owner_id,workspace_id,json.loads(p['evidence_ids_json'])) if allowed else [],
            'availability':'available' if allowed else 'unavailable'})
    return {**safe_payload(dict(row)),'proposals':proposals}


def session(db,owner_id,workspace_id,session_id):
    queries.workspace(db,owner_id,workspace_id)
    row=db.execute('SELECT session_id,host,opened_at,closed_at FROM sessions WHERE session_id=? AND owner_id=? AND workspace_id=?',(session_id,owner_id,workspace_id)).fetchone()
    if not row:
        raise ConsoleReadError('not_found')
    truncated=[]
    def limited(name,sql,params):
        rows=[dict(r) for r in db.execute(sql+' LIMIT 101',params)]
        if len(rows)>100:
            truncated.append(name)
        return rows[:100]
    events=limited('events','SELECT event_id,event_kind,occurred_at,received_at FROM events WHERE owner_id=? AND session_id=? ORDER BY received_at DESC,event_id DESC',(owner_id,session_id))
    batches=limited('batches','SELECT batch_id,status,created_at FROM batches WHERE owner_id=? AND session_id=? ORDER BY created_at DESC,batch_id DESC',(owner_id,session_id))
    candidates=limited('candidates','SELECT candidate_id,kind,status,active_batch_id FROM candidates WHERE owner_id=? AND session_id=? ORDER BY created_sequence DESC,candidate_id DESC',(owner_id,session_id))
    injections=[]
    receipts=limited('injections',"SELECT receipt_id,payload_json,created_at FROM rollout_audit WHERE owner_id=? AND workspace_id=? AND session_id=? AND kind='injection' ORDER BY created_at DESC,receipt_id DESC",(owner_id,workspace_id,session_id))
    for receipt in receipts:
        payload=json.loads(receipt['payload_json'])
        fields=('status','reason','memory_ids','revisions','generation','ledger_sequence','omissions','bundle_digest','session_digest')
        safe={key:payload.get(key) for key in fields}
        safe['memory_ids']=safe['memory_ids'] or []
        safe['revisions']=safe['revisions'] or []
        safe['omissions']=safe['omissions'] or []
        safe['consumption_records']=[dict(r) for r in db.execute('SELECT receipt_id,status,created_at FROM rollout_consumption_receipts WHERE owner_id=? AND session_digest=? AND bundle_digest=? ORDER BY created_at,receipt_id',(owner_id,payload.get('session_digest'),payload.get('bundle_digest')))] if payload.get('bundle_digest') else []
        injections.append(safe_payload({'receipt_id':receipt['receipt_id'],'created_at':receipt['created_at'],**safe}))
    bindings=[dict(r) for r in db.execute('SELECT task_id,start_event_id,end_event_id FROM task_bindings WHERE owner_id=? AND session_id=? ORDER BY created_at, binding_id',(owner_id,session_id))]
    return safe_payload({**dict(row),'events':events,'batches':batches,'candidates':candidates,
        'injections':injections,'bindings':bindings,'truncated':truncated})
