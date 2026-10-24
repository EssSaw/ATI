import json
from unittest.mock import patch


def test_health_endpoint(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_providers_endpoint(client):
    res = client.get("/providers")
    assert res.status_code == 200
    data = res.json()
    assert "providers" in data
    names = [p["name"] for p in data["providers"]]
    assert "openai" in names
    assert "groq" in names
    assert "xai" in names
    assert "openrouter" in names
    assert "custom" in names
    assert "9router" in names


def test_agent_run_success(client):
    mock_report = {
        "summary": "Detected 1 syntax issue.",
        "bugs": [
            {
                "type": "syntax",
                "line": 1,
                "message": "Missing colon",
                "suggestion": "Add colon",
            }
        ],
        "explanation": "Colons are required for function definitions.",
        "fixed_code": "def foo(): pass",
    }
    llm_mock_res = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {
                                "name": "submit_report",
                                "arguments": json.dumps(mock_report),
                            },
                        }
                    ],
                }
            }
        ]
    }

    with patch("backend.app.agent.runner.call_chat_completion", return_value=llm_mock_res):
        res = client.post(
            "/agent/run",
            json={
                "code": "def foo() pass",
                "question": "What is wrong?",
                "settings": {
                    "mode": "direct",
                    "provider": "openai",
                    "api_key": "sk-secret-key-12345",
                },
            },
        )

    assert res.status_code == 200
    data = res.json()
    assert "session_id" in data
    assert data["report"]["summary"] == "Detected 1 syntax issue."
    assert len(data["report"]["bugs"]) == 1
    assert data["report"]["bugs"][0]["type"] == "syntax"
    assert "submit_report" in data["tools_used"]
    assert "latency_ms" in data

    # Verify API key is NOT leaked anywhere in response!
    assert "sk-secret-key-12345" not in res.text


def test_agent_run_missing_code_validation_error(client):
    res = client.post("/agent/run", json={"question": "No code"})
    assert res.status_code == 422
    data = res.json()
    assert "detail" in data


def test_agent_run_forbidden_metadata_url_returns_400(client):
    res = client.post(
        "/agent/run",
        json={
            "code": "print(1)",
            "settings": {
                "mode": "direct",
                "provider": "custom",
                "base_url": "http://169.254.169.254/v1",
                "api_key": "dummy",
            },
        },
    )
    assert res.status_code == 400
    data = res.json()
    assert "detail" in data
    assert "forbidden" in data["detail"].lower()


def test_agent_run_missing_key_returns_400(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    res = client.post(
        "/agent/run",
        json={
            "code": "print(1)",
            "settings": {
                "mode": "direct",
                "provider": "openai",
                "api_key": None,
            },
        },
    )
    assert res.status_code == 400
    data = res.json()
    assert "detail" in data
    assert "Missing API key" in data["detail"]


def test_agent_chat_workflow(client):
    # First create a session via agent/run
    mock_report = {
        "summary": "Clean code.",
        "bugs": [],
        "explanation": "Code runs cleanly.",
        "fixed_code": "x = 42",
    }
    llm_run_res = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {
                                "name": "submit_report",
                                "arguments": json.dumps(mock_report),
                            },
                        }
                    ],
                }
            }
        ]
    }
    with patch("backend.app.agent.runner.call_chat_completion", return_value=llm_run_res):
        run_res = client.post(
            "/agent/run",
            json={
                "code": "x = 42",
                "settings": {"api_key": "test-key"},
            },
        )
    session_id = run_res.json()["session_id"]

    # Now post a follow-up chat message
    llm_chat_res = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Assigning 42 to x binds the integer 42 to variable x.",
                }
            }
        ]
    }
    with patch("backend.app.agent.runner.call_chat_completion", return_value=llm_chat_res):
        chat_res = client.post(
            "/agent/chat",
            json={
                "session_id": session_id,
                "message": "What does x = 42 actually do?",
                "settings": {"api_key": "test-key"},
            },
        )

    assert chat_res.status_code == 200
    assert "Assigning 42" in chat_res.json()["reply"]


def test_agent_chat_not_found(client):
    res = client.post(
        "/agent/chat",
        json={
            "session_id": "non-existent-uuid",
            "message": "Hello?",
            "settings": {"api_key": "test-key"},
        },
    )
    assert res.status_code == 404
    data = res.json()
    assert "detail" in data
    assert "Session not found" in data["detail"]
