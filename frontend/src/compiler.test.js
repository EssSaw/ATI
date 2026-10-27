import test from 'node:test';
import assert from 'node:assert/strict';
import { BROWSER_LANGUAGES, scheduleCompilerLoad, validateCompilerSettings, compilerRequest, defaultSource, parseLanguages, parseResult, SOURCE_LIMIT, STDIN_LIMIT, statusTone, validateRun } from './compiler.js';
import { defaults } from './state.js';
import { setTimeout as wait } from 'node:timers/promises';
const settings = { ...defaults, api_key: 'ai-secret', router_api_key: 'gateway-secret', compiler_mode: 'judge0', compiler_base_url: 'https://judge0.example', compiler_api_key: 'user-key', compiler_rapidapi_host: '' };
const connection = { base_url: 'https://judge0.example', api_key: 'user-key', rapidapi_host: '' };

const result = { status: { id: 3, description: 'Accepted' }, stdout: 'Hello\n', stderr: '', compile_output: '', message: '', exit_code: 0, time: '0.01', memory: 1024, truncated: false };

test('catalog uses actual version IDs, supports empty catalogs, and rejects malformed catalogs', () => {
  const languages = [{ id: 109, name: 'Python (3.13.2)' }, { id: 63, name: 'JavaScript (Node.js 12.14.0)' }];
  assert.deepEqual(parseLanguages({ languages }), languages);
  assert.deepEqual(parseLanguages({ languages: [] }), []);
  for (const data of [{}, { languages: [{ id: '71', name: 'Python' }] }, { languages: [{ id: 1, name: '' }] }, { languages: [languages[0], languages[0]] }]) assert.throws(() => parseLanguages(data));
});

test('starter code matches language names rather than version IDs', () => {
  for (const name of ['Python (3.13)', 'JavaScript (Node.js 22)', 'TypeScript (5.6)', 'C (GCC 14)', 'C++ (GCC 14)', 'Java (JDK 21)', 'C# (Mono)', 'Go (1.23)', 'Rust (1.85)', 'Ruby (3.4)', 'PHP (8.3)']) assert.ok(defaultSource(name), name);
  assert.match(defaultSource('C++ (GCC)'), /iostream/);
  assert.match(defaultSource('C# (Mono)'), /using System/);
  assert.match(defaultSource('JavaScript (Node.js)'), /console.log/);
  assert.equal(defaultSource('Unknown language'), '');
});

test('execution validation enforces exact character budgets and a selected numeric version', () => {
  assert.equal(validateRun(109, 'x'.repeat(SOURCE_LIMIT), 'a'.repeat(STDIN_LIMIT)), '');
  assert.match(validateRun(109, 'x'.repeat(SOURCE_LIMIT + 1), ''), /Source exceeds/);
  assert.match(validateRun(109, 'x', 'a'.repeat(STDIN_LIMIT + 1)), /Standard input exceeds/);
  assert.match(validateRun('109', 'x', ''), /language/);
  assert.match(validateRun(109, ' \n', ''), /source code/);
});

test('results preserve failures and empty streams without inventing successful execution', () => {
  assert.deepEqual(parseResult(result), result);
  for (const id of [4, 5, 6, 7, 11, 13, 14]) {
    assert.equal(statusTone(id), 'failure');
    assert.equal(parseResult({ ...result, status: { id, description: 'Failure' }, exit_code: null, time: null, memory: null }).status.id, id);
  }
  assert.equal(statusTone(3), 'success');
  assert.equal(statusTone(1), 'pending');
  assert.equal(statusTone(2), 'pending');
  assert.throws(() => parseResult({ ...result, stdout: null }));
  assert.throws(() => parseResult({ ...result, status: undefined }));
  assert.throws(() => parseResult({ ...result, memory: NaN }));
  assert.equal(parseResult({ ...result, truncated: true }).truncated, true);
});

test('compiler fetch uses the backend contract without AI provider settings', async () => {
  const original = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options) => { calls.push({ url, options }); return { ok: true, json: async () => result }; };
  try {
    const controller = new AbortController();
    await compilerRequest('languages', undefined, controller.signal, settings);
    const payload = { language_id: 109, source: 'print(1)', stdin: '' };
    await compilerRequest('run', payload, controller.signal, settings);
    assert.equal(calls[0].url, '/agent/compiler/languages');
    assert.equal(calls[0].options.method, 'POST');
    assert.deepEqual(JSON.parse(calls[0].options.body), { settings: connection });
    assert.equal(calls[1].url, '/agent/compiler/run');
    assert.equal(calls[1].options.method, 'POST');
    assert.deepEqual(JSON.parse(calls[1].options.body), { ...payload, settings: connection });
    assert.equal(calls[1].options.signal, controller.signal);
    globalThis.fetch = async () => ({ ok: false, status: 503, json: async () => ({ detail: 'Compiler service is not configured.' }) });
    await assert.rejects(compilerRequest('languages', undefined, undefined, settings), /Compiler service is not configured/);
    globalThis.fetch = async () => ({ ok: false, status: 502, json: async () => { throw new Error(); } });
    await assert.rejects(compilerRequest('languages', undefined, undefined, settings), /unreadable response \(502\)/);
  } finally { globalThis.fetch = original; }
});


test('browser is the free default and Judge0 requires an explicit safe connection', async () => {
  assert.equal(defaults.compiler_mode, 'browser');
  assert.equal(validateCompilerSettings(defaults), '');
  assert.deepEqual(BROWSER_LANGUAGES.map(item => item.id), ['javascript', 'python']);
  for (const language of ['javascript', 'python']) assert.equal(validateRun(language, 'print(1)', '', 'browser'), '');
  assert.match(validateRun(71, 'x', '', 'browser'), /language/);
  assert.match(validateCompilerSettings({ ...settings, compiler_api_key: '' }), /API key/);
  for (const compiler_base_url of ['', 'ftp://example.com', 'https://user:password@example.com', 'https://example.com?key=secret']) assert.match(validateCompilerSettings({ ...settings, compiler_base_url }), /base URL/);
  assert.match(validateCompilerSettings({ ...settings, compiler_rapidapi_host: 'https://example.com' }), /hostname/);
  assert.equal(validateCompilerSettings(settings), '');
  const original = globalThis.fetch;
  globalThis.fetch = () => assert.fail('browser or invalid settings must never send a request');
  try {
    await assert.rejects(compilerRequest('languages', undefined, undefined, defaults), /does not use/);
    await assert.rejects(compilerRequest('run', {}, undefined, { ...settings, compiler_api_key: '' }), /API key/);
    const cancelBrowser = scheduleCompilerLoad(defaults, () => assert.fail('browser catalog published'), 0);
    const cancelInvalid = scheduleCompilerLoad({ ...settings, compiler_api_key: '' }, () => assert.fail('invalid catalog published'), 0);
    await wait(10);
    cancelBrowser();
    cancelInvalid();
  } finally { globalThis.fetch = original; }
});

test('Judge0 catalog debounce aborts requests and suppresses stale results', async () => {
  const original = globalThis.fetch;
  let calls = 0;
  let resolve;
  let signal;
  globalThis.fetch = async (url, options) => { calls++; signal = options.signal; return new Promise(done => { resolve = done; }); };
  try {
    const cancelPending = scheduleCompilerLoad(settings, () => assert.fail('cancelled load published'), 10);
    cancelPending();
    await wait(20);
    assert.equal(calls, 0);
    const cancelActive = scheduleCompilerLoad(settings, () => assert.fail('stale catalog published'), 0);
    await wait(10);
    assert.equal(calls, 1);
    cancelActive();
    assert.equal(signal.aborted, true);
    resolve({ ok: true, json: async () => ({ languages: [{ id: 71, name: 'Python' }] }) });
    await wait(0);
  } finally { globalThis.fetch = original; }
});


test('Judge0 catalog publishes valid, empty and failed responses without fallback', async () => {
  const original = globalThis.fetch;
  try {
    for (const [response, expected] of [
      [{ languages: [{ id: 71, name: 'Python' }] }, { languages: [{ id: 71, name: 'Python' }], status: 'ready', error: '' }],
      [{ languages: [] }, { languages: [], status: 'ready', error: '' }],
      [{ languages: [{ id: '71', name: 'Python' }] }, { languages: [], status: 'error', error: 'The backend returned an invalid language catalog. Reload languages to retry.' }],
    ]) {
      globalThis.fetch = async () => ({ ok: true, json: async () => response });
      let published;
      const cancel = scheduleCompilerLoad(settings, next => { published = next; }, 0);
      await wait(10);
      assert.deepEqual(published, expected);
      cancel();
    }
  } finally { globalThis.fetch = original; }
});
