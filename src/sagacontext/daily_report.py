"""Read-only daily rollout review queue and denominators. No raw events."""
import json


def pending(ledger):
    rows = ledger.db.execute(
        "SELECT r.rollout_id,b.batch_id,b.status FROM batches b JOIN rollout_batches rb USING(batch_id) "
        "JOIN rollout_runs r USING(rollout_id) WHERE r.owner_id=? AND r.status IN ('running','draining') "
        "AND b.status='awaiting_review' ORDER BY b.created_at", (ledger.owner_id,)).fetchall()
    return [{**dict(row), 'proposals': [dict(p) for p in ledger.db.execute(
        'SELECT proposal_id,operation,memory_type,scope_json,payload_patch_json,evidence_ids_json FROM proposals WHERE batch_id=?', (row['batch_id'],))]} for row in rows]


def report(ledger, rollout_id):
    row = ledger.db.execute('SELECT * FROM rollout_runs WHERE rollout_id=? AND owner_id=?', (rollout_id,ledger.owner_id)).fetchone()
    if not row:
        raise ValueError('rollout_not_found')
    def groups(query):
        return {r[0]:r[1] for r in ledger.db.execute(query, (rollout_id,))}
    def count(table):
        return ledger.db.execute('SELECT COUNT(*) FROM '+table+' WHERE rollout_id=?',(rollout_id,)).fetchone()[0]
    latencies = [json.loads(r[0]).get('latency_ms') for r in ledger.db.execute("SELECT payload_json FROM rollout_audit WHERE rollout_id=? AND kind='judge_scheduled'",(rollout_id,))]
    latencies = sorted(v for v in latencies if isinstance(v,int))
    def quantile(p):
        import math
        return latencies[max(0,math.ceil(len(latencies)*p)-1)] if latencies else None
    return {'rollout_id':rollout_id,'mode':row['mode'],'status':row['status'],'deadline':row['deadline'],
        'session_reservations':count('rollout_sessions'),'session_limit':row['max_sessions'],
        'candidate_reservations':count('rollout_candidate_reservations'),'candidate_limit':row['max_candidates'],
        'batches':groups('SELECT b.status,COUNT(*) FROM batches b JOIN rollout_batches rb USING(batch_id) WHERE rb.rollout_id=? GROUP BY b.status'),
        'reviews':groups('SELECT decision,COUNT(*) FROM rollout_review_receipts WHERE rollout_id=? GROUP BY decision'),
        'proposals':groups('SELECT p.operation,COUNT(*) FROM proposals p JOIN rollout_batches rb USING(batch_id) WHERE rb.rollout_id=? GROUP BY p.operation'),
        'judge_latency_ms':{'samples':len(latencies),'p50':quantile(.5),'p95':quantile(.95)},
        'consumption_reports':count('rollout_consumption_receipts'),
        # No observational labels means unknown, not a zero error rate.
        'candidate_effectiveness':None,'review_modification_rate':None,'wrong_recall_rate':None,
        'consumption_rate':None,'quality_status':'requires_observational_labels',
        'projection_states':groups('SELECT state,COUNT(*) FROM projection_operations WHERE rollout_id=? GROUP BY state'),
        'observations':observation_metrics(ledger,rollout_id),
        'scope_expansion_admitted':False}


def annotate(ledger, rollout_id, *, receipt_id, kind, target_id, value):
    """Record one categorical operator observation, never arbitrary text.

    One label per target/kind; a duplicate receipt is idempotent, conflicting
    replacements require explicit correction tooling (not silent overwrites).
    """
    allowed = {'candidate_valid':{'yes','no'}, 'review_modified':{'yes','no'},
               'wrong_recall':{'yes','no'}, 'consumed':{'yes','no','unknown'}}
    if kind not in allowed or value not in allowed[kind] or not receipt_id:
        raise ValueError('invalid_observation')
    if not ledger.db.execute('SELECT 1 FROM rollout_runs WHERE rollout_id=? AND owner_id=?',(rollout_id,ledger.owner_id)).fetchone():
        raise ValueError('rollout_not_found')
    if kind == 'candidate_valid':
        valid = ledger.db.execute('SELECT 1 FROM rollout_candidate_reservations WHERE rollout_id=? AND candidate_id=?',(rollout_id,target_id)).fetchone()
    elif kind == 'review_modified':
        valid = ledger.db.execute('SELECT 1 FROM rollout_batches WHERE rollout_id=? AND batch_id=?',(rollout_id,target_id)).fetchone()
    else:
        valid = ledger.db.execute("SELECT 1 FROM rollout_audit WHERE rollout_id=? AND receipt_id=? AND kind='injection'",(rollout_id,target_id)).fetchone()
    if not valid:
        raise ValueError('observation_target_mismatch')
    payload={'kind':kind,'target_id':target_id,'value':value,'source':'operator_observation'}
    with ledger._write_transaction():
        rows=ledger.db.execute("SELECT receipt_id,payload_json FROM rollout_audit WHERE rollout_id=? AND kind='quality_observation'",(rollout_id,)).fetchall()
        for row in rows:
            old=json.loads(row['payload_json'])
            if row['receipt_id']==receipt_id or (old['kind']==kind and old['target_id']==target_id):
                if old!=payload:raise ValueError('observation_conflict')
                return {'receipt_id':row['receipt_id'],'status':'duplicate'}
        result=ledger.record_rollout_receipt('quality_observation',payload,rollout_id=rollout_id,receipt_id=receipt_id)
    return {**result,'status':'recorded'}


def observation_metrics(ledger, rollout_id):
    groups={kind:[] for kind in ('candidate_valid','review_modified','wrong_recall','consumed')}
    for row in ledger.db.execute("SELECT payload_json FROM rollout_audit WHERE rollout_id=? AND kind='quality_observation'",(rollout_id,)):
        label=json.loads(row[0]);groups[label['kind']].append(label['value'])
    return {kind:{'yes':values.count('yes'),'no':values.count('no'),'unknown':values.count('unknown'),
                  'labeled_samples':len(values),'yes_rate':values.count('yes')/(values.count('yes')+values.count('no')) if any(v!='unknown' for v in values) else None}
            for kind,values in groups.items()}
