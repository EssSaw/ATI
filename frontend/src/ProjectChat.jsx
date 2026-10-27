import React, { useEffect, useRef, useState } from 'react';
import { MAX_MESSAGES, assistantMessage, projectPayload, proposalDownload, readProject, workspaceSnapshot, validArgv, commandResultText, handleComposerKeydown, projectReady } from './project.js';
import WorkspaceControls, { WorkspaceApproval } from './WorkspaceControls.jsx';
import { validateSettings } from './state.js';
import { ATTACHMENT_ACCEPT, MAX_ATTACHMENTS, attachmentType, preparedAttachment, readAttachment, safeImageUrl } from './attachments.js';
import { CONTEXT_PARTS, compactBoundary, contextStart, estimateContext, safeContext } from './context.js';
import ModelPicker from './ModelPicker.jsx';
import Markdown from './Markdown.jsx';
import FilePreview from './FilePreview.jsx';
import { loadChats, saveChats } from './chatStorage.js';

function Proposal({ proposal, applied, onApply, disabled }) {
  const [feedback, setFeedback] = useState('');
  async function copy() {
    try { await navigator.clipboard.writeText(proposal.diff); setFeedback('Patch copied. No files changed.'); }
    catch { setFeedback('Clipboard unavailable. Download the patch or select the diff to copy.'); }
  }
  function download() {
    try {
      const { filename, blob } = proposalDownload(proposal);
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url; link.download = filename;
      document.body.append(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setFeedback('Patch downloaded. Review before applying it yourself.');
    } catch { setFeedback('Download unavailable. Select the diff to copy.'); }
  }
  return <section className="change-proposal" aria-label="Proposed changes">
    <div className="proposal-heading"><strong>Change proposal</strong><span>{applied ? 'Applied with your approval' : 'Not applied'}</span></div>
    <p>{proposal.summary}</p>
    <ul className="proposal-paths">{proposal.changes.map((change, i) => <li key={i}><span>{change.operation}</span><code>{change.path}</code></li>)}</ul>
    <details className="proposal-diff"><summary>Review unified diff</summary><pre><code>{proposal.diff}</code></pre></details>
    <div className="proposal-actions">{onApply && <button type="button" className="primary" disabled={disabled || applied} onClick={onApply}>Review and apply</button>}<button type="button" className="secondary" onClick={copy}>Copy patch</button><button type="button" className="secondary" onClick={download}>Download .patch</button></div>
    <small>{applied ? 'You approved these disk changes. The AI did not apply them independently.' : 'Proposal only. No disk changes until explicit confirmation.'}</small>
    {feedback && <p className="proposal-feedback" role="status">{feedback}</p>}
  </section>;
}
function ToolActivity({ trace }) {
  return trace?.length > 0 && <details className="tool-activity"><summary>Tool activity <span>{trace.length} completed calls</span></summary><ol>{trace.map((step, i) => <li key={i}><div><code>{step.tool}</code><span className={`tool-status ${step.status}`}>{step.status}</span><small>{step.duration_ms} ms</small></div><p>{step.detail}</p></li>)}</ol></details>;
}
function AssistantTools({ message, local, disabled, reviewChanges, prepare, applied, messageIndex }) {
  return <><ToolActivity trace={message.trace} />
    {message.subagents?.length > 0 && <section className="worker-results" aria-label="Worker results">
      <h3>Worker results <span>{message.subagents.length} returned</span></h3>
      <p className="worker-note">Completed results for this reply only. Shown after the request finishes, not live progress.</p>
      {message.subagents.map((worker, i) => <details className="worker-card" key={i}>
        <summary><span className="worker-name">{worker.name}</span><span className={`tool-status ${worker.status}`}>{worker.status}</span><code className="worker-model">{worker.model}</code></summary>
        <div className="worker-body">{worker.reply ? <Markdown text={worker.reply} /> : <p>No reply returned.</p>}{worker.error && <p className="worker-error">Error: {worker.error}</p>}<ToolActivity trace={worker.trace} /></div>
      </details>)}
    </section>}
    {message.proposals?.map((proposal, i) => <Proposal key={`${proposal.id}-${i}`} proposal={proposal} applied={applied?.includes(`${messageIndex}:${i}`)} disabled={disabled || !message.workspace_revision} onApply={local ? () => reviewChanges(proposal, message.workspace_revision, `${messageIndex}:${i}`) : undefined} />)}{local && message.command_requests?.map((command, i) => <section className="change-proposal" key={`command-${i}`}><strong>Command request — not executed</strong><p>{command.summary}</p><pre>{JSON.stringify(command.argv)}</pre><button className="secondary" disabled={disabled} onClick={() => prepare(command.argv)}>Review command</button></section>)}</>;
}
function Attachments({ items, remove, disabled, onPreview }) {
  return items?.length > 0 && <ul className="attachment-list" aria-label="Attachments">{items.map((item, index) => <li key={item.id}><button type="button" className="attachment-preview" aria-label={'Preview ' + item.name} onClick={event => { event.currentTarget.focus(); onPreview(item); }}>{item.kind === 'image' && safeImageUrl(item.data_url) ? <img src={item.data_url} alt="" /> : <span className="attachment-icon" aria-hidden="true">DOC</span>}<span><strong>{item.name}</strong><small>{item.kind === 'image' ? 'Image' : 'Document'} · {Math.ceil(item.size / 1024)} KB</small></span></button>{remove && <button type="button" disabled={disabled} onClick={() => remove(index)} aria-label={'Remove ' + item.name}>×</button>}</li>)}</ul>;
}
function ContextInfo({ preview, lastContext, loading, error }) {
  const context = preview?.context || lastContext;
  return <><h3>Context window</h3>{loading && <p role="status">Estimating next request…</p>}{error && <p role="status">{error}</p>}{context ? <>
    <p className="context-source">{preview ? preview.local ? 'Local lower-bound estimate · server overhead unavailable' : 'Next request estimate · includes draft (or preview placeholder)' : 'Last request · excludes current draft'}</p>
    <strong>~{Math.ceil(context.estimated_tokens).toLocaleString()} / {context.window_tokens.toLocaleString()} tokens · ~{Math.round(context.usage_percent)}%</strong>
    <div role="progressbar" aria-label="Estimated context usage" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.min(100, Math.round(context.usage_percent))}><span style={{ width: Math.min(100, context.usage_percent) + '%' }} /></div>
    <dl><div><dt>Response reserve</dt><dd>{context.reserved_tokens.toLocaleString()}</dd></div>{CONTEXT_PARTS.map(key => <div key={key}><dt>{key.replaceAll('_', ' ')}</dt><dd>~{Math.ceil(context.breakdown[key]).toLocaleString()}</dd></div>)}</dl>
    <p>Approximate tokens, not exact tokenizer counts. {context.dropped_messages > 0 ? context.dropped_messages + ' older messages omitted by the server budget; saved history is unchanged.' : 'The server may omit older turns to fit this budget.'}</p>
  </> : <p>Open a project or attach a file to estimate its next request. No provider key is needed for preview.</p>}{lastContext && preview && <p className="context-source">Last request: ~{Math.ceil(lastContext.estimated_tokens).toLocaleString()} tokens (excludes draft). {lastContext.dropped_messages > 0 && lastContext.dropped_messages + ' older messages omitted; still saved.'}</p>}</>;
}
const newChat = () => ({ contextStart: 0, lastContext: null, id: crypto.randomUUID(), name: 'New conversation', files: [], skipped: [], messages: [], draft: '', attachments: [], attachmentErrors: [], preparing: false, error: '', pending: false, approval: null, applied: [], commandResults: [] });
export default function ProjectChat({ settings, request, openSettings, health, route, onCompiler, hidden, modelCatalog = { models: [], status: 'idle', error: '' }, onModelOpen, onModelReload, onModelSelect, onPendingChange }) {
  const [chats, setChats] = useState(() => [newChat()]);
  const [active, setActive] = useState(null);
  const [storageReady, setStorageReady] = useState(false);
  const [storageError, setStorageError] = useState('');
  const [saving, setSaving] = useState(false);
  const [menu, setMenu] = useState(null);
  const [menuPosition, setMenuPosition] = useState({});
  const [attachMenu, setAttachMenu] = useState(false);
  const [contextOpen, setContextOpen] = useState(false);
  const [filePreview, setFilePreview] = useState(null);
  const previewGeneration = useRef(0);
  const workspaceTokens = useRef(new Map());
  const [preview, setPreview] = useState(null);
  const [previewError, setPreviewError] = useState('');
  const [previewLoading, setPreviewLoading] = useState(false);
  const contextButton = useRef(null);
  const contextPanel = useRef(null);
  const settingsDialog = useRef(null);
  const attachmentPicker = useRef(null);
  const [dragging, setDragging] = useState(false);
  const dragDepth = useRef(0);
  const [reading, setReading] = useState(false);
  const [review, setReview] = useState(true);
  const saveQueue = useRef(Promise.resolve());
  const storageFailed = useRef(false);
  const locks = useRef(new Set());
  const picker = useRef(null);
  const end = useRef(null);
  const chat = chats.find(c => c.id === active) || chats[0];
  useEffect(() => { onPendingChange?.(chats.some(item => item.pending)); }, [chats, onPendingChange]);
  useEffect(() => { dragDepth.current = 0; setDragging(false); }, [chat.id, hidden]);
  useEffect(() => { previewGeneration.current++; setFilePreview(null); }, [chat.id, hidden]);
  const patch = (id, changes) => setChats(all => all.map(c => c.id === id ? { ...c, ...changes } : c));
  useEffect(() => {
    let mounted = true;
    loadChats().then(saved => {
      if (mounted && saved?.chats.length) { setChats(saved.chats); setActive(saved.active); }
    }).catch(() => {
      storageFailed.current = true;
      if (mounted) setStorageError('Saved chats could not be loaded. Existing saved data has not been overwritten.');
    }).finally(() => { if (mounted) setStorageReady(true); });
    return () => { mounted = false; };
  }, []);
  useEffect(() => {
    if (!storageReady || storageFailed.current) return;
    setSaving(true);
    let current = true;
    const timer = setTimeout(() => {
      saveQueue.current = saveQueue.current.catch(() => {}).then(() => saveChats(chats, active));
      saveQueue.current.then(() => { if (current) { setStorageError(''); setSaving(false); } })
        .catch(() => { if (current) { setStorageError('Autosave failed. Your current chats are still open; browser storage may be full or unavailable.'); setSaving(false); } });
    }, 350);
    return () => { current = false; clearTimeout(timer); };
  }, [chats, active, storageReady]);
  useEffect(() => {
    const warnUnsaved = event => {
      if (saving || storageError) { event.preventDefault(); event.returnValue = ''; }
    };
    globalThis.addEventListener('beforeunload', warnUnsaved);
    return () => globalThis.removeEventListener('beforeunload', warnUnsaved);
  }, [saving, storageError]);
  useEffect(() => { if (!hidden) end.current?.scrollIntoView({ block: 'nearest' }); }, [chat.id, chat.messages.length, chat.pending, hidden]);
  useEffect(() => {
    if (!menu && !attachMenu) return;
    const selector = menu ? '.conversation-menu' : '.attach-menu';
    document.querySelector(selector + ' button')?.focus();
    const close = event => { if (!event.target.closest('.conversation-menu, .conversation-more, .attach-menu, .attach-toggle')) { setMenu(null); setAttachMenu(false); } };
    document.addEventListener('pointerdown', close);
    return () => document.removeEventListener('pointerdown', close);
  }, [menu, attachMenu]);
  useEffect(() => {
    if (!contextOpen) return;
    contextPanel.current?.focus();
    const close = event => {
      if (!event.target.closest('.context-control')) setContextOpen(false);
    };
    const escape = event => {
      if (event.key === 'Escape') { setContextOpen(false); contextButton.current?.focus(); }
    };
    document.addEventListener('pointerdown', close);
    document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('pointerdown', close); document.removeEventListener('keydown', escape); };
  }, [contextOpen]);
  useEffect(() => {
    if (!contextOpen || hidden) return;
    const controller = new AbortController();
    setPreview(null); setPreviewError(''); setPreviewLoading(true);
    const timer = setTimeout(async () => {
      try {
        const payload = projectPayload(chat, settings, { preview: true });
        setPreview({ context: estimateContext(payload), local: true });
        const data = await request('/agent/project-chat/context', payload, controller.signal);
        if (!controller.signal.aborted) {
          const context = safeContext(data.context);
          if (!context) throw new Error('Context estimate unavailable from the backend.');
          setPreview({ context, local: false });
        }
      } catch (error) {
        if (!controller.signal.aborted) setPreviewError(error.message || 'Context preview unavailable.');
      } finally { if (!controller.signal.aborted) setPreviewLoading(false); }
    }, 300);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [contextOpen, hidden, chat.id, chat.files, chat.workspace_id, chat.messages, chat.draft, chat.attachments, chat.contextStart, settings, request]);
  function compactConversation() {
    const boundary = Math.max(contextStart(chat), compactBoundary(chat.messages));
    patch(chat.id, { contextStart: boundary, lastContext: null });
    setPreview(null);
  }
  function menuKeydown(event, close) {
    const buttons = [...event.currentTarget.querySelectorAll('button:not(:disabled)')];
    const index = buttons.indexOf(document.activeElement);
    if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
      event.preventDefault();
      buttons[event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : (index + (event.key === 'ArrowUp' ? -1 : 1) + buttons.length) % buttons.length]?.focus();
    }
    if (event.key === 'Escape') { event.preventDefault(); close(); }
  }
  function deleteChat(id) {
    if (chats.some(c => c.pending || c.preparing) || reading) return;
    if (!globalThis.confirm('Delete this saved conversation and its stored snapshot? This cannot be undone.')) return;
    const remaining = chats.filter(c => c.id !== id);
    const next = remaining.length ? remaining : [newChat()];
    setChats(next); if (id === chat.id) setActive(next[0].id); setMenu(null);
  }
  function addChat() { if (chats.length >= 100) { setStorageError('100-chat limit reached. Delete a conversation to make room.'); return; } const next = newChat(); setChats(all => [...all, next]); setActive(next.id); setReview(true); }
  function projectSettings(id) {
    setActive(id); setMenu(null); setAttachMenu(false);
    settingsDialog.current.showModal();
  }
  async function attachFiles(event) {
    const files = Array.from(event);
    const id = chat.id;
    if (!files.length) return;
    if (locks.current.has(id) || reading || chat.pending || chat.preparing || !storageReady) { patch(id, { attachmentErrors: ['Wait until the current send or file preparation finishes before attaching files.'] }); return; }
    locks.current.add(id); setAttachMenu(false);
    patch(id, { preparing: true, attachmentErrors: [] });
    const attached = [...(chat.attachments || [])], errors = [];
    try {
      for (const file of files) {
        try {
          if (attached.length >= MAX_ATTACHMENTS) throw new Error('Only 6 attachments are allowed per message.');
          const mime_type = attachmentType(file);
          const data_base64 = await readAttachment(file);
          attached.push(preparedAttachment(await request('/agent/attachments/prepare', { name: file.name, mime_type, data_base64 })));
        } catch (error) { errors.push(file.name + ': ' + (error.message || 'Could not prepare this file.')); }
        patch(id, { attachments: [...attached], attachmentErrors: [...errors] });
      }
    } finally { locks.current.delete(id); patch(id, { preparing: false }); }
  }
  function fileDrag(event) { return Array.from(event.dataTransfer?.types || []).includes('Files'); }
  function dragEnter(event) { if (!fileDrag(event)) return; event.preventDefault(); dragDepth.current++; setDragging(true); }
  function dragLeave(event) { if (!fileDrag(event)) return; event.preventDefault(); if (--dragDepth.current <= 0) { dragDepth.current = 0; setDragging(false); } }
  function dropFiles(event) {
    if (!fileDrag(event)) return;
    event.preventDefault(); dragDepth.current = 0; setDragging(false);
    const items = Array.from(event.dataTransfer.items || []);
    if (items.some(item => item.webkitGetAsEntry?.()?.isDirectory)) {
      patch(chat.id, { attachmentErrors: ['Directories cannot be attached. Use Select project or the browser snapshot folder picker.'] }); return;
    }
    attachFiles(event.dataTransfer.files);
  }
  async function chooseFolder(event) {
    const input = Array.from(event.target.files || []); event.target.value = '';
    if (!input.length || locks.current.has(chat.id) || chat.messages.length) return;
    const id = chat.id; locks.current.add(id); setReading(true);
    try { patch(id, { ...await readProject(input), error: '' }); setReview(true); }
    catch { patch(id, { error: 'Could not read this folder. Try selecting it again.' }); }
    finally { locks.current.delete(id); setReading(false); }
  }
  async function localAction(action) {
    const id = chat.id;
    if (!storageReady || locks.current.has(id)) return;
    locks.current.add(id); patch(id, { pending: true, error: '' });
    try { await action(id); }
    catch (e) { patch(id, { error: e.message || 'Local operation failed. Existing state retained.' }); }
    finally { locks.current.delete(id); patch(id, { pending: false }); }
  }
  function connect(path) {
    localAction(async id => { const data = await request('/agent/workspaces/open', { path }); workspaceTokens.current.set(id, typeof data.token === 'string' ? data.token : data.workspace_id); patch(id, { ...workspaceSnapshot(data), needsReconnect: false, approval: null, applied: [] }); setReview(true); });
  }
  function refresh() {
    localAction(async id => { const data = await request('/agent/workspaces/snapshot', { workspace_id: chat.workspace_id }); patch(id, { ...workspaceSnapshot(data), approval: null }); });
  }
  function reviewChanges(proposal, revision, key) {
    if (!revision || locks.current.has(chat.id)) return;
    patch(chat.id, { approval: { kind: 'files', proposal, revision, key }, error: '' });
  }
  function prepare(argv) {
    localAction(async id => {
      patch(id, { approval: null });
      const data = await request('/agent/workspaces/commands/prepare', { workspace_id: chat.workspace_id, argv });
      if (!data || typeof data.approval_id !== 'string' || !data.approval_id || !validArgv(data.argv) || typeof data.cwd !== 'string' || typeof data.warning !== 'string') throw new Error('Invalid command approval response. Nothing was run.');
      patch(id, { approval: { kind: 'command', approval_id: data.approval_id, argv: data.argv, cwd: data.cwd, warning: data.warning } });
    });
  }
  function confirmApproval() {
    const approval = chat.approval;
    if (!approval) return;
    localAction(async id => {
      try {
        if (approval.kind === 'command') {
          const result = await request('/agent/workspaces/commands/run', { workspace_id: chat.workspace_id, approval_id: approval.approval_id, approved: true });
          const summary = commandResultText(result);
          setChats(all => all.map(c => c.id === id ? { ...c, commandResults: [...c.commandResults, summary], draft: [c.draft, summary].filter(Boolean).join('\n\n') } : c));
        } else {
          const data = await request('/agent/workspaces/apply', { workspace_id: chat.workspace_id, revision: approval.revision, changes: approval.proposal.changes });
          patch(id, { ...workspaceSnapshot(data), applied: approval.key ? [...chat.applied, approval.key] : chat.applied });
        }
      } finally { patch(id, { approval: null }); }
    });
  }
  async function send(event) {
    event.preventDefault();
    const id = chat.id;
    if (!storageReady || locks.current.has(id)) return;
    if (!projectReady(chat)) { patch(id, { error: chat.needsReconnect ? 'Reconnect this local project before sending. Filesystem access is never restored automatically.' : 'Select a project folder before sending. Attachments are not a project directory.' }); return; }
    const invalid = validateSettings(settings);
    if (invalid) { patch(id, { error: invalid }); openSettings(); return; }
    let payload;
    try { payload = projectPayload(chat, settings); } catch (e) { patch(id, { error: e.message }); return; }
    locks.current.add(id); patch(id, { pending: true, error: '', approval: null });
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 120000);
    try {
      const data = await request('/agent/project-chat', payload, controller.signal);
      const assistant = assistantMessage(data);
      patch(id, { messages: [...chat.messages, payload.messages.at(-1), assistant], draft: '', attachments: [], attachmentErrors: [], error: '', lastContext: safeContext(data.context) });
    } catch (e) { patch(id, { error: e.name === 'AbortError' ? 'The request timed out. Your draft is kept; try again.' : (e.message || 'Could not send. Your draft is kept; try again.') + (payload.messages.some(m => m.attachments?.some(a => a.kind === 'image')) ? ' Images require a vision-capable model and provider. Your draft and attachments are kept.' : '') }); }
    finally { clearTimeout(timeout); locks.current.delete(id); patch(id, { pending: false }); }
  }
  async function openFilePreview(path) {
    const generation = ++previewGeneration.current;
    const local = Boolean(chat.workspace_id || chat.needsReconnect);
    setFilePreview({ local, loading: local, item: null, error: '' });
    try {
      let files = chat.files.filter(file => file.selected);
      if (local) {
        const token = workspaceTokens.current.get(chat.id);
        if (chat.needsReconnect || !chat.workspace_id || !token) throw new Error('Reconnect this local project to preview its allowed files. No disk access was attempted.');
        const data = await request('/agent/workspaces/snapshot', { workspace_id: chat.workspace_id });
        const snapshot = workspaceSnapshot(data);
        files = snapshot.files;
      }
      if (generation !== previewGeneration.current) return;
      const item = path ? files.find(file => file.path === path) : null;
      setFilePreview({ local, files, item, loading: false, error: path && !item ? 'This path is not in the allowed snapshot. No arbitrary file was read.' : '' });
    } catch (error) { if (generation === previewGeneration.current) setFilePreview({ local, files: [], item: null, loading: false, error: error.message || 'Preview unavailable. Reconnect and try again.' }); }
  }
  function previewAttachment(item) { previewGeneration.current++; setFilePreview({ item, local: false, files: [] }); }
  function closeFilePreview() { previewGeneration.current++; setFilePreview(null); }
  const busy = chat.pending || chat.preparing || reading || !storageReady;
  const frozen = busy || Boolean(chat.workspace_id) || chat.messages.length > 0;
  const selected = chat.files.filter(f => f.selected);
  return <div className="project-shell" hidden={hidden}>
    <aside className="project-sidebar" aria-label="Project conversations">
      <a className="brand" href="#project-main"><span className="brand-mark">c:</span>codebook</a>
      <button className="new-conversation" disabled={reading || !storageReady} onClick={addChat}>+ New chat</button>
      <p className="sidebar-label">PROJECT CHATS</p>
      <nav className="conversation-list" aria-label="Conversations">{chats.map(c => <div className="conversation-row" key={c.id}>
        <button className="conversation-select" disabled={reading || !storageReady} aria-current={c.id === chat.id ? 'page' : undefined} onClick={() => { setActive(c.id); setReview(false); setMenu(null); }}><span>{c.name}</span><small>{c.preparing ? 'Preparing attachments…' : c.pending ? 'Thinking...' : c.messages.length / 2 + ' exchanges'}</small></button>
        <button className="conversation-more" type="button" aria-label={'Options for ' + c.name} aria-haspopup="menu" aria-expanded={menu === c.id} disabled={reading || !storageReady} onClick={event => { const rect = event.currentTarget.getBoundingClientRect(); setMenuPosition({ top: Math.min(rect.bottom + 4, globalThis.innerHeight - 150), left: Math.max(8, Math.min(rect.right - 220, globalThis.innerWidth - 230)) }); setMenu(menu === c.id ? null : c.id); }}>⋯</button>
        {menu === c.id && <div className="conversation-menu" style={menuPosition} role="menu" onKeyDown={event => menuKeydown(event, () => { setMenu(null); document.querySelector('.conversation-more[aria-expanded="true"]')?.focus(); })}><button role="menuitem" onClick={() => projectSettings(c.id)}>Project settings</button><button role="menuitem" disabled={c.pending || c.preparing} onClick={() => { const name = globalThis.prompt('Conversation name', c.name); if (name?.trim()) patch(c.id, { name: name.trim().slice(0, 120) }); setMenu(null); }}>Rename</button><button role="menuitem" disabled={chats.some(item => item.pending || item.preparing) || reading} onClick={() => deleteChat(c.id)}>Delete conversation</button></div>}
      </div>)}</nav>
      <div className="sidebar-bottom"><button onClick={onCompiler}>Online compiler <span aria-hidden="true">↗</span></button><button onClick={openSettings}>Connection settings</button><small role="status">{!storageReady ? 'Loading saved chats…' : storageError || (saving ? 'Saving locally…' : 'Chats saved on this browser. API keys are not saved.')}</small></div>
    </aside>
    <main className={"project-main" + (filePreview ? " with-preview" : "")} id="project-main" onDragEnter={dragEnter} onDragLeave={dragLeave} onDragOver={event => { if (fileDrag(event)) { event.preventDefault(); event.dataTransfer.dropEffect = busy ? 'none' : 'copy'; } }} onDrop={dropFiles}>
      {dragging && <div className="file-drop-overlay" role="status">{busy ? 'Busy: wait before dropping files' : 'Drop documents or images to attach (up to 6)'}</div>}
      <header className="project-header"><div><span className="sidebar-label">PROJECT CHAT</span><h1>{chat.name}</h1><button className="project-status" onClick={() => projectSettings(chat.id)}>{chat.needsReconnect ? 'Local project · reconnect required' : chat.workspace_id ? 'Local project connected' : selected.length ? selected.length + ' snapshot files' : 'No project selected'} <span aria-hidden="true">↗</span></button>{(!projectReady(chat) || chat.needsReconnect) && <aside className="project-tutorial" role="note"><p>{chat.needsReconnect ? 'Reconnect your local project before asking about its code.' : 'Select a project folder before asking about its code'}</p><small>Attachments are not a project directory.</small><button type="button" onClick={() => projectSettings(chat.id)}>{chat.needsReconnect ? 'Reconnect project' : 'Select project'}</button></aside>}</div><div><button type="button" className="preview-files-toggle" onClick={event => { event.currentTarget.focus(); openFilePreview(); }} aria-expanded={Boolean(filePreview)}>Preview files</button><span className="project-route">{route}</span><small>{health}</small></div></header>
      <div className="project-scroll">
        <dialog ref={settingsDialog} className="project-settings-dialog" aria-labelledby="project-settings-title" onClick={event => { if (event.target === settingsDialog.current) settingsDialog.current.close(); }}><header><div><span className="sidebar-label">CONVERSATION WORKSPACE</span><h2 id="project-settings-title">Project settings</h2><p>{chat.name}</p></div><button className="settings-close" type="button" aria-label="Close project settings" onClick={() => settingsDialog.current.close()}>×</button></header>
        <section className="project-context" aria-label="Project snapshot">
          <WorkspaceControls key={chat.id} request={request} chat={chat} busy={busy} connect={connect} refresh={refresh} prepare={prepare} reviewChanges={reviewChanges} />
          <div className="context-toolbar"><span>{selected.length} files selected</span>{chat.files.length > 0 && <button className="text-button" onClick={() => setReview(v => !v)} aria-expanded={review}>{review ? 'Hide file review' : 'Review files'}</button>}</div>
          <p>{chat.workspace_id ? 'Live workspace. Refresh from disk at any time when idle. File operations require your approval.' : 'Browser snapshot, not live access. Reselect before sending; start a new chat to change files afterward.'}</p>
          <details className="coding-toolkit"><summary>Coding toolkit <span>Proposals first · explicit local approvals</span></summary><p><code>list_files</code> · <code>read_file</code> · <code>search_files</code> · <code>inspect_project</code> · <code>analyze_python</code> · <code>validate_json</code> · <code>check_conflicts</code> · <code>propose_changes</code> · <code>delegate_tasks</code></p><p>Diagnostics are static: Python analysis, JSON validation and conflict-marker checks do not run your code. Delegated workers use separate context through the same configured provider/gateway: at most 3 workers per request, 2 at once, and no nested workers. The orchestrator may choose an optional model override per task; otherwise workers use your current connection model. Workers cannot run shell commands or apply changes. Results appear only after the request completes.</p><p>Work across frameworks through selected plain-text source and manifests: React, Vue, Svelte, Astro, Python, .NET, Java/Kotlin, Flutter/Dart, Ruby, PHP, Go, Rust, Elixir/Erlang and more. This is code assistance, not framework execution.</p><p>Local mode supports explicit file and command approvals. Snapshot mode never writes or executes. No automatic installation, deployment or browser automation.</p></details>
          {review && chat.files.length > 0 && <div className="file-review"><p>Review before sharing. In local mode the entire eligible snapshot is shared; in browser mode deselect anything private; filename filtering cannot find every secret inside a file.</p>{chat.files.map(file => <details key={file.path}><summary><label onClick={e => e.stopPropagation()}><input type="checkbox" checked={file.selected} disabled={frozen} onChange={e => patch(chat.id, { files: chat.files.map(f => f.path === file.path ? { ...f, selected: e.target.checked } : f) })} />{file.path}</label><small>{file.content.length.toLocaleString()} chars</small></summary><pre>{file.content}</pre></details>)}</div>}
          {chat.skipped.length > 0 && <details className="skipped-files"><summary>{chat.skipped.length} files skipped entirely (not truncated)</summary><ul>{chat.skipped.map((f, i) => <li key={i}>{f.path}: {f.reason}</li>)}</ul></details>}
        </section>{chat.error && <p className="error" role="alert">{chat.error}</p>}<WorkspaceApproval approval={chat.approval} busy={busy} cancel={() => patch(chat.id, { approval: null })} confirm={confirmApproval} /></dialog>
        <WorkspaceApproval approval={chat.approval} busy={busy} cancel={() => patch(chat.id, { approval: null })} confirm={confirmApproval} />
        {chat.commandResults.map((result, i) => <details className="command-result" key={i}><summary>Approved local command result {i + 1}</summary><pre>{result}</pre><p>Added to your draft for the next prompt; not sent automatically.</p></details>)}
        {contextStart(chat) > 0 && <p className="context-banner" role="status">Earlier messages not sent. Compact conversation keeps recent turns without a summary; all earlier messages remain visible and saved.</p>}
        <section className="project-transcript" aria-label="Conversation" aria-live="polite">
          {!chat.messages.length && <div className="project-welcome"><span className="welcome-glyph" aria-hidden="true">{'{ }'}</span><h2>Let's look at your project.</h2><p>Choose a project folder, then ask a question. Attach supporting files if needed.<br />Understand the code. Work through a change. Find the next step.</p><div className="prompt-ideas">{['Explain the project structure', 'What should I improve first?'].map(text => <button key={text} disabled={chat.pending} onClick={() => patch(chat.id, { draft: text })}>{text}</button>)}</div></div>}
          {chat.messages.map((m, i) => <article className={`project-message ${m.role}`} key={i}><strong>{m.role === 'user' ? 'You' : 'Codebook'}</strong>{m.role === 'assistant' ? <><Markdown text={m.content} files={selected} onPreview={openFilePreview} /><AssistantTools message={m} messageIndex={i} local={Boolean(chat.workspace_id)} disabled={busy} applied={chat.applied} reviewChanges={reviewChanges} prepare={prepare} /></> : <><Attachments items={m.attachments} onPreview={previewAttachment} /><p>{m.content}</p></>}</article>)}
          {chat.pending && <div className="pending-message" role="status">Reading your snapshot and thinking...</div>}<div ref={end} />
        </section>
      </div>

      <div className="project-composer-wrap">{chat.error && <div className="error" role="alert">{chat.error}</div>}{chat.messages.length >= MAX_MESSAGES && <p role="status">500-message storage safety limit reached. Start a new chat to continue; compacting only changes what is sent.</p>}
        <input ref={picker} className="sr-only" aria-label="Choose project folder" type="file" webkitdirectory="" multiple onChange={chooseFolder} disabled={frozen} />
        <input ref={attachmentPicker} className="sr-only" type="file" multiple accept={ATTACHMENT_ACCEPT} aria-label="Attach files" disabled={busy} onChange={event => { const files = Array.from(event.target.files || []); event.target.value = ''; attachFiles(files); }} />
        {chat.attachmentErrors?.map((error, index) => <p className="attachment-error" role="alert" key={index}>{error}</p>)}
        {chat.preparing && <p role="status">Preparing attachments locally…</p>}
        <form className="project-composer" onSubmit={send}><Attachments items={chat.attachments} onPreview={previewAttachment} disabled={busy} remove={index => patch(chat.id, { attachments: chat.attachments.filter((_, i) => i !== index) })} /><label className="sr-only" htmlFor="project-prompt">Message about your project</label><textarea id="project-prompt" placeholder="Ask about your project..." value={chat.draft} disabled={busy} maxLength={10000} onChange={e => patch(chat.id, { draft: e.target.value })} onKeyDown={e => handleComposerKeydown(e, { busy })} /><div className="composer-actions"><div className="attach-control"><button type="button" className="attach-toggle" aria-label="Add attachment or browser snapshot" aria-expanded={attachMenu} aria-haspopup="menu" disabled={busy} onClick={() => setAttachMenu(!attachMenu)}>+</button>{attachMenu && <div className="attach-menu" role="menu" onKeyDown={event => menuKeydown(event, () => { setAttachMenu(false); document.querySelector('.attach-toggle')?.focus(); })}><button type="button" role="menuitem" disabled={busy || chat.attachments?.length >= MAX_ATTACHMENTS} onClick={() => { setAttachMenu(false); attachmentPicker.current.click(); }}>Attach files</button><button type="button" role="menuitem" disabled={frozen} aria-describedby="snapshot-reason" onClick={() => { setAttachMenu(false); picker.current.click(); }}>Choose browser snapshot</button><small id="snapshot-reason">{chat.messages.length ? 'Snapshot is frozen after the first message. Start a new chat to switch.' : chat.workspace_id ? 'A local workspace is connected. Start a new chat for a browser snapshot.' : 'Select a folder to review its source files.'}</small></div>}</div><div className="composer-right"><ModelPicker settings={settings} catalog={modelCatalog} onOpen={onModelOpen} onReload={onModelReload} onSelect={onModelSelect} disabled={chats.some(item => item.pending) || !storageReady} /><div className="context-control"><button ref={contextButton} type="button" className="context-toggle" aria-label="Session info and context window" title="Session info and context window" aria-haspopup="dialog" aria-controls="context-popover" aria-expanded={contextOpen} onClick={() => { setAttachMenu(false); setContextOpen(value => !value); }}><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8" /><path d="M12 4a8 8 0 0 1 8 8" /></svg></button>{contextOpen && <section ref={contextPanel} tabIndex={-1} className="context-popover" id="context-popover" role="dialog" aria-labelledby="context-title"><header><h2 id="context-title">Session info</h2><button type="button" aria-label="Close session info" onClick={() => { setContextOpen(false); contextButton.current?.focus(); }}>×</button></header><ContextInfo preview={preview} lastContext={chat.lastContext} loading={previewLoading} error={previewError} /><p>Compact conversation retains the last four user turns and excludes earlier turns from future requests. It does not create a summary or delete visible history.</p><button type="button" className="secondary" disabled={busy || compactBoundary(chat.messages) <= contextStart(chat)} onClick={compactConversation}>Compact conversation</button></section>}</div><button className="primary composer-send" type="submit" aria-label={chat.pending ? 'Sending message' : 'Send message'} title={!projectReady(chat) ? chat.needsReconnect ? "Reconnect project first" : "Select project first" : "Send message"} disabled={busy || !projectReady(chat) || (!chat.draft.trim() && !chat.attachments?.length) || chat.messages.length >= MAX_MESSAGES}><span aria-hidden="true">{chat.pending ? '…' : '↑'}</span></button></div></div></form>
        <details className="project-disclosure"><summary>Prepared locally · shared only on Send</summary><p>Attachments are parsed by the local backend before sending; no provider receives them until Send. On Send, attachments, eligible local workspace contents (or selected browser snapshot files) and this chat go through the local backend to your chosen provider{settings.mode === '9router' ? ' via 9router' : ''}. Local edits and commands require separate explicit approval. Commands are not sandboxed. Verify AI advice.</p></details>
      </div>
      {filePreview && <FilePreview key={chat.id} state={filePreview} files={filePreview.files || []} onSelect={openFilePreview} onClose={closeFilePreview} />}
    </main>
  </div>;
}
