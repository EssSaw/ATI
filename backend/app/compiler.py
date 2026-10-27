import asyncio
import base64
import ipaddress
import json as json_module
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

try:
    from .config import validate_base_url
except ImportError:
    from backend.app.config import validate_base_url

router = APIRouter(prefix="/agent/compiler", tags=["compiler"])

MAX_SOURCE_LENGTH = 50000
MAX_STDIN_LENGTH = 20000
MAX_RESPONSE_BYTES = 2 * 1024 * 1024  # 2MB response cap
MAX_OUTPUT_BUDGET = 65536  # 64KB collective output cap
DEFAULT_MAX_POLLS = 20
DEFAULT_POLL_INTERVAL = 0.3
DEFAULT_TOTAL_TIMEOUT = 30.0
DEFAULT_REQUEST_TIMEOUT = 10.0

MAX_CONCURRENT_RUNS = 2
RUN_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_RUNS)

TOKEN_REGEX = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")


def reset_semaphore() -> None:
    """Reset the module-level concurrency semaphore for testing or clean restarts."""
    global RUN_SEMAPHORE
    RUN_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_RUNS)


def is_loopback_host(hostname: Optional[str]) -> bool:
    """Check if the given hostname is a local loopback address."""
    if not hostname:
        return False
    host_lower = hostname.lower()
    if host_lower in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        ip = ipaddress.ip_address(host_lower)
        return ip.is_loopback
    except ValueError:
        return False


class CompilerSettings(BaseModel):
    base_url: str = Field(..., description="Judge0 base URL")
    api_key: str = Field(..., repr=False, description="Judge0 API key / auth token")
    rapidapi_host: Optional[str] = Field(default="", description="RapidAPI host header")


class CompilerLanguagesRequest(BaseModel):
    settings: CompilerSettings = Field(..., description="Compiler service settings")


class LanguageItem(BaseModel):
    id: int
    name: str


class LanguagesResponse(BaseModel):
    languages: List[LanguageItem]


class CompilerRunRequest(BaseModel):
    language_id: int = Field(..., gt=0, description="Judge0 language ID")
    source: str = Field(..., min_length=1, max_length=MAX_SOURCE_LENGTH, description="Source code")
    stdin: str = Field(default="", max_length=MAX_STDIN_LENGTH, description="Standard input")
    settings: CompilerSettings = Field(..., description="Compiler service settings")


class StatusItem(BaseModel):
    id: int
    description: str


class CompilerRunResponse(BaseModel):
    status: StatusItem
    stdout: str = ""
    stderr: str = ""
    compile_output: str = ""
    message: str = ""
    exit_code: Optional[int] = None
    time: Optional[str] = None
    memory: Optional[int] = None
    truncated: bool = False


def resolve_compiler_target(settings: CompilerSettings) -> Tuple[str, Dict[str, str]]:
    """Validate request-scoped Judge0 settings and construct base URL and upstream headers."""
    if not settings.api_key or not settings.api_key.strip():
        raise HTTPException(
            status_code=400,
            detail="API key must not be blank.",
        )
    if "\r" in settings.api_key or "\n" in settings.api_key:
        raise HTTPException(
            status_code=400,
            detail="API key must not contain newline characters.",
        )

    try:
        clean_url = validate_base_url(settings.base_url)
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid compiler base URL: {str(e)}",
        )

    parsed = urlparse(clean_url)
    hostname = (parsed.hostname or "").lower()

    if parsed.scheme == "http":
        if not is_loopback_host(hostname):
            raise HTTPException(
                status_code=400,
                detail="HTTP is only allowed for loopback addresses. Remote Judge0 URLs must use HTTPS.",
            )
    elif parsed.scheme != "https":
        raise HTTPException(
            status_code=400,
            detail=f"Invalid URL scheme '{parsed.scheme}'. Only http and https are allowed.",
        )

    rapidapi_host = (settings.rapidapi_host or "").strip()
    headers: Dict[str, str] = {"Accept": "application/json"}

    if rapidapi_host:
        if "\r" in rapidapi_host or "\n" in rapidapi_host:
            raise HTTPException(
                status_code=400,
                detail="RapidAPI host must not contain newline characters.",
            )
        target_host = hostname
        target_netloc = (parsed.netloc or "").lower()
        if rapidapi_host.lower() != target_host and rapidapi_host.lower() != target_netloc:
            raise HTTPException(
                status_code=400,
                detail="RapidAPI host does not match base URL hostname.",
            )
        headers["X-RapidAPI-Host"] = rapidapi_host
        headers["X-RapidAPI-Key"] = settings.api_key.strip()
    else:
        headers["X-Auth-Token"] = settings.api_key.strip()

    return clean_url, headers


def _decode_b64(val: Any) -> Optional[str]:
    """Decode a nullable base64-encoded string using UTF-8 with error replacement."""
    if val is None:
        return None
    if not isinstance(val, str):
        raise HTTPException(
            status_code=502,
            detail="Upstream compiler service returned non-string output field.",
        )
    if not val:
        return ""
    try:
        raw_bytes = base64.b64decode(val.encode("ascii"), validate=True)
    except Exception:
        raise HTTPException(
            status_code=502,
            detail="Upstream compiler service returned invalid base64 data.",
        )
    return raw_bytes.decode("utf-8", errors="replace")


async def _fetch_upstream(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    json_data: Optional[Any] = None,
) -> Any:
    """Make bounded upstream HTTP request with streaming response size enforcement."""
    try:
        async with client.stream(
            method,
            url,
            headers=headers,
            json=json_data,
        ) as response:
            content_length = response.headers.get("content-length")
            if content_length:
                try:
                    if int(content_length) > MAX_RESPONSE_BYTES:
                        raise HTTPException(
                            status_code=502,
                            detail="Upstream compiler response exceeded size limit.",
                        )
                except ValueError:
                    pass

            chunks = []
            total_bytes = 0
            async for chunk in response.aiter_bytes():
                total_bytes += len(chunk)
                if total_bytes > MAX_RESPONSE_BYTES:
                    raise HTTPException(
                        status_code=502,
                        detail="Upstream compiler response exceeded size limit.",
                    )
                chunks.append(chunk)

            raw_bytes = b"".join(chunks)
            status = response.status_code

            if status < 200 or status >= 300:
                if status in (401, 403):
                    raise HTTPException(
                        status_code=status,
                        detail=f"Compiler authentication failed (HTTP {status}).",
                    )
                elif status == 429:
                    raise HTTPException(
                        status_code=429,
                        detail="Compiler service rate limit exceeded.",
                    )
                else:
                    raise HTTPException(
                        status_code=502,
                        detail=f"Upstream compiler service returned error (HTTP {status}).",
                    )

            try:
                return json_module.loads(raw_bytes.decode("utf-8"))
            except Exception:
                raise HTTPException(
                    status_code=502,
                    detail="Upstream compiler service returned invalid JSON.",
                )
    except HTTPException:
        raise
    except httpx.TimeoutException:
        raise HTTPException(
            status_code=504,
            detail="Compiler service request timed out.",
        )
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in (401, 403):
            raise HTTPException(
                status_code=status,
                detail=f"Compiler authentication failed (HTTP {status}).",
            )
        elif status == 429:
            raise HTTPException(
                status_code=429,
                detail="Compiler service rate limit exceeded.",
            )
        else:
            raise HTTPException(
                status_code=502,
                detail=f"Upstream compiler service returned error (HTTP {status}).",
            )
    except httpx.RequestError:
        raise HTTPException(
            status_code=502,
            detail="Could not connect to compiler service.",
        )


@router.get("/languages", response_model=LanguagesResponse)
async def get_languages_legacy() -> LanguagesResponse:
    """Legacy endpoint: Instruct client to configure request-scoped settings via UI."""
    raise HTTPException(
        status_code=503,
        detail="Compiler service requires request-scoped settings. Use POST /agent/compiler/languages with settings.",
    )


@router.post("/languages", response_model=LanguagesResponse)
async def get_languages(request: CompilerLanguagesRequest) -> LanguagesResponse:
    """Fetch available programming languages from configured Judge0-compatible server."""
    base_url, headers = resolve_compiler_target(request.settings)

    url = f"{base_url}/languages"
    req_timeout = float(os.getenv("COMPILER_REQUEST_TIMEOUT", str(DEFAULT_REQUEST_TIMEOUT)))
    async with httpx.AsyncClient(timeout=req_timeout, follow_redirects=False) as client:
        data = await _fetch_upstream(client, "GET", url, headers=headers)

    if not isinstance(data, list):
        raise HTTPException(
            status_code=502,
            detail="Upstream compiler service returned invalid language catalog.",
        )

    valid_languages: List[LanguageItem] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        lang_id = item.get("id")
        lang_name = item.get("name")
        if isinstance(lang_id, int) and lang_id > 0 and isinstance(lang_name, str) and lang_name.strip():
            valid_languages.append(LanguageItem(id=lang_id, name=lang_name.strip()))

    valid_languages = list({item.id: item for item in valid_languages}.values())
    valid_languages.sort(key=lambda x: x.id)
    return LanguagesResponse(languages=valid_languages)


@router.post("/run", response_model=CompilerRunResponse)
async def run_code(request: CompilerRunRequest) -> CompilerRunResponse:
    """Submit source code to Judge0-compatible server and poll for execution results."""
    concurrency_timeout = float(os.getenv("COMPILER_CONCURRENCY_TIMEOUT", "0.5"))
    try:
        await asyncio.wait_for(RUN_SEMAPHORE.acquire(), timeout=concurrency_timeout)
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=429,
            detail="Compiler service is currently busy. Please try again shortly.",
        )

    try:
        base_url, headers = resolve_compiler_target(request.settings)
        post_headers = {**headers, "Content-Type": "application/json"}

        submission_body = {
            "source_code": base64.b64encode(request.source.encode("utf-8")).decode("ascii"),
            "language_id": request.language_id,
            "stdin": base64.b64encode((request.stdin or "").encode("utf-8")).decode("ascii"),
            "cpu_time_limit": 2.0,
            "wall_time_limit": 5.0,
            "memory_limit": 128000,
            "max_file_size": 1024,
            "enable_network": False,
        }

        submit_url = f"{base_url}/submissions?base64_encoded=true&wait=false"
        req_timeout = float(os.getenv("COMPILER_REQUEST_TIMEOUT", str(DEFAULT_REQUEST_TIMEOUT)))

        async with httpx.AsyncClient(timeout=req_timeout, follow_redirects=False) as client:
            resp_data = await _fetch_upstream(
                client,
                "POST",
                submit_url,
                headers=post_headers,
                json_data=submission_body,
            )

            if not isinstance(resp_data, dict) or "token" not in resp_data:
                raise HTTPException(
                    status_code=502,
                    detail="Upstream compiler service returned invalid submission response.",
                )

            token = resp_data.get("token")
            if not isinstance(token, str) or not TOKEN_REGEX.match(token):
                raise HTTPException(
                    status_code=502,
                    detail="Upstream compiler service returned invalid submission token.",
                )

            poll_url = (
                f"{base_url}/submissions/{token}"
                "?base64_encoded=true&fields=status,stdout,stderr,compile_output,message,exit_code,time,memory"
            )

            max_polls = int(os.getenv("COMPILER_MAX_POLLS", str(DEFAULT_MAX_POLLS)))
            poll_interval = float(os.getenv("COMPILER_POLL_INTERVAL", str(DEFAULT_POLL_INTERVAL)))
            total_timeout = float(os.getenv("COMPILER_TOTAL_TIMEOUT", str(DEFAULT_TOTAL_TIMEOUT)))

            start_time = time.monotonic()
            final_data = None

            for poll_idx in range(max_polls):
                if time.monotonic() - start_time >= total_timeout:
                    break

                poll_resp = await _fetch_upstream(client, "GET", poll_url, headers=headers)
                if not isinstance(poll_resp, dict):
                    raise HTTPException(
                        status_code=502,
                        detail="Upstream compiler service returned invalid poll response.",
                    )

                status_obj = poll_resp.get("status")
                if not isinstance(status_obj, dict):
                    raise HTTPException(
                        status_code=502,
                        detail="Upstream compiler service returned invalid status object.",
                    )

                status_id = status_obj.get("id")
                if not isinstance(status_id, int):
                    raise HTTPException(
                        status_code=502,
                        detail="Upstream compiler service returned invalid status ID.",
                    )

                if status_id > 2:
                    final_data = poll_resp
                    break

                if poll_idx < max_polls - 1:
                    if time.monotonic() - start_time + poll_interval >= total_timeout:
                        break
                    await asyncio.sleep(poll_interval)

            if final_data is None:
                raise HTTPException(
                    status_code=504,
                    detail="Compiler execution timed out waiting for results.",
                )

            status_dict = final_data.get("status", {})
            status_id = status_dict.get("id")
            status_desc = str(status_dict.get("description", ""))

            stdout_text = _decode_b64(final_data.get("stdout"))
            stderr_text = _decode_b64(final_data.get("stderr"))
            compile_output_text = _decode_b64(final_data.get("compile_output"))
            message_text = _decode_b64(final_data.get("message"))

            total_output_chars = sum(
                len(t)
                for t in (compile_output_text, stdout_text, stderr_text, message_text)
                if t is not None
            )

            truncated = False
            if total_output_chars > MAX_OUTPUT_BUDGET:
                truncated = True
                remaining = MAX_OUTPUT_BUDGET

                def _cap(text: Optional[str]) -> Optional[str]:
                    nonlocal remaining
                    if text is None:
                        return None
                    if len(text) > remaining:
                        res = text[:remaining]
                        remaining = 0
                        return res
                    remaining -= len(text)
                    return text

                compile_output_text = _cap(compile_output_text)
                stdout_text = _cap(stdout_text)
                stderr_text = _cap(stderr_text)
                message_text = _cap(message_text)

            exit_code = final_data.get("exit_code")
            if exit_code is not None and not isinstance(exit_code, int):
                try:
                    exit_code = int(exit_code)
                except (ValueError, TypeError):
                    exit_code = None

            time_val = final_data.get("time")
            if time_val is not None:
                time_val = str(time_val)

            memory_val = final_data.get("memory")
            if memory_val is not None and not isinstance(memory_val, int):
                try:
                    memory_val = int(memory_val)
                except (ValueError, TypeError):
                    memory_val = None

            return CompilerRunResponse(
                status=StatusItem(id=status_id, description=status_desc),
                stdout=stdout_text or "",
                stderr=stderr_text or "",
                compile_output=compile_output_text or "",
                message=message_text or "",
                exit_code=exit_code,
                time=time_val,
                memory=memory_val,
                truncated=truncated,
            )
    finally:
        RUN_SEMAPHORE.release()
