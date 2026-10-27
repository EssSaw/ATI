from fastapi.testclient import TestClient
from backend.app.main import app


def test_local_json_requests_remain_available():
    with TestClient(app) as client:
        assert client.get('/health').status_code == 200
        assert client.post('/agent/project-chat', json={'messages': []}, headers={'Origin': 'http://localhost:5173'}).status_code != 403


def test_untrusted_origin_cannot_reach_local_api():
    with TestClient(app) as client:
        result = client.post('/agent/workspaces/open', json={'path': '/private'}, headers={'Origin': 'https://attacker.example'})
    assert result.status_code == 403


def test_rebinding_host_is_rejected():
    with TestClient(app, base_url='http://attacker.example') as client:
        assert client.get('/health').status_code == 403


def test_cross_site_fetch_is_rejected_even_without_origin():
    with TestClient(app) as client:
        assert client.post('/agent/workspaces/open', json={}, headers={'Sec-Fetch-Site': 'cross-site'}).status_code == 403


def test_form_requests_cannot_mutate_workspace():
    with TestClient(app) as client:
        assert client.post('/agent/workspaces/open', content='path=/private', headers={'Content-Type': 'application/x-www-form-urlencoded'}).status_code == 415
