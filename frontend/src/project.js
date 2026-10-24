export const MAX_FILES = 80;
export const MAX_FILE_CHARS = 30000;
export const MAX_TOTAL_CHARS = 200000;
export const MAX_MESSAGES = 30;
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
export function projectPayload(chat, settings) {
  const text = chat.draft.trim();
  if (!text) throw new Error('Write a message first.');
  if (text.length > 10000) throw new Error('Keep your message under 10,000 characters.');
  if (chat.messages.length + 2 > MAX_MESSAGES) throw new Error('This chat has reached 30 messages. Start a new chat.');
  const files = chat.files.filter(f => f.selected).map(({ path, content }) => ({ path, content }));
  if (!files.length) throw new Error('Select at least one project file before sending.');
  return { project_name: chat.name, files, messages: [...chat.messages.map(({ role, content }) => ({ role, content })), { role: 'user', content: text }], settings };
}

export function assistantMessage(data) {
  if (typeof data?.reply !== 'string' || !data.reply.trim()) throw new Error('The backend returned no reply. Your message is kept; try again.');
  const trace = Array.isArray(data.trace) ? data.trace.filter(t => t && typeof t.tool === 'string' && ['success', 'error'].includes(t.status) && typeof t.detail === 'string' && Number.isFinite(t.duration_ms) && t.duration_ms >= 0) : [];
  const proposals = Array.isArray(data.proposals) ? data.proposals.filter(p => p && typeof p.id === 'string' && typeof p.summary === 'string' && typeof p.diff === 'string' && Array.isArray(p.changes) && p.changes.every(c => c && typeof c.path === 'string' && ['create', 'replace', 'delete'].includes(c.operation) && typeof c.content === 'string')) : [];
  return { role: 'assistant', content: data.reply, trace, proposals };
}

export function proposalDownload(proposal) {
  const stem = String(proposal.id || 'proposal').replace(/[^a-zA-Z0-9_-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 80) || 'proposal';
  return { filename: `proposal-${stem}.patch`, blob: new Blob([proposal.diff], { type: 'text/plain;charset=utf-8' }) };
}
