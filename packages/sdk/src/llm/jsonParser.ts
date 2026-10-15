import { Pdf2SkillError } from '../errors';

/**
 * Port of utils/llm_client.py::parse_json_response.
 * Python falls back to ast.literal_eval, which accepts single quotes and
 * bare True/False/None; we approximate with a state-machine repair pass.
 */
export function parseJsonResponse(text: string): Record<string, unknown> {
  // Compat with reasoning models that may include <think> tags
  const cleaned = text.replace(/<think>[\s\S]*?<\/think>/g, '');

  try {
    return JSON.parse(cleaned);
  } catch {
    const from = cleaned.indexOf('{');
    const to = cleaned.lastIndexOf('}');
    if (from !== -1 && to !== -1 && to > from) {
      const raw = cleaned.slice(from, to + 1);
      try {
        return JSON.parse(raw);
      } catch {
        /* fall through to loose repair */
      }
      try {
        return JSON.parse(repairPythonLiteral(raw));
      } catch (e) {
        throw new Pdf2SkillError(
          'LLM_PARSE',
          `Failed to parse JSON from LLM response: ${e instanceof Error ? e.message : e}`,
          `响应前 200 字符: ${cleaned.slice(0, 200)}`,
        );
      }
    }
  }
  throw new Pdf2SkillError('LLM_PARSE', 'Invalid JSON format from LLM.', '');
}

/**
 * Convert Python-literal-flavored text to JSON: bare True/False/None become
 * true/false/null, single-quoted strings become double-quoted (with escaping).
 * Tracks in-string state so conversions never touch string contents.
 */
export function repairPythonLiteral(text: string): string {
  let out = '';
  let i = 0;
  const n = text.length;
  while (i < n) {
    const ch = text[i];
    if (ch === "'" || ch === '"') {
      // Copy the string token, normalizing quotes to double quotes
      const quote = ch;
      let s = '"';
      i++;
      while (i < n && text[i] !== quote) {
        const c = text[i];
        if (c === '\\' && i + 1 < n) {
          // Keep escape pairs; JSON-compatible ones pass through
          const next = text[i + 1];
          if (next === quote) {
            s += quote;
          } else {
            s += c + next;
          }
          i += 2;
          continue;
        }
        s += c === '"' ? '\\"' : c;
        i++;
      }
      i++; // closing quote
      out += s + '"';
      continue;
    }
    if (/[A-Za-z_]/.test(ch)) {
      let word = '';
      let j = i;
      while (j < n && /[A-Za-z0-9_]/.test(text[j])) {
        word += text[j];
        j++;
      }
      if (word === 'True') out += 'true';
      else if (word === 'False') out += 'false';
      else if (word === 'None') out += 'null';
      else out += word;
      i = j;
      continue;
    }
    out += ch;
    i++;
  }
  return out;
}
