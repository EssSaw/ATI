import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from backend.app.config import ResolvedConfig
from backend.app.project_chat import router as project_chat_router
from backend.app.project_subagents import (
    MAX_WORKER_BATCH_CALLS,
    MAX_WORKER_CONCURRENCY,
    MAX_WORKER_OUTPUT_CHARS,
    MAX_WORKER_ROUNDS,
    MAX_WORKER_TOTAL_CALLS,
    MAX_WORKERS_PER_REQUEST,
    PROJECT_SUBAGENT_DEFINITION,
    SubagentResult,
    execute_delegate_tasks,
    get_worker_tool_definitions,
    run_single_worker,
    sanitize_error_message,
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
    """TestClient that uses main app if coordinator wired /agent/project-chat, else local_app."""
    try:
        from backend.app.main import app as main_app

        has_route = any(
            getattr(route, "path", None) == "/agent/project-chat"
            for route in getattr(main_app, "routes", [])
        )
        if has_route:
            return TestClient(main_app)
    except Exception:
        pass
    return TestClient(local_app)


@pytest.fixture
def sample_snapshot():
    return {
        "src/main.py": "def main():\n    print('hello world')\n",
        "src/utils.py": "def helper():\n    return 42\n",
        "tests/test_main.py": "def test_ok():\n    assert True\n",
    }


@pytest.fixture
def resolved_config():
    return ResolvedConfig(
        base_url="https://api.openai.com/v1",
        api_key="sk-test-secret-coordinator-key",
        model="gpt-4o-mini",
    )


# ---------------------------------------------------------------------------
# 1. Schema & Tool Definitions Tests
# ---------------------------------------------------------------------------


def test_subagent_tool_schema_definition():
    """Verify delegate_tasks schema conforms to OpenAI tool definitions."""
    assert PROJECT_SUBAGENT_DEFINITION["type"] == "function"
    func = PROJECT_SUBAGENT_DEFINITION["function"]
    assert func["name"] == "delegate_tasks"
    params = func["parameters"]
    assert params["type"] == "object"
    assert "tasks" in params["properties"]
    assert params["required"] == ["tasks"]
    task_items = params["properties"]["tasks"]["items"]
    assert "name" in task_items["properties"]
    assert "task" in task_items["properties"]
    assert "paths" in task_items["properties"]
    assert "model" in task_items["properties"]
    assert set(task_items["required"]) == {"name", "task"}


def test_worker_tool_definitions_exclusions():
    """Verify worker tools exclude propose_changes and delegate_tasks, but include snapshot tools."""
    worker_tools = get_worker_tool_definitions()
    tool_names = [t["function"]["name"] for t in worker_tools]

    assert "propose_changes" not in tool_names
    assert "delegate_tasks" not in tool_names
    assert "list_files" in tool_names
    assert "read_file" in tool_names
    assert "search_files" in tool_names
    assert "inspect_project" in tool_names


# ---------------------------------------------------------------------------
# 2. Concurrency Capped at 2 via Semaphore
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_subagent_concurrency_max_2(sample_snapshot, resolved_config):
    """Evidence concurrency max 2: when 3 workers run, at most 2 execute concurrently."""
    active_calls = 0
    max_active_calls = 0

    async def mock_call_chat_completion(**kwargs):
        nonlocal active_calls, max_active_calls
        active_calls += 1
        if active_calls > max_active_calls:
            max_active_calls = active_calls
        await asyncio.sleep(0.04)
        active_calls -= 1
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Worker analysis complete.",
                    }
                }
            ]
        }

    tasks = [
        {"name": "worker_1", "task": "Analyze main.py"},
        {"name": "worker_2", "task": "Analyze utils.py"},
        {"name": "worker_3", "task": "Analyze tests"},
    ]

    with patch("backend.app.project_subagents.call_chat_completion", side_effect=mock_call_chat_completion):
        out_str, trace, sub_results, new_count = await execute_delegate_tasks(
            raw_arguments={"tasks": tasks},
            snapshot=sample_snapshot,
            resolved_settings=resolved_config,
            current_workers_count=0,
            max_workers_budget=3,
        )

    assert max_active_calls == 2
    assert new_count == 3
    assert len(sub_results) == 3
    assert all(r.status == "success" for r in sub_results)
    assert trace.status == "success"
    assert "worker_1 (success)" in trace.detail
    assert "worker_2 (success)" in trace.detail
    assert "worker_3 (success)" in trace.detail


# ---------------------------------------------------------------------------
# 3. Model Override & Credential Inheritance
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_subagent_model_override_and_credential_inheritance(sample_snapshot, resolved_config):
    """Verify worker can override model and inherits base_url and api_key."""
    recorded_calls = []

    async def mock_call_chat_completion(**kwargs):
        recorded_calls.append(kwargs)
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": f"Result for {kwargs['model']}",
                    }
                }
            ]
        }

    tasks = [
        {"name": "task_custom", "task": "Analyze with custom", "model": "specialized-code-model"},
        {"name": "task_default", "task": "Analyze with default"},
    ]

    with patch("backend.app.project_subagents.call_chat_completion", side_effect=mock_call_chat_completion):
        out_str, trace, sub_results, new_count = await execute_delegate_tasks(
            raw_arguments={"tasks": tasks},
            snapshot=sample_snapshot,
            resolved_settings=resolved_config,
            current_workers_count=0,
            max_workers_budget=3,
        )

    assert new_count == 2
    assert len(recorded_calls) == 2

    call_custom = next(c for c in recorded_calls if c["model"] == "specialized-code-model")
    call_default = next(c for c in recorded_calls if c["model"] == "gpt-4o-mini")

    assert call_custom["base_url"] == resolved_config.base_url
    assert call_custom["api_key"] == resolved_config.api_key
    assert call_default["base_url"] == resolved_config.base_url
    assert call_default["api_key"] == resolved_config.api_key


# ---------------------------------------------------------------------------
# 4. Prohibit Recursive Delegation & propose_changes
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_subagent_prohibits_recursive_delegation(sample_snapshot, resolved_config):
    """Verify worker cannot call delegate_tasks recursively."""
    round_idx = 0

    async def mock_call_chat_completion(**kwargs):
        nonlocal round_idx
        round_idx += 1
        if round_idx == 1:
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "rec_1",
                                    "type": "function",
                                    "function": {
                                        "name": "delegate_tasks",
                                        "arguments": json.dumps({"tasks": [{"name": "sub", "task": "subtask"}]}),
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        else:
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Understood, I will handle it directly.",
                        }
                    }
                ]
            }

    tasks = [{"name": "worker_rec", "task": "Try recursive call"}]

    with patch("backend.app.project_subagents.call_chat_completion", side_effect=mock_call_chat_completion):
        out_str, trace, sub_results, new_count = await execute_delegate_tasks(
            raw_arguments={"tasks": tasks},
            snapshot=sample_snapshot,
            resolved_settings=resolved_config,
            current_workers_count=0,
        )

    assert sub_results[0].status == "success"
    assert sub_results[0].reply == "Understood, I will handle it directly."
    worker_trace = sub_results[0].trace
    assert any(t.tool == "delegate_tasks" and t.status == "error" for t in worker_trace)


@pytest.mark.anyio
async def test_subagent_prohibits_propose_changes(sample_snapshot, resolved_config):
    """Verify worker cannot call propose_changes."""
    round_idx = 0

    async def mock_call_chat_completion(**kwargs):
        nonlocal round_idx
        round_idx += 1
        if round_idx == 1:
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "prop_1",
                                    "type": "function",
                                    "function": {
                                        "name": "propose_changes",
                                        "arguments": json.dumps({"changes": [{"path": "src/main.py", "operation": "create", "content": "x=1"}]}),
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        else:
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Propose changes blocked, providing advisory feedback only.",
                        }
                    }
                ]
            }

    tasks = [{"name": "worker_prop", "task": "Try propose changes"}]

    with patch("backend.app.project_subagents.call_chat_completion", side_effect=mock_call_chat_completion):
        out_str, trace, sub_results, new_count = await execute_delegate_tasks(
            raw_arguments={"tasks": tasks},
            snapshot=sample_snapshot,
            resolved_settings=resolved_config,
            current_workers_count=0,
        )

    assert sub_results[0].status == "success"
    worker_trace = sub_results[0].trace
    assert any(t.tool == "propose_changes" and t.status == "error" for t in worker_trace)


# ---------------------------------------------------------------------------
# 5. Snapshot Isolation & Excluded Paths
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_subagent_selected_snapshot_paths(sample_snapshot, resolved_config):
    """Verify worker receives only selected paths when specified."""
    captured_messages = []

    async def mock_call_chat_completion(**kwargs):
        captured_messages.append(kwargs["messages"])
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Inspected subset.",
                    }
                }
            ]
        }

    tasks = [{"name": "worker_scoped", "task": "Inspect utils only", "paths": ["src/utils.py"]}]

    with patch("backend.app.project_subagents.call_chat_completion", side_effect=mock_call_chat_completion):
        await execute_delegate_tasks(
            raw_arguments={"tasks": tasks},
            snapshot=sample_snapshot,
            resolved_settings=resolved_config,
            current_workers_count=0,
        )

    user_content = captured_messages[0][1]["content"]
    assert "--- File: src/utils.py ---" in user_content
    assert "src/main.py" not in user_content
    assert "tests/test_main.py" not in user_content


@pytest.mark.anyio
async def test_subagent_default_all_snapshot(sample_snapshot, resolved_config):
    """Verify worker receives all snapshot files when paths is omitted or None."""
    captured_messages = []

    async def mock_call_chat_completion(**kwargs):
        captured_messages.append(kwargs["messages"])
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Inspected all files.",
                    }
                }
            ]
        }

    tasks = [{"name": "worker_all", "task": "Inspect all"}]

    with patch("backend.app.project_subagents.call_chat_completion", side_effect=mock_call_chat_completion):
        await execute_delegate_tasks(
            raw_arguments={"tasks": tasks},
            snapshot=sample_snapshot,
            resolved_settings=resolved_config,
            current_workers_count=0,
        )

    user_content = captured_messages[0][1]["content"]
    assert "src/main.py" in user_content
    assert "src/utils.py" in user_content
    assert "tests/test_main.py" in user_content


@pytest.mark.anyio
@pytest.mark.parametrize("bad_path", [
    ".env",
    ".env.local",
    "id_rsa",
    "../secret.py",
    "/etc/passwd",
    "src/nonexistent.py",
])
async def test_subagent_rejects_unsafe_and_excluded_paths(bad_path, sample_snapshot, resolved_config):
    """Verify task with unsafe, excluded, or nonexistent path is rejected with error tool output (safe not 500)."""
    tasks = [{"name": "worker_bad", "task": "Inspect bad file", "paths": [bad_path]}]

    out_str, trace, sub_results, new_count = await execute_delegate_tasks(
        raw_arguments={"tasks": tasks},
        snapshot=sample_snapshot,
        resolved_settings=resolved_config,
        current_workers_count=0,
    )

    assert new_count == 0
    assert len(sub_results) == 0
    assert trace.status == "error"
    data = json.loads(out_str)
    assert "error" in data


# ---------------------------------------------------------------------------
# 6. Failure Isolation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_subagent_failure_isolation(sample_snapshot, resolved_config):
    """Verify that if one worker fails, others succeed and parent does not crash."""
    async def mock_call_chat_completion(**kwargs):
        task_name = kwargs["messages"][1]["content"]
        if "failing" in task_name:
            raise RuntimeError("Upstream provider failure for worker")
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Successful worker output",
                    }
                }
            ]
        }

    tasks = [
        {"name": "worker_ok", "task": "Normal task"},
        {"name": "worker_failing", "task": "This is failing task"},
    ]

    with patch("backend.app.project_subagents.call_chat_completion", side_effect=mock_call_chat_completion):
        out_str, trace, sub_results, new_count = await execute_delegate_tasks(
            raw_arguments={"tasks": tasks},
            snapshot=sample_snapshot,
            resolved_settings=resolved_config,
            current_workers_count=0,
        )

    assert new_count == 2
    assert len(sub_results) == 2

    res_ok = next(r for r in sub_results if r.name == "worker_ok")
    res_fail = next(r for r in sub_results if r.name == "worker_failing")

    assert res_ok.status == "success"
    assert res_ok.reply == "Successful worker output"
    assert res_fail.status == "error"
    assert res_fail.error is not None
    assert "RuntimeError" in res_fail.error

    assert "worker_ok (success)" in trace.detail
    assert "worker_failing (error)" in trace.detail


# ---------------------------------------------------------------------------
# 7. Worker Limits (Rounds, Batch, Total Calls, Output Chars)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_subagent_limits_max_rounds(sample_snapshot, resolved_config):
    """Worker model loops with tool calls past MAX_WORKER_ROUNDS=3 and terminates safely."""
    async def mock_call_chat_completion(**kwargs):
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "list_files", "arguments": "{}"},
                            }
                        ],
                    }
                }
            ]
        }

    tasks = [{"name": "loop_worker", "task": "Infinite loop"}]

    with patch("backend.app.project_subagents.call_chat_completion", side_effect=mock_call_chat_completion):
        out_str, trace, sub_results, new_count = await execute_delegate_tasks(
            raw_arguments={"tasks": tasks},
            snapshot=sample_snapshot,
            resolved_settings=resolved_config,
            current_workers_count=0,
        )

    assert sub_results[0].status == "error"
    assert "round limit" in sub_results[0].error.lower()


@pytest.mark.anyio
async def test_subagent_limits_batch_calls(sample_snapshot, resolved_config):
    """Worker model returns > MAX_WORKER_BATCH_CALLS (3) in a single turn."""
    async def mock_call_chat_completion(**kwargs):
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {"id": f"c_{i}", "type": "function", "function": {"name": "list_files", "arguments": "{}"}}
                            for i in range(4)
                        ],
                    }
                }
            ]
        }

    tasks = [{"name": "batch_worker", "task": "Batch excess"}]

    with patch("backend.app.project_subagents.call_chat_completion", side_effect=mock_call_chat_completion):
        out_str, trace, sub_results, new_count = await execute_delegate_tasks(
            raw_arguments={"tasks": tasks},
            snapshot=sample_snapshot,
            resolved_settings=resolved_config,
            current_workers_count=0,
        )

    assert sub_results[0].status == "error"
    assert "batch limit" in sub_results[0].error.lower()


@pytest.mark.anyio
async def test_subagent_limits_output_characters(sample_snapshot, resolved_config):
    """Worker model returns > 12000 chars; output is truncated to 12000 chars."""
    huge_text = "A" * 15000

    async def mock_call_chat_completion(**kwargs):
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": huge_text,
                    }
                }
            ]
        }

    tasks = [{"name": "verbose_worker", "task": "Write verbose essay"}]

    with patch("backend.app.project_subagents.call_chat_completion", side_effect=mock_call_chat_completion):
        out_str, trace, sub_results, new_count = await execute_delegate_tasks(
            raw_arguments={"tasks": tasks},
            snapshot=sample_snapshot,
            resolved_settings=resolved_config,
            current_workers_count=0,
        )

    assert sub_results[0].status == "success"
    assert len(sub_results[0].reply) == MAX_WORKER_OUTPUT_CHARS


# ---------------------------------------------------------------------------
# 8. Parent Worker Budget Enforcement (Max 3 Total Across Calls)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_parent_worker_budget_tracking(sample_snapshot, resolved_config):
    """Parent is capped at <= 3 workers total across multiple delegate calls."""
    async def mock_call_chat_completion(**kwargs):
        return {
            "choices": [{"message": {"role": "assistant", "content": "Done"}}]
        }

    with patch("backend.app.project_subagents.call_chat_completion", side_effect=mock_call_chat_completion):
        # Call 1: 2 workers -> allowed
        _, _, _, count1 = await execute_delegate_tasks(
            raw_arguments={"tasks": [{"name": "w1", "task": "t1"}, {"name": "w2", "task": "t2"}]},
            snapshot=sample_snapshot,
            resolved_settings=resolved_config,
            current_workers_count=0,
            max_workers_budget=3,
        )
        assert count1 == 2

        # Call 2: 2 workers -> rejected (2 + 2 = 4 > 3)
        out2, trace2, _, count2 = await execute_delegate_tasks(
            raw_arguments={"tasks": [{"name": "w3", "task": "t3"}, {"name": "w4", "task": "t4"}]},
            snapshot=sample_snapshot,
            resolved_settings=resolved_config,
            current_workers_count=2,
            max_workers_budget=3,
        )
        assert count2 == 0
        assert trace2.status == "error"
        assert "budget exceeded" in json.loads(out2)["error"].lower()

        # Call 3: 1 worker -> allowed (2 + 1 = 3)
        _, _, _, count3 = await execute_delegate_tasks(
            raw_arguments={"tasks": [{"name": "w5", "task": "t5"}]},
            snapshot=sample_snapshot,
            resolved_settings=resolved_config,
            current_workers_count=2,
            max_workers_budget=3,
        )
        assert count3 == 1

        # Call 4: 1 worker -> rejected (3 + 1 = 4 > 3)
        out4, trace4, _, count4 = await execute_delegate_tasks(
            raw_arguments={"tasks": [{"name": "w6", "task": "t6"}]},
            snapshot=sample_snapshot,
            resolved_settings=resolved_config,
            current_workers_count=3,
            max_workers_budget=3,
        )
        assert count4 == 0
        assert trace4.status == "error"


@pytest.mark.anyio
async def test_parent_worker_budget_counts_failures(sample_snapshot, resolved_config):
    """Failed workers still count toward the 3-worker budget."""
    async def mock_call_chat_completion(**kwargs):
        raise RuntimeError("Immediate failure")

    with patch("backend.app.project_subagents.call_chat_completion", side_effect=mock_call_chat_completion):
        # Delegate 2 failing workers
        _, _, _, count1 = await execute_delegate_tasks(
            raw_arguments={"tasks": [{"name": "w1", "task": "t1"}, {"name": "w2", "task": "t2"}]},
            snapshot=sample_snapshot,
            resolved_settings=resolved_config,
            current_workers_count=0,
            max_workers_budget=3,
        )
        assert count1 == 2

        # Attempting 2 more workers is rejected because 2 + 2 = 4 > 3
        out2, trace2, _, count2 = await execute_delegate_tasks(
            raw_arguments={"tasks": [{"name": "w3", "task": "t3"}, {"name": "w4", "task": "t4"}]},
            snapshot=sample_snapshot,
            resolved_settings=resolved_config,
            current_workers_count=2,
            max_workers_budget=3,
        )
        assert count2 == 0
        assert "budget exceeded" in json.loads(out2)["error"].lower()


# ---------------------------------------------------------------------------
# 9. Argument Validation (Safe Not 500)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize("invalid_raw", [
    "{not valid json",
    "123",
    "\"a string\"",
    json.dumps({}),
    json.dumps({"tasks": []}),
    json.dumps({"tasks": "not a list"}),
    json.dumps({"tasks": [{"task": "missing name"}]}),
    json.dumps({"tasks": [{"name": "", "task": "blank name"}]}),
    json.dumps({"tasks": [{"name": "w1"}]}),
    json.dumps({"tasks": [{"name": "w1", "task": ""}]}),
    json.dumps({"tasks": [{"name": "w1", "task": "ok", "model": ""}]}),
    json.dumps({"tasks": [{"name": "w1", "task": "ok", "paths": "not a list"}]}),
    json.dumps({"tasks": [{"name": "w1", "task": "ok", "paths": [123]}]}),
])
async def test_delegate_tasks_argument_validation(invalid_raw, sample_snapshot, resolved_config):
    """Verify malformed arguments return safe error tool result instead of 500 crash."""
    out_str, trace, sub_results, new_count = await execute_delegate_tasks(
        raw_arguments=invalid_raw,
        snapshot=sample_snapshot,
        resolved_settings=resolved_config,
        current_workers_count=0,
    )

    assert new_count == 0
    assert len(sub_results) == 0
    assert trace.status == "error"
    data = json.loads(out_str)
    assert "error" in data


# ---------------------------------------------------------------------------
# 10. Credential Sanitization
# ---------------------------------------------------------------------------


def test_sanitize_error_message():
    raw = "Request failed for sk-test1234567890abcdef123456 with Bearer eyJhbGciOiJIUzI1NiJ9.test"
    sanitized = sanitize_error_message(raw, api_key="sk-test1234567890abcdef123456")
    assert "sk-test1234567890abcdef123456" not in sanitized
    assert "Bearer eyJhbGciOiJIUzI1NiJ9.test" not in sanitized
    assert "[REDACTED]" in sanitized


# ---------------------------------------------------------------------------
# 11. Full End-to-End Integration via /agent/project-chat
# ---------------------------------------------------------------------------


def test_project_chat_integration_with_delegate_tasks(client):
    """Full coordinator flow calling delegate_tasks and returning final response with subagents."""
    coord_round_0 = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "del_call_1",
                            "type": "function",
                            "function": {
                                "name": "delegate_tasks",
                                "arguments": json.dumps({
                                    "tasks": [
                                        {"name": "auth_checker", "task": "Check authentication in src/main.py"},
                                        {"name": "perf_checker", "task": "Check performance in src/utils.py"},
                                    ]
                                }),
                            },
                        }
                    ],
                }
            }
        ]
    }

    coord_round_1 = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Subagents completed. Auth is clean and performance is optimal.",
                }
            }
        ]
    }

    worker_res_1 = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Auth report: No credentials hardcoded.",
                }
            }
        ]
    }

    worker_res_2 = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Perf report: Algorithms run in O(1).",
                }
            }
        ]
    }

    with patch(
        "backend.app.project_chat.call_chat_completion",
        side_effect=[coord_round_0, coord_round_1],
    ), patch(
        "backend.app.project_subagents.call_chat_completion",
        side_effect=[worker_res_1, worker_res_2],
    ):
        res = client.post(
            "/agent/project-chat",
            json={
                "project_name": "DemoProject",
                "files": [
                    {"path": "src/main.py", "content": "print('hello')\n"},
                    {"path": "src/utils.py", "content": "def add(a, b): return a + b\n"},
                ],
                "messages": [{"role": "user", "content": "Run security and perf audits"}],
                "settings": {
                    "mode": "direct",
                    "provider": "openai",
                    "api_key": "sk-test-secret-key-123",
                },
            },
        )

    assert res.status_code == 200
    data = res.json()
    assert data["reply"] == "Subagents completed. Auth is clean and performance is optimal."

    # Subagents field populated
    subagents = data.get("subagents", [])
    assert len(subagents) == 2
    assert subagents[0]["name"] == "auth_checker"
    assert subagents[0]["status"] == "success"
    assert "Auth report" in subagents[0]["reply"]
    assert subagents[1]["name"] == "perf_checker"
    assert subagents[1]["status"] == "success"
    assert "Perf report" in subagents[1]["reply"]

    # Trace includes delegate_tasks
    del_trace = next((t for t in data["trace"] if t["tool"] == "delegate_tasks"), None)
    assert del_trace is not None
    assert "auth_checker (success)" in del_trace["detail"]
    assert "perf_checker (success)" in del_trace["detail"]


def test_project_chat_response_subagents_defaults_empty(client):
    """When delegate_tasks is not called, subagents field defaults to []."""
    mock_res = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Direct reply without subagents.",
                }
            }
        ]
    }

    with patch("backend.app.project_chat.call_chat_completion", return_value=mock_res):
        res = client.post(
            "/agent/project-chat",
            json={
                "project_name": "SimpleProject",
                "files": [{"path": "src/main.py", "content": "x = 1"}],
                "messages": [{"role": "user", "content": "Hello"}],
                "settings": {
                    "mode": "direct",
                    "provider": "openai",
                    "api_key": "sk-test-secret-key-123",
                },
            },
        )

    assert res.status_code == 200
    data = res.json()
    assert data["reply"] == "Direct reply without subagents."
    assert data.get("subagents") == []


@pytest.mark.anyio
async def test_worker_can_execute_snapshot_tools(sample_snapshot, resolved_config):
    """Worker can call list_files and read_file on its snapshot."""
    round_idx = 0

    async def mock_call_chat_completion(**kwargs):
        nonlocal round_idx
        round_idx += 1
        if round_idx == 1:
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "c1",
                                    "type": "function",
                                    "function": {"name": "read_file", "arguments": json.dumps({"path": "src/main.py"})},
                                }
                            ],
                        }
                    }
                ]
            }
        else:
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Read main.py successfully.",
                        }
                    }
                ]
            }

    tasks = [{"name": "reader", "task": "Read main file"}]

    with patch("backend.app.project_subagents.call_chat_completion", side_effect=mock_call_chat_completion):
        out_str, trace, sub_results, new_count = await execute_delegate_tasks(
            raw_arguments={"tasks": tasks},
            snapshot=sample_snapshot,
            resolved_settings=resolved_config,
            current_workers_count=0,
        )

    assert sub_results[0].status == "success"
    assert sub_results[0].reply == "Read main.py successfully."
    assert any(t.tool == "read_file" and t.status == "success" for t in sub_results[0].trace)
