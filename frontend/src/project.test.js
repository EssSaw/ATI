import test from 'node:test';
import assert from 'node:assert/strict';
import { assistantMessage, fileExclusion, readProject, projectPayload, proposalDownload, parseCommand, commandResultText, workspaceSnapshot, handleComposerKeydown, handleComposerKeyDown } from './project.js';
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
  assert.throws(() => projectPayload({ ...chat, messages: Array(500).fill({ role: 'user', content: 'x' }) }, {}), /500-message/);
  assert.throws(() => projectPayload({ ...chat, files: [] }, {}), /project folder/);
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
  assert.deepEqual(assistantMessage({ reply: 'Legacy answer' }), { role: 'assistant', content: 'Legacy answer', context: null, trace: [], proposals: [], subagents: [], command_requests: [], workspace_revision: null });
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

test('worker results retain validated fields and sanitize malformed optional metadata', () => {
  const trace = [{ tool: 'read_file', status: 'success', detail: 'Read source', duration_ms: 1 }];
  const worker = { name: 'Reviewer', model: 'chosen/model', status: 'success', reply: '## Findings\n<script>alert(1)</script>', trace };
  const result = assistantMessage({ reply: 'Done', subagents: [null, {}, { ...worker, name: 42 }, { ...worker, model: '' }, { ...worker, status: 'running' }, { ...worker, reply: null }, { ...worker, error: {}, trace: [null, ...trace, { ...trace[0], duration_ms: -1 }, { ...trace[0], duration_ms: Infinity }], subagents: [worker], extra: 'discard' }, { ...worker, status: 'error', reply: '', error: '<img src=x onerror=alert(1)>', trace: {} }] });
  assert.deepEqual(result.subagents, [worker, { name: worker.name, model: worker.model, status: 'error', reply: '', error: '<img src=x onerror=alert(1)>', trace: [] }]);
  for (const subagents of [undefined, null, {}, 'invalid']) assert.deepEqual(assistantMessage({ reply: 'Legacy', subagents }).subagents, []);
  assert.equal(assistantMessage({ reply: 'Done', subagents: Array(5).fill(worker) }).subagents.length, 3);
  const chat = { name: 'demo', files: [{ path: 'app.js', content: 'old', selected: true }], messages: [result], draft: 'Next' };
  assert.deepEqual(projectPayload(chat, {}).messages, [{ role: 'assistant', content: 'Done' }, { role: 'user', content: 'Next' }]);
  assert.equal(result.subagents.length, 2);
});

test('local payload sends the workspace token but no duplicate contents or metadata', () => {
  const payload = projectPayload({ workspace_id: 'token', name: 'local', files: [{ path: 'a.js', content: 'PRIVATE', selected: true }], messages: [], draft: 'Help' }, {});
  assert.equal(payload.workspace_id, 'token');
  assert.equal('files' in payload, false);
  assert.equal(JSON.stringify(payload).includes('PRIVATE'), false);
  assert.doesNotThrow(() => projectPayload({ workspace_id: 'token', name: 'empty', files: [], messages: [], draft: 'Create file' }, {}));
});
test('assistant turn retains its original revision and validates command metadata', () => {
  const message = assistantMessage({ reply: 'Review', workspace_revision: 'original', command_requests: [null, {}, { argv: ['npm', 'test'], summary: 'Test', extra: true }, { argv: [], summary: 'Bad' }, { argv: [2], summary: 'Bad' }] });
  assert.equal(message.workspace_revision, 'original');
  assert.deepEqual(message.command_requests, [{ argv: ['npm', 'test'], summary: 'Test' }]);
  assert.equal(assistantMessage({ reply: 'Old', workspace_revision: {} }).workspace_revision, null);
});
test('commands require argv JSON and format actual results for a user draft', () => {
  assert.deepEqual(parseCommand('["npm", "test", "a b"]'), ['npm', 'test', 'a b']);
  for (const input of ['npm test', '{}', '[]', '[1]', '[""]']) assert.throws(() => parseCommand(input));
  const text = commandResultText({ argv: ['npm', 'test'], cwd: '/project', exit_code: 1, stdout: 'out', stderr: 'err', timed_out: true, truncated: true });
  for (const value of ['not by the AI', '/project', 'exit code: 1', 'timed out: true', 'output truncated: true', 'out', 'err']) assert.ok(text.includes(value));
});
test('snapshots validate before state changes and normalize selection', () => {
  const snapshot = { workspace_id: 'token', name: 'project', path: '/project', revision: 'r1', files: [{ path: 'a.js', content: '' }], skipped: [] };
  assert.equal(workspaceSnapshot(snapshot).files[0].selected, true);
  assert.throws(() => workspaceSnapshot({ ...snapshot, revision: null }));
  assert.throws(() => workspaceSnapshot({ ...snapshot, files: [{}] }));
});

test('composer keydown handles Enter with requestSubmit and preserves ShiftEnter and IME', () => {
  let submitted = false;
  let prevented = false;
  const form = { requestSubmit: () => { submitted = true; } };

  const enterEvent = { key: 'Enter', shiftKey: false, preventDefault: () => { prevented = true; } };
  assert.equal(handleComposerKeydown(enterEvent, { form }), true);
  assert.equal(prevented, true);
  assert.equal(submitted, true);

  prevented = false;
  submitted = false;
  const shiftEnterEvent = { key: 'Enter', shiftKey: true, preventDefault: () => { prevented = true; } };
  assert.equal(handleComposerKeydown(shiftEnterEvent, { form }), false);
  assert.equal(prevented, false);
  assert.equal(submitted, false);

  prevented = false;
  submitted = false;
  const composingEvent = { key: 'Enter', shiftKey: false, isComposing: true, preventDefault: () => { prevented = true; } };
  assert.equal(handleComposerKeydown(composingEvent, { form }), false);
  assert.equal(prevented, false);
  assert.equal(submitted, false);

  prevented = false;
  submitted = false;
  const nativeComposingEvent = { key: 'Enter', shiftKey: false, nativeEvent: { isComposing: true }, preventDefault: () => { prevented = true; } };
  assert.equal(handleComposerKeydown(nativeComposingEvent, { form }), false);
  assert.equal(prevented, false);
  assert.equal(submitted, false);

  prevented = false;
  submitted = false;
  const keyCode229Event = { key: 'Enter', shiftKey: false, keyCode: 229, preventDefault: () => { prevented = true; } };
  assert.equal(handleComposerKeydown(keyCode229Event, { form }), false);
  assert.equal(prevented, false);
  assert.equal(submitted, false);

  prevented = false;
  submitted = false;
  const keyAEvent = { key: 'a', preventDefault: () => { prevented = true; } };
  assert.equal(handleComposerKeydown(keyAEvent, { form }), false);
  assert.equal(prevented, false);
  assert.equal(submitted, false);

  assert.equal(handleComposerKeyDown, handleComposerKeydown);
});

test('composer keydown respects busy, disabled form, disabled submitter, and validation', () => {
  let submitted = false;
  let prevented = false;
  const form = { requestSubmit: () => { submitted = true; } };

  prevented = false;
  submitted = false;
  const busyEvent = { key: 'Enter', shiftKey: false, preventDefault: () => { prevented = true; } };
  assert.equal(handleComposerKeydown(busyEvent, { form, busy: true }), false);
  assert.equal(prevented, true);
  assert.equal(submitted, false);

  prevented = false;
  submitted = false;
  const disabledOptionEvent = { key: 'Enter', shiftKey: false, preventDefault: () => { prevented = true; } };
  assert.equal(handleComposerKeydown(disabledOptionEvent, { form, disabled: true }), false);
  assert.equal(prevented, true);
  assert.equal(submitted, false);

  prevented = false;
  submitted = false;
  const submitter = { disabled: true };
  const formWithDisabledBtn = {
    querySelector: () => submitter,
    requestSubmit: () => { submitted = true; }
  };
  const enterEvent = { key: 'Enter', shiftKey: false, preventDefault: () => { prevented = true; } };
  assert.equal(handleComposerKeydown(enterEvent, { form: formWithDisabledBtn }), false);
  assert.equal(prevented, true);
  assert.equal(submitted, false);

  prevented = false;
  submitted = false;
  const disabledForm = { disabled: true, requestSubmit: () => { submitted = true; } };
  assert.equal(handleComposerKeydown(enterEvent, { form: disabledForm }), false);
  assert.equal(prevented, true);
  assert.equal(submitted, false);

  prevented = false;
  submitted = false;
  const invalidForm = { checkValidity: () => false, requestSubmit: () => { submitted = true; } };
  assert.equal(handleComposerKeydown(enterEvent, { form: invalidForm }), false);
  assert.equal(prevented, true);
  assert.equal(submitted, false);

  let submittedWith = null;
  const activeSubmitter = { disabled: false };
  const formCapturingSubmitter = {
    requestSubmit: (s) => { submittedWith = s; }
  };
  assert.equal(handleComposerKeydown(enterEvent, { form: formCapturingSubmitter, submitter: activeSubmitter }), true);
  assert.equal(submittedWith, activeSubmitter);

  submitted = false;
  const eventWithForm = {
    key: 'Enter',
    currentTarget: { form },
    preventDefault: () => {}
  };
  assert.equal(handleComposerKeydown(eventWithForm), true);
  assert.equal(submitted, true);
});

test('project readiness requires selected source or connected workspace, not attachments', () => {
  const base = {name:'Demo', files:[], messages:[], draft:'Question', attachments:[]};
  assert.throws(() => projectPayload(base, {}), /project folder/);
  assert.throws(() => projectPayload({...base, workspace_id:'stale', needsReconnect:true}, {}), /Reconnect/);
  assert.doesNotThrow(() => projectPayload({...base, workspace_id:'live'}, {}));
  assert.throws(() => projectPayload({...base, files:[{path:'a.js',content:'',selected:false}]}, {}), /project folder/);
});
