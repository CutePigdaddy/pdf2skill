import { describe, it, expect } from 'vitest';
import { runPipeline } from '../src/pipeline';
import type { PipelineResult } from '../src/pipeline';
import { defaultConfig, mergeConfig } from '../src/config';
import type { LlmTransport } from '../src/stages/llmTransport';
import { mockOpfsRoot } from './helpers/mockOpfs';
import { CheckpointManager } from '../src/storage/checkpoint';
import { ensureDir, fileExists, readText, getDir } from '../src/storage/opfs';

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

describe('runPipeline (markdown input)', () => {
  it('runs all four stages and produces the expected layout', async () => {
    const config = mergeConfig(defaultConfig(), {});
    const { jobRoot, input } = mdInput();
    const llm = makeLlm();
    const events: { stage: string; status: string }[] = [];
    const logs: string[] = [];

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
            logs.push((payload as { message: string }).message);
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
});
