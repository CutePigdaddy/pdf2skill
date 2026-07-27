import re
import shutil
from pathlib import Path
from tqdm import tqdm
from core.tree_merger import ChunkNode
from utils.logger import logger
from utils.llm_client import LLMClient
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

    def _collect_flat_nodes(self, node: ChunkNode) -> list[ChunkNode]:
        """Flatten the chunk tree into a flat list of non-root nodes.

        Args:
            node: Root ChunkNode to traverse.

        Returns:
            List of all ChunkNode objects (excluding master/root).
        """
        nodes = []
        if getattr(node, 'id', None) and node.id not in ("master", "root"):
            nodes.append(node)
        for child in getattr(node, 'children', []):
            nodes.extend(self._collect_flat_nodes(child))
        return nodes

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

        # 1. Book Overview Generation
        if not checkpoint.is_stage_completed("book_overview"):
            # Build Table of Contents preview for context
            toc_lines = [f"{'  ' * len(n.parent_path)}- {n.title}" for n in all_nodes]
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

        # 2. Individual Chunk Tagging
        bar = tqdm(total=len(all_nodes), desc="Skill标签", unit="chunk", ncols=80)
        for idx, node in enumerate(all_nodes, 1):
            if node.id in summaries:
                bar.update(1)
                continue

            bar.set_postfix_str(node.title[:20])
            preview = node.content[:2000] # Use a preview to save tokens
            
            if not preview.strip():
                summaries[node.id] = "No explicit content"
                bar.update(1)
                continue
                
            prompt = self.skill_prompt.format(content_preview=preview)
            
            try:
                tags = self.llm.chat(prompt).strip()
                # Formatting: Convert newlines and semicolons to consistent separators
                tags = tags.replace("\n", " ").replace(";", " | ")
                summaries[node.id] = tags
                
                # Persistent progress tracking
                checkpoint.state["data"]["summaries"] = summaries
                checkpoint.save()
            except Exception as e:
                logger.error(f"Error tagging chunk {node.id}: {e}")
                # Not written to summaries so chunk can be retried on re-run

            bar.update(1)

            # 3. Save reference Markdown with frontmatter
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

        bar.close()

        # 3. Create Master Index
        kebab_name = re.sub(r"[^a-zA-Z0-9]+", "-", book_title.lower()).strip("-")
        
        # Determine the description for metadata
        # If we have a dedicated metadata description from the LLM, use it
        skill_description = metadata_desc.replace("\n", " ") if metadata_desc else book_overview.replace("\n", " ").strip()
        
        if len(skill_description) > 500:
            skill_description = skill_description[:497] + "..."

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
