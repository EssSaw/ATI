import json
import time
from typing import Any, Dict, List, Optional, Set
from fastapi import HTTPException

from ..config import resolve_settings
from ..schemas import (
    AgentChatResponse,
    AgentRunRequest,
    AgentRunResponse,
    Report,
    Settings,
    TraceItem,
)
from ..sessions import SessionStore
from ..tools import TOOL_DEFINITIONS, execute_tool
from .llm_client import call_chat_completion
from .prompts import SYSTEM_PROMPT_AGENT, build_chat_system_prompt

MAX_STEPS = 6
MAX_TEXT_RETRIES = 2


async def run_agent(request: AgentRunRequest, session_store: SessionStore) -> AgentRunResponse:
    """Run the bounded multi-step function calling agent on the submitted code."""
    start_time = time.perf_counter()
    resolved = resolve_settings(request.settings)

    trace: List[TraceItem] = []
    tools_used: Set[str] = set()
    final_report: Optional[Report] = None

    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT_AGENT},
        {
            "role": "user",
            "content": (
                f"Please analyze this Python code for bugs and quality issues.\n\n"
                f"```python\n{request.code}\n```\n\n"
                f"Student Question: {request.question or 'Please check my code for errors and explain how to fix them.'}"
            ),
        },
    ]

    text_retries = 0

    for step in range(MAX_STEPS):
        response = await call_chat_completion(
            base_url=resolved.base_url,
            api_key=resolved.api_key,
            model=resolved.model,
            messages=messages,
            tools=TOOL_DEFINITIONS,
        )

        choice = response["choices"][0]
        message = choice["message"]
        tool_calls = message.get("tool_calls")

        if tool_calls:
            messages.append(message)
            if len(tool_calls) > 8:
                raise HTTPException(status_code=502, detail="Model requested too many tool calls.")
            for tc in tool_calls:
                func = tc.get("function", {})
                name = func.get("name", "")
                args = func.get("arguments", "{}")
                tool_call_id = tc.get("id", f"call_{int(time.time()*1000)}")

                out_str, trace_item, is_submit, report_obj = execute_tool(
                    tool_name=name,
                    raw_arguments=args,
                    request_code=request.code,
                    enable_code_execution=request.settings.enable_code_execution,
                )

                trace.append(trace_item)
                tools_used.add(name)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "name": name,
                    "content": out_str,
                })

                if is_submit and report_obj:
                    final_report = report_obj
                    break

            if final_report:
                break
        else:
            # Model returned plain text instead of tool call
            content = message.get("content") or ""
            messages.append({"role": "assistant", "content": content})

            if text_retries < MAX_TEXT_RETRIES:
                text_retries += 1
                messages.append({
                    "role": "user",
                    "content": (
                        "You returned a text response instead of calling the required 'submit_report' tool. "
                        "Please call 'submit_report' with your complete analysis report containing summary, "
                        "bugs, explanation, and fixed_code."
                    ),
                })
            else:
                raise HTTPException(
                    status_code=502,
                    detail="The model did not call submit_report. Select a tool-capable model and retry.",
                )

    if final_report is None:
        raise HTTPException(status_code=502, detail="Agent step limit reached without a completed report.")

    latency_ms = (time.perf_counter() - start_time) * 1000.0
    session_id = session_store.create_session(
        code=request.code,
        question=request.question,
        report=final_report,
    )

    return AgentRunResponse(
        session_id=session_id,
        report=final_report,
        trace=trace,
        tools_used=sorted(list(tools_used)),
        latency_ms=round(latency_ms, 2),
    )


async def run_chat(
    session_id: str,
    message: str,
    settings: Settings,
    session_store: SessionStore,
) -> AgentChatResponse:
    """Handle follow-up student chat questions grounded in the original review context.

    No tools or code execution are permitted during follow-up chat.
    """
    session = session_store.get_session(session_id)
    if session is None:
        raise HTTPException(
            status_code=404,
            detail="Session not found or has expired. Please run a new analysis.",
        )

    resolved = resolve_settings(settings)

    system_prompt = build_chat_system_prompt(
        code=session.code,
        question=session.question,
        summary=session.report.summary,
        explanation=session.report.explanation,
        fixed_code=session.report.fixed_code,
    )

    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
    ]
    # Replay previous follow-up conversation turns
    for m in session.messages:
        messages.append({"role": m["role"], "content": m["content"]})

    # Add current student message
    messages.append({"role": "user", "content": message})

    response = await call_chat_completion(
        base_url=resolved.base_url,
        api_key=resolved.api_key,
        model=resolved.model,
        messages=messages,
        tools=None,  # No tools permitted in chat!
    )

    reply = (response["choices"][0]["message"].get("content") or "").strip()
    if not reply:
        raise HTTPException(status_code=502, detail="Model returned an empty chat reply.")

    # Save to session history
    session_store.add_message(session_id, "user", message)
    session_store.add_message(session_id, "assistant", reply)

    return AgentChatResponse(reply=reply)
