export const DEFAULT_CONTEXT_WINDOW = 32768;
export const DEFAULT_RESPONSE_RESERVED = 4096;
export const CONTEXT_PARTS = ['system', 'tools', 'project', 'messages', 'attachments', 'tool_results'];

export function validateBudget(settings) {
  const window = settings.context_window ?? DEFAULT_CONTEXT_WINDOW;
  const reserve = settings.response_reserved ?? DEFAULT_RESPONSE_RESERVED;
  if (!Number.isInteger(window) || window < 8192 || window > 2000000) return 'Context window must be an integer from 8,192 to 2,000,000 tokens.';
  if (!Number.isInteger(reserve) || reserve < 512 || reserve > 32768 || reserve >= window) return 'Response reserve must be an integer from 512 to 32,768 tokens and smaller than the context window.';
  return '';
}
export function contextStart(chat) {
  return Number.isInteger(chat.contextStart) ? Math.max(0, Math.min(chat.contextStart, chat.messages.length)) : 0;
}
export function compactBoundary(messages) {
  const users = messages.flatMap((message, index) => message.role === 'user' ? [index] : []);
  return users.at(-4) ?? 0;
}
export function safeContext(context) {
  if (!context || typeof context !== 'object') return null;
  const numbers = ['window_tokens', 'reserved_tokens', 'estimated_tokens', 'usage_percent', 'dropped_messages'];
  if (!numbers.every(key => Number.isFinite(context[key]) && context[key] >= 0) || context.window_tokens < 8192 || context.window_tokens > 2000000 || context.reserved_tokens >= context.window_tokens || typeof context.compacted !== 'boolean' || context.estimate !== true) return null;
  if (!CONTEXT_PARTS.every(key => Number.isFinite(context.breakdown?.[key]) && context.breakdown[key] >= 0)) return null;
  return { ...Object.fromEntries(numbers.map(key => [key, context[key]])), breakdown: Object.fromEntries(CONTEXT_PARTS.map(key => [key, context.breakdown[key]])), estimate: true, compacted: context.compacted };
}
const tokens = text => text ? Math.ceil(new TextEncoder().encode(text).length / 3) : 0;
export function estimateContext(payload) {
  const breakdown = { system: 0, tools: 0, project: 0, messages: 0, attachments: 0, tool_results: 0 };
  for (const file of payload.files || []) breakdown.project += tokens(file.path + '\n' + file.content);
  for (const message of payload.messages || []) {
    breakdown.messages += tokens(message.content) + 4;
    for (const attachment of message.attachments || []) breakdown.attachments += attachment.kind === 'image' ? 1024 : tokens(attachment.text);
  }
  const estimated_tokens = Object.values(breakdown).reduce((sum, value) => sum + value, 0);
  const window_tokens = payload.context_window ?? DEFAULT_CONTEXT_WINDOW;
  const reserved_tokens = payload.response_reserved ?? DEFAULT_RESPONSE_RESERVED;
  return { window_tokens, reserved_tokens, estimated_tokens, usage_percent: estimated_tokens / window_tokens * 100, breakdown, dropped_messages: 0, estimate: true, compacted: false };
}
