import React, { useState } from 'react';
import { parseCommand } from './project.js';

export default function WorkspaceControls({ chat, busy, connect, refresh, prepare, reviewChanges, request }) {
  const [path, setPath] = useState(chat.path || '');
  const [command, setCommand] = useState('["npm", "test"]');
  const [operation, setOperation] = useState('create');
  const [file, setFile] = useState('');
  const [target, setTarget] = useState('');
  const [content, setContent] = useState('');
  const [error, setError] = useState('');
  const [browsing, setBrowsing] = useState(false);
  async function browse() {
    setBrowsing(true); setError('');
    try {
      const result = await request('/agent/workspaces/browse', {});
      if (result.path !== null && (typeof result.path !== 'string' || !result.path)) throw new Error('The folder chooser returned an invalid path. Paste your project path instead.');
      if (result.path) setPath(result.path);
    } catch (err) { setError(err.message || 'Could not browse folders. Paste your project path instead.'); }
    finally { setBrowsing(false); }
  }
  return <section className="local-workspace" aria-label="Local workspace">
    {!chat.workspace_id ? <form onSubmit={e => { e.preventDefault(); connect(path.trim()); }}><div className="workspace-path-row"><label>Absolute local project path<input value={path} onChange={e => setPath(e.target.value)} placeholder="/Users/you/project" disabled={busy || browsing || (chat.messages.length > 0 && !chat.needsReconnect)} required /></label><button type="button" className="secondary" disabled={busy || browsing || (chat.messages.length > 0 && !chat.needsReconnect)} onClick={browse}>{browsing ? 'Choosing…' : 'Browse…'}</button></div><button className="secondary" disabled={busy || browsing || (chat.messages.length > 0 && !chat.needsReconnect) || !path.trim()}>{chat.needsReconnect ? 'Reconnect local project' : 'Connect local project'}</button><p>The backend reads this folder on its own machine. Connecting does not send contents to your AI provider.</p></form> : <>
      <div className="context-toolbar"><strong>Connected local project</strong><button className="secondary" disabled={busy} onClick={refresh}>Refresh from disk</button></div><p><code>{chat.path}</code></p>
      <details><summary>Run project command</summary><form onSubmit={e => { e.preventDefault(); try { const argv = parseCommand(command); setError(''); prepare(argv); } catch (err) { setError(err.message); } }}><label>Arguments (JSON array, no shell splitting)<textarea value={command} onChange={e => setCommand(e.target.value)} disabled={busy} /></label><button className="secondary" disabled={busy}>Review command</button></form></details>
      <details><summary>Create, rename or delete a file</summary><form onSubmit={e => { e.preventDefault(); reviewChanges({ summary: 'Manual file operation', changes: [{ path: file, operation, ...(operation === 'create' ? { content } : {}), ...(operation === 'rename' ? { target } : {}) }], diff: operation === 'create' ? content : `${operation}: ${file}${operation === 'rename' ? ' → ' + target : ''}` }, chat.revision); }}><label>Operation<select value={operation} disabled={busy} onChange={e => setOperation(e.target.value)}>{['create', 'rename', 'delete'].map(op => <option key={op}>{op}</option>)}</select></label><label>Relative file path<input value={file} onChange={e => setFile(e.target.value)} required disabled={busy} /></label>{operation === 'rename' && <label>New relative path<input value={target} onChange={e => setTarget(e.target.value)} required disabled={busy} /></label>}{operation === 'create' && <label>File contents<textarea value={content} onChange={e => setContent(e.target.value)} disabled={busy} /></label>}<button className="secondary" disabled={busy}>Review file operation</button></form></details>
    </>}
    {error && <p role="alert">{error}</p>}
    <p>Privacy: on every prompt, eligible local file contents and chat history are sent to the configured provider, including remote providers. Filename filters cannot detect every secret. Local mode shares the backend snapshot as a whole; use browser snapshot mode to select individual files.</p>
  </section>;
}

export function WorkspaceApproval({ approval, busy, cancel, confirm }) {
  if (!approval) return null;
  const command = approval.kind === 'command';
  return <section className="workspace-approval" role="region" aria-label={command ? 'Confirm command execution' : 'Confirm disk changes'}>
    <h3>{command ? 'Approve this exact command?' : 'Apply these changes to disk?'}</h3>
    {command ? <><p>Arguments: <code>{JSON.stringify(approval.argv)}</code></p><p>Working directory: <code>{approval.cwd}</code></p><p>{approval.warning}</p><p><strong>This runs as your OS user, NOT in a sandbox. Commands and project scripts can access arbitrary files, credentials and network resources. No automatic installation or deployment.</strong></p></> : <><p>{approval.proposal.summary}</p><p>Expected revision: <code>{approval.revision}</code></p><ul>{approval.proposal.changes.map((change, i) => <li key={i}>{change.operation} <code>{change.path}</code>{change.target && <> → <code>{change.target}</code></>}</li>)}</ul><pre>{approval.proposal.diff}</pre><p>Only your confirmation writes these changes. If files changed since this proposal, the server rejects it; refresh and request a new proposal rather than overwriting.</p></>}
    <div className="proposal-actions"><button className="secondary" disabled={busy} onClick={cancel}>{command ? 'Deny' : 'Cancel'}</button><button className="primary" disabled={busy} onClick={confirm}>{busy ? 'Working...' : command ? 'Approve and run' : 'Confirm and apply'}</button></div>
  </section>;
}
