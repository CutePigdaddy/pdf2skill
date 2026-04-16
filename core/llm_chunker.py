import re
from pathlib import Path
from dataclasses import dataclass
from utils.logger import logger
from utils.llm_client import LLMClient
from config.config import config

@dataclass
class Header:
    level: int
    text: str
    line_number: int

class LLMChunker:
    """
    Handles the first stage of document processing: Strategic Chunking.
    Uses an LLM to analyze the document's header structure (TOC) and decide 
    where to split major sections to preserve conceptual integrity.
    """
    def __init__(self):
        self.llm = LLMClient(stage="chunking")
        self.prompt_template = (Path(config.get("paths.prompts_dir", "prompts")) / "chunk_strategy.txt").read_text(encoding="utf-8")

    def split(self, markdown_path: Path) -> dict:
        """
        Extracts headers from markdown and asks LLM for a splitting plan.
        
        The plan includes:
        - Major chapter split points
        - Table of Contents (TOC) range
        - Preface range
        - Atomic ranges (exercises, etc.) that should not be split further
        """
        logger.info(f"Analyzing document structure: {markdown_path}")
        content = markdown_path.read_text(encoding="utf-8")
        lines = content.split('\n')
        
        headers = []
        for i, line in enumerate(lines, 1):
            match = re.match(r'^(#{1,6})\s+(.+)$', line)
            if match:
                headers.append(Header(len(match.group(1)), match.group(2).strip(), i))
                
        tree_text = self._build_tree_text(headers)
        
        total_chars = len(content)
        total_lines = len(lines)
        
        # Inject document metrics into the prompt
        prompt = self.prompt_template.replace("{total_chars}", str(total_chars))
        prompt = prompt.replace("{total_lines}", str(total_lines))
        prompt = prompt.replace("{header_tree}", tree_text)
        
        if "{estimated_tokens}" in prompt:
            prompt = prompt.replace("{estimated_tokens}", str(total_chars // 2))
            
        logger.info("Requesting strategic split points from LLM...")
        try:
            response = self.llm.chat(prompt, is_json=True)
            split_plan = self.llm.parse_json_response(response)
        except Exception as e:
            logger.error(f"Failed to get chunk strategy: {e}")
            # Fallback to a single chunk if LLM fails
            split_plan = {"chapter_splits": [1]}
        
        return {
            "splits": split_plan.get("chapter_splits", []),
            "toc_range": split_plan.get("toc_range"),
            "preface_range": split_plan.get("preface_range"),
            "atomic_ranges": split_plan.get("atomic_ranges", {}),
            "headers": headers,
            "lines": lines
        }

    def _build_tree_text(self, headers: list[Header]) -> str:
        """Converts header list into a simplified text tree for LLM analysis."""
        lines = []
        for h in headers:
            # Inclue more levels for better context if necessary, but keep it concise
            if h.level > 4: continue
            indent = "  " * (h.level - 1)
            # Use a more descriptive format for the LLM
            lines.append(f"[Line {h.line_number}] {'#' * h.level} {h.text}")
        return "\n".join(lines)

    def extract_chunks(self, split_data: dict) -> list:
        """Creates the initial base chunks according to the strategic splits"""
        lines = split_data["lines"]
        splits = sorted(set([1] + split_data.get("splits", []) + [len(lines) + 1]))
        
        # Use toc_range from LLM if provided
        toc_range = split_data.get("toc_range", None)
        
        # Get atomic ranges (Exercises, Appendix, References, etc.)
        # These are used as informational metadata but we only force ATOMIC for TOC,
        # otherwise we loose the ability to peel through large sections containing exercises.
        atomic_ranges = split_data.get("atomic_ranges", {})
        toc_range = split_data.get("toc_range", None)
        
        chunks = []
        for i in range(len(splits) - 1):
            start = splits[i]
            end = splits[i+1] - 1
            chunk_content = "\n".join(lines[start-1:end])
            if chunk_content.strip():
                is_atomic = False
                
                # We ONLY force atomic for the Table of Contents range.
                # Other sections like Exercises/Appendix returned by LLM are "atomic" 
                # in a logical sense but often need peeling if they are too large.
                if toc_range and isinstance(toc_range, list) and len(toc_range) >= 2:
                    toc_start, toc_end = int(toc_range[0]), int(toc_range[1])
                    if start >= toc_start and end <= toc_end:
                        is_atomic = True
                        logger.info(f"Marked chunk (lines {start}-{end}) as ATOMIC (TOC range)")
                
                chunks.append({
                    "start_line": start,
                    "end_line": end,
                    "content": chunk_content,
                    "is_atomic": is_atomic
                })
        return chunks
