import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { defaults, example, preferences, providers, validateSettings } from './state.js';
import './styles.css';

function loadSettings() {
  try {
    const stored = JSON.parse(localStorage.getItem('codebook-preferences') || '{}');
    const candidate = { ...defaults, ...preferences({ ...defaults, ...stored }), api_key: '', router_api_key: '' };
    return ['direct', '9router'].includes(candidate.mode) && providers[candidate.provider] ? candidate : { ...defaults };
  } catch { return { ...defaults }; }
}
async function request(path, body) {
  const response = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  let data;
  try { data = await response.json(); } catch { throw new Error('The local backend returned an unreadable response. Check that it is running on port 8000.'); }
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Request failed (${response.status}). Check your connection settings and try again.`);
  return data;
}
function App() {
  const [settings, setSettings] = useState(loadSettings);
  const [code, setCode] = useState(example);
  const [question, setQuestion] = useState('Why does this crash, and how can I include students scoring exactly 60?');
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [messages, setMessages] = useState([]);
  const [message, setMessage] = useState('');
  const [tab, setTab] = useState('report');
  const [health, setHealth] = useState('Checking backend');
  const [copied, setCopied] = useState(false);
  const dialog = useRef(null);
  const lines = useRef(null);
  const lock = useRef(false);
  useEffect(() => {
    fetch('/health').then(r => r.ok ? r.json() : Promise.reject()).then(d => setHealth(d.status === 'ok' ? 'Backend connected' : 'Backend unavailable')).catch(() => setHealth('Backend unavailable'));
  }, []);
  function update(key, value) { setSettings(s => ({ ...s, [key]: value })); }
  function saveSettings(event) {
    event.preventDefault();
    const invalid = validateSettings(settings);
    if (invalid) { setError(invalid); return; }
    try { localStorage.setItem('codebook-preferences', JSON.stringify(preferences(settings))); } catch { /* Preferences are optional when browser storage is unavailable. */ }
    setError(''); dialog.current.close();
  }
  async function run(event) {
    event.preventDefault();
    if (lock.current || !code.trim()) return;
    const invalid = validateSettings(settings);
    if (invalid) { setError(invalid); dialog.current.showModal(); return; }
    lock.current = true; setBusy('run'); setError(''); setResult(null); setMessages([]); setMessage(''); setTab('report'); setCopied(false);
    try {
      const data = await request('/agent/run', { code, question, settings });
      if (!data.session_id || !data.report || !Array.isArray(data.report.bugs) || typeof data.report.fixed_code !== 'string' || !Array.isArray(data.trace)) throw new Error('The backend response is missing the report or trace. No analysis has been displayed.');
      setResult(data);
    } catch (e) { setError(e.message || 'Could not reach the local backend.'); }
    finally { lock.current = false; setBusy(''); }
  }
  async function chat(event) {
    event.preventDefault();
    if (lock.current || !message.trim() || !result) return;
    const invalid = validateSettings(settings);
    if (invalid) { setError(invalid); dialog.current.showModal(); return; }
    const text = message.trim(); lock.current = true; setBusy('chat'); setError('');
    try {
      const data = await request('/agent/chat', { session_id: result.session_id, message: text, settings });
      if (typeof data.reply !== 'string') throw new Error('The backend did not return a chat reply.');
      setMessages(m => [...m, { role: 'You', text }, { role: 'Codebook', text: data.reply }]); setMessage('');
    } catch (e) { setError(e.message || 'Could not send your message.'); }
    finally { lock.current = false; setBusy(''); }
  }
  async function copyCode() {
    try { await navigator.clipboard.writeText(result.report.fixed_code); setCopied(true); } catch { setError('Clipboard access is unavailable. Select and copy the corrected code manually.'); }
  }
  const model = settings.mode === '9router' ? settings.router_model : settings.model;
  return <>
    <header className="masthead"><a className="brand" href="#main"><span className="brand-mark">c<span>:</span></span>codebook<span className="brand-label">STUDENT WORKBENCH</span></a><div className="header-actions"><span className={`health ${health === 'Backend connected' ? 'online' : ''}`}><i />{health}</span><button className="settings-button" onClick={() => { setError(''); dialog.current.showModal(); }}>⚙ <span>Connection settings</span></button></div></header>
    <main id="main">
      <section className="intro"><div><p className="eyebrow">A LITTLE GUIDANCE. A LOT OF LEARNING.</p><h1>Understand the <em>why.</em><br />Then fix the code.</h1><p className="intro-copy">Your Python study partner. Find the bug, follow the reasoning,<br className="desktop-break" /> and leave with a better understanding—not just an answer.</p></div><aside className="lesson-note"><span className="note-number">01 / THE PROCESS</span><p>Write. Investigate.<br /><em>Learn something new.</em></p><span>Real tools. Clear explanations. Your code.</span></aside></section>
      <div className="workspace-label"><span>YOUR WORKBENCH</span><span className="route-badge">{settings.mode === '9router' ? '9router' : providers[settings.provider].label} <span> / {model || 'Choose a model'}</span></span></div>
      {error && !dialog.current?.open && <div className="error" role="alert"><strong>Something needs attention.</strong> {error}<button aria-label="Dismiss error" onClick={() => setError('')}>×</button></div>}
      <div className="workspace">
        <section className="panel editor-panel" aria-labelledby="editor-title"><form onSubmit={run}><div className="panel-heading"><div><span className="step">01</span><h2 id="editor-title">Your code</h2></div><span className="language"><i /> Python 3</span></div><div className="file-bar"><span>▤ &nbsp; practice.py</span><button type="button" disabled={!!busy} onClick={() => setCode(example)}>Reset example</button></div><div className="editor"><div className="line-numbers" ref={lines} aria-hidden="true">{code.split('\n').map((_, i) => <div key={i}>{i + 1}</div>)}</div><textarea aria-label="Python code" value={code} onChange={e => setCode(e.target.value)} onScroll={e => { lines.current.scrollTop = e.target.scrollTop; }} spellCheck="false" autoCapitalize="off" autoCorrect="off" wrap="off" disabled={!!busy} /></div><div className="question-area"><label htmlFor="question">What would you like to understand?<span>Optional</span></label><textarea id="question" value={question} onChange={e => setQuestion(e.target.value)} placeholder="Describe what you expected, or where you’re stuck…" disabled={!!busy} rows="2" /><div className="run-row"><span>Make mistakes.<br />That’s how you learn.</span><button className="primary" disabled={!!busy || !code.trim()}>{busy === 'run' ? <><span className="spinner" /> Analyzing…</> : <>Analyze code <span aria-hidden="true">↗</span></>}</button></div></div></form></section>
        <section className="panel results-panel" aria-labelledby="results-title"><div className="panel-heading"><div><span className="step">02</span><h2 id="results-title">The learning report</h2></div>{result && <span className="duration">{(result.latency_ms / 1000).toFixed(1)}s</span>}</div><div className="tabs" role="tablist" aria-label="Analysis results">{[['report', 'Overview'], ['code', 'Corrected code'], ['trace', 'Agent trace']].map(([id, label]) => <button key={id} id={`tab-${id}`} role="tab" aria-selected={tab === id} aria-controls={`panel-${id}`} tabIndex={tab === id ? 0 : -1} onKeyDown={e => { if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) { e.preventDefault(); const ids = ['report', 'code', 'trace']; const next = e.key === 'Home' ? 'report' : e.key === 'End' ? 'trace' : ids[(ids.indexOf(tab) + (e.key === 'ArrowRight' ? 1 : 2)) % 3]; setTab(next); document.getElementById(`tab-${next}`).focus(); } }} onClick={() => setTab(id)}>{label}{id === 'trace' && result && <span className="count">{result.trace.length}</span>}</button>)}</div>
          <div className="result-body" id={`panel-${tab}`} role="tabpanel" aria-labelledby={`tab-${tab}`} tabIndex="0" aria-busy={busy === 'run'}>
            {!result ? <div className="empty-state" role="status"><div className={`empty-icon ${busy === 'run' ? 'working' : ''}`}>{busy === 'run' ? '⌕' : '{ }'}</div><span className="eyebrow">{busy === 'run' ? 'INVESTIGATION IN PROGRESS' : 'A FRESH PAIR OF EYES'}</span><h3>{busy === 'run' ? 'Taking a closer look.' : 'Every bug has a lesson.'}</h3><p>{busy === 'run' ? 'Waiting for your backend to return its report. Actual tool calls will appear in Agent trace after the run completes.' : 'Add your connection settings, then analyze your code. Your findings, explanation, and corrected code will appear here.'}</p><div className="empty-checks"><span>01 &nbsp; Find the issue</span><span>02 &nbsp; Understand it</span><span>03 &nbsp; Try the fix</span></div></div> : tab === 'report' ? <><div className="report-summary"><span className="eyebrow">THE BIG PICTURE</span><h3>{result.report.summary}</h3><span className="finding-count">{result.report.bugs.length} finding{result.report.bugs.length !== 1 ? 's' : ''} to explore</span></div><div className="findings">{result.report.bugs.map((bug, i) => <article className="finding" key={i}><div><span className={`bug-type ${bug.type}`}>{bug.type}</span><span className="line-label">{bug.line == null ? 'General' : `Line ${bug.line}`}</span></div><h4>{bug.message}</h4><p>{bug.suggestion}</p></article>)}{!result.report.bugs.length && <p className="no-findings">No issues reported by this analysis. Keep testing your edge cases.</p>}</div><div className="explanation"><h3>Let’s unpack it</h3><p>{result.report.explanation}</p></div></> : tab === 'code' ? <><div className="code-heading"><p>A version to learn from.<br /><small>Review the changes before you use them.</small></p><button className="secondary" onClick={copyCode}>{copied ? 'Copied ✓' : 'Copy code'}</button></div><pre className="fixed-code" tabIndex="0"><code>{result.report.fixed_code}</code></pre></> : <><p className="trace-note">Actual tool calls returned by the backend—not a live simulation.</p>{result.trace.length ? <ol className="trace-list">{result.trace.map((item, i) => <li key={i}><span className="trace-index">{String(i + 1).padStart(2, '0')}</span><div><div className="trace-title"><strong>{item.tool}</strong><span>{item.duration_ms} ms</span></div><span className="tool-status">{item.status}</span><p>{item.detail}</p></div></li>)}</ol> : <p>No tool calls were reported for this run.</p>}<p className="tools-used">Tools used: {(result.tools_used || []).join(', ') || 'None reported'}</p></>}
          </div>
        </section>
      </div>
      <section className="chat-panel" aria-labelledby="chat-title"><div className="chat-heading"><span className="step">03</span><div><h2 id="chat-title">Keep asking questions.</h2><p>{result ? 'Continue with the context of this analysis.' : 'Your follow-up conversation starts after the first analysis.'}</p></div><span className="chat-decoration" aria-hidden="true">↳</span></div>{messages.length > 0 && <div className="messages" role="log" aria-label="Follow-up conversation">{messages.map((m, i) => <article className={`message ${m.role === 'You' ? 'user' : ''}`} key={i}><strong>{m.role}</strong><p>{m.text}</p></article>)}</div>}<form className="chat-form" onSubmit={chat}><label className="sr-only" htmlFor="followup">Follow-up question</label><input id="followup" value={message} onChange={e => setMessage(e.target.value)} disabled={!result || !!busy} placeholder="Why does an empty list cause this error?" /><button className="primary" disabled={!result || !!busy || !message.trim()}>{busy === 'chat' ? 'Thinking…' : 'Ask a follow-up'} <span aria-hidden="true">↑</span></button></form><p className="chat-footnote">New analysis, fresh conversation. Your questions stay tied to the current report.</p></section>
      <footer><span><strong>Learn, don’t just copy.</strong> AI can make mistakes. Verify its suggestions.</span><span>Runtime execution is unavailable in this local build.<br />Code is analyzed, never executed on your host.</span></footer>
    </main>
    <dialog ref={dialog} className="settings-dialog" aria-labelledby="settings-title"><form onSubmit={saveSettings}><div className="dialog-heading"><div><span className="eyebrow">YOUR CONNECTION</span><h2 id="settings-title">Choose your study partner.</h2></div><button type="button" aria-label="Close settings" className="close" onClick={() => dialog.current.close()}>×</button></div><p className="privacy-note">API keys stay in memory only—never in browser storage or URLs. They are sent only to the local backend and your chosen service. Reloading clears them.</p><fieldset disabled={!!busy}><legend>Connection mode</legend><div className="mode-switch">{['direct', '9router'].map(mode => <label key={mode} className={settings.mode === mode ? 'active' : ''}><input type="radio" name="mode" value={mode} checked={settings.mode === mode} onChange={() => update('mode', mode)} />{mode === 'direct' ? 'Direct provider' : '9router gateway'}</label>)}</div>{settings.mode === 'direct' ? <div className="setting-fields"><label>Provider<select value={settings.provider} onChange={e => setSettings(s => ({ ...s, provider: e.target.value, base_url: providers[e.target.value].base_url, model: providers[e.target.value].model, api_key: '' }))}>{Object.entries(providers).map(([id, p]) => <option key={id} value={id}>{p.label}</option>)}</select></label><label>API base URL<input type="url" required value={settings.base_url} onChange={e => update('base_url', e.target.value)} placeholder="https://api.example.com/v1" autoComplete="off" /></label><label>Model<input required value={settings.model} onChange={e => update('model', e.target.value)} placeholder="Provider model ID" /></label><label>Provider API key<input type="password" value={settings.api_key} onChange={e => update('api_key', e.target.value)} autoComplete="off" spellCheck="false" placeholder="Your key — cleared on reload" /></label></div> : <div className="setting-fields"><p className="router-note">Separate gateway settings. Use the exact model identifier your 9router supports, including its provider prefix.</p><label>9router base URL<input type="url" required value={settings.router_base_url} onChange={e => update('router_base_url', e.target.value)} autoComplete="off" /></label><label>9router model<input required value={settings.router_model} onChange={e => update('router_model', e.target.value)} placeholder="e.g. openai/gpt-4o-mini" /></label><label>9router API key<input type="password" value={settings.router_api_key} onChange={e => update('router_api_key', e.target.value)} autoComplete="off" spellCheck="false" placeholder="If your gateway requires a key" /></label></div>}</fieldset>{error && <p className="error" role="alert">{error}</p>}<div className="dialog-footer"><span>Only non-secret preferences are saved.</span><button className="primary" disabled={!!busy}>Use connection ↗</button></div></form></dialog>
  </>;
}

createRoot(document.getElementById('root')).render(<App />);
