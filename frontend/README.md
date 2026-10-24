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

Connection settings support OpenAI, Groq, xAI, OpenRouter, custom OpenAI-compatible APIs, and an explicitly separate 9router mode. Model IDs are editable. Keys remain in React memory only and clear on reload; only non-secret preferences persist. Use URL fields without embedded credentials or query parameters. Changing providers clears the current direct API key. Requests go to the local backend, which forwards them to the chosen service.

A completed analysis shows the actual returned report and tool trace. There are no fabricated results or simulated live tool events. Follow-up messages use the returned session ID; new analysis clears the conversation. Runtime execution is unavailable and fails closed; submitted code is never executed on the host. An independently isolated worker is required to implement that tool.

The included Python example demonstrates an empty-list failure and an inclusive passing-score boundary. Tests cover settings validation and secret-exclusion from persisted preferences. Real AI results require a running backend and valid provider configuration.
