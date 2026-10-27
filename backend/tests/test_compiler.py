import asyncio
import base64
from urllib.parse import urlsplit
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from backend.app.compiler import (
    MAX_OUTPUT_BUDGET,
    CompilerRunRequest,
    CompilerSettings,
    is_loopback_host,
    reset_semaphore,
    router as compiler_router,
)

local_app = FastAPI()


@local_app.middleware("http")
async def local_request_guard(request: Request, call_next):
    allowed_hosts = {"localhost", "127.0.0.1", "::1", "testserver"}
    if request.url.hostname not in allowed_hosts:
        return JSONResponse(status_code=403, content={"detail": "Local application access only."})
    origin = request.headers.get("origin")
    if origin:
        try:
            parsed = urlsplit(origin)
            valid_origin = (
                parsed.scheme in {"http", "https"}
                and parsed.hostname in allowed_hosts - {"testserver"}
                and not parsed.username
                and not parsed.password
            )
        except ValueError:
            valid_origin = False
        if not valid_origin:
            return JSONResponse(status_code=403, content={"detail": "Untrusted browser origin."})
    if request.headers.get("sec-fetch-site") == "cross-site":
        return JSONResponse(status_code=403, content={"detail": "Cross-site requests are not allowed."})
    if (
        request.method in {"POST", "PUT", "PATCH", "DELETE"}
        and request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json"
    ):
        return JSONResponse(status_code=415, content={"detail": "Application/json is required."})
    return await call_next(request)


@local_app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = exc.errors()
    msg = "; ".join([f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']}" for err in errors])
    return JSONResponse(status_code=422, content={"detail": f"Validation error: {msg}"})


local_app.include_router(compiler_router)


DEFAULT_SETTINGS = {
    "base_url": "https://judge0.example.com",
    "api_key": "test-auth-token",
    "rapidapi_host": "",
}


@pytest.fixture(autouse=True)
def clean_concurrency():
    reset_semaphore()
    yield
    reset_semaphore()


@pytest.fixture
def client():
    return TestClient(local_app)


def mock_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=10.0, follow_redirects=False)


# --- Loopback Host Helper Tests ---


def test_is_loopback_host():
    assert is_loopback_host("localhost") is True
    assert is_loopback_host("127.0.0.1") is True
    assert is_loopback_host("127.0.0.2") is True
    assert is_loopback_host("::1") is True
    assert is_loopback_host("judge0.example.com") is False
    assert is_loopback_host("192.168.1.1") is False
    assert is_loopback_host("") is False
    assert is_loopback_host(None) is False


# --- Legacy GET /languages Test ---


def test_get_languages_legacy_returns_503(client):
    resp = client.get("/agent/compiler/languages")
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert "Compiler service requires request-scoped settings" in detail
    assert "POST /agent/compiler/languages" in detail


# --- Settings Contract & Validation Tests ---


def test_missing_settings_in_languages_post(client):
    resp = client.post("/agent/compiler/languages", json={})
    assert resp.status_code == 422
    assert "Validation error" in resp.json()["detail"]


def test_missing_settings_in_run_post(client):
    resp = client.post("/agent/compiler/run", json={"language_id": 71, "source": "print(1)"})
    assert resp.status_code == 422
    assert "Validation error" in resp.json()["detail"]


@pytest.mark.parametrize(
    "blank_key",
    ["", "   ", "	"],
)
def test_blank_api_key_rejected(client, blank_key):
    settings = {"base_url": "https://judge0.example.com", "api_key": blank_key}
    resp = client.post("/agent/compiler/languages", json={"settings": settings})
    assert resp.status_code == 400
    assert "API key must not be blank" in resp.json()["detail"]

    run_resp = client.post(
        "/agent/compiler/run",
        json={"language_id": 71, "source": "print(1)", "settings": settings},
    )
    assert run_resp.status_code == 400
    assert "API key must not be blank" in run_resp.json()["detail"]


def test_api_key_newline_rejected(client):
    settings = {"base_url": "https://judge0.example.com", "api_key": "token\nInjected: yes"}
    resp = client.post("/agent/compiler/languages", json={"settings": settings})
    assert resp.status_code == 400
    assert "API key must not contain newline characters" in resp.json()["detail"]


def test_rapidapi_host_newline_rejected(client):
    settings = {
        "base_url": "https://judge0-ce.p.rapidapi.com",
        "api_key": "valid-key",
        "rapidapi_host": "judge0-ce.p.rapidapi.com\r\nInjected: yes",
    }
    resp = client.post("/agent/compiler/languages", json={"settings": settings})
    assert resp.status_code == 400
    assert "RapidAPI host must not contain newline characters" in resp.json()["detail"]


@pytest.mark.parametrize(
    "invalid_url",
    [
        "ftp://judge0.example.com",
        "http://169.254.169.254/v1",
        "https://judge0.example.com?query=forbidden",
        "https://judge0.example.com#fragment",
        "******judge0.example.com",
        "https://user:pass@judge0.example.com",
    ],
)
def test_invalid_compiler_base_url(client, invalid_url):
    settings = {"base_url": invalid_url, "api_key": "some-key"}
    resp = client.post("/agent/compiler/languages", json={"settings": settings})
    assert resp.status_code == 400
    assert "Invalid compiler base URL" in resp.json()["detail"]

    run_resp = client.post(
        "/agent/compiler/run",
        json={"language_id": 71, "source": "print(1)", "settings": settings},
    )
    assert run_resp.status_code == 400
    assert "Invalid compiler base URL" in run_resp.json()["detail"]


def test_remote_http_rejected(client):
    settings = {"base_url": "http://judge0.example.com", "api_key": "secret"}
    resp = client.post("/agent/compiler/languages", json={"settings": settings})
    assert resp.status_code == 400
    assert "HTTP is only allowed for loopback addresses" in resp.json()["detail"]


def test_loopback_http_allowed(client):
    def handler(request):
        assert request.url.scheme == "http"
        assert request.url.host in ("localhost", "127.0.0.1")
        return httpx.Response(200, json=[{"id": 71, "name": "Python"}])

    for loopback_url in ["http://localhost:2358", "http://127.0.0.1:2358"]:
        settings = {"base_url": loopback_url, "api_key": "local-key"}
        with patch("backend.app.compiler.httpx.AsyncClient", return_value=mock_client(handler)):
            resp = client.post("/agent/compiler/languages", json={"settings": settings})
        assert resp.status_code == 200
        assert resp.json()["languages"][0]["name"] == "Python"


def test_rapidapi_host_mismatch_rejected(client):
    settings = {
        "base_url": "https://judge0.example.com",
        "api_key": "secret-key",
        "rapidapi_host": "judge0-ce.p.rapidapi.com",
    }
    resp = client.post("/agent/compiler/languages", json={"settings": settings})
    assert resp.status_code == 400
    assert "RapidAPI host does not match base URL hostname" in resp.json()["detail"]


def test_rapidapi_host_matching_success(client):
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json=[{"id": 71, "name": "Python"}])

    settings = {
        "base_url": "https://judge0-ce.p.rapidapi.com",
        "api_key": "rapid-secret-key",
        "rapidapi_host": "judge0-ce.p.rapidapi.com",
    }
    with patch("backend.app.compiler.httpx.AsyncClient", return_value=mock_client(handler)):
        resp = client.post("/agent/compiler/languages", json={"settings": settings})

    assert resp.status_code == 200
    assert len(captured) == 1
    assert captured[0].headers.get("X-RapidAPI-Host") == "judge0-ce.p.rapidapi.com"
    assert captured[0].headers.get("X-RapidAPI-Key") == "rapid-secret-key"
    assert "X-Auth-Token" not in captured[0].headers


# --- POST /languages Catalog Tests ---


def test_get_languages_success(client):
    def handler(request):
        assert request.method == "GET"
        assert str(request.url) == "https://judge0.example.com/languages"
        return httpx.Response(
            200,
            json=[
                {"id": 71, "name": "Python (3.8.1)"},
                {"id": 63, "name": "JavaScript (Node.js 12.14.0)"},
                {"id": 54, "name": "C++ (GCC 9.2.0)"},
            ],
        )

    with patch("backend.app.compiler.httpx.AsyncClient", return_value=mock_client(handler)):
        resp = client.post("/agent/compiler/languages", json={"settings": DEFAULT_SETTINGS})

    assert resp.status_code == 200
    data = resp.json()
    assert "languages" in data
    ids = [item["id"] for item in data["languages"]]
    assert ids == [54, 63, 71]
    assert data["languages"][0]["name"] == "C++ (GCC 9.2.0)"


def test_get_languages_filters_invalid_items(client):
    def handler(request):
        return httpx.Response(
            200,
            json=[
                {"id": 71, "name": "Python"},
                "not-a-dict",
                {"id": -5, "name": "Invalid ID"},
                {"id": 0, "name": "Zero ID"},
                {"id": "not-int", "name": "String ID"},
                {"id": 63, "name": ""},
                {"id": 64, "name": "   "},
                {"id": 65, "name": None},
                {"id": 50, "name": "C"},
            ],
        )

    with patch("backend.app.compiler.httpx.AsyncClient", return_value=mock_client(handler)):
        resp = client.post("/agent/compiler/languages", json={"settings": DEFAULT_SETTINGS})

    assert resp.status_code == 200
    data = resp.json()["languages"]
    assert len(data) == 2
    assert data[0] == {"id": 50, "name": "C"}
    assert data[1] == {"id": 71, "name": "Python"}


def test_get_languages_upstream_invalid_json(client):
    def handler(request):
        return httpx.Response(200, content=b"this is not json")

    with patch("backend.app.compiler.httpx.AsyncClient", return_value=mock_client(handler)):
        resp = client.post("/agent/compiler/languages", json={"settings": DEFAULT_SETTINGS})

    assert resp.status_code == 502
    assert "invalid JSON" in resp.json()["detail"]


def test_get_languages_upstream_non_list(client):
    def handler(request):
        return httpx.Response(200, json={"error": "not a list"})

    with patch("backend.app.compiler.httpx.AsyncClient", return_value=mock_client(handler)):
        resp = client.post("/agent/compiler/languages", json={"settings": DEFAULT_SETTINGS})

    assert resp.status_code == 502
    assert "invalid language catalog" in resp.json()["detail"]


@pytest.mark.parametrize(
    "upstream_status,expected_status",
    [
        (401, 401),
        (403, 403),
        (429, 429),
        (500, 502),
        (503, 502),
        (404, 502),
        (302, 502),
    ],
)
def test_get_languages_sanitizes_errors(client, upstream_status, expected_status):
    def handler(request):
        return httpx.Response(upstream_status, json={"secret_leak": "dont_expose"})

    with patch("backend.app.compiler.httpx.AsyncClient", return_value=mock_client(handler)):
        resp = client.post("/agent/compiler/languages", json={"settings": DEFAULT_SETTINGS})

    assert resp.status_code == expected_status
    assert "dont_expose" not in resp.text


def test_get_languages_timeout(client):
    def handler(request):
        raise httpx.ReadTimeout("timeout")

    with patch("backend.app.compiler.httpx.AsyncClient", return_value=mock_client(handler)):
        resp = client.post("/agent/compiler/languages", json={"settings": DEFAULT_SETTINGS})

    assert resp.status_code == 504
    assert "timed out" in resp.json()["detail"]


def test_get_languages_connect_error(client):
    def handler(request):
        raise httpx.ConnectError("connection refused")

    with patch("backend.app.compiler.httpx.AsyncClient", return_value=mock_client(handler)):
        resp = client.post("/agent/compiler/languages", json={"settings": DEFAULT_SETTINGS})

    assert resp.status_code == 502
    assert "Could not connect" in resp.json()["detail"]


# --- POST /run: Headers & Payload Tests ---


def test_run_auth_headers_direct_judge0(client):
    captured_requests = []

    def handler(request):
        captured_requests.append(request)
        if request.method == "POST":
            return httpx.Response(201, json={"token": "test-uuid-token"})
        return httpx.Response(
            200,
            json={
                "status": {"id": 3, "description": "Accepted"},
                "stdout": base64.b64encode(b"ok\n").decode("ascii"),
                "stderr": None,
                "compile_output": None,
                "message": None,
                "exit_code": 0,
                "time": "0.01",
                "memory": 1000,
            },
        )

    settings = {
        "base_url": "https://judge0.example.com",
        "api_key": "secret-auth-token",
        "rapidapi_host": "",
    }
    with patch("backend.app.compiler.httpx.AsyncClient", return_value=mock_client(handler)):
        resp = client.post(
            "/agent/compiler/run",
            json={"language_id": 71, "source": "print('ok')", "settings": settings},
        )

    assert resp.status_code == 200
    assert len(captured_requests) >= 2
    post_req = captured_requests[0]
    assert post_req.headers.get("X-Auth-Token") == "secret-auth-token"
    assert "X-RapidAPI-Key" not in post_req.headers
    assert "X-RapidAPI-Host" not in post_req.headers


def test_run_auth_headers_rapidapi(client):
    captured_requests = []

    def handler(request):
        captured_requests.append(request)
        if request.method == "POST":
            return httpx.Response(201, json={"token": "test-uuid-token"})
        return httpx.Response(
            200,
            json={
                "status": {"id": 3, "description": "Accepted"},
                "stdout": base64.b64encode(b"ok\n").decode("ascii"),
                "stderr": None,
                "compile_output": None,
                "message": None,
                "exit_code": 0,
                "time": "0.01",
                "memory": 1000,
            },
        )

    settings = {
        "base_url": "https://judge0-ce.p.rapidapi.com",
        "api_key": "rapid-secret-key",
        "rapidapi_host": "judge0-ce.p.rapidapi.com",
    }
    with patch("backend.app.compiler.httpx.AsyncClient", return_value=mock_client(handler)):
        resp = client.post(
            "/agent/compiler/run",
            json={"language_id": 71, "source": "print('ok')", "settings": settings},
        )

    assert resp.status_code == 200
    post_req = captured_requests[0]
    assert post_req.headers.get("X-RapidAPI-Key") == "rapid-secret-key"
    assert post_req.headers.get("X-RapidAPI-Host") == "judge0-ce.p.rapidapi.com"
    assert "X-Auth-Token" not in post_req.headers


def test_run_submits_expected_payload(client):
    captured_requests = []

    def handler(request):
        captured_requests.append(request)
        if request.method == "POST":
            return httpx.Response(201, json={"token": "test-uuid-token"})
        return httpx.Response(
            200,
            json={
                "status": {"id": 3, "description": "Accepted"},
                "stdout": base64.b64encode(b"hello 42\n").decode("ascii"),
                "stderr": None,
                "compile_output": None,
                "message": None,
                "exit_code": 0,
                "time": "0.02",
                "memory": 2048,
            },
        )

    with patch("backend.app.compiler.httpx.AsyncClient", return_value=mock_client(handler)):
        resp = client.post(
            "/agent/compiler/run",
            json={
                "language_id": 71,
                "source": "import sys\nprint(sys.stdin.read().strip())",
                "stdin": "42\n",
                "settings": DEFAULT_SETTINGS,
            },
        )

    assert resp.status_code == 200
    post_req = captured_requests[0]
    assert str(post_req.url) == "https://judge0.example.com/submissions?base64_encoded=true&wait=false"
    assert post_req.headers.get("content-type") == "application/json"
    assert post_req.headers.get("accept") == "application/json"

    import json

    body = json.loads(post_req.content.decode("utf-8"))
    assert body["language_id"] == 71
    assert base64.b64decode(body["source_code"]).decode("utf-8") == "import sys\nprint(sys.stdin.read().strip())"
    assert base64.b64decode(body["stdin"]).decode("utf-8") == "42\n"
    assert body["cpu_time_limit"] == 2.0
    assert body["wall_time_limit"] == 5.0
    assert body["memory_limit"] == 128000
    assert body["max_file_size"] == 1024
    assert body["enable_network"] is False


# --- Polling & Outcomes Tests ---


def test_run_polling_progression_success(client):
    poll_count = 0

    def handler(request):
        nonlocal poll_count
        if request.method == "POST":
            return httpx.Response(201, json={"token": "progression-token-123"})
        poll_count += 1
        if poll_count == 1:
            return httpx.Response(200, json={"status": {"id": 1, "description": "In Queue"}})
        if poll_count == 2:
            return httpx.Response(200, json={"status": {"id": 2, "description": "Processing"}})
        return httpx.Response(
            200,
            json={
                "status": {"id": 3, "description": "Accepted"},
                "stdout": base64.b64encode(b"Success!\n").decode("ascii"),
                "stderr": None,
                "compile_output": None,
                "message": None,
                "exit_code": 0,
                "time": "0.035",
                "memory": 1540,
            },
        )

    with patch.dict("os.environ", {"COMPILER_POLL_INTERVAL": "0.001"}):
        with patch("backend.app.compiler.httpx.AsyncClient", return_value=mock_client(handler)):
            resp = client.post(
                "/agent/compiler/run",
                json={"language_id": 71, "source": "print('Success!')", "settings": DEFAULT_SETTINGS},
            )

    assert resp.status_code == 200
    res = resp.json()
    assert res["status"] == {"id": 3, "description": "Accepted"}
    assert res["stdout"] == "Success!\n"
    assert res["stderr"] == ""
    assert res["compile_output"] == ""
    assert res["exit_code"] == 0
    assert res["time"] == "0.035"
    assert res["memory"] == 1540
    assert res["truncated"] is False
    assert poll_count == 3


def test_run_compilation_error(client):
    def handler(request):
        if request.method == "POST":
            return httpx.Response(201, json={"token": "compile-error-token"})
        return httpx.Response(
            200,
            json={
                "status": {"id": 6, "description": "Compilation Error"},
                "stdout": None,
                "stderr": None,
                "compile_output": base64.b64encode(b"main.cpp:2:1: error: expected ';\n").decode("ascii"),
                "message": None,
                "exit_code": 1,
                "time": None,
                "memory": None,
            },
        )

    with patch("backend.app.compiler.httpx.AsyncClient", return_value=mock_client(handler)):
        resp = client.post(
            "/agent/compiler/run",
            json={"language_id": 54, "source": "int main() { return 0 }", "settings": DEFAULT_SETTINGS},
        )

    assert resp.status_code == 200
    res = resp.json()
    assert res["status"]["id"] == 6
    assert res["status"]["description"] == "Compilation Error"
    assert res["compile_output"] == "main.cpp:2:1: error: expected ';\n"
    assert res["stdout"] == ""
    assert res["exit_code"] == 1


def test_run_runtime_error(client):
    def handler(request):
        if request.method == "POST":
            return httpx.Response(201, json={"token": "runtime-error-token"})
        return httpx.Response(
            200,
            json={
                "status": {"id": 11, "description": "Runtime Error (NZEC)"},
                "stdout": None,
                "stderr": base64.b64encode(b"ZeroDivisionError: division by zero\n").decode("ascii"),
                "compile_output": None,
                "message": None,
                "exit_code": 1,
                "time": "0.02",
                "memory": 2048,
            },
        )

    with patch("backend.app.compiler.httpx.AsyncClient", return_value=mock_client(handler)):
        resp = client.post(
            "/agent/compiler/run",
            json={"language_id": 71, "source": "1/0", "settings": DEFAULT_SETTINGS},
        )

    assert resp.status_code == 200
    res = resp.json()
    assert res["status"]["id"] == 11
    assert "ZeroDivisionError" in res["stderr"]
    assert res["exit_code"] == 1


def test_run_time_limit_exceeded(client):
    def handler(request):
        if request.method == "POST":
            return httpx.Response(201, json={"token": "tle-token"})
        return httpx.Response(
            200,
            json={
                "status": {"id": 5, "description": "Time Limit Exceeded"},
                "stdout": None,
                "stderr": None,
                "compile_output": None,
                "message": base64.b64encode(b"Time limit exceeded\n").decode("ascii"),
                "exit_code": None,
                "time": "2.05",
                "memory": 12000,
            },
        )

    with patch("backend.app.compiler.httpx.AsyncClient", return_value=mock_client(handler)):
        resp = client.post(
            "/agent/compiler/run",
            json={"language_id": 71, "source": "while True: pass", "settings": DEFAULT_SETTINGS},
        )

    assert resp.status_code == 200
    res = resp.json()
    assert res["status"]["id"] == 5
    assert res["time"] == "2.05"
    assert res["message"] == "Time limit exceeded\n"


def test_run_polling_timeout_exhausted(client):
    def handler(request):
        if request.method == "POST":
            return httpx.Response(201, json={"token": "timeout-token"})
        return httpx.Response(200, json={"status": {"id": 2, "description": "Processing"}})

    env = {
        "COMPILER_MAX_POLLS": "3",
        "COMPILER_POLL_INTERVAL": "0.001",
    }
    with patch.dict("os.environ", env):
        with patch("backend.app.compiler.httpx.AsyncClient", return_value=mock_client(handler)):
            resp = client.post(
                "/agent/compiler/run",
                json={"language_id": 71, "source": "print(1)", "settings": DEFAULT_SETTINGS},
            )

    assert resp.status_code == 504
    assert "timed out" in resp.json()["detail"]


# --- Validation & Token Safety Tests ---


@pytest.mark.parametrize(
    "unsafe_token",
    [
        "../../etc/passwd",
        "token/with/slashes",
        "token?query=true",
        "token#fragment",
        "token with space",
        "",
    ],
)
def test_run_rejects_unsafe_tokens(client, unsafe_token):
    def handler(request):
        return httpx.Response(201, json={"token": unsafe_token})

    with patch("backend.app.compiler.httpx.AsyncClient", return_value=mock_client(handler)):
        resp = client.post(
            "/agent/compiler/run",
            json={"language_id": 71, "source": "print(1)", "settings": DEFAULT_SETTINGS},
        )

    assert resp.status_code == 502
    assert "invalid submission token" in resp.json()["detail"]


def test_run_rejects_invalid_base64_in_output(client):
    def handler(request):
        if request.method == "POST":
            return httpx.Response(201, json={"token": "valid-token-123"})
        return httpx.Response(
            200,
            json={
                "status": {"id": 3, "description": "Accepted"},
                "stdout": "not-valid-base64-content!@#$%",
                "stderr": None,
                "compile_output": None,
                "message": None,
            },
        )

    with patch("backend.app.compiler.httpx.AsyncClient", return_value=mock_client(handler)):
        resp = client.post(
            "/agent/compiler/run",
            json={"language_id": 71, "source": "print(1)", "settings": DEFAULT_SETTINGS},
        )

    assert resp.status_code == 502
    assert "invalid base64 data" in resp.json()["detail"]


def test_run_utf8_error_replacement(client):
    invalid_utf8_b64 = base64.b64encode(b"start \xff\xfe end").decode("ascii")

    def handler(request):
        if request.method == "POST":
            return httpx.Response(201, json={"token": "valid-token-123"})
        return httpx.Response(
            200,
            json={
                "status": {"id": 3, "description": "Accepted"},
                "stdout": invalid_utf8_b64,
                "stderr": None,
                "compile_output": None,
                "message": None,
                "exit_code": 0,
            },
        )

    with patch("backend.app.compiler.httpx.AsyncClient", return_value=mock_client(handler)):
        resp = client.post(
            "/agent/compiler/run",
            json={"language_id": 71, "source": "print(1)", "settings": DEFAULT_SETTINGS},
        )

    assert resp.status_code == 200
    res = resp.json()
    assert "\ufffd" in res["stdout"]
    assert "start " in res["stdout"]
    assert " end" in res["stdout"]


def test_run_collective_output_truncation(client):
    large_stdout = "A" * 50000
    large_stderr = "B" * 30000

    def handler(request):
        if request.method == "POST":
            return httpx.Response(201, json={"token": "large-output-token"})
        return httpx.Response(
            200,
            json={
                "status": {"id": 3, "description": "Accepted"},
                "stdout": base64.b64encode(large_stdout.encode("ascii")).decode("ascii"),
                "stderr": base64.b64encode(large_stderr.encode("ascii")).decode("ascii"),
                "compile_output": None,
                "message": None,
                "exit_code": 0,
            },
        )

    with patch("backend.app.compiler.httpx.AsyncClient", return_value=mock_client(handler)):
        resp = client.post(
            "/agent/compiler/run",
            json={"language_id": 71, "source": "print(1)", "settings": DEFAULT_SETTINGS},
        )

    assert resp.status_code == 200
    res = resp.json()
    assert res["truncated"] is True
    total_len = len(res["stdout"]) + len(res["stderr"])
    assert total_len == MAX_OUTPUT_BUDGET
    assert len(res["stdout"]) == 50000
    assert len(res["stderr"]) == MAX_OUTPUT_BUDGET - 50000


def test_run_small_output_not_truncated(client):
    small_stdout = "hello world"

    def handler(request):
        if request.method == "POST":
            return httpx.Response(201, json={"token": "small-token"})
        return httpx.Response(
            200,
            json={
                "status": {"id": 3, "description": "Accepted"},
                "stdout": base64.b64encode(small_stdout.encode("ascii")).decode("ascii"),
                "stderr": None,
                "compile_output": None,
                "message": None,
                "exit_code": 0,
            },
        )

    with patch("backend.app.compiler.httpx.AsyncClient", return_value=mock_client(handler)):
        resp = client.post(
            "/agent/compiler/run",
            json={"language_id": 71, "source": "print(1)", "settings": DEFAULT_SETTINGS},
        )

    assert resp.status_code == 200
    res = resp.json()
    assert res["truncated"] is False
    assert res["stdout"] == "hello world"


def test_run_upstream_response_size_cap(client):
    huge_data = b"x" * (2 * 1024 * 1024 + 100)

    def handler(request):
        return httpx.Response(200, content=huge_data)

    with patch("backend.app.compiler.httpx.AsyncClient", return_value=mock_client(handler)):
        resp = client.post(
            "/agent/compiler/run",
            json={"language_id": 71, "source": "print(1)", "settings": DEFAULT_SETTINGS},
        )

    assert resp.status_code == 502
    assert "exceeded size limit" in resp.json()["detail"]


# --- Concurrency Semaphore Tests ---


def test_run_concurrency_semaphore_rejects_busy(client):
    from backend.app.compiler import RUN_SEMAPHORE

    # Acquire both permits
    asyncio.run(RUN_SEMAPHORE.acquire())
    asyncio.run(RUN_SEMAPHORE.acquire())

    with patch.dict("os.environ", {"COMPILER_CONCURRENCY_TIMEOUT": "0.05"}):
        resp = client.post(
            "/agent/compiler/run",
            json={"language_id": 71, "source": "print(1)", "settings": DEFAULT_SETTINGS},
        )

    assert resp.status_code == 429
    assert "Compiler service is currently busy" in resp.json()["detail"]

    # Release one permit
    RUN_SEMAPHORE.release()

    def handler(request):
        if request.method == "POST":
            return httpx.Response(201, json={"token": "semaphore-token"})
        return httpx.Response(
            200,
            json={
                "status": {"id": 3, "description": "Accepted"},
                "stdout": base64.b64encode(b"ok\n").decode("ascii"),
                "stderr": None,
                "compile_output": None,
                "message": None,
            },
        )

    with patch.dict("os.environ", {"COMPILER_CONCURRENCY_TIMEOUT": "0.05"}):
        with patch("backend.app.compiler.httpx.AsyncClient", return_value=mock_client(handler)):
            resp = client.post(
                "/agent/compiler/run",
                json={"language_id": 71, "source": "print(1)", "settings": DEFAULT_SETTINGS},
            )

    assert resp.status_code == 200


# --- Main Guard & Input Validation Tests ---


def test_post_run_requires_json_content_type(client):
    resp = client.post(
        "/agent/compiler/run",
        content="print(1)",
        headers={"Content-Type": "text/plain"},
    )
    assert resp.status_code == 415
    assert "Application/json is required" in resp.json()["detail"]


@pytest.mark.parametrize(
    "invalid_payload",
    [
        {"language_id": 0, "source": "print(1)", "settings": DEFAULT_SETTINGS},
        {"language_id": -1, "source": "print(1)", "settings": DEFAULT_SETTINGS},
        {"language_id": 71, "source": "", "settings": DEFAULT_SETTINGS},
        {"language_id": 71, "source": "x" * 50001, "settings": DEFAULT_SETTINGS},
        {"language_id": 71, "source": "print(1)", "stdin": "x" * 20001, "settings": DEFAULT_SETTINGS},
    ],
)
def test_post_run_validation_errors(client, invalid_payload):
    resp = client.post("/agent/compiler/run", json=invalid_payload)
    assert resp.status_code == 422
    assert "Validation error" in resp.json()["detail"]


# --- Per-Request Isolation, Repr Safety, and Never Read Secrets From ENV ---


def test_compiler_never_reads_env_secrets(client):
    captured_requests = []

    def handler(request):
        captured_requests.append(request)
        return httpx.Response(200, json=[{"id": 71, "name": "Python"}])

    env_poison = {
        "COMPILER_BASE_URL": "https://poisoned-env.example.com",
        "COMPILER_API_KEY": "poisoned-secret-key",
        "COMPILER_RAPIDAPI_HOST": "poisoned-env.example.com",
    }
    client_settings = {
        "base_url": "https://isolated-client.example.com",
        "api_key": "clean-request-key",
        "rapidapi_host": "",
    }

    with patch.dict("os.environ", env_poison):
        with patch("backend.app.compiler.httpx.AsyncClient", return_value=mock_client(handler)):
            resp = client.post("/agent/compiler/languages", json={"settings": client_settings})

    assert resp.status_code == 200
    assert len(captured_requests) == 1
    req = captured_requests[0]
    assert str(req.url) == "https://isolated-client.example.com/languages"
    assert req.headers.get("X-Auth-Token") == "clean-request-key"
    assert "poisoned-env.example.com" not in str(req.url)
    assert "poisoned-secret-key" not in str(req.headers)
    assert "X-RapidAPI-Host" not in req.headers


def test_per_request_isolation(client):
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json=[{"id": 71, "name": "Python"}])

    settings_a = {
        "base_url": "https://target-a.example.com",
        "api_key": "key-aaa",
        "rapidapi_host": "",
    }
    settings_b = {
        "base_url": "https://target-b.example.com",
        "api_key": "key-bbb",
        "rapidapi_host": "",
    }

    clients = [mock_client(handler), mock_client(handler)]
    with patch("backend.app.compiler.httpx.AsyncClient", side_effect=clients):
        resp_a = client.post("/agent/compiler/languages", json={"settings": settings_a})
        resp_b = client.post("/agent/compiler/languages", json={"settings": settings_b})

    assert resp_a.status_code == 200
    assert resp_b.status_code == 200
    assert len(captured) == 2
    assert str(captured[0].url) == "https://target-a.example.com/languages"
    assert captured[0].headers.get("X-Auth-Token") == "key-aaa"
    assert str(captured[1].url) == "https://target-b.example.com/languages"
    assert captured[1].headers.get("X-Auth-Token") == "key-bbb"


def test_compiler_settings_repr_never_leaks_key():
    secret_key = "super-secret-key-12345"
    settings = CompilerSettings(base_url="https://judge0.example.com", api_key=secret_key)
    run_req = CompilerRunRequest(language_id=71, source="print(1)", settings=settings)

    assert secret_key not in repr(settings)
    assert secret_key not in str(settings)
    assert secret_key not in repr(run_req)
    assert secret_key not in str(run_req)


def test_error_responses_never_leak_key(client):
    secret_key = "confidential-secret-token"

    # Upstream failure returning 500
    def handler(request):
        return httpx.Response(500, json={"message": "Internal error"})

    settings = {"base_url": "https://judge0.example.com", "api_key": secret_key}
    with patch("backend.app.compiler.httpx.AsyncClient", return_value=mock_client(handler)):
        resp = client.post("/agent/compiler/languages", json={"settings": settings})

    assert resp.status_code == 502
    assert secret_key not in resp.text
