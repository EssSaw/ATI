import React, { useEffect, useRef, useState } from 'react';
import { providers } from './state.js';

export default function ModelPicker({ settings, catalog, onOpen, onReload, onSelect, disabled }) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const root = useRef(null), trigger = useRef(null), input = useRef(null);
  const router = settings.mode === '9router';
  const model = router ? settings.router_model : settings.model;
  useEffect(() => { onOpen?.(open); return () => onOpen?.(false); }, [open, onOpen]);
  useEffect(() => { if (disabled) setOpen(false); }, [disabled]);
  useEffect(() => {
    if (!open) return;
    input.current?.focus();
    const outside = event => { if (!root.current?.contains(event.target)) setOpen(false); };
    const escape = event => { if (event.key === 'Escape') { setOpen(false); trigger.current?.focus(); } };
    document.addEventListener('pointerdown', outside); document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', escape); };
  }, [open]);
  function select(id) { if (disabled || !id.trim()) return; onSelect?.(id); setOpen(false); trigger.current?.focus(); }
  const suggestions = router ? catalog.models : [providers[settings.provider]?.model].filter(Boolean);
  const matches = suggestions.filter(id => id.toLowerCase().includes(search.toLowerCase()));
  return <div className="model-control" ref={root}>
    <button ref={trigger} type="button" className="model-toggle" aria-label={'Choose model: ' + (model || 'Choose a model')} title={model || 'Choose a model'} aria-haspopup="dialog" aria-expanded={open} aria-controls="model-picker" disabled={disabled} onClick={() => { setSearch(''); setOpen(value => !value); }}><span>{model || 'Choose a model'}</span><span aria-hidden="true">⌄</span></button>
    {open && <section className="model-popover" id="model-picker" role="dialog" aria-label="Choose model"><h3>{router ? '9router models' : 'Provider model'}</h3><label>{router ? 'Search model IDs' : 'Exact model ID'}<input ref={input} value={search} placeholder={router ? 'Search models' : model} onChange={event => setSearch(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') { event.preventDefault(); if (!router) select(search); } }} /></label>
      {!router && <button type="button" className="secondary" disabled={!search.trim()} onClick={() => select(search)}>Use model ID</button>}
      {router && <><p role="status">{catalog.status === 'loading' || catalog.status === 'idle' ? 'Loading models...' : catalog.status === 'error' ? catalog.error : !catalog.models.length ? 'No models returned.' : !matches.length ? 'No matching models.' : 'Choose an exact model ID. Your selection is never changed automatically.'}</p><button type="button" className="text-button" onClick={onReload}>Reload models</button></>}
      <div className="model-options">{matches.map(id => <button key={id} type="button" aria-pressed={id === model} onClick={() => select(id)}>{id}</button>)}</div>
    </section>}
  </div>;
}
