import test from 'node:test';
import assert from 'node:assert/strict';
import {
  pack,
  unpack,
  loadChats,
  saveChats,
  clearChats,
  DB_NAME,
  DB_VERSION,
  STORE_NAME,
  STORAGE_KEY,
  MAX_CHATS,
  MAX_MESSAGES,
  MAX_STORAGE_CHARS,
} from './chatStorage.js';

function createFakeIndexedDB() {
  const stores = new Map();
  let failOpen = false;
  let failTx = false;
  let failPut = null;

  return {
    _stores: stores,
    setFailOpen(fail) { failOpen = fail; },
    setFailTx(fail) { failTx = fail; },
    setFailPut(err) { failPut = err; },
    open(name, version) {
      const req = {
        result: null,
        error: null,
        onupgradeneeded: null,
        onsuccess: null,
        onerror: null,
        onblocked: null,
      };

      queueMicrotask(() => {
        if (failOpen) {
          req.error = new Error('Simulated IDB open failure');
          if (req.onerror) req.onerror({ target: req });
          return;
        }

        const db = {
          name,
          version,
          objectStoreNames: {
            contains: (s) => stores.has(s),
          },
          createObjectStore(s) {
            if (!stores.has(s)) stores.set(s, new Map());
            return {};
          },
          close() {},
          transaction(storeNames) {
            const storeName = Array.isArray(storeNames) ? storeNames[0] : storeNames;
            if (!stores.has(storeName)) stores.set(storeName, new Map());
            const txStore = stores.get(storeName);

            const tx = {
              error: null,
              oncomplete: null,
              onerror: null,
              onabort: null,
              objectStore() {
                return {
                  put(val, key) {
                    const putReq = { result: key, error: null, onsuccess: null, onerror: null };
                    queueMicrotask(() => {
                      if (failPut) {
                        putReq.error = failPut;
                        tx.error = failPut;
                        if (putReq.onerror) putReq.onerror({ target: putReq });
                        if (tx.onerror) tx.onerror({ target: tx });
                      } else {
                        txStore.set(key, JSON.parse(JSON.stringify(val)));
                        if (putReq.onsuccess) putReq.onsuccess({ target: putReq });
                        queueMicrotask(() => {
                          if (failTx) {
                            tx.error = new Error('Simulated transaction commit failure');
                            if (tx.onerror) tx.onerror({ target: tx });
                          } else if (tx.oncomplete) {
                            tx.oncomplete({ target: tx });
                          }
                        });
                      }
                    });
                    return putReq;
                  },
                  get(key) {
                    const getReq = { result: undefined, error: null, onsuccess: null, onerror: null };
                    queueMicrotask(() => {
                      const val = txStore.get(key);
                      getReq.result = val !== undefined ? JSON.parse(JSON.stringify(val)) : undefined;
                      if (getReq.onsuccess) getReq.onsuccess({ target: getReq });
                      queueMicrotask(() => {
                        if (failTx) {
                          tx.error = new Error('Simulated transaction commit failure');
                          if (tx.onerror) tx.onerror({ target: tx });
                        } else if (tx.oncomplete) {
                          tx.oncomplete({ target: tx });
                        }
                      });
                    });
                    return getReq;
                  },
                  delete(key) {
                    const delReq = { result: undefined, error: null, onsuccess: null, onerror: null };
                    queueMicrotask(() => {
                      txStore.delete(key);
                      if (delReq.onsuccess) delReq.onsuccess({ target: delReq });
                      queueMicrotask(() => {
                        if (failTx) {
                          tx.error = new Error('Simulated transaction commit failure');
                          if (tx.onerror) tx.onerror({ target: tx });
                        } else if (tx.oncomplete) {
                          tx.oncomplete({ target: tx });
                        }
                      });
                    });
                    return delReq;
                  },
                };
              },
            };
            return tx;
          },
        };

        req.result = db;
        if (!stores.has(STORE_NAME) && req.onupgradeneeded) {
          req.onupgradeneeded({ target: req });
        }
        if (req.onsuccess) req.onsuccess({ target: req });
      });

      return req;
    },
  };
}

test('pack and unpack sanitize snapshot chats with explicit whitelist fields', () => {
  const proposal = {
    id: 'prop-1',
    summary: 'Fix bug',
    diff: '--- a/main.py\n+++ b/main.py\n@@ -1 +1 @@\n-x\n+y\n',
    changes: [{ path: 'main.py', operation: 'replace', content: 'y\n' }],
  };
  const trace = [{ tool: 'search_files', status: 'success', detail: 'found 1', duration_ms: 10 }];
  const subagent = { name: 'reviewer', model: 'gpt-4o', status: 'success', reply: 'LGTM', trace: [] };

  const inputChat = {
    id: 'chat-uuid-1',
    name: 'Browser Snapshot Chat',
    draft: 'Draft message text',
    files: [
      { path: 'src/app.py', content: 'print("hello")', selected: true },
      { path: 'src/secret.txt', content: 'hidden', selected: false },
    ],
    skipped: [{ path: '.git/HEAD', reason: 'unsafe path' }],
    messages: [
      { role: 'user', content: 'Help me test this' },
      {
        role: 'assistant',
        content: 'I analyzed your files.',
        trace,
        proposals: [proposal],
        subagents: [subagent],
        command_requests: [{ argv: ['pytest'], summary: 'Run tests' }],
        workspace_revision: 'rev-xyz-123',
      },
    ],
    // Forbidden state that should never be persisted
    approval: { kind: 'command', approval_id: 'app-99' },
    applied: ['1:0'],
    pending: true,
    error: 'Temporary backend error',
    busy: true,
    commandResults: ['exit code 0'],
    api_key: 'sk-secret-do-not-save',
    settings: { provider: 'openai', key: 'secret' },
  };

  const packed = pack([inputChat], inputChat.id);

  assert.equal(packed.version, 1);
  assert.equal(packed.active, 'chat-uuid-1');
  assert.equal(packed.chats.length, 1);

  const serializedStr = JSON.stringify(packed);
  assert.equal(serializedStr.includes('app-99'), false);
  assert.equal(serializedStr.includes('Temporary backend error'), false);
  assert.equal(serializedStr.includes('sk-secret-do-not-save'), false);
  assert.equal(serializedStr.includes('rev-xyz-123'), false);

  const restored = unpack(packed);
  assert.equal(restored.active, 'chat-uuid-1');
  assert.equal(restored.chats.length, 1);

  const chat = restored.chats[0];
  assert.equal(chat.id, 'chat-uuid-1');
  assert.equal(chat.name, 'Browser Snapshot Chat');
  assert.equal(chat.draft, 'Draft message text');
  assert.equal(chat.source, 'snapshot');
  assert.equal(chat.needsReconnect, false);
  assert.equal(chat.pending, false);
  assert.equal(chat.error, '');
  assert.equal(chat.approval, null);
  assert.deepEqual(chat.applied, []);
  assert.deepEqual(chat.commandResults, []);
  assert.equal(chat.workspace_id, undefined);
  assert.equal(chat.revision, undefined);

  assert.equal(chat.files.length, 2);
  assert.equal(chat.files[0].path, 'src/app.py');
  assert.equal(chat.files[0].content, 'print("hello")');
  assert.equal(chat.files[0].selected, true);
  assert.equal(chat.files[1].selected, false);

  assert.deepEqual(chat.skipped, [{ path: '.git/HEAD', reason: 'unsafe path' }]);

  assert.equal(chat.messages.length, 2);
  assert.deepEqual(chat.messages[0], { role: 'user', content: 'Help me test this' });

  const assistant = chat.messages[1];
  assert.equal(assistant.role, 'assistant');
  assert.equal(assistant.content, 'I analyzed your files.');
  assert.deepEqual(assistant.trace, trace);
  assert.deepEqual(assistant.proposals, [proposal]);
  assert.deepEqual(assistant.subagents, [subagent]);
  assert.deepEqual(assistant.command_requests, []);
  assert.equal(assistant.workspace_revision, null);
});

test('local mode chats do not store snapshot file contents and require reconnect upon restore', () => {
  const localChat = {
    id: 'local-chat-1',
    name: 'Local Repo Chat',
    source: 'local',
    path: '/Users/developer/my-project',
    workspace_id: 'ws-live-active',
    revision: 'rev-disk-100',
    files: [
      { path: 'giant_source.py', content: 'huge file content'.repeat(1000), selected: true },
    ],
    skipped: [],
    messages: [
      { role: 'user', content: 'Refactor main' },
    ],
    draft: 'next steps',
  };

  const packed = pack([localChat], localChat.id);
  const packedChat = packed.chats[0];

  assert.equal(packedChat.source, 'local');
  assert.equal(packedChat.path, '/Users/developer/my-project');
  assert.deepEqual(packedChat.files, []);
  assert.equal(JSON.stringify(packed).includes('huge file content'), false);
  assert.equal(JSON.stringify(packed).includes('ws-live-active'), false);

  const restored = unpack(packed);
  const restoredChat = restored.chats[0];

  assert.equal(restoredChat.id, 'local-chat-1');
  assert.equal(restoredChat.source, 'local');
  assert.equal(restoredChat.path, '/Users/developer/my-project');
  assert.equal(restoredChat.needsReconnect, true);
  assert.deepEqual(restoredChat.files, []);
  assert.equal(restoredChat.workspace_id, undefined);
  assert.equal(restoredChat.revision, undefined);
});

test('bounds chats to 100, messages to 500, and rejects exceeding 20MB budget', () => {
  const manyChats = Array.from({ length: 120 }, (_, i) => ({
    id: `c-${i}`,
    name: `Chat ${i}`,
    messages: Array.from({ length: 501 }, (_, j) => ({
      role: j % 2 === 0 ? 'user' : 'assistant',
      content: `Message ${j}`,
    })),
    files: [],
    skipped: [],
  }));

  assert.throws(() => pack(manyChats), /Saved chat limit/);
  assert.throws(() => pack([manyChats[0]]), /message limit/);
  const packed = pack(manyChats.slice(0, MAX_CHATS).map(c => ({ ...c, messages: c.messages.slice(0, MAX_MESSAGES) })), 'c-10');
  assert.equal(packed.chats.length, MAX_CHATS);
  assert.equal(packed.chats[0].messages.length, MAX_MESSAGES);
  assert.equal(packed.active, 'c-10');

  const hugeChat = {
    id: 'huge',
    name: 'Huge',
    files: [{ path: 'large.txt', content: 'x'.repeat(MAX_STORAGE_CHARS + 500), selected: true }],
    messages: [],
  };

  assert.throws(
    () => pack([hugeChat]),
    (err) => err.name === 'QuotaExceededError' && err.message.includes('Storage quota exceeded')
  );
});

test('unpack rejects corrupted records, invalid versions, and malformed entries', () => {
  assert.throws(() => unpack(null), /expected an object/);
  assert.throws(() => unpack('not-json'), /invalid JSON/);
  assert.throws(() => unpack([]), /expected an object/);
  assert.throws(() => unpack({ version: 99, chats: [] }), /Unsupported chat storage version: 99/);
  assert.throws(() => unpack({ version: 1, chats: 'invalid' }), /chats must be an array/);
  assert.throws(() => unpack({ version: 1, chats: [null] }), /chat entry must be an object/);
  assert.throws(() => unpack({ version: 1, chats: [{ name: 'missing id' }] }), /missing or invalid chat id/);

  // Missing active gracefully defaults to first chat id or null
  const emptyUnpacked = unpack({ version: 1, chats: [] });
  assert.equal(emptyUnpacked.active, null);

  const fallbackActive = unpack({
    version: 1,
    active: 'non-existent-id',
    chats: [{ id: 'valid-chat', name: 'Valid' }],
  });
  assert.equal(fallbackActive.active, 'valid-chat');
});

test('IndexedDB operations loadChats, saveChats, clearChats round-trip cleanly', async () => {
  const originalIDB = globalThis.indexedDB;
  const fake = createFakeIndexedDB();
  globalThis.indexedDB = fake;

  try {
    const emptyLoad = await loadChats();
    assert.equal(emptyLoad, null);

    const testChats = [
      {
        id: 'roundtrip-1',
        name: 'First roundtrip',
        draft: 'my draft',
        files: [{ path: 'a.js', content: 'const a = 1;', selected: true }],
        skipped: [],
        messages: [{ role: 'user', content: 'test msg' }],
      },
    ];

    await saveChats(testChats, 'roundtrip-1');

    const loaded = await loadChats();
    assert.ok(loaded);
    assert.equal(loaded.active, 'roundtrip-1');
    assert.equal(loaded.chats.length, 1);
    assert.equal(loaded.chats[0].id, 'roundtrip-1');
    assert.equal(loaded.chats[0].name, 'First roundtrip');
    assert.equal(loaded.chats[0].draft, 'my draft');
    assert.equal(loaded.chats[0].files[0].content, 'const a = 1;');

    await clearChats();
    const afterClear = await loadChats();
    assert.equal(afterClear, null);
  } finally {
    globalThis.indexedDB = originalIDB;
  }
});

test('IndexedDB errors propagate without silent eviction or silent failure', async () => {
  const originalIDB = globalThis.indexedDB;
  const fake = createFakeIndexedDB();
  globalThis.indexedDB = fake;

  try {
    fake.setFailPut(new Error('QuotaExceededError'));
    await assert.rejects(
      () => saveChats([{ id: 'c1', name: 'Chat 1', files: [], skipped: [], messages: [] }], 'c1'),
      /QuotaExceededError/
    );
    fake.setFailPut(null);

    fake.setFailTx(true);
    await assert.rejects(
      () => saveChats([{ id: 'c1', name: 'Chat 1', files: [], skipped: [], messages: [] }], 'c1'),
      /Simulated transaction commit failure/
    );
    fake.setFailTx(false);

    fake.setFailOpen(true);
    await assert.rejects(() => loadChats(), /Simulated IDB open failure/);
    fake.setFailOpen(false);

    // Corrupted record in DB causes loadChats to reject with readable error
    fake._stores.set(STORE_NAME, new Map([[STORAGE_KEY, { version: 2, chats: [] }]]));
    await assert.rejects(() => loadChats(), /Unsupported chat storage version: 2/);
    // Data remains in store (no destructive deletion)
    assert.ok(fake._stores.get(STORE_NAME).has(STORAGE_KEY));
  } finally {
    globalThis.indexedDB = originalIDB;
  }
});

test('environment without IndexedDB rejects operations with readable error', async () => {
  const originalIDB = globalThis.indexedDB;
  delete globalThis.indexedDB;

  try {
    await assert.rejects(() => loadChats(), /IndexedDB is not supported/);
    await assert.rejects(() => saveChats([], null), /IndexedDB is not supported/);
    await assert.rejects(() => clearChats(), /IndexedDB is not supported/);
  } finally {
    globalThis.indexedDB = originalIDB;
  }
});

test('local chat strips live tokens and forbidden fields while preserving local path', () => {
  const liveLocalChat = {
    id: 'live-local-1',
    name: 'Live Local Project',
    path: '/Users/developer/app',
    workspace_id: 'ws-secret-live-token-123',
    revision: 'rev-disk-hash-456',
    approval: { kind: 'files', revision: 'rev-disk-hash-456', key: '0:0' },
    applied: ['0:0'],
    pending: true,
    error: 'Temporary error state',
    busy: true,
    commandResults: ['npm test pass'],
    api_key: 'sk-test-key-789',
    files: [{ path: 'src/huge.ts', content: 'export const x = 1;', selected: true }],
    skipped: [{ path: '.git', reason: 'private directory' }],
    messages: [
      { role: 'user', content: 'Review changes' },
      {
        role: 'assistant',
        content: '',
        trace: [{ tool: 'list_files', status: 'success', detail: 'listed 10', duration_ms: 5 }],
        command_requests: [{ argv: ['echo', 'hi'], summary: 'echo' }],
        workspace_revision: 'rev-disk-hash-456',
      },
    ],
  };

  const packed = pack([liveLocalChat], 'live-local-1');
  const serialized = JSON.stringify(packed);

  assert.equal(serialized.includes('ws-secret-live-token-123'), false);
  assert.equal(serialized.includes('rev-disk-hash-456'), false);
  assert.equal(serialized.includes('sk-test-key-789'), false);
  assert.equal(serialized.includes('npm test pass'), false);
  assert.equal(serialized.includes('export const x = 1;'), false);

  const restored = unpack(packed);
  const chat = restored.chats[0];

  assert.equal(chat.id, 'live-local-1');
  assert.equal(chat.source, 'local');
  assert.equal(chat.path, '/Users/developer/app');
  assert.equal(chat.needsReconnect, true);
  assert.deepEqual(chat.files, []);
  assert.equal(chat.workspace_id, undefined);
  assert.equal(chat.revision, undefined);
  assert.equal(chat.approval, null);
  assert.deepEqual(chat.applied, []);
  assert.deepEqual(chat.commandResults, []);
  assert.equal(chat.pending, false);
  assert.equal(chat.error, '');

  const assistantMsg = chat.messages[1];
  assert.equal(assistantMsg.role, 'assistant');
  assert.deepEqual(assistantMsg.command_requests, []);
  assert.equal(assistantMsg.workspace_revision, null);
  assert.equal(assistantMsg.trace.length, 1);
});

test('pack validates input and throws TypeError if chats is not an array', () => {
  assert.throws(() => pack(null), TypeError);
  assert.throws(() => pack(undefined), TypeError);
  assert.throws(() => pack('not-an-array'), TypeError);
  assert.throws(() => pack({}), TypeError);
});
