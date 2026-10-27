export function applyModelLimits(settings, id, limits = {}) {
  const entry = limits[id];
  return entry ? { ...settings, router_model: id, context_window: entry.context_length, response_reserved: entry.max_completion_tokens } : { ...settings, router_model: id };
}

export function scheduleModelLoad(request, settings, publish, delay = 400) {
  const controller = new AbortController();
  const timer = setTimeout(async () => {
    try {
      const data = await request('/agent/models', {
        router_base_url: settings.router_base_url,
        router_api_key: settings.router_api_key,
      }, controller.signal);
      if (!Array.isArray(data.models) || data.models.some(id => typeof id !== 'string' || !id.trim())) {
        throw new Error('The backend returned an invalid model catalog.');
      }
      const models = [...new Set(data.models)];
      const limits = {};
      for (const id of models) {
        const entry = data.limits?.[id];
        if (Number.isSafeInteger(entry?.context_length) && entry.context_length >= 8192 && entry.context_length <= 2000000 &&
            Number.isSafeInteger(entry?.max_completion_tokens) && entry.max_completion_tokens >= 512 && entry.max_completion_tokens <= 200000 &&
            entry.max_completion_tokens < entry.context_length) {
          limits[id] = { context_length: entry.context_length, max_completion_tokens: entry.max_completion_tokens };
        }
      }
      if (!controller.signal.aborted) publish({ models, limits, status: 'ready', error: '' });
    } catch (error) {
      if (!controller.signal.aborted) publish({ models: [], limits: {}, status: 'error', error: error.message || 'Could not load models. Check your gateway connection.' });
    }
  }, delay);
  return () => { clearTimeout(timer); controller.abort(); };
}
