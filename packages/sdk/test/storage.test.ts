import { describe, expect, it } from 'vitest';
import JSZip from 'jszip';
import { CheckpointManager } from '../src/storage/checkpoint';
import { extractMineruZip } from '../src/mineru/zipExtract';
import { writeText, readText } from '../src/storage/opfs';
import { mockOpfsRoot } from './helpers/mockOpfs';

describe('CheckpointManager', () => {
  it('starts empty and persists marks', async () => {
    const root = mockOpfsRoot();
    const dir = await root.getDirectoryHandle('job1', { create: true });
    const ckpt = new CheckpointManager(dir);
    await ckpt.load();
    expect(ckpt.isStageCompleted('pdf_conversion')).toBe(false);

    await ckpt.markStageCompleted('pdf_conversion', { md_file: 'full.md' });
    expect(ckpt.isStageCompleted('pdf_conversion')).toBe(true);
    expect(ckpt.getStageData('pdf_conversion')).toEqual({ md_file: 'full.md' });

    // Reload from "disk"
    const ckpt2 = new CheckpointManager(dir);
    await ckpt2.load();
    expect(ckpt2.isStageCompleted('pdf_conversion')).toBe(true);
    expect(ckpt2.getStageData<{ md_file: string }>('pdf_conversion')?.md_file).toBe('full.md');
  });

  it('resetFromStage removes the stage and all later ones', async () => {
    const root = mockOpfsRoot();
    const dir = await root.getDirectoryHandle('job2', { create: true });
    const ckpt = new CheckpointManager(dir);
    await ckpt.load();
    await ckpt.markStageCompleted('pdf_conversion');
    await ckpt.markStageCompleted('llm_chunking', { base_chunks: [] });
    await ckpt.resetFromStage('llm_chunking');
    expect(ckpt.isStageCompleted('pdf_conversion')).toBe(true);
    expect(ckpt.isStageCompleted('llm_chunking')).toBe(false);
  });

  it('clearAll empties everything', async () => {
    const root = mockOpfsRoot();
    const dir = await root.getDirectoryHandle('job3', { create: true });
    const ckpt = new CheckpointManager(dir);
    await ckpt.load();
    await ckpt.markStageCompleted('pdf_conversion');
    await ckpt.clearAll();
    expect(ckpt.isStageCompleted('pdf_conversion')).toBe(false);
  });

  it('resets on version mismatch', async () => {
    const root = mockOpfsRoot();
    const dir = await root.getDirectoryHandle('job4', { create: true });
    await writeText(dir, '.checkpoint.json', JSON.stringify({ version: 99, completed_stages: ['pdf_conversion'], data: {} }));
    const ckpt = new CheckpointManager(dir);
    await ckpt.load();
    expect(ckpt.isStageCompleted('pdf_conversion')).toBe(false);
  });
});

describe('extractMineruZip', () => {
  it('extracts md + nested files and selects the lexicographically last md', async () => {
    const zip = new JSZip();
    zip.file('book/full.md', '# Main content');
    zip.file('book/nested.md', '# Nested');
    zip.file('book/images/fig1.jpg', new Uint8Array([1, 2, 3]));
    const bytes = new Uint8Array(await zip.generateAsync({ type: 'arraybuffer' }));

    const root = mockOpfsRoot();
    const dir = await root.getDirectoryHandle('job5', { create: true });
    const result = await extractMineruZip(bytes, dir);

    // sorted md paths: ['book/full.md', 'book/nested.md'] → last is nested
    expect(result.mdText).toBe('# Nested');
    expect(result.mdPath).toBe('book/nested.md');

    // files actually written into the mock OPFS tree
    const bookDir = await dir.getDirectoryHandle('book');
    const imgDir = await bookDir.getDirectoryHandle('images');
    await imgDir.getFileHandle('fig1.jpg');
    const full = await readText(bookDir, 'full.md');
    expect(full).toBe('# Main content');
  });

  it('throws when no md present', async () => {
    const zip = new JSZip();
    zip.file('a.txt', 'hello');
    const bytes = new Uint8Array(await zip.generateAsync({ type: 'arraybuffer' }));
    const root = mockOpfsRoot();
    const dir = await root.getDirectoryHandle('job6', { create: true });
    await expect(extractMineruZip(bytes, dir)).rejects.toThrow();
  });
});
