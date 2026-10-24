import asyncio
from unittest.mock import patch
import httpx
import pytest
from fastapi import HTTPException
from backend.app.agent.llm_client import call_chat_completion


def test_client_sends_bearer_key_and_exact_router_model():
    def handler(request):
        assert request.url == 'http://localhost:20128/v1/chat/completions'
        assert request.headers['Authorization'] == 'Bearer test-router-key'
        assert b'cx/test-model' in request.content
        return httpx.Response(200, json={'choices': [{'message': {'role': 'assistant', 'content': 'ok'}}]})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with patch('backend.app.agent.llm_client.httpx.AsyncClient', return_value=client):
        result = asyncio.run(call_chat_completion('http://localhost:20128/v1', 'test-router-key', 'cx/test-model', []))
    assert result['choices'][0]['message']['content'] == 'ok'


@pytest.mark.parametrize('status,payload', [(401, {'error': {'message': 'private-code secret-key'}}), (200, {'bad': 'response'})])
def test_client_errors_do_not_expose_upstream_data(status, payload):
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(status, json=payload)))
    with patch('backend.app.agent.llm_client.httpx.AsyncClient', return_value=client):
        with pytest.raises(HTTPException) as error:
            asyncio.run(call_chat_completion('https://example.test/v1', 'secret-key', 'test', []))
    assert 'secret-key' not in error.value.detail
    assert 'private-code' not in error.value.detail
