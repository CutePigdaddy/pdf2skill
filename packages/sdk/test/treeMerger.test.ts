import { describe, expect, it } from 'vitest';
import { TreeMerger, ChunkNode } from '../src/stages/treeMerger';
import type { LlmTransport } from '../src/stages/llmTransport';
import { mockOpfsRoot, type MockDirHandle } from './helpers/mockOpfs';
import { ensureDir, readText, listDir } from '../src/storage/opfs';

function mockLlm(handler: (prompt: string) => string): LlmTransport & { prompts: string[] } {
  const prompts: string[] = [];
  return {
    prompts,
    async chat(prompt: string) {
      prompts.push(prompt);
      return handler(prompt);
    },
    parseJson: (t: string) => JSON.parse(t),
  };
}

describe('findAnchorPosition', () => {
  const merger = new TreeMerger(mockLlm(() => ''));

  it('finds exact matches (code-point indexed)', () => {
    const content = 'aaaaChapter One starts herebbbb';
    expect(merger.findAnchorPosition(content, 'Chapter One starts here')).toBe(4);
  });

  it('trims the anchor before matching', () => {
    expect(merger.findAnchorPosition('xxhello worldyy', '  hello world  ')).toBe(2);
  });

  it('returns -1 for empty anchors', () => {
    expect(merger.findAnchorPosition('content', '   ')).toBe(-1);
  });

  it('fuzzy matches anchors with small edits within threshold', () => {
    // threshold for a 30-char anchor = max(2, 6) = 6
    const target = 'The beginning of section five is here';
    const anchor = 'The beginxing of section five is here'; // 2 edits
    expect(merger.findAnchorPosition(target, anchor)).toBe(0);
  });

  it('rejects anchors beyond the distance threshold', () => {
    const target = 'The beginning of section five is here';
    const anchor = 'xxxxxxxx of section five is here'; // 9 edits > 6
    expect(merger.findAnchorPosition(target, anchor)).toBe(-1);
  });

  it('skips fuzzy matching for content over 50k chars', () => {
    const content = 'a'.repeat(50001);
    expect(merger.findAnchorPosition(content, 'zzzzzzzzzzzzzzzzzzzzzzzzzzzzzz')).toBe(-1);
  });

  it('restricts the fuzzy window to first/last 20% for content over 20k chars', () => {
    const head = 'anchor lives at the very start!!';
    const marker = 'XQ7_MARKER_IN_MIDDLE';
    const middle = 'm'.repeat(10000) + marker + 'm'.repeat(10000);
    const tail = 'tail anchor at the very end here!';
    const content = head + middle + tail; // 20084 chars > 20000
    // The marker sits around position 10031 — deep middle, outside both
    // 20% windows. A near-match (1 edit, threshold 4) must not be found.
    const midAnchor = 'XZ7_MARKER_IN_MIDDLE';
    expect(merger.findAnchorPosition(content, midAnchor)).toBe(-1);
    // ...while the head is still findable exactly
    expect(merger.findAnchorPosition(content, 'anchor lives at the very start')).toBe(0);
  });

  it('treats astral characters as single code points', () => {
    const content = '😀😀😀target string here';
    expect(merger.findAnchorPosition(content, 'target string here')).toBe(3);
  });
});

describe('recursivePeel', () => {
  it('returns small chunks untouched', async () => {
    const merger = new TreeMerger(mockLlm(() => { throw new Error('should not call LLM'); }));
    const node = new ChunkNode('chunk_0001', 'Small', [], 'tiny content', 1, 2, 1);
    const result = await merger.recursivePeel(node);
    expect(result).toEqual([node]);
  });

  it('returns atomic chunks untouched', async () => {
    const merger = new TreeMerger(mockLlm(() => { throw new Error('should not call LLM'); }));
    const node = new ChunkNode('chunk_0001', 'TOC', [], 'x'.repeat(30000), 1, 2, 1, true);
    const result = await merger.recursivePeel(node);
    expect(result).toEqual([node]);
  });

  it('splits at found anchors and merges tiny trailing chunks', async () => {
    const secA = '# Section A\n\n' + 'alpha content '.repeat(200); // ~3k chars
    const secB = '# Section B\n\n' + 'beta content '.repeat(200);
    const secC = '# Section C\n\n' + 'gamma content '.repeat(200);
    const content = secA + secB + secC;

    const anchorB = '# Section B\n\nbeta content beta c'; // 30 chars
    const anchorC = '# Section C\n\ngamma content gamma ';
    const llm = mockLlm(() =>
      JSON.stringify({
        anchors: [
          { anchor: anchorB, description: 'Section B', suggested_title: 'Section B' },
          { anchor: anchorC, description: 'Section C', suggested_title: 'Section C' },
        ],
        is_atomic: false,
      }),
    );
    // Disable auto-merge interference; lower token limit so the 7.8k-char
    // content actually gets peeled.
    const merger = new TreeMerger(llm, { chunkMinThreshold: 1, chunkMaxTokens: 1000 });
    const node = new ChunkNode('chunk_0001', 'Big Chapter', [], content, 1, 100, 1);
    const leaves = await merger.recursivePeel(node);

    expect(node.children.length).toBe(3);
    expect(node.children[0].title).toBe('Section A');
    expect(node.children[1].title).toBe('Section B');
    expect(node.children[2].title).toBe('Section C');
    // children are still too big? ~3k chars -> 1500 tokens < 10000 -> leaves
    expect(leaves.map((l) => l.id)).toEqual(node.children.map((c) => c.id));
    expect(node.children[1].parentPath).toEqual(['Big Chapter']);
    expect(merger.peelErrors).toBe(0);
  });

  it('merges tiny chunks into the next sibling', async () => {
    const part1 = 'A'.repeat(3000);
    const part2 = 'B'.repeat(3000);
    const part3 = 'C'.repeat(100); // tiny -> merged into previous or next
    const content = part1 + part2 + part3;
    const anchor2 = 'B'.repeat(30);
    const llm = mockLlm(() =>
      JSON.stringify({
        anchors: [
          { anchor: anchor2, description: '', suggested_title: 'Part B' },
          { anchor: 'C'.repeat(30), description: '', suggested_title: 'Part C' },
        ],
        is_atomic: false,
      }),
    );
    const merger = new TreeMerger(llm, { chunkMinThreshold: 1000, chunkMaxTokens: 2000 });
    const node = new ChunkNode('chunk_0001', 'Chapter', [], content, 1, 100, 1);
    await merger.recursivePeel(node);
    // Three segments: [0,3000) 'Part B', [3000,6000) 'Part C', [6000,6100)
    // 'Chapter (Part 3)'. The last (100 chars < 1000) has no next sibling
    // and merges into the previous one.
    expect(node.children.length).toBe(2);
    expect(node.children[1].content.endsWith(part3)).toBe(true);
    expect(node.children[1].title).toBe('Part C & Chapter (Part 3)');
  });

  it('counts peel errors on unparseable responses and keeps the chunk', async () => {
    const llm = mockLlm(() => 'This is not JSON at all {{{');
    const merger = new TreeMerger(llm);
    const node = new ChunkNode('chunk_0001', 'Big', [], 'x'.repeat(30000), 1, 100, 1);
    const result = await merger.recursivePeel(node);
    expect(result).toEqual([node]);
    expect(node.children.length).toBe(0);
    expect(merger.peelErrors).toBe(1);
  });

  it('propagates LLM_PARSE from unparseable responses but swallows LLM_TIMEOUT', async () => {
    // swallowed: timeout
    const timeoutLlm = mockLlm(() => { throw Object.assign(new Error('timeout'), { code: 'LLM_TIMEOUT' }); });
    const m1 = new TreeMerger(timeoutLlm);
    const n1 = new ChunkNode('c', 't', [], 'x'.repeat(30000), 1, 2, 1);
    expect(await m1.recursivePeel(n1)).toEqual([n1]);
    expect(m1.peelErrors).toBe(1);

    // swallowed: LLM_PARSE (JSON decode / response format errors)
    const parseLlm = mockLlm(() => { throw Object.assign(new Error('bad format'), { code: 'LLM_PARSE' }); });
    const m2 = new TreeMerger(parseLlm);
    const n2 = new ChunkNode('c', 't', [], 'x'.repeat(30000), 1, 2, 1);
    expect(await m2.recursivePeel(n2)).toEqual([n2]);
    expect(m2.peelErrors).toBe(1);

    // propagated: auth error (Python ValueError)
    const authLlm = mockLlm(() => { throw Object.assign(new Error('401'), { code: 'LLM_AUTH' }); });
    const m3 = new TreeMerger(authLlm);
    const n3 = new ChunkNode('c', 't', [], 'x'.repeat(30000), 1, 2, 1);
    await expect(m3.recursivePeel(n3)).rejects.toMatchObject({ code: 'LLM_AUTH' });
  });

  it('honours the AI atomic decision', async () => {
    const llm = mockLlm(() => JSON.stringify({ anchors: [], is_atomic: true }));
    const merger = new TreeMerger(llm);
    const node = new ChunkNode('chunk_0001', 'Exercises', [], 'q'.repeat(30000), 1, 100, 1);
    const result = await merger.recursivePeel(node);
    expect(result).toEqual([node]);
    expect(node.isAtomic).toBe(true);
  });

  it('refines titles from headers when LLM title is generic', async () => {
    const h1 = '# First Header\n\n' + 'a'.repeat(3000);
    const h2 = '# Second Header\n\n' + 'b'.repeat(3000);
    const content = h1 + h2;
    const llm = mockLlm(() =>
      JSON.stringify({
        anchors: [{ anchor: '# Second Header\n\n' + 'b'.repeat(15), description: '', suggested_title: 'Chapter (Part 2)' }],
        is_atomic: false,
      }),
    );
    const merger = new TreeMerger(llm, { chunkMinThreshold: 1, chunkMaxTokens: 1500 });
    const node = new ChunkNode('chunk_0001', 'Chapter', [], content, 1, 100, 1);
    await merger.recursivePeel(node);
    expect(node.children[0].title).toBe('First Header');
    // second child: generic "Part" title + 1 header -> header title
    expect(node.children[1].title).toBe('Second Header');
  });

  it('stops at max iterations', async () => {
    let calls = 0;
    const llm = mockLlm(() => {
      calls += 1;
      return JSON.stringify({
        anchors: [{ anchor: 'y'.repeat(30), description: '', suggested_title: 'mid' }],
        is_atomic: false,
      });
    });
    const merger = new TreeMerger(llm, { chunkMaxIterations: 2, chunkMinThreshold: 1 });
    const node = new ChunkNode('chunk_0001', 'Chapter', [], 'x'.repeat(30000) + 'y'.repeat(30), 1, 100, 1);
    const leaves = await merger.recursivePeel(node);
    // First peel splits into two; the 30k-char half would need iteration 3,
    // which exceeds maxIterations=2, so it is kept as-is with no extra LLM call.
    expect(calls).toBe(1);
    expect(node.children.length).toBe(2);
    expect(leaves).toHaveLength(2);
    const big = leaves.find((l) => l.content.length === 30000)!;
    expect(big.iteration).toBe(2);
    expect(big.children.length).toBe(0);
  });
});

describe('buildAndMerge and persistence', () => {
  it('builds a master root, detects TOC, and assigns sequential ids', async () => {
    const toc = '# 目录\n1. Chapter One ... 1\n2. Chapter Two ... 5';
    const ch1 = '# Chapter One\n' + 'one content '.repeat(300);
    const ch2 = '# Chapter Two\n' + 'two content '.repeat(300);
    const llm = mockLlm(() => JSON.stringify({ anchors: [], is_atomic: false }));
    const merger = new TreeMerger(llm);
    const root = await merger.buildAndMerge([
      { content: toc, start_line: 1, end_line: 3 },
      { content: ch1, start_line: 4, end_line: 310 },
      { content: ch2, start_line: 311, end_line: 700 },
    ]);
    expect(root.id).toBe('master');
    expect(root.title).toBe('Document');
    expect(root.children.map((c) => c.id)).toEqual(['chunk_0001', 'chunk_0002', 'chunk_0003']);
    expect(root.children[0].title).toBe('目录');
    expect(root.children[0].isAtomic).toBe(true); // auto-detected TOC
    expect(root.children[1].title).toBe('Chapter One');
    expect(root.children[1].startLine).toBe(4);
  });

  it('writes tree.json, chunks/*.md and chunks_index.json with exact frontmatter', async () => {
    const llm = mockLlm(() => JSON.stringify({ anchors: [], is_atomic: false }));
    const merger = new TreeMerger(llm);
    const content = '# Chapter\n\n' + 'some content '.repeat(100);
    const root = await merger.buildAndMerge([{ content, start_line: 1, end_line: 210 }]);

    const jobRoot = mockOpfsRoot() as unknown as MockDirHandle;
    const outDir = await ensureDir(jobRoot as never, 'full_chunks');
    await merger.saveResults(outDir, root);

    const treeJson = JSON.parse(await readText(outDir, 'tree.json'));
    expect(treeJson.id).toBe('master');
    expect(treeJson.children[0].line_range).toEqual([1, 210]);

    const index = JSON.parse(await readText(outDir, 'chunks_index.json'));
    expect(index).toHaveLength(1);
    expect(index[0].file).toBe('chunks/chunk_0001.md');
    expect(index[0].parent_path).toEqual([]);
    expect(index[0].tokens).toBe(merger.estimateTokens(content));

    const chunksDir = await (outDir as unknown as MockDirHandle).getDirectoryHandle('chunks');
    const names = await listDir(chunksDir);
    expect(names.map((e) => e.name)).toEqual(['chunk_0001.md']);

    const md = await readText(chunksDir, 'chunk_0001.md');
    const expected = `---
id: chunk_0001
title: Chapter
parent_path: Root
start_line: 1
end_line: 210
iteration: 0
tokens: ~${merger.estimateTokens(content)}
---

${content}`;
    expect(md).toBe(expected);
  });

  it('saveOriginalChunks writes chunk_NNNN.md with iteration 1', async () => {
    const llm = mockLlm(() => '""');
    const merger = new TreeMerger(llm);
    const jobRoot = mockOpfsRoot() as unknown as MockDirHandle;
    const outDir = await ensureDir(jobRoot as never, 'full_chunks_original');
    await merger.saveOriginalChunks(outDir, [
      { content: '# A\ncontent', start_line: 1, end_line: 2 },
      { content: 'plain', start_line: 3, end_line: 3 },
    ]);

    const chunksDir = await (outDir as unknown as MockDirHandle).getDirectoryHandle('chunks');
    const a = await readText(chunksDir, 'chunk_0001.md');
    expect(a).toBe(`---
id: chunk_0001
title: Original Chunk 1
parent_path: Root
start_line: 1
end_line: 2
iteration: 1
tokens: ~5
---

# A
content`);
    const index = JSON.parse(await readText(outDir, 'chunks_index.json'));
    expect(index.map((e: { title: string }) => e.title)).toEqual([
      'Original Chunk 1',
      'Original Chunk 2',
    ]);
  });

  it('only leaf nodes become flat chunk files', async () => {
    // Parent with children -> parent excluded from flat chunks
    const llm = mockLlm(() =>
      JSON.stringify({
        anchors: [{ anchor: '# B\n\n' + 'b'.repeat(25), description: '', suggested_title: 'B' }],
        is_atomic: false,
      }),
    );
    const content = '# A\n\n' + 'a'.repeat(3000) + '# B\n\n' + 'b'.repeat(3000);
    const merger = new TreeMerger(llm, { chunkMinThreshold: 1, chunkMaxTokens: 1500 });
    const root = await merger.buildAndMerge([{ content, start_line: 1, end_line: 100 }]);

    const jobRoot = mockOpfsRoot() as unknown as MockDirHandle;
    const outDir = await ensureDir(jobRoot as never, 'full_chunks');
    await merger.saveResults(outDir, root);

    const index = JSON.parse(await readText(outDir, 'chunks_index.json'));
    // chunk_0001 is the parent (has children) -> only leaves chunk_0002/0003
    expect(index.map((e: { id: string }) => e.id)).toEqual(['chunk_0002', 'chunk_0003']);
    expect(index.map((e: { parent_path: string[] }) => e.parent_path)).toEqual([
      ['A'],
      ['A'],
    ]);
  });
});

describe('ChunkNode serialization', () => {
  it('round-trips through to_dict/from_dict', () => {
    const parent = new ChunkNode('chunk_0001', 'Chapter', [], 'content', 10, 20, 1, false);
    parent.children.push(new ChunkNode('chunk_0002', 'Sub', ['Chapter'], 'sub', 10, 20, 2, true));
    const dict = parent.toDict();
    expect(dict).toEqual({
      id: 'chunk_0001',
      title: 'Chapter',
      parent_path: [],
      children: [
        {
          id: 'chunk_0002',
          title: 'Sub',
          parent_path: ['Chapter'],
          children: [],
          content: 'sub',
          line_range: [10, 20],
          iteration: 2,
          is_atomic: true,
        },
      ],
      content: 'content',
      line_range: [10, 20],
      iteration: 1,
      is_atomic: false,
    });
    const restored = ChunkNode.fromDict(dict);
    expect(restored.children[0].title).toBe('Sub');
    expect(restored.children[0].isAtomic).toBe(true);
    expect(restored.toDict()).toEqual(dict);
  });
});
