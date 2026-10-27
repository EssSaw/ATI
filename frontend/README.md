# Codebook frontend

React + Vite Project Chat and Online Compiler workbench. Run the backend at `127.0.0.1:8000`, then:

```sh
npm install
npm run dev
```

Vite proxies `/agent` and `/health` to the local backend. For deployment, configure the same-origin reverse proxy separately; Vite's development proxy is not a production API server.

```sh
npm test
npm run build
```

Connection settings support OpenAI, Groq, xAI, OpenRouter, custom OpenAI-compatible APIs, and an explicitly separate 9router mode. Direct-provider model IDs are editable. In 9router mode, opening settings or editing the gateway URL/key automatically loads `/agent/models` after a 400 ms debounce. The native model dropdown shows exact gateway IDs, with loading, empty, and error feedback and a Reload models button. Previously selected IDs remain visible (marked unavailable if absent); no alternative is selected automatically. Closing settings, switching to Compiler execution, or switching to direct mode cancels loading, and changed credentials clear the old catalog immediately. Keys remain in React memory only and clear on reload; only non-secret preferences persist. Use URL fields without embedded credentials or query parameters. Changing providers clears the current direct API key. Requests go to the local backend, which forwards them to the chosen service.

## Online Compiler

Open **Online compiler** from the Project Chat sidebar. The compiler replaces the former Python Review screen; Project Chat remains the default workspace. Both screens stay mounted after opening so switching screens preserves chat state, compiler drafts and results. Compiler drafts are in memory only (not localStorage or IndexedDB) and clear on page reload. Source edits are retained separately for each language version during this session.

**Browser mode is the default:** free JavaScript and Python with no API key or backend compiler configuration. Source and stdin run off the main thread in an isolated browser worker, not through an execution API. Runtime assets download from a CDN. Python uses Pyodide, with a larger initial download; standard-library programs are supported, not arbitrary native packages. The runner restricts network access, but browser isolation is not a production security sandbox. Do not run sensitive or untrusted code. **Stop** terminates browser execution.

Connection settings has separate **Project Chat** and **Compiler execution** tabs. Each screen opens its own tab; arrow keys, Home and End navigate the tabs. Saving validates only the active tab, so browser execution never requires an AI provider key. The dialog keeps its heading, tabs and save action visible while the body scrolls on smaller screens. On desktop the chat sidebar and composer stay within the viewport; conversation history and the main context/transcript scroll independently. Mobile keeps a compact sidebar above the chat. Text and controls use a larger, readable scale without changing the existing fonts or palette.

**Judge0 is optional:** select it in the separate Compiler execution tab of shared Connection settings. Supply your own base URL and masked API key, plus an optional RapidAPI host. For RapidAPI, the suggested URL is `https://judge0-ce.p.rapidapi.com` and host `judge0-ce.p.rapidapi.com`; neither is silently configured. There is no environment-key fallback. Compiler settings do not alter Project Chat credentials or models. Keys remain in memory only and clear on reload. Only an explicit whitelist of non-secret preferences (including compiler mode, URL and host) persists.

Judge0 languages load only while the compiler is visible and Judge0 is selected with valid configuration. Settings changes clear the old catalog, debounce loading by 400 ms, abort prior requests and ignore stale responses. **Reload languages** retries failed/empty catalogs. Exact backend-supplied numeric IDs and version names are used. The browser catalog is local and never requests the compiler backend.

- POST `/agent/compiler/languages` sends `{settings: {base_url, api_key, rapidapi_host}}`.
- POST `/agent/compiler/run` sends `{language_id, source, stdin, settings: {base_url, api_key, rapidapi_host}}`.
- Judge0 submits source and stdin through the local backend to your chosen service. **Stop** aborts the app's request, not the remote job; it may continue on that service.
- Results show actual status, stdout, stderr, compile output, message, exit code, time, available memory metrics and truncation. Unavailable metrics are not invented. Status 3 is accepted; failures remain failures even after HTTP 200.

Source is limited to 50,000 characters and stdin to 20,000. Drafts are separate per execution mode/language, kept only in memory. Concurrent runs and edits during execution are blocked; changing execution settings cancels the current browser run or stops waiting on Judge0. Loading/status updates include Python initialization. Tests use local fixtures, not live execution services.

## Project Chat (default workspace)

The default view is a directory-based, read-only project conversation. Online compiler is available from the sidebar; Project Chat provider settings and its router model catalog remain separate from compiler settings. Choose a folder with the browser directory picker, review/deselect files and expand any file to inspect its exact contents before sending. Folder selection reads locally, not over the network. Each Send posts selected files, that conversation's history ending with the current prompt, and connection settings to `/agent/project-chat`. Nothing is automatically edited or executed.

Snapshots are frozen after the first successful send, keeping each conversation's context consistent. Before sending, reselect the folder to refresh; afterward start a new chat for updated files. Chats, drafts, selected browser snapshot contents and the active conversation autosave to this site's IndexedDB. API keys, live workspace file contents, workspace tokens, pending approvals and executable command requests are excluded. Reload restores history; saved local projects require explicit reconnection. Saved text is not encrypted or synced. A save-status message reports storage failures, and Delete conversation removes the selected record. Requests are keyed to conversation IDs, so switching conversations cannot misroute replies. Failed requests retain the draft and do not append phantom messages. Each conversation supports 30 total messages (15 exchanges).

The local filter rejects unsafe paths, environment/secret/credential/key files, private/generated folders, lockfiles, binaries, unknown extensions and files larger than 30,000 bytes before reading. UTF-8 text is accepted only within 30,000 characters per file, 200,000 total characters and 80 files. Conservative byte checks may exclude some multibyte text below the character limit. Over-budget files are skipped whole, never silently truncated, with reasons shown for review. These checks do not replace backend validation or human review for secrets embedded in otherwise ordinary source files. Assistant Markdown supports headings, lists and fenced code as escaped React text, never raw HTML.

The message composer supports `Enter` to submit the form via `requestSubmit` (honoring busy/disabled validation guards) and `Shift+Enter` to insert a newline. Active IME composition (`isComposing`, `nativeEvent.isComposing`, or `keyCode` 229) is left untouched without submitting or preventing default. A subtle composer hint displays `Enter to send, Shift+Enter for newline`.

`npm test` includes snapshot filtering/budget and conversation-payload regressions alongside the existing settings/catalog checks. No external inference is required for these tests.

### Snapshot coding toolkit

Project Chat displays actual returned `list_files`, `read_file`, `search_files`, `inspect_project`, `analyze_python`, `validate_json`, `check_conflicts`, `propose_changes`, and `delegate_tasks` activity per assistant reply, including status, detail and duration. Python diagnostics, JSON validation and conflict-marker checks are static; they do not execute project code. Expand Tool activity to inspect completed calls; no events are simulated. Replies without metadata remain supported. Trace/proposal/worker metadata stays beside the assistant message in browser memory across exchanges, but outgoing history includes only `role` and `content`.

Expandable Worker results cards show each worker's name, actual model, final success/error status, safely rendered reply, escaped error and nested tool trace. They appear only after the request finishes and belong to that assistant reply, not a live activity feed. Malformed worker entries and trace steps are discarded. Delegation is bounded to 3 workers total per request, at most 2 concurrently, with separate context and no nested delegation. Workers use the same configured provider/gateway and current connection model unless the orchestrator supplies an optional per-task model override. There are no per-worker settings or additional model defaults. Workers cannot run shell commands or apply changes.

Change proposals show their summary, affected paths/operations and expandable unified diff. Copy patch and Download .patch export the exact diff as plain text with a sanitized download filename. They do not apply changes to disk or the snapshot. Review patches before applying them yourself, then start a new chat with a fresh snapshot. Clipboard errors offer download/manual-copy alternatives.

Framework assistance uses plain-text files and manifests, not installed framework tooling. Supported examples include React/Vue/Svelte/Astro, Python, .NET (`csproj`, `fsproj`, `sln`, `props`, `targets`), Java/Kotlin/Gradle, Flutter/Dart, Ruby (`Gemfile`, `Rakefile`), PHP, Go, Rust, Elixir/Erlang, Dockerfiles and requirements files. Existing secret, generated-directory, binary, lockfile and budget exclusions remain in force. Terminal execution, package installation, browser automation and deployment are unavailable. No remote resources are fetched by the toolkit UI.

Regression tests also cover patch filename sanitation, multiline fidelity, metadata-free request history, legacy replies and framework manifest filtering.

### Connected local workspace

The default view now also supports a connected local workspace. The read-only and frozen-context restrictions above apply to **browser snapshots**, not local mode.

Enter an absolute path and choose **Connect local project**. The backend reads that path on its own machine through `/agent/workspaces/open`; no source reaches the provider until you send a prompt. Connected name/path remain visible. **Refresh from disk** reads a current snapshot even after chatting (disabled only during a pending operation). Browser snapshot selection remains available in a new, unconnected chat.

**Privacy:** every local prompt shares the eligible backend snapshot and conversation with the configured provider, including remote providers. Local mode is not offline inference. Local files cannot be individually deselected; use browser snapshot mode for selective sharing. Filename exclusions cannot detect secrets embedded in ordinary source. Workspace IDs and approvals stay in React memory only; chat history and drafts are saved locally, but live file snapshots are not persisted. Local chat requests include `workspace_id` without duplicate `files`.

**Review and apply** shows affected files and diff before **Confirm and apply**. Apply uses the original assistant turn's revision, never a newer refreshed revision. A conflict (409) retains the proposal without marking it applied; refresh and request a fresh proposal. Success replaces the displayed snapshot from the server and marks only that proposal as applied with your approval, not independently by AI. Copy/download patches remain available. Manual create, rename and delete operations use the same confirmation and revision checks.

**Review command** prepares but does not execute a command. Separate approval displays the exact argument array, working directory and server warning. **Deny** clears it without running; **Approve and run** submits the approval ID. Commands run as the backend OS user, **not in a sandbox**, and project scripts can access arbitrary files, credentials and networks. Only approve commands/scripts you trust. Manual commands use a JSON array (e.g. `["npm", "test"]`), not shell-split text. Results show stdout, stderr, exit code, timeout and truncation status, and append to the editable user draft for the next prompt, never fabricated as AI tool execution or sent automatically. Shorten long output to fit the 10,000-character prompt limit. Refresh after commands change files. No automatic installation or deployment occurs.

Approvals clear after confirmation attempts. Failed requests retain chat context, proposals and drafts. The main AI request still times out after 120 seconds; multi-call orchestration may take longer, and browser timeout does not imply cancellation of backend work. Commands use the backend's own timeout instead of the AI-request timer.

Local helper tests cover workspace-only payloads, original assistant revisions, command metadata validation, JSON argument parsing, result formatting, snapshot normalization, and composer Enter/Shift+Enter/IME event handling. Run `npm test` and `npm run build`.

### Conversation attachments and project settings

The conversation's ellipsis menu opens **Project settings**, including the local path/Browse picker, explicit connect/reconnect, disk refresh, file review, toolkit and approved local operations. No local filesystem capability reconnects automatically. Browser snapshots are chosen from the composer's **+** menu and cannot be changed after the first message; start a new chat to change context. The compact composer is structurally inside the main column below the conversation on desktop and mobile; the sidebar footer remains available. The + menu holds attachments and browser snapshots; the icon-only Send button has an accessible label and tooltip. Enter sends; Shift+Enter inserts a newline (IME composition is preserved).

Use **+ → Attach files** to prepare multiple PNG/JPEG/WEBP images (4 MB each), PDF or DOCX documents, and plain-text/source-code/log files (5 MB each). Maximum: 6 files per message and 12 across the submitted conversation history; start a new chat after reaching the history limit. Unsupported files and individual preparation failures are shown inline. Image thumbnails and document filenames appear in the draft and sent messages; draft chips can be removed. Attachments-only messages do not require a project folder.

Preparation sends files only to the local backend for parsing. Nothing is sent to the selected AI provider until **Send**. PDF extraction reads embedded text, not OCR; scanned/image-only PDFs need another workflow. Images require a vision-capable model/provider; provider failures preserve the draft and attachments. Successful sends clear the draft. Preparation disables Send and workspace changes until it finishes.

Prepared document text, image data, draft attachments and message history are stored **unencrypted in this browser's IndexedDB**, under the existing 20 MB serialized-character budget. Other users with access to the browser profile may read them. Do not attach secrets. Quota or storage failures remain visible; no chats are silently evicted. API keys, local workspace capabilities and command approvals are never persisted.
Extracted document/text content is limited to 100,000 characters by the backend; oversized extractions fail visibly rather than being silently truncated. PDFs are also limited to 100 pages. Filenames are limited to 200 characters.


### Context window and compact conversation

The small circle beside Send opens **Session info**. Its debounced, abortable local-backend preview includes selected project files, active conversation history, prepared attachments, and the draft; a blank draft uses a single `Context preview` user placeholder without hiding attachments. Preview needs no provider key and never calls the provider. The breakdown covers system, tools, project, messages, attachments and tool results; estimates are approximate, not exact tokenizer counts. Before the server responds, the lower-bound browser estimate counts UTF-8 bytes / 3 and 1,024 tokens per image; unavailable server overhead is explicitly labeled. Local workspace contents are not duplicated in the browser request. Returned context is shown as **Last request** (excluding the current draft), with any automatically omitted older messages reported without deleting saved history.

**Chat settings** exposes a persisted, non-secret context window (default 32,768; integer 8,192–2,000,000) and response reserve (default 4,096; integer 512–200,000 and smaller than the window; this is a budget, not a guarantee the selected model can emit that many tokens). Selecting a 9router model automatically uses its advertised `context_length` and `max_completion_tokens` when both are present and valid; you can still edit the budget afterward. Missing metadata or direct providers retain your manual settings. Model names alone never imply a million-token budget. Requests send `context_window` and `response_reserved` at the top level. The server budgets history rather than enforcing a 30-message limit.

**Compact conversation** retains the last four user turns in future requests using a persisted, clamped `contextStart` boundary. Earlier messages stay visible and saved, with an **Earlier messages not sent** banner. This is not summarization. Compaction clears last-request metadata and refreshes the preview. Autosave retains up to 500 messages per conversation as a storage safety bound, not a user context limit; start another chat at that bound. Attachment requests retain their separate 12-attachment history safety limit (6 per message); compact older attachment turns, remove draft attachments, or start a new conversation when exceeded. No hidden attachments are silently discarded.

### Chat model, drops and project readiness

The compact composer model button shows the exact active ID. Open it to search the 9router catalog or edit a direct-provider ID with its current default as a suggestion. The picker shares the debounced `/agent/models` loader with connection settings; no provider switch or fallback model occurs. Selection persists only non-secret preferences, refreshes context estimates, and is disabled while a chat send is pending. Escape and outside click close the picker.

Drop documents or images on the conversation/composer, or use Attach files; both use the same preparation pipeline and six-file/size/type limits. Directories are rejected with a folder-picker reminder. Busy drops are ignored with a notice; preparation remains bound to its original chat when switching chats.

A persistent header callout guides every chat without selected project files to Select project. Attachments do not count as a directory: Send requires a connected workspace or selected snapshot source. Empty/deselected snapshots retain the tutorial. Restored local projects display a persistent Reconnect reminder and cannot send until explicitly reconnected.

### Rich messages and file preview

Assistant replies use `react-markdown` with `remark-gfm` for headings, emphasis,
lists/tasks, tables, and fenced code with language labels and copy feedback.
Raw HTML is shown as text, never executed. Markdown images are blocked (including
remote tracking images); external HTTP(S)/mail links require a click and use
`noopener noreferrer`. Only prepared PNG/JPEG/WEBP data images are displayed.

Use **Preview files** in the conversation header to browse selected project files,
or click an exact eligible path rendered as inline code/a Markdown link. Unknown
paths never trigger a filesystem read. Browser folders show their selected saved
snapshot. Connected local workspaces explicitly fetch the bounded allowed snapshot
on opening/selecting a project preview, not automatically or through the provider.
The current backend uses `workspace_id` as its capability; preview also sends it as
`token`, or uses a separate `token` returned by connection when available. Preview
capabilities are kept only in memory and reconnect is required after reload.
Snapshots are not live views and can be stale after disk edits.

Draft and sent attachment cards open the same pane. Text/code stays selectable
plain text (HTML/SVG/scripts are never executed); copy/download preserves its text.
PDF/DOCX show **Extracted document text**, not a rendered original. Their downloads
are `.txt` because original bytes are unavailable. Image downloads use the actual
safe data URL. Desktop keeps the composer beneath the conversation beside the pane;
mobile uses a full-width modal with Escape/close, focus containment and return.
Preview selection/content is ephemeral, reset on chat changes, and never autosaved.

`npm test` includes actual Chrome fixtures (1280px/390px) for formatting, escaped
HTML, blocked images, local capability mock, existing browser files, attachments,
extracted downloads, responsive layout, close/reopen, and reconnect isolation.
Chrome fixtures skip when Google Chrome is not installed at the macOS test path.
