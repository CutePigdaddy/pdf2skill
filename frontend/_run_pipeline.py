"""
Thin wrapper invoked by the frontend server to run the pipeline.
Accepts CLI args instead of interactive prompts.
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))


def main():
    """CLI entry point invoked by the frontend server's subprocess.

    Parses ``--input``, ``--output``, ``--mode`` args and delegates
    to ``main.run_pipeline``.  In markdown mode, marks the PDF
    conversion stage as already completed in the checkpoint.
    """
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Input file path")
    parser.add_argument("--output", required=True, help="Output directory")
    parser.add_argument("--mode", default="pdf", choices=["pdf", "markdown"])
    parser.add_argument("--from-stage", type=str, default=None,
                        choices=["pdf_conversion", "llm_chunking", "tree_merging"],
                        help="Restart from this stage (clears it and all later stages from checkpoint)")
    parser.add_argument("--restart", action="store_true",
                        help="Clear all checkpoint data and run from scratch")
    args = parser.parse_args()

    from dotenv import load_dotenv
    load_dotenv()

    from main import run_pipeline
    from utils.checkpoint import CheckpointManager

    # Add a DEBUG-level handler so __MINERU_PROGRESS__ markers (emitted at
    # debug level to avoid console clutter) still reach the frontend's
    # subprocess log file via stdout capture.
    import logging
    from utils.logger import logger
    debug_handler = logging.StreamHandler(sys.stdout)
    debug_handler.setLevel(logging.DEBUG)
    debug_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    logger.addHandler(debug_handler)

    print(f"Input: {args.input}")
    print(f"Output: {args.output}")
    print(f"Mode: {args.mode}")
    print()

    if args.mode == "markdown":
        out_path = Path(args.output)
        out_path.mkdir(parents=True, exist_ok=True)
        checkpoint = CheckpointManager(out_path)
        checkpoint.mark_stage_completed("pdf_conversion", {"md_file": args.input})

    run_pipeline(args.input, args.output, from_stage=args.from_stage, restart=args.restart)
    print("\nPipeline finished successfully.")


if __name__ == "__main__":
    main()
