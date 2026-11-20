import JSZip from 'jszip';
import { Pdf2SkillError } from '../errors';

/** Parse a JSON error envelope that arrived with HTTP 200 (proxy bug or
 * misrouted response) before the bytes are mistaken for a ZIP. */
export async function parseJsonErrorBody(bytes: Uint8Array): Promise<
  { code: string; message: string; advice?: string } | null
> {
  const head = new TextDecoder().decode(bytes.slice(0, 512)).trim();
  if (!head.startsWith('{')) return null;
  try {
    const parsed = JSON.parse(head.endsWith('}') ? head : new TextDecoder().decode(bytes)) as {
      ok?: boolean;
      error?: { code: string; message: string; advice?: string };
      detail?: { ok?: boolean; error?: { code: string; message: string; advice?: string } };
    };
    const envelope = (parsed.detail ?? parsed) as typeof parsed;
    if (envelope.ok === false && envelope.error) return envelope.error;
  } catch {
    // Not JSON (or truncated) — fall through and treat as binary.
  }
  return null;
}
import { ensureDir, writeBytes } from '../storage/opfs';

export interface ExtractResult {
  /** Markdown text of the selected md file. */
  mdText: string;
  /** OPFS-relative path of the selected md file inside extractDir. */
  mdPath: string;
}

/**
 * Extract a MinerU result ZIP into an OPFS directory and return the Markdown
 * text. Mirrors _download_and_extract: all entries are extracted, then the
 * "main" md file is selected.
 *
 * Known difference vs Python: Python takes ``rglob('*.md')[-1]`` which
 * depends on filesystem enumeration order; here we take the lexicographically
 * last md path (deterministic across platforms).
 */
export async function extractMineruZip(
  zipBytes: Uint8Array,
  extractDir: FileSystemDirectoryHandle,
): Promise<ExtractResult> {
  let zip: JSZip;
  try {
    zip = await JSZip.loadAsync(zipBytes);
  } catch (e) {
    throw new Pdf2SkillError(
      'MINERU_RESULT_INVALID',
      'MinerU 返回的内容不是有效的 ZIP 文件',
      '通常是下载环节失败，请查看服务端日志中的真实错误后重试',
      'mineru',
      e,
    );
  }

  const mdPaths: string[] = [];
  for (const path of Object.keys(zip.files)) {
    const entry = zip.files[path];
    if (entry.dir) continue;
    // Write every file, creating intermediate directories
    const segs = path.split('/').filter(Boolean);
    const fileName = segs.pop() as string;
    const targetDir = await ensureDir(extractDir, segs.join('/'));
    const bytes = new Uint8Array(await entry.async('arraybuffer'));
    await writeBytes(targetDir, fileName, bytes);
    if (fileName.toLowerCase().endsWith('.md')) {
      mdPaths.push(path);
    }
  }

  if (mdPaths.length === 0) {
    throw new Pdf2SkillError(
      'MINERU_CONVERSION',
      'MinerU succeeded but no Markdown file found.',
      '请检查 MinerU 返回的压缩包内容',
      'mineru',
    );
  }

  mdPaths.sort();
  const mdPath = mdPaths[mdPaths.length - 1];
  const mdText = await zip.files[mdPath].async('string');
  return { mdText, mdPath };
}
