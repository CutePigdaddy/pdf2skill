"""
Thin wrapper invoked by the frontend server to run the pipeline.
Accepts CLI args instead of interactive prompts.
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Input file path")
    parser.add_argument("--output", required=True, help="Output directory")
    parser.add_argument("--mode", default="pdf", choices=["pdf", "markdown"])
    args = parser.parse_args()

    from dotenv import load_dotenv
    load_dotenv()

    from main import run_pipeline
    from utils.checkpoint import CheckpointManager

    print(f"Input: {args.input}")
    print(f"Output: {args.output}")
    print(f"Mode: {args.mode}")
    print()

    if args.mode == "markdown":
        out_path = Path(args.output)
        out_path.mkdir(parents=True, exist_ok=True)
        checkpoint = CheckpointManager(out_path)
        checkpoint.mark_stage_completed("pdf_conversion", {"md_file": args.input})

    run_pipeline(args.input, args.output)
    print("\nPipeline finished successfully.")


if __name__ == "__main__":
    main()
