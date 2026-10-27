"""Tests for ATI chat context estimation, budgeting, trimming, and preview endpoint."""

import base64
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from backend.app.attachments import PreparedAttachment
from backend.app.chat_context import (
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_RESPONSE_RESERVED,
    IMAGE_FIXED_TOKENS,
    PER_MESSAGE_OVERHEAD_TOKENS,
    ChatContextInfo,
    ContextBreakdown,
    ProjectChatContextResponse,
    budget_and_trim_messages,
    check_and_trim_tool_loop_context,
    estimate_attachment_tokens,
    estimate_image_tokens,
    estimate_message_tokens,
    estimate_text_tokens,
    estimate_tools_tokens,
)
from backend.app.project_chat import (
    ProjectChatMessage,
    ProjectChatRequest,
    ProjectFile,
    router as project_chat_router,
)

# Standalone test app for router testing
_test_app = FastAPI()


@_test_app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = exc.errors()
    msg = "; ".join([f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']}" for err in errors])
    return JSONResponse(status_code=422, content={"detail": f"Validation error: {msg}"})


_test_app.include_router(project_chat_router)


@pytest.fixture
def client():
    return TestClient(_test_app)


VALID_1X1_PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="


def make_large_valid_png_data_url(num_extra_bytes: int = 10000) -> str:
    # Must start with PNG magic bytes and be at least 24 bytes
    png_bytes = b"\x89PNG\r\n\x1a\n" + (b"\x00" * max(24, num_extra_bytes))
    return "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")


# 1. Conservative Token Estimation Tests
def test_estimate_text_tokens():
    assert estimate_text_tokens("") == 0
    assert estimate_text_tokens(None) == 0
    # "abc" -> 3 bytes -> 1 token
    assert estimate_text_tokens("abc") == 1
    # "abcd" -> 4 bytes -> ceil(4/3) = 2 tokens
    assert estimate_text_tokens("abcd") == 2
    # UTF-8 multi-byte: "Xin chào" -> 9 bytes -> ceil(9/3) = 3 tokens
    assert estimate_text_tokens("Xin chào") == 3


def test_estimate_image_tokens_fixed():
    assert estimate_image_tokens() == 1024


def test_estimate_attachment_tokens_image_no_raw_base64_counting():
    # Large base64 data_url should NEVER be counted as text tokens!
    large_png_url = make_large_valid_png_data_url(30000)
    image_att = PreparedAttachment(
        id="att_img_1",
        name="screenshot.png",
        mime_type="image/png",
        kind="image",
        data_url=large_png_url,
        size=30008,
    )
    tokens = estimate_attachment_tokens(image_att)
    # MUST be 1024, not 10000+ tokens from the base64 string
    assert tokens == IMAGE_FIXED_TOKENS


def test_estimate_attachment_tokens_text():
    text_att = PreparedAttachment(
        id="att_txt_1",
        name="notes.txt",
        mime_type="text/plain",
        kind="text",
        text="Hello world test",  # 16 bytes -> ceil(16/3) = 6 tokens
        size=16,
    )
    assert estimate_attachment_tokens(text_att) == 6


def test_estimate_message_tokens_with_overhead_and_attachments():
    msg = ProjectChatMessage(
        role="user",
        content="Short prompt",  # 12 bytes -> 4 tokens + 4 overhead = 8 tokens
        attachments=[
            PreparedAttachment(
                id="img_1",
                name="fig.png",
                mime_type="image/png",
                kind="image",
                data_url=VALID_1X1_PNG,
                size=68,
            ),
            PreparedAttachment(
                id="doc_1",
                name="doc.txt",
                mime_type="text/plain",
                kind="text",
                text="123456",  # 6 bytes -> 2 tokens
                size=6,
            ),
        ],
    )
    msg_tokens, att_tokens = estimate_message_tokens(msg)
    assert msg_tokens == 8
    assert att_tokens == 1024 + 2


def test_estimate_message_tokens_multimodal_list_content():
    content_list = [
        {"type": "text", "text": "Describe this"},  # 13 bytes -> 5 tokens
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + ("Z" * 50000)}},
    ]
    msg_tokens, att_tokens = estimate_message_tokens({"content": content_list})
    assert msg_tokens == 5 + PER_MESSAGE_OVERHEAD_TOKENS
    assert att_tokens == IMAGE_FIXED_TOKENS  # Image cost is 1024, NOT base64 length


def test_estimate_tools_tokens():
    tools = [
        {"type": "function", "function": {"name": "test_tool", "description": "sample"}}
    ]
    toks = estimate_tools_tokens(tools)
    assert toks > 0
    assert estimate_tools_tokens(None) == 0
    assert estimate_tools_tokens([]) == 0


# 2. ProjectChatRequest Model Validation Tests
def test_project_chat_request_defaults():
    req = ProjectChatRequest(
        messages=[ProjectChatMessage(role="user", content="Hi")],
    )
    assert req.context_window == DEFAULT_CONTEXT_WINDOW
    assert req.response_reserved == DEFAULT_RESPONSE_RESERVED


def test_project_chat_request_valid_custom():
    req = ProjectChatRequest(
        context_window=65536,
        response_reserved=8192,
        messages=[ProjectChatMessage(role="user", content="Hi")],
    )
    assert req.context_window == 65536
    assert req.response_reserved == 8192


def test_project_chat_request_bounds(client):
    # context_window < 8192
    res = client.post(
        "/agent/project-chat/context",
        json={
            "context_window": 8000,
            "response_reserved": 1000,
            "messages": [{"role": "user", "content": "Hi"}],
        },
    )
    assert res.status_code == 422

    # context_window > 2000000
    res = client.post(
        "/agent/project-chat/context",
        json={
            "context_window": 2000001,
            "response_reserved": 1000,
            "messages": [{"role": "user", "content": "Hi"}],
        },
    )
    assert res.status_code == 422

    # response_reserved < 512
    res = client.post(
        "/agent/project-chat/context",
        json={
            "context_window": 32768,
            "response_reserved": 500,
            "messages": [{"role": "user", "content": "Hi"}],
        },
    )
    assert res.status_code == 422

    # response_reserved > 200000
    res = client.post(
        "/agent/project-chat/context",
        json={
            "context_window": 65536,
            "response_reserved": 200001,
            "messages": [{"role": "user", "content": "Hi"}],
        },
    )
    assert res.status_code == 422

    # A 1M context with a 200k response reserve is valid.
    res = client.post(
        "/agent/project-chat/context",
        json={
            "context_window": 1000000,
            "response_reserved": 200000,
            "messages": [{"role": "user", "content": "Hi"}],
        },
    )
    assert res.status_code == 200
    assert res.json()["context"]["reserved_tokens"] == 200000

    # response_reserved >= context_window
    res = client.post(
        "/agent/project-chat/context",
        json={
            "context_window": 8192,
            "response_reserved": 8192,
            "messages": [{"role": "user", "content": "Hi"}],
        },
    )
    assert res.status_code == 422


# 3. Transport Message Bound Tests (500)
def test_transport_message_bound_500_accepted(client):
    # 500 messages should be accepted by message validation
    messages = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"msg {i}"} for i in range(499)]
    messages.append({"role": "user", "content": "Final user msg"})  # exactly 500 messages
    res = client.post(
        "/agent/project-chat/context",
        json={
            "project_name": "P",
            "messages": messages,
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert "context" in data
    assert data["context"]["estimate"] is True


def test_transport_message_bound_501_rejected(client):
    messages = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"msg {i}"} for i in range(500)]
    messages.append({"role": "user", "content": "Final user msg"})  # 501 messages
    res = client.post(
        "/agent/project-chat/context",
        json={
            "project_name": "P",
            "messages": messages,
        },
    )
    assert res.status_code == 400
    assert "500" in res.json()["detail"]


# 4. Trimming and Budgeting Logic Tests
def test_budget_no_trimming_needed():
    messages = [
        ProjectChatMessage(role="user", content="Turn 1 question"),
        ProjectChatMessage(role="assistant", content="Turn 1 answer"),
        ProjectChatMessage(role="user", content="Turn 2 question"),
    ]
    retained, context_info = budget_and_trim_messages(
        messages=messages,
        context_window=32768,
        response_reserved=4096,
        system_tokens=100,
        tools_tokens=200,
        project_tokens=300,
        tool_results_tokens=0,
    )
    assert len(retained) == 3
    assert context_info.dropped_messages == 0
    assert context_info.compacted is False
    assert context_info.estimate is True
    assert context_info.breakdown.system == 100
    assert context_info.breakdown.tools == 200
    assert context_info.breakdown.project == 300
    assert context_info.breakdown.tool_results == 0
    assert context_info.estimated_tokens == (
        100 + 200 + 300 + context_info.breakdown.messages + context_info.breakdown.attachments
    )
    assert context_info.usage_percent == round((context_info.estimated_tokens / 32768) * 100, 2)


def test_budget_trims_oldest_whole_pairs():
    # Long messages in history
    u0 = ProjectChatMessage(role="user", content="A" * 3000)      # ~1000 tokens
    a0 = ProjectChatMessage(role="assistant", content="B" * 3000) # ~1000 tokens
    u1 = ProjectChatMessage(role="user", content="C" * 3000)      # ~1000 tokens
    a1 = ProjectChatMessage(role="assistant", content="D" * 3000) # ~1000 tokens
    u2 = ProjectChatMessage(role="user", content="Latest user question") # ~10 tokens

    messages = [u0, a0, u1, a1, u2]
    # Budget = 8192 - 4096 = 4096 tokens
    # Fixed = system(500) + tools(500) + project(500) + latest(10) = 1510 tokens
    # History total = ~4000 tokens. Total = ~5510 > 4096.
    # Trimming u0 + a0 (~2000 tokens) -> remaining = ~3510 <= 4096.
    retained, context_info = budget_and_trim_messages(
        messages=messages,
        context_window=8192,
        response_reserved=4096,
        system_tokens=500,
        tools_tokens=500,
        project_tokens=500,
    )
    assert len(retained) == 3
    assert retained[0].content == u1.content
    assert retained[1].content == a1.content
    assert retained[2].content == u2.content
    assert context_info.dropped_messages == 2
    assert context_info.compacted is True
    assert context_info.estimated_tokens <= (8192 - 4096)


def test_budget_fixed_exceeds_budget_raises_400():
    # Fixed context alone exceeds budget
    u_latest = ProjectChatMessage(role="user", content="Latest question")
    messages = [u_latest]

    with pytest.raises(Exception) as exc_info:
        budget_and_trim_messages(
            messages=messages,
            context_window=8192,
            response_reserved=4096,  # budget = 4096
            system_tokens=2000,
            tools_tokens=2000,
            project_tokens=2000,  # 6000 > 4096!
        )
    assert exc_info.value.status_code == 400
    assert "exceeds available token budget" in exc_info.value.detail.lower()


# 5. POST /agent/project-chat/context Preview Endpoint Tests
def test_context_preview_endpoint_success(client):
    res = client.post(
        "/agent/project-chat/context",
        json={
            "project_name": "DemoApp",
            "files": [{"path": "main.py", "content": "print('hello')"}],
            "messages": [
                {"role": "user", "content": "Hello there"},
            ],
            "context_window": 32768,
            "response_reserved": 4096,
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert "context" in data
    ctx = data["context"]
    assert ctx["window_tokens"] == 32768
    assert ctx["reserved_tokens"] == 4096
    assert ctx["estimated_tokens"] > 0
    assert ctx["usage_percent"] >= 0.0
    assert ctx["estimate"] is True
    assert ctx["compacted"] is False
    assert ctx["dropped_messages"] == 0

    breakdown = ctx["breakdown"]
    assert breakdown["system"] > 0
    assert breakdown["tools"] > 0
    assert breakdown["project"] > 0
    assert breakdown["messages"] > 0
    assert breakdown["attachments"] == 0
    assert breakdown["tool_results"] == 0
    assert ctx["estimated_tokens"] == sum(breakdown.values())


def test_context_preview_without_api_key(client):
    # Preview endpoint must NOT require an API key even if settings has provider='openai'
    res = client.post(
        "/agent/project-chat/context",
        json={
            "project_name": "DemoApp",
            "messages": [{"role": "user", "content": "Hi"}],
            "settings": {"provider": "openai", "api_key": ""},
        },
    )
    assert res.status_code == 200
    assert "context" in res.json()


def test_context_preview_with_attachments(client):
    res = client.post(
        "/agent/project-chat/context",
        json={
            "messages": [
                {
                    "role": "user",
                    "content": "Look at this",
                    "attachments": [
                        {
                            "id": "img1",
                            "name": "pic.png",
                            "mime_type": "image/png",
                            "kind": "image",
                            "data_url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
                            "size": 100,
                        },
                        {
                            "id": "doc1",
                            "name": "readme.txt",
                            "mime_type": "text/plain",
                            "kind": "text",
                            "text": "Some text content here",
                            "size": 22,
                        },
                    ],
                }
            ],
        },
    )
    assert res.status_code == 200
    ctx = res.json()["context"]
    # Image (1024) + text attachment tokens
    assert ctx["breakdown"]["attachments"] >= 1024


def test_context_preview_compacts_exceeded_history(client):
    # Send enough messages that exceed a tight budget of 8192 with 4096 reserved
    long_text = "x" * 2000
    messages = [
        {"role": "user", "content": long_text},
        {"role": "assistant", "content": long_text},
        {"role": "user", "content": long_text},
        {"role": "assistant", "content": long_text},
        {"role": "user", "content": "Final user request"},
    ]
    res = client.post(
        "/agent/project-chat/context",
        json={
            "context_window": 8192,
            "response_reserved": 4096,
            "messages": messages,
        },
    )
    assert res.status_code == 200
    ctx = res.json()["context"]
    assert ctx["dropped_messages"] > 0
    assert ctx["compacted"] is True


def test_context_preview_actionable_error_when_cannot_fit(client):
    # When latest message + system + tools alone exceeds budget
    huge_message = "x" * 9000  # ~3000 tokens
    res = client.post(
        "/agent/project-chat/context",
        json={
            "context_window": 8192,
            "response_reserved": 7500,  # budget = 692 tokens
            "messages": [{"role": "user", "content": huge_message}],
        },
    )
    assert res.status_code == 400
    assert "exceeds available token budget" in res.json()["detail"].lower()


# 6. POST /agent/project-chat Integration and Tool Loop Tests
@pytest.mark.anyio
async def test_project_chat_response_includes_context(client):
    mock_llm_response = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "This is the final assistant reply.",
                }
            }
        ]
    }
    with patch(
        "backend.app.project_chat.call_chat_completion",
        new=AsyncMock(return_value=mock_llm_response),
    ):
        res = client.post(
            "/agent/project-chat",
            json={
                "project_name": "TestProj",
                "messages": [{"role": "user", "content": "Hello"}],
                "settings": {"api_key": "test-key"},
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["reply"] == "This is the final assistant reply."
        assert "context" in data
        assert data["context"] is not None
        assert data["context"]["estimate"] is True
        assert data["context"]["window_tokens"] == 32768
        assert data["context"]["reserved_tokens"] == 4096
        assert data["context"]["breakdown"]["system"] > 0
        assert data["context"]["breakdown"]["tools"] > 0


@pytest.mark.anyio
async def test_project_chat_atomic_tool_chain_preservation(client):
    # Assistant returns tool calls, tool runs, then assistant finishes
    tool_call_response = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "tc_1",
                            "type": "function",
                            "function": {
                                "name": "list_files",
                                "arguments": "{}",
                            },
                        }
                    ],
                }
            }
        ]
    }
    final_response = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Files listed successfully.",
                }
            }
        ]
    }

    mock_llm = AsyncMock(side_effect=[tool_call_response, final_response])
    with patch("backend.app.project_chat.call_chat_completion", new=mock_llm):
        res = client.post(
            "/agent/project-chat",
            json={
                "project_name": "TestProj",
                "files": [{"path": "a.txt", "content": "hello"}],
                "messages": [{"role": "user", "content": "List the files"}],
                "settings": {"api_key": "test-key"},
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["reply"] == "Files listed successfully."
        assert len(data["trace"]) == 1
        assert data["context"]["breakdown"]["tool_results"] > 0


@pytest.mark.anyio
async def test_project_chat_tool_loop_exceeded_context_fails_cleanly():
    # Helper unit test for check_and_trim_tool_loop_context
    llm_messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "proj"},
        {"role": "user", "content": "latest user"},
        {
            "role": "assistant",
            "tool_calls": [{"id": "tc_1", "function": {"name": "f", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "tc_1", "name": "f", "content": "X" * 10000},
    ]
    # tool_chain_start_idx = 3 (pointing to the assistant tool_calls message)
    # budget = 8192 - 8000 = 192 tokens. Content alone is ~3333 tokens.
    with pytest.raises(Exception) as exc_info:
        check_and_trim_tool_loop_context(
            llm_messages=llm_messages,
            tool_chain_start_idx=3,
            context_window=8192,
            response_reserved=8000,
            system_tokens=100,
            tools_tokens=100,
            project_tokens=100,
            current_dropped_messages=0,
        )
    assert exc_info.value.status_code == 400
    assert "Context window exceeded during tool execution" in exc_info.value.detail
