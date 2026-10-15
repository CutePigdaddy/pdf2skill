import type {
  JobMeta,
  OutputFile,
  OutputTreeNode,
  Pdf2SkillConfig,
  RunInput,
  RunOptions,
  RunResult,
} from './types';
import { Pdf2SkillError } from './errors';
import { Emitter } from './events';
import { defaultConfig, loadStoredConfig, mergeConfig, saveStoredConfig } from './config';
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

const JOBS_DIR = 'jobs';

export interface Pdf2SkillOptions {
  /** Initial config (deep-merged onto defaults; falls back to localStorage). */
  config?: Partial<Pdf2SkillConfig>;
  /** Storage root override (tests inject an in-memory OPFS mock). */
  storageRoot?: FileSystemDirectoryHandle;
  /** LLM transport factory override (tests inject a mock). */
  createLlm?: (stage: LlmStage) => LlmTransport;
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

  getConfig(): Pdf2SkillConfig {
    return this.config;
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
    if (!/^[A-Za-z0-9._-]+$/.test(jobId)) {
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

  async listJobs(): Promise<JobMeta[]> {
    const jobs = await this.jobsDir();
    const metas: JobMeta[] = [];
    for (const entry of await listDir(jobs)) {
      if (entry.kind !== 'directory') continue;
      const dir = await jobs.getDirectoryHandle(entry.name);
      if (!(await fileExists(dir, 'job.json'))) continue;
      try {
        metas.push(await this.readJobMeta(dir));
      } catch {
        // Corrupt job.json: still list it with a minimal meta.
        metas.push({ jobId: entry.name, inputName: entry.name, completedStages: [], updatedAt: 0 });
      }
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
    const controller = new AbortController();
    this.abortController = controller;
    if (options.signal) {
      options.signal.addEventListener('abort', () => controller.abort(), { once: true });
    }

    const jobId = Pdf2Skill.jobIdFor(input.name);
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
    meta.updatedAt = Date.now();
    await this.writeJobMeta(jobRoot, meta);

    try {
      const result = await this.execute(jobRoot, input, options, controller.signal, meta);
      return { jobId, ...result };
    } finally {
      this.abortController = null;
      this.runningJobId = null;
    }
  }

  /** Continue a previously created job from its checkpoint. */
  async resume(jobId: string, options: Omit<RunOptions, 'restart'> = {}): Promise<RunResult> {
    if (this.abortController) {
      throw new Pdf2SkillError('ALREADY_RUNNING', '已有任务正在运行', '请先等待完成或调用 abort()');
    }
    const jobRoot = await this.jobRoot(jobId);
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

    const controller = new AbortController();
    this.abortController = controller;
    if (options.signal) {
      options.signal.addEventListener('abort', () => controller.abort(), { once: true });
    }
    this.runningJobId = jobId;
    try {
      const result = await this.execute(jobRoot, input, { ...options, fromStage: options.fromStage }, controller.signal, meta);
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
