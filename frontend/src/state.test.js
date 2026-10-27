import test from 'node:test';
import assert from 'node:assert/strict';
import { defaults, preferences, validateSettings } from './state.js';
import { applyModelLimits, scheduleModelLoad } from './models.js';
import { setTimeout as wait } from 'node:timers/promises';

test('catalog loads exact IDs with only gateway settings and an abort signal', async () => {
  let result;
  const cancel = scheduleModelLoad(async (path, body, signal) => {
    assert.equal(path, '/agent/models');
    assert.deepEqual(body, { router_base_url: defaults.router_base_url, router_api_key: '' });
    assert.ok(signal instanceof AbortSignal);
    return { models: ['provider/model:exact', 'other/model', 'provider/model:exact'], limits: { 'provider/model:exact': {context_length: 1048576, max_completion_tokens: 65536} } };
  }, defaults, value => { result = value; }, 0);
  await wait(20);
  assert.deepEqual(result, { models: ['provider/model:exact', 'other/model'], limits: {'provider/model:exact': {context_length: 1048576, max_completion_tokens: 65536}}, status: 'ready', error: '' });
  cancel();
});

test('debounce cancellation prevents requests and stale responses are ignored', async () => {
  let calls = 0;
  const cancelPending = scheduleModelLoad(() => { calls++; }, defaults, () => assert.fail('cancelled load published'), 10);
  cancelPending();
  await wait(20);
  assert.equal(calls, 0);
  let resolve;
  let signal;
  const cancelActive = scheduleModelLoad((path, body, abortSignal) => {
    signal = abortSignal;
    return new Promise(done => { resolve = done; });
  }, defaults, () => assert.fail('stale load published'), 0);
  await wait(20);
  cancelActive();
  assert.equal(signal.aborted, true);
  resolve({ models: ['old/model'] });
  await wait(0);
});

test('catalog handles empty, malformed, and failed responses', async () => {
  for (const [request, expected] of [
    [async () => ({ models: [] }), { models: [], limits: {}, status: 'ready', error: '' }],
    [async () => ({ models: [3] }), { models: [], limits: {}, status: 'error', error: 'The backend returned an invalid model catalog.' }],
    [async () => { throw new Error('Gateway unavailable'); }, { models: [], limits: {}, status: 'error', error: 'Gateway unavailable' }],
  ]) {
    let result;
    const cancel = scheduleModelLoad(request, defaults, value => { result = value; }, 0);
    await wait(20);
    assert.deepEqual(result, expected);
    cancel();
  }
});
test('preferences never include either secret', () => {
  const saved = preferences({ ...defaults, api_key: 'secret-a', router_api_key: 'secret-b', extra: 'secret-c', compiler_api_key: 'secret-d' });
  assert.equal(JSON.stringify(saved).includes('secret'), false);
  assert.equal('api_key' in saved, false);
  assert.equal('router_api_key' in saved, false);
  assert.equal('compiler_api_key' in saved, false);
  assert.equal(saved.compiler_mode, 'browser');
  assert.equal(saved.compiler_base_url, '');
  assert.equal(saved.compiler_rapidapi_host, '');
  assert.deepEqual(Object.keys(saved).sort(), ['mode', 'provider', 'base_url', 'model', 'router_base_url', 'router_model', 'compiler_mode', 'compiler_base_url', 'compiler_rapidapi_host', 'context_window', 'response_reserved'].sort());
});
test('validates selected route without requiring inactive credentials', () => {
  assert.match(validateSettings(defaults), /API key/);
  assert.equal(validateSettings({ ...defaults, mode: '9router', router_model: 'openai/gpt-4o-mini' }), '');
  assert.match(validateSettings({ ...defaults, api_key: 'x', base_url: 'https://secret@example.com/v1' }), /base URL/);
  assert.match(validateSettings({ ...defaults, api_key: 'x', base_url: 'https://example.com?key=secret' }), /base URL/);
  assert.equal(validateSettings({ ...defaults, api_key: 'x' }), '');
});

test('model selection adopts published limits but does not invent missing values', () => {
  const limits = { 'ag/gemini-3.8-flash-high': { context_length: 1048576, max_completion_tokens: 65536 } };
  assert.deepEqual(applyModelLimits({ ...defaults, mode: '9router' }, 'ag/gemini-3.8-flash-high', limits), {
    ...defaults, mode: '9router', router_model: 'ag/gemini-3.8-flash-high', context_window: 1048576, response_reserved: 65536,
  });
  assert.deepEqual(applyModelLimits({ ...defaults, context_window: 900000, response_reserved: 200000 }, 'unknown', limits), {
    ...defaults, context_window: 900000, response_reserved: 200000, router_model: 'unknown',
  });
});

test('catalog ignores invalid token metadata and never publishes arbitrary fields', async () => {
  let result;
  const cancel = scheduleModelLoad(async () => ({
    models: ['good', 'invalid', 'good'], limits: {
      good: { context_length: 872000, max_completion_tokens: 128000, secret: 'hidden' },
      invalid: { context_length: 4000, max_completion_tokens: 3000 },
    },
  }), defaults, value => { result = value; }, 0);
  await wait(20);
  assert.deepEqual(result, { models: ['good', 'invalid'], limits: { good: { context_length: 872000, max_completion_tokens: 128000 } }, status: 'ready', error: '' });
  cancel();
});
