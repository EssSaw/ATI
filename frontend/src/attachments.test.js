import test from 'node:test';
import assert from 'node:assert/strict';
import { attachmentType, preparedAttachment, preparedAttachments, safeImageUrl, readAttachment, IMAGE_LIMIT, DOCUMENT_LIMIT } from './attachments.js';
import { projectPayload } from './project.js';
import { pack, unpack, MAX_STORAGE_CHARS } from './chatStorage.js';
const text = { id: 'doc-1', name: 'notes.txt', mime_type: 'text/plain', kind: 'text', text: 'Review this document', size: 20 };
const image = { id: 'img-1', name: 'screen.png', mime_type: 'image/png', kind: 'image', data_url: 'data:image/png;base64,aGVsbG8=', size: 5 };
const chat = { id: 'one', name: 'Attachments', files: [{ path: 'main.js', content: 'code', selected: true }], skipped: [], messages: [], draft: '', attachments: [text, image] };
test('attachment selection filters unsupported formats and enforces image/document limits', () => {
  for (const name of ['pic.PNG', 'pic.jpeg', 'pic.webp', 'report.pdf', 'report.docx', 'server.log', 'app.tsx']) assert.ok(attachmentType({ name, size: 50 }));
  for (const name of ['file.exe', 'anim.gif', 'image.svg', 'report.doc']) assert.throws(() => attachmentType({ name, size: 50 }), /Unsupported/);
  assert.throws(() => attachmentType({ name: 'pic.png', size: IMAGE_LIMIT + 1 }), /4 MB/);
  assert.throws(() => attachmentType({ name: 'report.pdf', size: DOCUMENT_LIMIT + 1 }), /5 MB/);
  assert.throws(() => attachmentType({ name: 'empty.txt', size: 0 }), /nonempty/);
});
test('prepared metadata whitelists contents and rejects unsafe or mismatched images', () => {
  assert.deepEqual(preparedAttachment({ ...text, api_key: 'secret', workspace_id: 'cap' }), text);
  assert.deepEqual(preparedAttachment(image), image);
  for (const url of ['https://example.com/a.png', 'javascript:alert(1)', 'data:image/svg+xml;base64,AAAA', 'data:text/html;base64,AAAA']) { assert.equal(safeImageUrl(url), false); assert.throws(() => preparedAttachment({ ...image, data_url: url })); }
  assert.throws(() => preparedAttachment({ ...image, mime_type: 'image/jpeg' }));
  assert.throws(() => preparedAttachment({ ...text, kind: 'binary' }));
  assert.throws(() => preparedAttachment({ ...text, text: '' }));
  assert.throws(() => preparedAttachments(Array(7).fill(text)), /6 files/);
});
test('attachments-only payload sends explicit user fields, no credentials or assistant attachments', () => {
  const payload = projectPayload({ ...chat, messages: [{ role: 'assistant', content: 'Earlier', attachments: [image], workspace_id: 'cap' }] }, {});
  assert.deepEqual(payload.messages.at(-1), { role: 'user', content: '', attachments: [text, image] });
  assert.deepEqual(payload.messages[0], { role: 'assistant', content: 'Earlier' });
  assert.deepEqual(payload.files, [{ path: 'main.js', content: 'code' }]);
  assert.throws(() => projectPayload({ ...chat, files: [] }, {}), /project folder/);
  assert.throws(() => projectPayload({ ...chat, attachments: Array(7).fill(text) }, {}), /6 files/);
  assert.throws(() => projectPayload({ ...chat, messages: [{ role: 'user', content: '', attachments: Array(6).fill(text) }, { role: 'user', content: '', attachments: Array(6).fill(text) }] }, {}), /12 attachments.*new chat/);
});
test('prepared attachments round-trip both draft and history without restoring capabilities', () => {
  const packed = pack([{ ...chat, messages: [{ role: 'user', content: '', attachments: [image, text] }], api_key: 'secret', workspace_id: 'cap', path: '/project' }], chat.id);
  const restored = unpack(packed).chats[0];
  assert.deepEqual(restored.attachments, [text, image]);
  assert.deepEqual(restored.messages[0].attachments, [image, text]);
  assert.equal(restored.needsReconnect, true);
  assert.equal(restored.workspace_id, undefined);
  assert.equal(restored.api_key, undefined);
  assert.deepEqual(unpack(pack([{ ...chat, attachments: undefined }])).chats[0].attachments, []);
  assert.throws(() => pack([{ ...chat, attachments: [{ ...image, data_url: 'data:text/html;base64,AAAA' }] }]));
  assert.throws(() => pack([{ ...chat, draft: '', messages: [], files: [{ path: 'huge.txt', content: 'x'.repeat(MAX_STORAGE_CHARS), selected: true }] }]), /budget/);
});
test('FileReader base64 conversion never spreads file bytes', async () => {
  const original = globalThis.FileReader;
  globalThis.FileReader = class { readAsDataURL() { this.result = 'data:application/pdf;base64,YWJj'; this.onload(); } };
  try { assert.equal(await readAttachment({}), 'YWJj'); } finally { globalThis.FileReader = original; }
});
