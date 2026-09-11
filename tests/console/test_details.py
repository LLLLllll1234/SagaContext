import json
import sqlite3
import pytest
from sagacontext.console.db import ConsoleReadError
from sagacontext.ledger import TaskContext


def add_supersede_pair(c):
    successor='successor-memory'
    with sqlite3.connect(c.path) as db:
        scope=db.execute('SELECT scope_json FROM memories WHERE memory_id=?',(c.memory_id,)).fetchone()[0]
        stamp='2026-09-11T01:00:00+00:00'
        db.execute("UPDATE memories SET state='retired' WHERE memory_id=?",(c.memory_id,))
        db.execute("INSERT INTO revisions VALUES (?,?,?,?,?,?,?)",
                   (c.memory_id,2,'supersede',1,json.dumps({'rule':'新约定'}),stamp,'synthetic'))
        db.execute("INSERT INTO memories VALUES (?,?,?,?,?,'active','none',?)",
                   (successor,c.owner_a,1,'convention',scope,100))
        db.execute("INSERT INTO revisions VALUES (?,?,?,?,?,?,?)",
                   (successor,1,'new',1,json.dumps({'rule':'新约定'}),stamp,'synthetic'))
        db.execute("""INSERT INTO proposals(proposal_id,batch_id,candidate_id,operation,target_id,
            expected_revision,memory_type,scope_json,payload_patch_json,evidence_ids_json,
            input_digest,output_digest,source_kind,status,created_at)
            SELECT 'supersede-proposal',batch_id,candidate_id,'supersede',?,1,memory_type,scope_json,
            ?,evidence_ids_json,'supersede-in','supersede-out',source_kind,'committed',?
            FROM proposals WHERE proposal_id='demo-proposal'""",
            (c.memory_id,json.dumps({'rule':'新约定'}),stamp))
        db.execute("""INSERT INTO rollout_commits(rollout_id,proposal_id,memory_id,revision,
            previous_revision,previous_state,operation,created_at) VALUES (?,?,?,?,?,?,?,?)""",
            (c.rollout_id,'supersede-proposal',c.memory_id,2,1,'active','supersede',stamp))
        db.execute("""INSERT INTO rollout_commits(rollout_id,proposal_id,memory_id,revision,
            operation,created_at) VALUES (?,?,?,?,?,?)""",
            (c.rollout_id,'supersede-proposal',successor,1,'new',stamp))
    return successor


def test_proposal_memory_and_evidence_connect(console_case):
    c=console_case
    batch=c.service.batch(c.workspace_a,c.batch_id)['data']
    proposal=next(p for p in batch['proposals'] if p['proposal_id']=='demo-proposal')
    assert proposal['old_payload']=={'rule':'记录失败原因'}
    assert proposal['evidence'][0]['source']['session_id']==c.session_a
    memory=c.service.memory(c.workspace_a,c.memory_id)['data']
    assert memory['revisions'][0]['evidence'][0]['source']['event_id']==c.event_id
    session=c.service.session(c.workspace_a,c.session_a)['data']
    assert session['injections'][0]['consumption_records']==[]
    assert 'payload' not in session['events'][0]


def test_deleted_body_cannot_be_read_via_memory_or_proposal(console_case):
    c=console_case
    with sqlite3.connect(c.path) as db:
        db.execute("UPDATE memories SET state='deleted' WHERE memory_id=?",(c.memory_id,))
    with pytest.raises(ConsoleReadError,match='not_found'):
        c.service.memory(c.workspace_a,c.memory_id)
    assert c.service.memories(c.project_a,c.workspace_a)['data']['items']==[]
    proposal=next(p for p in c.service.batch(c.workspace_a,c.batch_id)['data']['proposals'] if p['proposal_id']=='demo-proposal')
    assert proposal['old_payload'] is None and proposal['new_payload'] is None
    committed=next(p for p in c.service.batch(c.workspace_a,c.batch_id)['data']['proposals'] if p['proposal_id']=='demo-committed')
    assert committed['target_id'] is None
    assert committed['new_payload'] is None
    assert committed['rationale'] is None
    assert committed['evidence'] == []
    assert committed['availability'] == 'unavailable'


@pytest.mark.parametrize('rule', ['token=abc', 'Bearer abc', '-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----'])
def test_memory_list_parses_json_before_redaction(console_case, rule):
    c=console_case
    with sqlite3.connect(c.path) as db:
        db.execute('UPDATE revisions SET payload_json=? WHERE memory_id=? AND revision=1',
                   (json.dumps({'rule':rule}),c.memory_id))
    response=c.service.memories(c.project_a,c.workspace_a)
    assert response['data']['items'][0]['payload']['rule'] != rule


def test_context_and_cross_workspace_sources(console_case):
    c=console_case
    with pytest.raises(ConsoleReadError,match='not_found'):
        c.service.session(c.workspace_b,c.session_a)
    memory=c.service.memory(c.workspace_b,c.memory_id)['data']
    assert memory['revisions'][0]['evidence'][0]['source'] is None
    context=TaskContext(owner_id=c.owner_a,project_id=c.project_a,workspace_id=c.workspace_a,touched_paths=['../secret'])
    with pytest.raises(ConsoleReadError,match='invalid_request'):
        c.service.memory(c.workspace_a,c.memory_id,context)


def test_path_memory_requires_matching_context(console_case):
    c=console_case
    with sqlite3.connect(c.path) as db:
        db.execute('UPDATE memories SET scope_json=? WHERE memory_id=?',(json.dumps({'kind':'path','project_id':c.project_a,'path_pattern':'src/*'}),c.memory_id))
    with pytest.raises(ConsoleReadError):
        c.service.memory(c.workspace_a,c.memory_id)
    context=TaskContext(owner_id=c.owner_a,project_id=c.project_a,workspace_id=c.workspace_a,touched_paths=['src/app.py'])
    assert c.service.memory(c.workspace_a,c.memory_id,context)['data']['memory_id']==c.memory_id


def test_supersede_relations_and_inaccessible_successor(console_case):
    c=console_case
    successor=add_supersede_pair(c)
    assert c.service.memory(c.workspace_a,c.memory_id)['data']['relations']==[
        {'memory_id':successor,'relation':'replaced_by'}]
    assert c.service.memory(c.workspace_a,successor)['data']['relations']==[
        {'memory_id':c.memory_id,'relation':'replaces'}]

    with sqlite3.connect(c.path) as db:
        db.execute("UPDATE memories SET state='deleted' WHERE memory_id=?",(successor,))
    assert c.service.memory(c.workspace_a,c.memory_id)['data']['relations']==[]
    proposal=next(p for p in c.service.batch(c.workspace_a,c.batch_id)['data']['proposals']
                  if p['proposal_id']=='supersede-proposal')
    assert proposal['availability']=='unavailable'
    assert proposal['old_payload'] is None
    assert proposal['new_payload'] is None
    assert proposal['rationale'] is None
    assert proposal['evidence']==[]


def test_plain_supersede_has_no_inferred_relation(console_case):
    c=console_case
    with sqlite3.connect(c.path) as db:
        db.execute("UPDATE memories SET state='retired' WHERE memory_id=?",(c.memory_id,))
    assert c.service.memory(c.workspace_a,c.memory_id)['data']['relations']==[]


def test_session_reports_truncated_collections(console_case):
    c=console_case
    with sqlite3.connect(c.path) as db:
        base=db.execute('SELECT * FROM events WHERE event_id=?',(c.event_id,)).fetchone()
        columns=[row[1] for row in db.execute('PRAGMA table_info(events)')]
        for index in range(101):
            values=dict(zip(columns,base))
            values.update(event_id=f'extra-{index:03}',source_event_key=f'extra-{index:03}',
                          ingest_sequence=1000+index)
            db.execute(f"INSERT INTO events({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                       tuple(values[column] for column in columns))
    session=c.service.session(c.workspace_a,c.session_a)['data']
    assert len(session['events'])==100
    assert session['truncated']==['events']
