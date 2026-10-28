import { validateBudget } from './context.js';
export const providers = {
  openai: { label: 'OpenAI', base_url: 'https://api.openai.com/v1', model: 'gpt-4o-mini' },
  groq: { label: 'Groq', base_url: 'https://api.groq.com/openai/v1', model: 'llama-3.3-70b-versatile' },
  xai: { label: 'xAI', base_url: 'https://api.x.ai/v1', model: 'grok-3-mini' },
  openrouter: { label: 'OpenRouter', base_url: 'https://openrouter.ai/api/v1', model: 'openai/gpt-4o-mini' },
  custom: { label: 'Custom compatible API', base_url: '', model: '' },
};
export const defaults = { context_window: 32768, response_reserved: 4096, mode: 'direct', provider: 'openai', api_key: '', base_url: providers.openai.base_url, model: providers.openai.model, router_base_url: 'http://localhost:20128/v1', router_api_key: '', router_model: '', compiler_mode: 'browser', compiler_base_url: '', compiler_api_key: '', compiler_rapidapi_host: '' };
export function preferences(settings) {
  const { mode, provider, base_url, model, router_base_url, router_model, compiler_mode, compiler_base_url, compiler_rapidapi_host, context_window, response_reserved } = settings;
  return { mode, provider, base_url, model, router_base_url, router_model, compiler_mode, compiler_base_url, compiler_rapidapi_host, context_window, response_reserved };
}
export function validateConnectionSettings(s) {
  const invalid = validateBudget(s);
  if (invalid) return invalid;
  const router = s.mode === '9router';
  try {
    const url = new URL(router ? s.router_base_url : s.base_url);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) throw new Error();
  } catch { return 'Use an HTTP(S) base URL without credentials, query parameters, or a fragment.'; }
  if (!router && s.provider !== 'custom' && !s.api_key.trim()) return 'Add your provider API key in connection settings.';
  if (!router && !s.model.trim()) return 'Add a model name in connection settings.';
  return '';
}
export function validateSettings(s) {
  return validateConnectionSettings(s) || (s.mode === '9router' && !s.router_model.trim() ? 'Select a 9router model before sending. Use Test connection & load models in connection settings.' : '');
}
export const example = `def average_score(scores):
    total = 0
    for score in scores:
        total += score
    return total / len(scores)


def passing_students(students):
    passed = []
    for name, scores in students.items():
        if average_score(scores) > 60:
            passed.append(name)
    return passed

students = {
    "Ada": [80, 90, 70],
    "Lin": [],
    "Sam": [60, 60, 60],
}
print(passing_students(students))`;
