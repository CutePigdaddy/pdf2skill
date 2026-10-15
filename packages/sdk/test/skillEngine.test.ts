import { describe, it, expect } from 'vitest';
import { SkillEngine } from '../src/stages/skillEngine';
import { ChunkNode } from '../src/stages/treeMerger';
import type { LlmTransport } from '../src/stages/llmTransport';
import { mockOpfsRoot, type MockDirHandle } from './helpers/mockOpfs';
import { ensureDir, readText, fileExists, getDir, listDir } from '../src/storage/opfs';
import { CheckpointManager } from '../src/storage/checkpoint';

function makeTree(): ChunkNode {
  const root = new ChunkNode('master', 'master', []);
  const a = new ChunkNode('chunk_0001', 'Chapter 1', [], 'Content of chapter one.', 1, 10);
  const b = new ChunkNode('chunk_0002', 'Chapter 2', ['Part I'], 'Content of chapter two.', 11, 20);
  root.children.push(a, b);
  return root;
}

interface MockLlmOpts {
  overview?: string;
  tags?: string;
  name?: string;
  calls?: string[];
  /** Throw on the Nth tagging call (1-based). */
  failTagCall?: number;
}

function makeLlm(opts: MockLlmOpts = {}): LlmTransport {
  let tagCalls = 0;
  return {
    async chat(prompt: string): Promise<string> {
      opts.calls?.push(prompt.slice(0, 40));
      if (prompt.includes('curriculum designer')) {
        return opts.overview ?? 'A great overview.\n[METADATA_DESC: Book about testing]';
      }
      if (prompt.includes('kebab-case')) {
        return opts.name ?? 'test-book-skill';
      }
      // Tagging prompt (technical indexer)
      tagCalls++;
      if (opts.failTagCall && tagCalls === opts.failTagCall) {
        const err = new Error('Aborted');
        err.name = 'AbortError';
        throw err;
      }
      return opts.tags ?? 'topic-a | topic-b';
    },
    parseJson(text: string): Record<string, unknown> {
      return JSON.parse(text);
    },
  };
}

async function setup(): Promise<{ jobRoot: MockDirHandle; skillDir: FileSystemDirectoryHandle }> {
  const jobRoot = mockOpfsRoot() as unknown as MockDirHandle;
  const skillDir = await ensureDir(jobRoot as unknown as FileSystemDirectoryHandle, 'generated_skills');
  return { jobRoot, skillDir };
}

describe('SkillEngine', () => {
  it('generate writes SKILL.md, reference files and checkpoint', async () => {
    const { jobRoot, skillDir } = await setup();
    const engine = new SkillEngine(skillDir, makeLlm(), {
      jobRoot: jobRoot as unknown as FileSystemDirectoryHandle,
    });
    await engine.generate(makeTree(), 'My Test Book');

    const skillMd = await readText(skillDir, 'SKILL.md');
    expect(skillMd.startsWith('---\nname: test-book-skill\ndescription: ')).toBe(true);
    expect(skillMd).toContain('# My Test Book');
    expect(skillMd).toContain('## Overview');
    expect(skillMd).toContain('- [Chapter 1](references/chunk_0001.md): topic-a | topic-b');
    expect(skillMd).toContain('- [Part I > Chapter 2](references/chunk_0002.md): topic-a | topic-b');

    const refDir = await ensureDir(skillDir, 'references');
    const ref1 = await readText(refDir, 'chunk_0001.md');
    expect(ref1).toBe(
      `---
id: chunk_0001
title: Chapter 1
parent_path: Root
start_line: 1
end_line: 10
---

Content of chapter one.`,
    );
    const ref2 = await readText(refDir, 'chunk_0002.md');
    expect(ref2).toContain('parent_path: Part I');

    const cp = new CheckpointManager(skillDir);
    await cp.load();
    expect(cp.isStageCompleted('book_overview')).toBe(true);
    expect(cp.isStageCompleted('skill_name')).toBe(true);
    const summaries = cp.getStageData('summaries') as Record<string, string>;
    expect(Object.keys(summaries).sort()).toEqual(['chunk_0001', 'chunk_0002']);
  });

  it('appends a trigger phrase when the description lacks one', async () => {
    const { jobRoot, skillDir } = await setup();
    const engine = new SkillEngine(skillDir, makeLlm({ overview: 'Plain overview without trigger.\n[METADATA_DESC: Just facts]' }), {
      jobRoot: jobRoot as unknown as FileSystemDirectoryHandle,
    });
    await engine.generate(makeTree(), 'Trigger Book');
    const skillMd = await readText(skillDir, 'SKILL.md');
    expect(skillMd).toContain(`Use this skill when the user asks about topics covered in 'Trigger Book'.`);
  });

  it('removes angle brackets and truncates descriptions over 1000 chars', async () => {
    const { jobRoot, skillDir } = await setup();
    const longDesc = '使用此技能'.padEnd(1010, 'x');
    const engine = new SkillEngine(skillDir, makeLlm({ overview: `O\n[METADATA_DESC: <b>${longDesc}</b>]` }), {
      jobRoot: jobRoot as unknown as FileSystemDirectoryHandle,
    });
    await engine.generate(makeTree(), 'Big Book');
    const skillMd = await readText(skillDir, 'SKILL.md');
    const descLine = skillMd.split('\n')[2];
    expect(descLine.startsWith('description: ')).toBe(true);
    const desc = descLine.slice('description: '.length);
    expect(desc).not.toContain('<');
    expect(desc).not.toContain('>');
    expect(desc.endsWith('...')).toBe(true);
    // 997 chars + '...'
    expect([...desc].length).toBe(1000);
  });

  it('falls back to regex name when the LLM name is invalid', async () => {
    const { jobRoot, skillDir } = await setup();
    const engine = new SkillEngine(skillDir, makeLlm({ name: 'Not Valid!!' }), {
      jobRoot: jobRoot as unknown as FileSystemDirectoryHandle,
    });
    await engine.generate(makeTree(), 'My Book!');
    const skillMd = await readText(skillDir, 'SKILL.md');
    expect(skillMd).toContain('name: my-book');
  });

  it('falls back to a timestamp name for non-latin titles', async () => {
    const { jobRoot, skillDir } = await setup();
    const engine = new SkillEngine(skillDir, makeLlm({ name: 'Bad Name' }), {
      jobRoot: jobRoot as unknown as FileSystemDirectoryHandle,
    });
    await engine.generate(makeTree(), '高等数学');
    const skillMd = await readText(skillDir, 'SKILL.md');
    expect(skillMd).toMatch(/name: skill-\d+\n/);
  });

  it('skips already-summarised chunks on resume and reuses the stored name', async () => {
    const { jobRoot, skillDir } = await setup();
    // Pre-populate checkpoint: overview + summary for chunk_0001 + skill name.
    const cp = new CheckpointManager(skillDir);
    await cp.load();
    await cp.markStageCompleted('book_overview', {
      overview: 'Stored overview',
      metadata_desc: 'Stored desc',
    });
    await cp.markStageCompleted('skill_name', { name: 'stored-name' });
    cp.state.data.summaries = { chunk_0001: 'old-tag' };
    await cp.save();

    const calls: string[] = [];
    const engine = new SkillEngine(skillDir, makeLlm({ calls }), {
      jobRoot: jobRoot as unknown as FileSystemDirectoryHandle,
    });
    await engine.generate(makeTree(), 'Resume Book');

    // Only one LLM call: tagging chunk_0002 (no overview, no name calls).
    expect(calls.length).toBe(1);
    const skillMd = await readText(skillDir, 'SKILL.md');
    expect(skillMd).toContain('name: stored-name');
    expect(skillMd).toContain('(references/chunk_0001.md): old-tag');
    expect(skillMd).toContain('(references/chunk_0002.md): topic-a | topic-b');
  });

  it('retries chunks whose tagging failed (not stored in summaries)', async () => {
    const { jobRoot, skillDir } = await setup();
    // First run: tagging of chunk_0002 aborts.
    const failLlm = makeLlm({ failTagCall: 2 });
    const failing = new SkillEngine(skillDir, failLlm, {
      jobRoot: jobRoot as unknown as FileSystemDirectoryHandle,
    });
    await expect(failing.generate(makeTree(), 'Retry Book')).rejects.toMatchObject({
      name: 'AbortError',
    });
    const cp = new CheckpointManager(skillDir);
    await cp.load();
    expect(Object.keys(cp.getStageData('summaries') as Record<string, string>)).toEqual([
      'chunk_0001',
    ]);

    // Second run completes the missing chunk.
    const engine = new SkillEngine(skillDir, makeLlm(), {
      jobRoot: jobRoot as unknown as FileSystemDirectoryHandle,
    });
    await engine.generate(makeTree(), 'Retry Book');
    const skillMd = await readText(skillDir, 'SKILL.md');
    expect(skillMd).toContain('(references/chunk_0002.md): topic-a | topic-b');
  });

  it('copies images from jobRoot into references/images (idempotent)', async () => {
    const { jobRoot, skillDir } = await setup();
    const imgDir = await ensureDir(jobRoot as unknown as FileSystemDirectoryHandle, 'images');
    const { writeBytes } = await import('../src/storage/opfs');
    await writeBytes(imgDir, 'fig1.png', new Uint8Array([1, 2, 3]));

    const engine = new SkillEngine(skillDir, makeLlm(), {
      jobRoot: jobRoot as unknown as FileSystemDirectoryHandle,
    });
    await engine.generate(makeTree(), 'Img Book');

    const refDir = await ensureDir(skillDir, 'references');
    const copied = await getDir(refDir, 'images');
    expect(copied).toBeTruthy();
    expect(await fileExists(copied!, 'fig1.png')).toBe(true);

    // Second run does not fail or duplicate.
    await engine.generate(makeTree(), 'Img Book');
    const entries = await listDir(await ensureDir(refDir, 'images'));
    expect(entries.length).toBe(1);
  });
});

describe('SkillEngine.validateKebabName', () => {
  it('accepts valid kebab-case names', () => {
    expect(SkillEngine.validateKebabName('my-book')).toBe(true);
    expect(SkillEngine.validateKebabName('a')).toBe(true);
    expect(SkillEngine.validateKebabName('book-2nd-edition')).toBe(true);
  });
  it('rejects invalid names', () => {
    expect(SkillEngine.validateKebabName('')).toBe(false);
    expect(SkillEngine.validateKebabName('My-Book')).toBe(false);
    expect(SkillEngine.validateKebabName('-book')).toBe(false);
    expect(SkillEngine.validateKebabName('book-')).toBe(false);
    expect(SkillEngine.validateKebabName('book--two')).toBe(false);
    expect(SkillEngine.validateKebabName('a'.repeat(65))).toBe(false);
    expect(SkillEngine.validateKebabName('book_name')).toBe(false);
  });
});
