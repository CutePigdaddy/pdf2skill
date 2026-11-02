import JSZip from 'jszip';
import { Pdf2SkillError } from '../errors';
import { slugifySkillName, validateSkillName } from '../naming';
import type { SkillZipResult } from '../types';
import {
  fileExists,
  getDir,
  listDir,
  readBlob,
  readText,
} from '../storage/opfs';

/** SDK bookkeeping files that never appear in a Python outputs/ directory. */
const EXCLUDED_FILES = new Set(['job.json', 'input.pdf']);
/** Internal caches are resumable state, not downloadable output. */
const EXCLUDED_DIRS = new Set(['peeling_cache']);
const SKILL_DIR = 'generated_skills';

interface AddDirOptions {
  skipFile?: (name: string) => boolean;
}

async function addDir(
  zip: JSZip,
  dir: FileSystemDirectoryHandle,
  prefix: string,
  options: AddDirOptions = {},
): Promise<void> {
  for (const entry of await listDir(dir)) {
    const path = prefix ? `${prefix}/${entry.name}` : entry.name;
    if (entry.kind === 'directory') {
      if (EXCLUDED_DIRS.has(entry.name)) continue;
      await addDir(zip, await dir.getDirectoryHandle(entry.name), path, options);
    } else if (!EXCLUDED_FILES.has(entry.name) && !options.skipFile?.(entry.name)) {
      const blob = await readBlob(dir, entry.name);
      zip.file(path, blob);
    }
  }
}

/**
 * Package the job directory as a zip Blob mirroring the Python
 * `outputs/<book>/` layout (stage outputs + checkpoint, no input file).
 */
export async function exportJobZip(jobRoot: FileSystemDirectoryHandle): Promise<Blob> {
  const zip = new JSZip();
  await addDir(zip, jobRoot, '');
  return zip.generateAsync({ type: 'blob', compression: 'DEFLATE' });
}

/** Read the `name:` value from SKILL.md's YAML frontmatter. */
export function parseSkillName(markdown: string): string | null {
  const source = markdown.replace(/^\uFEFF/, '');
  const frontmatter = /^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/.exec(source);
  if (!frontmatter) return null;
  const match = /^name:\s*(.+?)\s*$/m.exec(frontmatter[1]);
  if (!match) return null;
  let value = match[1].trim();
  if (value.startsWith('>') || value.startsWith('|')) return null;
  value = value.replace(/\s+#.*$/, '').trim();
  value = value.replace(/^(['"])(.*)\1$/, '$2').trim();
  return value || null;
}

/** Make the skill name safe as a single zip folder segment. */
function safeFolderName(raw: string, fallback: string): string {
  const cleaned = raw
    .replace(/[\\/:*?"<>|\u0000-\u001f]/g, '-')
    .replace(/^\.+/, '')
    .replace(/[. ]+$/g, '')
    .trim();
  return cleaned || fallback;
}

/**
 * Package `generated_skills/` as a ready-to-install skill zip: SKILL.md and
 * references live at the archive root; the download filename uses the
 * SKILL.md `name`.
 */
export async function exportSkillZipBundle(
  jobRoot: FileSystemDirectoryHandle,
  fallbackName: string,
): Promise<SkillZipResult> {
  const skillDir = await getDir(jobRoot, SKILL_DIR);
  if (!skillDir || !(await fileExists(skillDir, 'SKILL.md'))) {
    throw new Pdf2SkillError(
      'SKILL_NOT_FOUND',
      '该任务还没有生成技能库',
      '请先完成 Skill Engine 阶段再下载 Skill zip',
      'skill',
    );
  }

  const markdown = await readText(skillDir, 'SKILL.md');
  const declared = parseSkillName(markdown);
  const name =
    declared && validateSkillName(declared).length === 0
      ? declared
      : slugifySkillName(fallbackName) || fallbackName;
  const skillName = safeFolderName(name, fallbackName);
  const zip = new JSZip();
  await addDir(zip, skillDir, '', {
    skipFile: (name) => name === '.checkpoint.json',
  });
  const blob = await zip.generateAsync({ type: 'blob', compression: 'DEFLATE' });
  return { blob, skillName };
}
