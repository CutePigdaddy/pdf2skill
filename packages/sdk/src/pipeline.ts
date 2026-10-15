import type { Pdf2SkillConfig, ResettableStage } from './types';
import type { Emitter } from './events';
import { Pdf2SkillError } from './errors';
import type { LlmTransport } from './stages/llmTransport';
import type { LlmStage } from './llm/llmClient';
import { LlmClient } from './llm/llmClient';
import { LlmChunker, type BaseChunk } from './stages/llmChunker';
import { TreeMerger, ChunkNode } from './stages/treeMerger';
import { SkillEngine } from './stages/skillEngine';
import { RemoteMinerUClient, LocalMinerUClient } from './mineru/clients';
import { CheckpointManager } from './storage/checkpoint';
import { validatePdfFull, sanitizeFilename } from './pdf/validator';
import { ensureDir, fileExists, listDir, readText, removeEntry, writeBytes, writeText } from './storage/opfs';

export interface PipelineInput {
  kind: 'pdf' | 'markdown';
  /** Raw input bytes. Required unless Stage 1 is already checkpointed. */
  bytes?: Uint8Array;
  filename: string;
}

export interface RunPipelineOptions {
  jobRoot: FileSystemDirectoryHandle;
  config: Pdf2SkillConfig;
  input: PipelineInput;
  fromStage?: ResettableStage;
  restart?: boolean;
  signal?: AbortSignal;
  /** LLM factory (defaults to real clients through the proxy backend). */
  createLlm?: (stage: LlmStage) => LlmTransport;
  /** Event sink for stage progress and logs. */
  emitter?: Emitter;
}

export interface PipelineResult {
  skillName: string;
  skillMd: string;
  referenceCount: number;
}

/**
 * Port of main.py::run_pipeline — the 4-stage orchestration with checkpoint
 * resume semantics:
 * - Stage 2 fallback does not write a checkpoint (retried next run)
 * - Stage 3 peel_errors > 0 does not write a checkpoint
 * - Stage 4 has its own fine-grained checkpoint inside generated_skills/
 */
export async function runPipeline(opts: RunPipelineOptions): Promise<PipelineResult> {
  const { jobRoot, config, input, emitter } = opts;
  const log = (level: 'info' | 'warn', message: string) =>
    emitter?.emit('log', { level, message });
  const stage = (
    s: 'mineru' | 'chunking' | 'peeling' | 'skill',
    status: 'active' | 'done',
    extra?: { percent?: number; detail?: string; resumed?: boolean },
  ) => emitter?.emit('stage', { stage: s, status, ...extra });

  const createLlm =
    opts.createLlm ?? ((s: LlmStage) => new LlmClient(s, config, opts.signal));

  const checkpoint = new CheckpointManager(jobRoot);
  await checkpoint.load();

  if (opts.restart) {
    await checkpoint.clearAll();
    if (await fileExists(jobRoot, 'full.md')) {
      await removeEntry(jobRoot, 'full.md');
      log('info', '--restart: removed stale full.md');
    }
    log('info', '--restart: all checkpoint data cleared');
  } else if (opts.fromStage) {
    await checkpoint.resetFromStage(opts.fromStage);
    log('info', `--from-stage ${opts.fromStage}: checkpoint reset from '${opts.fromStage}' onwards`);
  }

  // ── Stage 1: PDF → Markdown ────────────────────────────────────────
  let mdFile = 'full.md';
  if (!checkpoint.isStageCompleted('pdf_conversion')) {
    stage('mineru', 'active');
    if (input.kind === 'markdown') {
      if (input.bytes) {
        await writeBytes(jobRoot, 'input.md', input.bytes);
      }
      await checkpoint.markStageCompleted('pdf_conversion', { md_file: 'input.md' });
      mdFile = 'input.md';
    } else {
      if (!input.bytes) {
        throw new Pdf2SkillError(
          'PDF_INVALID_NAME',
          '缺少输入文件且 Stage 1 未完成',
          '请重新提供 PDF 文件',
          'mineru',
        );
      }
      const validation = await validatePdfFull(input.bytes, input.filename);
      if (validation) {
        throw new Pdf2SkillError(
          validation.code,
          validation.message,
          validation.advice,
          'mineru',
        );
      }
      if (await fileExists(jobRoot, 'full.md')) {
        log('info', 'Markdown already generated. Skipping conversion.');
      } else {
        const safeName = sanitizeFilename(input.filename);
        const mineruOpts = {
          signal: opts.signal,
          onProgress: (p: { phase: string; pct: number; extracted: number; total: number }) => {
            stage('mineru', 'active', {
              percent: p.pct,
              detail: `${p.phase} ${p.extracted}/${p.total}页`,
            });
          },
        };
        const client =
          config.mineru.api_mode === 'local'
            ? new LocalMinerUClient(config, mineruOpts)
            : new RemoteMinerUClient(config, mineruOpts);
        const md = await client.process(input.bytes, safeName, jobRoot);
        await writeText(jobRoot, 'full.md', md);
      }
      await checkpoint.markStageCompleted('pdf_conversion', { md_file: 'full.md' });
    }
    stage('mineru', 'done', { percent: 100 });
  } else {
    const data = (checkpoint.getStageData('pdf_conversion') as { md_file?: string }) ?? {};
    mdFile = data.md_file ?? 'full.md';
    stage('mineru', 'done', { percent: 100, resumed: true });
    log('info', `Loaded existing markdown: ${mdFile}`);
  }
  const content = await readText(jobRoot, mdFile);

  // ── Stage 2: LLM chunking ──────────────────────────────────────────
  let baseChunks: BaseChunk[];
  if (!checkpoint.isStageCompleted('llm_chunking')) {
    stage('chunking', 'active');
    log('info', '--- Stage 2: LLM Chunking Strategy ---');
    const chunker = new LlmChunker(createLlm('chunking'));
    const splitData = await chunker.split(content);
    baseChunks = chunker.extractChunks(splitData);
    if (splitData.fallback) {
      log('warn', 'Stage 2 used fallback (LLM failed). Checkpoint NOT saved — will retry on next run.');
    } else {
      await checkpoint.markStageCompleted('llm_chunking', { base_chunks: baseChunks });
    }
    stage('chunking', 'done');
  } else {
    const data = (checkpoint.getStageData('llm_chunking') as { base_chunks?: BaseChunk[] }) ?? {};
    baseChunks = data.base_chunks ?? [];
    log('info', `Loaded ${baseChunks.length} base chunks from checkpoint`);
    stage('chunking', 'done', { resumed: true });
  }

  const merger = new TreeMerger(createLlm('peeling'), {
    config: {
      chunkMaxTokens: config.pdf.chunk_max_tokens,
      chunkMaxIterations: config.pdf.chunk_max_iterations,
      chunkAnchorLength: config.pdf.chunk_anchor_length,
      chunkMinThreshold: config.pdf.chunk_min_threshold,
    },
    onProgress: (idx, total, chunkId) =>
      stage('peeling', 'active', {
        percent: Math.trunc((idx / total) * 100),
        detail: `${chunkId} (${idx}/${total})`,
      }),
    log: (m) => log('info', m),
  });
  await merger.saveOriginalChunks(await ensureDir(jobRoot, 'full_chunks_original'), baseChunks);
  log('info', 'Original chunks saved to: full_chunks_original/');

  // ── Stage 3: tree merging / TOC drilling ───────────────────────────
  let masterRoot: ChunkNode;
  if (!checkpoint.isStageCompleted('tree_merging')) {
    stage('peeling', 'active');
    log('info', '--- Stage 3: TOC Drilling & Merging ---');
    masterRoot = await merger.buildAndMerge(baseChunks);
    await merger.saveResults(await ensureDir(jobRoot, 'full_chunks'), masterRoot);
    if (merger.peelErrors > 0) {
      log(
        'warn',
        `Stage 3 had ${merger.peelErrors} LLM error(s) during peeling. Checkpoint NOT saved — will retry on next run.`,
      );
    } else {
      await checkpoint.markStageCompleted('tree_merging', {
        master_root: masterRoot.toDict(),
      });
    }
    stage('peeling', 'done', { percent: 100 });
  } else {
    const data = (checkpoint.getStageData('tree_merging') as { master_root?: Record<string, unknown> }) ?? {};
    masterRoot = ChunkNode.fromDict(data.master_root ?? {});
    log('info', 'Loading merged tree from checkpoint...');
    stage('peeling', 'done', { percent: 100, resumed: true });
  }

  // ── Stage 4: skill generation ──────────────────────────────────────
  stage('skill', 'active');
  log('info', '--- Stage 4: Skill Generation ---');
  const bookTitle = input.filename.replace(/\.[^.]*$/, '');
  const skillDir = await ensureDir(jobRoot, 'generated_skills');
  const engine = new SkillEngine(skillDir, createLlm('skill_engine'), {
    jobRoot,
    onProgress: (idx, total, title) =>
      stage('skill', 'active', {
        percent: Math.trunc((idx / total) * 100),
        detail: `${title.slice(0, 20)} (${idx}/${total})`,
      }),
    log: (m) => log('info', m),
  });
  await engine.generate(masterRoot, bookTitle);

  const skillMd = await readText(skillDir, 'SKILL.md');
  const refDir = await ensureDir(skillDir, 'references');
  let referenceCount = 0;
  for (const entry of await listDir(refDir)) {
    if (entry.kind === 'file' && entry.name.endsWith('.md')) referenceCount++;
  }
  const nameMatch = /^name:\s*(.+)$/m.exec(skillMd);
  stage('skill', 'done', { percent: 100 });
  log('info', 'Pipeline Execution Finished Successfully!');

  return {
    skillName: nameMatch ? nameMatch[1].trim() : bookTitle,
    skillMd,
    referenceCount,
  };
}
