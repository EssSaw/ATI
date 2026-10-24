import asyncio
import json
from unittest.mock import AsyncMock, patch
import pytest
from fastapi import HTTPException

from backend.app.agent.runner import run_agent, run_chat
from backend.app.schemas import AgentRunRequest, Settings
from backend.app.sessions import SessionStore


def make_tool_call_response(tool_name: str, arguments: dict, call_id: str = "call_abc"):
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "arguments": json.dumps(arguments),
                            },
                        }
                    ],
                }
            }
        ]
    }


def make_text_response(text: str):
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": text,
                    "tool_calls": None,
                }
            }
        ]
    }


def test_agent_multi_step_flow():
    async def _run():
        store = SessionStore()
        request = AgentRunRequest(
            code="def foo()\n    pass",
            question="Fix this",
            settings=Settings(api_key="test-key", provider="openai"),
        )

        mock_report = {
            "summary": "Syntax error identified.",
            "bugs": [
                {
                    "type": "syntax",
                    "line": 1,
                    "message": "Missing colon",
                    "suggestion": "Add colon",
                }
            ],
            "explanation": "Need colon after def.",
            "fixed_code": "def foo():\n    pass",
        }

        # Sequence: parse_ast -> run_linter -> submit_report
        responses = [
            make_tool_call_response("parse_ast", {}, "call_1"),
            make_tool_call_response("run_linter", {}, "call_2"),
            make_tool_call_response("submit_report", mock_report, "call_3"),
        ]

        with patch("backend.app.agent.runner.call_chat_completion", side_effect=responses):
            res = await run_agent(request, store)

        assert res.report.summary == "Syntax error identified."
        assert len(res.report.bugs) == 1
        assert res.report.bugs[0].type == "syntax"
        assert len(res.trace) == 3
        assert [t.tool for t in res.trace] == ["parse_ast", "run_linter", "submit_report"]
        assert "parse_ast" in res.tools_used
        assert "run_linter" in res.tools_used
        assert "submit_report" in res.tools_used
        assert res.latency_ms > 0
        assert store.get_session(res.session_id) is not None

    asyncio.run(_run())


def test_agent_recovers_when_model_replies_text_first():
    async def _run():
        store = SessionStore()
        request = AgentRunRequest(
            code="x = 10",
            settings=Settings(api_key="test-key"),
        )

        mock_report = {
            "summary": "Clean code.",
            "bugs": [],
            "explanation": "No bugs found.",
            "fixed_code": "x = 10",
        }

        # Step 1: Model replies text "I found no bugs"
        # Step 2: Agent prompts to call submit_report -> Model calls submit_report
        responses = [
            make_text_response("I found no bugs in this code."),
            make_tool_call_response("submit_report", mock_report),
        ]

        with patch("backend.app.agent.runner.call_chat_completion", side_effect=responses) as mock_call:
            res = await run_agent(request, store)

        assert mock_call.call_count == 2
        assert res.report.summary == "Clean code."
        assert "submit_report" in res.tools_used

    asyncio.run(_run())


def test_agent_fallback_when_model_never_calls_submit():
    async def _run():
        store = SessionStore()
        request = AgentRunRequest(
            code="a = 1",
            settings=Settings(api_key="test-key"),
        )

        # Model replies text constantly
        responses = [
            make_text_response("Text 1"),
            make_text_response("Text 2"),
            make_text_response("Text 3: ```json\n{\"summary\": \"JSON in text\", \"bugs\": [], \"explanation\": \"fine\", \"fixed_code\": \"a = 1\"}\n```"),
        ]

        with patch("backend.app.agent.runner.call_chat_completion", side_effect=responses):
            with pytest.raises(HTTPException) as error:
                await run_agent(request, store)
        assert error.value.status_code == 502
        assert "submit_report" in error.value.detail

    asyncio.run(_run())


def test_run_chat_grounded_conversation():
    async def _run():
        store = SessionStore()
        # Create initial session
        from backend.app.schemas import BugItem, Report
        report = Report(
            summary="Found 1 error",
            bugs=[BugItem(type="syntax", line=1, message="err", suggestion="fix")],
            explanation="Explain",
            fixed_code="fixed",
        )
        sid = store.create_session("def broken():", "Help me", report)

        chat_response = make_text_response("A colon tells Python that an indented block follows.")
        with patch("backend.app.agent.runner.call_chat_completion", return_value=chat_response) as mock_call:
            res = await run_chat(
                session_id=sid,
                message="Why do we need a colon?",
                settings=Settings(api_key="test-key"),
                session_store=store,
            )

        assert res.reply == "A colon tells Python that an indented block follows."
        # Verify tools was None in call_chat_completion (no code execution in follow-ups!)
        call_args = mock_call.call_args[1]
        assert call_args.get("tools") is None

    asyncio.run(_run())


def test_run_chat_invalid_session_raises_404():
    async def _run():
        store = SessionStore()
        with pytest.raises(HTTPException) as exc_info:
            await run_chat(
                session_id="non-existent-uuid",
                message="Hello",
                settings=Settings(api_key="test-key"),
                session_store=store,
            )
        assert exc_info.value.status_code == 404

    asyncio.run(_run())
