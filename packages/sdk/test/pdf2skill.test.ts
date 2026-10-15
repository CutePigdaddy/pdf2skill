import { describe, it, expect } from 'vitest';
import { Pdf2Skill } from '../src/pdf2skill';
import { Pdf2SkillError } from '../src/errors';
import type { LlmTransport } from '../src/stages/llmTransport';
import { mockOpfsRoot } from './helpers/mockOpfs';
import { CheckpointManager } from '../src/storage/checkpoint';
import { ensureDir, readText } from '../src/storage/opfs';

const DOC = `# Book Title

## Chapter 1

Intro text for chapter one with some words.

## Chapter 2

More text here for the second chapter.

## Chapter 3

Final chapter content.
`;

interface MockLlmHooks {
  /** Throw an AbortError on the Nth tagging call (1-based). */
  abortTagCall?: number;
  tagsByChunk?: string[];
}

function makeLlm(hooks: MockLlmHooks = {}): LlmTransport & { tagCalls: string[] } {
  const tagCalls: string[] = [];
  const llm: LlmTransport & { tagCalls: string[] } = {
    tagCalls,
    async chat(prompt: string): Promise<string> {
      if (prompt.includes('document structure analyzer')) {
        return JSON.stringify({ chapter_splits: [4, 7], toc_range: [1, 2], atomic_ranges: {} });
      }
      if (prompt.includes('curriculum designer')) {
        return 'A solid overview.\n[METADATA_DESC: Guide description]';
      }
      if (prompt.includes('kebab-case')) {
        return 'facade-test-book';
      }
      // Tagging: prompt embeds the chunk content preview.
      tagCalls.push(prompt.slice(0, 60));
      if (hooks.abortTagCall && tagCalls.length === hooks.abortTagCall) {
        const err = new Error('Aborted');
        err.name = 'AbortError';
        throw err;
      }
      return `tag-${tagCalls.length}`;
    },
    parseJson(text: string): Record<string, unknown> {
      return JSON.parse(text);
    },
  };
  return llm;
}

function makeSdk(
  hooks: MockLlmHooks = {},
): {
  root: FileSystemDirectoryHandle;
  sdk: Pdf2Skill;
  llm: LlmTransport & { tagCalls: string[] };
} {
  const root = mockOpfsRoot();
  const llm = makeLlm(hooks);
  const sdk = new Pdf2Skill({ storageRoot: root, createLlm: () => llm });
  return { root, sdk, llm };
}

const file = () => new File([DOC], 'facade book.md', { type: 'text/markdown' });

describe('Pdf2Skill facade', () => {
  it('run → done event → jobs/output APIs', async () => {
    const { sdk } = makeSdk();
    const doneResults: unknown[] = [];
    const stageEvents: string[] = [];
    sdk.on('done', (e) => doneResults.push(e));
    sdk.on('stage', (e) => stageEvents.push(`${e.stage}:${e.status}`));

    const result = await sdk.run({ kind: 'markdown', file: file(), name: 'facade book.md' });
    expect(result.jobId).toBe('facade_book');
    expect(result.skillName).toBe('facade-test-book');
    expect(result.referenceCount).toBe(3);
    expect(doneResults.length).toBe(1);
    expect(stageEvents).toContain('skill:done');

    // listJobs
    const jobs = await sdk.listJobs();
    expect(jobs.length).toBe(1);
    expect(jobs[0].jobId).toBe('facade_book');
    expect(jobs[0].inputName).toBe('facade book.md');
    expect(jobs[0].completedStages).toContain('skill_generation');

    // listOutputTree
    const tree = await sdk.listOutputTree('facade_book');
    expect(tree.type).toBe('dir');
    const names = (tree.children ?? []).map((c) => c.name);
    expect(names).toContain('generated_skills');
    expect(names).toContain('full_chunks');
    expect(names).toContain('full_chunks_original');
    expect(names).toContain('.checkpoint.json');

    // readOutputFile
    const skill = await sdk.readOutputFile('facade_book', 'generated_skills/SKILL.md');
    expect(skill.type).toBe('text');
    expect((skill as { content: string }).content).toContain('name: facade-test-book');
    const ref = await sdk.readOutputFile('facade_book', 'generated_skills/references/chunk_0001.md');
    expect(ref.type).toBe('text');

    await expect(sdk.readOutputFile('facade_book', 'no/such/file.md')).rejects.toMatchObject({
      code: 'FILE_NOT_FOUND',
    });
  });

  it('re-running the same file continues the same job from checkpoint', async () => {
    const { root, sdk } = makeSdk();
    const a = await sdk.run({ kind: 'markdown', file: file(), name: 'facade book.md' });
    expect(a.jobId).toBe('facade_book');

    // Second run of the same file: same job, no new LLM calls (all stages
    // checkpointed).
    const llm2 = makeLlm();
    const sdk2 = new Pdf2Skill({ storageRoot: root, createLlm: () => llm2 });
    const b = await sdk2.run({ kind: 'markdown', file: file(), name: 'facade book.md' });
    expect(b.jobId).toBe('facade_book');
    expect(llm2.tagCalls.length).toBe(0);
    expect(b.referenceCount).toBe(3);

    const jobs = await sdk.listJobs();
    expect(jobs.map((j) => j.jobId)).toEqual(['facade_book']);

    // A different filename creates a separate job.
    const c = await sdk.run({
      kind: 'markdown',
      file: new File([DOC], 'other book.md'),
      name: 'other book.md',
    });
    expect(c.jobId).toBe('other_book');
    await sdk.deleteJob('facade_book');
    expect((await sdk.listJobs()).map((j) => j.jobId)).toEqual(['other_book']);
    await expect(sdk.listOutputTree('facade_book')).rejects.toMatchObject({ code: 'JOB_NOT_FOUND' });
  });

  it('exportZip mirrors the Python outputs layout (no input file / job meta)', async () => {
    const { sdk } = makeSdk();
    await sdk.run({ kind: 'markdown', file: file(), name: 'facade book.md' });

    const blob = await sdk.exportZip('facade_book');
    expect(blob.size).toBeGreaterThan(0);

    const JSZip = (await import('jszip')).default;
    const zip = await JSZip.loadAsync(blob);
    const paths = Object.keys(zip.files).sort();
    expect(paths).toContain('.checkpoint.json');
    expect(paths).toContain('generated_skills/SKILL.md');
    expect(paths).toContain('generated_skills/references/chunk_0001.md');
    expect(paths).toContain('full_chunks/tree.json');
    expect(paths).toContain('full_chunks_original/chunks_index.json');
    expect(paths).not.toContain('job.json');
    expect(paths).not.toContain('input.md');

    const skillMd = await zip.files['generated_skills/SKILL.md'].async('string');
    expect(skillMd).toContain('name: facade-test-book');
  });

  it('abort mid-Stage-4 keeps summaries; resume completes without redoing them', async () => {
    const { root, sdk, llm } = makeSdk({ abortTagCall: 2 });
    let errored: Pdf2SkillError | null = null;
    sdk.on('error', (e) => (errored = e.error));

    // Abort during the 2nd chunk's tagging.
    await expect(
      sdk.run({ kind: 'markdown', file: file(), name: 'resume book.md' }),
    ).rejects.toMatchObject({ code: 'ABORTED' });
    expect(errored).toMatchObject({ code: 'ABORTED' });
    expect(llm.tagCalls.length).toBe(2);

    // Fine-grained checkpoint: chunk 1 summarised, chunk 2/3 missing.
    const jobs = await sdk.listJobs();
    const jobRoot = await ensureDir(await ensureDir(root, 'jobs'), jobs[0].jobId);
    const skillCp = new CheckpointManager(await ensureDir(jobRoot, 'generated_skills'));
    await skillCp.load();
    const summaries = skillCp.getStageData('summaries') as Record<string, string>;
    expect(Object.keys(summaries)).toEqual(['chunk_0001']);
    expect(skillCp.isStageCompleted('book_overview')).toBe(true);

    // Resume with a healthy LLM: only chunks 2 and 3 get tagged.
    const sdk2 = new Pdf2Skill({ storageRoot: root, createLlm: () => makeLlm() });
    const resumed = await sdk2.resume(jobs[0].jobId);
    expect(resumed.skillName).toBe('facade-test-book');
    expect(resumed.referenceCount).toBe(3);

    const skillMd = await readText(await ensureDir(jobRoot, 'generated_skills'), 'SKILL.md');
    expect(skillMd).toContain('(references/chunk_0001.md): tag-1');
    expect(skillMd).toContain('(references/chunk_0002.md): ');
    expect(skillMd).toContain('(references/chunk_0003.md): ');
  });

  it('resume rejects when Stage 1 is not checkpointed', async () => {
    const { sdk } = makeSdk();
    await expect(sdk.resume('missing-job')).rejects.toMatchObject({ code: 'JOB_NOT_FOUND' });
  });
});

describe('Pdf2Skill config semantics', () => {
  it('getConfig returns a copy — mutations do not leak into the SDK', () => {
    const { sdk } = makeSdk();
    const cfg = sdk.getConfig();
    cfg.providers.evil = { base_url: 'https://evil.example.com', api_key_env: 'X' };
    cfg.pdf.page_limit = 99999;

    const fresh = sdk.getConfig();
    expect(fresh.providers.evil).toBeUndefined();
    expect(fresh.pdf.page_limit).not.toBe(99999);
  });

  it('configure replaces the provider registry so deletions stick', () => {
    const { sdk } = makeSdk();
    sdk.configure({
      providers: {
        a: { base_url: 'https://a.example.com', api_key_env: 'A_KEY' },
        b: { base_url: 'https://b.example.com', api_key_env: 'B_KEY' },
      },
    });
    expect(Object.keys(sdk.getConfig().providers).sort()).toEqual(['a', 'b']);

    sdk.configure({ providers: { b: sdk.getConfig().providers.b } });
    expect(Object.keys(sdk.getConfig().providers)).toEqual(['b']);
  });
});
