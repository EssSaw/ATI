from unittest.mock import patch

import httpx
import pytest


def gateway_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_models_uses_gateway_key_and_preserves_exact_ids(client):
    def handler(request):
        assert request.method == 'GET'
        assert str(request.url) == 'http://localhost:20128/v1/models'
        assert request.headers['Authorization'] == 'Bearer fixture-router-key'
        return httpx.Response(200, json={'data': [{'id': 'cx/model-b'}, {'id': 'cc/model-a'}, {'id': 'cx/model-b'}]})

    with patch('backend.app.api.routes.httpx.AsyncClient', return_value=gateway_client(handler)):
        response = client.post('/agent/models', json={'router_base_url': 'http://localhost:20128/v1/', 'router_api_key': 'fixture-router-key'})
    assert response.status_code == 200
    assert response.json() == {'models': ['cc/model-a', 'cx/model-b']}
    assert 'fixture-router-key' not in response.text


@pytest.mark.parametrize('data', [{}, {'data': None}, {'data': [{}]}, {'data': [{'id': 123}]}, {'data': [{'id': ''}]}])
def test_models_rejects_malformed_gateway_response(client, data):
    with patch('backend.app.api.routes.httpx.AsyncClient', return_value=gateway_client(lambda r: httpx.Response(200, json=data))):
        response = client.post('/agent/models', json={})
    assert response.status_code == 502


def test_empty_model_catalog_is_valid(client):
    with patch('backend.app.api.routes.httpx.AsyncClient', return_value=gateway_client(lambda r: httpx.Response(200, json={'data': []}))):
        response = client.post('/agent/models', json={})
    assert response.json() == {'models': []}


@pytest.mark.parametrize('status,expected', [(401, 401), (403, 403), (429, 429), (500, 502), (302, 502)])
def test_gateway_errors_are_sanitized(client, status, expected):
    with patch('backend.app.api.routes.httpx.AsyncClient', return_value=gateway_client(lambda r: httpx.Response(status, json={'error': 'private-upstream-body'}))):
        response = client.post('/agent/models', json={})
    assert response.status_code == expected
    assert 'private-upstream-body' not in response.text


@pytest.mark.parametrize('error,expected', [(httpx.ReadTimeout('secret-url'), 504), (httpx.ConnectError('secret-url'), 502)])
def test_gateway_transport_failure(client, error, expected):
    def handler(request):
        raise error
    with patch('backend.app.api.routes.httpx.AsyncClient', return_value=gateway_client(handler)):
        response = client.post('/agent/models', json={})
    assert response.status_code == expected
    assert 'secret-url' not in response.text


def test_gateway_rejects_metadata_url_before_request(client):
    with patch('backend.app.api.routes.httpx.AsyncClient') as upstream:
        response = client.post('/agent/models', json={'router_base_url': 'http://169.254.169.254/v1'})
    upstream.assert_not_called()
    assert response.status_code == 400
