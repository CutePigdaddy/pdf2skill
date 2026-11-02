import { fileExists, readText, writeTextAtomic } from '../storage/opfs';

/** Bump when the peel prompt or split algorithm changes. */
export const PEEL_CACHE_VERSION = 1;

export interface PeelCacheConfig {
  chunkMaxTokens: number;
  chunkMaxIterations: number;
  chunkAnchorLength: number;
  chunkMinThreshold: number;
}

export interface PeelCacheKeyInput {
  content: string;
  title: string;
  parentPath: string[];
  startLine: number;
  endLine: number;
  iteration: number;
  config: PeelCacheConfig;
}

export async function peelCacheKey(input: PeelCacheKeyInput): Promise<string> {
  const payload = JSON.stringify({
    v: PEEL_CACHE_VERSION,
    content: input.content,
    title: input.title,
    parentPath: input.parentPath,
    startLine: input.startLine,
    endLine: input.endLine,
    iteration: input.iteration,
    config: [
      input.config.chunkMaxTokens,
      input.config.chunkMaxIterations,
      input.config.chunkAnchorLength,
      input.config.chunkMinThreshold,
    ],
  });
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(payload));
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('');
}

export async function loadPeelCache(
  dir: FileSystemDirectoryHandle,
  key: string,
): Promise<Record<string, unknown> | null> {
  const name = `${key}.json`;
  try {
    if (!(await fileExists(dir, name))) return null;
    const parsed = JSON.parse(await readText(dir, name)) as {
      version?: unknown;
      node?: unknown;
    } | null;
    if (!parsed || parsed.version !== PEEL_CACHE_VERSION) return null;
    if (typeof parsed.node !== 'object' || parsed.node === null || Array.isArray(parsed.node)) {
      return null;
    }
    return parsed.node as Record<string, unknown>;
  } catch {
    return null;
  }
}

export async function savePeelCache(
  dir: FileSystemDirectoryHandle,
  key: string,
  node: Record<string, unknown>,
): Promise<void> {
  await writeTextAtomic(
    dir,
    `${key}.json`,
    JSON.stringify({ version: PEEL_CACHE_VERSION, node }),
  );
}
