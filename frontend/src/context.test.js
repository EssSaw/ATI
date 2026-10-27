import test from 'node:test';
import assert from 'node:assert/strict';
import { compactBoundary, contextStart, estimateContext, safeContext, validateBudget } from './context.js';
import { projectPayload } from './project.js';
import { pack, unpack } from './chatStorage.js';
import { defaults, preferences } from './state.js';

const messages = Array.from({ length: 40 }, (_, i) => ({ role: i % 2 ? 'assistant' : 'user', content: `Turn ${i}` }));
const chat = { id: 'budget-chat', name: 'Demo', files: [{ path: 'main.js', content: 'a'.repeat(101000), selected: true }], messages, draft: 'Current draft', attachments: [] };
const context = { window_tokens: 32768, reserved_tokens: 4096, estimated_tokens: 12000, usage_percent: 36.6, breakdown: { system: 1000, tools: 1000, project: 8000, messages: 2000, attachments: 0, tool_results: 0 }, dropped_messages: 4, estimate: true, compacted: true };

test('explicit budget validates integers and is saved without any secret or model-name inference', () => {
  assert.equal(defaults.context_window, 32768);
  assert.equal(defaults.response_reserved, 4096);
  for (const settings of [{ context_window: 8191 }, { context_window: 2000001 }, { context_window: 9000.5 }, { response_reserved: 511 }, { response_reserved: 200001 }, { context_window: 8192, response_reserved: 8192 }]) assert.ok(validateBudget(settings));
  assert.equal(validateBudget({ context_window: 1000000, response_reserved: 200000 }), '');
  assert.equal(validateBudget({ context_window: 2000000, response_reserved: 32768 }), '');
  const payload = projectPayload(chat, { model: 'model-1M', api_key: 'secret' });
  assert.equal(payload.context_window, 32768);
  assert.equal(payload.response_reserved, 4096);
  assert.equal(preferences({ ...defaults, context_window: 1000000, api_key: 'secret' }).context_window, 1000000);
  assert.ok(!JSON.stringify(preferences({ ...defaults, api_key: 'secret' })).includes('secret'));
});

test('compaction sends a recent user-aligned context boundary without deleting persisted history', () => {
  const boundary = compactBoundary(messages);
  assert.equal(boundary, 32);
  const compacted = { ...chat, contextStart: boundary, lastContext: { ...context, api_key: 'hidden', breakdown: { ...context.breakdown, secret: 'hidden' } } };
  const payload = projectPayload(compacted, defaults);
  assert.deepEqual(payload.messages.slice(0, -1), messages.slice(32));
  assert.equal(compacted.messages.length, 40);
  const record = pack([compacted], chat.id);
  const restored = unpack(record).chats[0];
  assert.equal(restored.messages.length, 40);
  assert.equal(restored.contextStart, 32);
  assert.deepEqual(restored.lastContext, context);
  assert.ok(!JSON.stringify(record).includes('hidden'));
  assert.equal(contextStart({ ...chat, contextStart: 9999 }), 40);
  assert.equal(contextStart({ ...chat, contextStart: -9 }), 0);
  assert.equal(contextStart({ ...chat, contextStart: '32' }), 0);
  assert.equal(compactBoundary(messages.slice(0, 4)), 0);
});

test('preview includes draft or safe blank fallback, retains attachments, and counts large files only once', () => {
  const payload = projectPayload(chat, defaults, { preview: true });
  assert.equal(payload.messages.at(-1).content, 'Current draft');
  const expected = Math.ceil(new TextEncoder().encode('main.js\n' + 'a'.repeat(101000)).length / 3);
  assert.equal(estimateContext(payload).breakdown.project, expected);
  assert.equal(projectPayload({ ...chat, draft: '' }, defaults, { preview: true }).messages.at(-1).content, 'Context preview');
  const image = { id: 'image', name: 'a.png', kind: 'image', mime_type: 'image/png', size: 10, data_url: 'data:image/png;base64,YQ==' };
  const withAttachment = projectPayload({ ...chat, draft: '', attachments: [image] }, { ...defaults, api_key: 'secret' }, { preview: true });
  assert.equal(withAttachment.messages.at(-1).content, '');
  assert.deepEqual(withAttachment.messages.at(-1).attachments, [image]);
  assert.equal(estimateContext(withAttachment).breakdown.attachments, 1024);
  assert.ok(!JSON.stringify(withAttachment.settings).includes('secret'));
  const unicode = estimateContext({ messages: [{ content: '界界界', role: 'user' }], context_window: 8192, response_reserved: 512 });
  assert.equal(unicode.breakdown.messages, 7);
  const local = projectPayload({ ...chat, workspace_id: 'workspace' }, defaults, { preview: true });
  assert.equal(local.files, undefined);
  assert.equal(estimateContext(local).breakdown.project, 0);
});

test('metadata is explicitly whitelisted and attachment safety applies only to active context', () => {
  assert.deepEqual(safeContext({ ...context, api_key: 'hidden' }), context);
  assert.equal(safeContext({ ...context, estimated_tokens: NaN }), null);
  assert.equal(safeContext({ ...context, breakdown: { system: 1 } }), null);
  assert.equal(safeContext({ ...context, estimate: false }), null);
  const attachment = { id: 'text', name: 'a.txt', kind: 'text', mime_type: 'text/plain', size: 1, text: 'notes' };
  const history = messages.map(message => message.role === 'user' ? { ...message, attachments: [attachment] } : message);
  assert.throws(() => projectPayload({ ...chat, messages: history }, defaults), /12 attachments/);
  assert.equal(projectPayload({ ...chat, messages: history, contextStart: 32 }, defaults).messages.length, 9);
  assert.equal(pack([{ ...chat, messages: Array(500).fill(messages[0]) }]).chats[0].messages.length, 500);
  assert.throws(() => projectPayload({ ...chat, messages: Array(500).fill(messages[0]) }, defaults), /500-message storage safety/);
});
