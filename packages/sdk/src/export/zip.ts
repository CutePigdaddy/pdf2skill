import JSZip from 'jszip';
import { listDir, readBlob } from '../storage/opfs';

/** SDK bookkeeping files that never appear in a Python outputs/ directory. */
const EXCLUDED_FILES = new Set(['job.json', 'input.pdf', 'input.md']);

async function addDir(zip: JSZip, dir: FileSystemDirectoryHandle, prefix: string): Promise<void> {
  for (const entry of await listDir(dir)) {
    const path = prefix ? `${prefix}/${entry.name}` : entry.name;
    if (entry.kind === 'directory') {
      await addDir(zip, await dir.getDirectoryHandle(entry.name), path);
    } else if (!EXCLUDED_FILES.has(entry.name)) {
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
