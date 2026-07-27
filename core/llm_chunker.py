import re
from pathlib import Path
from dataclasses import dataclass
from tqdm import tqdm
from utils.logger import logger
from utils.llm_client import LLMClient
from config.config import config

@dataclass
class Header:
    level: int
    text: str
    line_number: int

class LLMChunker:
    """Stage 2: Strategic Chunking.

    Uses an LLM to analyse the document's header structure (TOC) and
    decide where to split major sections to preserve conceptual integrity.
    """
    def __init__(self):
        self.llm = LLMClient(stage="chunking")
        self.prompt_template = (Path(config.get("paths.prompts_dir", "prompts")) / "chunk_strategy.txt").read_text(encoding="utf-8")

    def split(self, markdown_path: Path) -> dict:
        """Extract headers from Markdown and ask the LLM for a splitting plan.

        The plan includes major chapter split points, TOC range, preface
        range, and atomic ranges (exercises, etc.) that should not be
        split further.

        Args:
            markdown_path: Path to the source Markdown file.

        Returns:
            Dict with keys ``splits``, ``toc_range``, ``preface_range``,
            ``atomic_ranges``, ``headers``, ``lines``, ``fallback``.
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
        bar = tqdm(total=2, desc="Chunking策略", unit="attempt", ncols=80)
        split_plan = None
        used_fallback = False
        for attempt in range(2):
            try:
                response = self.llm.chat(prompt, is_json=True)
                split_plan = self.llm.parse_json_response(response)
                bar.update(1)
                break
            except Exception as e:
                bar.update(1)
                logger.warning(f"LLM chunk strategy attempt {attempt + 1} failed: {e}")
        if split_plan is None:
            logger.warning("All LLM attempts failed, falling back to uniform splitting")
            total = len(lines)
            num_chunks = max(1, total // 2000)
            step = total // num_chunks
            split_plan = {"chapter_splits": [i * step for i in range(1, num_chunks)]}
            used_fallback = True

        bar.close()

        return {
            "splits": split_plan.get("chapter_splits", []),
            "toc_range": split_plan.get("toc_range"),
            "preface_range": split_plan.get("preface_range"),
            "atomic_ranges": split_plan.get("atomic_ranges", {}),
            "headers": headers,
            "lines": lines,
            "fallback": used_fallback
        }

    def _build_tree_text(self, headers: list[Header]) -> str:
        """Convert a header list into a simplified text tree for LLM analysis.

        Args:
            headers: List of ``Header`` objects.

        Returns:
            Newline-separated string like ``[Line 12] ## Chapter 1``.
        """
        lines = []
        for h in headers:
            # Inclue more levels for better context if necessary, but keep it concise
            if h.level > 4:
                continue
            # Use a more descriptive format for the LLM
            lines.append(f"[Line {h.line_number}] {'#' * h.level} {h.text}")
        return "\n".join(lines)

    def extract_chunks(self, split_data: dict) -> list:
        """Create base chunks according to the LLM splitting plan.

        Marks the Table of Contents range as atomic (un-splittable).

        Args:
            split_data: Output of ``split()``, containing ``lines``,
                ``splits``, ``toc_range``, etc.

        Returns:
            List of chunk dicts with ``start_line``, ``end_line``,
            ``content``, and ``is_atomic`` keys.
        """
        lines = split_data["lines"]
        splits = sorted(set([1] + split_data.get("splits", []) + [len(lines) + 1]))
        
        # Use toc_range from LLM if provided
        toc_range = split_data.get("toc_range", None)
        
        # Get atomic ranges (Exercises, Appendix, References, etc.)
        # These are used as informational metadata but we only force ATOMIC for TOC,
        # otherwise we loose the ability to peel through large sections containing exercises.
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
