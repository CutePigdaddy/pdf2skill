"""M4/M5 alignment verification (Python side).

Runs Stage 2 (LLMChunker) + Stage 3 (TreeMerger) + Stage 4 (SkillEngine) on
.m4align/full.md with a deterministic mock LLM (responses derived purely from
the prompt), writing outputs to .m4align/py_out/. The identical mock logic
runs on the TS side; if both implementations are aligned, the outputs are
byte-identical.
"""

import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).parent
ROOT = BASE.parent
sys.path.insert(0, str(ROOT))

PROMPTS: list[str] = []


def mock_response(prompt: str) -> str:
    if prompt.startswith("You are a document structure analyzer"):
        # Parse the header tree embedded in the prompt: '[Line N] # Title'
        tree = re.findall(r"^\[Line (\d+)\] (#{1,4}) (.+)$", prompt, re.MULTILINE)
        level1 = [(int(n), t) for n, h, t in tree if h == "#"]
        toc_line = next((n for n, t in level1 if t in ("目录", "TOC")), None)
        toc_range = None
        if toc_line is not None:
            after = [n for n, _ in level1 if n > toc_line]
            toc_end = (after[0] - 1) if after else None
            if toc_end is not None:
                toc_range = [toc_line, toc_end]
        plan = {
            "toc_range": toc_range,
            "preface_range": None,
            "chapter_splits": [n for n, _ in level1],
            "atomic_ranges": {},
        }
        return json.dumps(plan, ensure_ascii=False)

    if prompt.startswith("You are an expert curriculum designer"):
        return (
            "This overview covers the synthetic fixture book.\n"
            "It spans three chapters plus a references section.\n"
            "[METADATA_DESC: A concise guide to the sample book content covering fundamentals, applications, and summary.]"
        )

    if prompt.startswith("You are an expert at creating concise and technical identifiers"):
        return "sample-book-fixture"

    if prompt.startswith("You are a technical indexer"):
        m = re.search(r"## Sub (\d+)\.(\d+): Topic (\d+)", prompt)
        if m:
            return f"sub-{m.group(1)}-{m.group(2)} | topic-{m.group(3)} | sample-fixture"
        m = re.search(r"# Chapter (\d+): ([A-Za-z ]+)", prompt)
        if m:
            slug = m.group(2).strip().lower().replace(" ", "-")
            return f"chapter-{m.group(1)} | {slug} | sample-fixture"
        return "general | sample-fixture"

    # Peeling prompt: extract the fenced content block
    start = prompt.index("```\n") + 4
    end = prompt.index("\n```", start)
    content = prompt[start:end]
    occ = [m.start() for m in re.finditer(r"\n## ", content)]
    if len(occ) < 2:
        return json.dumps({"anchors": [], "is_atomic": False})
    picks = occ[1:3] if len(occ) >= 3 else occ[0:2]
    anchors = []
    for i, pos in enumerate(picks):
        anchors.append(
            {
                "anchor": content[pos + 1 : pos + 31],
                "description": f"start of part {i + 1}",
                "suggested_title": f"Part {i + 1}",
            }
        )
    return json.dumps({"anchors": anchors, "is_atomic": False}, ensure_ascii=False)


class MockLLM:
    """Drop-in replacement for LLMClient with deterministic responses."""

    def __init__(self, stage=None, **_kwargs):
        self.stage = stage

    def chat(self, prompt, is_json=False, max_tokens=None):
        PROMPTS.append(prompt)
        return mock_response(prompt)

    def parse_json_response(self, text):
        return json.loads(text)


def main() -> None:
    import core.llm_chunker as chunker_mod
    import core.tree_merger as merger_mod
    import core.skill_engine as skill_mod

    chunker_mod.LLMClient = MockLLM
    merger_mod.LLMClient = MockLLM
    skill_mod.LLMClient = MockLLM

    from core.llm_chunker import LLMChunker
    from core.tree_merger import TreeMerger
    from core.skill_engine import SkillEngine

    out_dir = BASE / "py_out"
    if out_dir.exists():
        import shutil

        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    chunker = LLMChunker()
    split_data = chunker.split(BASE / "full.md")
    base_chunks = chunker.extract_chunks(split_data)
    print(f"base chunks: {len(base_chunks)}, fallback={split_data['fallback']}")

    merger = TreeMerger()
    merger.save_original_chunks(out_dir / "full_chunks_original", base_chunks)
    master_root = merger.build_and_merge(base_chunks)
    merger.save_results(out_dir / "full_chunks", master_root)
    print(f"peel_errors: {merger.peel_errors}")

    engine = SkillEngine(out_dir / "generated_skills")
    engine.generate(master_root, "Test Book")
    print("skill generation done")

    (BASE / "py_prompts.json").write_text(
        json.dumps(PROMPTS, ensure_ascii=False), encoding="utf-8"
    )
    print(f"prompts recorded: {len(PROMPTS)}")


if __name__ == "__main__":
    main()
