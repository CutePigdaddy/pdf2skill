import skillParadigmPrompt from '../prompts/skill_paradigm.txt?raw';
import bookSummaryPrompt from '../prompts/book_summary.txt?raw';
import skillNamePrompt from '../prompts/skill_name.txt?raw';
import type { LlmTransport } from './llmTransport';
import { ChunkNode } from './treeMerger';
import { charCount, codePoints } from './levenshtein';
import { validateSkillName } from '../naming';
import { CheckpointManager } from '../storage/checkpoint';
import { dirExists, ensureDir, fileExists, getDir, writeText, writeBytes } from '../storage/opfs';

export interface SkillEngineOptions {
  /** Job root directory (parent of the skill output dir; images live at `<jobRoot>/images`). */
  jobRoot?: FileSystemDirectoryHandle;
  /**
   * User-specified skill name (already validated as kebab-case). When set it
   * becomes the SKILL.md `name` verbatim and skips LLM name generation.
   */
  skillName?: string;
  /** Progress callback for chunk tagging (idx is 1-based). */
  onProgress?: (idx: number, total: number, title: string) => void;
  /** Logger for warnings/errors (defaults to console). */
  log?: (msg: string) => void;
}

/**
 * Stage 4: skill generation. Port of core/skill_engine.py::SkillEngine.
 * Tags each leaf chunk with keywords via the LLM, writes reference files and
 * the master SKILL.md index. Fine-grained checkpointing (book_overview,
 * summaries, skill_name) mirrors the Python semantics exactly.
 */
export class SkillEngine {
  private readonly outputDir: FileSystemDirectoryHandle;
  private readonly llm: LlmTransport;
  private checkpoint!: CheckpointManager;
  private readonly opts: SkillEngineOptions;
  private readonly prompts: { skill: string; book: string; name: string };

  constructor(
    outputDir: FileSystemDirectoryHandle,
    llm: LlmTransport,
    opts: SkillEngineOptions = {},
  ) {
    this.outputDir = outputDir;
    this.llm = llm;
    this.opts = opts;
    // Python read_text() normalizes \r\n/\r to \n.
    const norm = (s: string) => s.replace(/\r\n|\r/g, '\n');
    this.prompts = {
      skill: norm(skillParadigmPrompt),
      book: norm(bookSummaryPrompt),
      name: norm(skillNamePrompt),
    };
  }

  private log(msg: string): void {
    (this.opts.log ?? console.log)(msg);
  }

  /** Flatten the tree to leaf nodes (excluding master/root). */
  private collectFlatNodes(node: ChunkNode): ChunkNode[] {
    const nodes: ChunkNode[] = [];
    if (node.id && node.id !== 'master' && node.id !== 'root' && node.children.length === 0) {
      nodes.push(node);
    }
    for (const child of node.children) {
      nodes.push(...this.collectFlatNodes(child));
    }
    return nodes;
  }

  /** Kebab-case validation per the Claude Code skill spec. */
  static validateKebabName(name: string): boolean {
    return validateSkillName(name).length === 0;
  }

  /** LLM name → regex conversion → timestamp fallback chain. */
  private async generateSkillName(
    bookTitle: string,
    overview: string,
    toc: string,
  ): Promise<string> {
    try {
      const prompt = formatTemplate(this.prompts.name, {
        book_title: bookTitle,
        overview: codePoints(overview).slice(0, 1000).join(''),
        toc: codePoints(toc).slice(0, 2000).join(''),
      });
      const raw = (await this.llm.chat(prompt)).trim();
      const candidate = raw.split('\n')[0].trim();
      if (SkillEngine.validateKebabName(candidate)) {
        this.log(`LLM-generated skill name: ${candidate}`);
        return candidate;
      }
      this.log(`LLM name '${candidate}' failed validation, falling back to regex`);
    } catch (e) {
      if ((e as Error).name === 'AbortError') throw e;
      this.log(`LLM name generation failed: ${e}, falling back to regex`);
    }

    let kebab = bookTitle
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '');
    kebab = kebab.replace(/-+/g, '-').slice(0, 64).replace(/-+$/, '');
    if (SkillEngine.validateKebabName(kebab)) return kebab;

    const fallback = `skill-${Math.floor(Date.now() / 1000)}`;
    this.log(`Name fallback to timestamp: ${fallback}`);
    return fallback;
  }

  /**
   * Orchestrate skill generation: book overview → per-chunk tagging →
   * reference files → SKILL.md index. Checkpoint-backed for resume.
   */
  async generate(rootNode: ChunkNode, bookTitle: string): Promise<void> {
    this.log(`Generating SKILL mapped index for '${bookTitle}'`);
    this.checkpoint = new CheckpointManager(this.outputDir);
    await this.checkpoint.load();

    const refDir = await ensureDir(this.outputDir, 'references');

    // 0. Copy images directory (idempotent)
    const sourceImgDir = this.opts.jobRoot ? await getDir(this.opts.jobRoot, 'images') : null;
    if (sourceImgDir && !(await dirExists(refDir, 'images'))) {
      const targetImgDir = await ensureDir(refDir, 'images');
      await copyTree(sourceImgDir, targetImgDir);
      this.log('Copied images into references/images');
    }

    const allNodes = this.collectFlatNodes(rootNode);

    // TOC preview (needed on fresh runs and resume alike)
    const tocLines = allNodes.map((n) => `${'  '.repeat(n.parentPath.length)}- ${n.title}`);

    // 1. Book overview
    let bookOverview = '';
    let metadataDesc = '';
    if (!this.checkpoint.isStageCompleted('book_overview')) {
      const tocText = tocLines.join('\n');
      const prompt = formatTemplate(this.prompts.book, {
        book_title: bookTitle,
        toc: codePoints(tocText).slice(0, 4000).join(''),
      });
      const rawOverview = (await this.llm.chat(prompt)).trim();

      const metaMatch = /\[METADATA_DESC:\s*([\s\S]*?)\]/.exec(rawOverview);
      metadataDesc = metaMatch ? metaMatch[1].trim() : '';

      bookOverview = rawOverview.replace(/\[METADATA_DESC:[\s\S]*?\]/g, '').trim();

      await this.checkpoint.markStageCompleted('book_overview', {
        overview: bookOverview,
        metadata_desc: metadataDesc,
      });
    } else {
      const stageData = (this.checkpoint.getStageData('book_overview') as Record<string, string>) ?? {};
      bookOverview = stageData.overview ?? '';
      metadataDesc = stageData.metadata_desc ?? '';
    }

    let summaries = (this.checkpoint.getStageData('summaries') as Record<string, string>) ?? {};
    // Attach the dict so later checkpoint.save() calls persist tagging progress.
    this.checkpoint.state.data.summaries = summaries;

    // 2. Per-chunk tagging + reference files
    for (let idx = 1; idx <= allNodes.length; idx++) {
      const node = allNodes[idx - 1];
      this.opts.onProgress?.(idx, allNodes.length, node.title);

      // Resume guard requires BOTH the summary and the reference file: a
      // summary persisted before its file was written (crash in between,
      // OPFS quota failure) must not permanently skip the file on resume,
      // which would leave a SKILL.md dead link.
      if (node.id in summaries && (await fileExists(refDir, `${node.id}.md`))) continue;

      if (!(node.id in summaries)) {
        const preview = codePoints(node.content).slice(0, 2000).join('');

        if (!preview.trim()) {
          summaries[node.id] = 'No explicit content';
        } else {
          const prompt = formatTemplate(this.prompts.skill, { content_preview: preview });

          try {
            let tags = (await this.llm.chat(prompt)).trim();
            tags = tags
              .replace(/[;\n；]/g, ' | ')
              .replace(/\s*\|\s*/g, ' | ')
              .replace(/\|\s+\|/g, '|');
            summaries[node.id] = tags;

            // Persistent progress tracking
            this.checkpoint.state.data.summaries = summaries;
            await this.checkpoint.save();
          } catch (e) {
            if ((e as Error).name === 'AbortError') throw e;
            // Not written to summaries so the chunk is retried on re-run
            this.log(`Error tagging chunk ${node.id}: ${e}`);
          }
        }
      }

      // 3. Reference Markdown with frontmatter — written for every node
      // (empty-content ones included) so SKILL.md links never dangle.
      const pathStr = node.parentPath.length ? node.parentPath.join(' > ') : 'Root';
      const metadata = `---
id: ${node.id}
title: ${node.title}
parent_path: ${pathStr}
start_line: ${node.startLine}
end_line: ${node.endLine}
---

`;
      await writeText(refDir, `${node.id}.md`, metadata + node.content);
    }

    // 3. Master index — skill name via user input / LLM + fallback chain
    let kebabName: string;
    if (!this.checkpoint.isStageCompleted('skill_name')) {
      if (this.opts.skillName) {
        kebabName = this.opts.skillName;
        this.log(`Using user-specified skill name: ${kebabName}`);
      } else {
        const tocPreview = tocLines.join('\n');
        kebabName = await this.generateSkillName(bookTitle, bookOverview, tocPreview);
      }
      await this.checkpoint.markStageCompleted('skill_name', { name: kebabName });
    } else {
      kebabName =
        ((this.checkpoint.getStageData('skill_name') as Record<string, string>) ?? {}).name ?? '';
    }

    // Sanitise description for spec compliance
    let skillDescription = metadataDesc
      ? metadataDesc.replace(/\n/g, ' ')
      : bookOverview.replace(/\n/g, ' ').trim();

    skillDescription = skillDescription.replaceAll('<', '').replaceAll('>', '');

    const hasTrigger = ['Use this skill when', '使用此技能', '适用', '请使用此技能'].some((p) =>
      skillDescription.includes(p),
    );
    if (!hasTrigger) {
      skillDescription += ` Use this skill when the user asks about topics covered in '${bookTitle}'.`;
    }

    if (charCount(skillDescription) > 1000) {
      skillDescription = codePoints(skillDescription).slice(0, 997).join('') + '...';
    }

    const referenceLines = allNodes.map((node) => {
      const pathStr = node.parentPath.length
        ? [...node.parentPath, node.title].join(' > ')
        : node.title;
      const summary = summaries[node.id] ?? '';
      return `- [${pathStr}](references/${node.id}.md): ${summary}`;
    });

    const skillContent = `---
name: ${kebabName}
description: ${skillDescription}
---

# ${bookTitle}

## Overview
${bookOverview}

## Reference Guide
${referenceLines.join('\n')}
`;
    await writeText(this.outputDir, 'SKILL.md', skillContent);
    this.log('SKILL mapping completed successfully (SKILL.md)');
  }
}

/** Python str.format-like substitution; unknown keys keep their placeholder. */
function formatTemplate(tpl: string, vars: Record<string, string>): string {
  return tpl.replace(/\{(\w+)\}/g, (m, k: string) => (k in vars ? vars[k] : m));
}

/** Recursive directory copy (Python shutil.copytree equivalent). */
async function copyTree(src: FileSystemDirectoryHandle, dst: FileSystemDirectoryHandle): Promise<void> {
  for await (const [name, handle] of src) {
    if (handle.kind === 'file') {
      const file = await (handle as FileSystemFileHandle).getFile();
      await writeBytes(dst, name, new Uint8Array(await file.arrayBuffer()));
    } else {
      await copyTree(handle as FileSystemDirectoryHandle, await ensureDir(dst, name));
    }
  }
}
