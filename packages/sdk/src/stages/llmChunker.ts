import chunkStrategyPrompt from '../prompts/chunk_strategy.txt?raw';
import { charCount } from './levenshtein';
import type { LlmTransport } from './llmTransport';

export interface Header {
  level: number;
  text: string;
  lineNumber: number;
}

export interface SplitData {
  /** Raw LLM-provided split line numbers (unsanitized). */
  splits: unknown[];
  tocRange: unknown;
  prefaceRange: unknown;
  atomicRanges: Record<string, unknown>;
  headers: Header[];
  lines: string[];
  fallback: boolean;
}

export interface BaseChunk {
  start_line: number;
  end_line: number;
  content: string;
  is_atomic: boolean;
}

// [^\n] mirrors Python's `.` (which matches \r) so CRLF docs behave identically.
const HEADER_RE = /^(#{1,6})\s+([^\n]+)$/;

/**
 * Stage 2: strategic chunking. Port of core/llm_chunker.py::LLMChunker.
 * Analyses the document's header structure (TOC) and asks the LLM where to
 * split major sections to preserve conceptual integrity.
 */
export class LlmChunker {
  private readonly promptTemplate: string;

  constructor(private readonly llm: LlmTransport, promptTemplate?: string) {
    // Python's read_text() applies universal newlines (\r\n and \r -> \n).
    this.promptTemplate = (promptTemplate ?? chunkStrategyPrompt).replace(/\r\n|\r/g, '\n');
  }

  /** Extract headers and ask the LLM for a splitting plan. */
  async split(content: string): Promise<SplitData> {
    content = content.replace(/\r\n|\r/g, '\n');
    const lines = content.split('\n');

    const headers: Header[] = [];
    for (let i = 0; i < lines.length; i++) {
      const match = HEADER_RE.exec(lines[i]);
      if (match) {
        headers.push({ level: match[1].length, text: match[2].trim(), lineNumber: i + 1 });
      }
    }

    const treeText = this.buildTreeText(headers);
    const totalChars = charCount(content);
    const totalLines = lines.length;

    let prompt = this.promptTemplate
      .replaceAll('{total_chars}', String(totalChars))
      .replaceAll('{total_lines}', String(totalLines))
      .replaceAll('{header_tree}', treeText);
    if (prompt.includes('{estimated_tokens}')) {
      prompt = prompt.replaceAll('{estimated_tokens}', String(Math.trunc(totalChars / 2)));
    }

    let splitPlan: Record<string, unknown> | null = null;
    let usedFallback = false;
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        const response = await this.llm.chat(prompt, { isJson: true });
        splitPlan = this.llm.parseJson(response);
        break;
      } catch (e) {
        if ((e as Error).name === 'AbortError') throw e;
        console.warn(`LLM chunk strategy attempt ${attempt + 1} failed: ${e}`);
      }
    }
    if (splitPlan === null) {
      console.warn('All LLM attempts failed, falling back to uniform splitting');
      const total = lines.length;
      const numChunks = Math.max(1, Math.trunc(total / 2000));
      const step = Math.trunc(total / numChunks);
      const chapterSplits: number[] = [];
      for (let i = 1; i < numChunks; i++) chapterSplits.push(i * step);
      splitPlan = { chapter_splits: chapterSplits };
      usedFallback = true;
    }

    return {
      splits: (splitPlan.chapter_splits as unknown[]) ?? [],
      tocRange: splitPlan.toc_range,
      prefaceRange: splitPlan.preface_range,
      atomicRanges: (splitPlan.atomic_ranges as Record<string, unknown>) ?? {},
      headers,
      lines,
      fallback: usedFallback,
    };
  }

  private buildTreeText(headers: Header[]): string {
    const out: string[] = [];
    for (const h of headers) {
      if (h.level > 4) continue;
      out.push(`[Line ${h.lineNumber}] ${'#'.repeat(h.level)} ${h.text}`);
    }
    return out.join('\n');
  }

  /**
   * Create base chunks according to the LLM splitting plan. Marks the TOC
   * range as atomic (un-splittable). Malformed split points (strings,
   * negatives, out-of-range) are silently dropped, like the Python version.
   */
  extractChunks(splitData: SplitData): BaseChunk[] {
    const lines = splitData.lines;
    const total = lines.length;

    const validSplits: number[] = [];
    for (const s of splitData.splits ?? []) {
      const n = toInt(s);
      if (n !== null && n >= 1 && n <= total) validSplits.push(n);
    }
    const splits = Array.from(new Set([1, ...validSplits, total + 1])).sort((a, b) => a - b);

    let tocBounds: [number, number] | null = null;
    const tocRange = splitData.tocRange;
    if (Array.isArray(tocRange) && tocRange.length >= 2) {
      const start = toInt(tocRange[0]);
      const end = toInt(tocRange[1]);
      if (start !== null && end !== null) {
        tocBounds = [start, end];
      } else {
        console.warn(`Ignoring malformed toc_range from LLM: ${JSON.stringify(tocRange)}`);
      }
    }

    const chunks: BaseChunk[] = [];
    for (let i = 0; i < splits.length - 1; i++) {
      const start = splits[i];
      const end = splits[i + 1] - 1;
      const chunkContent = lines.slice(start - 1, end).join('\n');
      if (chunkContent.trim()) {
        let isAtomic = false;
        if (tocBounds && start >= tocBounds[0] && end <= tocBounds[1]) {
          isAtomic = true;
          console.log(`Marked chunk (lines ${start}-${end}) as ATOMIC (TOC range)`);
        }
        chunks.push({ start_line: start, end_line: end, content: chunkContent, is_atomic: isAtomic });
      }
    }
    return chunks;
  }
}

/** Python `int(s)` coercion: accepts ints, float-like numbers, numeric strings. */
function toInt(v: unknown): number | null {
  if (typeof v === 'number' && Number.isFinite(v)) return Math.trunc(v);
  if (typeof v === 'boolean') return v ? 1 : 0;
  if (typeof v === 'string') {
    const t = v.trim();
    if (/^[+-]?\d+$/.test(t)) return parseInt(t, 10);
    return null;
  }
  return null;
}
