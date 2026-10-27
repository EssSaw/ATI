import React, { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

export function CopyText({ text, label = 'Copy code' }) {
  const [feedback, setFeedback] = useState('');
  async function copy() {
    try { await navigator.clipboard.writeText(text); setFeedback('Copied'); }
    catch { setFeedback('Clipboard unavailable. Select the text to copy.'); }
  }
  return <><button type="button" onClick={copy}>{label}</button><span className="copy-feedback" role="status">{feedback}</span></>;
}
export default function Markdown({ text, files = [], onPreview }) {
  const pathFor = value => files.find(file => file.path === value || './' + file.path === value)?.path;
  return <div className="safe-markdown"><ReactMarkdown remarkPlugins={[remarkGfm]} components={{
    a({ href, children }) {
      const path = pathFor(href);
      if (path && onPreview) return <button type="button" className="file-reference" onClick={event => { event.currentTarget.focus(); onPreview(path); }}>{children}</button>;
      return href && /^(https?:\/\/|mailto:)/i.test(href) ? <a href={href} target="_blank" rel="noopener noreferrer">{children}</a> : <span>{children}</span>;
    },
    img({ alt }) { return <span className="blocked-image">[Image blocked{alt ? ': ' + alt : ''}]</span>; },
    code({ children, className }) {
      const path = !className && typeof children === 'string' && pathFor(children);
      return path && onPreview ? <button type="button" className="file-reference" onClick={event => { event.currentTarget.focus(); onPreview(path); }}><code>{children}</code></button> : <code className={className}>{children}</code>;
    },
    pre({ children }) {
      const code = React.Children.toArray(children)[0];
      const value = String(code?.props?.children ?? '');
      const language = code?.props?.className?.replace(/^language-/, '') || 'text';
      return <div className="code-fence"><div className="code-heading"><span>{language}</span><CopyText text={value} /></div><pre><code className={code?.props?.className}>{value}</code></pre></div>;
    },
    table({ children }) { return <div className="markdown-table"><table>{children}</table></div>; },
  }}>{text}</ReactMarkdown></div>;
}
