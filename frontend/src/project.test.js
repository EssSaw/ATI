import test from 'node:test';
import assert from 'node:assert/strict';
import { fileExclusion, readProject, projectPayload } from './project.js';
const file = (path, content = 'hello', size = content.length) => ({ webkitRelativePath: path, size, text: async () => content });
test('rejects private, generated, binary, lock and unsafe paths before reading', async () => {
  const paths = ['p/.env', 'p/.env.local', 'p/secrets.json', 'p/api_key.txt', 'p/credentials.json', 'p/.ssh/config', 'p/node_modules/a.js', 'p/.git/config', 'p/.venv/x.py', 'p/dist/a.js', 'p/build/x.js', 'p/a.png', 'p/package-lock.json', 'p/yarn.lock', '../x.py', '/x.py', 'C:/x.py', 'p\\x.py', 'p/./x.py', 'p//x.py'];
  let reads = 0;
  const result = await readProject(paths.map(path => ({ ...file(path), text: async () => { reads++; return ''; } })));
  assert.equal(reads, 0); assert.equal(result.files.length, 0); assert.equal(result.skipped.length, paths.length);
  assert.equal(fileExclusion('p/src/main.py'), '');
});
test('budgets skip whole files and never read oversized files', async () => {
  let read = false;
  const big = { ...file('p/big.txt', '', 30001), text: async () => { read = true; return ''; } };
  const result = await readProject([big, ...Array.from({ length: 8 }, (_, i) => file(`p/${i}.txt`, 'a'.repeat(30000)))]);
  assert.equal(read, false); assert.equal(result.files.length, 6); assert.equal(result.skipped.length, 3);
  assert.ok(result.files.every(f => f.content.length === 30000));
  const count = await readProject(Array.from({ length: 81 }, (_, i) => file(`p/${i}.py`)));
  assert.equal(count.files.length, 80); assert.equal(count.skipped.length, 1);
});
test('reports binary, unreadable, duplicate files and retains snapshot project name', async () => {
  const result = await readProject([file('demo/a.py'), file('demo/a.py'), file('demo/b.txt', '\x00bad'), { ...file('demo/c.py'), text: async () => { throw Error(); } }]);
  assert.equal(result.name, 'demo'); assert.equal(result.files.length, 1); assert.equal(result.skipped.length, 3);
});
test('payload contains only selected files and isolated history ending in current user prompt', () => {
  const chat = { name: 'demo', files: [{ path: 'demo/a.py', content: 'pass', selected: true }, { path: 'demo/b.py', content: 'private', selected: false }], messages: [{ role: 'user', content: 'first' }, { role: 'assistant', content: 'answer' }], draft: ' next ' };
  const before = JSON.stringify(chat); const payload = projectPayload(chat, { mode: 'direct' });
  assert.deepEqual(payload.files, [{ path: 'demo/a.py', content: 'pass' }]);
  assert.deepEqual(payload.messages.at(-1), { role: 'user', content: 'next' });
  assert.equal(JSON.stringify(chat), before);
  assert.throws(() => projectPayload({ ...chat, messages: Array(30).fill({ role: 'user', content: 'x' }) }, {}), /30 messages/);
  assert.throws(() => projectPayload({ ...chat, files: [] }, {}), /at least one/);
  assert.throws(() => projectPayload({ ...chat, draft: ' ' }, {}), /Write/);
  assert.throws(() => projectPayload({ ...chat, draft: 'x'.repeat(10001) }, {}), /10,000/);
});
