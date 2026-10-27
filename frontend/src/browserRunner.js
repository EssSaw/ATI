/**
 * Browser Execution Engine for JavaScript and Python
 *
 * Runs client-side user code in an isolated sandboxed iframe with opaque origin
 * (sandbox="allow-scripts" WITHOUT allow-same-origin) and strict CSP blocking network
 * access except pinned Pyodide CDN assets. Code is executed off-main-thread via Web Worker
 * and fails closed when isolated worker creation is unavailable.
 */

export const SOURCE_LIMIT = 50_000;
export const STDIN_LIMIT = 20_000;
export const OUTPUT_CAP = 65_536; // 64 KB
export const BOOT_TIMEOUT_MS = 90_000; // 90s for runtime boot (Pyodide initial download / init)
export const RUN_TIMEOUT_MS = 5_000; // 5s for code execution
export const RUNNER_PATH = '/browser-runner.html';
export const PYODIDE_VERSION = '0.26.4';
export const PYODIDE_CDN_URL = 'https://cdn.jsdelivr.net/pyodide/v0.26.4/full/';

export const SUPPORTED_LANGUAGES = Object.freeze(['javascript', 'python']);

export function normalizeLanguage(language) {
  if (typeof language !== 'string') return null;
  const lang = language.toLowerCase().trim();
  if (lang === 'javascript' || lang === 'js' || /javascript|node\.js/i.test(lang)) {
    return 'javascript';
  }
  if (lang === 'python' || lang === 'py' || /python/i.test(lang)) {
    return 'python';
  }
  return null;
}

export function validateBrowserRun({ language, source, stdin }) {
  const normLang = normalizeLanguage(language);
  if (!normLang) {
    return `Unsupported browser execution language "${language}". Only JavaScript and Python are supported.`;
  }
  if (typeof source !== 'string' || !source.trim()) {
    return 'Enter source code before running.';
  }
  if (source.length > SOURCE_LIMIT) {
    return `Source exceeds ${SOURCE_LIMIT.toLocaleString('en-US')} characters.`;
  }
  if (typeof stdin === 'string' && stdin.length > STDIN_LIMIT) {
    return `Standard input exceeds ${STDIN_LIMIT.toLocaleString('en-US')} characters.`;
  }
  return '';
}

export function createStdinReader(stdin = '') {
  const lines = String(stdin || '').split(/\r?\n/);
  let index = 0;
  return {
    readline() {
      return index < lines.length ? lines[index++] : '';
    },
    input() {
      return index < lines.length ? lines[index++] : '';
    },
    read() {
      if (index >= lines.length) return '';
      const rest = lines.slice(index).join('\n');
      index = lines.length;
      return rest;
    },
    reset() {
      index = 0;
    }
  };
}

export function formatBrowserResult(data = {}) {
  let {
    status = { id: 3, description: 'Accepted' },
    stdout = '',
    stderr = '',
    compile_output = '',
    message = '',
    exit_code = 0,
    time = null,
    memory = null,
    truncated = false
  } = data;

  stdout = typeof stdout === 'string' ? stdout : '';
  stderr = typeof stderr === 'string' ? stderr : '';
  compile_output = typeof compile_output === 'string' ? compile_output : '';
  message = typeof message === 'string' ? message : '';

  if (stdout.length > OUTPUT_CAP) {
    stdout = stdout.slice(0, OUTPUT_CAP);
    truncated = true;
  }
  if (stderr.length > OUTPUT_CAP) {
    stderr = stderr.slice(0, OUTPUT_CAP);
    truncated = true;
  }
  if (compile_output.length > OUTPUT_CAP) {
    compile_output = compile_output.slice(0, OUTPUT_CAP);
    truncated = true;
  }

  return {
    status: {
      id: Number.isInteger(status?.id) ? status.id : 3,
      description: typeof status?.description === 'string' ? status.description : 'Accepted'
    },
    stdout,
    stderr,
    compile_output,
    message,
    exit_code: exit_code === null || Number.isInteger(exit_code) ? exit_code : null,
    time: time === null || typeof time === 'string' ? time : null,
    memory: typeof memory === 'number' && Number.isFinite(memory) ? memory : null,
    truncated: Boolean(truncated)
  };
}

function generateRunId() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return 'run_' + Math.random().toString(36).slice(2, 11) + '_' + Date.now().toString(36);
}

/**
 * Execute code inside a sandboxed iframe with an opaque origin.
 *
 * @param {Object} options
 * @param {'javascript'|'python'} options.language - Target execution language
 * @param {string} options.source - User source code (max 50k chars)
 * @param {string} [options.stdin] - Standard input string (max 20k chars)
 * @param {Function} [options.onStatus] - Optional status callback ({ stage, message })
 * @returns {{ promise: Promise<Object>, cancel: () => void }}
 */
export function runBrowserCode({ language, source, stdin = '', onStatus }) {
  const validationError = validateBrowserRun({ language, source, stdin });
  if (validationError) {
    return {
      promise: Promise.reject(new Error(validationError)),
      cancel: () => {}
    };
  }

  const normalizedLanguage = normalizeLanguage(language);
  const stdinStr = stdin == null ? '' : String(stdin);
  const runId = generateRunId();

  let cancelFn = () => {};

  const promise = new Promise((resolve, reject) => {
    if (typeof window === 'undefined' || typeof document === 'undefined') {
      reject(new Error('Browser execution requires a browser DOM environment with iframe support.'));
      return;
    }

    let settled = false;
    let currentPhase = 'booting';
    let bootTimer = null;
    let runTimer = null;
    let payloadSent = false;

    const iframe = document.createElement('iframe');
    // Sandboxed iframe WITHOUT allow-same-origin: enforces opaque origin ("null")
    iframe.setAttribute('sandbox', 'allow-scripts');
    iframe.src = RUNNER_PATH;
    iframe.style.cssText = 'display:none;width:0;height:0;border:0;position:absolute;pointer-events:none;';

    function cleanup() {
      if (bootTimer) {
        clearTimeout(bootTimer);
        bootTimer = null;
      }
      if (runTimer) {
        clearTimeout(runTimer);
        runTimer = null;
      }
      window.removeEventListener('message', handleMessage);
      if (iframe.parentNode) {
        try {
          iframe.parentNode.removeChild(iframe);
        } catch (_) {}
      }
    }

    function settleSuccess(rawResult) {
      if (settled) return;
      settled = true;
      currentPhase = 'completed';
      cleanup();
      resolve(formatBrowserResult(rawResult));
    }

    function settleTimeout() {
      if (settled) return;
      settled = true;
      currentPhase = 'completed';
      cleanup();
      resolve(formatBrowserResult({
        status: { id: 5, description: 'Time Limit Exceeded' },
        stdout: '',
        stderr: '',
        compile_output: '',
        message: 'Time limit exceeded (5.0s)',
        exit_code: null,
        time: '5.000',
        memory: null,
        truncated: false
      }));
    }

    function settleError(error) {
      if (settled) return;
      settled = true;
      currentPhase = 'completed';
      cleanup();
      reject(error instanceof Error ? error : new Error(String(error)));
    }

    cancelFn = () => {
      if (settled) return;
      const wasBooting = currentPhase === 'booting';
      settled = true;
      currentPhase = 'cancelled';
      cleanup();
      if (wasBooting) {
        reject(new Error('Execution cancelled during runtime initialization'));
      } else {
        reject(new Error('Execution cancelled'));
      }
    };

    function startRunTimer() {
      if (runTimer) clearTimeout(runTimer);
      runTimer = setTimeout(() => {
        settleTimeout();
      }, RUN_TIMEOUT_MS);
    }

    function sendRunPayload() {
      if (payloadSent || settled) return;
      payloadSent = true;
      try {
        iframe.contentWindow.postMessage({
          type: 'run',
          runId,
          language: normalizedLanguage,
          source,
          stdin: stdinStr
        }, '*');
      } catch (err) {
        settleError(err);
      }
    }

    function handleMessage(event) {
      if (event.source !== iframe.contentWindow) return;
      const data = event.data;
      if (!data) return;

      if (data.type === 'ready') {
        sendRunPayload();
        return;
      }

      if (data.runId !== runId) return;

      if (data.type === 'status') {
        if (data.stage === 'running' && currentPhase !== 'running') {
          currentPhase = 'running';
          if (bootTimer) {
            clearTimeout(bootTimer);
            bootTimer = null;
          }
          startRunTimer();
        }
        onStatus?.({ stage: data.stage, message: data.message });
        return;
      }

      if (data.type === 'result') {
        settleSuccess(data.result);
        return;
      }

      if (data.type === 'error') {
        settleError(new Error(data.message || 'Execution failed'));
        return;
      }
    }

    window.addEventListener('message', handleMessage);

    // Boot timer: 90s for runtime download/initialization
    bootTimer = setTimeout(() => {
      if (currentPhase === 'booting' && !settled) {
        settleError(new Error('Python runtime initialization timed out (90s)'));
      }
    }, BOOT_TIMEOUT_MS);

    // Initial status update
    onStatus?.({
      stage: 'booting',
      message: normalizedLanguage === 'python'
        ? 'Loading Python runtime (Pyodide)...'
        : 'Initializing JavaScript sandbox...'
    });

    iframe.addEventListener('load', () => {
      setTimeout(sendRunPayload, 20);
    });

    iframe.addEventListener('error', () => {
      settleError(new Error('Failed to load sandbox runner iframe'));
    });

    try {
      document.body.appendChild(iframe);
    } catch (appendErr) {
      settleError(appendErr);
    }
  });

  return {
    promise,
    cancel: () => cancelFn()
  };
}
