from typing import Any, Dict, List, Optional
import httpx
from fastapi import HTTPException


async def call_chat_completion(
    base_url: str,
    api_key: str,
    model: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    tool_choice: Optional[Any] = None,
    temperature: float = 0.2,
    timeout: float = 60.0,
) -> Dict[str, Any]:
    """Call an OpenAI-compatible /chat/completions endpoint using httpx.

    API keys are request-scoped, sent only in headers, and never saved or logged.
    Upstream response bodies and credentials are sanitized before errors are raised.
    """
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
    }
    if tools:
        payload["tools"] = tools
    if tool_choice:
        payload["tool_choice"] = tool_choice

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            if "text/event-stream" in response.headers.get("content-type", "").lower():
                raise HTTPException(status_code=502, detail="AI provider returned a streamed response despite stream=false. Check the gateway's OpenAI-compatible JSON route.")
            try:
                data = response.json()
            except ValueError:
                raise HTTPException(status_code=502, detail="AI provider returned a non-JSON response. Check the gateway URL and selected model route.")
            if isinstance(data, dict) and "error" in data:
                raise HTTPException(status_code=502, detail="AI provider reported an error inside an HTTP 200 response. Check the selected model's upstream connection and quota in the gateway.")
            try:
                message = data["choices"][0]["message"]
                if not isinstance(message, dict):
                    raise ValueError()
            except (ValueError, KeyError, IndexError, TypeError):
                raise HTTPException(status_code=502, detail="AI provider returned an invalid chat response.")
            return data
        except httpx.HTTPStatusError as exc:
            # Sanitize upstream error; do not expose internal body/tokens
            status = exc.response.status_code
            safe_detail = f"AI Provider returned HTTP {status}."
            raise HTTPException(status_code=status if status in (400, 401, 403, 404, 429) else 502, detail=safe_detail)
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="AI Provider request timed out.")
        except httpx.RequestError as exc:
            raise HTTPException(status_code=502, detail=f"Failed to communicate with AI Provider: {type(exc).__name__}")
