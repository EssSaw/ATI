"""Chat context estimation, budgeting, and trimming for ATI Project Chat.

Provides conservative UTF-8 token estimation (UTF-8 bytes / 3 + per-message overhead),
image fixed-cost estimation (1024 tokens), attachment handling, and history trimming
that preserves the latest user prompt, system prompt, and project context.
"""

import json
import math
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from pydantic import BaseModel, Field


DEFAULT_CONTEXT_WINDOW = 32768
MIN_CONTEXT_WINDOW = 8192
MAX_CONTEXT_WINDOW = 2000000

DEFAULT_RESPONSE_RESERVED = 4096
MIN_RESPONSE_RESERVED = 512
MAX_RESPONSE_RESERVED = 200000

IMAGE_FIXED_TOKENS = 1024
PER_MESSAGE_OVERHEAD_TOKENS = 4
MAX_TRANSPORT_MESSAGES = 500


class ContextBreakdown(BaseModel):
    system: int = Field(default=0, ge=0)
    tools: int = Field(default=0, ge=0)
    project: int = Field(default=0, ge=0)
    messages: int = Field(default=0, ge=0)
    attachments: int = Field(default=0, ge=0)
    tool_results: int = Field(default=0, ge=0)


class ChatContextInfo(BaseModel):
    window_tokens: int = Field(..., ge=MIN_CONTEXT_WINDOW, le=MAX_CONTEXT_WINDOW)
    reserved_tokens: int = Field(..., ge=MIN_RESPONSE_RESERVED, le=MAX_RESPONSE_RESERVED)
    estimated_tokens: int = Field(..., ge=0)
    usage_percent: float = Field(..., ge=0.0)
    breakdown: ContextBreakdown
    dropped_messages: int = Field(default=0, ge=0)
    estimate: bool = Field(default=True)
    compacted: bool = Field(default=False)


class ProjectChatContextResponse(BaseModel):
    context: ChatContextInfo


def estimate_text_tokens(text: Optional[str]) -> int:
    """Conservative token estimate: UTF-8 bytes divided by 3, rounded up.

    Provides a genuine, bounded approximation without claiming exact tokenizer precision.
    """
    if not text:
        return 0
    if not isinstance(text, str):
        text = str(text)
    return math.ceil(len(text.encode("utf-8", errors="replace")) / 3.0)


def estimate_image_tokens() -> int:
    """Fixed conservative estimate for an image input (1024 tokens)."""
    return IMAGE_FIXED_TOKENS


def estimate_attachment_tokens(attachment: Any) -> int:
    """Estimate tokens for an attachment.

    Images have a fixed cost of 1024 tokens.
    Raw base64 data URLs are NOT counted as text tokens.
    Text documents use conservative UTF-8 bytes / 3 of the extracted text.
    """
    if isinstance(attachment, dict):
        kind = attachment.get("kind")
        text = attachment.get("text")
    else:
        kind = getattr(attachment, "kind", None)
        text = getattr(attachment, "text", None)

    if kind == "image":
        return IMAGE_FIXED_TOKENS
    if text:
        return estimate_text_tokens(text)
    return 0


def estimate_message_tokens(message: Any) -> Tuple[int, int]:
    """Estimate (content_tokens_with_overhead, attachment_tokens) for a message.

    Returns:
        Tuple of (message_text_tokens, attachments_tokens)
    """
    if isinstance(message, dict):
        content = message.get("content", "")
        attachments = message.get("attachments", []) or []
    else:
        content = getattr(message, "content", "") or ""
        attachments = getattr(message, "attachments", []) or []

    if isinstance(content, list):
        text_tokens = 0
        extra_img_tokens = 0
        for block in content:
            if isinstance(block, dict):
                b_type = block.get("type")
                if b_type == "text":
                    text_tokens += estimate_text_tokens(block.get("text", ""))
                elif b_type == "image_url":
                    extra_img_tokens += IMAGE_FIXED_TOKENS
        msg_tokens = text_tokens + PER_MESSAGE_OVERHEAD_TOKENS
        att_tokens = extra_img_tokens
    else:
        msg_tokens = estimate_text_tokens(content) + PER_MESSAGE_OVERHEAD_TOKENS
        att_tokens = 0

    for att in attachments:
        att_tokens += estimate_attachment_tokens(att)

    return msg_tokens, att_tokens


def estimate_tools_tokens(tools: Optional[List[Dict[str, Any]]]) -> int:
    """Estimate token cost for tool definitions."""
    if not tools:
        return 0
    try:
        tools_json = json.dumps(tools, separators=(",", ":"))
    except Exception:
        tools_json = str(tools)
    return estimate_text_tokens(tools_json)


def budget_and_trim_messages(
    messages: List[Any],
    context_window: int,
    response_reserved: int,
    system_tokens: int,
    tools_tokens: int,
    project_tokens: int,
    tool_results_tokens: int = 0,
) -> Tuple[List[Any], ChatContextInfo]:
    """Validate token budget and trim oldest whole user/assistant pairs if needed.

    Preserves:
    - system prompt
    - tool definitions
    - project context
    - latest user message (and its attachments)

    If the fixed context alone exceeds (context_window - response_reserved),
    raises HTTP 400 with an actionable explanation before any LLM call.
    """
    if response_reserved >= context_window:
        raise HTTPException(
            status_code=400,
            detail="response_reserved must be strictly less than context_window.",
        )

    budget = context_window - response_reserved

    if not messages:
        raise HTTPException(status_code=400, detail="Messages list cannot be empty.")

    latest_user_message = messages[-1]
    latest_msg_tokens, latest_att_tokens = estimate_message_tokens(latest_user_message)

    fixed_required = (
        system_tokens
        + tools_tokens
        + project_tokens
        + latest_msg_tokens
        + latest_att_tokens
        + tool_results_tokens
    )

    if fixed_required > budget:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Request context exceeds available token budget: estimated {fixed_required} tokens "
                f"(system: {system_tokens}, tools: {tools_tokens}, project: {project_tokens}, "
                f"latest message: {latest_msg_tokens}, attachments: {latest_att_tokens}"
                + (f", tool results: {tool_results_tokens}" if tool_results_tokens else "")
                + f") exceeds budget of {budget} tokens (context window {context_window} "
                f"minus {response_reserved} response reserve). "
                f"Reduce project files, shorten the message, remove attachments, or increase context_window."
            ),
        )

    # Calculate tokens for history
    history = list(messages[:-1])
    history_msg_tokens = 0
    history_att_tokens = 0
    for m in history:
        mt, at = estimate_message_tokens(m)
        history_msg_tokens += mt
        history_att_tokens += at

    total_msg_tokens = history_msg_tokens + latest_msg_tokens
    total_att_tokens = history_att_tokens + latest_att_tokens
    total_tokens = (
        system_tokens
        + tools_tokens
        + project_tokens
        + total_msg_tokens
        + total_att_tokens
        + tool_results_tokens
    )

    if total_tokens <= budget:
        breakdown = ContextBreakdown(
            system=system_tokens,
            tools=tools_tokens,
            project=project_tokens,
            messages=total_msg_tokens,
            attachments=total_att_tokens,
            tool_results=tool_results_tokens,
        )
        usage_pct = round((total_tokens / context_window) * 100, 2)
        return messages, ChatContextInfo(
            window_tokens=context_window,
            reserved_tokens=response_reserved,
            estimated_tokens=total_tokens,
            usage_percent=usage_pct,
            breakdown=breakdown,
            dropped_messages=0,
            estimate=True,
            compacted=False,
        )

    # Need to trim oldest whole user/assistant pairs from history
    retained_history = list(history)
    dropped_messages = 0

    while retained_history and total_tokens > budget:
        first_role = getattr(retained_history[0], "role", None) if not isinstance(retained_history[0], dict) else retained_history[0].get("role")
        if len(retained_history) >= 2:
            second_role = getattr(retained_history[1], "role", None) if not isinstance(retained_history[1], dict) else retained_history[1].get("role")
            if first_role == "user" and second_role == "assistant":
                chunk = retained_history[:2]
            else:
                chunk = retained_history[:1]
        else:
            chunk = retained_history[:1]

        chunk_msg_tokens = 0
        chunk_att_tokens = 0
        for m in chunk:
            mt, at = estimate_message_tokens(m)
            chunk_msg_tokens += mt
            chunk_att_tokens += at

        chunk_len = len(chunk)
        dropped_messages += chunk_len
        retained_history = retained_history[chunk_len:]
        total_msg_tokens -= chunk_msg_tokens
        total_att_tokens -= chunk_att_tokens
        total_tokens = (
            system_tokens
            + tools_tokens
            + project_tokens
            + total_msg_tokens
            + total_att_tokens
            + tool_results_tokens
        )

    retained_messages = retained_history + [latest_user_message]
    breakdown = ContextBreakdown(
        system=system_tokens,
        tools=tools_tokens,
        project=project_tokens,
        messages=total_msg_tokens,
        attachments=total_att_tokens,
        tool_results=tool_results_tokens,
    )
    usage_pct = round((total_tokens / context_window) * 100, 2)

    return retained_messages, ChatContextInfo(
        window_tokens=context_window,
        reserved_tokens=response_reserved,
        estimated_tokens=total_tokens,
        usage_percent=usage_pct,
        breakdown=breakdown,
        dropped_messages=dropped_messages,
        estimate=True,
        compacted=(dropped_messages > 0),
    )


def check_and_trim_tool_loop_context(
    llm_messages: List[Dict[str, Any]],
    tool_chain_start_idx: int,
    context_window: int,
    response_reserved: int,
    system_tokens: int,
    tools_tokens: int,
    project_tokens: int,
    current_dropped_messages: int = 0,
) -> Tuple[int, int, ChatContextInfo]:
    """Check context during multi-round tool execution.

    Treats the trailing toolchain (indices tool_chain_start_idx onwards) as ATOMIC:
    never trims tool responses or tool calls from the current chain to avoid orphaning tool IDs.

    If context exceeds budget, trims older user/assistant history messages between index 2
    and tool_chain_start_idx - 1 (preserving the latest user message at tool_chain_start_idx - 1).

    If it cannot fit even with all history trimmed, raises HTTP 400.

    Returns:
        (new_tool_chain_start_idx, total_dropped_messages, current_context_info)
    """
    budget = context_window - response_reserved

    # Calculate tool results tokens from active toolchain
    tool_results_tokens = 0
    for m in llm_messages[tool_chain_start_idx:]:
        role = m.get("role")
        if role == "tool":
            tool_results_tokens += estimate_text_tokens(m.get("content", "")) + PER_MESSAGE_OVERHEAD_TOKENS
        elif role == "assistant" and m.get("tool_calls"):
            tool_results_tokens += estimate_text_tokens(json.dumps(m.get("tool_calls"), separators=(",", ":"))) + PER_MESSAGE_OVERHEAD_TOKENS

    # Calculate tokens of conversation messages (indices 2 to tool_chain_start_idx)
    conv_msg_tokens = 0
    conv_att_tokens = 0
    for m in llm_messages[2:tool_chain_start_idx]:
        c = m.get("content", "")
        if isinstance(c, list):
            for blk in c:
                if isinstance(blk, dict):
                    if blk.get("type") == "text":
                        conv_msg_tokens += estimate_text_tokens(blk.get("text", ""))
                    elif blk.get("type") == "image_url":
                        conv_att_tokens += IMAGE_FIXED_TOKENS
            conv_msg_tokens += PER_MESSAGE_OVERHEAD_TOKENS
        else:
            conv_msg_tokens += estimate_text_tokens(c) + PER_MESSAGE_OVERHEAD_TOKENS

    total_tokens = (
        system_tokens
        + tools_tokens
        + project_tokens
        + conv_msg_tokens
        + conv_att_tokens
        + tool_results_tokens
    )

    # Trim older history if needed (preserving latest user message at tool_chain_start_idx - 1)
    while total_tokens > budget and (tool_chain_start_idx - 1) > 2:
        num_history = (tool_chain_start_idx - 1) - 2
        if (
            num_history >= 2
            and llm_messages[2].get("role") == "user"
            and llm_messages[3].get("role") == "assistant"
        ):
            chunk_len = 2
        else:
            chunk_len = 1

        chunk = llm_messages[2 : 2 + chunk_len]
        for m in chunk:
            m_c = m.get("content", "")
            if isinstance(m_c, list):
                for blk in m_c:
                    if isinstance(blk, dict):
                        if blk.get("type") == "text":
                            conv_msg_tokens -= estimate_text_tokens(blk.get("text", ""))
                        elif blk.get("type") == "image_url":
                            conv_att_tokens -= IMAGE_FIXED_TOKENS
                conv_msg_tokens -= PER_MESSAGE_OVERHEAD_TOKENS
            else:
                conv_msg_tokens -= (estimate_text_tokens(m_c) + PER_MESSAGE_OVERHEAD_TOKENS)

        del llm_messages[2 : 2 + chunk_len]
        tool_chain_start_idx -= chunk_len
        current_dropped_messages += chunk_len
        total_tokens = (
            system_tokens
            + tools_tokens
            + project_tokens
            + conv_msg_tokens
            + conv_att_tokens
            + tool_results_tokens
        )

    if total_tokens > budget:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Context window exceeded during tool execution: prompt estimated at {total_tokens} tokens "
                f"exceeds available budget of {budget} tokens (context window {context_window} "
                f"minus {response_reserved} response reserve)."
            ),
        )

    breakdown = ContextBreakdown(
        system=system_tokens,
        tools=tools_tokens,
        project=project_tokens,
        messages=conv_msg_tokens,
        attachments=conv_att_tokens,
        tool_results=tool_results_tokens,
    )
    usage_pct = round((total_tokens / context_window) * 100, 2)
    context_info = ChatContextInfo(
        window_tokens=context_window,
        reserved_tokens=response_reserved,
        estimated_tokens=total_tokens,
        usage_percent=usage_pct,
        breakdown=breakdown,
        dropped_messages=current_dropped_messages,
        estimate=True,
        compacted=(current_dropped_messages > 0),
    )

    return tool_chain_start_idx, current_dropped_messages, context_info
