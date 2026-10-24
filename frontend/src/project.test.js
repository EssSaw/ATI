import test from 'node:test';
import assert from 'node:assert/strict';
import { assistantMessage, fileExclusion, readProject, projectPayload, proposalDownload } from './project.js';
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

test('accepts framework sources and manifests while preserving exclusions', async () => {
  const paths = ['App.csproj', 'App.fsproj', 'App.sln', 'Directory.Build.props', 'App.targets', 'build.gradle', 'settings.gradle', 'App.kt', 'lib/main.dart', 'mix.exs', 'lib/app.ex', 'app.erl', 'app.hrl', 'Gemfile', 'Rakefile', 'Dockerfile', 'requirements.txt', 'pubspec.yaml', 'page.astro', 'schema.graphql'];
  const result = await readProject(paths.map(path => file(`demo/${path}`)));
  assert.equal(result.files.length, paths.length);
  for (const path of ['Gemfile.lock', 'pubspec.lock', 'mix.lock', 'Cargo.lock', 'app.dll', 'app.jar', 'app.exe']) assert.notEqual(fileExclusion(`demo/${path}`), '');
});

test('normalizes legacy replies and rejects invalid metadata without fabricating activity', () => {
  assert.deepEqual(assistantMessage({ reply: 'Legacy answer' }), { role: 'assistant', content: 'Legacy answer', trace: [], proposals: [] });
  assert.deepEqual(assistantMessage({ reply: 'Answer', trace: [null, { tool: 'read_file', status: 'running' }], proposals: [null, { id: 'bad' }] }).trace, []);
  assert.deepEqual(assistantMessage({ reply: 'Answer', proposals: [{ id: 'bad' }] }).proposals, []);
  assert.throws(() => assistantMessage({ reply: ' ' }), /no reply/);
});

const proposal = { id: '../../unsafe\\name\r\n.patch', summary: 'Update file', diff: '--- a/app.js\n+++ b/app.js\n@@ -1 +1,2 @@\n-old\n+new\n+second\n', changes: [{ path: 'app.js', operation: 'replace', content: 'new\nsecond\n' }] };
test('patch downloads sanitize names and preserve multiline text exactly', async () => {
  const download = proposalDownload(proposal);
  assert.match(download.filename, /^proposal-[a-zA-Z0-9_-]+\.patch$/);
  assert.equal(download.blob.type, 'text/plain;charset=utf-8');
  assert.equal(await download.blob.text(), proposal.diff);
  assert.equal(proposalDownload({ ...proposal, id: '.../\n' }).filename, 'proposal-proposal.patch');
  assert.ok(proposalDownload({ ...proposal, id: 'x'.repeat(200) }).filename.length <= 95);
  assert.equal(await proposalDownload({ ...proposal, diff: 'first\r\nsecond\nlast' }).blob.text(), 'first\r\nsecond\nlast');
});
test('assistant metadata stays in local history and never enters the next message payload', () => {
  const trace = [{ tool: 'propose_changes', status: 'success', detail: 'Prepared patch', duration_ms: 2 }];
  const message = assistantMessage({ reply: 'Proposed only', trace, proposals: [proposal] });
  const chat = { name: 'demo', files: [{ path: 'app.js', content: 'old', selected: true }], messages: [{ role: 'user', content: 'Change it' }, message], draft: 'Explain it' };
  const payload = projectPayload(chat, {});
  assert.deepEqual(payload.messages, [{ role: 'user', content: 'Change it' }, { role: 'assistant', content: 'Proposed only' }, { role: 'user', content: 'Explain it' }]);
  assert.deepEqual(message.trace, trace);
  assert.deepEqual(message.proposals, [proposal]);
});
