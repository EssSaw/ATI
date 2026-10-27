import json
from unittest.mock import patch
import pytest
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from backend.app.project_chat import router as project_chat_router
from backend.app.project_tools import (
    PROJECT_TOOL_DEFINITIONS,
    ProjectChatTraceItem,
    Proposal,
    detect_frameworks,
    execute_project_tool,
    handle_inspect_project,
    handle_list_files,
    handle_propose_changes,
    handle_read_file,
    handle_search_files,
)
from backend.app.sessions import session_store

# Local app instance for testing
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


# ---------------------------------------------------------------------------
# Toolkit Unit Tests: list_files
# ---------------------------------------------------------------------------


def test_list_files_all():
    snapshot = {
        "src/main.py": "print('hello')",
        "src/utils.py": "def add(a, b): return a + b",
        "README.md": "# Readme",
    }
    out, status, detail = handle_list_files(snapshot, {})
    assert status == "success"
    assert out["count"] == 3
    assert out["files"] == ["README.md", "src/main.py", "src/utils.py"]
    assert "3 file(s)" in detail


def test_list_files_with_glob():
    snapshot = {
        "src/main.py": "print('hello')",
        "src/utils.py": "def add(a, b): return a + b",
        "README.md": "# Readme",
        "package.json": "{}",
    }
    out, status, detail = handle_list_files(snapshot, {"glob": "*.py"})
    assert status == "success"
    assert out["count"] == 2
    assert out["files"] == ["src/main.py", "src/utils.py"]
    assert out["glob"] == "*.py"


def test_list_files_invalid_glob_argument():
    snapshot = {"src/main.py": "x = 1"}
    out, status, detail = handle_list_files(snapshot, {"glob": 12345})
    assert status == "error"
    assert "must be a string" in out["error"]


def test_list_files_empty_snapshot():
    snapshot = {}
    out, status, detail = handle_list_files(snapshot, {})
    assert status == "success"
    assert out["count"] == 0
    assert out["files"] == []


# ---------------------------------------------------------------------------
# Toolkit Unit Tests: read_file
# ---------------------------------------------------------------------------


def test_read_file_full():
    content = "line1\nline2\nline3\n"
    snapshot = {"src/test.txt": content}
    out, status, detail = handle_read_file(snapshot, {"path": "src/test.txt"})
    assert status == "success"
    assert out["content"] == content
    assert out["total_lines"] == 3
    assert out["start_line"] == 1
    assert out["end_line"] == 3


def test_read_file_bounded_lines():
    content = "line 1\nline 2\nline 3\nline 4\nline 5\n"
    snapshot = {"src/test.txt": content}
    out, status, detail = handle_read_file(snapshot, {"path": "src/test.txt", "start_line": 2, "end_line": 4})
    assert status == "success"
    assert out["content"] == "line 2\nline 3\nline 4\n"
    assert out["start_line"] == 2
    assert out["end_line"] == 4
    assert out["total_lines"] == 5


def test_read_file_clamping():
    content = "a\nb\nc\n"
    snapshot = {"src/test.txt": content}
    out, status, detail = handle_read_file(snapshot, {"path": "src/test.txt", "start_line": -5, "end_line": 100})
    assert status == "success"
    assert out["start_line"] == 1
    assert out["end_line"] == 3
    assert out["content"] == content


def test_read_file_empty_file():
    snapshot = {"empty.txt": ""}
    out, status, detail = handle_read_file(snapshot, {"path": "empty.txt"})
    assert status == "success"
    assert out["total_lines"] == 0
    assert out["content"] == ""


def test_read_file_line_range_error():
    content = "a\nb\nc\n"
    snapshot = {"src/test.txt": content}
    out, status, detail = handle_read_file(snapshot, {"path": "src/test.txt", "start_line": 4, "end_line": 2})
    assert status == "error"
    assert "exceeds total lines" in out["error"] or "cannot be greater than" in out["error"]


def test_read_file_non_existent():
    snapshot = {"src/main.py": "x = 1"}
    out, status, detail = handle_read_file(snapshot, {"path": "src/missing.py"})
    assert status == "error"
    assert "not found" in out["error"].lower()


def test_read_file_unsafe_path():
    snapshot = {"src/main.py": "x = 1"}
    out, status, detail = handle_read_file(snapshot, {"path": "../etc/passwd"})
    assert status == "error"
    assert "unsafe" in out["error"].lower()


def test_read_file_excluded_path():
    snapshot = {"src/main.py": "x = 1"}
    out, status, detail = handle_read_file(snapshot, {"path": ".env"})
    assert status == "error"
    assert "excluded" in out["error"].lower()


def test_read_file_invalid_argument_types():
    snapshot = {"src/main.py": "x = 1"}
    out, status, detail = handle_read_file(snapshot, {"path": "src/main.py", "start_line": "not_an_int"})
    assert status == "error"
    assert "integer" in out["error"].lower()


# ---------------------------------------------------------------------------
# Toolkit Unit Tests: search_files
# ---------------------------------------------------------------------------


def test_search_files_literal_match():
    snapshot = {
        "src/a.py": "def calculate_sum(x, y):\n    return x + y\n",
        "src/b.py": "from a import calculate_sum\nresult = calculate_sum(1, 2)\n",
        "README.md": "# No match here",
    }
    out, status, detail = handle_search_files(snapshot, {"query": "calculate_sum"})
    assert status == "success"
    assert out["count"] == 3
    paths = [m["path"] for m in out["matches"]]
    assert "src/a.py" in paths
    assert "src/b.py" in paths
    assert "README.md" not in paths


def test_search_files_literal_not_regex():
    # Query contains regex meta-characters like ".*" or "[0-9]"
    snapshot = {
        "src/a.py": "pattern = r'.*'\n",
        "src/b.py": "print('anything')\n",
    }
    out, status, detail = handle_search_files(snapshot, {"query": ".*"})
    assert status == "success"
    assert out["count"] == 1
    assert out["matches"][0]["path"] == "src/a.py"


def test_search_files_with_glob():
    snapshot = {
        "src/a.py": "TARGET = 42\n",
        "docs/a.md": "TARGET is mentioned here\n",
    }
    out, status, detail = handle_search_files(snapshot, {"query": "TARGET", "glob": "*.py"})
    assert status == "success"
    assert out["count"] == 1
    assert out["matches"][0]["path"] == "src/a.py"


def test_search_files_empty_query():
    snapshot = {"src/a.py": "content"}
    out, status, detail = handle_search_files(snapshot, {"query": ""})
    assert status == "error"
    assert "empty" in out["error"].lower()


def test_search_files_max_results_cap():
    # 60 matches
    content = "\n".join(f"MATCH_ME line {i}" for i in range(60))
    snapshot = {"src/big.txt": content}
    out, status, detail = handle_search_files(snapshot, {"query": "MATCH_ME", "max_results": 100})
    assert status == "success"
    assert out["count"] == 50  # Hard capped at 50
    assert out["capped"] is True


# ---------------------------------------------------------------------------
# Toolkit Unit Tests: inspect_project & framework detection
# ---------------------------------------------------------------------------


def test_inspect_project_js_manifest():
    pkg_json = json.dumps({
        "name": "my-web-app",
        "scripts": {"dev": "vite", "build": "vite build", "test": "vitest"},
        "dependencies": {"react": "^18.2.0", "react-dom": "^18.2.0"},
        "devDependencies": {"vite": "^4.0.0"},
    })
    snapshot = {
        "package.json": pkg_json,
        "src/App.jsx": "export default function App() { return <h1>Hello</h1>; }",
    }
    out, status, detail = handle_inspect_project(snapshot, {})
    assert status == "success"
    manifests = out["manifests"]
    assert "package.json" in manifests
    assert manifests["package.json"]["scripts"]["build"] == "vite build"
    assert "React" in out["detected_frameworks"]
    assert "package.json:build" in out["declared_capabilities"]["scripts"]
    assert "not installed" in out["declared_capabilities"]["note"].lower()


def test_inspect_project_python_manifest():
    snapshot = {
        "requirements.txt": "fastapi>=0.100.0\nuvicorn[standard]\npydantic>=2.0\n# comment\n",
        "main.py": "from fastapi import FastAPI\napp = FastAPI()\n",
    }
    out, status, detail = handle_inspect_project(snapshot, {})
    assert status == "success"
    assert "FastAPI" in out["detected_frameworks"]
    assert "requirements.txt" in out["manifests"]
    assert "fastapi" in out["manifests"]["requirements.txt"]["dependencies"]


def test_framework_detection_all_targets():
    # Test all frameworks required by spec:
    # React, Vue, Svelte, Next, Nuxt, Angular, Express, Nest, FastAPI, Django, Flask,
    # Rails, Laravel, Spring, .NET, Flutter, Electron, Tauri
    snapshot = {
        "package.json": json.dumps({
            "dependencies": {
                "react": "18",
                "vue": "3",
                "svelte": "4",
                "next": "14",
                "nuxt": "3",
                "@angular/core": "17",
                "express": "4",
                "@nestjs/core": "10",
                "electron": "28",
                "@tauri-apps/api": "1",
            }
        }),
        "requirements.txt": "fastapi\ndjango\nflask\n",
        "Gemfile": "gem 'rails'\n",
        "composer.json": json.dumps({"require": {"laravel/framework": "^10.0"}}),
        "pom.xml": "<artifactId>spring-boot-starter-web</artifactId>",
        "App.csproj": "<TargetFramework>net8.0</TargetFramework>",
        "pubspec.yaml": "dependencies:\n  flutter:\n    sdk: flutter\n",
    }
    out, status, detail = handle_inspect_project(snapshot, {})
    detected = out["detected_frameworks"]

    expected_frameworks = [
        ".NET",
        "Angular",
        "Django",
        "Electron",
        "Express",
        "FastAPI",
        "Flask",
        "Flutter",
        "Laravel",
        "Nest",
        "Next",
        "Nuxt",
        "Rails",
        "React",
        "Spring",
        "Svelte",
        "Tauri",
        "Vue",
    ]
    for fw in expected_frameworks:
        assert fw in detected, f"Expected framework {fw} to be detected"


def test_inspect_project_empty_snapshot():
    out, status, detail = handle_inspect_project({}, {})
    assert status == "success"
    assert out["manifests"] == {}
    assert out["detected_frameworks"] == []
    assert out["total_files"] == 0


# ---------------------------------------------------------------------------
# Toolkit Unit Tests: propose_changes
# ---------------------------------------------------------------------------


def test_propose_changes_create():
    snapshot = {"src/existing.py": "x = 1\n"}
    changes = [
        {"path": "src/new_file.py", "operation": "create", "content": "def hello():\n    return 'world'\n"}
    ]
    out, status, detail, proposal = handle_propose_changes(snapshot, {"changes": changes})
    assert status == "success"
    assert out["status"] == "proposal_generated"
    assert out["applied"] is False
    assert proposal is not None
    assert proposal["changes"][0]["operation"] == "create"
    assert "--- /dev/null" in proposal["diff"]
    assert "+++ b/src/new_file.py" in proposal["diff"]
    assert "+def hello():" in proposal["diff"]
    # Verify snapshot was NOT modified
    assert "src/new_file.py" not in snapshot


def test_propose_changes_replace():
    snapshot = {"src/calc.py": "def add(a, b):\n    return a - b\n"}
    changes = [
        {"path": "src/calc.py", "operation": "replace", "content": "def add(a, b):\n    return a + b\n"}
    ]
    out, status, detail, proposal = handle_propose_changes(snapshot, {"changes": changes})
    assert status == "success"
    assert proposal is not None
    assert "--- a/src/calc.py" in proposal["diff"]
    assert "+++ b/src/calc.py" in proposal["diff"]
    assert "-    return a - b" in proposal["diff"]
    assert "+    return a + b" in proposal["diff"]
    # Verify snapshot was NOT modified
    assert snapshot["src/calc.py"] == "def add(a, b):\n    return a - b\n"


def test_propose_changes_delete():
    snapshot = {"src/deprecated.py": "# old code\n"}
    changes = [
        {"path": "src/deprecated.py", "operation": "delete"}
    ]
    out, status, detail, proposal = handle_propose_changes(snapshot, {"changes": changes})
    assert status == "success"
    assert proposal is not None
    assert "--- a/src/deprecated.py" in proposal["diff"]
    assert "+++ /dev/null" in proposal["diff"]
    assert "-# old code" in proposal["diff"]
    # Verify snapshot was NOT modified
    assert "src/deprecated.py" in snapshot


def test_propose_changes_unicode_and_newlines():
    snapshot = {"src/i18n.py": "greeting = 'Hello'\n"}
    new_code = "greeting = 'Xin chào thế giới 🚀'\nfarewell = 'Tạm biệt!\n'"
    changes = [
        {"path": "src/i18n.py", "operation": "replace", "content": new_code}
    ]
    out, status, detail, proposal = handle_propose_changes(snapshot, {"changes": changes})
    assert status == "success"
    assert proposal is not None
    assert "Xin chào thế giới 🚀" in proposal["diff"]
    assert proposal["changes"][0]["content"] == new_code


def test_propose_changes_reject_create_existing():
    snapshot = {"src/main.py": "x = 1\n"}
    changes = [{"path": "src/main.py", "operation": "create", "content": "x = 2\n"}]
    out, status, detail, proposal = handle_propose_changes(snapshot, {"changes": changes})
    assert status == "error"
    assert proposal is None
    assert "already exists" in out["error"].lower()


def test_propose_changes_reject_replace_nonexistent():
    snapshot = {"src/main.py": "x = 1\n"}
    changes = [{"path": "src/missing.py", "operation": "replace", "content": "x = 2\n"}]
    out, status, detail, proposal = handle_propose_changes(snapshot, {"changes": changes})
    assert status == "error"
    assert proposal is None
    assert "does not exist" in out["error"].lower()


def test_propose_changes_reject_delete_nonexistent():
    snapshot = {"src/main.py": "x = 1\n"}
    changes = [{"path": "src/missing.py", "operation": "delete"}]
    out, status, detail, proposal = handle_propose_changes(snapshot, {"changes": changes})
    assert status == "error"
    assert proposal is None
    assert "does not exist" in out["error"].lower()


def test_propose_changes_reject_duplicate_paths():
    snapshot = {"src/a.py": "a = 1\n"}
    changes = [
        {"path": "src/a.py", "operation": "replace", "content": "a = 2\n"},
        {"path": "src/a.py", "operation": "replace", "content": "a = 3\n"},
    ]
    out, status, detail, proposal = handle_propose_changes(snapshot, {"changes": changes})
    assert status == "error"
    assert proposal is None
    assert "duplicate" in out["error"].lower()


def test_propose_changes_reject_unsafe_path():
    snapshot = {}
    changes = [{"path": "../outside.py", "operation": "create", "content": "pass\n"}]
    out, status, detail, proposal = handle_propose_changes(snapshot, {"changes": changes})
    assert status == "error"
    assert proposal is None
    assert "unsafe" in out["error"].lower()


def test_propose_changes_reject_excluded_path():
    snapshot = {}
    changes = [{"path": ".env", "operation": "create", "content": "SECRET=123\n"}]
    out, status, detail, proposal = handle_propose_changes(snapshot, {"changes": changes})
    assert status == "error"
    assert proposal is None
    assert "excluded" in out["error"].lower()


def test_propose_changes_reject_more_than_20_changes():
    snapshot = {}
    changes = [{"path": f"src/file_{i}.py", "operation": "create", "content": "pass\n"} for i in range(21)]
    out, status, detail, proposal = handle_propose_changes(snapshot, {"changes": changes})
    assert status == "error"
    assert proposal is None
    assert "20" in out["error"]


def test_propose_changes_reject_per_file_content_limit():
    snapshot = {}
    changes = [{"path": "src/large.py", "operation": "create", "content": "x" * 30001}]
    out, status, detail, proposal = handle_propose_changes(snapshot, {"changes": changes})
    assert status == "error"
    assert proposal is None
    assert "30,000" in out["error"]


def test_propose_changes_reject_total_content_limit():
    snapshot = {}
    # 7 changes of 29,000 chars = 203,000 chars (> 200,000)
    changes = [{"path": f"src/f_{i}.py", "operation": "create", "content": "x" * 29000} for i in range(7)]
    out, status, detail, proposal = handle_propose_changes(snapshot, {"changes": changes})
    assert status == "error"
    assert proposal is None
    assert "200,000" in out["error"]


def test_propose_changes_reject_binary_chars():
    snapshot = {}
    changes = [{"path": "src/bin.py", "operation": "create", "content": "val = '\x00\x01'"}]
    out, status, detail, proposal = handle_propose_changes(snapshot, {"changes": changes})
    assert status == "error"
    assert proposal is None
    assert "binary" in out["error"].lower() or "control" in out["error"].lower()


# ---------------------------------------------------------------------------
# Toolkit Unit Tests: execute_project_tool
# ---------------------------------------------------------------------------


def test_execute_unknown_tool():
    snapshot = {"src/main.py": "x = 1"}
    out_str, trace, prop = execute_project_tool("unknown_tool", "{}", snapshot)
    assert trace.status == "error"
    assert "unknown" in trace.detail.lower()
    data = json.loads(out_str)
    assert "error" in data


def test_execute_malformed_json_arguments():
    snapshot = {"src/main.py": "x = 1"}
    out_str, trace, prop = execute_project_tool("read_file", "{bad_json", snapshot)
    assert trace.status == "error"
    assert "invalid json" in trace.detail.lower()
    data = json.loads(out_str)
    assert "error" in data


# ---------------------------------------------------------------------------
# Integration Tests: /agent/project-chat Multi-turn Tool Calling Loop
# ---------------------------------------------------------------------------


def test_project_chat_single_tool_search_and_reply(client):
    """Model calls search_files, gets results, then provides a final reply."""
    snapshot_files = [
        {"path": "src/main.py", "content": "def calculate_sum(a, b):\n    return a + b\n"}
    ]

    # Turn 1: Model calls search_files
    response_round_0 = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_search_1",
                            "type": "function",
                            "function": {
                                "name": "search_files",
                                "arguments": json.dumps({"query": "calculate_sum"}),
                            },
                        }
                    ],
                }
            }
        ]
    }

    # Turn 2: Model returns final assistant text
    response_round_1 = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "I found calculate_sum at line 1 of src/main.py.",
                }
            }
        ]
    }

    with patch("backend.app.project_chat.call_chat_completion", side_effect=[response_round_0, response_round_1]) as mock_call:
        res = client.post(
            "/agent/project-chat",
            json={
                "project_name": "DemoApp",
                "files": snapshot_files,
                "messages": [{"role": "user", "content": "Where is calculate_sum defined?"}],
                "settings": {"api_key": "test-key"},
            },
        )

    assert res.status_code == 200
    data = res.json()
    assert "calculate_sum" in data["reply"]
    assert len(data["trace"]) == 1
    trace_item = data["trace"][0]
    assert trace_item["tool"] == "search_files"
    assert trace_item["status"] == "success"
    assert "calculate_sum" in trace_item["detail"]
    assert data["proposals"] == []

    # Verify call_chat_completion was invoked twice
    assert mock_call.call_count == 2
    # Check messages in round 1 contain tool output
    second_call_messages = mock_call.call_args_list[1].kwargs["messages"]
    tool_msg = next((m for m in second_call_messages if m.get("role") == "tool"), None)
    assert tool_msg is not None
    assert tool_msg["tool_call_id"] == "call_search_1"
    tool_output = json.loads(tool_msg["content"])
    assert tool_output["count"] == 1
    assert tool_output["matches"][0]["path"] == "src/main.py"


def test_project_chat_propose_changes_returned_in_response(client):
    """Model calls propose_changes and the structured proposal appears in API response."""
    snapshot_files = [
        {"path": "src/math.py", "content": "def multiply(a, b):\n    return a * b\n"}
    ]

    response_round_0 = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_prop_1",
                            "type": "function",
                            "function": {
                                "name": "propose_changes",
                                "arguments": json.dumps({
                                    "changes": [
                                        {
                                            "path": "src/math.py",
                                            "operation": "replace",
                                            "content": "def multiply(a, b):\n    \"\"\"Multiply two numbers.\"\"\"\n    return a * b\n",
                                        }
                                    ]
                                }),
                            },
                        }
                    ],
                }
            }
        ]
    }

    response_round_1 = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "I have created a proposal to add docstrings to src/math.py.",
                }
            }
        ]
    }

    with patch("backend.app.project_chat.call_chat_completion", side_effect=[response_round_0, response_round_1]):
        res = client.post(
            "/agent/project-chat",
            json={
                "project_name": "MathProject",
                "files": snapshot_files,
                "messages": [{"role": "user", "content": "Add docstrings to multiply"}],
                "settings": {"api_key": "test-key"},
            },
        )

    assert res.status_code == 200
    data = res.json()
    assert "proposal" in data["reply"].lower()
    assert len(data["trace"]) == 1
    assert data["trace"][0]["tool"] == "propose_changes"
    assert data["trace"][0]["status"] == "success"

    assert len(data["proposals"]) == 1
    proposal = data["proposals"][0]
    assert proposal["id"].startswith("prop_")
    assert "src/math.py" in proposal["diff"]
    assert "+    \"\"\"Multiply two numbers.\"\"\"" in proposal["diff"]
    assert proposal["changes"][0]["path"] == "src/math.py"
    assert proposal["changes"][0]["operation"] == "replace"


def test_project_chat_batch_multiple_tools(client):
    """Model calls list_files and inspect_project in a single batch turn."""
    snapshot_files = [
        {"path": "package.json", "content": json.dumps({"dependencies": {"react": "18"}})},
        {"path": "src/index.js", "content": "console.log('init');"},
    ]

    response_round_0 = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "list_files", "arguments": "{}"},
                        },
                        {
                            "id": "c2",
                            "type": "function",
                            "function": {"name": "inspect_project", "arguments": "{}"},
                        },
                    ],
                }
            }
        ]
    }

    response_round_1 = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "This is a React project with 2 files.",
                }
            }
        ]
    }

    with patch("backend.app.project_chat.call_chat_completion", side_effect=[response_round_0, response_round_1]):
        res = client.post(
            "/agent/project-chat",
            json={
                "project_name": "ReactApp",
                "files": snapshot_files,
                "messages": [{"role": "user", "content": "Analyze my project"}],
                "settings": {"api_key": "test-key"},
            },
        )

    assert res.status_code == 200
    data = res.json()
    assert len(data["trace"]) == 2
    assert data["trace"][0]["tool"] == "list_files"
    assert data["trace"][1]["tool"] == "inspect_project"
    assert "React" in data["reply"]


def test_project_chat_reject_exceeding_batch_limit(client):
    """Model returning > 8 tool calls in a single turn triggers 502."""
    tool_calls = [
        {
            "id": f"call_{i}",
            "type": "function",
            "function": {"name": "list_files", "arguments": "{}"},
        }
        for i in range(9)  # 9 > MAX_PROJECT_CHAT_BATCH_CALLS (8)
    ]
    response = {
        "choices": [{"message": {"role": "assistant", "tool_calls": tool_calls}}]
    }

    with patch("backend.app.project_chat.call_chat_completion", return_value=response):
        res = client.post(
            "/agent/project-chat",
            json={
                "project_name": "TestApp",
                "files": [{"path": "main.py", "content": "x = 1"}],
                "messages": [{"role": "user", "content": "Hi"}],
                "settings": {"api_key": "test-key"},
            },
        )

    assert res.status_code == 502
    assert "batch limit" in res.json()["detail"].lower()


def test_project_chat_reject_exceeding_total_call_limit(client):
    """Model making tool calls that sum to > 24 total calls triggers 502."""
    # 3 rounds of 8 calls = 24 calls, then round 4 requests 1 call = 25 > 24
    batch_8 = [
        {"id": f"c_{i}", "type": "function", "function": {"name": "list_files", "arguments": "{}"}}
        for i in range(8)
    ]
    resp_8 = {"choices": [{"message": {"role": "assistant", "tool_calls": batch_8}}]}
    resp_1 = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {"id": "c_25", "type": "function", "function": {"name": "list_files", "arguments": "{}"}}
                    ],
                }
            }
        ]
    }

    with patch("backend.app.project_chat.call_chat_completion", side_effect=[resp_8, resp_8, resp_8, resp_1]):
        res = client.post(
            "/agent/project-chat",
            json={
                "project_name": "TestApp",
                "files": [{"path": "main.py", "content": "x = 1"}],
                "messages": [{"role": "user", "content": "Hi"}],
                "settings": {"api_key": "test-key"},
            },
        )

    assert res.status_code == 502
    assert "maximum allowed tool calls" in res.json()["detail"].lower()


def test_project_chat_reject_exceeding_round_limit(client):
    """Model that keeps calling tools for 8 rounds without a final answer triggers 502."""
    tool_call = [
        {"id": "call_1", "type": "function", "function": {"name": "list_files", "arguments": "{}"}}
    ]
    resp = {"choices": [{"message": {"role": "assistant", "tool_calls": tool_call}}]}

    # 8 responses all calling tools
    with patch("backend.app.project_chat.call_chat_completion", side_effect=[resp] * 8):
        res = client.post(
            "/agent/project-chat",
            json={
                "project_name": "TestApp",
                "files": [{"path": "main.py", "content": "x = 1"}],
                "messages": [{"role": "user", "content": "Hi"}],
                "settings": {"api_key": "test-key"},
            },
        )

    assert res.status_code == 502
    assert "step limit" in res.json()["detail"].lower()


def test_project_chat_tool_argument_error_recovers(client):
    """Tool argument error produces an error tool response and does not crash the server (no 500)."""
    snapshot_files = [{"path": "src/main.py", "content": "x = 1\n"}]

    # Round 0: model calls read_file with an invalid/unsafe path
    response_round_0 = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c_bad_path",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": json.dumps({"path": "../etc/passwd"}),
                            },
                        }
                    ],
                }
            }
        ]
    }

    # Round 1: model sees the error tool output and replies gracefully
    response_round_1 = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "I cannot access ../etc/passwd because it is outside the project snapshot.",
                }
            }
        ]
    }

    with patch("backend.app.project_chat.call_chat_completion", side_effect=[response_round_0, response_round_1]):
        res = client.post(
            "/agent/project-chat",
            json={
                "project_name": "App",
                "files": snapshot_files,
                "messages": [{"role": "user", "content": "Read /etc/passwd"}],
                "settings": {"api_key": "test-key"},
            },
        )

    assert res.status_code == 200
    data = res.json()
    assert len(data["trace"]) == 1
    assert data["trace"][0]["status"] == "error"
    assert "rejected" in data["trace"][0]["detail"].lower() or "unsafe" in data["trace"][0]["detail"].lower()
    assert "cannot access" in data["reply"].lower()


@pytest.mark.parametrize('before,after', [('x', 'x\n'), ('x\n', 'x'), ('café\r\n', 'café\n')])
def test_proposal_preserves_newline_only_changes(before, after):
    output, status, _, proposal = handle_propose_changes(
        {'app.js': before}, {'changes': [{'path': 'app.js', 'operation': 'replace', 'content': after}]}
    )
    assert status == 'success'
    assert proposal['diff']
    assert proposal['changes'][0]['content'] == after
    if not before.endswith('\n') or not after.endswith('\n'):
        assert '\\ No newline at end of file' in proposal['diff']


def test_replace_missing_content_does_not_propose_erasing_file():
    _, status, _, proposal = handle_propose_changes(
        {'app.js': 'important'}, {'changes': [{'path': 'app.js', 'operation': 'replace'}]}
    )
    assert status == 'error'
    assert proposal is None


@pytest.mark.parametrize('tool_call', [None, 42, {'id': 'x', 'function': 'bad'}, {'function': {'name': 'list_files'}}])
def test_malformed_tool_call_returns_safe_502(client, tool_call):
    response = {'choices': [{'message': {'role': 'assistant', 'tool_calls': [tool_call]}}]}
    with patch('backend.app.project_chat.call_chat_completion', return_value=response):
        result = client.post('/agent/project-chat', json={
            'files': [{'path': 'app.js', 'content': 'hello'}],
            'messages': [{'role': 'user', 'content': 'Help'}],
            'settings': {'api_key': 'fixture'},
        })
    assert result.status_code == 502
    assert result.json()['detail'] == 'Model returned an invalid tool call.'
