# Codebook frontend

React + Vite student Python analysis workbench. Run the backend at `127.0.0.1:8000`, then:

```sh
npm install
npm run dev
```

Vite proxies `/agent` and `/health` to the local backend. For deployment, configure the same-origin reverse proxy separately; Vite's development proxy is not a production API server.

```sh
npm test
npm run build
```

Connection settings support OpenAI, Groq, xAI, OpenRouter, custom OpenAI-compatible APIs, and an explicitly separate 9router mode. Direct-provider model IDs are editable. In 9router mode, opening settings or editing the gateway URL/key automatically loads `/agent/models` after a 400 ms debounce. The native model dropdown shows exact gateway IDs, with loading, empty, and error feedback and a Reload models button. Previously selected IDs remain visible (marked unavailable if absent); no alternative is selected automatically. Closing settings or switching to direct mode cancels loading, and changed credentials clear the old catalog immediately. Keys remain in React memory only and clear on reload; only non-secret preferences persist. Use URL fields without embedded credentials or query parameters. Changing providers clears the current direct API key. Requests go to the local backend, which forwards them to the chosen service.

A completed analysis shows the actual returned report and tool trace. There are no fabricated results or simulated live tool events. Follow-up messages use the returned session ID; new analysis clears the conversation. Runtime execution is unavailable and fails closed; submitted code is never executed on the host. An independently isolated worker is required to implement that tool.

The included Python example demonstrates an empty-list failure and an inclusive passing-score boundary. Tests cover settings validation, secret-exclusion from persisted preferences, model catalog responses, debounce cancellation, and stale-response suppression. Real AI results require a running backend and valid provider configuration.

## Project Chat (default workspace)

The default view is a directory-based, read-only project conversation. Python review remains available from the sidebar; both views share the same connection settings and router model catalog. Choose a folder with the browser directory picker, review/deselect files and expand any file to inspect its exact contents before sending. Folder selection reads locally, not over the network. Each Send posts selected files, that conversation's history ending with the current prompt, and connection settings to `/agent/project-chat`. Nothing is automatically edited or executed.

Snapshots are frozen after the first successful send, keeping each conversation's context consistent. Before sending, reselect the folder to refresh; afterward start a new chat for updated files. Chats, drafts, file contents and keys stay in memory only. Reload clears them. Requests are keyed to conversation IDs, so switching conversations cannot misroute replies. Failed requests retain the draft and do not append phantom messages. Each conversation supports 30 total messages (15 exchanges).

The local filter rejects unsafe paths, environment/secret/credential/key files, private/generated folders, lockfiles, binaries, unknown extensions and files larger than 30,000 bytes before reading. UTF-8 text is accepted only within 30,000 characters per file, 200,000 total characters and 80 files. Conservative byte checks may exclude some multibyte text below the character limit. Over-budget files are skipped whole, never silently truncated, with reasons shown for review. These checks do not replace backend validation or human review for secrets embedded in otherwise ordinary source files. Assistant Markdown supports headings, lists and fenced code as escaped React text, never raw HTML.

`npm test` includes snapshot filtering/budget and conversation-payload regressions alongside the existing settings/catalog checks. No external inference is required for these tests.
