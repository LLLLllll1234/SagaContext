import json
import sqlite3
import pytest
from sagacontext.console.db import ConsoleReadError
from sagacontext.ledger import TaskContext


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
