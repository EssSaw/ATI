import React, { useEffect, useRef, useState } from 'react';
import { safeImageUrl } from './attachments.js';
import { CopyText } from './Markdown.jsx';

export default function FilePreview({ state, files, onSelect, onClose }) {
  const panel = useRef(null);
  const [mobile, setMobile] = useState(() => globalThis.matchMedia('(max-width: 760px)').matches);
  useEffect(() => {
    const media = globalThis.matchMedia('(max-width: 760px)');
    const update = () => setMobile(media.matches);
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);
  useEffect(() => {
    const previous = document.activeElement;
    panel.current?.focus();
    return () => { if (previous?.isConnected) previous.focus(); };
  }, []);
  const item = state.item;
  const image = item?.kind === 'image';
  const extracted = item?.mime_type === 'application/pdf' || item?.mime_type?.includes('wordprocessingml');
  const text = item?.content ?? item?.text ?? '';
  const name = item?.path ?? item?.name;
  function download() {
    if (image && !safeImageUrl(item.data_url)) return;
    const url = image ? item.data_url : URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }));
    const link = document.createElement('a');
    link.href = url; link.download = extracted ? name.replace(/\.(pdf|docx)$/i, '') + '.txt' : name;
    link.click();
    if (!image) setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  function keydown(event) {
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); onClose(); }
    if (event.key === 'Tab' && mobile) {
      const buttons = [...panel.current.querySelectorAll('button:not(:disabled)')];
      if (event.shiftKey && (document.activeElement === buttons[0] || document.activeElement === panel.current)) { event.preventDefault(); buttons.at(-1)?.focus(); }
      else if (!event.shiftKey && document.activeElement === buttons.at(-1)) { event.preventDefault(); buttons[0]?.focus(); }
    }
  }
  return <section ref={panel} tabIndex={-1} className="file-preview" role="dialog" aria-modal={mobile || undefined} aria-labelledby="file-preview-title" onKeyDown={keydown}>
    <header><div><small>FILE PREVIEW</small><h2 id="file-preview-title">{name || 'Project files'}</h2></div><button type="button" aria-label="Close file preview" onClick={onClose}>×</button></header>
    <p className="preview-privacy">{state.local ? 'Local snapshot fetched only on explicit preview. Not live; disk changes may make it stale.' : 'Saved browser snapshot / prepared attachments, not live disk access.'} Preview does not send files to your provider.</p>
    {files.length > 0 && <nav aria-label="Preview project files">{files.map(file => <button type="button" key={file.path} aria-current={name === file.path ? 'true' : undefined} onClick={() => onSelect(file.path)}>{file.path}</button>)}</nav>}
    {state.loading && <p role="status">Reading allowed local snapshot…</p>}
    {state.error && <p role="alert" className="error">{state.error}</p>}
    {!state.loading && item && <><div className="preview-actions">{!image && <CopyText key={name} text={text} label="Copy text" />}<button type="button" onClick={download}>Download {extracted ? 'extracted .txt' : image ? 'image' : 'text'}</button></div>{extracted && <p>Extracted document text. Original document bytes are unavailable; download contains text only, not a rendered PDF/DOCX.</p>}<div className="preview-content">{image ? safeImageUrl(item.data_url) ? <img src={item.data_url} alt={name} /> : <p>Unsafe image blocked.</p> : <pre aria-label={extracted ? 'Extracted document text' : 'File contents'}><code>{text}</code></pre>}</div></>}
    {!item && !state.loading && !state.error && <p>Select a file to preview.{!files.length && ' No selected eligible files in this snapshot.'}</p>}
  </section>;
}
