import argparse
import sys
from pathlib import Path

# Add project root to Python path
sys.path.append(str(Path(__file__).parent))

from config.config import config
from utils.logger import logger, PDF2SkillsException
from utils.checkpoint import CheckpointManager
from core.pdf_processor import PDFProcessor
from core.llm_chunker import LLMChunker
from core.tree_merger import TreeMerger, ChunkNode
from core.skill_engine import SkillEngine

def run_pipeline(pdf_path: str, output_dir: str):
    logger.info(f"Starting pipeline for {pdf_path}")
    pdf_file = Path(pdf_path)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    
    checkpoint = CheckpointManager(out_dir)
    
    # 1. PDF -> Markdown
    if not checkpoint.is_stage_completed("pdf_conversion"):
        logger.info("--- Stage 1: PDF to Markdown ---")
        processor = PDFProcessor()
        md_file = processor.process(pdf_file, out_dir)
        checkpoint.mark_stage_completed("pdf_conversion", {"md_file": str(md_file)})
    else:
        md_file = Path(checkpoint.get_stage_data("pdf_conversion")["md_file"])
        logger.info(f"Loaded existing markdown: {md_file}")

    # 2. LLM Conceptual Chunking
    if not checkpoint.is_stage_completed("llm_chunking"):
        logger.info("--- Stage 2: LLM Chunking Strategy ---")
        chunker = LLMChunker()
        split_data = chunker.split(md_file)
        base_chunks = chunker.extract_chunks(split_data)
        checkpoint.mark_stage_completed("llm_chunking", {"base_chunks": base_chunks})
    else:
        base_chunks = checkpoint.get_stage_data("llm_chunking")["base_chunks"]
        logger.info(f"Loaded {len(base_chunks)} base chunks from checkpoint")

    # Keep raw chunks in a separate folder for parity with the legacy project.
    merger = TreeMerger()
    raw_chunks_dir = out_dir / "full_chunks_original"
    merger.save_original_chunks(raw_chunks_dir, base_chunks)
    logger.info(f"Original chunks saved to: {raw_chunks_dir}")

    # 3. Tree Merging & TOC Drilling
    if not checkpoint.is_stage_completed("tree_merging"):
        logger.info("--- Stage 3: TOC Drilling & Merging ---")
        master_root = merger.build_and_merge(base_chunks)
        peeled_chunks_dir = out_dir / "full_chunks"
        merger.save_results(peeled_chunks_dir, master_root)
        logger.info(f"Peeled chunks saved to: {peeled_chunks_dir}")
        checkpoint.mark_stage_completed("tree_merging", {"master_root": master_root.to_dict()})
    else:
        logger.info("Loading merged tree from checkpoint...")
        tree_data = checkpoint.get_stage_data("tree_merging")["master_root"]
        master_root = ChunkNode.from_dict(tree_data)

    # 4. Skill Generation
    logger.info("--- Stage 4: Skill Generation ---")
    skill_out_dir = out_dir / "generated_skills"
    engine = SkillEngine(skill_out_dir)
    engine.generate(master_root, pdf_file.stem)
    
    logger.info("Pipeline Execution Finished Successfully!")

def main():
    parser = argparse.ArgumentParser(description="PDF2Skills v2 - Refactored Pipeline")
    parser.add_argument("pdf_path", type=str, help="Path to the PDF file")
    parser.add_argument("--output", type=str, default="outputs", help="Output directory")
    args = parser.parse_args()
    
    try:
        run_pipeline(args.pdf_path, args.output)
    except Exception as e:
        logger.exception(f"Pipeline crashed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
