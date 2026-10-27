import json
import sys
import types
from unittest.mock import patch
import pytest
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from backend.app.project_chat import (
    REQUEST_COMMAND_TOOL_DEFINITION,
    router as project_chat_router,
)

local_app = FastAPI()


@local_app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = exc.errors()
    msg = "; ".join([f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']}" for err in errors])
    return JSONResponse(status_code=422, content={"detail": f"Validation error: {msg}"})


local_app.include_router(project_chat_router)


@pytest.fixture
def client():
    return TestClient(local_app)


def make_mock_response(content: str):
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": content,
                }
            }
        ]
    }


def make_mock_tool_response(tool_calls: list):
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": tool_calls,
                }
            }
        ]
    }


# ---------------------------------------------------------------------------
# 1. Basic Workspace Chat Success & Contract Verification
# ---------------------------------------------------------------------------


def test_workspace_chat_success(client):
    """Verify live workspace mode loads snapshot from contract, injects workspace context, and returns revision."""
    workspace_snapshot = {
        "workspace_id": "ws-test-1",
        "name": "LiveWorkspaceApp",
        "path": "/Users/test/projects/LiveWorkspaceApp",
        "files": [
            {"path": "src/main.py", "content": "def run():\n    return 'live code'\n"}
        ],
        "skipped": [],
        "revision": "rev-alpha-1",
    }

    mock_llm = make_mock_response("I see your live workspace project LiveWorkspaceApp.")

    with patch("backend.app.project_chat.get_workspace_snapshot", return_value=workspace_snapshot) as mock_snap:
        with patch("backend.app.project_chat.call_chat_completion", return_value=mock_llm) as mock_call:
            res = client.post(
                "/agent/project-chat",
                json={
                    "workspace_id": "ws-test-1",
                    "messages": [{"role": "user", "content": "Explain run()"}],
                    "settings": {"api_key": "test-key"},
                },
            )

    assert res.status_code == 200
    data = res.json()
    assert data["reply"] == "I see your live workspace project LiveWorkspaceApp."
    assert data["workspace_revision"] == "rev-alpha-1"
    assert data["command_requests"] == []
    assert data["proposals"] == []
    assert data["subagents"] == []

    mock_snap.assert_called_once_with("ws-test-1")
    kwargs = mock_call.call_args.kwargs
    # Verify workspace tools include request_command
    tool_names = [t["function"]["name"] for t in kwargs["tools"]]
    assert "request_command" in tool_names

    # Verify system prompt has live workspace mode instructions
    llm_msgs = kwargs["messages"]
    system_prompt = llm_msgs[0]["content"]
    assert "LIVE WORKSPACE MODE INSTRUCTIONS" in system_prompt
    assert "request_command" in system_prompt
    assert "LiveWorkspaceApp" in llm_msgs[1]["content"]
    assert "src/main.py" in llm_msgs[1]["content"]


# ---------------------------------------------------------------------------
# 2. Workspace Invalid Error: No Fallback
# ---------------------------------------------------------------------------


def test_workspace_invalid_error_no_fallback(client):
    """When workspace_id is invalid or snapshot raises an error, must return 400 and NOT fall back to browser files."""
    def fail_snapshot(ws_id):
        raise ValueError(f"Workspace '{ws_id}' not found on disk")

    with patch("backend.app.project_chat.get_workspace_snapshot", side_effect=fail_snapshot):
        res = client.post(
            "/agent/project-chat",
            json={
                "workspace_id": "ws-nonexistent",
                "files": [
                    {"path": "fallback.py", "content": "print('should never be used')"}
                ],
                "messages": [{"role": "user", "content": "Hello"}],
                "settings": {"api_key": "test-key"},
            },
        )

    assert res.status_code == 400
    detail = res.json()["detail"].lower()
    assert "failed to load workspace" in detail or "not found" in detail


def test_workspace_blank_id_rejected(client):
    """Blank or whitespace-only workspace_id must return 400 Bad Request."""
    res = client.post(
        "/agent/project-chat",
        json={
            "workspace_id": "   ",
            "messages": [{"role": "user", "content": "Hello"}],
            "settings": {"api_key": "test-key"},
        },
    )
    assert res.status_code == 400
    assert "invalid workspace" in res.json()["detail"].lower()


def test_workspace_snapshot_missing_files_rejected(client):
    """Corrupted snapshot without files array must return 400."""
    corrupted = {
        "workspace_id": "ws-corrupt",
        "name": "Corrupt",
        "path": "/tmp/corrupt",
        "files": None,
        "revision": "rev-0",
    }
    with patch("backend.app.project_chat.get_workspace_snapshot", return_value=corrupted):
        res = client.post(
            "/agent/project-chat",
            json={
                "workspace_id": "ws-corrupt",
                "messages": [{"role": "user", "content": "Hello"}],
                "settings": {"api_key": "test-key"},
            },
        )
    assert res.status_code == 400
    assert "invalid files" in res.json()["detail"].lower() or "missing" in res.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 3. Browser Payload Cannot Override Local Files
# ---------------------------------------------------------------------------


def test_browser_payload_cannot_override_local_files(client):
    """Browser request.files payload must be completely ignored in workspace mode."""
    workspace_snapshot = {
        "workspace_id": "ws-secure",
        "name": "SecureApp",
        "path": "/path/to/secure",
        "files": [
            {"path": "src/auth.py", "content": "SECRET_ALGO = 'argon2id'\n"}
        ],
        "skipped": [],
        "revision": "rev-1",
    }

    mock_llm = make_mock_response("Verified local auth code.")

    with patch("backend.app.project_chat.get_workspace_snapshot", return_value=workspace_snapshot):
        with patch("backend.app.project_chat.call_chat_completion", return_value=mock_llm) as mock_call:
            res = client.post(
                "/agent/project-chat",
                json={
                    "workspace_id": "ws-secure",
                    # Attacker attempts to overwrite src/auth.py or supply injected files
                    "files": [
                        {"path": "src/auth.py", "content": "SECRET_ALGO = 'insecure_override'"},
                        {"path": "injected.py", "content": "MALICIOUS = True"},
                    ],
                    "messages": [{"role": "user", "content": "Check auth algorithm"}],
                    "settings": {"api_key": "test-key"},
                },
            )

    assert res.status_code == 200
    llm_msgs = mock_call.call_args.kwargs["messages"]
    user_context = llm_msgs[1]["content"]

    assert "src/auth.py" in user_context
    assert "SECRET_ALGO = 'argon2id'" not in user_context  # File bodies are fetched on demand.
    assert "insecure_override" not in user_context
    assert "injected.py" not in user_context


# ---------------------------------------------------------------------------
# 4. Exclusions & Filters Preserved in Workspace Mode
# ---------------------------------------------------------------------------


def test_workspace_files_exclude_secrets_and_binaries(client):
    """Secrets, binary files, and excluded directories in workspace snapshot must be filtered."""
    workspace_snapshot = {
        "workspace_id": "ws-filters",
        "name": "FilteredApp",
        "path": "/path",
        "files": [
            {"path": "src/app.py", "content": "print('clean code')"},
            {"path": ".env", "content": "DB_PASSWORD=secret"},
            {"path": "id_rsa", "content": "SSH_KEY_DATA"},
            {"path": "node_modules/lib.js", "content": "console.log()"},
            {"path": "app.pyc", "content": "bytecode"},
        ],
        "skipped": [],
        "revision": "rev-filter",
    }

    mock_llm = make_mock_response("Filtered inspection.")

    with patch("backend.app.project_chat.get_workspace_snapshot", return_value=workspace_snapshot):
        with patch("backend.app.project_chat.call_chat_completion", return_value=mock_llm) as mock_call:
            res = client.post(
                "/agent/project-chat",
                json={
                    "workspace_id": "ws-filters",
                    "messages": [{"role": "user", "content": "Inspect files"}],
                    "settings": {"api_key": "test-key"},
                },
            )

    assert res.status_code == 200
    llm_msgs = mock_call.call_args.kwargs["messages"]
    user_context = llm_msgs[1]["content"]
    assert "src/app.py" in user_context
    assert ".env" not in user_context
    assert "id_rsa" not in user_context
    assert "node_modules" not in user_context
    assert "app.pyc" not in user_context


# ---------------------------------------------------------------------------
# 5. Snapshot Refreshing on EACH Tool Call
# ---------------------------------------------------------------------------


def test_snapshot_refreshes_on_tool_call(client):
    """When a snapshot tool is executed, it must refresh the backing snapshot dictionary from disk."""
    snap_v1 = {
        "workspace_id": "ws-live",
        "name": "LiveApp",
        "path": "/path",
        "files": [{"path": "counter.txt", "content": "count = 1\n"}],
        "skipped": [],
        "revision": "rev-1",
    }
    # Disk was updated after prompt was sent!
    snap_v2 = {
        "workspace_id": "ws-live",
        "name": "LiveApp",
        "path": "/path",
        "files": [{"path": "counter.txt", "content": "count = 2\n"}],
        "skipped": [],
        "revision": "rev-2",
    }

    round_0 = make_mock_tool_response([
        {
            "id": "tc_read_1",
            "type": "function",
            "function": {"name": "read_file", "arguments": json.dumps({"path": "counter.txt"})},
        }
    ])
    round_1 = make_mock_response("The updated count in counter.txt is 2.")

    # Call 1 (start of turn): snap_v1
    # Call 2 (on read_file tool call): snap_v2
    with patch("backend.app.project_chat.get_workspace_snapshot", side_effect=[snap_v1, snap_v2]) as mock_snap:
        with patch("backend.app.project_chat.call_chat_completion", side_effect=[round_0, round_1]):
            res = client.post(
                "/agent/project-chat",
                json={
                    "workspace_id": "ws-live",
                    "messages": [{"role": "user", "content": "What is the counter?"}],
                    "settings": {"api_key": "test-key"},
                },
            )

    assert res.status_code == 200
    data = res.json()
    assert "count in counter.txt is 2" in data["reply"]
    # Revision updated to rev-2
    assert data["workspace_revision"] == "rev-2"
    assert len(data["trace"]) == 1
    assert data["trace"][0]["tool"] == "read_file"
    assert data["trace"][0]["status"] == "success"

    # get_workspace_snapshot was called twice (turn start + tool refresh)
    assert mock_snap.call_count == 2


def test_snapshot_refreshes_on_each_batch_tool_call(client):
    """Multiple tool calls in a batch must each refresh the backing snapshot."""
    snap_v1 = {"workspace_id": "w", "name": "W", "path": "/p", "files": [{"path": "a.txt", "content": "A"}], "skipped": [], "revision": "r1"}
    snap_v2 = {"workspace_id": "w", "name": "W", "path": "/p", "files": [{"path": "a.txt", "content": "A"}, {"path": "b.txt", "content": "B"}], "skipped": [], "revision": "r2"}
    snap_v3 = {"workspace_id": "w", "name": "W", "path": "/p", "files": [{"path": "a.txt", "content": "A"}, {"path": "b.txt", "content": "B2"}], "skipped": [], "revision": "r3"}

    round_0 = make_mock_tool_response([
        {"id": "tc1", "type": "function", "function": {"name": "list_files", "arguments": "{}"}},
        {"id": "tc2", "type": "function", "function": {"name": "read_file", "arguments": json.dumps({"path": "b.txt"})}},
    ])
    round_1 = make_mock_response("Finished batch calls.")

    with patch("backend.app.project_chat.get_workspace_snapshot", side_effect=[snap_v1, snap_v2, snap_v3]) as mock_snap:
        with patch("backend.app.project_chat.call_chat_completion", side_effect=[round_0, round_1]):
            res = client.post(
                "/agent/project-chat",
                json={
                    "workspace_id": "w",
                    "messages": [{"role": "user", "content": "Inspect"}],
                    "settings": {"api_key": "test-key"},
                },
            )

    assert res.status_code == 200
    data = res.json()
    assert data["workspace_revision"] == "r3"
    assert len(data["trace"]) == 2
    # list_files (ran with snap_v2) listed both a.txt and b.txt
    assert "2 file(s)" in data["trace"][0]["detail"]
    assert mock_snap.call_count == 3


# ---------------------------------------------------------------------------
# 6. Snapshot Refreshing before delegate_tasks (Workers Immutable)
# ---------------------------------------------------------------------------


def test_snapshot_refreshes_before_delegate_tasks(client):
    """delegate_tasks must refresh the snapshot before spawning worker subagents."""
    snap_v1 = {
        "workspace_id": "ws-sub",
        "name": "SubApp",
        "path": "/path",
        "files": [{"path": "calc.py", "content": "def add(x, y): return x + y\n"}],
        "skipped": [],
        "revision": "rev-sub-1",
    }
    snap_v2 = {
        "workspace_id": "ws-sub",
        "name": "SubApp",
        "path": "/path",
        "files": [{"path": "calc.py", "content": "def add(x, y): return x + y + 1\n"}],
        "skipped": [],
        "revision": "rev-sub-2",
    }

    round_0 = make_mock_tool_response([
        {
            "id": "tc_delegate",
            "type": "function",
            "function": {
                "name": "delegate_tasks",
                "arguments": json.dumps({
                    "tasks": [
                        {"name": "calc_analyzer", "task": "Check add function in calc.py"}
                    ]
                }),
            },
        }
    ])
    round_1 = make_mock_response("The subagent analyzed calc.py.")
    worker_llm = make_mock_response("add returns x + y + 1.")

    with patch("backend.app.project_chat.get_workspace_snapshot", side_effect=[snap_v1, snap_v2]) as mock_snap:
        with patch("backend.app.project_chat.call_chat_completion", side_effect=[round_0, round_1]):
            with patch("backend.app.project_subagents.call_chat_completion", return_value=worker_llm):
                res = client.post(
                    "/agent/project-chat",
                    json={
                        "workspace_id": "ws-sub",
                        "messages": [{"role": "user", "content": "Analyze calc.py"}],
                        "settings": {"api_key": "test-key"},
                    },
                )

    assert res.status_code == 200
    data = res.json()
    assert len(data["subagents"]) == 1
    assert data["subagents"][0]["name"] == "calc_analyzer"
    assert data["subagents"][0]["status"] == "success"
    assert "x + y + 1" in data["subagents"][0]["reply"]
    assert data["workspace_revision"] == "rev-sub-2"
    assert mock_snap.call_count == 2


# ---------------------------------------------------------------------------
# 7. request_command Execution & No Subprocess Verification
# ---------------------------------------------------------------------------


def test_request_command_success(client):
    """request_command tool stages command request for user approval without executing."""
    snap = {
        "workspace_id": "ws-cmd",
        "name": "CmdApp",
        "path": "/path",
        "files": [{"path": "package.json", "content": "{}"}],
        "skipped": [],
        "revision": "rev-cmd",
    }

    round_0 = make_mock_tool_response([
        {
            "id": "tc_cmd_1",
            "type": "function",
            "function": {
                "name": "request_command",
                "arguments": json.dumps({"argv": ["npm", "test"]}),
            },
        }
    ])
    round_1 = make_mock_response("I have requested permission to run 'npm test'. Please review and approve.")

    with patch("backend.app.project_chat.get_workspace_snapshot", return_value=snap):
        with patch("subprocess.run") as mock_proc:
            with patch("subprocess.Popen") as mock_popen:
                with patch("backend.app.project_chat.call_chat_completion", side_effect=[round_0, round_1]) as mock_call:
                    res = client.post(
                        "/agent/project-chat",
                        json={
                            "workspace_id": "ws-cmd",
                            "messages": [{"role": "user", "content": "Run tests"}],
                            "settings": {"api_key": "test-key"},
                        },
                    )

    assert res.status_code == 200
    data = res.json()
    assert "permission to run 'npm test'" in data["reply"]
    # Verify command_requests top-level array
    assert len(data["command_requests"]) == 1
    assert data["command_requests"][0]["argv"] == ["npm", "test"]
    assert "npm test" in data["command_requests"][0]["summary"]

    # Verify trace success
    assert len(data["trace"]) == 1
    trace_item = data["trace"][0]
    assert trace_item["tool"] == "request_command"
    assert trace_item["status"] == "success"
    assert "approval: npm test" in trace_item["detail"].lower()

    # CRITICAL: Verify NO process was spawned
    mock_proc.assert_not_called()
    mock_popen.assert_not_called()

    # Verify tool response fed to LLM shows approval_requested, executed: False
    second_round_msgs = mock_call.call_args_list[1].kwargs["messages"]
    tool_msg = next((m for m in second_round_msgs if m.get("role") == "tool"), None)
    assert tool_msg is not None
    assert tool_msg["tool_call_id"] == "tc_cmd_1"
    tool_out = json.loads(tool_msg["content"])
    assert tool_out["status"] == "approval_requested"
    assert tool_out["executed"] is False
    assert tool_out["argv"] == ["npm", "test"]


def test_prepare_command_alias_supported(client):
    """The alias name prepare_command must work identically to request_command."""
    snap = {"workspace_id": "ws-alias", "name": "A", "path": "/p", "files": [], "skipped": [], "revision": "r"}

    round_0 = make_mock_tool_response([
        {
            "id": "tc_alias",
            "type": "function",
            "function": {
                "name": "prepare_command",
                "arguments": json.dumps({"argv": ["pytest", "tests/"]}),
            },
        }
    ])
    round_1 = make_mock_response("Approval requested.")

    with patch("backend.app.project_chat.get_workspace_snapshot", return_value=snap):
        with patch("backend.app.project_chat.call_chat_completion", side_effect=[round_0, round_1]):
            res = client.post(
                "/agent/project-chat",
                json={
                    "workspace_id": "ws-alias",
                    "messages": [{"role": "user", "content": "Test"}],
                    "settings": {"api_key": "test-key"},
                },
            )

    assert res.status_code == 200
    data = res.json()
    assert len(data["command_requests"]) == 1
    assert data["command_requests"][0]["argv"] == ["pytest", "tests/"]
    assert data["trace"][0]["status"] == "success"


# ---------------------------------------------------------------------------
# 8. request_command Validation & Security Rejections
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_argv,expected_msg",
    [
        ([], "between 1 and 20"),  # 0 arguments
        (["arg"] * 21, "between 1 and 20"),  # 21 arguments (> 20)
        ([123], "must be a string"),  # non-string
        (["a" * 2001], "exceeds maximum allowed length of 2000"),  # > 2000 chars
        (["npm\x00test"], "forbidden NUL character"),  # NUL byte
        (["FOO=bar", "npm", "test"], "environment variable assignment"),  # env key assignment
        (["NODE_ENV=production", "npm", "start"], "environment variable assignment"),  # env key assignment
        (["   "], "cannot be blank"),  # blank executable
    ],
)
def test_request_command_argv_validation(client, bad_argv, expected_msg):
    snap = {"workspace_id": "ws-v", "name": "V", "path": "/p", "files": [], "skipped": [], "revision": "r"}

    round_0 = make_mock_tool_response([
        {
            "id": "tc_bad",
            "type": "function",
            "function": {
                "name": "request_command",
                "arguments": json.dumps({"argv": bad_argv}),
            },
        }
    ])
    round_1 = make_mock_response("Command was invalid.")

    with patch("backend.app.project_chat.get_workspace_snapshot", return_value=snap):
        with patch("backend.app.project_chat.call_chat_completion", side_effect=[round_0, round_1]) as mock_call:
            res = client.post(
                "/agent/project-chat",
                json={
                    "workspace_id": "ws-v",
                    "messages": [{"role": "user", "content": "Run"}],
                    "settings": {"api_key": "test-key"},
                },
            )

    assert res.status_code == 200
    data = res.json()
    # No command request was staged
    assert data["command_requests"] == []
    # Trace reports error with expected detail
    assert len(data["trace"]) == 1
    assert data["trace"][0]["status"] == "error"
    assert expected_msg.lower() in data["trace"][0]["detail"].lower()

    # Tool output returned to model has error
    second_msgs = mock_call.call_args_list[1].kwargs["messages"]
    tool_msg = next((m for m in second_msgs if m.get("role") == "tool"), None)
    tool_out = json.loads(tool_msg["content"])
    assert "error" in tool_out


def test_request_command_rejects_env_dictionary(client):
    """request_command rejects when 'env' dictionary is provided."""
    snap = {"workspace_id": "ws-env", "name": "E", "path": "/p", "files": [], "skipped": [], "revision": "r"}

    round_0 = make_mock_tool_response([
        {
            "id": "tc_env",
            "type": "function",
            "function": {
                "name": "request_command",
                "arguments": json.dumps({
                    "argv": ["npm", "test"],
                    "env": {"API_KEY": "secret"},
                }),
            },
        }
    ])
    round_1 = make_mock_response("Env not allowed.")

    with patch("backend.app.project_chat.get_workspace_snapshot", return_value=snap):
        with patch("backend.app.project_chat.call_chat_completion", side_effect=[round_0, round_1]):
            res = client.post(
                "/agent/project-chat",
                json={
                    "workspace_id": "ws-env",
                    "messages": [{"role": "user", "content": "Run with env"}],
                    "settings": {"api_key": "test-key"},
                },
            )

    assert res.status_code == 200
    data = res.json()
    assert data["command_requests"] == []
    assert data["trace"][0]["status"] == "error"
    assert "environment variables" in data["trace"][0]["detail"].lower()


def test_request_command_not_allowed_in_plain_snapshot_mode(client):
    """In non-workspace mode, request_command is not in tool definitions and is rejected if invoked."""
    round_0 = make_mock_tool_response([
        {
            "id": "tc_no_ws",
            "type": "function",
            "function": {
                "name": "request_command",
                "arguments": json.dumps({"argv": ["npm", "test"]}),
            },
        }
    ])
    round_1 = make_mock_response("Cannot run command in plain snapshot mode.")

    with patch("backend.app.project_chat.call_chat_completion", side_effect=[round_0, round_1]) as mock_call:
        res = client.post(
            "/agent/project-chat",
            json={
                "files": [{"path": "main.py", "content": "x = 1"}],
                "messages": [{"role": "user", "content": "Run test"}],
                "settings": {"api_key": "test-key"},
            },
        )

    assert res.status_code == 200
    data = res.json()
    # In round 0, coordinator_tools did NOT contain request_command
    tool_names = [t["function"]["name"] for t in mock_call.call_args_list[0].kwargs["tools"]]
    assert "request_command" not in tool_names
    assert data["command_requests"] == []
    assert data["workspace_revision"] is None
    assert len(data["trace"]) == 1
    assert data["trace"][0]["status"] == "error"
    assert "live workspace mode" in data["trace"][0]["detail"].lower()


# ---------------------------------------------------------------------------
# 9. propose_changes in Workspace Mode Remains Staged
# ---------------------------------------------------------------------------


def test_workspace_propose_changes_staged_not_applied(client):
    """propose_changes in workspace mode creates a proposal but does NOT edit disk or snapshot directly."""
    snap = {
        "workspace_id": "ws-prop",
        "name": "PropApp",
        "path": "/path",
        "files": [{"path": "src/fix.py", "content": "def fix(): pass\n"}],
        "skipped": [],
        "revision": "rev-p1",
    }

    round_0 = make_mock_tool_response([
        {
            "id": "tc_prop",
            "type": "function",
            "function": {
                "name": "propose_changes",
                "arguments": json.dumps({
                    "changes": [
                        {"path": "src/fix.py", "operation": "replace", "content": "def fix(): return True\n"}
                    ]
                }),
            },
        }
    ])
    round_1 = make_mock_response("I have proposed changes for src/fix.py.")

    with patch("backend.app.project_chat.get_workspace_snapshot", return_value=snap):
        with patch("backend.app.project_chat.call_chat_completion", side_effect=[round_0, round_1]):
            res = client.post(
                "/agent/project-chat",
                json={
                    "workspace_id": "ws-prop",
                    "messages": [{"role": "user", "content": "Propose fix"}],
                    "settings": {"api_key": "test-key"},
                },
            )

    assert res.status_code == 200
    data = res.json()
    assert len(data["proposals"]) == 1
    assert data["proposals"][0]["changes"][0]["path"] == "src/fix.py"
    assert "+def fix(): return True" in data["proposals"][0]["diff"]
    # proposal exists, but no automatic disk write occurred


# ---------------------------------------------------------------------------
# 10. Lazy Module Import Resilience
# ---------------------------------------------------------------------------


def test_workspace_module_missing_returns_500(client, monkeypatch):
    """If the .workspaces module is not available when workspace_id is provided, returns 500 error cleanly."""
    # Ensure get_workspace_snapshot is None and .workspaces import raises ImportError
    monkeypatch.setattr("backend.app.project_chat.get_workspace_snapshot", None)
    
    # Hide any potential workspaces module from sys.modules
    with patch.dict(sys.modules, {"backend.app.workspaces": None}):
        res = client.post(
            "/agent/project-chat",
            json={
                "workspace_id": "ws-any",
                "messages": [{"role": "user", "content": "Hello"}],
                "settings": {"api_key": "test-key"},
            },
        )
    assert res.status_code == 500
    assert "workspaces module is not available" in res.json()["detail"].lower()


def test_non_workspace_request_works_even_if_workspaces_missing(client, monkeypatch):
    """Plain snapshot requests must succeed even if .workspaces module is completely absent."""
    monkeypatch.setattr("backend.app.project_chat.get_workspace_snapshot", None)
    mock_llm = make_mock_response("Regular response.")

    with patch.dict(sys.modules, {"backend.app.workspaces": None}):
        with patch("backend.app.project_chat.call_chat_completion", return_value=mock_llm):
            res = client.post(
                "/agent/project-chat",
                json={
                    "files": [{"path": "test.py", "content": "x = 1"}],
                    "messages": [{"role": "user", "content": "Hello"}],
                    "settings": {"api_key": "test-key"},
                },
            )
    assert res.status_code == 200
    data = res.json()
    assert data["reply"] == "Regular response."
    assert data["workspace_revision"] is None
    assert data["command_requests"] == []
