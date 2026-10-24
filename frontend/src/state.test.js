import test from 'node:test';
import assert from 'node:assert/strict';
import { defaults, preferences, validateSettings } from './state.js';
import { scheduleModelLoad } from './models.js';
import { setTimeout as wait } from 'node:timers/promises';

test('catalog loads exact IDs with only gateway settings and an abort signal', async () => {
  let result;
  const cancel = scheduleModelLoad(async (path, body, signal) => {
    assert.equal(path, '/agent/models');
    assert.deepEqual(body, { router_base_url: defaults.router_base_url, router_api_key: '' });
    assert.ok(signal instanceof AbortSignal);
    return { models: ['provider/model:exact', 'other/model', 'provider/model:exact'] };
  }, defaults, value => { result = value; }, 0);
  await wait(20);
  assert.deepEqual(result, { models: ['provider/model:exact', 'other/model'], status: 'ready', error: '' });
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
    [async () => ({ models: [] }), { models: [], status: 'ready', error: '' }],
    [async () => ({ models: [3] }), { models: [], status: 'error', error: 'The backend returned an invalid model catalog.' }],
    [async () => { throw new Error('Gateway unavailable'); }, { models: [], status: 'error', error: 'Gateway unavailable' }],
  ]) {
    let result;
    const cancel = scheduleModelLoad(request, defaults, value => { result = value; }, 0);
    await wait(20);
    assert.deepEqual(result, expected);
    cancel();
  }
});
test('preferences never include either secret', () => {
  const saved = preferences({ ...defaults, api_key: 'secret-a', router_api_key: 'secret-b', extra: 'secret-c' });
  assert.equal(JSON.stringify(saved).includes('secret'), false);
  assert.equal('api_key' in saved, false);
  assert.equal('router_api_key' in saved, false);
});
test('validates selected route without requiring inactive credentials', () => {
  assert.match(validateSettings(defaults), /API key/);
  assert.equal(validateSettings({ ...defaults, mode: '9router', router_model: 'openai/gpt-4o-mini' }), '');
  assert.match(validateSettings({ ...defaults, api_key: 'x', base_url: 'https://secret@example.com/v1' }), /base URL/);
  assert.match(validateSettings({ ...defaults, api_key: 'x', base_url: 'https://example.com?key=secret' }), /base URL/);
  assert.equal(validateSettings({ ...defaults, api_key: 'x' }), '');
});
