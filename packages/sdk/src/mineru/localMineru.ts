import type { Pdf2SkillConfig } from '../types';
import { Pdf2SkillError } from '../errors';
import { extractMineruZip } from './zipExtract';
import { ensureDir, writeBytes, copyDirFiles, getDir } from '../storage/opfs';

export interface MinerUClientOptions {
  signal?: AbortSignal;
  onProgress?: (p: { phase: string; pct: number; extracted: number; total: number }) => void;
}

/**
 * Port of core/pdf_processor.py::LocalMinerUProcessor. The browser uploads
 * the PDF to the proxy backend, which invokes the self-hosted MinerU Gradio
 * endpoint (via gradio_client) and streams the result ZIP back.
 */
export class LocalMinerUClient {
  private readonly config: Pdf2SkillConfig;
  private readonly opts: MinerUClientOptions;

  constructor(config: Pdf2SkillConfig, opts: MinerUClientOptions = {}) {
    this.config = config;
    this.opts = opts;
  }

  private api(path: string): string {
    return `${this.config.backendBaseUrl}${path}`;
  }

  /**
   * Convert a PDF via the self-hosted MinerU Gradio endpoint (no splitting,
   * matching Python behaviour). Extracts the ZIP into jobRoot and copies
   * images to jobRoot/images. Returns the Markdown text.
   */
  async process(
    pdfBytes: Uint8Array,
    filename: string,
    jobRoot: FileSystemDirectoryHandle,
  ): Promise<string> {
    const local = this.config.mineru.local;
    const form = new FormData();
    form.append('file', new Blob([pdfBytes as BlobPart], { type: 'application/pdf' }), filename);
    form.append('base_url', local.base_url);
    form.append('backend', local.backend);
    form.append('formula_enable', String(local.formula_enable));
    form.append('table_enable', String(local.table_enable));
    form.append('language', this.config.mineru.language);

    let resp: Response;
    try {
      resp = await fetch(this.api('/api/mineru/local'), {
        method: 'POST',
        body: form,
        signal: this.opts.signal,
      });
    } catch (e) {
      if ((e as Error).name === 'AbortError') throw e;
      throw new Pdf2SkillError(
        'MINERU_LOCAL_CONN',
        `无法通过代理连接本地 MinerU 服务 (${local.base_url})`,
        '请确认自部署 MinerU 服务正在运行且地址正确',
        'mineru',
        e,
      );
    }

    const errBody = (await resp.json().catch(() => null)) as
      | { detail?: { error?: { code: string; message: string; advice?: string } } }
      | null;
    if (!resp.ok && errBody?.detail?.error) {
      const err = errBody.detail.error;
      throw new Pdf2SkillError(err.code, err.message, err.advice ?? '', 'mineru');
    }
    if (!resp.ok) {
      throw new Pdf2SkillError(
        'MINERU_LOCAL_PREDICT',
        `本地 MinerU 请求失败 (HTTP ${resp.status})`,
        '请检查自部署 MinerU 服务日志',
        'mineru',
      );
    }

    const zipBytes = new Uint8Array(await resp.arrayBuffer());
    const stem = filename.replace(/\.pdf$/i, '');
    await writeBytes(jobRoot, `${stem}_mineru_output.zip`, zipBytes);

    const { mdText, mdPath } = await extractMineruZip(zipBytes, jobRoot);

    // Copy images next to the md file into jobRoot/images — unless the md
    // file sits directly in jobRoot (source and target are then the same dir).
    const mdSegs = mdPath.split('/').filter(Boolean);
    mdSegs.pop();
    const imgPath = mdSegs.concat('images').join('/');
    if (imgPath !== 'images') {
      const imgDir = await getDir(jobRoot, imgPath);
      if (imgDir) {
        const targetImgDir = await ensureDir(jobRoot, 'images');
        await copyDirFiles(imgDir, targetImgDir);
      }
    }
    return mdText;
  }
}
