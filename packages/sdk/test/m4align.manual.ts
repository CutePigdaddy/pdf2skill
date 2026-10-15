/**
 * M4/M5 alignment verification (TS side). Mirrors .m4align/py_run.py: same
 * deterministic mock LLM, same full.md, Stage 2+3+4 outputs dumped to
 * .m4align/ts_out/ for byte-diffing against py_out/.
 * Run: npx vite-node test/m4align.manual.ts
 */
import { mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { LlmChunker } from '../src/stages/llmChunker';
import { TreeMerger } from '../src/stages/treeMerger';
import { SkillEngine } from '../src/stages/skillEngine';
import type { LlmTransport } from '../src/stages/llmTransport';
import { mockOpfsRoot, type MockDirHandle } from './helpers/mockOpfs';
import { ensureDir } from '../src/storage/opfs';

const BASE = resolve(__dirname, '../../../.m4align');
const prompts: string[] = [];

function mockResponse(prompt: string): string {
  if (prompt.startsWith('You are a document structure analyzer')) {
    const tree = [...prompt.matchAll(/^\[Line (\d+)\] (#{1,4}) (.+)$/gm)];
    const level1 = tree
      .filter((m) => m[2] === '#')
      .map((m) => [parseInt(m[1], 10), m[3]] as const);
    const tocLine = level1.find(([, t]) => t === '目录' || t === 'TOC')?.[0];
    let tocRange: [number, number] | null = null;
    if (tocLine !== undefined) {
      const after = level1.filter(([n]) => n > tocLine).map(([n]) => n);
      const tocEnd = after.length ? after[0] - 1 : undefined;
      if (tocEnd !== undefined) tocRange = [tocLine, tocEnd];
    }
    const plan = {
      toc_range: tocRange,
      preface_range: null,
      chapter_splits: level1.map(([n]) => n),
      atomic_ranges: {},
    };
    return JSON.stringify(plan);
  }

  if (prompt.startsWith('You are an expert curriculum designer')) {
    return (
      'This overview covers the synthetic fixture book.\n' +
      'It spans three chapters plus a references section.\n' +
      '[METADATA_DESC: A concise guide to the sample book content covering fundamentals, applications, and summary.]'
    );
  }

  if (prompt.startsWith('You are an expert at creating concise and technical identifiers')) {
    return 'sample-book-fixture';
  }

  if (prompt.startsWith('You are a technical indexer')) {
    const m = /## Sub (\d+)\.(\d+): Topic (\d+)/.exec(prompt);
    if (m) return `sub-${m[1]}-${m[2]} | topic-${m[3]} | sample-fixture`;
    const m2 = /# Chapter (\d+): ([A-Za-z ]+)/.exec(prompt);
    if (m2) return `chapter-${m2[1]} | ${m2[2].trim().toLowerCase().replace(/ /g, '-')} | sample-fixture`;
    return 'general | sample-fixture';
  }

  // Peeling prompt: extract the fenced content block
  const start = prompt.indexOf('```\n') + 4;
  const end = prompt.indexOf('\n```', start);
  const content = prompt.slice(start, end);
  const occ: number[] = [];
  let from = 0;
  for (;;) {
    const idx = content.indexOf('\n## ', from);
    if (idx === -1) break;
    occ.push(idx);
    from = idx + 1;
  }
  if (occ.length < 2) return JSON.stringify({ anchors: [], is_atomic: false });
  const picks = occ.length >= 3 ? occ.slice(1, 3) : occ.slice(0, 2);
  const anchors = picks.map((pos, i) => ({
    anchor: [...content].slice(pos + 1, pos + 31).join(''),
    description: `start of part ${i + 1}`,
    suggested_title: `Part ${i + 1}`,
  }));
  return JSON.stringify({ anchors, is_atomic: false });
}

const llm: LlmTransport = {
  async chat(prompt: string) {
    prompts.push(prompt);
    return mockResponse(prompt);
  },
  parseJson(text: string) {
    return JSON.parse(text);
  },
};

function dumpDir(dir: MockDirHandle, diskPath: string): void {
  mkdirSync(diskPath, { recursive: true });
  for (const [name, file] of dir.files) {
    writeFileSync(resolve(diskPath, name), file.data);
  }
  for (const [name, sub] of dir.dirs) {
    dumpDir(sub, resolve(diskPath, name));
  }
}

async function main() {
  const content = readFileSync(resolve(BASE, 'full.md'), 'utf-8');

  const chunker = new LlmChunker(llm);
  const splitData = await chunker.split(content);
  const baseChunks = chunker.extractChunks(splitData);
  console.log(`base chunks: ${baseChunks.length}, fallback=${splitData.fallback}`);

  const merger = new TreeMerger(llm);
  const root = mockOpfsRoot() as unknown as MockDirHandle;
  const originalDir = await ensureDir(root as never, 'full_chunks_original');
  await merger.saveOriginalChunks(originalDir, baseChunks);
  const masterRoot = await merger.buildAndMerge(baseChunks);
  const resultsDir = await ensureDir(root as never, 'full_chunks');
  await merger.saveResults(resultsDir, masterRoot);
  console.log(`peel_errors: ${merger.peelErrors}`);

  const skillDir = await ensureDir(root as never, 'generated_skills');
  const engine = new SkillEngine(skillDir, llm, { jobRoot: root as never });
  await engine.generate(masterRoot, 'Test Book');
  console.log('skill generation done');

  const outDir = resolve(BASE, 'ts_out');
  rmSync(outDir, { recursive: true, force: true });
  dumpDir(root, outDir);
  writeFileSync(resolve(BASE, 'ts_prompts.json'), JSON.stringify(prompts));
  console.log(`prompts recorded: ${prompts.length}`);
}

main().catch((e) => {
  console.error('[m4align] FAILED:', e);
  process.exit(1);
});
