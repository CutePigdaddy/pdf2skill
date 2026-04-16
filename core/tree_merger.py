import re
import json
import uuid
import shutil
from pathlib import Path
from dataclasses import dataclass, field
from utils.logger import logger
from utils.llm_client import LLMClient
from config.config import config
from Levenshtein import distance as levenshtein_distance

@dataclass
class ChunkNode:
    id: str
    title: str
    parent_path: list
    children: list = field(default_factory=list)
    content: str = ""
    start_line: int = 0
    end_line: int = 0
    iteration: int = 0
    is_atomic: bool = False

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "parent_path": self.parent_path,
            "children": [c.to_dict() for c in self.children],
            "content": self.content,
            "line_range": [self.start_line, self.end_line],
            "iteration": self.iteration,
            "is_atomic": self.is_atomic
        }

    @staticmethod
    def from_dict(data: dict) -> 'ChunkNode':
        node = ChunkNode(
            id=data["id"],
            title=data["title"],
            parent_path=data["parent_path"],
            content=data.get("content", ""),
            start_line=data.get("line_range", [0, 0])[0],
            end_line=data.get("line_range", [0, 0])[1],
            iteration=data.get("iteration", 0),
            is_atomic=data.get("is_atomic", False)
        )
        node.children = [ChunkNode.from_dict(c) for c in data.get("children", [])]
        return node

class TreeMerger:
    """
    Handles Stage 3: TOC Drilling & Merging.
    Recursively "peels" large chunks into smaller logical sub-chunks by identifying 
    sub-headers using an LLM, until all chunks are within the token limit.
    """
    def __init__(self):
        self.chunk_max_tokens = config.get("pdf.chunk_max_tokens", 10000)
        self.chunk_max_iterations = config.get("pdf.chunk_max_iterations", 5)
        self.chunk_anchor_length = config.get("pdf.chunk_anchor_length", 30)
        self.output_language = config.get("system.output_language", "English")
        self.chars_per_token = 2
        self.llm = LLMClient(stage="peeling")
        self.chunk_counter = 0

    def _generate_chunk_id(self) -> str:
        self.chunk_counter += 1
        return f"chunk_{self.chunk_counter:04d}"

    def estimate_tokens(self, text: str) -> int:
        """Rough estimation of token count based on character length."""
        return len(text) // self.chars_per_token

    def find_anchor_position(self, content: str, anchor: str) -> int:
        """
        Locates the position of a sub-header anchor in the text.
        Uses Levenshtein distance for fuzzy matching to handle LLM minor variations.
        """
        anchor = anchor.strip()
        if not anchor:
            return -1

        pos = content.find(anchor)
        if pos != -1:
            return pos

        # Fuzzy match if exact match fails
        anchor_len = len(anchor)
        best_pos = -1
        best_distance = float('inf')
        # Use a more conservative threshold for long content to avoid random matches
        threshold = max(2, anchor_len // 5) 

        # Optimization: Search within a sliding window if the content is huge
        # (Though content is limited to 40k in prompt, the full text might be 100k+)
        for i in range(len(content) - anchor_len + 1):
            window = content[i:i + anchor_len]
            dist = levenshtein_distance(anchor, window)
            if dist < best_distance and dist <= threshold:
                best_distance = dist
                best_pos = i

        return best_pos

    def recursive_peel(self, chunk: ChunkNode, current_iteration: int = 2) -> list[ChunkNode]:
        """
        Recursively splits a ChunkNode if it exceeds token limits.
        
        Args:
            chunk: The ChunkNode to process.
            current_iteration: The current depth of recursion.
            
        Returns:
            A list of smaller sub-chunks.
        """
        if getattr(chunk, 'is_atomic', False):
            logger.info(f"  [Atomic Protection] Skipping recursive peel for: {chunk.title} (ID: {chunk.id})")
            return [chunk]

        if current_iteration > self.chunk_max_iterations:
            logger.warning(f"  Max iterations ({self.chunk_max_iterations}) reached for {chunk.id}. Stopping split.")
            return [chunk]

        estimated_tokens = self.estimate_tokens(chunk.content)
        if estimated_tokens <= self.chunk_max_tokens:
            logger.debug(f"  Chunk {chunk.id} is small enough ({estimated_tokens:,} tokens)")
            return [chunk]

        logger.info(f"  Peeling {chunk.id} (iteration {current_iteration}, ~{estimated_tokens:,} tokens)...")

        # LLM-assisted splitting prompt.
        # It asks the model to either provide split anchors or mark the section as atomic.
        prompt = f"""You are a document chunking assistant. Your task is to identify logical split points within this text.

## Chunk Information
- Parent path: {' > '.join(chunk.parent_path) if chunk.parent_path else 'Root'}
- Current title: {chunk.title}
- Content length: {len(chunk.content):,} characters (~{estimated_tokens:,} tokens)
- Target chunk size: {self.chunk_max_tokens:,} tokens

## Content to Split
(Note: Content is truncated or summarized if too long for prompt, focusing on representative sections)
```
{chunk.content[:40000]}
```

## Your Task
Identify 2-5 split points (anchors) where this content should be divided.

## Special Focus: Atomic Units
- **ATOMIC RULE**: Identify coherent "Atomic Units" that should NEVER be split internally. These include:
    - **TECHNICAL BLOCKS**: Code listings, large tables, data sheets, mathematical proofs.
    - **INSTRUCTIONAL BLOCKS**: Lab procedures, step-by-step guides, complete recipes.
    - **ASSESSMENT BLOCKS**: Exercise sets, problem sets, quiz questions.
- **LEAD-IN RULE**: If a section ends with an Atomic Unit (e.g., Exercises), you MUST find an anchor at the start of the next section (the next # Header) AND an anchor at the start of the Atomic Unit itself.
- **NO FRAGMENTATION**: Do NOT split an Atomic Unit in the middle. If a chunk consists primarily of one Atomic Unit and exceeds the token limit, mark `is_atomic: true` rather than breaking the sequence.
- **DENSITY**: Avoid creating chunks smaller than 100 characters unless it is the very end of a chapter.

Rules:
1. SPLIT POINTS: Choose anchors at logical boundaries (between sections, topics, paragraphs).
2. The anchor should be EXACTLY {self.chunk_anchor_length} characters from the text (including punctuation and spaces).
3. Do NOT hallucinate anchors. The anchor MUST exist in the provided text snippet.
4. IMPORTANT: Only use text from the FIRST 40,000 characters provided above to find anchors. If the text is longer than that, simply find anchors as close to the 20,000 - 35,000 char range as possible.
5. GENERATE TITLES: For each resulting sub-chunk (the parts between anchors), suggest a descriptive title. If a sub-chunk contains multiple headers, generate a title like "Section A & Section B" or "Problems (1.1 to 1.5)".

## Output Format
Return a JSON object:
```json
{{
  "anchors": [
    {{
      "anchor": "exactly {self.chunk_anchor_length} chars from text", 
      "description": "Start of Exercises",
      "suggested_title": "Section 17.4 & Exercises"
    }},
    {{
      "anchor": "exactly {self.chunk_anchor_length} chars from text", 
      "description": "Start of Section 17.5",
      "suggested_title": "Section 17.5: Advanced Topics"
    }} 
  ],
  "is_atomic": false
}}
```

IMPORTANT: Use a string of exactly {self.chunk_anchor_length} characters for each anchor.
Return ONLY the JSON object. No prose."""

        try:
            # LLMClient already has its stage set in __init__
            response = self.llm.chat(prompt)
            if not response or not response.strip():
                logger.warning(f"    Empty response from LLM for {chunk.id}")
                return [chunk]

            json_match = re.search(r'(\{.*\})', response, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group(1))
            else:
                result = json.loads(response)

            if result.get("is_atomic", False):
                logger.info(f"  [AI Decision] Chunk {chunk.id} marked as ATOMIC. Skipping split.")
                chunk.is_atomic = True
                return [chunk]

            anchors = result.get("anchors", [])

        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"    Could not parse LLM response: {e}")
            return [chunk]

        if not anchors:
            logger.info(f"    No anchors found, keeping chunk as-is")
            return [chunk]

        split_positions = [0]
        suggested_titles = []
        for anchor_info in anchors:
            anchor_text = anchor_info.get("anchor", "")
            pos = self.find_anchor_position(chunk.content, anchor_text)
            if pos > 0:
                split_positions.append(pos)
                suggested_titles.append(anchor_info.get("suggested_title", ""))
                logger.info(f"    Found anchor at position {pos}: '{anchor_text[:30]}...'")
            else:
                logger.warning(f"    Could not find anchor: '{anchor_text[:30]}...'")

        split_positions.append(len(chunk.content))
        # Ensure we have a title for the last segment if the LLM didn't provide enough
        while len(suggested_titles) < len(split_positions) - 1:
            suggested_titles.append(None)
            
        # Sort positions and corresponding titles together
        combined = sorted(zip(split_positions[:-1], suggested_titles), key=lambda x: x[0])
        split_positions = [x[0] for x in combined] + [split_positions[-1]]
        suggested_titles = [x[1] for x in combined]

        if len(split_positions) <= 2:
            logger.info(f"    No valid split positions found")
            return [chunk]

        raw_child_chunks = []
        for i in range(len(split_positions) - 1):
            start_pos = split_positions[i]
            end_pos = split_positions[i + 1]

            sub_content = chunk.content[start_pos:end_pos].strip()
            if not sub_content:
                continue
            
            # Initial title fallback
            title = suggested_titles[i] if (i < len(suggested_titles) and suggested_titles[i]) else f"{chunk.title} (Part {i + 1})"
            
            # Refine title if multiple headers are present and LLM title is generic
            headers = re.findall(r'^#+\s+(.+)$', sub_content, re.MULTILINE)
            if headers:
                if len(headers) == 1:
                    title = headers[0]
                elif len(headers) > 1 and (not suggested_titles[i] or "Part" in title):
                    title = f"{headers[0]} & {headers[-1]}"
            
            raw_child_chunks.append({
                "content": sub_content,
                "title": title
            })

        # Tiny Chunk Auto-Merging
        min_threshold = config.get("pdf.chunk_min_threshold", 1000)
        child_chunks = []
        
        i = 0
        while i < len(raw_child_chunks):
            current = raw_child_chunks[i]
            
            # If current chunk is too small, try to merge it
            if len(current["content"]) < min_threshold:
                # Merge with NEXT if available
                if i + 1 < len(raw_child_chunks):
                    next_chunk = raw_child_chunks[i+1]
                    logger.info(f"    Merging small chunk '{current['title']}' ({len(current['content'])} chars) with next chunk.")
                    next_chunk["content"] = current["content"] + "\n\n" + next_chunk["content"]
                    # Update next title if it's a simple part titration
                    if " & " not in next_chunk["title"]:
                        next_chunk["title"] = f"{current['title']} & {next_chunk['title']}"
                    i += 1
                    continue
                # Merge with PREVIOUS if no next
                elif child_chunks:
                    prev_chunk = child_chunks[-1]
                    logger.info(f"    Merging small trailing chunk '{current['title']}' ({len(current['content'])} chars) with previous chunk.")
                    prev_chunk.content += "\n\n" + current["content"]
                    if " & " not in prev_chunk.title:
                        prev_chunk.title = f"{prev_chunk.title} & {current['title']}"
                    i += 1
                    continue

            # Create the actual ChunkNode
            child = ChunkNode(
                id=self._generate_chunk_id(),
                title=current["title"],
                parent_path=chunk.parent_path + [chunk.title],
                content=current["content"],
                start_line=chunk.start_line,
                end_line=chunk.end_line,
                iteration=current_iteration,
                is_atomic=False # Default to false, let recursion or build_and_merge handle it
            )
            child_chunks.append(child)
            i += 1

        final_chunks = []
        for child in child_chunks:
            final_chunks.extend(self.recursive_peel(child, current_iteration + 1))

        chunk.children = child_chunks

        return final_chunks

    def build_and_merge(self, llm_chunks: list) -> ChunkNode:
        master_root = ChunkNode(id="master", title="Document", parent_path=[])
        
        for idx, c in enumerate(llm_chunks):
            # Try to get the real title from content or fallback
            title = "Document Section"
            content = c["content"]
            lines = content.strip().split('\n')
            for line in lines:
                header_match = re.search(r'^(#{1,6})\s+(.+)$', line)
                if header_match:
                    title = header_match.group(2).strip()
                    break

            # Detect if this chunk should be atomic based on content patterns
            is_atomic = c.get("is_atomic", False)
            
            # Use LLM decision if available, only fallback to basic sanity check for TOC 
            if not is_atomic:
                content_lower = content.lower()
                # We only force atomic for very specific, non-content heavy headers if LLM didn't catch it
                if re.search(r'^(#+)\s+(目录|table of contents|toc)$', content_lower, re.MULTILINE):
                    is_atomic = True
                    logger.info(f"  Fallback: Auto-detected TOC as ATOMIC for chunk {idx}")

            node = ChunkNode(
                id=self._generate_chunk_id(),
                title=title,
                parent_path=[],
                content=content,
                start_line=c.get("start_line", 0),
                end_line=c.get("end_line", 0),
                is_atomic=is_atomic
            )
            
            # Recurse into peeling for the node, but add directly to master children
            peeled_nodes = self.recursive_peel(node)
            master_root.children.extend(peeled_nodes)
            
        return master_root

    def _write_chunk_files(self, output_dir: Path, chunks: list[ChunkNode], include_tree: bool = False, root: ChunkNode = None) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)

        if include_tree and root is not None:
            tree_path = output_dir / "tree.json"
            with open(tree_path, "w", encoding="utf-8") as f:
                json.dump(root.to_dict(), f, ensure_ascii=False, indent=2)

        chunks_dir = output_dir / "chunks"
        if chunks_dir.exists():
            shutil.rmtree(chunks_dir)
        chunks_dir.mkdir(exist_ok=True)

        chunk_index = []
        for chunk in chunks:
            chunk_path = chunks_dir / f"{chunk.id}.md"
            path_str = " > ".join(chunk.parent_path) if chunk.parent_path else "Root"
            metadata = f"""---
id: {chunk.id}
title: {chunk.title}
parent_path: {path_str}
start_line: {chunk.start_line}
end_line: {chunk.end_line}
iteration: {chunk.iteration}
tokens: ~{self.estimate_tokens(chunk.content)}
---

"""
            with open(chunk_path, "w", encoding="utf-8") as f:
                f.write(metadata + chunk.content)

            chunk_index.append({
                "id": chunk.id,
                "title": chunk.title,
                "parent_path": chunk.parent_path,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
                "file": str(chunk_path.relative_to(output_dir)),
                "tokens": self.estimate_tokens(chunk.content)
            })

        index_path = output_dir / "chunks_index.json"
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(chunk_index, f, ensure_ascii=False, indent=2)

        return output_dir

    def save_results(self, output_dir: str | Path, root: ChunkNode) -> Path:
        output_dir = Path(output_dir)
        flat_chunks = []

        def collect_chunks(node: ChunkNode):
            if node.id not in {"root", "master"}:
                flat_chunks.append(node)
            for child in node.children:
                collect_chunks(child)

        collect_chunks(root)
        return self._write_chunk_files(output_dir, flat_chunks, include_tree=True, root=root)

    def save_original_chunks(self, output_dir: str | Path, base_chunks: list[dict]) -> Path:
        output_dir = Path(output_dir)
        original_nodes = []

        for idx, c in enumerate(base_chunks, start=1):
            node = ChunkNode(
                id=f"chunk_{idx:04d}",
                title=f"Original Chunk {idx}",
                parent_path=[],
                content=c.get("content", ""),
                start_line=c.get("start_line", 0),
                end_line=c.get("end_line", 0),
                iteration=1,
                is_atomic=c.get("is_atomic", False)
            )
            original_nodes.append(node)

        return self._write_chunk_files(output_dir, original_nodes, include_tree=False)