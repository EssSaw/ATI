import React, { useEffect, useRef, useState } from 'react';
import { MAX_MESSAGES, assistantMessage, projectPayload, proposalDownload, readProject } from './project.js';
import { validateSettings } from './state.js';

function Markdown({ text }) {
  const blocks = text.split(/(```[^\n]*\n[\s\S]*?(?:```|$))/g);
  return <div className="safe-markdown">{blocks.map((block, i) => {
    if (block.startsWith('```')) { const newline = block.indexOf('\n'); return <pre key={i}><code>{block.slice(newline + 1).replace(/```$/, '')}</code></pre>; }
    return block.split('\n').map((line, j) => {
      const heading = /^(#{1,6})\s+(.+)/.exec(line);
      if (heading) return React.createElement(`h${Math.min(heading[1].length + 1, 6)}`, { key: `${i}-${j}` }, heading[2]);
      const list = /^\s*(?:[-*]|\d+\.)\s+(.+)/.exec(line);
      if (list) return <ul key={`${i}-${j}`}><li>{list[1]}</li></ul>;
      return line ? <p key={`${i}-${j}`}>{line}</p> : <br key={`${i}-${j}`} />;
    });
  })}</div>;
}
function Proposal({ proposal }) {
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
    <div className="proposal-heading"><strong>Change proposal</strong><span>Not applied</span></div>
    <p>{proposal.summary}</p>
    <ul className="proposal-paths">{proposal.changes.map((change, i) => <li key={i}><span>{change.operation}</span><code>{change.path}</code></li>)}</ul>
    <details className="proposal-diff"><summary>Review unified diff</summary><pre><code>{proposal.diff}</code></pre></details>
    <div className="proposal-actions"><button type="button" className="secondary" onClick={copy}>Copy patch</button><button type="button" className="secondary" onClick={download}>Download .patch</button></div>
    <small>Proposal only. Your folder and snapshot are unchanged.</small>
    {feedback && <p className="proposal-feedback" role="status">{feedback}</p>}
  </section>;
}
function AssistantTools({ message }) {
  return <>{message.trace?.length > 0 && <details className="tool-activity"><summary>Tool activity <span>{message.trace.length} completed calls</span></summary><ol>{message.trace.map((step, i) => <li key={i}><div><code>{step.tool}</code><span className={`tool-status ${step.status}`}>{step.status}</span><small>{step.duration_ms} ms</small></div><p>{step.detail}</p></li>)}</ol></details>}{message.proposals?.map((proposal, i) => <Proposal key={`${proposal.id}-${i}`} proposal={proposal} />)}</>;
}
const newChat = () => ({ id: crypto.randomUUID(), name: 'New conversation', files: [], skipped: [], messages: [], draft: '', error: '', pending: false });
export default function ProjectChat({ settings, request, openSettings, health, route, onReview, hidden }) {
  const [chats, setChats] = useState(() => [newChat()]);
  const [active, setActive] = useState(null);
  const [reading, setReading] = useState(false);
  const [review, setReview] = useState(true);
  const locks = useRef(new Set());
  const picker = useRef(null);
  const end = useRef(null);
  const chat = chats.find(c => c.id === active) || chats[0];
  const patch = (id, changes) => setChats(all => all.map(c => c.id === id ? { ...c, ...changes } : c));
  useEffect(() => { if (!hidden) end.current?.scrollIntoView({ block: 'nearest' }); }, [chat.id, chat.messages.length, chat.pending, hidden]);
  function addChat() { const next = newChat(); setChats(all => [...all, next]); setActive(next.id); setReview(true); }
  async function chooseFolder(event) {
    const input = Array.from(event.target.files || []); event.target.value = '';
    if (!input.length || locks.current.has(chat.id) || chat.messages.length) return;
    const id = chat.id; locks.current.add(id); setReading(true);
    try { patch(id, { ...await readProject(input), error: '' }); setReview(true); }
    catch { patch(id, { error: 'Could not read this folder. Try selecting it again.' }); }
    finally { locks.current.delete(id); setReading(false); }
  }
  async function send(event) {
    event.preventDefault();
    const id = chat.id;
    if (locks.current.has(id)) return;
    const invalid = validateSettings(settings);
    if (invalid) { patch(id, { error: invalid }); openSettings(); return; }
    let payload;
    try { payload = projectPayload(chat, settings); } catch (e) { patch(id, { error: e.message }); return; }
    locks.current.add(id); patch(id, { pending: true, error: '' });
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 120000);
    try {
      const data = await request('/agent/project-chat', payload, controller.signal);
      const assistant = assistantMessage(data);
      patch(id, { messages: [...chat.messages, payload.messages.at(-1), assistant], draft: '', error: '' });
    } catch (e) { patch(id, { error: e.name === 'AbortError' ? 'The request timed out. Your draft is kept; try again.' : e.message || 'Could not send. Your draft is kept; try again.' }); }
    finally { clearTimeout(timeout); locks.current.delete(id); patch(id, { pending: false }); }
  }
  const frozen = chat.pending || reading || chat.messages.length > 0;
  const selected = chat.files.filter(f => f.selected);
  return <div className="project-shell" hidden={hidden}>
    <aside className="project-sidebar" aria-label="Project conversations">
      <a className="brand" href="#project-main"><span className="brand-mark">c:</span>codebook</a>
      <button className="new-conversation" disabled={reading} onClick={addChat}>+ New chat</button>
      <p className="sidebar-label">PROJECT CHATS</p>
      <nav className="conversation-list">{chats.map(c => <button key={c.id} disabled={reading} aria-current={c.id === chat.id ? 'page' : undefined} onClick={() => { setActive(c.id); setReview(false); }}><span>{c.name}</span><small>{c.pending ? 'Thinking...' : `${c.messages.length / 2} exchanges`}</small></button>)}</nav>
      <div className="sidebar-bottom"><button onClick={onReview}>Python review <span aria-hidden="true">↗</span></button><button onClick={openSettings}>Connection settings</button><small>In memory only. Reload clears chats and keys.</small></div>
    </aside>
    <main className="project-main" id="project-main">
      <header className="project-header"><div><span className="sidebar-label">PROJECT CHAT</span><h1>{chat.name}</h1></div><div><span className="project-route">{route}</span><small>{health}</small></div></header>
      <div className="project-scroll">
        <section className="project-context" aria-label="Project snapshot">
          <div className="context-toolbar"><button className="secondary" disabled={frozen} onClick={() => picker.current.click()}>{reading ? 'Reading locally...' : chat.files.length ? 'Reselect folder' : 'Choose project folder'}</button><span>{selected.length} files selected</span>{chat.files.length > 0 && <button className="text-button" onClick={() => setReview(v => !v)} aria-expanded={review}>{review ? 'Hide file review' : 'Review files'}</button>}</div>
          <input ref={picker} className="sr-only" aria-label="Choose project folder" type="file" webkitdirectory="" multiple onChange={chooseFolder} disabled={frozen} />
          <p>Folder snapshot, not live access. Reselect to refresh before sending; start a new chat to change files afterward.</p>
          <details className="coding-toolkit"><summary>Coding toolkit <span>Snapshot only · proposals, never edits</span></summary><p><code>list_files</code> · <code>read_file</code> · <code>search_files</code> · <code>inspect_project</code> · <code>propose_changes</code></p><p>Work across frameworks through selected plain-text source and manifests: React, Vue, Svelte, Astro, Python, .NET, Java/Kotlin, Flutter/Dart, Ruby, PHP, Go, Rust, Elixir/Erlang and more. This is code assistance, not framework execution.</p><p>Unavailable: terminal commands, package installation, browser automation and deployment. No live disk access or automatic application of changes.</p></details>
          {review && chat.files.length > 0 && <div className="file-review"><p>Review before sharing. Deselect anything private; filename filtering cannot find every secret inside a file.</p>{chat.files.map(file => <details key={file.path}><summary><label onClick={e => e.stopPropagation()}><input type="checkbox" checked={file.selected} disabled={frozen} onChange={e => patch(chat.id, { files: chat.files.map(f => f.path === file.path ? { ...f, selected: e.target.checked } : f) })} />{file.path}</label><small>{file.content.length.toLocaleString()} chars</small></summary><pre>{file.content}</pre></details>)}</div>}
          {chat.skipped.length > 0 && <details className="skipped-files"><summary>{chat.skipped.length} files skipped entirely (not truncated)</summary><ul>{chat.skipped.map((f, i) => <li key={i}>{f.path}: {f.reason}</li>)}</ul></details>}
        </section>
        <section className="project-transcript" aria-label="Conversation" aria-live="polite">
          {!chat.messages.length && <div className="project-welcome"><span className="welcome-glyph" aria-hidden="true">{'{ }'}</span><h2>Let's look at your project.</h2><p>Choose a folder, review the files, and ask a question.<br />Understand the code. Work through a change. Find the next step.</p><div className="prompt-ideas">{['Explain the project structure', 'What should I improve first?'].map(text => <button key={text} disabled={chat.pending} onClick={() => patch(chat.id, { draft: text })}>{text}</button>)}</div></div>}
          {chat.messages.map((m, i) => <article className={`project-message ${m.role}`} key={i}><strong>{m.role === 'user' ? 'You' : 'Codebook'}</strong>{m.role === 'assistant' ? <><Markdown text={m.content} /><AssistantTools message={m} /></> : <p>{m.content}</p>}</article>)}
          {chat.pending && <div className="pending-message" role="status">Reading your snapshot and thinking...</div>}<div ref={end} />
        </section>
      </div>
      <div className="project-composer-wrap">{chat.error && <div className="error" role="alert">{chat.error}</div>}{chat.messages.length >= MAX_MESSAGES && <p role="status">30-message limit reached. Start a new chat to continue.</p>}
        <form className="project-composer" onSubmit={send}><label className="sr-only" htmlFor="project-prompt">Message about your project</label><textarea id="project-prompt" placeholder="Ask about your project..." value={chat.draft} disabled={chat.pending} maxLength={10000} onChange={e => patch(chat.id, { draft: e.target.value })} /><div><span>{chat.messages.length} / 30 messages</span><button className="primary" disabled={chat.pending || reading || !chat.draft.trim() || !selected.length || chat.messages.length >= MAX_MESSAGES}>{chat.pending ? 'Thinking...' : 'Send message'} <span aria-hidden="true">↑</span></button></div></form>
        <p className="project-disclosure">On Send, selected text files and this chat go through the local backend to your chosen provider{settings.mode === '9router' ? ' via 9router' : ''}. Read-only suggestions: no file edits or shell execution. Verify AI advice.</p>
      </div>
    </main>
  </div>;
}
