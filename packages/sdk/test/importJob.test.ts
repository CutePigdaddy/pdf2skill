import { describe, expect, it } from 'vitest';
import JSZip from 'jszip';

import { Pdf2Skill } from '../src/pdf2skill';
import { runPipeline } from '../src/pipeline';
import { Emitter } from '../src/events';
import { defaultConfig, mergeConfig } from '../src/config';
import { getDir, readText, fileExists } from '../src/storage/opfs';
import { mockOpfsRoot } from './helpers/mockOpfs';
import type { LlmTransport } from '../src/stages/llmTransport';
import type { StageEvent } from '../src/types';

const CHECKPOINT = {
  version: 1,
  completed_stages: ['pdf_conversion', 'llm_chunking'],
  data: {
    pdf_conversion: { md_file: 'outputs\\book-a\\full.md' },
    llm_chunking: {
      base_chunks: [
        {
          start_line: 1,
          end_line: 5,
          content: '# Book\n\n## Chapter 1\n\nHello world.',
          is_atomic: false,
        },
      ],
    },
  },
};

async function makeBundle(extra: Record<string, string | Uint8Array> = {}): Promise<Uint8Array> {
  const zip = new JSZip();
  zip.file('.checkpoint.json', JSON.stringify(CHECKPOINT));
  zip.file('full.md', '# Book\n\n## Chapter 1\n\nHello world.\n');
  for (const [name, data] of Object.entries(extra)) zip.file(name, data);
  return zip.generateAsync({ type: 'uint8array' });
}

function makeLlm(): LlmTransport & { calls: string[] } {
  const calls: string[] = [];
  return {
    calls,
    async chat(prompt: string): Promise<string> {
      calls.push(prompt);
      if (prompt.includes('kebab-case')) return 'imported-book';
      if (prompt.includes('curriculum designer')) {
        return 'An imported overview.\n[METADATA_DESC: Imported]';
      }
      return JSON.stringify({ subsections: [], suggested_title: 'Sub' });
    },
    parseJson(text: string): Record<string, unknown> {
      return JSON.parse(text);
    },
  };
}

describe('importJobBundle', () => {
  it('writes the bundle, normalizes the checkpoint path and hides bulk files', async () => {
    const root = mockOpfsRoot();
    const sdk = new Pdf2Skill({ storageRoot: root });
    const bytes = await makeBundle({
      'images/a.png': new Uint8Array([1, 2, 3]),
      'splits/part01.pdf': new Uint8Array([9]),
      '../evil.txt': 'nope',
    });

    const summary = await sdk.importJob('book-a', bytes, { inputName: 'Book A' });

    expect(summary.jobId).toBe('book-a');
    expect(summary.completedStages).toEqual(['pdf_conversion', 'llm_chunking']);
    expect(summary.resumable).toBe(true);

    const jobRoot = (await getDir(root, 'jobs/book-a'))!;
    const checkpoint = JSON.parse(await readText(jobRoot, '.checkpoint.json'));
    expect(checkpoint.data.pdf_conversion.md_file).toBe('full.md');
    const meta = JSON.parse(await readText(jobRoot, 'job.json'));
    expect(meta.inputName).toBe('Book A');
    const images = (await getDir(jobRoot, 'images'))!;
    expect(await fileExists(images, 'a.png')).toBe(true);
    expect(await getDir(jobRoot, 'splits')).toBeNull();
  });

  it('refuses to overwrite and keeps the existing job intact', async () => {
    const root = mockOpfsRoot();
    const sdk = new Pdf2Skill({ storageRoot: root });
    const bytes = await makeBundle();

    await sdk.importJob('book-a', bytes);
    const jobRoot = (await getDir(root, 'jobs/book-a'))!;

    await expect(sdk.importJob('book-a', bytes)).rejects.toMatchObject({ code: 'JOB_EXISTS' });
    expect(await readText(jobRoot, 'full.md')).toContain('Hello world');
  });

  it('rejects bundles without a usable checkpoint', async () => {
    const zip = new JSZip();
    zip.file('full.md', '# Book\n');
    const bytes = await zip.generateAsync({ type: 'uint8array' });

    const sdk = new Pdf2Skill({ storageRoot: mockOpfsRoot() });
    await expect(sdk.importJob('book-a', bytes)).rejects.toMatchObject({
      code: 'IMPORT_INVALID',
    });
  });

  it('resumes the imported job without re-running chunking', async () => {
    const root = mockOpfsRoot();
    const llm = makeLlm();
    const sdk = new Pdf2Skill({ storageRoot: root, createLlm: () => llm });
    await sdk.importJob('book-a', await makeBundle());

    const jobRoot = (await getDir(root, 'jobs/book-a'))!;
    const emitter = new Emitter();
    const stages: StageEvent[] = [];
    emitter.on('stage', (e) => stages.push(e));

    await runPipeline({
      jobRoot,
      config: mergeConfig(defaultConfig(), {}),
      input: { kind: 'markdown', filename: 'book.md' },
      createLlm: () => llm,
      emitter,
    });

    const chunkingDone = stages.find((e) => e.stage === 'chunking' && e.status === 'done');
    expect(chunkingDone?.resumed).toBe(true);
    expect(llm.calls.some((p) => p.includes('document structure analyzer'))).toBe(false);
  });
});
