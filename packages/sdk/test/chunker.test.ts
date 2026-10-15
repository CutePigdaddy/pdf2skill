import { describe, expect, it } from 'vitest';
import { LlmChunker } from '../src/stages/llmChunker';
import type { LlmTransport } from '../src/stages/llmTransport';

function mockLlm(responses: string | string[] | ((prompt: string) => string)): LlmTransport {
  const queue = typeof responses === 'string' ? [responses] : Array.isArray(responses) ? [...responses] : null;
  return {
    async chat(prompt: string) {
      if (queue) {
        const next = queue.shift();
        if (next === undefined) throw new Error('mock exhausted');
        return next;
      }
      return (responses as (p: string) => string)(prompt);
    },
    parseJson(text: string) {
      return JSON.parse(text.replace(/^```json\n/, '').replace(/\n```$/, ''));
    },
  };
}

const DOC = [
  '# Book Title',
  'intro text',
  '# TOC',
  '1. Chapter One ....... 1',
  '2. Chapter Two ....... 20',
  '# Chapter One',
  'Content of chapter one.'.repeat(10),
  '# Chapter Two',
  'Content of chapter two.'.repeat(10),
].join('\n');

describe('LlmChunker.split', () => {
  it('extracts headers with 1-based line numbers', async () => {
    const chunker = new LlmChunker(mockLlm('{"chapter_splits": [1, 4, 6, 8]}'));
    const data = await chunker.split(DOC);
    expect(data.headers.map((h) => [h.level, h.text, h.lineNumber])).toEqual([
      [1, 'Book Title', 1],
      [1, 'TOC', 3],
      [1, 'Chapter One', 6],
      [1, 'Chapter Two', 8],
    ]);
  });

  it('injects document metrics into the prompt', async () => {
    let seen = '';
    const spy = {
      async chat(prompt: string) {
        seen = prompt;
        return '{"chapter_splits": []}';
      },
      parseJson: (t: string) => JSON.parse(t),
    } as unknown as LlmTransport;
    const chunker = new LlmChunker(
      spy,
      'chars={total_chars} lines={total_lines} tree=[{header_tree}] tokens={estimated_tokens}',
    );
    await chunker.split(DOC);
    expect(seen).toContain(`chars=${[...DOC].length}`);
    expect(seen).toContain('lines=9');
    expect(seen).toContain(`tokens=${Math.trunc([...DOC].length / 2)}`);
    expect(seen).toContain('[Line 3] # TOC');
  });

  it('parses the LLM plan and keeps metadata', async () => {
    const chunker = new LlmChunker(
      mockLlm('{"toc_range": [3, 5], "preface_range": [2, 2], "chapter_splits": [1, 6]}'),
    );
    const data = await chunker.split(DOC);
    expect(data.splits).toEqual([1, 6]);
    expect(data.tocRange).toEqual([3, 5]);
    expect(data.prefaceRange).toEqual([2, 2]);
    expect(data.fallback).toBe(false);
  });

  it('falls back to uniform splitting when both attempts fail', async () => {
    const failing: LlmTransport = {
      chat: async () => {
        throw new Error('LLM down');
      },
      parseJson: (t) => JSON.parse(t),
    };
    const chunker = new LlmChunker(failing);
    const lines = Array.from({ length: 5000 }, (_, i) => `line ${i}`).join('\n');
    const data = await chunker.split(lines);
    expect(data.fallback).toBe(true);
    // 5000 lines -> num_chunks = 2, step = 2500 -> [2500]
    expect(data.splits).toEqual([2500]);
  });

  it('retries once before falling back', async () => {
    let calls = 0;
    const flaky: LlmTransport = {
      async chat() {
        calls += 1;
        if (calls === 1) throw new Error('transient');
        return '{"chapter_splits": [42]}';
      },
      parseJson: (t) => JSON.parse(t),
    };
    const data = await new LlmChunker(flaky).split(DOC);
    expect(calls).toBe(2);
    expect(data.splits).toEqual([42]);
    expect(data.fallback).toBe(false);
  });

  it('single-chunk fallback for short documents', async () => {
    const failing: LlmTransport = {
      chat: async () => {
        throw new Error('down');
      },
      parseJson: (t) => JSON.parse(t),
    };
    const data = await new LlmChunker(failing).split('short doc');
    expect(data.fallback).toBe(true);
    expect(data.splits).toEqual([]);
  });
});

describe('LlmChunker.extractChunks', () => {
  it('creates chunks from valid split points and marks TOC atomic', () => {
    const chunker = new LlmChunker(mockLlm('{}'));
    const lines = DOC.split('\n');
    const data = {
      splits: [1, 4, 6],
      tocRange: [4, 5],
      prefaceRange: null,
      atomicRanges: {},
      headers: [],
      lines,
      fallback: false,
    };
    const chunks = chunker.extractChunks(data);
    expect(chunks.length).toBe(3);
    expect(chunks[0]).toMatchObject({ start_line: 1, end_line: 3, is_atomic: false });
    expect(chunks[1]).toMatchObject({ start_line: 4, end_line: 5, is_atomic: true });
    expect(chunks[1].content).toContain('Chapter One');
    expect(chunks[2]).toMatchObject({ start_line: 6, end_line: 9, is_atomic: false });
    expect(chunks[2].content).toContain('# Chapter One');
  });

  it('drops malformed and out-of-range split points', () => {
    const chunker = new LlmChunker(mockLlm('{}'));
    const data = {
      splits: ['abc', -5, 0, 3.7, 99999, '6', 2],
      tocRange: null,
      prefaceRange: null,
      atomicRanges: {},
      headers: [],
      lines: DOC.split('\n'),
      fallback: false,
    };
    const chunks = chunker.extractChunks(data);
    // valid: 1, 6 (string '6'), 2, plus truncated 3.7->3
    expect(chunks.map((c) => c.start_line)).toEqual([1, 2, 3, 6]);
  });

  it('skips empty chunks', () => {
    const chunker = new LlmChunker(mockLlm('{}'));
    const data = {
      splits: [1, 2, 3],
      tocRange: null,
      prefaceRange: null,
      atomicRanges: {},
      headers: [],
      lines: ['# Title', '', 'body'],
      fallback: false,
    };
    const chunks = chunker.extractChunks(data);
    // lines 2-2 is empty -> skipped; total=3, splits=[1,2,3,4]
    expect(chunks.map((c) => [c.start_line, c.end_line])).toEqual([
      [1, 1],
      [3, 3],
    ]);
  });

  it('ignores malformed toc_range', () => {
    const chunker = new LlmChunker(mockLlm('{}'));
    const data = {
      splits: [1],
      tocRange: ['x', 3],
      prefaceRange: null,
      atomicRanges: {},
      headers: [],
      lines: DOC.split('\n'),
      fallback: false,
    };
    const chunks = chunker.extractChunks(data);
    expect(chunks.every((c) => !c.is_atomic)).toBe(true);
  });
});
