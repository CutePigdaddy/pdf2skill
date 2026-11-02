import type {
  JobMeta,
  JobSummary,
  OutputFile,
  OutputTreeNode,
  Pdf2SkillConfig,
  ResettableStage,
  RunInput,
  RunOptions,
  RunResult,
  StageName,
} from './types';
import { Pdf2SkillError } from './errors';
import { Emitter } from './events';
import { defaultConfig, loadStoredConfig, mergeConfig, saveStoredConfig } from './config';
import { SKILL_NAME_RULES, validateSkillName } from './naming';
import type { LlmTransport } from './stages/llmTransport';
import type { LlmStage } from './llm/llmClient';
import { runPipeline } from './pipeline';
import { exportJobZip } from './export/zip';
import {
  ensureDir,
  fileExists,
  getDir,
  getOpfsRoot,
  listDir,
  readBlob,
  readText,
  removeEntry,
  writeText,
} from './storage/opfs';
import { CheckpointManager } from './storage/checkpoint';
import { importJobBundle } from './storage/importJob';

const JOBS_DIR = 'jobs';
const INTERNAL_JOB_DIRS = new Set(['peeling_cache']);

const STAGE_SEQUENCE: StageName[] = [
  'pdf_conversion',
  'llm_chunking',
  'tree_merging',
  'skill_generation',
];

/**
 * Keep job.json aligned with an explicit stage reset. listJobs derives the
 * three pipeline stages from the checkpoint, but Stage 4 has no top-level
 * checkpoint entry, so its flag must be dropped here or it would survive a
 * Stage 2/3 rebuild and describe a SKILL.md that is about to be replaced.
 */
function truncateCompletedStages(
  meta: JobMeta,
  fromStage?: ResettableStage,
  restart = false,
): void {
  if (restart) {
    meta.completedStages = [];
    return;
  }
  if (!fromStage) return;
  const keep = new Set<StageName>(STAGE_SEQUENCE.slice(0, STAGE_SEQUENCE.indexOf(fromStage)));
  meta.completedStages = meta.completedStages.filter((s) => keep.has(s));
}

export interface Pdf2SkillOptions {
  /** Initial config (deep-merged onto defaults; falls back to localStorage). */
  config?: Partial<Pdf2SkillConfig>;
  /** Storage root override (tests inject an in-memory OPFS mock). */
  storageRoot?: FileSystemDirectoryHandle;
  /** LLM transport factory override (tests inject a mock). */
  createLlm?: (stage: LlmStage) => LlmTransport;
}

export interface ImportJobOptions {
  /** Replace an existing browser job with the imported one. */
  overwrite?: boolean;
  /** Display name for the job list (defaults to the job id). */
  inputName?: string;
  onProgress?: (done: number, total: number) => void;
}

/**
 * High-level facade over the 4-stage pipeline. Jobs and their outputs live in
 * OPFS under `<root>/jobs/{jobId}/`, mirroring the Python `outputs/<book>/`
 * layout. All LLM/MinerU traffic goes through the thin proxy backend, which
 * keeps API keys server-side.
 */
export class Pdf2Skill {
  private config: Pdf2SkillConfig;
  private readonly emitter = new Emitter();
  private readonly storageRoot?: FileSystemDirectoryHandle;
  private readonly createLlm?: (stage: LlmStage) => LlmTransport;
  private abortController: AbortController | null = null;
  private runningJobId: string | null = null;

  constructor(opts: Pdf2SkillOptions = {}) {
    this.config = opts.config
      ? mergeConfig(defaultConfig(), opts.config)
      : (loadStoredConfig() ?? defaultConfig());
    this.storageRoot = opts.storageRoot;
    this.createLlm = opts.createLlm;
  }

  // ── configuration ───────────────────────────────────────────────────

  configure(patch: Partial<Pdf2SkillConfig>): void {
    this.config = mergeConfig(this.config, patch);
    try {
      saveStoredConfig(this.config);
    } catch {
      // localStorage unavailable (e.g. Node tests) — config stays in memory.
    }
  }

  /** A deep copy — mutating it has no effect until passed to `configure()`. */
  getConfig(): Pdf2SkillConfig {
    return structuredClone(this.config);
  }

  // ── events ──────────────────────────────────────────────────────────

  on(event: 'stage', handler: (e: import('./types').StageEvent) => void): () => void;
  on(event: 'log', handler: (e: import('./types').LogEvent) => void): () => void;
  on(event: 'error', handler: (e: { error: Pdf2SkillError }) => void): () => void;
  on(event: 'done', handler: (e: RunResult) => void): () => void;
  on(event: keyof import('./types').Pdf2SkillEvents, handler: never): () => void {
    return this.emitter.on(event, handler);
  }

  removeAllListeners(): void {
    this.emitter.removeAll();
  }

  // ── job lifecycle ───────────────────────────────────────────────────

  private async root(): Promise<FileSystemDirectoryHandle> {
    return this.storageRoot ?? getOpfsRoot();
  }

  private async jobsDir(): Promise<FileSystemDirectoryHandle> {
    return ensureDir(await this.root(), JOBS_DIR);
  }

  private async jobRoot(jobId: string): Promise<FileSystemDirectoryHandle> {
    // One path segment: letters/digits (any script), dot, underscore, hyphen.
    // Unicode letters are allowed so filename-derived job IDs (e.g. Chinese
    // book names) stay browsable; '.'/'..' are rejected as traversal.
    if (!/^[\p{L}\p{N}._-]{1,100}$/u.test(jobId) || /^[.]+$/.test(jobId)) {
      throw new Pdf2SkillError('INVALID_JOB_ID', `非法的任务 ID: ${jobId}`, '');
    }
    const dir = await getDir(await this.jobsDir(), jobId);
    if (!dir) {
      throw new Pdf2SkillError(
        'JOB_NOT_FOUND',
        `任务 '${jobId}' 不存在`,
        '请先运行任务或检查任务列表',
      );
    }
    return dir;
  }

  /** Slugify an input filename into a Python-style output dir name. */
  private static jobIdFor(filename: string): string {
    const stem = filename.replace(/\.[^.]*$/, '');
    const slug = stem
      .replace(/[^\p{L}\p{N}]+/gu, '_')
      .replace(/^_+|_+$/g, '')
      .slice(0, 60) || 'book';
    return slug;
  }

  private async writeJobMeta(
    jobRoot: FileSystemDirectoryHandle,
    meta: JobMeta,
  ): Promise<void> {
    await writeText(jobRoot, 'job.json', JSON.stringify(meta, null, 2));
  }

  private async readJobMeta(jobRoot: FileSystemDirectoryHandle): Promise<JobMeta> {
    const raw = await readText(jobRoot, 'job.json');
    return JSON.parse(raw) as JobMeta;
  }

  private async summarizeJob(
    dir: FileSystemDirectoryHandle,
    meta: JobMeta,
  ): Promise<JobSummary> {
    const checkpoint = new CheckpointManager(dir);
    await checkpoint.load();

    const stage1Data =
      (checkpoint.getStageData('pdf_conversion') as { md_file?: string } | undefined) ?? {};
    const mdName = (stage1Data.md_file ?? '').split(/[\\/]/).filter(Boolean).pop() ?? '';
    const mdExists = !!mdName && (await fileExists(dir, mdName));
    const completed = new Set<StageName>();
    if (checkpoint.isStageCompleted('pdf_conversion')) completed.add('pdf_conversion');
    if (checkpoint.isStageCompleted('llm_chunking')) completed.add('llm_chunking');
    if (checkpoint.isStageCompleted('tree_merging')) completed.add('tree_merging');

    const skillDir = await getDir(dir, 'generated_skills');
    if (
      skillDir &&
      meta.completedStages.includes('skill_generation') &&
      (await fileExists(skillDir, 'SKILL.md'))
    ) {
      completed.add('skill_generation');
    }

    return {
      ...meta,
      completedStages: STAGE_SEQUENCE.filter((s) => completed.has(s)),
      resumable: checkpoint.isStageCompleted('pdf_conversion') && mdExists,
    };
  }

  async listJobs(): Promise<JobSummary[]> {
    const jobs = await this.jobsDir();
    const metas: JobSummary[] = [];
    for (const entry of await listDir(jobs)) {
      if (entry.kind !== 'directory') continue;
      const dir = await jobs.getDirectoryHandle(entry.name);
      if (!(await fileExists(dir, 'job.json'))) continue;
      let meta: JobMeta;
      try {
        meta = await this.readJobMeta(dir);
      } catch {
        // Corrupt job.json: still list it with a minimal meta.
        meta = { jobId: entry.name, inputName: entry.name, completedStages: [], updatedAt: 0 };
      }
      metas.push(await this.summarizeJob(dir, meta));
    }
    metas.sort((a, b) => b.updatedAt - a.updatedAt);
    return metas;
  }

  async deleteJob(jobId: string): Promise<void> {
    const jobs = await this.jobsDir();
    if (!(await getDir(jobs, jobId))) {
      throw new Pdf2SkillError('JOB_NOT_FOUND', `任务 '${jobId}' 不存在`, '');
    }
    await removeEntry(jobs, jobId);
  }

  /**
   * Import a server-side job bundle (``frontend/job_bridge.py``) into OPFS so
   * a CLI job can be resumed from the same checkpoint in the browser.
   */
  async importJob(
    jobId: string,
    zipBytes: Uint8Array,
    options: ImportJobOptions = {},
  ): Promise<JobSummary> {
    if (this.abortController) {
      throw new Pdf2SkillError('ALREADY_RUNNING', '已有任务正在运行，无法导入', '请先停止当前任务');
    }
    const jobs = await this.jobsDir();
    const meta = await importJobBundle({
      jobsDir: jobs,
      jobId,
      zipBytes,
      overwrite: options.overwrite,
      inputName: options.inputName,
      onProgress: options.onProgress,
    });
    const dir = await getDir(jobs, jobId);
    if (!dir) {
      throw new Pdf2SkillError('JOB_NOT_FOUND', `导入后未找到任务 '${jobId}'`, '');
    }
    return this.summarizeJob(dir, meta);
  }

  // ── run / abort / resume ────────────────────────────────────────────

  /**
   * Run (or continue) a conversion job. The jobId is derived from the input
   * filename — re-running the same file continues its checkpoint (mirrors the
   * Python `outputs/<stem>/` semantics); use `options.restart` or `fromStage`
   * to re-run stages.
   */
  async run(input: RunInput, options: RunOptions = {}): Promise<RunResult> {
    if (this.abortController) {
      throw new Pdf2SkillError('ALREADY_RUNNING', '已有任务正在运行', '请先等待完成或调用 abort()');
    }

    // The output name becomes the output directory name and the SKILL.md
    // frontmatter `name`, so it must satisfy the kebab-case skill spec.
    const outputName = options.outputName?.trim() || undefined;
    if (outputName) {
      const issues = validateSkillName(outputName);
      if (issues.length > 0) {
        throw new Pdf2SkillError(
          'INVALID_OUTPUT_NAME',
          `输出名称 '${outputName}' 不符合命名规范：${issues[0]}`,
          `${SKILL_NAME_RULES} 也可以留空，自动从文件名生成。`,
        );
      }
    }

    const controller = new AbortController();
    this.abortController = controller;
    if (options.signal) {
      options.signal.addEventListener('abort', () => controller.abort(), { once: true });
    }

    const jobId = outputName ?? Pdf2Skill.jobIdFor(input.name);
    const jobs = await this.jobsDir();
    const jobRoot = await ensureDir(jobs, jobId);
    this.runningJobId = jobId;

    // Preserve the existing job's stage history; only stamp the input name.
    const meta: JobMeta = (await this.readJobMeta(jobRoot).catch(() => null)) ?? {
      jobId,
      inputName: input.name,
      completedStages: [],
      updatedAt: 0,
    };
    meta.inputName = input.name;
    truncateCompletedStages(meta, options.fromStage, !!options.restart);
    meta.updatedAt = Date.now();
    await this.writeJobMeta(jobRoot, meta);

    try {
      const result = await this.execute(jobRoot, input, { ...options, outputName }, controller.signal, meta);
      return { jobId, ...result };
    } finally {
      this.abortController = null;
      this.runningJobId = null;
    }
  }

  /** Continue a previously created job from its checkpoint. */
  async resume(
    jobId: string,
    options: Omit<RunOptions, 'restart' | 'outputName'> = {},
  ): Promise<RunResult> {
    if (this.abortController) {
      throw new Pdf2SkillError('ALREADY_RUNNING', '已有任务正在运行', '请先等待完成或调用 abort()');
    }
    const jobRoot = await this.jobRoot(jobId);
    // resume() has no source bytes; re-running Stage 1 would either fail
    // validation or silently reuse the old markdown. Require a fresh run().
    if (options.fromStage === 'pdf_conversion') {
      throw new Pdf2SkillError(
        'JOB_MISSING_INPUT',
        `任务 '${jobId}' 缺少原始输入，无法重跑 Stage 1`,
        '请重新上传原始 PDF / Markdown 文件后运行',
      );
    }
    const meta = await this.readJobMeta(jobRoot).catch(() => ({
      jobId,
      inputName: jobId,
      completedStages: [],
      updatedAt: 0,
    }) as JobMeta);
    // Stage 1 needs input bytes unless pdf_conversion is already checkpointed
    // with its markdown file still on disk.
    const checkpoint = new CheckpointManager(jobRoot);
    await checkpoint.load();
    const mdFile =
      (checkpoint.getStageData('pdf_conversion') as { md_file?: string } | undefined)?.md_file ??
      '';
    let input: RunInput;
    if (checkpoint.isStageCompleted('pdf_conversion') && mdFile && (await fileExists(jobRoot, mdFile))) {
      input = { kind: 'pdf', file: new Blob(), name: meta.inputName };
    } else {
      throw new Pdf2SkillError(
        'JOB_MISSING_INPUT',
        `任务 '${jobId}' 缺少输入文件，无法从当前进度恢复`,
        '请重新上传 PDF / Markdown 文件运行',
      );
    }

    truncateCompletedStages(meta, options.fromStage);
    meta.updatedAt = Date.now();
    await this.writeJobMeta(jobRoot, meta);

    const controller = new AbortController();
    this.abortController = controller;
    if (options.signal) {
      options.signal.addEventListener('abort', () => controller.abort(), { once: true });
    }
    this.runningJobId = jobId;
    try {
      const result = await this.execute(
        jobRoot,
        input,
        { fromStage: options.fromStage },
        controller.signal,
        meta,
      );
      return { jobId, ...result };
    } finally {
      this.abortController = null;
      this.runningJobId = null;
    }
  }

  private async execute(
    jobRoot: FileSystemDirectoryHandle,
    input: RunInput,
    options: RunOptions,
    signal: AbortSignal,
    meta: JobMeta,
  ): Promise<Omit<RunResult, 'jobId'>> {
    const { config } = this;
    const bytes = new Uint8Array(await input.file.arrayBuffer());
    try {
      const result = await runPipeline({
        jobRoot,
        config,
        input: { kind: input.kind, bytes, filename: input.name },
        outputName: options.outputName,
        fromStage: options.fromStage,
        restart: options.restart,
        signal,
        emitter: this.emitter,
        createLlm: this.createLlm,
      });
      const stages: import('./types').StageName[] = [
        'pdf_conversion',
        'llm_chunking',
        'tree_merging',
        'skill_generation',
      ];
      meta.completedStages = stages;
      meta.updatedAt = Date.now();
      await this.writeJobMeta(jobRoot, meta);
      const full: RunResult = { jobId: meta.jobId, ...result };
      this.emitter.emit('done', full);
      return result;
    } catch (e) {
      const err =
        e instanceof Pdf2SkillError
          ? e
          : signal.aborted || (e as Error).name === 'AbortError'
            ? new Pdf2SkillError('ABORTED', '任务已中止', '已完成阶段的 checkpoint 已保存，可稍后续传', undefined, e)
            : new Pdf2SkillError('UNKNOWN', (e as Error).message || String(e), '', undefined, e);
      meta.updatedAt = Date.now();
      await this.writeJobMeta(jobRoot, meta).catch(() => {});
      this.emitter.emit('error', { error: err });
      throw err;
    }
  }

  abort(): void {
    this.abortController?.abort();
  }

  get runningJob(): string | null {
    return this.runningJobId;
  }

  // ── outputs ─────────────────────────────────────────────────────────

  private async buildTree(dir: FileSystemDirectoryHandle): Promise<OutputTreeNode[]> {
    const nodes: OutputTreeNode[] = [];
    for (const entry of await listDir(dir)) {
      if (INTERNAL_JOB_DIRS.has(entry.name)) continue;
      if (entry.kind === 'directory') {
        const children = await this.buildTree(await dir.getDirectoryHandle(entry.name));
        nodes.push({
          name: entry.name,
          type: 'dir',
          count: children.length,
          children,
        });
      } else {
        const file = await (await dir.getFileHandle(entry.name)).getFile();
        nodes.push({ name: entry.name, type: 'file', size: file.size });
      }
    }
    return nodes;
  }

  /** List a job's output directory tree (like the old /api/outputs endpoint). */
  async listOutputTree(jobId: string): Promise<OutputTreeNode> {
    const jobRoot = await this.jobRoot(jobId);
    const children = await this.buildTree(jobRoot);
    return { name: jobId, type: 'dir', count: children.length, children };
  }

  /** Read one file from a job by relative path ('/'-separated). */
  async readOutputFile(jobId: string, relPath: string): Promise<OutputFile> {
    const jobRoot = await this.jobRoot(jobId);
    const segs = relPath.split('/').filter((s) => s && s !== '.' && s !== '..');
    if (segs.length === 0) {
      throw new Pdf2SkillError('INVALID_PATH', `非法路径: ${relPath}`, '');
    }
    const fileName = segs[segs.length - 1];
    const parent =
      segs.length > 1
        ? await getDir(jobRoot, segs.slice(0, -1).join('/'))
        : jobRoot;
    if (!parent) {
      throw new Pdf2SkillError('FILE_NOT_FOUND', `文件不存在: ${relPath}`, '');
    }
    try {
      const blob = await readBlob(parent, fileName);
      if (/\.(png|jpe?g|gif|webp|svg|bmp)$/i.test(fileName)) {
        return { path: relPath, type: 'image', blob };
      }
      if (/\.(zip|pdf)$/i.test(fileName)) {
        return { path: relPath, type: 'binary', size: blob.size };
      }
      return { path: relPath, type: 'text', content: await blob.text() };
    } catch {
      throw new Pdf2SkillError('FILE_NOT_FOUND', `文件不存在: ${relPath}`, '');
    }
  }

  /** Package the job directory (minus input/job meta) as a downloadable zip. */
  async exportZip(jobId: string): Promise<Blob> {
    const jobRoot = await this.jobRoot(jobId);
    return exportJobZip(jobRoot);
  }

  /** Best-effort storage usage estimate (null when unsupported). */
  async storageEstimate(): Promise<{ usage: number; quota: number } | null> {
    const nav = navigator as Navigator & {
      storage?: { estimate?: () => Promise<StorageEstimate> };
    };
    if (!nav.storage?.estimate) return null;
    const est = await nav.storage.estimate();
    return { usage: est.usage ?? 0, quota: est.quota ?? 0 };
  }
}
