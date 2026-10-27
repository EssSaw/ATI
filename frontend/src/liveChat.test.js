import test from 'node:test';
import assert from 'node:assert/strict';
import { requestLiveChat } from './liveChat.js';

const originalFetch = globalThis.fetch;
const stream = chunks => new ReadableStream({
  async start(controller) {
    for (const [delay, chunk] of chunks) {
      await new Promise(resolve => setTimeout(resolve, delay));
      controller.enqueue(new TextEncoder().encode(chunk));
    }
    controller.close();
  },
});

test('chat stays alive past its idle threshold while backend heartbeats continue', async () => {
  try {
    globalThis.fetch = async () => new Response(stream([
      [0, '{"type":"heartbeat"}\n'],
      [15, '{"type":"heart'],
      [15, 'beat"}\n'],
      [15, '{"type":"heartbeat"}\n'],
      [15, '{"type":"result","data":{"reply":"Done"}}\n'],
    ]));
    assert.deepEqual(await requestLiveChat('/agent/project-chat/live', { messages: [] }, undefined, 35), { reply: 'Done' });
  } finally { globalThis.fetch = originalFetch; }
});

test('silent backend times out, but error frame retains server explanation', async () => {
  try {
    globalThis.fetch = async (_, options) => new Response(new ReadableStream({ start(controller) { controller.enqueue(new TextEncoder().encode('{"type":"heartbeat"}\n')); options.signal.addEventListener('abort', () => controller.error(new DOMException('Aborted', 'AbortError')), { once: true }); } }));
    await assert.rejects(requestLiveChat('/agent/project-chat/live', {}, undefined, 15), /backend was silent/);
    globalThis.fetch = async () => new Response(stream([[0, '{"type":"error","detail":"Provider timed out"}\n']]));
    await assert.rejects(requestLiveChat('/agent/project-chat/live', {}, undefined, 50), /Provider timed out/);
  } finally { globalThis.fetch = originalFetch; }
});
