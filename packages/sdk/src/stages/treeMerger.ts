import type { LlmTransport } from './llmTransport';
import { charCount, codePoints, levenshteinCp } from './levenshtein';
import { ensureDir, writeText } from '../storage/opfs';

export class ChunkNode {
  children: ChunkNode[] = [];

  constructor(
    public id: string,
    public title: string,
    public parentPath: string[],
    public content: string = '',
    public startLine: number = 0,
    public endLine: number = 0,
    public iteration: number = 0,
    public isAtomic: boolean = false,
  ) {}

  toDict(): Record<string, unknown> {
    return {
      id: this.id,
      title: this.title,
      parent_path: this.parentPath,
      children: this.children.map((c) => c.toDict()),
      content: this.content,
      line_range: [this.startLine, this.endLine],
      iteration: this.iteration,
      is_atomic: this.isAtomic,
    };
  }

  static fromDict(data: Record<string, unknown>): ChunkNode {
    const lineRange = (data.line_range as number[]) ?? [0, 0];
    const node = new ChunkNode(
      data.id as string,
      data.title as string,
      (data.parent_path as string[]) ?? [],
      (data.content as string) ?? '',
      lineRange[0] ?? 0,
      lineRange[1] ?? 0,
      (data.iteration as number) ?? 0,
      (data.is_atomic as boolean) ?? false,
    );
    node.children = ((data.children as Record<string, unknown>[]) ?? []).map((c) =>
      ChunkNode.fromDict(c),
    );
    return node;
  }
}

export interface TreeMergerConfig {
  chunkMaxTokens: number;
  chunkMaxIterations: number;
  chunkAnchorLength: number;
  chunkMinThreshold: number;
}

export interface TreeMergerOptions {
  config?: Partial<TreeMergerConfig>;
  /** Progress callback: base-chunk index (1-based) / total. */
  onProgress?: (idx: number, total: number, chunkId: string) => void;
  /** Log sink for warnings/info (defaults to console). */
  log?: (msg: string) => void;
}

export const DEFAULT_TREE_MERGER_CONFIG: TreeMergerConfig = {
  chunkMaxTokens: 10000,
  chunkMaxIterations: 5,
  chunkAnchorLength: 30,
  chunkMinThreshold: 1000,
};

export interface BaseChunkInput {
  content: string;
  start_line?: number;
  end_line?: number;
  is_atomic?: boolean;
}

const fmt = (n: number): string => n.toLocaleString('en-US');

/**
 * Stage 3: TOC drilling & merging. Port of core/tree_merger.py::TreeMerger.
 * Recursively "peels" large chunks into smaller logical sub-chunks by
 * identifying sub-headers using an LLM, until all chunks are within the
 * token limit.
 */
export class TreeMerger {
  readonly config: TreeMergerConfig;
  peelErrors = 0;
  private chunkCounter = 0;
  private readonly llm: LlmTransport;
  private readonly onProgress?: (idx: number, total: number, chunkId: string) => void;
  private readonly logFn: (msg: string) => void;

  constructor(llm: LlmTransport, opts: TreeMergerOptions | Partial<TreeMergerConfig> = {}) {
    // Accept either a plain config patch (back-compat) or full options.
    if ('config' in opts || 'onProgress' in opts || 'log' in opts) {
      const o = opts as TreeMergerOptions;
      this.config = { ...DEFAULT_TREE_MERGER_CONFIG, ...o.config };
      this.onProgress = o.onProgress;
      this.logFn = o.log ?? console.log;
    } else {
      this.config = { ...DEFAULT_TREE_MERGER_CONFIG, ...(opts as Partial<TreeMergerConfig>) };
      this.logFn = console.log;
    }
    this.llm = llm;
  }

  private generateChunkId(): string {
    this.chunkCounter += 1;
    return `chunk_${String(this.chunkCounter).padStart(4, '0')}`;
  }

  estimateTokens(text: string): number {
    return Math.trunc(charCount(text) / 2);
  }

  /**
   * Locate a sub-header anchor in the text using exact then fuzzy matching
   * (Levenshtein). Skips fuzzy matching for content over 50k chars and limits
   * the fuzzy search window to the first/last 20% for content over 20k chars.
   * All positions are code-point offsets (matching Python string semantics).
   */
  findAnchorPosition(content: string, anchor: string): number {
    anchor = anchor.trim();
    if (!anchor) return -1;

    const cps = codePoints(content);
    const anchorCps = codePoints(anchor);

    // Exact search (code-point indexed)
    const exact = indexOfCp(cps, anchorCps);
    if (exact !== -1) return exact;

    const contentLen = cps.length;
    const anchorLen = anchorCps.length;
    if (contentLen > 50000) {
      this.logFn(`Content too large (${contentLen} chars) for fuzzy anchor matching, skipping`);
      return -1;
    }

    let bestPos = -1;
    let bestDistance = Infinity;
    const threshold = Math.max(2, Math.trunc(anchorLen / 5));

    let searchRanges: Array<[number, number]>;
    if (contentLen > 20000) {
      searchRanges = [
        [0, Math.trunc(contentLen * 0.2)],
        [Math.trunc(contentLen * 0.8), contentLen],
      ];
    } else {
      searchRanges = [[0, contentLen]];
    }

    for (const [rngStart, rngEnd] of searchRanges) {
      const limit = Math.min(rngEnd, contentLen - anchorLen + 1);
      for (let i = rngStart; i < limit; i++) {
        const window = cps.slice(i, i + anchorLen);
        const dist = levenshteinCp(anchorCps, window);
        if (dist < bestDistance && dist <= threshold) {
          bestDistance = dist;
          bestPos = i;
        }
      }
    }

    return bestPos;
  }

  /** Recursively split a ChunkNode if it exceeds token limits. */
  async recursivePeel(chunk: ChunkNode, currentIteration = 2): Promise<ChunkNode[]> {
    if (chunk.isAtomic) {
      this.logFn(`  [Atomic] ${chunk.title}`);
      return [chunk];
    }

    if (currentIteration > this.config.chunkMaxIterations) {
      this.logFn(
        `  Max iterations (${this.config.chunkMaxIterations}) reached for ${chunk.id}. Stopping split.`,
      );
      return [chunk];
    }

    const estimatedTokens = this.estimateTokens(chunk.content);
    if (estimatedTokens <= this.config.chunkMaxTokens) {
      return [chunk];
    }

    console.log(
      `  Peeling ${chunk.id} (iteration ${currentIteration}, ~${fmt(estimatedTokens)} tokens)...`,
    );

    const prompt = this.buildPeelPrompt(chunk, estimatedTokens);

    let anchors: Array<Record<string, unknown>>;
    try {
      const response = await this.llm.chat(prompt);
      if (!response || !response.trim()) {
        this.logFn(`    Empty response from LLM for ${chunk.id}`);
        this.peelErrors += 1;
        return [chunk];
      }

      const jsonMatch = /(\{[\s\S]*\})/.exec(response);
      let result: Record<string, unknown>;
      if (jsonMatch) {
        result = JSON.parse(jsonMatch[1]) as Record<string, unknown>;
      } else {
        result = JSON.parse(response) as Record<string, unknown>;
      }

      if (result.is_atomic) {
        this.logFn(`  [Atomic] ${chunk.title} (AI decision)`);
        chunk.isAtomic = true;
        return [chunk];
      }

      anchors = (result.anchors as Array<Record<string, unknown>>) ?? [];
    } catch (e) {
      // Python catches json.JSONDecodeError, LLMParsingError and
      // requests.RequestException (timeouts). Connection/auth errors crash
      // the stage instead, so only LLM_PARSE/LLM_TIMEOUT are swallowed here.
      if ((e as Error).name === 'AbortError') throw e;
      const code = (e as { code?: string }).code;
      if (code && code !== 'LLM_PARSE' && code !== 'LLM_TIMEOUT') throw e;
      this.logFn(`    Could not parse LLM response: ${e}`);
      this.peelErrors += 1;
      return [chunk];
    }

    if (anchors.length === 0) {
      return [chunk];
    }

    const contentCps = codePoints(chunk.content);
    const splitPositions: number[] = [0];
    const suggestedTitles: Array<string | null> = [];
    for (const anchorInfo of anchors) {
      const anchorText = (anchorInfo.anchor as string) ?? '';
      const pos = this.findAnchorPosition(chunk.content, anchorText);
      if (pos > 0) {
        splitPositions.push(pos);
        suggestedTitles.push(((anchorInfo.suggested_title as string) ?? '') || null);
        this.logFn(
          `    Found anchor at position ${pos}: '${anchorText.slice(0, 30)}...'`,
        );
      } else {
        this.logFn(`    Could not find anchor: '${anchorText.slice(0, 30)}...'`);
      }
    }

    splitPositions.push(contentCps.length);
    while (suggestedTitles.length < splitPositions.length - 1) {
      suggestedTitles.push(null);
    }

    const combined = splitPositions
      .slice(0, -1)
      .map((pos, i) => [pos, suggestedTitles[i]] as const)
      .sort((a, b) => a[0] - b[0]);
    const sortedPositions = combined.map((x) => x[0]).concat(splitPositions[splitPositions.length - 1]);
    const sortedTitles = combined.map((x) => x[1]);

    if (sortedPositions.length <= 2) {
      return [chunk];
    }

    const rawChildChunks: Array<{ content: string; title: string }> = [];
    for (let i = 0; i < sortedPositions.length - 1; i++) {
      const startPos = sortedPositions[i];
      const endPos = sortedPositions[i + 1];
      const subContent = contentCps.slice(startPos, endPos).join('').trim();
      if (!subContent) continue;

      let title =
        i < sortedTitles.length && sortedTitles[i]
          ? (sortedTitles[i] as string)
          : `${chunk.title} (Part ${i + 1})`;

      const headers = [...subContent.matchAll(/^#+\s+([^\n]+)$/gm)].map((m) => m[1]);
      if (headers.length === 1) {
        title = headers[0];
      } else if (
        headers.length > 1 &&
        (!sortedTitles[i] || title.includes('Part'))
      ) {
        title = `${headers[0]} & ${headers[headers.length - 1]}`;
      }

      rawChildChunks.push({ content: subContent, title });
    }

    // Tiny chunk auto-merging
    const minThreshold = this.config.chunkMinThreshold;
    const childChunks: ChunkNode[] = [];

    let i = 0;
    while (i < rawChildChunks.length) {
      const current = rawChildChunks[i];

      if (charCount(current.content) < minThreshold) {
        if (i + 1 < rawChildChunks.length) {
          const nextChunk = rawChildChunks[i + 1];
          nextChunk.content = current.content + '\n\n' + nextChunk.content;
          if (!nextChunk.title.includes(' & ')) {
            nextChunk.title = `${current.title} & ${nextChunk.title}`;
          }
          i += 1;
          continue;
        } else if (childChunks.length > 0) {
          const prevChunk = childChunks[childChunks.length - 1];
          prevChunk.content += '\n\n' + current.content;
          if (!prevChunk.title.includes(' & ')) {
            prevChunk.title = `${prevChunk.title} & ${current.title}`;
          }
          i += 1;
          continue;
        }
      }

      childChunks.push(
        new ChunkNode(
          this.generateChunkId(),
          current.title,
          [...chunk.parentPath, chunk.title],
          current.content,
          chunk.startLine,
          chunk.endLine,
          currentIteration,
          false,
        ),
      );
      i += 1;
    }

    const finalChunks: ChunkNode[] = [];
    for (const child of childChunks) {
      finalChunks.push(...(await this.recursivePeel(child, currentIteration + 1)));
    }

    chunk.children = childChunks;

    return finalChunks;
  }

  private buildPeelPrompt(chunk: ChunkNode, estimatedTokens: number): string {
    const maxIter = this.config.chunkMaxTokens;
    const anchorLen = this.config.chunkAnchorLength;
    const parentPath = chunk.parentPath.length
      ? chunk.parentPath.join(' > ')
      : 'Root';
    const contentPreview = codePoints(chunk.content).slice(0, 40000).join('');
    return `You are a document chunking assistant. Your task is to identify logical split points within this text.

## Chunk Information
- Parent path: ${parentPath}
- Current title: ${chunk.title}
- Content length: ${fmt(charCount(chunk.content))} characters (~${fmt(estimatedTokens)} tokens)
- Target chunk size: ${fmt(maxIter)} tokens

## Content to Split
(Note: Content is truncated or summarized if too long for prompt, focusing on representative sections)
\`\`\`
${contentPreview}
\`\`\`

## Your Task
Identify 2-5 split points (anchors) where this content should be divided.

## Special Focus: Atomic Units
- **ATOMIC RULE**: Identify coherent "Atomic Units" that should NEVER be split internally. These include:
    - **TECHNICAL BLOCKS**: Code listings, large tables, data sheets, mathematical proofs.
    - **INSTRUCTIONAL BLOCKS**: Lab procedures, step-by-step guides, complete recipes.
    - **ASSESSMENT BLOCKS**: Exercise sets, problem sets, quiz questions.
- **LEAD-IN RULE**: If a section ends with an Atomic Unit (e.g., Exercises), you MUST find an anchor at the start of the next section (the next # Header) AND an anchor at the start of the Atomic Unit itself.
- **NO FRAGMENTATION**: Do NOT split an Atomic Unit in the middle. If a chunk consists primarily of one Atomic Unit and exceeds the token limit, mark \`is_atomic: true\` rather than breaking the sequence.
- **DENSITY**: Avoid creating chunks smaller than 100 characters unless it is the very end of a chapter.

Rules:
1. SPLIT POINTS: Choose anchors at logical boundaries (between sections, topics, paragraphs).
2. The anchor should be EXACTLY ${anchorLen} characters from the text (including punctuation and spaces).
3. Do NOT hallucinate anchors. The anchor MUST exist in the provided text snippet.
4. IMPORTANT: Only use text from the FIRST 40,000 characters provided above to find anchors. If the text is longer than that, simply find anchors as close to the 20,000 - 35,000 char range as possible.
5. GENERATE TITLES: For each resulting sub-chunk (the parts between anchors), suggest a descriptive title. If a sub-chunk contains multiple headers, generate a title like "Section A & Section B" or "Problems (1.1 to 1.5)".

## Output Format
Return a JSON object:
\`\`\`json
{
  "anchors": [
    {
      "anchor": "exactly ${anchorLen} chars from text", 
      "description": "Start of Exercises",
      "suggested_title": "Section 17.4 & Exercises"
    },
    {
      "anchor": "exactly ${anchorLen} chars from text", 
      "description": "Start of Section 17.5",
      "suggested_title": "Section 17.5: Advanced Topics"
    } 
  ],
  "is_atomic": false
}
\`\`\`

IMPORTANT: Use a string of exactly ${anchorLen} characters for each anchor.
Return ONLY the JSON object. No prose.`;
  }

  /** Peel all base chunks and assemble them under a master root. */
  async buildAndMerge(llmChunks: BaseChunkInput[]): Promise<ChunkNode> {
    this.peelErrors = 0;
    const masterRoot = new ChunkNode('master', 'Document', []);

    for (let idx = 0; idx < llmChunks.length; idx++) {
      const c = llmChunks[idx];
      let title = 'Document Section';
      const content = c.content;
      const lines = content.trim().split('\n');
      for (const line of lines) {
        const headerMatch = /^(#{1,6})\s+([^\n]+)$/.exec(line);
        if (headerMatch) {
          title = headerMatch[2].trim();
          break;
        }
      }

      let isAtomic = c.is_atomic ?? false;
      if (!isAtomic) {
        const contentLower = content.toLowerCase();
        if (/^(#+)\s+(目录|table of contents|toc)$/m.test(contentLower)) {
          isAtomic = true;
          this.logFn(`  Fallback: Auto-detected TOC as ATOMIC for chunk ${idx}`);
        }
      }

      const node = new ChunkNode(
        this.generateChunkId(),
        title,
        [],
        content,
        c.start_line ?? 0,
        c.end_line ?? 0,
        0,
        isAtomic,
      );

      await this.recursivePeel(node);
      masterRoot.children.push(node);
      this.onProgress?.(idx + 1, llmChunks.length, node.id);
    }

    return masterRoot;
  }

  /**
   * Save the final peeled chunk tree: tree.json, chunks/*.md and
   * chunks_index.json under `outputDir`. Only leaf nodes become flat files.
   */
  async saveResults(outputDir: FileSystemDirectoryHandle, root: ChunkNode): Promise<void> {
    const flatChunks: ChunkNode[] = [];

    const collect = (node: ChunkNode): void => {
      if (node.id !== 'root' && node.id !== 'master' && node.children.length === 0) {
        flatChunks.push(node);
        return;
      }
      for (const child of node.children) collect(child);
    };

    collect(root);
    await this.writeChunkFiles(outputDir, flatChunks, { includeTree: true, root });
  }

  /** Save the un-peeled base chunks for reference (full_chunks_original/). */
  async saveOriginalChunks(
    outputDir: FileSystemDirectoryHandle,
    baseChunks: BaseChunkInput[],
  ): Promise<void> {
    const originalNodes = baseChunks.map((c, idx) => {
      const i = idx + 1;
      return new ChunkNode(
        `chunk_${String(i).padStart(4, '0')}`,
        `Original Chunk ${i}`,
        [],
        c.content ?? '',
        c.start_line ?? 0,
        c.end_line ?? 0,
        1,
        c.is_atomic ?? false,
      );
    });
    await this.writeChunkFiles(outputDir, originalNodes, {});
  }

  private async writeChunkFiles(
    outputDir: FileSystemDirectoryHandle,
    chunks: ChunkNode[],
    opts: { includeTree?: boolean; root?: ChunkNode },
  ): Promise<void> {
    if (opts.includeTree && opts.root) {
      await writeText(outputDir, 'tree.json', JSON.stringify(opts.root.toDict(), null, 2));
    }

    try {
      await outputDir.removeEntry('chunks');
    } catch {
      // no existing chunks dir
    }
    const chunksDir = await ensureDir(outputDir, 'chunks');

    const chunkIndex: Array<Record<string, unknown>> = [];
    for (const chunk of chunks) {
      const pathStr = chunk.parentPath.length ? chunk.parentPath.join(' > ') : 'Root';
      const metadata = `---
id: ${chunk.id}
title: ${chunk.title}
parent_path: ${pathStr}
start_line: ${chunk.startLine}
end_line: ${chunk.endLine}
iteration: ${chunk.iteration}
tokens: ~${this.estimateTokens(chunk.content)}
---

`;
      await writeText(chunksDir, `${chunk.id}.md`, metadata + chunk.content);

      chunkIndex.push({
        id: chunk.id,
        title: chunk.title,
        parent_path: chunk.parentPath,
        start_line: chunk.startLine,
        end_line: chunk.endLine,
        file: `chunks/${chunk.id}.md`,
        tokens: this.estimateTokens(chunk.content),
      });
    }

    await writeText(outputDir, 'chunks_index.json', JSON.stringify(chunkIndex, null, 2));
  }
}

/** Code-point-indexed substring search. */
function indexOfCp(haystack: readonly string[], needle: readonly string[]): number {
  if (needle.length === 0) return 0;
  const first = needle[0];
  const limit = haystack.length - needle.length;
  outer: for (let i = 0; i <= limit; i++) {
    if (haystack[i] !== first) continue;
    for (let j = 1; j < needle.length; j++) {
      if (haystack[i + j] !== needle[j]) continue outer;
    }
    return i;
  }
  return -1;
}
