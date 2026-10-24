# AI Coding Assistant for Students - Backend

A FastAPI backend implementing a bounded multi-step function calling agent designed for evaluating and tutoring Python programming students.

## Architecture

- **Framework**: FastAPI (Python 3.11+)
- **HTTP Client**: `httpx` (OpenAI-compatible `/chat/completions`)
- **Static Analysis**: Python `ast` (syntax and structure) + `pyflakes` (lint warnings/undefined symbols)
- **Code Execution**: unavailable, fail-closed tool. Submitted programs are never executed on the host. A separately isolated, resource-limited worker is required before enabling runtime execution; a subprocess or a write/network-only sandbox is not sufficient.
- **Session Store**: In-memory bounded TTL (30 minutes) for follow-up conversational tutoring without code execution.

## API Contracts

### 1. `GET /health`
Returns:
```json
{
  "status": "ok"
}
```

### 2. `GET /providers`
Returns available direct provider presets (`openai`, `groq`, `xai`, `openrouter`, `custom`) and `9router` configuration.

### 3. `POST /agent/run`
Request Body:
```json
{
  "code": "def add(a, b)\n    return a + b",
  "question": "Why does this give a syntax error?",
  "settings": {
    "mode": "direct",
    "provider": "openai",
    "api_key": "sk-...",
    "model": "gpt-4o-mini",
    "enable_code_execution": false
  }
}
```

Response Body:
```json
{
  "session_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "report": {
    "summary": "Missing colon in function definition.",
    "bugs": [
      {
        "type": "syntax",
        "line": 1,
        "message": "SyntaxError: expected ':'",
        "suggestion": "Add a ':' at the end of line 1."
      }
    ],
    "explanation": "In Python, def statements must end with a colon.",
    "fixed_code": "def add(a, b):\n    return a + b"
  },
  "trace": [
    {
      "tool": "parse_ast",
      "status": "error",
      "detail": "Syntax error at line 1: expected ':'",
      "duration_ms": 1.2
    },
    {
      "tool": "submit_report",
      "status": "success",
      "detail": "Report submitted with 1 bug(s)",
      "duration_ms": 0.5
    }
  ],
  "tools_used": ["parse_ast", "submit_report"],
  "latency_ms": 420.5
}
```

### 4. `POST /agent/chat`
Follow-up conversational tutoring grounded in the original review. No code execution permitted.

Request Body:
```json
{
  "session_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "message": "Can you explain why colons are required in Python?",
  "settings": {
    "mode": "direct",
    "provider": "openai",
    "api_key": "sk-..."
  }
}
```

Response Body:
```json
{
  "reply": "Colons are used in Python to indicate the start of an indented block..."
}
```

## Security & URL Controls

- Custom base URLs must use `http` or `https`, contain no user credentials, no query string, no fragments, and reject cloud metadata IPs (e.g. `169.254.169.254`, `metadata.google.internal`).
- Localhost and 127.0.0.1 are permitted for local development and `9router`.
- API keys are request-scoped; never written to disk, logged, or returned in response bodies or errors.

## Running Tests and Offline Evaluation

```bash
# Run complete test suite
pytest backend/tests -v

# Run 12-fixture offline evaluation (no API keys required)
python -m backend.eval.run_eval

# Optional live evaluation
# Set OPENAI_API_KEY privately in your environment first (do not pass secrets in CLI arguments).
python -m backend.eval.live_eval
```
