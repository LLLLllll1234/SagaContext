import sqlite3
import socket
from urllib.parse import urlencode
import pytest
from fastapi.testclient import TestClient
from sagacontext.config import Config
from sagacontext.daemon import create_app
from sagacontext.rollout import RolloutRuntime


@pytest.fixture
def client(console_case,tmp_path):
    config=Config(state_path=tmp_path/'unused',ledger_path=console_case.path,port=37780,rollout_stop_file=tmp_path/'STOP')
    with TestClient(create_app(config),base_url='http://localhost:37780') as value:
        yield value


def test_all_console_gets_are_readonly(console_case,client,monkeypatch):
    c=console_case
    def forbidden(*args,**kwargs):
        raise AssertionError('read caused external mutation')
    monkeypatch.setattr(RolloutRuntime,'_run',forbidden)
    monkeypatch.setattr(socket.socket,'connect',forbidden)
    with sqlite3.connect(c.path) as db:
        before=list(db.iterdump())
    window=urlencode({'start':c.start.isoformat(),'end':c.end.isoformat()})
    paths=['projects',f'workspaces/{c.workspace_a}/overview?{window}',f'workspaces/{c.workspace_a}/sessions',
        f'projects/{c.project_a}/tasks',f'projects/{c.project_a}/tasks/{c.task_id}?workspace_id={c.workspace_a}',f'workspaces/{c.workspace_a}/activity?{window}',
        f'workspaces/{c.workspace_a}/batches',f'workspaces/{c.workspace_a}/rollouts/{c.rollout_id}',
        f'workspaces/{c.workspace_a}/sessions/{c.session_a}',f'workspaces/{c.workspace_a}/batches/{c.batch_id}',
        f'projects/{c.project_a}/memories?workspace_id={c.workspace_a}',f'workspaces/{c.workspace_a}/memories/{c.memory_id}']
    for path in paths:
        response=client.get('/console/v1/'+path)
        assert response.status_code==200,(path,response.text)
        assert response.headers['cache-control']=='no-store'
        assert 'request_id' in response.json()['meta']
        assert 'private.example' not in response.text
    with sqlite3.connect(c.path) as db:
        assert list(db.iterdump())==before


def test_access_and_errors(client,console_case):
    c=console_case
    for headers in ({'Origin':'https://evil.invalid'},{'Host':'evil.invalid:37780'},{'Host':'localhost:1234'},{'Sec-Fetch-Site':'cross-site'}):
        assert client.get('/console/v1/projects',headers=headers).status_code==403
    assert client.get('/console/v1/workspaces/missing/sessions').status_code==404
    assert client.get(f'/console/v1/workspaces/{c.workspace_a}/sessions?limit=101').status_code==400
    assert client.get(f'/console/v1/workspaces/{c.workspace_a}/overview?start=2026-01-01&end=2027-01-01').status_code==400
    assert client.get(f'/console/v1/projects/{c.project_b}/tasks/{c.task_id}?workspace_id={c.workspace_a}').status_code==404
    assert client.get(f'/console/v1/projects/{c.project_a}/tasks/missing?workspace_id={c.workspace_a}').status_code==404
    assert client.get('/console/v1/no-such-api').status_code==404


def test_invalid_response_is_a_safe_console_error(client,console_case):
    import json
    c=console_case
    with sqlite3.connect(c.path) as db:
        db.execute("UPDATE rollout_audit SET payload_json=? WHERE receipt_id='demo-injection'",
            (json.dumps({'status':['invalid stored shape'],'memory_ids':[],'revisions':[]}),))
    response=client.get(f'/console/v1/workspaces/{c.workspace_a}/sessions/{c.session_a}')
    assert response.status_code == 503
    assert response.json()['error']['code'] == 'data_invalid'
    assert 'invalid stored shape' not in response.text


def test_corrupt_stored_json_returns_data_invalid(client,console_case):
    c=console_case
    with sqlite3.connect(c.path) as db:
        db.execute("UPDATE revisions SET payload_json='{private-data' WHERE memory_id=?",(c.memory_id,))
    response=client.get(f'/console/v1/workspaces/{c.workspace_a}/memories/{c.memory_id}')
    assert response.status_code==503
    assert response.json()['error']['code']=='data_invalid'
    assert 'private-data' not in response.text
