import JSZip from 'jszip';
import type { JobMeta, StageName } from '../types';
import { Pdf2SkillError } from '../errors';
import {
  ensureDir,
  getDir,
  removeEntry,
  writeBytes,
  writeTextAtomic,
} from './opfs';
import { CHECKPOINT_VERSION } from './checkpoint';

const ROOT_FILES = new Set(['.checkpoint.json', 'full.md', 'input.md']);
const ROOT_DIRS = new Set([
  'images',
  'full_chunks_original',
  'full_chunks',
  'generated_skills',
  'peeling_cache',
]);
const STAGE_ORDER: StageName[] = [
  'pdf_conversion',
  'llm_chunking',
  'tree_merging',
  'skill_generation',
];

export interface ImportJobBundleOptions {
  jobsDir: FileSystemDirectoryHandle;
  jobId: string;
  zipBytes: Uint8Array;
  overwrite?: boolean;
  inputName?: string;
  onProgress?: (done: number, total: number) => void;
}

interface CheckpointShape {
  version: number;
  completed_stages: string[];
  data: Record<string, unknown>;
}

function invalid(message: string, advice: string): Pdf2SkillError {
  return new Pdf2SkillError('IMPORT_INVALID', message, advice);
}

function validateJobId(jobId: string): void {
  if (!/^[\p{L}\p{N}._-]{1,100}$/u.test(jobId) || /^[.]+$/.test(jobId)) {
    throw new Pdf2SkillError('INVALID_JOB_ID', `非法的任务 ID: ${jobId}`, '');
  }
}

function sanitizeZipPath(raw: string): string[] | null {
  const path = raw.replace(/\\/g, '/').replace(/^\.\/+/, '');
  if (!path || path.startsWith('/') || /^[A-Za-z]:/.test(path)) return null;
  const segments = path.split('/').filter((s) => s !== '' && s !== '.');
  if (segments.length === 0 || segments.some((s) => s === '..')) return null;
  return segments;
}

function isAllowed(segments: string[]): boolean {
  if (segments.length === 1) return ROOT_FILES.has(segments[0]);
  return ROOT_DIRS.has(segments[0]);
}

function parseCheckpoint(raw: string): CheckpointShape {
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    throw invalid('任务包中的 checkpoint 不是有效 JSON', '请重新从服务端导出该任务');
  }
  if (
    typeof data !== 'object' ||
    data === null ||
    (data as CheckpointShape).version !== CHECKPOINT_VERSION ||
    !Array.isArray((data as CheckpointShape).completed_stages) ||
    typeof (data as CheckpointShape).data !== 'object' ||
    (data as CheckpointShape).data === null
  ) {
    throw invalid('任务包中的 checkpoint 版本或结构不受支持', '请用当前版本重新运行或重新导出该任务');
  }
  return data as CheckpointShape;
}

function stageData(checkpoint: CheckpointShape, stage: string): Record<string, unknown> {
  const value = checkpoint.data[stage];
  return typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : {};
}

function pickMarkdownName(
  checkpoint: CheckpointShape,
  rootFiles: Set<string>,
): string | null {
  const declared = stageData(checkpoint, 'pdf_conversion').md_file;
  const candidates = [
    typeof declared === 'string' ? declared.replace(/\\/g, '/').split('/').pop() : '',
    'full.md',
    'input.md',
  ];
  for (const name of candidates) {
    if (name && ROOT_FILES.has(name) && name !== '.checkpoint.json' && rootFiles.has(name)) {
      return name;
    }
  }
  return null;
}

/**
 * Import a server-side job bundle (see frontend/job_bridge.py) into OPFS.
 *
 * The Python checkpoint stores `md_file` as a host path
 * (`outputs/book/full.md`); it is rewritten to the bundle-relative name so the
 * browser pipeline can read it from the job directory.
 */
export async function importJobBundle(opts: ImportJobBundleOptions): Promise<JobMeta> {
  validateJobId(opts.jobId);

  const existing = await getDir(opts.jobsDir, opts.jobId);
  if (existing && !opts.overwrite) {
    throw new Pdf2SkillError(
      'JOB_EXISTS',
      `浏览器中已存在任务 '${opts.jobId}'`,
      '如需用磁盘版本覆盖，请确认覆盖后重试',
    );
  }

  let zip: JSZip;
  try {
    zip = await JSZip.loadAsync(opts.zipBytes);
  } catch {
    throw invalid('导入文件不是有效的 zip 压缩包', '请重新从服务端下载任务包');
  }

  const entries: { segments: string[]; file: JSZip.JSZipObject }[] = [];
  for (const file of Object.values(zip.files)) {
    if (file.dir) continue;
    const segments = sanitizeZipPath(file.name);
    if (!segments || !isAllowed(segments)) continue;
    entries.push({ segments, file });
  }

  const checkpointEntry = entries.find(
    (e) => e.segments.length === 1 && e.segments[0] === '.checkpoint.json',
  );
  if (!checkpointEntry) {
    throw invalid('任务包缺少 .checkpoint.json', '请确认该任务在服务端 outputs/ 中完整存在');
  }

  const checkpoint = parseCheckpoint(await checkpointEntry.file.async('string'));
  const completed = new Set(
    checkpoint.completed_stages.filter((s): s is StageName =>
      STAGE_ORDER.includes(s as StageName),
    ),
  );
  if (!completed.has('pdf_conversion')) {
    throw invalid('该任务的 Stage 1 尚未完成，无法导入续跑', '请先在命令行完成 PDF 解析');
  }
  if (
    completed.has('llm_chunking') &&
    !Array.isArray(stageData(checkpoint, 'llm_chunking').base_chunks)
  ) {
    throw invalid('checkpoint 中的分块结果缺失', '请从命令行重新生成该任务');
  }
  if (
    completed.has('tree_merging') &&
    !stageData(checkpoint, 'tree_merging').master_root
  ) {
    throw invalid('checkpoint 中的树结构缺失', '请从命令行重新生成该任务');
  }

  const rootFiles = new Set(
    entries.filter((e) => e.segments.length === 1).map((e) => e.segments[0]),
  );
  // The bundle excludes job.json; it is regenerated from the checkpoint.
  rootFiles.delete('job.json');
  const mdName = pickMarkdownName(checkpoint, rootFiles);
  if (!mdName) {
    throw invalid('任务包缺少 Stage 1 Markdown 文件', '请确认 full.md / input.md 仍在 outputs/ 中');
  }

  if (existing) await removeEntry(opts.jobsDir, opts.jobId);
  const jobRoot = await ensureDir(opts.jobsDir, opts.jobId);

  try {
    const total = entries.length;
    let done = 0;
    for (const entry of entries) {
      const name = entry.segments[entry.segments.length - 1];
      if (entry.segments.length === 1 && name === '.checkpoint.json') {
        done++;
        opts.onProgress?.(done, total);
        continue;
      }
      const parent = entry.segments.slice(0, -1).join('/');
      const dir = parent ? await ensureDir(jobRoot, parent) : jobRoot;
      const bytes = new Uint8Array(await entry.file.async('arraybuffer'));
      await writeBytes(dir, name, bytes);
      done++;
      opts.onProgress?.(done, total);
    }

    checkpoint.data.pdf_conversion = {
      ...stageData(checkpoint, 'pdf_conversion'),
      md_file: mdName,
    };
    await writeTextAtomic(jobRoot, '.checkpoint.json', JSON.stringify(checkpoint, null, 2));

    if (entries.some((e) => e.segments.join('/') === 'generated_skills/SKILL.md')) {
      completed.add('skill_generation');
    }

    const meta: JobMeta = {
      jobId: opts.jobId,
      inputName: opts.inputName?.trim() || opts.jobId,
      completedStages: STAGE_ORDER.filter((s) => completed.has(s)),
      updatedAt: Date.now(),
    };
    await writeTextAtomic(jobRoot, 'job.json', JSON.stringify(meta, null, 2));
    return meta;
  } catch (e) {
    await removeEntry(opts.jobsDir, opts.jobId).catch(() => {});
    throw e;
  }
}
