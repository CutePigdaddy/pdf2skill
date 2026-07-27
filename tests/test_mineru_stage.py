#!/usr/bin/env python
"""
Stage 1 test: PDF upload and MinerU processing.

Uses REAL MinerU API to convert a PDF to Markdown.
Uploads a timestamped copy of 宪法.pdf to avoid MinerU caching,
then polls for completion and downloads the result.

This test is isolated from the rest of the pipeline — it only
exercises the MinerU remote API flow.

Usage:
    python tests/test_mineru_stage.py
"""

import sys
import time
import json
import shutil
import tempfile
import zipfile
import io
from datetime import datetime
from pathlib import Path

# Fix Windows console encoding
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

# -- Configuration -----------------------------------------------------------

SOURCE_PDF = PROJECT_ROOT / "docs" / "pdfs" / "行业规范" / "TCP行业规范.pdf"
LOGS_DIR = Path(__file__).resolve().parent / "logs"

MINERU_BASE_URL = "https://mineru.net/api/v4"


# -- Logging helper ----------------------------------------------------------

class TeeWriter:
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


def step(text: str):
    print(f"  >> {text}")


# -- Main --------------------------------------------------------------------

def main():
    now = datetime.now()
    ts = now.strftime("%Y%m%d_%H%M%S")
    ts_display = now.strftime("%Y-%m-%d %H:%M:%S")

    # Set up log file
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / f"mineru_{ts}.txt"
    tee = TeeWriter(log_path)
    sys.stdout = tee

    # Also pipe the pdf2skills logger into our tee
    import logging
    from utils.logger import logger
    file_handler = logging.StreamHandler(tee)
    file_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    logger.addHandler(file_handler)

    banner(f"MinerU Stage Test  [{ts_display}]")

    # -- Pre-flight ----------------------------------------------------------
    import os
    from config.config import Config
    Config._instance = None
    from config.config import config

    api_key = os.getenv("MINERU_API_KEY")
    if not api_key:
        print("[FAIL] MINERU_API_KEY not set in .env")
        tee.close()
        sys.exit(1)

    mineru_mode = config.get("mineru.api_mode", "remote")
    print(f"[CONFIG] MinerU mode: {mineru_mode}")

    if mineru_mode != "remote":
        print("[FAIL] This test only supports remote mode. Set MINERU_API_MODE=remote in .env")
        tee.close()
        sys.exit(1)

    if not SOURCE_PDF.exists():
        print(f"[FAIL] Test PDF not found: {SOURCE_PDF}")
        tee.close()
        sys.exit(1)

    # -- Prepare timestamped PDF copy ----------------------------------------
    tmp_dir = Path(tempfile.mkdtemp(prefix="pdf2skill_mineru_"))
    renamed_pdf = tmp_dir / f"宪法_{ts}.pdf"
    shutil.copy2(SOURCE_PDF, renamed_pdf)
    print(f"[PDF]   {SOURCE_PDF.name} -> {renamed_pdf.name} ({renamed_pdf.stat().st_size / 1024:.0f} KB)")

    # Output dir
    output_dir = PROJECT_ROOT / "outputs" / f"mineru_{ts}"
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"[OUT]   {output_dir}")
    print(f"[LOG]   {log_path}")

    total_start = time.time()

    try:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        remote_cfg = config.get("mineru.remote", {})
        language = config.get("mineru.language", "ch")

        # -- Step 1: Request signed upload URL --------------------------------
        step("Requesting signed upload URL from MinerU...")
        t0 = time.time()

        file_payload = {
            "name": renamed_pdf.name,
            "is_ocr": remote_cfg.get("is_ocr", False),
            "enable_formula": remote_cfg.get("enable_formula", True),
            "enable_table": remote_cfg.get("enable_table", True),
            "language": language,
        }
        page_ranges = remote_cfg.get("page_ranges", "")
        if page_ranges:
            file_payload["page_ranges"] = page_ranges

        payload = {
            "files": [file_payload],
            "model_version": remote_cfg.get("model_version", "vlm"),
        }
        extra_formats = remote_cfg.get("extra_formats", [])
        if extra_formats:
            payload["extra_formats"] = extra_formats

        from utils.retry_client import RetrySession

        url = f"{MINERU_BASE_URL}/file-urls/batch"
        resp = RetrySession.post(url, headers=headers, json=payload)
        data = resp.json()

        if data.get("code") != 0:
            print(f"[FAIL] MinerU API error: code={data.get('code')}, msg={data.get('msg')}")
            tee.close()
            sys.exit(1)

        batch_id = data["data"]["batch_id"]
        upload_url = data["data"]["file_urls"][0]
        t1 = time.time()
        print(f"[OK] Upload URL obtained in {t1 - t0:.1f}s")
        print(f"   batch_id:  {batch_id}")
        print(f"   upload_url: {upload_url[:80]}...")

        # -- Step 2: Upload PDF file ------------------------------------------
        step(f"Uploading {renamed_pdf.name}...")
        t0 = time.time()

        with open(renamed_pdf, "rb") as f:
            resp = RetrySession.put(upload_url, data=f, timeout=300)
            resp.raise_for_status()

        t1 = time.time()
        file_size_kb = renamed_pdf.stat().st_size / 1024
        print(f"[OK] Upload complete in {t1 - t0:.1f}s ({file_size_kb:.0f} KB)")

        # -- Step 3: Poll for completion --------------------------------------
        step("Polling MinerU for processing status...")
        poll_url = f"{MINERU_BASE_URL}/extract-results/batch/{batch_id}"
        poll_count = 0
        t0 = time.time()

        while True:
            elapsed = time.time() - t0
            if elapsed > 1800:
                print(f"[FAIL] MinerU timed out after {elapsed:.0f}s")
                tee.close()
                sys.exit(1)

            resp = RetrySession.get(url=poll_url, headers=headers)
            data = resp.json()

            if data.get("code") != 0:
                print(f"[FAIL] Poll error: code={data.get('code')}, msg={data.get('msg')}")
                tee.close()
                sys.exit(1)

            results = data["data"].get("extract_result", [])
            if not results:
                poll_count += 1
                print(f"   [{poll_count}] Pending... ({elapsed:.0f}s elapsed)")
                time.sleep(10)
                continue

            file_result = results[0]
            state = file_result.get("state", "unknown")
            progress = file_result.get("extract_progress", {})
            extracted = progress.get("extracted_pages", 0)
            total_pages = progress.get("total_pages", 0)

            poll_count += 1

            if state == "done":
                zip_url = file_result["full_zip_url"]
                print(f"[OK] Processing complete after {time.time() - t0:.1f}s ({poll_count} polls)")
                print(f"   Pages: {total_pages}")
                print(f"   ZIP:   {zip_url[:80]}...")
                break
            elif state == "failed":
                err_code = file_result.get("err_code", "")
                err_msg = file_result.get("err_msg", "Unknown error")
                print(f"[FAIL] MinerU processing failed: [{err_code}] {err_msg}")
                tee.close()
                sys.exit(1)
            else:
                pct = int((extracted / total_pages) * 100) if total_pages > 0 else 0
                print(f"   [{poll_count}] state={state}, pages={extracted}/{total_pages} ({pct}%), {elapsed:.0f}s elapsed")
                time.sleep(10)

        # -- Step 4: Download and extract result ZIP --------------------------
        step("Downloading and extracting result ZIP...")
        t0 = time.time()

        resp = RetrySession.get(zip_url, timeout=120)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Save raw zip
        zip_path = output_dir / "mineru_output.zip"
        with open(zip_path, "wb") as f_zip:
            f_zip.write(resp.content)
        print(f"   Raw ZIP saved: {zip_path} ({len(resp.content) / 1024:.1f} KB)")

        # Extract
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            zf.extractall(output_dir)
            md_files = list(output_dir.rglob("*.md"))

        t1 = time.time()
        print(f"[OK] Downloaded and extracted in {t1 - t0:.1f}s")

        if not md_files:
            print("[FAIL] No markdown files found in MinerU output!")
            tee.close()
            sys.exit(1)

        # -- Step 5: Analyze the result ----------------------------------------
        step("Analyzing MinerU output...")

        md_file = md_files[-1]
        md_content = md_file.read_text(encoding="utf-8")
        md_lines = md_content.splitlines()

        print(f"   Markdown file: {md_file.relative_to(output_dir)}")
        print(f"   Size:          {len(md_content):,} chars")
        print(f"   Lines:         {len(md_lines):,}")

        # Headers
        headers_found = [l for l in md_lines if l.startswith("#")]
        print(f"   Headers:       {len(headers_found)}")
        for h in headers_found[:15]:
            print(f"     {h}")
        if len(headers_found) > 15:
            print(f"     ... and {len(headers_found) - 15} more")

        # Check for images
        img_refs = [l for l in md_lines if "![" in l]
        print(f"   Image refs:    {len(img_refs)}")

        # Check for tables
        table_lines = [l for l in md_lines if "|" in l and "---" not in l]
        print(f"   Table lines:   {len(table_lines)}")

        # Preview first 30 lines
        print(f"\n   --- Markdown preview (first 30 lines) ---")
        for line in md_lines[:30]:
            print(f"   {line}")
        if len(md_lines) > 30:
            print(f"   ... ({len(md_lines) - 30} more lines)")

        # Copy to full.md for easy access
        full_md = output_dir / "full.md"
        if md_file != full_md:
            shutil.copy2(md_file, full_md)
            print(f"\n   Copied to: {full_md}")

        # List all extracted files
        print(f"\n   All extracted files:")
        for f in sorted(output_dir.rglob("*")):
            if f.is_file() and f.name != "full.md":
                rel = f.relative_to(output_dir)
                size = f.stat().st_size
                if size > 1024:
                    print(f"     {rel} ({size / 1024:.1f} KB)")
                else:
                    print(f"     {rel} ({size} B)")

    except KeyboardInterrupt:
        print("\n[INTERRUPTED] Test stopped by user.")
        tee.close()
        sys.exit(0)
    except Exception as e:
        print(f"\n[FAIL] Test failed: {e}")
        import traceback
        traceback.print_exc()
        tee.close()
        sys.exit(1)

    # -- Summary -------------------------------------------------------------
    total_elapsed = time.time() - total_start
    banner(f"MinerU Stage Complete ({total_elapsed:.1f}s)")
    print(f"Output:  {output_dir}")
    print(f"Log:     {log_path}")

    # Clean up temp dir
    try:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    except Exception:
        pass

    tee.close()


if __name__ == "__main__":
    main()
