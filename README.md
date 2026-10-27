# ATI — AI Coding Assistant for Students

A local coding workspace based on the supplied Topic 8 project brief. **Project Chat** is the main view for project explanations, debugging, reviewed changes, and approved local commands. **Online Compiler** replaces the former Python Review screen with multi-language, single-file compilation/execution through a configurable Judge0-compatible service.

## Online Compiler

Open **Online compiler** from the sidebar. **Browser mode is the free default:** run JavaScript or Python (Pyodide/WebAssembly), with optional stdin, without a compiler account, API key, Docker, or local execution server. Runtime downloads use your internet connection; Python has a larger first download and supports browser-compatible packages rather than a full desktop environment. Run and Stop controls bound execution; memory metrics may be unavailable.

For additional languages, open **Connection settings → Compiler execution**, choose optional **Judge0**, and enter your own service URL, API key, and optional RapidAPI host there. **Project Chat** has its own settings tab for direct AI providers and 9router; compiler credentials are not part of 9router settings. Judge0 language/version choices come from that service. AI provider settings are separate and never used as compiler credentials.

Use your Judge0 service's base URL without `/submissions`. Direct Judge0 uses `X-Auth-Token`; a configured RapidAPI host uses RapidAPI authentication. Enter credentials in settings, not source files or `.env`. The key is held only in memory, sent in the local backend request body (not URLs), and clears on reload; non-secret connection preferences may persist. No backend restart is required for setting changes. See [Judge0 CE API documentation](https://ce.judge0.com/) for service languages, statuses, and limits.

**Privacy and safety:** browser mode executes in an isolated browser context and downloads pinned Python runtime assets; it does not submit source to Judge0 or the AI provider. Browser isolation/resource limits are not a production multi-tenant sandbox. Judge0 mode sends source and stdin to your selected service, which may retain submissions, charge money, or impose quotas. Neither mode runs arbitrary snippets directly on the backend host. Single-file programs are supported; package installation, interactive sessions, and full-project builds are outside this view.

## Conversation Context

The composer belongs below the conversation, not in the sidebar. Its small context icon opens **Session info** with estimated prompt usage, configured window size, response reserve, and a breakdown of system instructions, tool definitions, project context, messages, attachments, and tool results. Counts are estimates, not provider-billed tokens; images have an approximate cost. The app does not infer a model's actual window from its name. Set the appropriate budget in **Connection settings → Project Chat**.

The old 30-message cutoff is replaced by token-budgeted prompt history. Older complete turns can be omitted to keep the latest request within the selected budget; visible chat history is retained. **Compact conversation** explicitly excludes earlier turns from future prompts while keeping recent turns and the displayed transcript—it does not generate a summary or promise to remember omitted details. A transport/storage safety ceiling still bounds unusually large histories separately from the context budget. Requests whose required context alone exceeds the selected window are rejected with an explanation rather than silently dropping the latest user message.

## Attachments and Project Menus

Each chat has a **⋯ menu** for its project settings and local path controls, rather than displaying the full path form in the conversation. The wider sidebar keeps chat names readable. Use the composer **+** menu for **Attach files** or **Choose browser snapshot**.

Supported attachments are UTF-8 text/code files, PDFs with extractable text, DOCX documents, and PNG/JPEG/WebP images. Documents are parsed by the local backend; their text is sent as untrusted user context when you send the message. Images are forwarded as actual image inputs and require a vision-capable model/service. Scanned PDFs without a text layer are not OCRed; unsupported/encrypted/malformed files produce explicit errors rather than empty analysis. File preparation does not run macros or document code.

Limits: up to 6 attachments per message and 12 across the conversation request; document files up to 5 MB, images up to 4 MB, and bounded extracted text. Attached content is included in local chat autosave (unencrypted browser storage) and sent to the selected model when you submit the message. Review attachments for secrets before sending; no filename filter can guarantee documents contain none.

## Local Workspace Quick Start

1. Start the local backend and frontend using the commands below.
2. In Project Chat, click **Browse…** beside the local path field to choose a folder using the Mac's native folder chooser (no browser upload dialog), or paste the **absolute local project path**. Then click **Connect local project**. Browsing only fills the path; connecting grants the backend access to that project, without sending files to the model until you submit a prompt. The chooser opens on the backend's Mac; other operating systems can still use the path field. Cancelling preserves the existing path.
3. Ask for a change. Review the proposed diff, click **Review and apply**, then **Confirm and apply**. A stale proposal is rejected if included project files changed on disk.
4. For tests/builds, review an AI command request or enter an argument array such as `["npm", "test"]` under **Run project command**. Review its exact arguments and directory, then explicitly approve or deny. Captured output can be sent back to the AI for the next fix.
5. **Refresh from disk** updates the displayed files. AI tool reads also refresh from disk automatically. Workspace grants and command tokens are in memory and expire; reconnect after a backend restart.

Commands are limited to supported development executables and arguments; shell interpreters, inline execution, and install/deploy operations are rejected. Approved project scripts still execute arbitrary code with your OS-user permissions. The service is for trusted, local single-user projects, not hostile repositories or public hosting. Multi-file batches are prevalidated and locked against this app's concurrent edits, but are not filesystem transactions; an OS I/O failure can leave partial changes, so use version control and refresh after errors.

## Project Chat

- Select a directory using the browser's folder picker, then review which supported text files are included before sending a message. This imports a **snapshot**, not continuous filesystem access; select the folder again to refresh it.
- Project conversations, drafts, active chat, and imported browser snapshots autosave locally in IndexedDB for this browser/site. They are not synced to a server or encrypted by the app; anyone with access to this browser profile may read them. Live project contents are re-read from disk rather than stored as saved snapshots. API keys, workspace capabilities, pending approvals, and executable command requests are not saved. After reload, saved local-project paths require explicit **Reconnect local project** before access resumes. **Delete conversation** removes the current saved conversation. Browser storage clearing/private browsing or a different hostname/port may make saved data unavailable; autosave failures are shown rather than silently discarding chats.
- Selected file contents and conversation are sent to the configured model service **when you send a prompt**. Common secrets, environment files, generated/vendor directories, binaries, and oversized files are excluded. Review the file list and remove anything sensitive; filename filtering cannot guarantee a project contains no secrets.
- Uploaded snapshot context is bounded to 80 files, 30,000 characters per file, and 200,000 file-content characters total. Live workspaces use a separate inventory of up to 1,000 eligible files and 10 MB total, retaining the 30 KB per-file bound. The model receives the live file inventory first and retrieves relevant contents through tools, so the initial prompt budget does not hide later source directories. Excluded files and limits remain visible.
- Project Chat includes **snapshot coding tools**: list files, read line ranges, search literal text, inspect project manifests/framework signals, and propose validated multi-file changes as unified diffs. Tool activity and proposed patches appear with the assistant reply. Patches can be copied or downloaded for review; they are **not automatically applied**.
- Additional tools perform Python syntax/static analysis, strict JSON validation, and merge-conflict marker checks. These are static diagnostics, not executed tests or runtime verification.
- **Subagents:** the main model can delegate bounded tasks to separate worker contexts using `delegate_tasks`. Workers share the selected provider/gateway and default model; an individual task may request another model ID on that same connection. At most 3 workers are allowed per request, with 2 active concurrently. Workers cannot spawn more workers, propose patches, execute commands, or access anything outside their permitted snapshot paths. Worker results and tool activity are displayed after the request completes, not as live streaming.
- These tools work on imported text across web and application frameworks, not just Python. Framework detection reports declared dependencies/scripts rather than claiming packages are installed or tests passed.
- **Local workspace mode** connects the backend to an explicitly selected absolute project path. Snapshot import remains an alternative. Local mode reads current project text instead of requiring browser reuploads; reviewed changes can be applied with explicit approval and stale-revision checks.
- Local commands require a separate prepare/approve/run flow displaying exact arguments and working directory. The model can request a command but cannot approve it. Commands run as your OS user, **not in a sandbox**: project scripts can access files and networks beyond the workspace. Approve only commands and repositories you trust. Do not expose the backend to other users or networks.
- Filesystem tool access blocks out-of-root paths, symlinks, and recognized sensitive/generated files. These guards are not an OS security boundary against malicious approved scripts or concurrent filesystem manipulation. Browser origins and Host headers are restricted to local access; this is not a multi-user authenticated service.
- Direct-provider and 9router settings configure Project Chat. Online Compiler uses a separate backend-configured execution service and does not require AI settings.

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
4. Open the **9router model** dropdown and select a tool-capable model from your gateway's catalog. The catalog reloads automatically when you open gateway settings or change its URL/key; **Reload models** refreshes it manually. Model IDs retain their provider prefixes. The backend queries your gateway's `GET /v1/models` using the gateway key without storing it. Availability depends on connected accounts and key permissions; listing a model does not guarantee tool support.
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

Project Chat supports project snapshots, scoped live workspaces, reviewed file changes, approved commands, and bounded subagents. Online Compiler supports single-file code and stdin using the configured language versions. The former Python Review UI is removed; legacy analysis APIs remain for compatibility. Persistent user accounts, autonomous unapproved edits, real-time streaming, and a bundled production execution infrastructure remain outside scope.

## Verification

Automated checks use local tool fixtures and mocked provider responses. Verified: 462 backend tests including context estimation/trimming, document parsing, multimodal payloads, and request-scoped compiler credential/polling/error scenarios, 64 frontend tests including real Chrome per-chat menus and composer attachments at desktop/mobile sizes including actual Chrome JavaScript execution, storage isolation, and blocked local-backend networking. Pinned Pyodide Python with stdin was also verified in real Chrome. Compiler integration build and diagnostics pass, plus previously verified actual Chrome IndexedDB history/draft reload, explicit local reconnection, excluded runtime tokens/live files, and deletion across reload, frontend production build, generated patches applied in temporary fixtures with exact newline/Unicode fidelity, actual health/proxy requests, project-chat route validation, metadata endpoint rejection, and browser report/trace/chat rendering using labelled fixtures. Project Chat browser checks cover directory filtering, safe formatted replies, mobile overflow, and replies staying in the original conversation when switching chats during a pending request. Offline evaluation contains 12 fixtures: 6 passed, 6 skipped (4 runtime and 2 logic); skipped cases do not count as detected bugs or model accuracy. A Starlette/httpx test-client deprecation warning remains, without failing tests.

A successful local test suite does not establish that a live external model or router is connected. Live inference requires your own configured endpoint, model access, and API key. Temporary verification servers were stopped; use the commands above to run the application.

Research was delegated to the requested Research model, backend implementation to Coding, and frontend implementation to Hard UI. The coordinator integrated the results and corrected fail-closed execution, Bearer authentication, report completion, error privacy, and evaluation claims.
