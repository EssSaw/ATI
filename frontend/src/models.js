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
      if (!controller.signal.aborted) publish({ models: [...new Set(data.models)], status: 'ready', error: '' });
    } catch (error) {
      if (!controller.signal.aborted) publish({ models: [], status: 'error', error: error.message || 'Could not load models. Check your gateway connection.' });
    }
  }, delay);
  return () => { clearTimeout(timer); controller.abort(); };
}
