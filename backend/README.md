# AI Coding Assistant for Students - Backend

A FastAPI backend implementing a bounded multi-step function calling agent designed for evaluating and tutoring Python programming students.

## Architecture

- **Framework**: FastAPI (Python 3.11+)
- **HTTP Client**: `httpx` (OpenAI-compatible `/chat/completions`)
- **Static Analysis**: Python `ast` (syntax and structure) + `pyflakes` (lint warnings/undefined symbols)
- **Code Execution**: unavailable, fail-closed tool. Submitted programs are never executed on the host. A separately isolated, resource-limited worker is required before enabling runtime execution; a subprocess or a write/network-only sandbox is not sufficient.
- **Session Store**: In-memory bounded TTL (30 minutes) for follow-up conversational tutoring without code execution.

## Online Compiler API

Browser JavaScript/Python execution does not use these backend endpoints. Optional Judge0 mode uses `POST /agent/compiler/languages` with `{settings:{base_url,api_key,rapidapi_host}}`, and `POST /agent/compiler/run` with `{language_id,source,stdin,settings}`. The run response contains `status`, `stdout`, `stderr`, `compile_output`, `message`, `exit_code`, `time`, `memory`, and `truncated`. Source/input are base64-encoded upstream, with bounded polling and output. Judge0 credentials are request-scoped, required in settings, and are not loaded from compiler environment-key defaults or saved server-side. HTTPS is required for remote endpoints; HTTP is limited to loopback. Tests use mocked services, not private-code uploads. Legacy Python analysis endpoints remain for compatibility but are no longer the UI's secondary feature.

## Local Workspace API

All workspace routes are under `/agent/workspaces`: `POST /open {path}`, `/snapshot {workspace_id}`, `/apply {workspace_id,revision,changes}`, `/commands/prepare {workspace_id,argv}`, and `/commands/run {workspace_id,approval_id,approved}`. Workspace IDs are temporary random capabilities: do not publish them. Snapshot responses include `workspace_id`, `name`, `path`, `files`, `skipped`, and a content revision. Apply supports create/replace/delete/rename and rejects stale revisions after re-reading disk. Batches are prevalidated and app-locked, not transactional against OS failures or external writers.

Live scanning permits up to 1,000 eligible files and 10 MB total (30 KB per file), separately from the uploaded snapshot's 80-file/200,000-character context limit. Live prompts contain file paths, with contents fetched by tools on demand; large delegated contexts also use inventories. This prevents early directories from consuming the prompt budget and hiding later frontend sources.

Project chat accepts `workspace_id` instead of uploaded `files`, refreshes disk content on each tool call, and returns `workspace_revision` plus `command_requests`. The AI only requests commands; a human must prepare and approve exact execution separately. One-use approval tokens expire after five minutes; commands have time/output limits and a reduced environment. They run as the OS user, not inside a sandbox, and project scripts may access resources outside the workspace. Root/home/system paths, symlinks, traversal, and recognized sensitive files are blocked by filesystem tools. Keep the API bound to loopback and do not use it with untrusted local users/repositories.

## Context Budgeting

Project Chat accepts `context_window` and `response_reserved` token budgets. Context metadata and the no-inference `POST /agent/project-chat/context` preview report approximate usage and category counts. Estimates use bounded heuristics, not exact provider tokenization; image costs are approximate. The app trims oldest complete turns while preserving the current user request and in-progress tool chains. If required context cannot fit, the request fails clearly. The selected window is user configuration, not a claim that a routed provider supports that size. A separate safety ceiling bounds transported history.

## Attachment Preparation

`POST /agent/attachments/prepare` accepts `{name,mime_type,data_base64}` and returns a validated prepared attachment `{id,name,mime_type,kind,text?,data_url?,size}`. Supported content includes UTF-8 text/code, PDF text, DOCX text, and PNG/JPEG/WebP images. Parsing is local and bounded; no external document fetch, macro execution, or OCR is performed. DOCX extraction limits expanded ZIP size and entry counts. Project messages accept prepared `attachments`; image content uses the provider's `image_url` input format, while extracted documents remain user-role untrusted text. The selected model must support vision for image requests. Upstream error text and attachment bytes are not logged or echoed in errors.

## Project Conversations

`POST /agent/project-chat` accepts `{project_name, files: [{path, content}], messages: [{role, content}], settings}` and returns `{reply}`. It uses the existing direct-provider/9router settings without storing keys or conversation state on the server. Project text is untrusted user context, not system instructions. The endpoint offers snapshot-only file listing/reading/search, manifest inspection, and validated diff proposals through a bounded tool-calling loop. It does not perform file writes or program execution. Replies include actual tool `trace` entries and `proposals` with unified diffs and proposed changes; consumers must not label them applied or tested. The loop permits at most 8 provider rounds, 8 calls per batch, and 24 total tool calls. `delegate_tasks` adds at most 3 snapshot-only workers per main request, at most 2 concurrently. Workers inherit the connection credentials, may override the model ID, and cannot recursively delegate or generate proposals. Their individual loops are capped at 3 rounds and 6 tool calls. Worker failures are isolated and returned as worker results. Additional static tools are `analyze_python`, `validate_json`, and `check_conflicts`; none execute project code. Each proposal allows at most 20 changes and 200,000 content characters, with the existing 30,000-character file limit. Replace/delete require existing snapshot paths; create requires an absent path. Missing replacement content is an error, not permission to erase a file. Patch generation preserves Unicode and end-of-file newline changes.

Files must use safe relative paths (unsafe paths are rejected); recognized secrets, environment files, vendor/generated directories, and binary content are filtered out before constructing model context. Limits: 80 files, 30,000 characters per file, 200,000 total file-content characters, 30 messages, and 10,000 characters per message. The last message must be a non-empty user prompt. Browser-selected content is a snapshot, not server access to arbitrary filesystem paths.

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

### `POST /agent/models`

Accepts `{ "router_base_url": "http://localhost:20128/v1", "router_api_key": "..." }` and returns `{ "models": ["provider/model-id"] }`. It requests the gateway's `GET /models` with Bearer authentication, preserving exact IDs and sorting/deduplicating them. Omitted values use the same environment defaults as gateway inference. Keys remain request-scoped; upstream error bodies are not exposed. Invalid catalogs and connection failures produce explicit errors; an empty catalog returns an empty list.

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
