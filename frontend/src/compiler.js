export const SOURCE_LIMIT = 50_000;
export const STDIN_LIMIT = 20_000;
export const BROWSER_LANGUAGES = [{ id: 'javascript', name: 'JavaScript (browser engine)' }, { id: 'python', name: 'Python (Pyodide)' }];

export function validateCompilerSettings(settings) {
  if (settings?.compiler_mode === 'browser') return '';
  if (settings?.compiler_mode !== 'judge0') return 'Choose browser or Judge0 execution in Connection settings.';
  try {
    const url = new URL(settings.compiler_base_url);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) throw new Error();
  } catch { return 'Add a Judge0 HTTP(S) base URL without credentials, query parameters, or a fragment.'; }
  if (!settings.compiler_api_key?.trim()) return 'Add your Judge0 API key in Connection settings.';
  if (settings.compiler_rapidapi_host && !/^[a-z\d.-]+(?::\d+)?$/i.test(settings.compiler_rapidapi_host)) return 'Use a RapidAPI hostname only (no URL or path).';
  return '';
}

export function scheduleCompilerLoad(settings, publish, delay = 400) {
  const controller = new AbortController();
  let timer;
  if (settings?.compiler_mode === 'judge0' && !validateCompilerSettings(settings)) {
    timer = setTimeout(async () => {
      try {
        const languages = parseLanguages(await compilerRequest('languages', undefined, controller.signal, settings));
        if (!controller.signal.aborted) publish({ languages, status: 'ready', error: '' });
      } catch (error) {
        if (!controller.signal.aborted) publish({ languages: [], status: 'error', error: error.message || 'Could not load Judge0 languages.' });
      }
    }, delay);
  }
  return () => { clearTimeout(timer); controller.abort(); };
}

export function defaultSource(name) {
  const examples = [
    [/python/i, 'print("Hello, world!")\n'],
    [/javascript|node\.js/i, 'console.log("Hello, world!");\n'],
    [/typescript/i, 'const greeting: string = "Hello, world!";\nconsole.log(greeting);\n'],
    [/c\+\+/i, '#include <iostream>\n\nint main() {\n    std::cout << "Hello, world!\\n";\n    return 0;\n}\n'],
    [/c#|c sharp/i, 'using System;\n\nclass Program {\n    static void Main() {\n        Console.WriteLine("Hello, world!");\n    }\n}\n'],
    [/^c(?:\s|\(|$)/i, '#include <stdio.h>\n\nint main(void) {\n    printf("Hello, world!\\n");\n    return 0;\n}\n'],
    [/java\b/i, 'public class Main {\n    public static void main(String[] args) {\n        System.out.println("Hello, world!");\n    }\n}\n'],
    [/^go\b|golang/i, 'package main\n\nimport "fmt"\n\nfunc main() {\n    fmt.Println("Hello, world!")\n}\n'],
    [/rust/i, 'fn main() {\n    println!("Hello, world!");\n}\n'],
    [/ruby/i, 'puts "Hello, world!"\n'],
    [/php/i, '<?php\necho "Hello, world!\\n";\n'],
    [/swift/i, 'print("Hello, world!")\n'],
    [/kotlin/i, 'fun main() {\n    println("Hello, world!")\n}\n'],
  ];
  return examples.find(([pattern]) => pattern.test(name))?.[1] ?? '';
}

export function validateRun(languageId, source, stdin, mode = 'judge0') {
  if (mode === 'browser' ? !BROWSER_LANGUAGES.some(item => item.id === languageId) : !Number.isInteger(languageId) || languageId < 1) return 'Choose an available language version.';
  if (!source.trim()) return 'Enter source code before running.';
  if (source.length > SOURCE_LIMIT) return `Source exceeds ${SOURCE_LIMIT.toLocaleString('en-US')} characters.`;
  if (stdin.length > STDIN_LIMIT) return `Standard input exceeds ${STDIN_LIMIT.toLocaleString('en-US')} characters.`;
  return '';
}

export function parseLanguages(data) {
  if (!Array.isArray(data?.languages) || data.languages.some(item => !Number.isInteger(item?.id) || item.id < 1 || typeof item.name !== 'string' || !item.name.trim()) || new Set(data.languages.map(item => item.id)).size !== data.languages.length) {
    throw new Error('The backend returned an invalid language catalog. Reload languages to retry.');
  }
  return data.languages;
}

export function parseResult(data) {
  if (!data || !Number.isInteger(data.status?.id) || typeof data.status.description !== 'string' ||
    ['stdout', 'stderr', 'compile_output', 'message'].some(key => typeof data[key] !== 'string') ||
    !(data.exit_code === null || Number.isInteger(data.exit_code)) ||
    !(data.time === null || typeof data.time === 'string') ||
    !(data.memory === null || (typeof data.memory === 'number' && Number.isFinite(data.memory))) || typeof data.truncated !== 'boolean') {
    throw new Error('The backend returned an invalid execution result. No successful run has been reported.');
  }
  return data;
}

export function statusTone(id) {
  return id === 3 ? 'success' : id === 1 || id === 2 ? 'pending' : 'failure';
}

export async function compilerRequest(path, body, signal, settings) {
  const invalid = validateCompilerSettings(settings);
  if (invalid) throw new Error(invalid);
  if (settings.compiler_mode !== 'judge0') throw new Error('Browser mode does not use an execution API.');
  const payload = { ...body, settings: { base_url: settings.compiler_base_url, api_key: settings.compiler_api_key, rapidapi_host: settings.compiler_rapidapi_host || '' } };
  const response = await fetch(`/agent/compiler/${path}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload), signal,
  });
  let data;
  try { data = await response.json(); } catch { throw new Error(`The compiler backend returned an unreadable response (${response.status}). Check the backend connection.`); }
  if (!response.ok) throw new Error(typeof data?.detail === 'string' ? data.detail : `Compiler request failed (${response.status}). Check backend compiler configuration and try again.`);
  return data;
}
