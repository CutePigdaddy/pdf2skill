import { describe, it, expect } from 'vitest';
import { runPipeline } from '../src/pipeline';
import type { PipelineResult } from '../src/pipeline';
import { defaultConfig, mergeConfig } from '../src/config';
import type { LlmTransport } from '../src/stages/llmTransport';
import { mockOpfsRoot } from './helpers/mockOpfs';
import { CheckpointManager } from '../src/storage/checkpoint';
import { dirExists, ensureDir, fileExists, readText, getDir, listDir, writeText } from '../src/storage/opfs';

const DOC = `# Book Title

## Chapter 1

Intro text for chapter one with some words.

## Chapter 2

More text here for the second chapter.

## Chapter 3

Final chapter content.
`;

function makeLlm(opts: { failFirstChunkAttempt?: boolean } = {}): LlmTransport & { calls: string[] } {
  const calls: string[] = [];
  let chunkAttempt = 0;
  const llm: LlmTransport & { calls: string[] } = {
    calls,
    async chat(prompt: string): Promise<string> {
      calls.push(prompt.slice(0, 40));
      if (prompt.includes('document structure analyzer')) {
        chunkAttempt++;
        if (opts.failFirstChunkAttempt && chunkAttempt === 1) {
          return 'not json at all';
        }
        return JSON.stringify({
          chapter_splits: [4, 7],
          toc_range: [1, 2],
          atomic_ranges: {},
        });
      }
      if (prompt.includes('curriculum designer')) {
        return 'Nice overview.\n[METADATA_DESC: An illustrated guide]';
      }
      if (prompt.includes('kebab-case')) {
        return 'my-test-book';
      }
      // Chunk tagging / peeling fallback
      return JSON.stringify({ subsections: [], suggested_title: 'Sub' });
    },
    parseJson(text: string): Record<string, unknown> {
      return JSON.parse(text);
    },
  };
  return llm;
}

const mdInput = (name = 'test book.md') => ({
  jobRoot: mockOpfsRoot(),
  input: { kind: 'markdown' as const, bytes: new TextEncoder().encode(DOC), filename: name },
});

const chapterBlock = (name: string, word: string): string =>
  `## ${name}\n\n${(word + ' ').repeat(1200)}\n\n`;

const BIG_DOC =
  '# Book Title\n\n' +
  chapterBlock('Chapter 1', 'alpha') +
  chapterBlock('Chapter 2', 'beta') +
  chapterBlock('Chapter 3', 'gamma');

const bigInput = (name = 'big book.md') => ({
  jobRoot: mockOpfsRoot(),
  input: {
    kind: 'markdown' as const,
    bytes: new TextEncoder().encode(BIG_DOC),
    filename: name,
  },
});

function makeCacheLlm(calls: { peel: number }): LlmTransport {
  let tag = 0;
  return {
    async chat(prompt: string): Promise<string> {
      if (prompt.includes('document structure analyzer')) {
        return JSON.stringify({
          chapter_splits: [3, 7, 11],
          toc_range: [1, 2],
          atomic_ranges: {},
        });
      }
      if (prompt.includes('document chunking assistant')) {
        calls.peel++;
        return JSON.stringify({ anchors: [], is_atomic: false });
      }
      if (prompt.includes('curriculum designer')) {
        return 'A cache test overview.\n[METADATA_DESC: Cache test]';
      }
      if (prompt.includes('kebab-case')) {
        return 'cache-book';
      }
      tag++;
      return `tag-${tag}`;
    },
    parseJson(text: string): Record<string, unknown> {
      return JSON.parse(text);
    },
  };
}

describe('runPipeline (markdown input)', () => {
  it('runs all four stages and produces the expected layout', async () => {
    const config = mergeConfig(defaultConfig(), {});
    const { jobRoot, input } = mdInput();
    const llm = makeLlm();
    const events: { stage: string; status: string }[] = [];
    const logs: { message: string; kind?: string }[] = [];

    const result: PipelineResult = await runPipeline({
      jobRoot,
      config,
      input,
      createLlm: () => llm,
      emitter: {
        emit: (event: string, payload: unknown) => {
          if (event === 'stage') {
            const p = payload as { stage: string; status: string };
            events.push({ stage: p.stage, status: p.status });
          } else if (event === 'log') {
            const p = payload as { message: string; kind?: string };
            logs.push({ message: p.message, kind: p.kind });
          }
        },
      } as never,
    });

    expect(result.skillName).toBe('my-test-book');
    expect(result.skillMd).toContain('name: my-test-book');
    expect(result.referenceCount).toBeGreaterThan(0);

    // Stage 1: input.md written, checkpoint marked
    expect(await fileExists(jobRoot, 'input.md')).toBe(true);
    const cp = new CheckpointManager(jobRoot);
    await cp.load();
    expect(cp.isStageCompleted('pdf_conversion')).toBe(true);
    expect(cp.isStageCompleted('llm_chunking')).toBe(true);
    expect(cp.isStageCompleted('tree_merging')).toBe(true);

    // Stage 2/3 artifacts
    expect(await fileExists(await ensureDir(jobRoot, 'full_chunks_original'), 'chunks_index.json')).toBe(true);
    const fullChunks = await ensureDir(jobRoot, 'full_chunks');
    expect(await fileExists(fullChunks, 'tree.json')).toBe(true);
    expect(await fileExists(fullChunks, 'chunks_index.json')).toBe(true);
    const tree = JSON.parse(await readText(fullChunks, 'tree.json'));
    expect(tree.id).toBe('master');

    // Stage 4 artifacts
    const skillDir = await getDir(jobRoot, 'generated_skills');
    expect(skillDir).toBeTruthy();
    expect(await fileExists(skillDir!, 'SKILL.md')).toBe(true);

    // Events: each stage active then done
    for (const s of ['mineru', 'chunking', 'peeling', 'skill']) {
      expect(events.filter((e) => e.stage === s && e.status === 'active').length).toBeGreaterThan(0);
      expect(events.filter((e) => e.stage === s && e.status === 'done').length).toBe(1);
    }

    // Logs carry UI hints: stage headers are dividers, stage internals are
    // verbose detail hidden by default in the workbench.
    expect(logs.find((l) => l.message === 'Stage 3 · Peeling')?.kind).toBe('stage');
    expect(logs.some((l) => l.kind === 'detail')).toBe(true);
  });

  it('Stage 2 fallback does not write a checkpoint', async () => {
    const config = mergeConfig(defaultConfig(), {});
    const { jobRoot, input } = mdInput();
    // chat always succeeds with junk text; parseJson always fails so the
    // chunker falls back to uniform splitting.
    const llm: LlmTransport = {
      async chat() {
        return 'garbage response';
      },
      parseJson() {
        throw new Error('no json');
      },
    };

    // Stage 3 will also run with the broken LLM (peel errors, no checkpoint),
    // Stage 4 completes with tag errors skipped.
    const result = await runPipeline({
      jobRoot,
      config,
      input,
      createLlm: () => llm,
    });
    expect(result.skillMd).toContain('description:');

    const cp = new CheckpointManager(jobRoot);
    await cp.load();
    expect(cp.isStageCompleted('llm_chunking')).toBe(false);
    // Fallback produced a single uniform chunk
    expect((cp.getStageData('llm_chunking') as unknown)).toBeUndefined();
  });

  it('resumes from checkpoint without re-running completed stages', async () => {
    const config = mergeConfig(defaultConfig(), {});
    const { jobRoot, input } = mdInput();
    const llm1 = makeLlm();
    await runPipeline({ jobRoot, config, input, createLlm: () => llm1 });
    const llm1Calls = llm1.calls.length;

    // Second run: chunking + peeling should be restored from checkpoint —
    // only Stage 4 tagging calls happen (all summaries checkpointed, so
    // only the name call is skipped too; expect zero new LLM calls).
    const llm2 = makeLlm();
    const result = await runPipeline({
      jobRoot,
      config,
      input: { kind: 'markdown', filename: 'test book.md' },
      createLlm: () => llm2,
    });
    expect(llm2.calls.length).toBe(0);
    expect(llm1Calls).toBeGreaterThan(0);
    expect(result.skillName).toBe('my-test-book');
    // Stage 1 restored the same md file
    expect(await fileExists(jobRoot, 'input.md')).toBe(true);
  });

  it('restart clears checkpoint and full.md, then re-runs everything', async () => {
    const config = mergeConfig(defaultConfig(), {});
    const { jobRoot, input } = mdInput();
    await runPipeline({ jobRoot, config, input, createLlm: () => makeLlm() });

    const llm = makeLlm();
    await runPipeline({
      jobRoot,
      config,
      input,
      restart: true,
      createLlm: () => llm,
    });
    // --restart also clears the Stage-4 fine-grained checkpoint in
    // generated_skills/: chunk ids are deterministic (chunk_0001…) but chunk
    // content is not, so surviving summaries would silently re-pair old tags
    // with new content. Strategy, overview, per-chunk tagging and name calls
    // are all re-issued (1 + 1 + 3 + 1).
    expect(llm.calls.length).toBe(6);
    expect(llm.calls[0]).toContain('document structure analyzer');
    expect(llm.calls[1]).toContain('curriculum designer');
  });

  it('Stage 2 retry after a fallback rebuilds the stale tree checkpoint', async () => {
    const config = mergeConfig(defaultConfig(), {});
    const { jobRoot, input } = mdInput();
    const brokenChunker: LlmTransport = {
      async chat() {
        return 'garbage response';
      },
      parseJson() {
        throw new Error('no json');
      },
    };
    const peelingLlm = makeLlm();

    // Run 1: chunking falls back to one uniform chunk; Stage 3 succeeds on
    // that chunk and checkpoints its tree, then Stage 4 completes.
    await runPipeline({
      jobRoot,
      config,
      input,
      createLlm: (stage) => (stage === 'chunking' ? brokenChunker : peelingLlm),
    });
    const afterFallback = new CheckpointManager(jobRoot);
    await afterFallback.load();
    expect(afterFallback.isStageCompleted('llm_chunking')).toBe(false);
    expect(afterFallback.isStageCompleted('tree_merging')).toBe(true);

    // Run 2: chunking succeeds. The fallback-era tree checkpoint must not
    // survive, or Stage 3 would be skipped and the new chunks ignored.
    const llm2 = makeLlm();
    await runPipeline({
      jobRoot,
      config,
      input: { kind: 'markdown', filename: 'test book.md' },
      createLlm: () => llm2,
    });

    const cp = new CheckpointManager(jobRoot);
    await cp.load();
    expect(cp.isStageCompleted('llm_chunking')).toBe(true);
    const index = JSON.parse(
      await readText(await ensureDir(jobRoot, 'full_chunks'), 'chunks_index.json'),
    ) as unknown[];
    expect(index.length).toBe(3);
    // Stage 4's id-keyed summaries were also stale, so tagging re-runs.
    expect(
      llm2.calls.some(
        (c) => c.includes('technical indexer') || c.includes('curriculum designer'),
      ),
    ).toBe(true);
  });

  it('fromStage pdf_conversion clears cached Stage 1 artifacts', async () => {
    const config = mergeConfig(defaultConfig(), {});
    const { jobRoot, input } = mdInput();
    await runPipeline({ jobRoot, config, input, createLlm: () => makeLlm() });

    // Simulate a previous PDF conversion's cached outputs.
    await writeText(jobRoot, 'full.md', '# stale conversion');
    await ensureDir(jobRoot, 'splits');
    await writeText(jobRoot, 'book_mineru_output.zip', 'PK');
    await writeText(jobRoot, 'remote_mineru_output.zip', 'PK');

    await runPipeline({
      jobRoot,
      config,
      input: { kind: 'markdown', filename: 'test book.md' },
      fromStage: 'pdf_conversion',
      createLlm: () => makeLlm(),
    });

    expect(await fileExists(jobRoot, 'full.md')).toBe(false);
    expect(await dirExists(jobRoot, 'splits')).toBe(false);
    expect(await fileExists(jobRoot, 'book_mineru_output.zip')).toBe(false);
    expect(await fileExists(jobRoot, 'remote_mineru_output.zip')).toBe(false);
  });

  it('fromStage resets that stage and all later stages', async () => {
    const config = mergeConfig(defaultConfig(), {});
    const { jobRoot, input } = mdInput();
    await runPipeline({ jobRoot, config, input, createLlm: () => makeLlm() });

    await runPipeline({
      jobRoot,
      config,
      input: { kind: 'markdown', filename: 'test book.md' },
      fromStage: 'llm_chunking',
      createLlm: () => makeLlm(),
    });
    const cp = new CheckpointManager(jobRoot);
    await cp.load();
    // pdf_conversion kept, later stages re-written on completion
    expect(cp.isStageCompleted('pdf_conversion')).toBe(true);
    expect(cp.isStageCompleted('llm_chunking')).toBe(true);
  });

  it('fromStage llm_chunking reuses peeled subtrees from the cache', async () => {
    const config = mergeConfig(defaultConfig(), { pdf: { chunk_max_tokens: 1000 } });
    const { jobRoot, input } = bigInput();
    const first = { peel: 0 };
    await runPipeline({
      jobRoot,
      config,
      input,
      createLlm: () => makeCacheLlm(first),
    });
    expect(first.peel).toBe(3);
    const cacheDir = await getDir(jobRoot, 'peeling_cache');
    expect(cacheDir).toBeTruthy();
    expect((await listDir(cacheDir!)).filter((e) => e.kind === 'file')).toHaveLength(3);

    const second = { peel: 0 };
    await runPipeline({
      jobRoot,
      config,
      input: { kind: 'markdown', filename: 'big book.md' },
      fromStage: 'llm_chunking',
      createLlm: () => makeCacheLlm(second),
    });
    expect(second.peel).toBe(0);
    expect(
      (await listDir((await getDir(jobRoot, 'peeling_cache'))!)).filter(
        (e) => e.kind === 'file',
      ),
    ).toHaveLength(3);
  });

  it('restart clears the peeling cache', async () => {
    const config = mergeConfig(defaultConfig(), { pdf: { chunk_max_tokens: 1000 } });
    const { jobRoot, input } = bigInput();
    const first = { peel: 0 };
    await runPipeline({
      jobRoot,
      config,
      input,
      createLlm: () => makeCacheLlm(first),
    });
    expect(first.peel).toBe(3);

    const second = { peel: 0 };
    await runPipeline({
      jobRoot,
      config,
      input: { ...input, bytes: new TextEncoder().encode(BIG_DOC) },
      restart: true,
      createLlm: () => makeCacheLlm(second),
    });
    expect(second.peel).toBe(3);
  });
});
