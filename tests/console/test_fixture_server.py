import socket
from pathlib import Path

from fastapi.testclient import TestClient
from scripts.serve_console_fixture import fixture_app
from sagacontext.application import Application
from sagacontext.config import Config


def test_fixture_never_loads_real_runtime_and_cleans_up(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('fixture touched real runtime')
    monkeypatch.setattr(Config, 'load', forbidden)
    monkeypatch.setattr(Application, '__init__', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    api = fixture_app()
    with TestClient(api, base_url='http://localhost:37781') as client:
        path = api.state.fixture.path
        assert path.exists()
        assert api.state.scheduler is None
        assert client.get('/console/v1/projects').status_code == 200
        assert client.post('/events').status_code == 404
        assert client.get('/health').status_code == 404
    assert not path.parent.exists()


def test_empty_and_stale_scenarios():
    from urllib.parse import urlencode
    for scenario in ['empty', 'stale']:
        api = fixture_app(scenario=scenario)
        with TestClient(api, base_url='http://localhost:37781') as client:
            case = api.state.fixture
            path = '/console/v1/workspaces/'+case.workspace_a+'/overview?'+urlencode({
                'start':case.start.isoformat(), 'end':case.end.isoformat()})
            response = client.get(path)
            assert response.status_code == 200
            if scenario == 'empty':
                assert response.json()['data']['sessions']['value']['items'] == []
            else:
                assert client.get(path).status_code == 503
