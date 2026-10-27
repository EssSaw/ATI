export const MAX_ATTACHMENTS = 6;
export const MAX_HISTORY_ATTACHMENTS = 12;
export const IMAGE_LIMIT = 4 * 1024 * 1024;
export const DOCUMENT_LIMIT = 5 * 1024 * 1024;
export const TEXT_LIMIT = 100000;
const imageTypes = { png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', webp: 'image/webp' };
const documentTypes = { pdf: 'application/pdf', docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' };
const textExtensions = new Set('txt text log md mdx rst csv tsv json jsonl yaml yml xml html htm css scss sass less js jsx ts tsx mjs cjs py pyi java kt kts c h cpp hpp cs fs fsx go rs rb php swift dart ex exs erl hrl sh bash zsh sql toml ini cfg conf vue svelte astro graphql gql proto dockerfile makefile gitignore'.split(' '));
export const ATTACHMENT_ACCEPT = [...Object.keys(imageTypes), ...Object.keys(documentTypes), ...textExtensions].map(ext => '.' + ext).join(',');
export function attachmentType(file) {
  const extension = String(file.name || '').toLowerCase().split('.').at(-1);
  const mime = imageTypes[extension] || documentTypes[extension] || (textExtensions.has(extension) ? 'text/plain' : null);
  if (!mime) throw new Error('Unsupported file type. Choose PNG, JPEG, WEBP, PDF, DOCX, or a text/code/log file.');
  const limit = mime.startsWith('image/') ? IMAGE_LIMIT : DOCUMENT_LIMIT;
  if (!Number.isSafeInteger(file.size) || file.size < 1 || file.size > limit) throw new Error(`File must be nonempty and no larger than ${limit / 1024 / 1024} MB.`);
  return mime;
}
export function safeImageUrl(url) {
  return typeof url === 'string' && /^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/]+={0,2}$/.test(url);
}
export function preparedAttachment(value) {
  if (!value || typeof value.id !== 'string' || !value.id.trim() || typeof value.name !== 'string' || !value.name.trim() || value.name.length > 200 || typeof value.mime_type !== 'string' || !Number.isSafeInteger(value.size) || value.size < 1) throw new Error('Invalid attachment metadata. Remove the file and attach it again.');
  const base = { id: value.id, name: value.name, mime_type: value.mime_type, kind: value.kind, size: value.size };
  if (value.kind === 'image' && Object.values(imageTypes).includes(value.mime_type) && value.size <= IMAGE_LIMIT && safeImageUrl(value.data_url) && value.data_url.startsWith(`data:${value.mime_type};base64,`) && value.data_url.length <= Math.ceil(IMAGE_LIMIT / 3) * 4 + 64) return { ...base, data_url: value.data_url };
  if (value.kind === 'text' && (value.mime_type.startsWith('text/') || Object.values(documentTypes).includes(value.mime_type) || ['application/json', 'application/xml', 'application/javascript'].includes(value.mime_type)) && value.size <= DOCUMENT_LIMIT && typeof value.text === 'string' && value.text.trim() && value.text.length <= TEXT_LIMIT) return { ...base, text: value.text };
  throw new Error('Invalid prepared attachment content. Remove the file and attach it again.');
}
export function preparedAttachments(values = []) {
  if (!Array.isArray(values) || values.length > MAX_ATTACHMENTS) throw new Error('Attach no more than 6 files per message.');
  return values.map(preparedAttachment);
}
export function readAttachment(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => typeof reader.result === 'string' && reader.result.includes(',') ? resolve(reader.result.slice(reader.result.indexOf(',') + 1)) : reject(new Error('Could not read this file.'));
    reader.onerror = () => reject(new Error('Could not read this file.'));
    reader.onabort = () => reject(new Error('File reading was cancelled.'));
    reader.readAsDataURL(file);
  });
}
