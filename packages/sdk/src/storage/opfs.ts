import { Pdf2SkillError } from '../errors';

/** Get the OPFS root, throwing a structured error when unavailable. */
export async function getOpfsRoot(): Promise<FileSystemDirectoryHandle> {
  const nav = navigator as Navigator & {
    storage?: { getDirectory?: () => Promise<FileSystemDirectoryHandle> };
  };
  if (!nav.storage?.getDirectory) {
    throw new Pdf2SkillError(
      'OPFS_UNAVAILABLE',
      '当前浏览器不支持 OPFS 存储',
      '请使用 Chrome / Edge / Firefox / Safari 15.2+ 等现代浏览器',
    );
  }
  return nav.storage.getDirectory();
}

/** Resolve (and create) a nested directory under a base handle. */
export async function ensureDir(
  base: FileSystemDirectoryHandle,
  path: string,
): Promise<FileSystemDirectoryHandle> {
  let dir = base;
  for (const seg of path.split('/').filter(Boolean)) {
    dir = await dir.getDirectoryHandle(seg, { create: true });
  }
  return dir;
}

export async function getDir(
  base: FileSystemDirectoryHandle,
  path: string,
): Promise<FileSystemDirectoryHandle | null> {
  let dir = base;
  for (const seg of path.split('/').filter(Boolean)) {
    try {
      dir = await dir.getDirectoryHandle(seg);
    } catch {
      return null;
    }
  }
  return dir;
}

export async function fileExists(dir: FileSystemDirectoryHandle, name: string): Promise<boolean> {
  try {
    await dir.getFileHandle(name);
    return true;
  } catch {
    return false;
  }
}

export async function dirExists(dir: FileSystemDirectoryHandle, path: string): Promise<boolean> {
  return (await getDir(dir, path)) !== null;
}

export async function writeBytes(
  dir: FileSystemDirectoryHandle,
  name: string,
  data: Uint8Array | Blob | ArrayBuffer,
): Promise<void> {
  const handle = await dir.getFileHandle(name, { create: true });
  const writable = await handle.createWritable();
  await writable.write(data as FileSystemWriteChunkType);
  await writable.close();
}

export async function writeText(
  dir: FileSystemDirectoryHandle,
  name: string,
  text: string,
): Promise<void> {
  await writeBytes(dir, name, new TextEncoder().encode(text));
}

/**
 * Write text through a temp file and atomically replace the target when the
 * browser exposes FileSystemFileHandle.move (mirrors Python's
 * ``.json.tmp`` + ``os.replace``); otherwise fall back to a direct write.
 */
export async function writeTextAtomic(
  dir: FileSystemDirectoryHandle,
  name: string,
  text: string,
): Promise<void> {
  const tmpName = `${name}.tmp`;
  try {
    const tmp = await dir.getFileHandle(tmpName, { create: true });
    const writable = await tmp.createWritable();
    await writable.write(new TextEncoder().encode(text));
    await writable.close();
    const move = (tmp as FileSystemFileHandle & {
      move?: (parent: FileSystemDirectoryHandle, newName: string) => Promise<void>;
    }).move;
    if (typeof move === 'function') {
      await move.call(tmp, dir, name);
      return;
    }
  } catch {
    // Fall through to the direct write below.
  }
  await writeText(dir, name, text);
  await removeEntry(dir, tmpName).catch(() => {});
}

export async function readBytes(
  dir: FileSystemDirectoryHandle,
  name: string,
): Promise<Uint8Array> {
  const handle = await dir.getFileHandle(name);
  const file = await handle.getFile();
  return new Uint8Array(await file.arrayBuffer());
}

export async function readText(dir: FileSystemDirectoryHandle, name: string): Promise<string> {
  const handle = await dir.getFileHandle(name);
  const file = await handle.getFile();
  return file.text();
}

export async function readBlob(
  dir: FileSystemDirectoryHandle,
  name: string,
): Promise<Blob> {
  const handle = await dir.getFileHandle(name);
  return handle.getFile();
}

export interface DirEntry {
  name: string;
  kind: 'file' | 'directory';
}

export async function listDir(dir: FileSystemDirectoryHandle): Promise<DirEntry[]> {
  const entries: DirEntry[] = [];
  for await (const [name, handle] of dir) {
    entries.push({ name, kind: handle.kind === 'file' ? 'file' : 'directory' });
  }
  entries.sort((a, b) => a.name.localeCompare(b.name));
  return entries;
}

export async function removeEntry(
  dir: FileSystemDirectoryHandle,
  name: string,
  recursive = true,
): Promise<void> {
  await dir.removeEntry(name, { recursive });
}

/** Copy every file from srcDir into dstDir (flat copy, overwrites). */
export async function copyDirFiles(
  srcDir: FileSystemDirectoryHandle,
  dstDir: FileSystemDirectoryHandle,
): Promise<number> {
  let count = 0;
  for await (const [name, handle] of srcDir) {
    if (handle.kind !== 'file') continue;
    const src = await (handle as FileSystemFileHandle).getFile();
    await writeBytes(dstDir, name, new Uint8Array(await src.arrayBuffer()));
    count++;
  }
  return count;
}
