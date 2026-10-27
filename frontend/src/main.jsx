import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { defaults, preferences, providers, validateSettings } from './state.js';
import { validateBudget } from './context.js';
import { scheduleModelLoad } from './models.js';
import './styles.css';
import ProjectChat from './ProjectChat.jsx';
import './project.css';
import Compiler from './Compiler.jsx';
import { validateCompilerSettings } from './compiler.js';

function loadSettings() {
  try {
    const stored = JSON.parse(localStorage.getItem('codebook-preferences') || '{}');
    const candidate = { ...defaults, ...preferences({ ...defaults, ...stored }), api_key: '', router_api_key: '', compiler_api_key: '' };
    if (!['browser', 'judge0'].includes(candidate.compiler_mode)) candidate.compiler_mode = 'browser';
    if (validateBudget(candidate)) { candidate.context_window = defaults.context_window; candidate.response_reserved = defaults.response_reserved; }
    return ['direct', '9router'].includes(candidate.mode) && providers[candidate.provider] ? candidate : { ...defaults };
  } catch { return { ...defaults }; }
}
async function request(path, body, signal) {
  const response = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal });
  let data;
  try { data = await response.json(); } catch { throw new Error('The local backend returned an unreadable response. Check that it is running on port 8000.'); }
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : Array.isArray(data.detail) ? data.detail.map(item => `${(item.loc || []).join('.')}: ${item.msg}`).join('; ') : `Request failed (${response.status}). Check your connection settings and try again.`);
  return data;
}
function App() {
  const [view, setView] = useState('project');
  const [settings, setSettings] = useState(loadSettings);
  const [compilerOpened, setCompilerOpened] = useState(false);
  const [error, setError] = useState('');
  const [health, setHealth] = useState('Checking backend');
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsTab, setSettingsTab] = useState('chat');
  const [catalog, setCatalog] = useState({ models: [], status: 'idle', error: '' });
  const [reloadModels, setReloadModels] = useState(0);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [chatPending, setChatPending] = useState(false);
  const cancelModels = useRef(() => {});
  const dialog = useRef(null);
  useEffect(() => {
    fetch('/health').then(r => r.ok ? r.json() : Promise.reject()).then(d => setHealth(d.status === 'ok' ? 'Backend connected' : 'Backend unavailable')).catch(() => setHealth('Backend unavailable'));
  }, []);
  useEffect(() => {
    if ((!pickerOpen && (!settingsOpen || settingsTab !== 'chat')) || settings.mode !== '9router') return;
    setCatalog({ models: [], status: 'loading', error: '' });
    const cancel = scheduleModelLoad(request, settings, setCatalog);
    cancelModels.current = cancel;
    return cancel;
  }, [pickerOpen, settingsOpen, settingsTab, settings.mode, settings.router_base_url, settings.router_api_key, reloadModels]);
  function clearCatalog() {
    cancelModels.current();
    setCatalog({ models: [], status: 'loading', error: '' });
  }
  function openSettings(tab) { clearCatalog(); setSettingsTab(tab); setError(''); setSettingsOpen(true); dialog.current.showModal(); }
  function selectSettingsTab(tab) { cancelModels.current(); setSettingsTab(tab); setError(''); }
  function navigateSettingsTabs(event) {
    const tabs = [...event.currentTarget.querySelectorAll('[role=tab]')];
    const index = tabs.indexOf(event.target);
    if (index < 0 || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
    tabs[next].click(); tabs[next].focus();
  }
  function closeSettings() { cancelModels.current(); setSettingsOpen(false); dialog.current.close(); }
  function selectModel(id) {
    if (chatPending || !id.trim()) return;
    setSettings(current => {
      const next = { ...current, [current.mode === '9router' ? 'router_model' : 'model']: id };
      try { localStorage.setItem('codebook-preferences', JSON.stringify(preferences(next))); } catch { /* Storage is optional. */ }
      return next;
    });
  }
  function update(key, value) {
    if (['mode', 'router_base_url', 'router_api_key'].includes(key)) clearCatalog();
    setSettings(s => ({ ...s, [key]: value }));
  }
  function saveSettings(event) {
    event.preventDefault();
    const invalid = settingsTab === 'compiler' ? validateCompilerSettings(settings) : validateSettings(settings);
    if (invalid) { setError(invalid); return; }
    try { localStorage.setItem('codebook-preferences', JSON.stringify(preferences(settings))); } catch { /* Preferences are optional when browser storage is unavailable. */ }
    setError(''); closeSettings();
  }
  const model = settings.mode === '9router' ? settings.router_model : settings.model;
  return <>
    <ProjectChat modelCatalog={catalog} onModelOpen={setPickerOpen} onModelReload={() => { clearCatalog(); setReloadModels(n => n + 1); }} onModelSelect={selectModel} onPendingChange={setChatPending} hidden={view !== 'project'} settings={settings} request={request} openSettings={() => openSettings('chat')} health={health} route={`${settings.mode === '9router' ? '9router' : providers[settings.provider].label} / ${model || 'Choose a model'}`} onCompiler={() => { setCompilerOpened(true); setView('compiler'); }} />
    {compilerOpened && <Compiler compilerSettings={settings} hidden={view !== 'compiler'} onBack={() => setView('project')} openSettings={() => openSettings('compiler')} />}
    <dialog ref={dialog} onClose={() => { cancelModels.current(); setSettingsOpen(false); }} onCancel={closeSettings} className="settings-dialog" aria-labelledby="settings-title"><form onSubmit={saveSettings} noValidate><div className="dialog-heading"><div><span className="eyebrow">YOUR CONNECTION</span><h2 id="settings-title">Connection settings.</h2></div><button type="button" aria-label="Close settings" className="close" onClick={closeSettings}>×</button></div><div className="settings-tabs" role="tablist" aria-label="Connection type" onKeyDown={navigateSettingsTabs}>{[['chat', 'Project Chat'], ['compiler', 'Compiler execution']].map(([id, label]) => <button key={id} type="button" role="tab" id={`settings-tab-${id}`} aria-controls={`settings-panel-${id}`} aria-selected={settingsTab === id} tabIndex={settingsTab === id ? 0 : -1} onClick={() => selectSettingsTab(id)}>{label}</button>)}</div><div className="settings-body"><p className="privacy-note">Project Chat and compiler connections are separate. Browser execution needs no key. API keys stay in memory only—never in browser storage or URLs. They are sent only to the local backend and your chosen service. Reloading clears them.</p><section role="tabpanel" id="settings-panel-chat" aria-labelledby="settings-tab-chat" hidden={settingsTab !== 'chat'}><fieldset disabled={chatPending}><legend>Project Chat connection</legend><div className="mode-switch">{['direct', '9router'].map(mode => <label key={mode} className={settings.mode === mode ? 'active' : ''}><input type="radio" name="mode" value={mode} checked={settings.mode === mode} onChange={() => update('mode', mode)} />{mode === 'direct' ? 'Direct provider' : '9router gateway'}</label>)}</div>{settings.mode === 'direct' ? <div className="setting-fields"><label>Provider<select value={settings.provider} onChange={e => setSettings(s => ({ ...s, provider: e.target.value, base_url: providers[e.target.value].base_url, model: providers[e.target.value].model, api_key: '' }))}>{Object.entries(providers).map(([id, p]) => <option key={id} value={id}>{p.label}</option>)}</select></label><label>API base URL<input type="url" required value={settings.base_url} onChange={e => update('base_url', e.target.value)} placeholder="https://api.example.com/v1" autoComplete="off" /></label><label>Model<input required value={settings.model} onChange={e => update('model', e.target.value)} placeholder="Provider model ID" /></label><label>Provider API key<input type="password" value={settings.api_key} onChange={e => update('api_key', e.target.value)} autoComplete="off" spellCheck="false" placeholder="Your key — cleared on reload" /></label></div> : <div className="setting-fields"><p className="router-note">Models load automatically from your gateway. Choose its exact model identifier, including the provider prefix.</p><label>9router base URL<input type="url" required value={settings.router_base_url} onChange={e => update('router_base_url', e.target.value)} autoComplete="off" /></label><label>9router model<select required value={settings.router_model} onChange={e => update('router_model', e.target.value)} aria-describedby="model-status" aria-busy={catalog.status === 'loading'}><option value="">Select a model</option>{settings.router_model && !catalog.models.includes(settings.router_model) && <option value={settings.router_model} disabled>{settings.router_model} (unavailable)</option>}{catalog.models.map(id => <option key={id} value={id}>{id}</option>)}</select></label><p id="model-status" role="status" className="router-note">{catalog.status === 'loading' ? 'Loading models...' : catalog.status === 'error' ? catalog.error : catalog.models.length ? 'Model catalog loaded. Your selection is not changed automatically.' : 'No models returned. Check your gateway configuration and reload.'}{settings.router_model && !catalog.models.includes(settings.router_model) && ' Your saved model is retained; select another model only if you want to change it.'}</p><button type="button" className="secondary" onClick={() => { clearCatalog(); setReloadModels(n => n + 1); }}>Reload models</button><label>9router API key<input type="password" value={settings.router_api_key} onChange={e => update('router_api_key', e.target.value)} autoComplete="off" spellCheck="false" placeholder="If your gateway requires a key" /></label></div>}</fieldset><fieldset className="context-settings"><legend>Chat context budget</legend><div className="setting-fields"><label>Context window (tokens)<input type="number" min="8192" max="2000000" step="1" value={settings.context_window} onChange={e => update('context_window', Number(e.target.value))} /></label><label>Response reserve (tokens)<input type="number" min="512" max="32768" step="1" value={settings.response_reserved} onChange={e => update('response_reserved', Number(e.target.value))} /></label></div><p className="router-note">Set your provider's actual limit. Model names do not determine the budget. Estimates are approximate, not tokenizer counts.</p></fieldset></section><section role="tabpanel" id="settings-panel-compiler" aria-labelledby="settings-tab-compiler" hidden={settingsTab !== 'compiler'}><fieldset className="compiler-settings"><legend>Compiler execution (separate from Project Chat)</legend><div className="setting-fields"><label>Execution mode<select value={settings.compiler_mode} onChange={e => update('compiler_mode', e.target.value)}><option value="browser">Browser - free JavaScript + Python, no key</option><option value="judge0">Judge0 - optional, bring your own connection</option></select></label><p className="router-note">Browser mode never calls the compiler API. Runtime assets download from a CDN; Python's first download is larger. Drafts and keys stay in memory.</p>{settings.compiler_mode === 'judge0' && <><label>Judge0 base URL<input type="url" value={settings.compiler_base_url} onChange={e => update('compiler_base_url', e.target.value)} placeholder="https://judge0-ce.p.rapidapi.com" autoComplete="off" /></label><label>Judge0 API key<input type="password" value={settings.compiler_api_key} onChange={e => update('compiler_api_key', e.target.value)} autoComplete="off" spellCheck="false" placeholder="Your Judge0 key - cleared on reload" /></label><label>RapidAPI host (optional)<input value={settings.compiler_rapidapi_host} onChange={e => update('compiler_rapidapi_host', e.target.value)} placeholder="judge0-ce.p.rapidapi.com" autoComplete="off" spellCheck="false" /></label><p className="router-note">Enter your own Judge0 endpoint and key. For RapidAPI, use https://judge0-ce.p.rapidapi.com and host judge0-ce.p.rapidapi.com. Leave host empty for a non-RapidAPI deployment. No default service or hidden fallback is used. Source and stdin go to this service through the local backend.</p></>}</div></fieldset></section>{error && <p className="error" role="alert">{error}</p>}</div><div className="dialog-footer"><span>Only non-secret preferences are saved.</span><button className="primary">Use {settingsTab === 'chat' ? 'chat' : 'compiler'} connection {'\u2197'}</button></div></form></dialog>
  </>;
}

createRoot(document.getElementById('root')).render(<App />);
