# ATI — AI Coding Assistant for Students

A local Python-learning workbench based on the supplied Topic 8 project brief. Students submit a single Python snippet and a learning question. A FastAPI agent chooses analysis tools and submits a structured report; a React interface shows the code, actual tool-call trace, feedback, corrected code, and follow-up conversation.

## Architecture

```text
React editor / report / trace / chat / settings
                    |
             local FastAPI API
                    |
       bounded LLM function-calling loop
          |         |          |
       parse_ast  pyflakes  optional isolated execution
                    |
             submit_report tool
```

The report separates syntax, style/static-analysis, runtime, and logic issues. Pyflakes finds static issues such as undefined names and unused imports; it is not a comprehensive style formatter or logic verifier. LLM suggestions may be incorrect: review generated fixes and never treat them as proof of program correctness.

## Run Locally

Requires Python 3.11+ and Node.js 20.19+ or 22.12+ (Node 26 and Python 3.14 were used for verification). From this project's root:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
.venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

On this Mac, `python3.14` is available if `python3` points to an older interpreter. Keep the backend terminal open. Keys can be entered in the UI without a `.env` file. For optional server defaults, copy `backend/.env.example` to `.env` in the project root and populate only the values you need. Do not commit `.env`.

In a second terminal, follow the frontend instructions below. Backend API docs are at `http://127.0.0.1:8000/docs`.

Backend checks (from project root):

```sh
.venv/bin/python -m pytest backend/tests -q
.venv/bin/python -m backend.eval.run_eval
```

## Frontend Development

```sh
cd frontend
npm ci
npm run dev
```

Open `http://127.0.0.1:5173`. The Vite development server proxies `/agent` and `/health` to the backend at `127.0.0.1:8000`. Start the backend separately before analyzing code. For a production deployment, provide a same-origin reverse proxy; the static build does not include an API server.

Frontend checks:

```sh
cd frontend
npm test
npm run build
```

## Provider Settings

The original brief specifies **Groq**, not Grok. This implementation supports Groq, xAI (Grok), OpenAI, OpenRouter, and custom OpenAI-compatible services instead of hardcoding one provider.

Two independent connection profiles are available:

- **Direct:** provider, API base URL, API key, and model ID.
- **9router:** router base URL, router API key, and router model ID. Switching modes does not overwrite the direct settings.

Models must support OpenAI-compatible chat completions **and tool calling**. An OpenAI-compatible text-only endpoint is insufficient for the analysis loop. A provider's available models, pricing, limits, and tool support may change.

API keys entered in the UI are held in memory only, sent to the local backend for the requested operation, and forwarded to the selected service. They are not saved in browser storage. Reloading the page clears them. Do not put real keys in source, screenshots, URLs, or Git. If you configure environment keys, keep them in an untracked local environment file or shell environment.

**Privacy:** analyzing a snippet sends its content and your question to the selected external provider or configured router. Follow-up chat includes session context. Do not submit confidential code unless that service is approved for it. This local application has no user authentication and is not intended for public exposure.

## Connecting 9router

1. Run your own 9router instance and connect upstream providers in its dashboard.
2. Create a **9router inference API key** in that dashboard. This is separate from your upstream provider keys and dashboard login.
3. In this app's settings choose **9router**, set the base URL to `http://localhost:20128/v1` (or your own instance's `/v1` URL), and enter the router key.
4. Set a tool-capable model ID exactly as your router exposes it, including any provider prefix. `GET /v1/models` on your instance lists accessible IDs; model availability depends on its connected accounts and key permissions.
5. Run an analysis. The backend sends OpenAI-compatible `POST /v1/chat/completions` with `Authorization: Bearer <router-key>`. Keep `/v1` in the base URL; do not append `/chat/completions` yourself.

No dashboard credentials or automatic account/key provisioning are needed by this app. Local 9router configurations may permit keyless access, but explicit keys are recommended; remote instances require their configured access credentials. Tool support still depends on the chosen upstream model.

Verified primary references:

- [9router integration examples and base URLs](https://github.com/decolua/9router/blob/master/gitbook/content/en/integration/other-tools.md)
- [9router provider setup](https://github.com/decolua/9router/blob/master/gitbook/content/en/getting-started/quick-start.md)
- [9router chat route](https://github.com/decolua/9router/blob/master/src/app/api/v1/chat/completions/route.js)
- [9router model listing](https://github.com/decolua/9router/blob/master/src/app/api/v1/models/route.js)
- [9router authentication guard](https://github.com/decolua/9router/blob/master/src/dashboardGuard.js)

## Execution Safety

Runtime execution is **off by default and unavailable in this implementation**, even if requested through the API. The execution tool fails closed until an independently isolated, resource-limited worker is provided. A subprocess with a timeout or a write/network-only macOS sandbox can still expose local files and inherited credentials, so neither is used to run submitted code.

Syntax parsing and pyflakes analysis remain usable without executing the program. A skipped runtime check must not be interpreted as a successful runtime test. Do not expose this development application to untrusted networks. OS isolation is defense in depth, not a production multi-tenant execution service.

## Scope

Implemented scope targets one Python snippet per analysis, a bounded tool loop, in-memory sessions, a post-run trace, and follow-up chat. Persistent accounts, multi-file projects, real-time streaming, and deployment-grade remote execution are intentionally outside this small project's scope.

## Verification

Automated checks use local tool fixtures and mocked provider responses. Verified: 53 backend tests, 2 frontend tests, frontend production build, actual health/proxy requests, metadata endpoint rejection, and browser report/trace/chat rendering using labelled fixtures. Offline evaluation contains 12 fixtures: 6 passed, 6 skipped (4 runtime and 2 logic); skipped cases do not count as detected bugs or model accuracy. A Starlette/httpx test-client deprecation warning remains, without failing tests.

A successful local test suite does not establish that a live external model or router is connected. Live inference requires your own configured endpoint, model access, and API key. Temporary verification servers were stopped; use the commands above to run the application.

Research was delegated to the requested Research model, backend implementation to Coding, and frontend implementation to Hard UI. The coordinator integrated the results and corrected fail-closed execution, Bearer authentication, report completion, error privacy, and evaluation claims.
