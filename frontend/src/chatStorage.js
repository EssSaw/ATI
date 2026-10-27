import { contextStart, safeContext } from './context.js';
import { preparedAttachments } from './attachments.js';
import { assistantMessage } from './project.js';

export const DB_NAME = 'codebook-data';
export const DB_VERSION = 1;
export const STORE_NAME = 'state';
export const STORAGE_KEY = 'project-chats';

export const MAX_CHATS = 100;
export const MAX_MESSAGES = 500;
export const MAX_STORAGE_CHARS = 20 * 1024 * 1024; // 20 MB character budget

function generateId() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return 'chat-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10);
}

function sanitizeTrace(trace) {
  return Array.isArray(trace)
    ? trace
        .filter(t => t && typeof t.tool === 'string' && ['success', 'error'].includes(t.status) && typeof t.detail === 'string' && Number.isFinite(t.duration_ms) && t.duration_ms >= 0)
        .map(({ tool, status, detail, duration_ms }) => ({ tool, status, detail, duration_ms }))
    : [];
}

function sanitizeProposals(proposals) {
  return Array.isArray(proposals)
    ? proposals.filter(p => p && typeof p.id === 'string' && typeof p.summary === 'string' && typeof p.diff === 'string' && Array.isArray(p.changes) && p.changes.every(c => c && typeof c.path === 'string' && ['create', 'replace', 'delete'].includes(c.operation) && (c.operation === 'delete' || typeof c.content === 'string')))
    : [];
}

function sanitizeSubagents(subagents) {
  return Array.isArray(subagents)
    ? subagents
        .filter(w => w && typeof w.name === 'string' && w.name.trim() && typeof w.model === 'string' && w.model.trim() && ['success', 'error'].includes(w.status) && typeof w.reply === 'string')
        .slice(0, 3)
        .map(w => ({
          name: w.name,
          model: w.model,
          status: w.status,
          reply: w.reply,
          ...(typeof w.error === 'string' ? { error: w.error } : {}),
          trace: sanitizeTrace(w.trace),
        }))
    : [];
}

function sanitizeAssistantMessage(m) {
  if (!m || typeof m !== 'object') return null;
  const content = typeof m.content === 'string' ? m.content : (typeof m.reply === 'string' ? m.reply : '');
  if (content.trim()) {
    try {
      const validated = assistantMessage({
        reply: content,
        trace: m.trace,
        proposals: m.proposals,
        subagents: m.subagents,
        context: m.context,
      });
      return {
        context: validated.context,
        role: 'assistant',
        content: validated.content,
        trace: validated.trace,
        proposals: validated.proposals,
        subagents: validated.subagents,
        command_requests: [],
        workspace_revision: null,
      };
    } catch {
      // Fall through to manual sanitization if assistantMessage validation threw
    }
  }

  if (typeof content !== 'string') return null;
  return {
    role: 'assistant',
    content,
    trace: sanitizeTrace(m.trace),
    proposals: sanitizeProposals(m.proposals),
    subagents: sanitizeSubagents(m.subagents),
    command_requests: [],
    workspace_revision: null,
  };
}

function sanitizeUserMessage(m) {
  if (!m || typeof m !== 'object') return null;
  if (typeof m.content !== 'string') return null;
  const attachments = preparedAttachments(m.attachments);
  return { role: 'user', content: m.content, ...(attachments.length ? { attachments } : {}) };
}

function sanitizeMessages(messages) {
  if (!Array.isArray(messages)) return [];
  const sanitized = [];
  if (messages.length > MAX_MESSAGES) throw new Error('Conversation exceeds the saved message limit.');
  const bounded = messages;
  for (const m of bounded) {
    if (!m || typeof m !== 'object') continue;
    if (m.role === 'user') {
      const u = sanitizeUserMessage(m);
      if (u) sanitized.push(u);
    } else if (m.role === 'assistant') {
      const a = sanitizeAssistantMessage(m);
      if (a) sanitized.push(a);
    }
  }
  return sanitized;
}

function sanitizeFiles(files) {
  if (!Array.isArray(files)) return [];
  return files
    .filter(f => f && typeof f.path === 'string' && f.path.trim().length > 0)
    .map(f => ({
      path: f.path,
      content: typeof f.content === 'string' ? f.content : '',
      selected: Boolean(f.selected),
    }));
}

function sanitizeSkipped(skipped) {
  if (!Array.isArray(skipped)) return [];
  return skipped
    .filter(s => s && typeof s.path === 'string' && typeof s.reason === 'string')
    .map(s => ({
      path: s.path,
      reason: s.reason,
    }));
}

export function pack(chats, active = null) {
  if (!Array.isArray(chats)) {
    throw new TypeError('chats must be an array');
  }

  if (chats.length > MAX_CHATS) throw new Error('Saved chat limit reached. Delete a conversation before adding another.');
  const boundedChats = chats;
  const packedChats = [];

  for (const chat of boundedChats) {
    if (!chat || typeof chat !== 'object' || Array.isArray(chat)) {
      continue;
    }
    const id = typeof chat.id === 'string' && chat.id.trim() ? chat.id : generateId();
    const name = typeof chat.name === 'string' && chat.name.trim() ? chat.name : 'New conversation';
    const isLocal = chat.source === 'local' || Boolean(chat.workspace_id) || Boolean(chat.path);
    const source = isLocal ? 'local' : 'snapshot';
    const path = isLocal && typeof chat.path === 'string' ? chat.path : '';
    const draft = typeof chat.draft === 'string' ? chat.draft.slice(0, 10000) : '';
    const skipped = sanitizeSkipped(chat.skipped);
    const files = isLocal ? [] : sanitizeFiles(chat.files);
    const messages = sanitizeMessages(chat.messages);

    packedChats.push({
      id,
      name,
      source,
      path,
      draft,
      attachments: preparedAttachments(chat.attachments),
      files,
      skipped,
      messages,
      contextStart: contextStart({ ...chat, messages }),
      lastContext: safeContext(chat.lastContext),
    });
  }

  const activeId = typeof active === 'string' && packedChats.some(c => c.id === active)
    ? active
    : (packedChats[0]?.id || null);

  const payload = {
    version: DB_VERSION,
    active: activeId,
    chats: packedChats,
  };

  const serialized = JSON.stringify(payload);
  if (serialized.length > MAX_STORAGE_CHARS) {
    const error = new Error(`Storage quota exceeded: chats payload (${serialized.length} chars) exceeds ${MAX_STORAGE_CHARS} budget.`);
    error.name = 'QuotaExceededError';
    throw error;
  }

  return payload;
}

export function unpack(record) {
  let data = record;
  if (typeof data === 'string') {
    try {
      data = JSON.parse(data);
    } catch {
      throw new Error('Corrupted chat storage record: invalid JSON');
    }
  }

  if (!data || typeof data !== 'object' || Array.isArray(data)) {
    throw new Error('Corrupted chat storage record: expected an object');
  }

  if (typeof data.version !== 'number' || data.version !== DB_VERSION) {
    throw new Error(`Unsupported chat storage version: ${data.version}`);
  }

  if (!Array.isArray(data.chats)) {
    throw new Error('Corrupted chat storage record: chats must be an array');
  }

  if (JSON.stringify(data).length > MAX_STORAGE_CHARS) throw new Error('Saved chats exceed the 20 MB storage budget.');
  const restoredChats = [];
  if (data.chats.length > MAX_CHATS) throw new Error('Saved chat record exceeds supported limits.');
  const boundedChats = data.chats;

  for (const chat of boundedChats) {
    if (!chat || typeof chat !== 'object' || Array.isArray(chat)) {
      throw new Error('Corrupted chat storage record: chat entry must be an object');
    }
    if (typeof chat.id !== 'string' || !chat.id.trim()) {
      throw new Error('Corrupted chat storage record: missing or invalid chat id');
    }

    const id = chat.id;
    const name = typeof chat.name === 'string' && chat.name.trim() ? chat.name : 'New conversation';
    const isLocal = chat.source === 'local' || Boolean(chat.path);
    const source = isLocal ? 'local' : 'snapshot';
    const path = isLocal && typeof chat.path === 'string' ? chat.path : '';
    const draft = typeof chat.draft === 'string' ? chat.draft.slice(0, 10000) : '';
    const skipped = sanitizeSkipped(chat.skipped);
    const files = isLocal ? [] : sanitizeFiles(chat.files);
    const messages = sanitizeMessages(chat.messages);

    restoredChats.push({
      id,
      name,
      source,
      path,
      needsReconnect: isLocal,
      files,
      skipped,
      messages,
      contextStart: contextStart({ ...chat, messages }),
      lastContext: safeContext(chat.lastContext),
      draft,
      attachments: preparedAttachments(chat.attachments),
      error: '',
      pending: false,
      approval: null,
      applied: [],
      commandResults: [],
    });
  }

  const active = typeof data.active === 'string' && restoredChats.some(c => c.id === data.active)
    ? data.active
    : (restoredChats[0]?.id || null);

  return {
    chats: restoredChats,
    active,
  };
}

function openDatabase() {
  return new Promise((resolve, reject) => {
    const idb = globalThis.indexedDB;
    if (!idb || typeof idb.open !== 'function') {
      reject(new Error('IndexedDB is not supported in this environment.'));
      return;
    }
    try {
      const req = idb.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = (event) => {
        const db = event.target.result;
        if (!db.objectStoreNames.contains(STORE_NAME)) {
          db.createObjectStore(STORE_NAME);
        }
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error || new Error('Failed to open IndexedDB database.'));
      req.onblocked = () => reject(new Error('IndexedDB open blocked by another connection.'));
    } catch (err) {
      reject(err);
    }
  });
}

export async function saveChats(chats, active = null) {
  const data = pack(chats, active);
  const db = await openDatabase();
  try {
    await new Promise((resolve, reject) => {
      let settled = false;
      const fail = (err) => {
        if (!settled) {
          settled = true;
          reject(err);
        }
      };

      const tx = db.transaction(STORE_NAME, 'readwrite');
      const store = tx.objectStore(STORE_NAME);

      tx.oncomplete = () => {
        if (!settled) {
          settled = true;
          resolve();
        }
      };
      tx.onerror = () => {
        fail(tx.error || new Error('IndexedDB transaction failed while saving chats.'));
      };
      tx.onabort = () => {
        fail(tx.error || new Error('IndexedDB transaction aborted while saving chats.'));
      };

      try {
        const req = store.put(data, STORAGE_KEY);
        req.onerror = () => {
          if (req.error) {
            fail(req.error);
          }
        };
      } catch (err) {
        fail(err);
      }
    });
  } finally {
    if (db && typeof db.close === 'function') {
      db.close();
    }
  }
}

export async function loadChats() {
  const db = await openDatabase();
  try {
    const record = await new Promise((resolve, reject) => {
      let settled = false;
      const fail = (err) => {
        if (!settled) {
          settled = true;
          reject(err);
        }
      };

      const tx = db.transaction(STORE_NAME, 'readonly');
      const store = tx.objectStore(STORE_NAME);
      let result = undefined;

      tx.oncomplete = () => {
        if (!settled) {
          settled = true;
          resolve(result);
        }
      };
      tx.onerror = () => {
        fail(tx.error || new Error('IndexedDB transaction failed while loading chats.'));
      };
      tx.onabort = () => {
        fail(tx.error || new Error('IndexedDB transaction aborted while loading chats.'));
      };

      try {
        const req = store.get(STORAGE_KEY);
        req.onsuccess = () => {
          result = req.result;
        };
        req.onerror = () => {
          if (req.error) {
            fail(req.error);
          }
        };
      } catch (err) {
        fail(err);
      }
    });

    if (record === undefined || record === null) {
      return null;
    }
    return unpack(record);
  } finally {
    if (db && typeof db.close === 'function') {
      db.close();
    }
  }
}

export async function clearChats() {
  const db = await openDatabase();
  try {
    await new Promise((resolve, reject) => {
      let settled = false;
      const fail = (err) => {
        if (!settled) {
          settled = true;
          reject(err);
        }
      };

      const tx = db.transaction(STORE_NAME, 'readwrite');
      const store = tx.objectStore(STORE_NAME);

      tx.oncomplete = () => {
        if (!settled) {
          settled = true;
          resolve();
        }
      };
      tx.onerror = () => {
        fail(tx.error || new Error('IndexedDB transaction failed while clearing chats.'));
      };
      tx.onabort = () => {
        fail(tx.error || new Error('IndexedDB transaction aborted while clearing chats.'));
      };

      try {
        const req = store.delete(STORAGE_KEY);
        req.onerror = () => {
          if (req.error) {
            fail(req.error);
          }
        };
      } catch (err) {
        fail(err);
      }
    });
  } finally {
    if (db && typeof db.close === 'function') {
      db.close();
    }
  }
}
