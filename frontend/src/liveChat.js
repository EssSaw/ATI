export async function requestLiveChat(path, body, signal, idleMs = 45000) {
  const controller = new AbortController();
  let idle;
  const resetIdle = () => { clearTimeout(idle); idle = setTimeout(() => controller.abort(), idleMs); };
  const abort = () => controller.abort();
  signal?.addEventListener('abort', abort, { once: true });
  resetIdle();
  try {
    const response = await fetch(path, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body), signal: controller.signal,
    });
    if (!response.ok) throw new Error(`Chat request failed (${response.status}).`);
    if (!response.body) throw new Error('Chat progress stream is unavailable. Your draft is kept.');
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let result;
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      resetIdle();
      buffer += decoder.decode(value, { stream: true });
      if (buffer.length > 20 * 1024 * 1024) throw new Error('Chat response exceeded the safety limit. Your draft is kept.');
      let end;
      while ((end = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, end);
        buffer = buffer.slice(end + 1);
        if (!line) continue;
        let event;
        try { event = JSON.parse(line); } catch { throw new Error('Invalid chat progress response. Your draft is kept.'); }
        if (event.type === 'error') throw new Error(typeof event.detail === 'string' ? event.detail : 'Chat failed. Your draft is kept.');
        if (event.type === 'result') result = event.data;
        else if (event.type !== 'heartbeat') throw new Error('Invalid chat progress response. Your draft is kept.');
      }
    }
    if (buffer.trim() || !result || typeof result !== 'object') throw new Error('Chat ended without a response. Your draft is kept.');
    return result;
  } catch (error) {
    if (controller.signal.aborted && !signal?.aborted) throw new Error('Chat stopped after the backend was silent. Your draft is kept; try again.');
    throw error;
  } finally {
    clearTimeout(idle);
    signal?.removeEventListener('abort', abort);
  }
}
