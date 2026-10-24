import re
import shutil
import time
from pathlib import Path
from tqdm import tqdm
from core.tree_merger import ChunkNode
from utils.logger import logger
from utils.llm_client import LLMClient
from utils.naming import validate_skill_name
from config.config import config
from utils.checkpoint import CheckpointManager

class SkillEngine:
    """Stage 4: Skill Generation.

    Iterates through the final hierarchy of chunks and generates
    descriptive SKILL tags for each, along with a master index
    (``SKILL.md``) and individual reference files.
    """
    def __init__(self, output_dir: Path):
        """Initialise the skill engine with LLM client and prompt templates.

        Args:
            output_dir: Directory for generated skill output.
        """
        self.llm = LLMClient(stage="skill_engine")
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.prompts_dir = Path(config.get("paths.prompts_dir", "prompts"))
        self.skill_prompt = (self.prompts_dir / "skill_paradigm.txt").read_text(encoding="utf-8")
        self.book_prompt = (self.prompts_dir / "book_summary.txt").read_text(encoding="utf-8")
        self.name_prompt = (self.prompts_dir / "skill_name.txt").read_text(encoding="utf-8")

    def _collect_flat_nodes(self, node: ChunkNode) -> list[ChunkNode]:
        """Flatten the chunk tree into a flat list of leaf nodes.

        Only leaf chunks (no children) become reference files / index
        entries; intermediate nodes live in tree.json and the
        ``parent_path`` metadata of each leaf.

        Args:
            node: Root ChunkNode to traverse.

        Returns:
            List of all leaf ChunkNode objects (excluding master/root).
        """
        nodes = []
        if (
            getattr(node, 'id', None)
            and node.id not in ("master", "root")
            and not node.children
        ):
            nodes.append(node)
        for child in getattr(node, 'children', []):
            nodes.extend(self._collect_flat_nodes(child))
        return nodes

    @staticmethod
    def _validate_kebab_name(name: str) -> bool:
        """Check whether *name* is a valid kebab-case skill identifier.

        Rules from the Claude Code skill spec:
        - Only ``a-z``, ``0-9``, and hyphens
        - No leading, trailing, or consecutive hyphens
        - Maximum 64 characters
        """
        return not validate_skill_name(name)

    def _generate_skill_name(self, book_title: str, overview: str, toc: str) -> str:
        """Produce a spec-compliant kebab-case skill name.

        Tries the LLM first (via ``skill_name.txt`` prompt), then falls
        back to regex-based conversion, and finally to a timestamp.
        """
        # 1. Try LLM-based generation
        try:
            toc_preview = toc[:2000]
            prompt = self.name_prompt.format(
                book_title=book_title,
                overview=overview[:1000],
                toc=toc_preview,
            )
            raw = self.llm.chat(prompt).strip()
            # Take only the first line in case the LLM adds extra text
            candidate = raw.split("\n")[0].strip()
            if self._validate_kebab_name(candidate):
                logger.info(f"LLM-generated skill name: {candidate}")
                return candidate
            logger.warning(f"LLM name '{candidate}' failed validation, falling back to regex")
        except Exception as e:
            logger.warning(f"LLM name generation failed: {e}, falling back to regex")

        # 2. Fallback: regex-based kebab conversion
        kebab = re.sub(r"[^a-zA-Z0-9]+", "-", book_title.lower()).strip("-")
        # Remove consecutive hyphens and truncate
        kebab = re.sub(r"-+", "-", kebab)[:64].rstrip("-")
        if self._validate_kebab_name(kebab):
            return kebab

        # 3. Last resort: timestamp-based name
        fallback = f"skill-{int(time.time())}"
        logger.warning(f"Name fallback to timestamp: {fallback}")
        return fallback

    def _write_reference_file(self, ref_dir: Path, node: ChunkNode) -> None:
        """Write the per-chunk reference Markdown (frontmatter + content).

        Written for every node — empty-content ones included — so the
        SKILL.md index never links to a missing file. Idempotent.
        """
        ref_file = ref_dir / f"{node.id}.md"
        path_str = ' > '.join(node.parent_path) if node.parent_path else 'Root'
        metadata = f"""---
id: {node.id}
title: {node.title}
parent_path: {path_str}
start_line: {node.start_line}
end_line: {node.end_line}
---

"""
        ref_file.write_text(metadata + node.content, encoding="utf-8")

    def generate(self, root_node: ChunkNode, book_title: str):
        """Orchestrate the full skill generation process.

        1. Summarises the book overview via LLM.
        2. Tags each chunk with keywords/skills.
        3. Saves individual reference Markdown files.
        4. Compiles the master ``SKILL.md`` index.

        Args:
            root_node: Root of the ChunkNode tree from Stage 3.
            book_title: Title used for the skill index name and frontmatter.
        """
        logger.info(f"Generating SKILL mapped index for '{book_title}'")
        checkpoint = CheckpointManager(self.output_dir)
        
        ref_dir = self.output_dir / "references"
        ref_dir.mkdir(exist_ok=True)
        
        # 0. Copy images directory to references if it exists
        source_img_dir = self.output_dir.parent / "images"
        target_img_dir = ref_dir / "images"
        if source_img_dir.exists() and not target_img_dir.exists():
            shutil.copytree(source_img_dir, target_img_dir)
            logger.info(f"Copied images from {source_img_dir} to {target_img_dir}")
            
        all_nodes = self._collect_flat_nodes(root_node)

        # Build Table of Contents preview for context.  Computed before
        # the overview branch so it also exists on resume when the
        # book_overview stage is already checkpointed (otherwise the
        # skill-name stage below would hit a NameError on re-run).
        toc_lines = [f"{'  ' * len(n.parent_path)}- {n.title}" for n in all_nodes]

        # 1. Book Overview Generation
        if not checkpoint.is_stage_completed("book_overview"):
            toc_text = "\n".join(toc_lines)
            
            book_overview_prompt = self.book_prompt.format(book_title=book_title, toc=toc_text[:4000])
            raw_overview = self.llm.chat(book_overview_prompt).strip()
            
            # Extract metadata description if present
            meta_match = re.search(r'\[METADATA_DESC:\s*(.*?)\]', raw_overview, re.DOTALL)
            metadata_desc = meta_match.group(1).strip() if meta_match else ""
            
            # Clean overview (remove the tag)
            book_overview = re.sub(r'\[METADATA_DESC:.*?\]', '', raw_overview, flags=re.DOTALL).strip()
            
            checkpoint.mark_stage_completed("book_overview", {
                "overview": book_overview,
                "metadata_desc": metadata_desc
            })
        else:
            stage_data = checkpoint.get_stage_data("book_overview") or {}
            book_overview = stage_data.get("overview", "")
            metadata_desc = stage_data.get("metadata_desc", "")

        summaries = checkpoint.get_stage_data("summaries") or {}
        # Attach the dict so later checkpoint.save() calls persist progress.
        checkpoint.state["data"]["summaries"] = summaries

        # 2. Individual Chunk Tagging
        bar = tqdm(total=len(all_nodes), desc="Skill标签", unit="chunk", ncols=80)
        try:
            for idx, node in enumerate(all_nodes, 1):
                # Resume guard requires BOTH the summary and the reference
                # file: a summary persisted before its file was written
                # (crash in between) must not permanently skip the file on
                # resume, which would leave a SKILL.md dead link.
                if node.id in summaries and (ref_dir / f"{node.id}.md").exists():
                    bar.update(1)
                    continue

                if node.id not in summaries:
                    bar.set_postfix_str(node.title[:20])
                    preview = node.content[:2000] # Use a preview to save tokens

                    if not preview.strip():
                        summaries[node.id] = "No explicit content"
                    else:
                        prompt = self.skill_prompt.format(content_preview=preview)

                        try:
                            tags = self.llm.chat(prompt).strip()
                            # Normalize separators: semicolons (both EN/CN) and newlines → " | "
                            tags = re.sub(r"[;\n；]", " | ", tags)
                            tags = re.sub(r"\s*\|\s*", " | ", tags)
                            tags = re.sub(r"\|\s+\|", "|", tags)
                            summaries[node.id] = tags

                            # Persistent progress tracking
                            checkpoint.state["data"]["summaries"] = summaries
                            checkpoint.save()
                        except Exception as e:
                            logger.error(f"Error tagging chunk {node.id}: {e}")
                            # Not written to summaries so chunk can be retried on re-run

                bar.update(1)

                # 3. Save reference Markdown — after tagging but guarded by the
                # resume check above, so a crash between save() and the write
                # is repaired on the next run instead of leaving a dead link.
                self._write_reference_file(ref_dir, node)
        except KeyboardInterrupt:
            bar.close()
            tagged = len(summaries)
            total = len(all_nodes)
            logger.warning(
                f"用户中断 Skill 标签生成 (Ctrl+C) — "
                f"已完成 {tagged}/{total} 个 chunk，进度已保存至 checkpoint"
            )
            raise
        finally:
            bar.close()

        # 3. Create Master Index
        # Generate skill name via LLM + validation + fallback chain
        if not checkpoint.is_stage_completed("skill_name"):
            toc_preview = "\n".join(toc_lines)
            kebab_name = self._generate_skill_name(book_title, book_overview, toc_preview)
            checkpoint.mark_stage_completed("skill_name", {"name": kebab_name})
        else:
            kebab_name = checkpoint.get_stage_data("skill_name").get("name", "")

        # Sanitise the description for spec compliance
        skill_description = metadata_desc.replace("\n", " ") if metadata_desc else book_overview.replace("\n", " ").strip()

        # Strip angle brackets (forbidden in description field)
        skill_description = skill_description.replace("<", "").replace(">", "")

        # Ensure the description has a trigger sentence
        has_trigger = any(
            phrase in skill_description
            for phrase in ("Use this skill when", "使用此技能", "适用", "请使用此技能")
        )
        if not has_trigger:
            trigger = f" Use this skill when the user asks about topics covered in '{book_title}'."
            skill_description += trigger

        # Truncate to 1000 chars (spec allows 1024; leave margin)
        if len(skill_description) > 1000:
            skill_description = skill_description[:997] + "..."

        index_file = self.output_dir / "SKILL.md"
        reference_lines = []
        
        for node in all_nodes:
            path_str = " > ".join(node.parent_path + [node.title]) if node.parent_path else node.title
            summary = summaries.get(node.id, "")
            reference_lines.append(f"- [{path_str}](references/{node.id}.md): {summary}")
            
        skill_content = f"""---
name: {kebab_name}
description: {skill_description}
---

# {book_title}

## Overview
{book_overview}

## Reference Guide
{chr(10).join(reference_lines)}
"""
        index_file.write_text(skill_content, encoding="utf-8")
        logger.info(f"SKILL mapping completed successfully at {index_file}")
