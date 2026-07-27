#!/usr/bin/env python
"""
E2E debug runner for pdf2skill pipeline.

Uses REAL API calls (MinerU + LLM) with your .env configuration.
Runs the full 4-stage pipeline on a timestamped copy of the test PDF
and saves a full log to tests/logs/<timestamp>.txt.

Usage:
    python tests/test_e2e_real.py           # concise output
    python tests/test_e2e_real.py -v        # verbose: headers, tree, SKILL.md preview
"""

import argparse
import sys
import time
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

# Fix Windows console encoding for Chinese + special chars
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

# -- Configuration -----------------------------------------------------------

SOURCE_PDF = PROJECT_ROOT / "docs" / "pdfs" / "行业规范" / "宪法.pdf"
LOGS_DIR = Path(__file__).resolve().parent / "logs"


# -- Logging helper: tee output to both console and log file -----------------

class TeeWriter:
    """Writes to both stdout and a log file simultaneously."""

    def __init__(self, log_path: Path):
        self._terminal = sys.stdout
        self._log = open(log_path, "w", encoding="utf-8", errors="replace")

    def write(self, data):
        self._terminal.write(data)
        self._log.write(data)
        self._log.flush()

    def flush(self):
        self._terminal.flush()
        self._log.flush()

    def close(self):
        self._log.close()


# -- Display helpers ---------------------------------------------------------

def banner(text: str, char: str = "="):
    width = 60
    print(f"\n{char * width}")
    print(f"  {text}".center(width))
    print(f"{char * width}\n")


def stage_banner(stage_num: int, title: str):
    print(f"\n{'-' * 60}")
    print(f"  Stage {stage_num}: {title}")
    print(f"{'-' * 60}")


# -- Verbose-only helpers -----------------------------------------------------

def print_tree(node, indent=0):
    """Pretty-print a ChunkNode tree (verbose only)."""
    prefix = "  " * indent
    atomic_tag = " [ATOMIC]" if getattr(node, 'is_atomic', False) else ""
    char_count = len(getattr(node, 'content', ''))
    print(f"{prefix}+-- {node.title} ({node.id}, ~{char_count} chars){atomic_tag}")
    for child in getattr(node, 'children', []):
        print_tree(child, indent + 1)


def list_output_files(output_dir: Path):
    """List all output files with sizes (verbose only)."""
    for f in sorted(output_dir.rglob("*")):
        if f.is_file():
            rel = f.relative_to(output_dir)
            size = f.stat().st_size
            if size > 1024:
                print(f"   {rel} ({size / 1024:.1f} KB)")
            else:
                print(f"   {rel} ({size} B)")


# -- Main --------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="PDF2Skill E2E real test")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Show detailed output: headers, chunk tree, SKILL.md preview, file list")
    args = parser.parse_args()
    verbose = args.verbose

    # Generate timestamp for this test run
    now = datetime.now()
    ts = now.strftime("%Y%m%d_%H%M%S")
    ts_display = now.strftime("%Y-%m-%d %H:%M:%S")

    # Set up log file
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / f"{ts}.txt"

    # Tee all stdout to log file
    tee = TeeWriter(log_path)
    sys.stdout = tee

    # Also redirect the pdf2skills logger to our tee so pipeline logs
    # appear in the log file too
    import logging
    from utils.logger import logger
    file_handler = logging.StreamHandler(tee)
    file_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    logger.addHandler(file_handler)

    # -- Pre-flight checks ---------------------------------------------------
    banner(f"PDF2Skill E2E  [{ts_display}]")

    if not SOURCE_PDF.exists():
        print(f"[FAIL] Test PDF not found: {SOURCE_PDF}")
        tee.close()
        sys.exit(1)

    # Copy and rename the PDF with timestamp so MinerU treats it as a
    # new file and does not return a cached result.
    tmp_dir = Path(tempfile.mkdtemp(prefix="pdf2skill_e2e_"))
    renamed_pdf = tmp_dir / f"宪法_{ts}.pdf"
    shutil.copy2(SOURCE_PDF, renamed_pdf)
    print(f"[PDF]   {SOURCE_PDF.name} -> {renamed_pdf.name} ({renamed_pdf.stat().st_size / 1024:.0f} KB)")

    # Output dir named with timestamp
    output_dir = PROJECT_ROOT / "outputs" / f"e2e_{ts}"

    # Force clean: remove all previous output and checkpoints so every
    # stage re-runs from scratch. No partial results are kept.
    if output_dir.exists():
        print(f"[CLEAN] Removing previous output: {output_dir}")
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"[OUT]   {output_dir}")
    print(f"[LOG]   {log_path}")
    if verbose:
        print("[MODE]  verbose")

    # Verify env
    from config.config import Config
    Config._instance = None  # force reload
    from config.config import config

    mineru_mode = config.get("mineru.api_mode", "remote")
    chunk_provider = config.get("llm.routers.chunking_provider", "")
    peel_provider = config.get("llm.routers.peeling_provider", "")
    skill_provider = config.get("llm.routers.skill_engine_provider", "")

    print(f"\n[CONFIG]")
    print(f"   MinerU:     {mineru_mode}")
    print(f"   Chunking:   {chunk_provider} -> {config.get(f'llm.providers.{chunk_provider}.chunking_model', '?')}")
    print(f"   Peeling:    {peel_provider} -> {config.get(f'llm.providers.{peel_provider}.peeling_model', '?')}")
    print(f"   Skill:      {skill_provider} -> {config.get(f'llm.providers.{skill_provider}.skill_engine_model', '?')}")

    # Check API keys
    import os
    keys_ok = True
    if mineru_mode == "remote" and not os.getenv("MINERU_API_KEY"):
        print("[WARN] MINERU_API_KEY not set -- Stage 1 will fail")
        keys_ok = False

    for provider in set([chunk_provider, peel_provider, skill_provider]):
        key_env = config.get(f"llm.providers.{provider}.api_key_env", "")
        if not os.getenv(key_env):
            print(f"[WARN] {key_env} not set -- stages using {provider} will fail")
            keys_ok = False

    if not keys_ok:
        print("\n[FAIL] Missing API keys. Check your .env file.")
        tee.close()
        sys.exit(1)

    print("[OK] API keys present")

    # -- Run pipeline --------------------------------------------------------
    from core.pdf_processor import PDFProcessor
    from core.llm_chunker import LLMChunker
    from core.tree_merger import TreeMerger, ChunkNode
    from core.skill_engine import SkillEngine

    total_start = time.time()

    try:
        # -- Stage 1: PDF -> Markdown ----------------------------------------
        stage_banner(1, "PDF -> Markdown (MinerU)")
        t0 = time.time()

        processor = PDFProcessor()
        md_file = processor.process(renamed_pdf, output_dir)

        t1 = time.time()
        md_content = md_file.read_text(encoding="utf-8")
        md_lines = md_content.splitlines()
        print(f"[OK] {t1 - t0:.1f}s | {len(md_content):,} chars, {len(md_lines):,} lines")

        if verbose:
            headers = [line for line in md_lines if line.startswith("#")]
            print(f"   Headers: {len(headers)}")
            for h in headers[:15]:
                print(f"     {h}")
            if len(headers) > 15:
                print(f"     ... and {len(headers) - 15} more")

        # -- Stage 2: LLM Chunking -------------------------------------------
        stage_banner(2, "LLM Strategic Chunking")
        t0 = time.time()

        chunker = LLMChunker()
        split_data = chunker.split(md_file)
        base_chunks = chunker.extract_chunks(split_data)

        t1 = time.time()
        is_fallback = split_data.get("fallback", False)
        fallback_tag = " [FALLBACK]" if is_fallback else ""
        print(f"[OK] {t1 - t0:.1f}s | {len(base_chunks)} chunks{fallback_tag}")

        if verbose:
            print(f"   Splits at lines: {split_data.get('splits', [])}")
            print(f"   TOC range: {split_data.get('toc_range')}")
            for i, c in enumerate(base_chunks):
                atomic = " [ATOMIC]" if c.get("is_atomic") else ""
                print(f"     Chunk {i+1}: lines {c['start_line']}-{c['end_line']}, "
                      f"{len(c['content']):,} chars{atomic}")

        # Save original chunks
        merger = TreeMerger()
        raw_chunks_dir = output_dir / "full_chunks_original"
        merger.save_original_chunks(raw_chunks_dir, base_chunks)

        # -- Stage 3: TOC Drilling & Merging ---------------------------------
        stage_banner(3, "TOC Drilling & Merging")
        t0 = time.time()

        master_root = merger.build_and_merge(base_chunks)
        peeled_chunks_dir = output_dir / "full_chunks"
        merger.save_results(peeled_chunks_dir, master_root)

        t1 = time.time()

        def count_leaves(node):
            children = getattr(node, 'children', [])
            if not children:
                return 1
            return sum(count_leaves(c) for c in children)
        total_leaves = count_leaves(master_root)

        error_tag = f" [{merger.peel_errors} errors]" if merger.peel_errors > 0 else ""
        print(f"[OK] {t1 - t0:.1f}s | {total_leaves} leaf chunks{error_tag}")

        if verbose:
            print(f"\n   Chunk tree:")
            print_tree(master_root, indent=2)

        # -- Stage 4: Skill Generation ---------------------------------------
        stage_banner(4, "Skill Generation")
        t0 = time.time()

        skill_out_dir = output_dir / "generated_skills"
        engine = SkillEngine(skill_out_dir)
        engine.generate(master_root, renamed_pdf.stem)

        t1 = time.time()

        # Verify SKILL.md
        skill_md = skill_out_dir / "SKILL.md"
        ref_count = 0
        skill_lines = 0
        if skill_md.exists():
            skill_content = skill_md.read_text(encoding="utf-8")
            skill_lines = len(skill_content.splitlines())
            ref_dir = skill_out_dir / "references"
            if ref_dir.exists():
                ref_count = len(list(ref_dir.glob("*.md")))

        print(f"[OK] {t1 - t0:.1f}s | SKILL.md {skill_lines} lines, {ref_count} refs")

        if verbose:
            if skill_md.exists():
                skill_content = skill_md.read_text(encoding="utf-8")
                lines = skill_content.splitlines()
                print(f"\n   --- SKILL.md preview ---")
                for line in lines[:20]:
                    print(f"   {line}")
                if len(lines) > 20:
                    print(f"   ... ({len(lines) - 20} more lines)")
            print(f"\n   Output files:")
            list_output_files(output_dir)

    except KeyboardInterrupt:
        print("\n[INTERRUPTED] Pipeline stopped by user.")
        tee.close()
        sys.exit(0)
    except Exception as e:
        print(f"\n[FAIL] Pipeline failed: {e}")
        import traceback
        traceback.print_exc()

        if output_dir.exists():
            print(f"\n[PARTIAL] Output generated so far:")
            list_output_files(output_dir)
        tee.close()
        sys.exit(1)

    # -- Summary -------------------------------------------------------------
    total_elapsed = time.time() - total_start
    banner(f"Done ({total_elapsed:.1f}s)")
    print(f"  Output: {output_dir}")
    print(f"  Log:    {log_path}")

    # Clean up temp dir (the renamed PDF copy)
    try:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    except Exception:
        pass

    tee.close()


if __name__ == "__main__":
    main()
