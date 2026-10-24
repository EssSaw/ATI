import test from 'node:test';
import assert from 'node:assert/strict';
import { defaults, preferences, validateSettings } from './state.js';
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
