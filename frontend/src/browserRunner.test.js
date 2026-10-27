import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { execFile } from 'node:child_process';
import { parseResult } from './compiler.js';
import {
  runBrowserCode,
  validateBrowserRun,
  normalizeLanguage,
  formatBrowserResult,
  createStdinReader,
  SOURCE_LIMIT,
  STDIN_LIMIT,
  OUTPUT_CAP,
  BOOT_TIMEOUT_MS,
  RUN_TIMEOUT_MS,
  RUNNER_PATH,
  SUPPORTED_LANGUAGES,
} from './browserRunner.js';

// --- Helper to build a mock DOM environment for protocol unit tests ---
function createMockDomEnvironment() {
  const windowListeners = new Map();
  const createdIframes = [];

  const fakeWindow = {
    addEventListener(event, handler) {
      if (!windowListeners.has(event)) windowListeners.set(event, new Set());
      windowListeners.get(event).add(handler);
    },
    removeEventListener(event, handler) {
      if (windowListeners.has(event)) windowListeners.get(event).delete(handler);
    },
    postMessage(data, origin) {},
  };

  const fakeDocument = {
    createElement(tagName) {
      if (tagName.toLowerCase() !== 'iframe') throw new Error(`Unexpected element: ${tagName}`);
      const iframeListeners = new Map();
      const attributes = new Map();
      let parentNode = null;

      const iframeObj = {
        style: {},
        src: '',
        contentWindow: {
          postMessage(msg, targetOrigin) {
            iframeObj.lastPostedMessage = msg;
          },
        },
        setAttribute(name, value) {
          attributes.set(name, value);
        },
        getAttribute(name) {
          return attributes.get(name);
        },
        hasAttribute(name) {
          return attributes.has(name);
        },
        addEventListener(event, handler) {
          if (!iframeListeners.has(event)) iframeListeners.set(event, new Set());
          iframeListeners.get(event).add(handler);
        },
        removeEventListener(event, handler) {
          if (iframeListeners.has(event)) iframeListeners.get(event).delete(handler);
        },
        get parentNode() {
          return parentNode;
        },
        set parentNode(val) {
          parentNode = val;
        },
        _triggerIframeEvent(event, eventObj = {}) {
          const handlers = iframeListeners.get(event);
          if (handlers) {
            for (const handler of handlers) handler(eventObj);
          }
        },
      };

      createdIframes.push(iframeObj);
      return iframeObj;
    },
    body: {
      appendChild(child) {
        child.parentNode = fakeDocument.body;
        return child;
      },
      removeChild(child) {
        if (child.parentNode === fakeDocument.body) {
          child.parentNode = null;
        }
        return child;
      },
    },
  };

  function dispatchWindowMessage(data, sourceWindow) {
    const handlers = windowListeners.get('message');
    if (handlers) {
      const event = { data, source: sourceWindow };
      for (const handler of Array.from(handlers)) {
        handler(event);
      }
    }
  }

  return {
    window: fakeWindow,
    document: fakeDocument,
    createdIframes,
    dispatchWindowMessage,
  };
}

// ============================================================================
// 1. INPUT VALIDATION & LANGUAGE NORMALIZATION
// ============================================================================

test('language normalization recognizes javascript and python aliases and rejects others', () => {
  assert.equal(normalizeLanguage('javascript'), 'javascript');
  assert.equal(normalizeLanguage('JavaScript'), 'javascript');
  assert.equal(normalizeLanguage('js'), 'javascript');
  assert.equal(normalizeLanguage('JavaScript (Node.js 22)'), 'javascript');
  assert.equal(normalizeLanguage('python'), 'python');
  assert.equal(normalizeLanguage('Python'), 'python');
  assert.equal(normalizeLanguage('py'), 'python');
  assert.equal(normalizeLanguage('Python (3.13.2)'), 'python');

  assert.equal(normalizeLanguage('c++'), null);
  assert.equal(normalizeLanguage('rust'), null);
  assert.equal(normalizeLanguage(''), null);
  assert.equal(normalizeLanguage(null), null);
  assert.equal(normalizeLanguage(undefined), null);
});

test('validateBrowserRun enforces limits and required fields', () => {
  // Valid
  assert.equal(validateBrowserRun({ language: 'javascript', source: 'console.log(1)', stdin: '' }), '');
  assert.equal(validateBrowserRun({ language: 'python', source: 'print(1)', stdin: 'a'.repeat(STDIN_LIMIT) }), '');
  assert.equal(validateBrowserRun({ language: 'javascript', source: 'x'.repeat(SOURCE_LIMIT), stdin: '' }), '');

  // Invalid language
  assert.match(validateBrowserRun({ language: 'ruby', source: 'puts 1', stdin: '' }), /Unsupported/);
  assert.match(validateBrowserRun({ language: '', source: 'puts 1', stdin: '' }), /Unsupported/);

  // Missing or whitespace source
  assert.match(validateBrowserRun({ language: 'javascript', source: '', stdin: '' }), /Enter source code/);
  assert.match(validateBrowserRun({ language: 'javascript', source: '   \n  ', stdin: '' }), /Enter source code/);

  // Exceeded source limit
  assert.match(validateBrowserRun({ language: 'javascript', source: 'x'.repeat(SOURCE_LIMIT + 1), stdin: '' }), /Source exceeds/);

  // Exceeded stdin limit
  assert.match(validateBrowserRun({ language: 'javascript', source: 'console.log(1)', stdin: 'a'.repeat(STDIN_LIMIT + 1) }), /Standard input exceeds/);
});

test('runBrowserCode immediately rejects invalid source, stdin, or language without creating iframes', async () => {
  const r1 = runBrowserCode({ language: 'unsupported', source: 'foo', stdin: '' });
  assert.equal(typeof r1.cancel, 'function');
  await assert.rejects(r1.promise, /Unsupported browser execution language/);

  const r2 = runBrowserCode({ language: 'javascript', source: '   ', stdin: '' });
  await assert.rejects(r2.promise, /Enter source code/);

  const r3 = runBrowserCode({ language: 'javascript', source: 'x'.repeat(SOURCE_LIMIT + 1), stdin: '' });
  await assert.rejects(r3.promise, /Source exceeds/);

  const r4 = runBrowserCode({ language: 'javascript', source: 'console.log(1)', stdin: 'a'.repeat(STDIN_LIMIT + 1) });
  await assert.rejects(r4.promise, /Standard input exceeds/);
});

// ============================================================================
// 2. COMPILER CONTRACT COMPLIANCE & RESULT FORMATTING
// ============================================================================

test('formatBrowserResult adheres to compiler contract and passes parseResult', () => {
  const accepted = formatBrowserResult({
    status: { id: 3, description: 'Accepted' },
    stdout: 'Hello World\n',
    stderr: '',
    compile_output: '',
    message: '',
    exit_code: 0,
    time: '0.015',
    memory: null,
    truncated: false,
  });
  assert.deepEqual(parseResult(accepted), accepted);
  assert.equal(accepted.status.id, 3);
  assert.equal(accepted.exit_code, 0);

  const timeout = formatBrowserResult({
    status: { id: 5, description: 'Time Limit Exceeded' },
    stdout: '',
    stderr: '',
    compile_output: '',
    message: 'Time limit exceeded (5.0s)',
    exit_code: null,
    time: '5.000',
    memory: null,
    truncated: false,
  });
  assert.deepEqual(parseResult(timeout), timeout);
  assert.equal(timeout.status.id, 5);
  assert.equal(timeout.exit_code, null);

  const compileErr = formatBrowserResult({
    status: { id: 6, description: 'Compilation Error' },
    stdout: '',
    stderr: '',
    compile_output: 'SyntaxError: Unexpected token',
    message: '',
    exit_code: null,
    time: null,
    memory: null,
    truncated: false,
  });
  assert.deepEqual(parseResult(compileErr), compileErr);
  assert.equal(compileErr.status.id, 6);
  assert.equal(compileErr.exit_code, null);

  const runtimeErr = formatBrowserResult({
    status: { id: 11, description: 'Runtime Error (NZEC)' },
    stdout: '',
    stderr: 'ReferenceError: x is not defined',
    compile_output: '',
    message: 'ReferenceError: x is not defined',
    exit_code: 1,
    time: '0.005',
    memory: null,
    truncated: false,
  });
  assert.deepEqual(parseResult(runtimeErr), runtimeErr);
  assert.equal(runtimeErr.status.id, 11);
  assert.equal(runtimeErr.exit_code, 1);
});

test('formatBrowserResult truncates output streams exceeding 64KB', () => {
  const oversized = 'z'.repeat(OUTPUT_CAP + 100);
  const res = formatBrowserResult({
    stdout: oversized,
    stderr: oversized,
    compile_output: oversized,
  });
  assert.equal(res.stdout.length, OUTPUT_CAP);
  assert.equal(res.stderr.length, OUTPUT_CAP);
  assert.equal(res.compile_output.length, OUTPUT_CAP);
  assert.equal(res.truncated, true);
  assert.doesNotThrow(() => parseResult(res));
});

// ============================================================================
// 3. STDIN READER HELPER
// ============================================================================

test('createStdinReader parses lines sequentially and handles EOF and reset', () => {
  const reader = createStdinReader('line1\r\nline2\nline3');
  assert.equal(reader.readline(), 'line1');
  assert.equal(reader.input(), 'line2');
  assert.equal(reader.readline(), 'line3');
  assert.equal(reader.readline(), ''); // EOF
  assert.equal(reader.input(), ''); // EOF

  reader.reset();
  assert.equal(reader.readline(), 'line1');
  assert.equal(reader.read(), 'line2\nline3');
  assert.equal(reader.read(), '');
});

// ============================================================================
// 4. PROTOCOL & LIFECYCLE GUARDS IN SIMULATED DOM
// ============================================================================

test('sandboxed iframe has allow-scripts without allow-same-origin and points to runner HTML', async () => {
  const mockDom = createMockDomEnvironment();
  const originalWindow = globalThis.window;
  const originalDocument = globalThis.document;
  globalThis.window = mockDom.window;
  globalThis.document = mockDom.document;

  try {
    const { promise } = runBrowserCode({
      language: 'javascript',
      source: 'console.log(42);',
    });

    assert.equal(mockDom.createdIframes.length, 1);
    const iframe = mockDom.createdIframes[0];

    // CRITICAL: sandbox must be "allow-scripts" and must NOT contain "allow-same-origin"
    const sandboxAttr = iframe.getAttribute('sandbox');
    assert.ok(sandboxAttr.includes('allow-scripts'), 'iframe has allow-scripts');
    assert.ok(!sandboxAttr.includes('allow-same-origin'), 'iframe MUST NOT have allow-same-origin');
    assert.equal(iframe.src, RUNNER_PATH);

    // Simulate ready handshake from iframe
    mockDom.dispatchWindowMessage({ type: 'ready' }, iframe.contentWindow);
    assert.ok(iframe.lastPostedMessage, 'parent posted message to iframe');
    assert.equal(iframe.lastPostedMessage.type, 'run');
    assert.equal(iframe.lastPostedMessage.language, 'javascript');
    assert.equal(iframe.lastPostedMessage.source, 'console.log(42);');

    // Respond with result
    mockDom.dispatchWindowMessage({
      type: 'result',
      runId: iframe.lastPostedMessage.runId,
      result: {
        status: { id: 3, description: 'Accepted' },
        stdout: '42\n',
        stderr: '',
        compile_output: '',
        message: '',
        exit_code: 0,
        time: '0.002',
        memory: null,
        truncated: false,
      },
    }, iframe.contentWindow);

    const result = await promise;
    assert.equal(result.stdout, '42\n');
    assert.equal(result.status.id, 3);
    assert.equal(iframe.parentNode, null, 'iframe is removed after completion');
  } finally {
    globalThis.window = originalWindow;
    globalThis.document = originalDocument;
  }
});

test('protocol ignores messages with wrong source window or mismatched runId', async () => {
  const mockDom = createMockDomEnvironment();
  const originalWindow = globalThis.window;
  const originalDocument = globalThis.document;
  globalThis.window = mockDom.window;
  globalThis.document = mockDom.document;

  try {
    const { promise } = runBrowserCode({
      language: 'javascript',
      source: 'console.log(1);',
    });

    const iframe = mockDom.createdIframes[0];
    mockDom.dispatchWindowMessage({ type: 'ready' }, iframe.contentWindow);
    const validRunId = iframe.lastPostedMessage.runId;

    // 1. Message from unrelated window: should be ignored
    const fakeOtherWindow = {};
    mockDom.dispatchWindowMessage({
      type: 'result',
      runId: validRunId,
      result: { stdout: 'spoofed\n' },
    }, fakeOtherWindow);

    // 2. Message with mismatched runId: should be ignored
    mockDom.dispatchWindowMessage({
      type: 'result',
      runId: 'different-run-id',
      result: { stdout: 'wrong-id\n' },
    }, iframe.contentWindow);

    // 3. Legitimate message from iframe with valid runId: should be processed
    mockDom.dispatchWindowMessage({
      type: 'result',
      runId: validRunId,
      result: {
        status: { id: 3, description: 'Accepted' },
        stdout: 'genuine\n',
        stderr: '',
        compile_output: '',
        message: '',
        exit_code: 0,
        time: '0.001',
        memory: null,
        truncated: false,
      },
    }, iframe.contentWindow);

    const res = await promise;
    assert.equal(res.stdout, 'genuine\n');
  } finally {
    globalThis.window = originalWindow;
    globalThis.document = originalDocument;
  }
});

test('cancellation during boot terminates runtime, removes iframe, and rejects promise', async () => {
  const mockDom = createMockDomEnvironment();
  const originalWindow = globalThis.window;
  const originalDocument = globalThis.document;
  globalThis.window = mockDom.window;
  globalThis.document = mockDom.document;

  try {
    const { promise, cancel } = runBrowserCode({
      language: 'python',
      source: 'print("hello")',
    });

    const iframe = mockDom.createdIframes[0];
    assert.ok(iframe.parentNode !== null, 'iframe is in DOM during boot');

    // Cancel while still booting
    cancel();

    await assert.rejects(promise, /Execution cancelled during runtime initialization/);
    assert.equal(iframe.parentNode, null, 'iframe removed from DOM upon cancellation');
  } finally {
    globalThis.window = originalWindow;
    globalThis.document = originalDocument;
  }
});

test('cancellation during execution removes iframe and rejects promise', async () => {
  const mockDom = createMockDomEnvironment();
  const originalWindow = globalThis.window;
  const originalDocument = globalThis.document;
  globalThis.window = mockDom.window;
  globalThis.document = mockDom.document;

  try {
    const statuses = [];
    const { promise, cancel } = runBrowserCode({
      language: 'javascript',
      source: 'while(true){}',
      onStatus: (s) => statuses.push(s),
    });

    const iframe = mockDom.createdIframes[0];
    mockDom.dispatchWindowMessage({ type: 'ready' }, iframe.contentWindow);
    const runId = iframe.lastPostedMessage.runId;

    // Notify running stage
    mockDom.dispatchWindowMessage({
      type: 'status',
      runId,
      stage: 'running',
      message: 'Running JavaScript...',
    }, iframe.contentWindow);

    assert.ok(statuses.some((s) => s.stage === 'running'));

    // Cancel while running
    cancel();

    await assert.rejects(promise, /Execution cancelled/);
    assert.equal(iframe.parentNode, null, 'iframe removed from DOM');
  } finally {
    globalThis.window = originalWindow;
    globalThis.document = originalDocument;
  }
});

test('runner error message from iframe rejects promise with error detail', async () => {
  const mockDom = createMockDomEnvironment();
  const originalWindow = globalThis.window;
  const originalDocument = globalThis.document;
  globalThis.window = mockDom.window;
  globalThis.document = mockDom.document;

  try {
    const { promise } = runBrowserCode({
      language: 'python',
      source: 'print(1)',
    });

    const iframe = mockDom.createdIframes[0];
    mockDom.dispatchWindowMessage({ type: 'ready' }, iframe.contentWindow);
    const runId = iframe.lastPostedMessage.runId;

    mockDom.dispatchWindowMessage({
      type: 'error',
      runId,
      message: 'Pyodide initialization failed: Network error',
    }, iframe.contentWindow);

    await assert.rejects(promise, /Pyodide initialization failed: Network error/);
    assert.equal(iframe.parentNode, null);
  } finally {
    globalThis.window = originalWindow;
    globalThis.document = originalDocument;
  }
});

test('simulated boot timeout removes iframe and rejects with 90s timeout error', async () => {
  const mockDom = createMockDomEnvironment();
  const originalWindow = globalThis.window;
  const originalDocument = globalThis.document;
  const originalSetTimeout = globalThis.setTimeout;
  globalThis.window = mockDom.window;
  globalThis.document = mockDom.document;

  let capturedBootCallback = null;
  globalThis.setTimeout = (fn, ms) => {
    if (ms === BOOT_TIMEOUT_MS) {
      capturedBootCallback = fn;
      return 999;
    }
    return originalSetTimeout(fn, ms);
  };

  try {
    const { promise } = runBrowserCode({
      language: 'python',
      source: 'print("hello")',
    });

    const iframe = mockDom.createdIframes[0];
    assert.ok(iframe.parentNode !== null);

    // Trigger boot timeout callback
    assert.ok(capturedBootCallback, 'boot timeout was registered');
    capturedBootCallback();

    await assert.rejects(promise, /Python runtime initialization timed out \(90s\)/);
    assert.equal(iframe.parentNode, null, 'iframe was removed upon boot timeout');
  } finally {
    globalThis.setTimeout = originalSetTimeout;
    globalThis.window = originalWindow;
    globalThis.document = originalDocument;
  }
});

test('simulated run timeout removes iframe and resolves with status 5 Time Limit Exceeded', async () => {
  const mockDom = createMockDomEnvironment();
  const originalWindow = globalThis.window;
  const originalDocument = globalThis.document;
  const originalSetTimeout = globalThis.setTimeout;
  globalThis.window = mockDom.window;
  globalThis.document = mockDom.document;

  let capturedRunCallback = null;
  globalThis.setTimeout = (fn, ms) => {
    if (ms === RUN_TIMEOUT_MS) {
      capturedRunCallback = fn;
      return 888;
    }
    return originalSetTimeout(fn, ms);
  };

  try {
    const { promise } = runBrowserCode({
      language: 'javascript',
      source: 'while(true){}',
    });

    const iframe = mockDom.createdIframes[0];
    mockDom.dispatchWindowMessage({ type: 'ready' }, iframe.contentWindow);
    const runId = iframe.lastPostedMessage.runId;

    // Transition to running
    mockDom.dispatchWindowMessage({
      type: 'status',
      runId,
      stage: 'running',
      message: 'Running code...',
    }, iframe.contentWindow);

    assert.ok(capturedRunCallback, 'run timer was registered');
    capturedRunCallback();

    const result = await promise;
    assert.equal(result.status.id, 5);
    assert.equal(result.status.description, 'Time Limit Exceeded');
    assert.match(result.message, /Time limit exceeded/);
    assert.equal(iframe.parentNode, null, 'iframe removed upon run timeout');
    assert.doesNotThrow(() => parseResult(result));
  } finally {
    globalThis.setTimeout = originalSetTimeout;
    globalThis.window = originalWindow;
    globalThis.document = originalDocument;
  }
});

// ============================================================================
// 5. CHROME REAL BROWSER HARNESS (RUNS IF CHROME IS DETECTED)
// ============================================================================

const CHROME_PATH = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const isChromeAvailable = fs.existsSync(CHROME_PATH);

const runnerHtmlPath = new URL('../public/browser-runner.html', import.meta.url);
const runnerHtml = fs.readFileSync(runnerHtmlPath, 'utf8');

test('Chrome harness verifies sandboxed execution, stdin helper, and security bounds', { skip: !isChromeAvailable }, async () => {

  // Launch a temporary local test server
  const server = http.createServer((req, res) => {
    if (req.url === '/') {
      res.writeHead(200, { 'Content-Type': 'text/html' });
      res.end(`<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body>
  <iframe id="runner" sandbox="allow-scripts" src="/browser-runner.html"></iframe>
  <script>
    window.addEventListener("message", (e) => {
      if (e.data && e.data.type === "ready") {
        e.source.postMessage({
          type: "run",
          runId: "chrome-test-1",
          language: "javascript",
          source: [
            "const name = readline();",
            "console.log('Hello ' + name);",
            "console.error('Diagnostic note');",
          ].join('\\n'),
          stdin: "Alice\\n"
        }, "*");
      } else if (e.data && e.data.type === "result") {
        fetch("/report?data=" + encodeURIComponent(JSON.stringify(e.data.result)));
      }
    });
  </script>
</body>
</html>`);
    } else if (req.url === '/browser-runner.html') {
      res.writeHead(200, { 'Content-Type': 'text/html' });
      res.end(runnerHtml);
    } else if (req.url.startsWith('/report')) {
      const resultData = JSON.parse(decodeURIComponent(req.url.split('?data=')[1]));
      res.writeHead(200);
      res.end('ok');
      server.emit('test-result', resultData);
    } else {
      res.writeHead(404);
      res.end();
    }
  });

  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;

  const resultPromise = new Promise((resolve, reject) => {
    server.on('test-result', resolve);
    setTimeout(() => reject(new Error('Chrome test timed out')), 10000);
  });

  const chromeProc = execFile(CHROME_PATH, [
    '--headless=new',
    '--disable-gpu',
    `http://127.0.0.1:${port}/`,
  ]);

  try {
    const result = await resultPromise;
    assert.equal(result.status.id, 3);
    assert.equal(result.status.description, 'Accepted');
    assert.equal(result.stdout, 'Hello Alice\n');
    assert.match(result.stderr, /Diagnostic note/);
    assert.equal(result.exit_code, 0);
    assert.equal(result.truncated, false);
    assert.doesNotThrow(() => parseResult(result));
  } finally {
    chromeProc.kill();
    server.close();
  }
});

test('Chrome harness verifies CSP blocks network to local backend from sandbox worker', { skip: !isChromeAvailable }, async () => {
  let secretAccessed = false;

  const server = http.createServer((req, res) => {
    if (req.url === '/') {
      res.writeHead(200, { 'Content-Type': 'text/html' });
      res.end(`<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body>
  <iframe id="runner" sandbox="allow-scripts" src="/browser-runner.html"></iframe>
  <script>
    window.addEventListener("message", (e) => {
      if (e.data && e.data.type === "ready") {
        e.source.postMessage({
          type: "run",
          runId: "csp-test-1",
          language: "javascript",
          source: [
            "try {",
            "  await fetch('http://127.0.0.1:" + location.port + "/forbidden-backend');",
            "  console.log('UNSAFE_FETCH_SUCCEEDED');",
            "} catch (e) {",
            "  console.log('SAFE_FETCH_BLOCKED: ' + e.message);",
            "}"
          ].join('\\n'),
          stdin: ""
        }, "*");
      } else if (e.data && e.data.type === "result") {
        fetch("/report?data=" + encodeURIComponent(JSON.stringify(e.data.result)));
      }
    });
  </script>
</body>
</html>`);
    } else if (req.url === '/browser-runner.html') {
      res.writeHead(200, { 'Content-Type': 'text/html' });
      res.end(runnerHtml);
    } else if (req.url === '/forbidden-backend') {
      secretAccessed = true;
      res.writeHead(200);
      res.end('secret');
    } else if (req.url.startsWith('/report')) {
      const resultData = JSON.parse(decodeURIComponent(req.url.split('?data=')[1]));
      res.writeHead(200);
      res.end('ok');
      server.emit('test-result', resultData);
    } else {
      res.writeHead(404);
      res.end();
    }
  });

  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;

  const resultPromise = new Promise((resolve, reject) => {
    server.on('test-result', resolve);
    setTimeout(() => reject(new Error('Chrome CSP test timed out')), 10000);
  });

  const chromeProc = execFile(CHROME_PATH, [
    '--headless=new',
    '--disable-gpu',
    `http://127.0.0.1:${port}/`,
  ]);

  try {
    const result = await resultPromise;
    assert.equal(secretAccessed, false, 'Local backend was not accessed');
    assert.match(result.stdout, /SAFE_FETCH_BLOCKED/);
    assert.ok(!result.stdout.includes('UNSAFE_FETCH_SUCCEEDED'));
  } finally {
    chromeProc.kill();
    server.close();
  }
});

test('Chrome harness verifies syntax and runtime errors and storage isolation in sandbox', { skip: !isChromeAvailable }, async () => {
  const server = http.createServer((req, res) => {
    if (req.url === '/') {
      res.writeHead(200, { 'Content-Type': 'text/html' });
      res.end(`<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body>
  <iframe id="runner" sandbox="allow-scripts" src="/browser-runner.html"></iframe>
  <script>
    const reports = [];
    window.addEventListener("message", (e) => {
      if (e.data && e.data.type === "ready") {
        // Run test 1: Syntax error
        e.source.postMessage({
          type: "run",
          runId: "syntax-run",
          language: "javascript",
          source: "function { bad syntax",
          stdin: ""
        }, "*");
      } else if (e.data && e.data.type === "result") {
        if (e.data.runId === "syntax-run") {
          reports.push({ test: "syntax", result: e.data.result });
          // Run test 2: Runtime error
          e.source.postMessage({
            type: "run",
            runId: "runtime-run",
            language: "javascript",
            source: "throw new Error('TestRuntimeCrash');",
            stdin: ""
          }, "*");
        } else if (e.data.runId === "runtime-run") {
          reports.push({ test: "runtime", result: e.data.result });
          // Run test 3: Storage isolation check
          e.source.postMessage({
            type: "run",
            runId: "storage-run",
            language: "javascript",
            source: [
              "try {",
              "  console.log('WORKER_ORIGIN: ' + self.location.origin);",
              "} catch (e) {",
              "  console.log('ORIGIN_ERROR: ' + e.message);",
              "}"
            ].join('\\n'),
            stdin: ""
          }, "*");
        } else if (e.data.runId === "storage-run") {
          reports.push({ test: "storage", result: e.data.result });
          fetch("/report?data=" + encodeURIComponent(JSON.stringify(reports)));
        }
      }
    });
  </script>
</body>
</html>`);
    } else if (req.url === '/browser-runner.html') {
      res.writeHead(200, { 'Content-Type': 'text/html' });
      res.end(runnerHtml);
    } else if (req.url.startsWith('/report')) {
      const reportsData = JSON.parse(decodeURIComponent(req.url.split('?data=')[1]));
      res.writeHead(200);
      res.end('ok');
      server.emit('test-reports', reportsData);
    } else {
      res.writeHead(404);
      res.end();
    }
  });

  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;

  const reportsPromise = new Promise((resolve, reject) => {
    server.on('test-reports', resolve);
    setTimeout(() => reject(new Error('Chrome error/isolation test timed out')), 10000);
  });

  const chromeProc = execFile(CHROME_PATH, [
    '--headless=new',
    '--disable-gpu',
    `http://127.0.0.1:${port}/`,
  ]);

  try {
    const reports = await reportsPromise;
    const syntaxReport = reports.find((r) => r.test === 'syntax');
    assert.equal(syntaxReport.result.status.id, 6, 'Syntax error reported as status 6');
    assert.equal(syntaxReport.result.status.description, 'Compilation Error');
    assert.ok(syntaxReport.result.compile_output.length > 0);

    const runtimeReport = reports.find((r) => r.test === 'runtime');
    assert.equal(runtimeReport.result.status.id, 11, 'Runtime error reported as status 11');
    assert.equal(runtimeReport.result.status.description, 'Runtime Error (NZEC)');
    assert.match(runtimeReport.result.stderr, /TestRuntimeCrash/);
    assert.equal(runtimeReport.result.exit_code, 1);

    const storageReport = reports.find((r) => r.test === 'storage');
    assert.match(storageReport.result.stdout, /WORKER_ORIGIN: null/);
  } finally {
    chromeProc.kill();
    server.close();
  }
});
