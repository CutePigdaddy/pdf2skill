import type { Pdf2SkillConfig } from '../types';
import { Pdf2SkillError } from '../errors';
import { mineruError } from './errors';
import { extractMineruZip } from './zipExtract';
import { splitPdfIfNeeded } from '../pdf/splitter';
import { ensureDir, writeBytes, copyDirFiles, getDir } from '../storage/opfs';

const PHASE_MAP: Record<string, string> = {
  'waiting-file': '上传中',
  pending: '排队中',
  running: '解析中',
  converting: '格式转换中',
  uploading: '上传中',
};

export interface MinerUProgress {
  phase: string;
  pct: number;
  extracted: number;
  total: number;
}

export interface MinerUClientOptions {
  signal?: AbortSignal;
  /** Progress callback (same shape as the old __MINERU_PROGRESS__ marker). */
  onProgress?: (p: MinerUProgress) => void;
}

interface FileResult {
  state: string;
  full_zip_url?: string;
  err_code?: string | number;
  err_msg?: string;
  extract_progress?: { extracted_pages?: number; total_pages?: number; start_time?: string };
}

/**
 * Port of core/pdf_processor.py::RemoteMinerUProcessor. All MinerU traffic is
 * relayed through the thin proxy backend (which holds MINERU_API_KEY), so the
 * browser never needs CORS access or the key itself.
 */
export class RemoteMinerUClient {
  private readonly config: Pdf2SkillConfig;
  private readonly opts: MinerUClientOptions;

  constructor(config: Pdf2SkillConfig, opts: MinerUClientOptions = {}) {
    this.config = config;
    this.opts = opts;
  }

  private api(path: string): string {
    return `${this.config.backendBaseUrl}${path}`;
  }

  private emitProgress(state: string, extracted = 0, total = 0): void {
    const phase = PHASE_MAP[state] ?? state;
    const pct = total > 0 ? Math.trunc((extracted / total) * 100) : 0;
    this.opts.onProgress?.({ phase, pct, extracted, total });
  }

  private async proxyJson(url: string, init?: RequestInit): Promise<Record<string, unknown>> {
    let resp: Response;
    try {
      resp = await fetch(url, { ...init, signal: this.opts.signal });
    } catch (e) {
      if ((e as Error).name === 'AbortError') throw e;
      throw new Pdf2SkillError(
        'MINERU_CONNECT',
        `无法连接代理后端 (${url})`,
        '请确认代理服务已启动（python frontend/proxy_server.py）',
        'mineru',
        e,
      );
    }
    let body: unknown;
    try {
      body = await resp.json();
    } catch {
      throw new Pdf2SkillError(
        'MINERU_CONNECT',
        `代理后端返回了非 JSON 响应 (HTTP ${resp.status})`,
        '请检查代理服务版本与地址',
        'mineru',
      );
    }
    const envelope = (body as { detail?: unknown })?.detail ?? body;
    const err = envelope as { ok?: boolean; error?: { code: string; message: string; advice?: string } };
    if (err && err.ok === false && err.error) {
      throw new Pdf2SkillError(err.error.code, err.error.message, err.error.advice || '', 'mineru');
    }
    return envelope as Record<string, unknown>;
  }

  /** Request a signed upload URL (POST /file-urls/batch through the proxy). */
  private async requestUploadUrl(filename: string): Promise<{ batchId: string; uploadUrl: string }> {
    const remote = this.config.mineru.remote;
    const filePayload: Record<string, unknown> = {
      name: filename,
      is_ocr: remote.is_ocr,
      enable_formula: remote.enable_formula,
      enable_table: remote.enable_table,
      language: this.config.mineru.language,
    };
    if (remote.page_ranges) filePayload.page_ranges = remote.page_ranges;

    const payload: Record<string, unknown> = {
      files: [filePayload],
      model_version: remote.model_version,
    };
    if (remote.extra_formats?.length) payload.extra_formats = remote.extra_formats;

    const data = await this.proxyJson(this.api('/api/mineru/file-urls/batch'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (data.code !== 0) {
      throw mineruError(data.code as string, String(data.msg ?? ''));
    }
    const result = data.data as { batch_id: string; file_urls: string[] };
    return { batchId: result.batch_id, uploadUrl: result.file_urls[0] };
  }

  /** PUT the PDF bytes to the signed URL (streamed through the proxy). */
  private async uploadPdf(uploadUrl: string, bytes: Uint8Array): Promise<void> {
    let resp: Response;
    try {
      resp = await fetch(
        `${this.api('/api/mineru/upload')}?url=${encodeURIComponent(uploadUrl)}`,
        { method: 'PUT', body: new Blob([bytes as BlobPart]), signal: this.opts.signal },
      );
    } catch (e) {
      if ((e as Error).name === 'AbortError') throw e;
      throw new Pdf2SkillError('MINERU_UPLOAD', '上传 PDF 到 MinerU 失败', '请检查网络连接后重试', 'mineru', e);
    }
    const body = (await resp.json().catch(() => null)) as
      | { ok?: boolean; error?: { code: string; message: string; advice?: string }; detail?: unknown }
      | null;
    const envelope = (body?.detail ?? body) as
      | { ok?: boolean; error?: { code: string; message: string; advice?: string } }
      | undefined;
    if (envelope && envelope.ok === false && envelope.error) {
      throw new Pdf2SkillError(envelope.error.code, envelope.error.message, envelope.error.advice || '', 'mineru');
    }
    if (!resp.ok && !envelope?.ok) {
      throw new Pdf2SkillError('MINERU_UPLOAD', `上传 PDF 到 MinerU 失败 (HTTP ${resp.status})`, '请重试', 'mineru');
    }
  }

  /** Poll until the batch completes (10s interval, 1800s timeout). */
  private async waitForCompletion(batchId: string, timeoutMs = 1800_000): Promise<FileResult> {
    const url = this.api(`/api/mineru/extract-results/batch/${encodeURIComponent(batchId)}`);
    const start = Date.now();
    for (;;) {
      if (Date.now() - start > timeoutMs) {
        throw new Pdf2SkillError('MINERU_TIMEOUT', 'MinerU conversion timed out', '任务超时（30 分钟），请重试或拆分文件', 'mineru');
      }
      const data = await this.proxyJson(url);
      if (data.code !== 0) {
        throw mineruError(data.code as string, String(data.msg ?? ''));
      }
      const results = (data.data as { extract_result?: FileResult[] }).extract_result ?? [];
      if (results.length === 0) {
        this.emitProgress('pending');
        await this.sleep(10_000);
        continue;
      }
      const fileResult = results[0];
      if (fileResult.state === 'done') {
        this.emitProgress('done', 1, 1);
        return fileResult;
      }
      if (fileResult.state === 'failed') {
        throw mineruError(fileResult.err_code, fileResult.err_msg ?? 'Unknown error');
      }
      const progress = fileResult.extract_progress ?? {};
      this.emitProgress(
        fileResult.state,
        progress.extracted_pages ?? 0,
        progress.total_pages ?? 0,
      );
      await this.sleep(10_000);
    }
  }

  private async sleep(ms: number): Promise<void> {
    await new Promise((resolve, reject) => {
      const t = setTimeout(resolve, ms);
      this.opts.signal?.addEventListener('abort', () => {
        clearTimeout(t);
        reject(new DOMException('Aborted', 'AbortError'));
      }, { once: true });
    });
  }

  private async downloadZip(zipUrl: string): Promise<Uint8Array<ArrayBuffer>> {
    let resp: Response;
    try {
      resp = await fetch(`${this.api('/api/mineru/download')}?url=${encodeURIComponent(zipUrl)}`, {
        signal: this.opts.signal,
      });
    } catch (e) {
      if ((e as Error).name === 'AbortError') throw e;
      throw new Pdf2SkillError('MINERU_DOWNLOAD', '下载 MinerU 结果文件超时或失败', '请检查网络后重试', 'mineru', e);
    }
    if (!resp.ok) {
      const errBody = (await resp.json().catch(() => null)) as
        | { detail?: { error?: { code: string; message: string; advice?: string } } }
        | null;
      if (errBody?.detail?.error) {
        throw new Pdf2SkillError(
          errBody.detail.error.code,
          errBody.detail.error.message,
          errBody.detail.error.advice ?? '',
          'mineru',
        );
      }
      throw new Pdf2SkillError('MINERU_DOWNLOAD', `下载 MinerU 结果失败 (HTTP ${resp.status})`, '请重试', 'mineru');
    }
    return new Uint8Array(await resp.arrayBuffer());
  }

  /**
   * Convert one PDF part: upload → poll → download → extract into
   * ``jobRoot/{partStem}`` (zip saved alongside), images copied to
   * ``jobRoot/images``. Returns the raw Markdown text.
   */
  private async processSinglePdf(
    partName: string,
    partBytes: Uint8Array,
    jobRoot: FileSystemDirectoryHandle,
  ): Promise<string> {
    const { batchId, uploadUrl } = await this.requestUploadUrl(partName);
    await this.uploadPdf(uploadUrl, partBytes);
    const result = await this.waitForCompletion(batchId);
    const zipUrl = result.full_zip_url;
    if (!zipUrl) {
      throw new Pdf2SkillError('MINERU_API', 'MinerU 结果缺少 full_zip_url', '请重试', 'mineru');
    }
    const zipBytes = await this.downloadZip(zipUrl);

    const partStem = partName.replace(/\.pdf$/i, '');
    const extractDir = await ensureDir(jobRoot, partStem);
    await writeBytes(extractDir, 'remote_mineru_output.zip', zipBytes);
    const { mdText, mdPath } = await extractMineruZip(zipBytes, extractDir);

    // Copy images next to the md file into jobRoot/images (Python: output_dir.parent/images)
    const mdSegs = mdPath.split('/').filter(Boolean);
    mdSegs.pop();
    const imgDir = await getDir(extractDir, mdSegs.concat('images').join('/'));
    if (imgDir) {
      const targetImgDir = await ensureDir(jobRoot, 'images');
      await copyDirFiles(imgDir, targetImgDir);
    }
    return mdText;
  }

  /**
   * Full Stage 1 conversion: split if over the page limit, process each part,
   * concatenate with ``\n\n---\n\n# Part N`` separators. Returns the combined
   * Markdown text. The caller is responsible for the full.md idempotency check.
   */
  async process(
    pdfBytes: Uint8Array,
    filename: string,
    jobRoot: FileSystemDirectoryHandle,
  ): Promise<string> {
    const pageLimit = this.config.pdf.page_limit;
    const parts = await splitPdfIfNeeded(pdfBytes, filename, pageLimit);
    if (parts.length > 1) {
      // Python: _split_pdf_if_needed writes parts to output_dir/splits/ (only when over the limit).
      const splitsDir = await ensureDir(jobRoot, 'splits');
      for (const part of parts) await writeBytes(splitsDir, part.name, part.bytes);
    }
    const mdContents: string[] = [];
    for (let i = 0; i < parts.length; i++) {
      const part = parts[i];
      const content = await this.processSinglePdf(part.name, part.bytes, jobRoot);
      if (i > 0) {
        mdContents.push('\n\n---\n\n');
        mdContents.push(`# Part ${i + 1}\n\n`);
      }
      mdContents.push(content);
    }
    return mdContents.join('');
  }
}
