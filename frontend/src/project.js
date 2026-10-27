import { contextStart, safeContext, validateBudget } from './context.js';
import { preparedAttachments, MAX_HISTORY_ATTACHMENTS } from './attachments.js';
export const MAX_FILES = 80;
export const MAX_FILE_CHARS = 30000;
export const MAX_TOTAL_CHARS = 200000;
export const MAX_MESSAGES = 500;
const extensions = new Set('py pyi js jsx ts tsx mjs cjs json html css scss sass less md mdx txt rst toml yaml yml xml csv sql sh bash zsh c h cpp hpp cs fs fsx csproj fsproj sln props targets java go rs rb erb rake gemspec php swift kt kts gradle dart ex exs erl hrl vue svelte astro graphql gql proto ini cfg conf gitignore dockerfile'.split(' '));
const excludedDirs = new Set(['.git', 'node_modules', '.venv', 'venv', 'dist', 'build', '__pycache__', '.next', '.ssh', '.aws', '.azure', '.kube', '.pytest_cache', '.mypy_cache', '.tox', 'vendor', 'target', 'out', '.nuxt', '.turbo', '.cache', '.idea', '.vscode']);
export function fileExclusion(path, size = 0) {
  if (!path || path.startsWith('/') || /[\\:\x00-\x1f\x7f]/.test(path) || path.split('/').some(p => !p || p === '.' || p === '..')) return 'unsafe path';
  const parts = path.toLowerCase().split('/');
  if (parts.some(p => excludedDirs.has(p))) return 'generated or private directory';
  const name = parts.at(-1);
  if (parts.some(p => p.startsWith('.env') || p.endsWith('.env') || /env\.(example|sample|template)/.test(p) || /credential|creds|secret|password|token|api[_-]?key|private[_-]?key|privatekey|service[-_]account|(?:^|[._-])(?:keys?|id_rsa|id_ed25519)(?:[._-]|$)/.test(p)) || /\.(pem|p12|pfx|key|keystore|pkcs12|jks|cer|crt|der)$/.test(name) || ['.npmrc', '.netrc', '.pypirc'].includes(name)) return 'potential secret';
  if (name.endsWith('.lock') || ['package-lock.json', 'npm-shrinkwrap.json', 'yarn.lock', 'pnpm-lock.yaml', 'bun.lockb', 'cargo.lock', 'poetry.lock', 'uv.lock'].includes(name)) return 'lockfile';
  if (!extensions.has(name.split('.').at(-1)) && !['dockerfile', 'makefile', 'gemfile', 'rakefile', 'procfile', '.gitignore'].includes(name)) return 'unsupported or binary file';
  if (size > MAX_FILE_CHARS) return 'file exceeds 30,000-byte read limit';
  return '';
}
export async function readProject(input) {
  const files = [], skipped = [], seen = new Set();
  let total = 0;
  for (const file of Array.from(input)) {
    const path = file.webkitRelativePath || file.name;
    let reason = fileExclusion(path, file.size);
    if (!reason && seen.has(path)) reason = 'duplicate path';
    if (!reason && files.length >= MAX_FILES) reason = '80-file limit';
    if (!reason && total + file.size > MAX_TOTAL_CHARS) reason = '200,000-character budget (conservative byte check)';
    if (!reason) {
      try {
        const content = await file.text();
        if (/[\x00-\x08\x0e-\x1f\ufffd]/.test(content)) reason = 'binary or unsupported encoding';
        else if (content.length > MAX_FILE_CHARS) reason = '30,000-character limit';
        else if (total + content.length > MAX_TOTAL_CHARS) reason = '200,000-character limit';
        else { files.push({ path, content, selected: true }); total += content.length; seen.add(path); }
      } catch { reason = 'could not read file'; }
    }
    if (reason) skipped.push({ path, reason });
  }
  const root = Array.from(input)[0]?.webkitRelativePath?.split('/')[0] || 'Project';
  return { name: root, files, skipped };
}
export function projectReady(chat) {
  return !chat.needsReconnect && Boolean(chat.workspace_id || chat.files?.some(file => file.selected));
}
export function projectPayload(chat, settings, { preview = false } = {}) {
  const invalid = validateBudget(settings);
  if (invalid) throw new Error(invalid);
  const text = chat.draft.trim() || (preview && !chat.attachments?.length ? 'Context preview' : '');
  const attachments = preparedAttachments(chat.attachments);
  if (!text && !attachments.length) throw new Error('Write a message or attach a file first.');
  if (text.length > 10000) throw new Error('Keep your message under 10,000 characters.');
  if (!preview && chat.messages.length + 2 > MAX_MESSAGES) throw new Error('This chat has reached the 500-message storage safety limit. Start a new chat; compacting context does not remove saved messages.');
  const files = chat.files.filter(f => f.selected).map(({ path, content }) => ({ path, content }));
  if (!preview && !projectReady(chat)) throw new Error(chat.needsReconnect ? 'Reconnect this local project before sending.' : 'Select a project folder before sending. Attachments are not a project directory.');
  const messages = [...chat.messages.slice(contextStart(chat)).map(({ role, content, attachments: attached }) => ({ role, content, ...(role === 'user' && attached?.length ? { attachments: preparedAttachments(attached) } : {}) })), { role: 'user', content: text, ...(attachments.length ? { attachments } : {}) }];
  if (messages.reduce((count, m) => count + (m.attachments?.length || 0), 0) > MAX_HISTORY_ATTACHMENTS) throw new Error('This conversation would exceed 12 attachments. Compact conversation to exclude older attachments, remove draft attachments, or start a new chat. This is a request safety limit, not the token context window.');
  return { context_window: settings.context_window ?? 32768, response_reserved: settings.response_reserved ?? 4096, project_name: chat.name, ...(chat.workspace_id ? { workspace_id: chat.workspace_id } : { files }), messages, settings: preview ? { ...settings, api_key: '', router_api_key: '', compiler_api_key: '' } : settings };
}

function validTrace(trace) {
  return Array.isArray(trace) ? trace.filter(t => t && typeof t.tool === 'string' && ['success', 'error'].includes(t.status) && typeof t.detail === 'string' && Number.isFinite(t.duration_ms) && t.duration_ms >= 0).map(({ tool, status, detail, duration_ms }) => ({ tool, status, detail, duration_ms })) : [];
}
export function assistantMessage(data) {
  if (typeof data?.reply !== 'string' || !data.reply.trim()) throw new Error('The backend returned no reply. Your message is kept; try again.');
  const trace = validTrace(data.trace);
  const subagents = Array.isArray(data.subagents) ? data.subagents.filter(w => w && typeof w.name === 'string' && w.name.trim() && typeof w.model === 'string' && w.model.trim() && ['success', 'error'].includes(w.status) && typeof w.reply === 'string').slice(0, 3).map(w => ({ name: w.name, model: w.model, status: w.status, reply: w.reply, ...(typeof w.error === 'string' ? { error: w.error } : {}), trace: validTrace(w.trace) })) : [];
  const proposals = Array.isArray(data.proposals) ? data.proposals.filter(p => p && typeof p.id === 'string' && typeof p.summary === 'string' && typeof p.diff === 'string' && Array.isArray(p.changes) && p.changes.every(c => c && typeof c.path === 'string' && ['create', 'replace', 'delete'].includes(c.operation) && (c.operation === 'delete' || typeof c.content === 'string'))) : [];
  const command_requests = Array.isArray(data.command_requests) ? data.command_requests.filter(c => c && validArgv(c.argv) && typeof c.summary === 'string').map(({ argv, summary }) => ({ argv, summary })) : [];
  return { role: 'assistant', content: data.reply, context: safeContext(data.context), trace, proposals, subagents, command_requests, workspace_revision: typeof data.workspace_revision === 'string' && data.workspace_revision ? data.workspace_revision : null };
}

export function proposalDownload(proposal) {
  const stem = String(proposal.id || 'proposal').replace(/[^a-zA-Z0-9_-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 80) || 'proposal';
  return { filename: `proposal-${stem}.patch`, blob: new Blob([proposal.diff], { type: 'text/plain;charset=utf-8' }) };
}

export function validArgv(argv) {
  return Array.isArray(argv) && argv.length > 0 && argv.every(a => typeof a === 'string' && !a.includes('\0')) && argv[0].trim().length > 0;
}
export function parseCommand(text) {
  let argv;
  try { argv = JSON.parse(text); } catch { throw new Error('Enter a JSON array of command arguments, e.g. ["npm", "test"].'); }
  if (!validArgv(argv)) throw new Error('Command must be a nonempty JSON array of strings with an executable first.');
  return argv;
}
export function commandResultText(result) {
  return ['Local command result (run with my explicit approval, not by the AI):', 'argv: ' + JSON.stringify(result.argv), 'cwd: ' + result.cwd, 'exit code: ' + result.exit_code, 'timed out: ' + Boolean(result.timed_out), 'output truncated: ' + Boolean(result.truncated), 'stdout:', result.stdout || '', 'stderr:', result.stderr || ''].join('\n');
}
export function workspaceSnapshot(data) {
  if (!data || typeof data.workspace_id !== 'string' || !data.workspace_id || typeof data.revision !== 'string' || !data.revision || typeof data.name !== 'string' || typeof data.path !== 'string' || !Array.isArray(data.files) || !data.files.every(f => f && typeof f.path === 'string' && typeof f.content === 'string') || !Array.isArray(data.skipped) || !data.skipped.every(f => f && typeof f.path === 'string' && typeof f.reason === 'string')) throw new Error('Invalid workspace snapshot. Existing state was retained; refresh to reconcile disk state.');
  return { workspace_id: data.workspace_id, revision: data.revision, name: data.name, path: data.path, files: data.files.map(f => ({ path: f.path, content: f.content, selected: true })), skipped: data.skipped };
}

export function handleComposerKeydown(event, options = {}) {
  if (!event || event.key !== 'Enter' || event.shiftKey) return false;
  if (event.isComposing || event.nativeEvent?.isComposing || event.keyCode === 229 || event.which === 229) return false;
  if (typeof event.preventDefault === 'function') event.preventDefault();
  const opts = (options && typeof options === 'object' && ('requestSubmit' in options || options.tagName === 'FORM')) ? { form: options } : (options || {});
  if (opts.busy || opts.disabled) return false;
  const form = opts.form || event.currentTarget?.form || event.target?.form || (typeof event.target?.closest === 'function' ? event.target.closest('form') : null);
  if (!form || form.disabled) return false;
  const submitter = opts.submitter || opts.submitButton || (typeof form.querySelector === 'function' ? form.querySelector('button[type="submit"], button:not([type="button"]):not([type="reset"])') : null);
  if (submitter?.disabled) return false;
  if (typeof form.checkValidity === 'function' && !form.checkValidity()) return false;
  if (typeof form.requestSubmit === 'function') {
    try {
      if (submitter) form.requestSubmit(submitter);
      else form.requestSubmit();
    } catch {
      form.requestSubmit();
    }
    return true;
  }
  if (typeof form.dispatchEvent === 'function' && typeof Event !== 'undefined') {
    return form.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
  }
  return false;
}
export const handleComposerKeyDown = handleComposerKeydown;
