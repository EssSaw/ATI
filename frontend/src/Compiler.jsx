import React, { useEffect, useRef, useState } from 'react';
import { BROWSER_LANGUAGES, compilerRequest, defaultSource, parseResult, scheduleCompilerLoad, SOURCE_LIMIT, STDIN_LIMIT, statusTone, validateCompilerSettings, validateRun } from './compiler.js';
import { runBrowserCode } from './browserRunner.js';
import './compiler.css';

export default function Compiler({ hidden, onBack, openSettings, compilerSettings }) {
  const mode = compilerSettings.compiler_mode;
  const browser = mode === 'browser';
  const [catalog, setCatalog] = useState({ languages: [], status: 'idle', error: '' });
  const [selection, setSelection] = useState({ browser: 'javascript', judge0: '' });
  const [sources, setSources] = useState({});
  const [stdin, setStdin] = useState('');
  const [runError, setRunError] = useState('');
  const [running, setRunning] = useState(false);
  const [runStatus, setRunStatus] = useState('');
  const [result, setResult] = useState(null);
  const [reload, setReload] = useState(0);
  const activeRun = useRef(null);
  const lines = useRef(null);
  const configError = validateCompilerSettings(compilerSettings);
  const connectionKey = JSON.stringify([mode, compilerSettings.compiler_base_url, compilerSettings.compiler_api_key, compilerSettings.compiler_rapidapi_host]);
  const currentCatalog = catalog.connectionKey === connectionKey;
  const languages = browser ? BROWSER_LANGUAGES : currentCatalog ? catalog.languages : [];
  const languageId = selection[mode] || '';
  const language = languages.find(item => String(item.id) === languageId);
  const draftKey = mode + ':' + languageId;
  const source = sources[draftKey] ?? (language ? defaultSource(language.name) : '');
  const catalogState = browser ? 'ready' : currentCatalog ? catalog.status : 'idle';
  const catalogError = configError || (currentCatalog ? catalog.error : '');
  const invalid = configError || validateRun(language?.id, source, stdin, mode);

  useEffect(() => {
    setCatalog({ languages: [], status: 'idle', error: '' });
    if (hidden || browser || configError) return;
    setCatalog({ languages: [], status: 'loading', error: '', connectionKey });
    return scheduleCompilerLoad(compilerSettings, next => {
      setCatalog({ ...next, connectionKey });
      setSelection(previous => ({ ...previous, judge0: next.languages.some(item => String(item.id) === previous.judge0) ? previous.judge0 : String((next.languages.find(item => /python/i.test(item.name)) || next.languages[0])?.id ?? '') }));
    });
  }, [hidden, mode, compilerSettings.compiler_base_url, compilerSettings.compiler_api_key, compilerSettings.compiler_rapidapi_host, reload]);

  function stop(message = browser ? 'Stopped. Browser execution terminated.' : 'Stopped waiting. The remote Judge0 job may still run; stopping does not cancel it on the service.') {
    const task = activeRun.current;
    activeRun.current = null;
    task?.cancel();
    setRunning(false);
    setRunStatus(message);
  }

  useEffect(() => {
    if (activeRun.current) stop('Execution settings changed. Run again with the current settings.');
    else setRunStatus('');
    setResult(null);
    setRunError('');
  }, [mode, compilerSettings.compiler_base_url, compilerSettings.compiler_api_key, compilerSettings.compiler_rapidapi_host]);
  useEffect(() => () => { activeRun.current?.cancel(); }, []);

  async function run(event) {
    event.preventDefault();
    if (activeRun.current || catalogState !== 'ready') return;
    if (invalid) { setRunError(invalid); return; }
    const task = { cancel: () => {} };
    activeRun.current = task;
    setRunning(true);
    setRunError('');
    setResult(null);
    setRunStatus(browser ? 'Starting isolated browser worker...' : 'Waiting for Judge0...');
    try {
      let promise;
      if (browser) {
        const execution = runBrowserCode({ language: language.id, source, stdin, onStatus: status => {
          if (activeRun.current === task) setRunStatus(typeof status === 'string' ? status : status?.message || 'Running in browser worker...');
        } });
        task.cancel = execution.cancel;
        promise = execution.promise;
      } else {
        const controller = new AbortController();
        task.cancel = () => controller.abort();
        promise = compilerRequest('run', { language_id: language.id, source, stdin }, controller.signal, compilerSettings);
      }
      const data = parseResult(await promise);
      if (activeRun.current === task) { setResult({ ...data, language: language.name }); setRunStatus('Execution complete.'); }
    } catch (error) {
      if (activeRun.current === task) { setRunError(error.message || 'Execution failed.'); setRunStatus('Execution failed.'); }
    } finally {
      if (activeRun.current === task) { activeRun.current = null; setRunning(false); }
    }
  }

  return <div className="compiler-shell" hidden={hidden}>
    <header className="compiler-header">
      <button className="secondary" onClick={onBack}>&larr; Project Chat</button>
      <a className="brand" href="#compiler-main"><span className="brand-mark">c<span>:</span></span>codebook<span className="brand-label">ONLINE COMPILER</span></a>
      <button className="settings-button" onClick={openSettings}>Connection settings</button>
    </header>
    <main id="compiler-main" className="compiler-main">
      <section className="compiler-intro"><div><p className="eyebrow">WRITE. RUN. SEE WHAT HAPPENS.</p><h1>A little code.<br /><em>A real result.</em></h1><p>JavaScript and Python, right in your browser. More languages when you bring Judge0.</p></div><aside><span className="eyebrow">EXECUTION, NOT AI</span><p>{browser ? 'Free browser execution. No key.' : 'Your Judge0 connection. Your choice.'}</p><small>{browser ? 'Code runs off the main thread in an isolated browser worker. No execution API submission.' : 'Optional remote execution uses only the URL and key you supply in Connection settings.'}</small></aside></section>
      <p className="compiler-disclosure"><strong>{browser ? 'Browser mode:' : 'Judge0 mode:'}</strong> {browser ? 'Runtime assets download from a CDN. Source and stdin are not submitted to an execution API. The runner restricts network access, but this browser isolation is not a production security sandbox: do not run untrusted or sensitive code. Python uses Pyodide: expect a larger first download; standard-library programs are supported, not arbitrary native packages.' : 'Source and stdin are sent through the local backend to your configured Judge0 service. Do not submit secrets. Stop aborts this app’s request, not the remote job.'} Drafts stay in memory only; reloading clears them.</p>
      <div className="compiler-grid">
        <section className="panel compiler-editor" aria-labelledby="compiler-source-title"><form onSubmit={run}>
          <div className="panel-heading"><div><span className="step">01</span><h2 id="compiler-source-title">Source code</h2></div><span className="compiler-count">{source.length.toLocaleString('en-US')} / 50,000</span></div>
          <div className="compiler-language"><label htmlFor="compiler-language">Language &amp; version</label><div><select id="compiler-language" value={languageId} disabled={running || catalogState !== 'ready' || !languages.length} onChange={event => { setSelection(previous => ({ ...previous, [mode]: event.target.value })); setRunError(''); setResult(null); }} aria-describedby="compiler-catalog-status"><option value="" disabled>Select a language</option>{languages.map(item => <option key={item.id} value={String(item.id)}>{item.name}</option>)}</select><button type="button" className="secondary" hidden={browser} disabled={running || catalogState === 'loading' || !!configError} onClick={() => { setResult(null); setRunError(''); setReload(value => value + 1); }}>Reload languages</button></div><p id="compiler-catalog-status" role="status">{catalogState === 'loading' ? 'Loading available language versions...' : catalogState === 'error' ? 'Language catalog unavailable.' : languages.length ? `${languages.length} language versions available.` : 'No languages available. Configure Judge0 in Connection settings, then reload.'}</p></div>
          {catalogError && <p className="compiler-error" role="alert">{catalogError}</p>}
          <div className="compiler-code"><div className="compiler-lines" ref={lines} aria-hidden="true">{source.split('\n').map((_, index) => <div key={index}>{index + 1}</div>)}</div><textarea id="compiler-source" aria-label="Source code" value={source} maxLength={SOURCE_LIMIT} disabled={running || !language || catalogState !== 'ready'} onChange={event => { setSources(previous => ({ ...previous, [draftKey]: event.target.value })); setResult(null); setRunError(''); }} onScroll={event => { lines.current.scrollTop = event.target.scrollTop; }} spellCheck="false" autoCapitalize="off" autoCorrect="off" wrap="off" /></div>
          <div className="compiler-input"><label htmlFor="compiler-stdin">Standard input <span>Optional &middot; {stdin.length.toLocaleString('en-US')} / 20,000</span></label><textarea id="compiler-stdin" rows="4" value={stdin} maxLength={STDIN_LIMIT} onChange={event => { setStdin(event.target.value); setResult(null); setRunError(''); }} disabled={running} spellCheck="false" placeholder="Input your program reads from stdin" /><div className="compiler-run"><small>{browser ? 'Local browser worker.' : 'Remote Judge0 service.'}<br />No analysis or fabricated output.</small><div className="compiler-actions">{running && <button type="button" className="secondary" onClick={() => stop()}>Stop</button>}<button className="primary" disabled={running || catalogState !== 'ready' || !!invalid}>{running ? <><span className="spinner" /> Running...</> : <>Run code <span aria-hidden="true">{'\u2197'}</span></>}</button></div></div></div>
        </form></section>
        <section className="panel compiler-results" aria-labelledby="compiler-results-title" aria-busy={running}>
          <div className="panel-heading"><div><span className="step">02</span><h2 id="compiler-results-title">Execution result</h2></div><span className="compiler-count">{browser ? 'BROWSER WORKER' : 'JUDGE0 SERVICE'}</span></div>
          <p className="compiler-live-status" role="status" aria-live="polite">{runStatus}</p>
          {runError && <p className="compiler-error" role="alert">{runError}</p>}
          {!result ? <div className="compiler-empty" role="status"><span aria-hidden="true">{'{ }'}</span><p className="eyebrow">{running ? 'EXECUTION IN PROGRESS' : 'READY WHEN YOU ARE'}</p><h3>{running ? 'Your program is running.' : 'Let the code speak.'}</h3><p>{running ? runStatus : 'Run your program to see its actual status, output, and available metrics here.'}</p></div> : <>
            <div className="compiler-result-summary"><div className={`compiler-status ${statusTone(result.status.id)}`} role="status">{result.status.description} <span>#{result.status.id}</span></div><p>{result.language}</p><dl><div><dt>Exit code</dt><dd>{result.exit_code ?? 'Not reported'}</dd></div><div><dt>Time</dt><dd>{result.time === null ? 'Not reported' : `${result.time} s`}</dd></div><div><dt>Memory</dt><dd>{result.memory === null ? 'Not reported' : `${result.memory} KB`}</dd></div></dl></div>
            {result.truncated && <p className="compiler-warning" role="status">Execution output was truncated. The streams below may be incomplete.</p>}
            <div className="compiler-streams">{[['stdout', 'Standard output (stdout)'], ['stderr', 'Standard error (stderr)'], ['compile_output', 'Compiler output'], ['message', 'Service message']].map(([key, label]) => <section key={key} aria-labelledby={`compiler-${key}-title`}><h3 id={`compiler-${key}-title`}>{label}</h3><pre tabIndex="0">{result[key] || '(empty)'}</pre></section>)}</div>
          </>}
        </section>
      </div>
      <footer className="compiler-footer"><span><strong>Real execution. Honest feedback.</strong> A successful request is not necessarily a successful program.</span><span>{browser ? 'JavaScript + Python. No execution API key.' : 'Language versions supplied by your Judge0 service.'}</span></footer>
    </main>
  </div>;
}
