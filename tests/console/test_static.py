from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sagacontext.console.static import mount_console_static


@pytest.fixture
def static_client(tmp_path):
    api = FastAPI()
    api.state.runtime = SimpleNamespace(config=SimpleNamespace(host='127.0.0.1', port=37781))
    mount_console_static(api, tmp_path)
    return TestClient(api, base_url='http://localhost:37781'), tmp_path


def test_missing_assets_and_api_are_not_html(static_client):
    client, root = static_client
    assert client.get('/console/').json()['error']['code'] == 'console_assets_missing'
    (root/'index.html').write_text('<head></head><body>console</body>')
    for path in ['v1/unknown', 'missing.js', 'assets/missing.js', 'assets/%2e%2e/index.html', 'assets/%2e%2e/%2e%2e/private.txt']:
        response = client.get('/console/'+path)
        assert response.status_code == 404
        assert 'text/html' not in response.headers.get('content-type', '')


def test_only_legal_routes_and_contained_assets_are_served(static_client, tmp_path):
    client, root = static_client
    (root/'index.html').write_text('<head></head><body>console</body>')
    (root/'assets').mkdir()
    (root/'assets/app.js').write_text('console.log("fixture")')
    (root/'assets/escape.js').symlink_to(tmp_path.parent/'private.js')
    (tmp_path.parent/'private.js').write_text('private')
    for path in ['', 'deployment', 'projects/p/workspaces/w', 'projects/p/workspaces/w/batches/b']:
        response = client.get('/console/'+path)
        assert response.status_code == 200
        assert "frame-ancestors 'none'" in response.headers['content-security-policy']
    assert client.get('/console/assets/app.js').status_code == 200
    assert client.get('/console/assets/escape.js').status_code == 404
    assert client.get('/console/deployment/unknown').status_code == 404
    assert client.get('/console/random').status_code == 404
    assert client.get('/console/', headers={'Host': 'evil.invalid:37781'}).status_code == 403
