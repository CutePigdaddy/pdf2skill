import os
import time
import json
import zipfile
import io
import requests
import shutil
from pathlib import Path
from tqdm import tqdm
from PyPDF2 import PdfReader, PdfWriter
from config.config import config
from utils.logger import logger, MinerUConversionError, MinerUAPIError, MinerUAuthError, MinerUFileError, MinerUSizeError, MinerUQuotaError, MinerUServiceError
from utils.retry_client import RetrySession

MINERU_BASE_URL = "https://mineru.net/api/v4"

# ── Helper: map API error code to structured exception ──────────────

def _raise_mineru_error(err_code, api_msg=""):
    """Raise the appropriate MinerUAPIError subclass based on error code.

    Maps MinerU API error codes to structured exception types
    (Auth, File, Size, Quota, Service) for user-friendly messages.

    Args:
        err_code: MinerU error code string (e.g. ``"A0202"``).
        api_msg: Raw error message from the API response.
    """
    code = str(err_code) if err_code else ""
    if code in ("A0202", "A0211"):
        raise MinerUAuthError(err_code=code, api_msg=api_msg)
    elif code in ("-60002", "-60003", "-60004"):
        raise MinerUFileError(err_code=code, api_msg=api_msg)
    elif code in ("-60005", "-60006"):
        raise MinerUSizeError(err_code=code, api_msg=api_msg)
    elif code in ("-60018", "-60019"):
        raise MinerUQuotaError(err_code=code, api_msg=api_msg)
    elif code in ("-60007", "-60009", "-60010", "-60001", "-60011"):
        raise MinerUServiceError(err_code=code, api_msg=api_msg)
    else:
        raise MinerUAPIError(err_code=code, api_msg=api_msg)


# ── Remote MinerU Processor (精准解析 API) ──────────────────────────

class RemoteMinerUProcessor:
    """Stage 1 (remote): Upload PDF to the MinerU cloud API, poll for
    completion, and download the resulting Markdown + images.
    """

    def __init__(self, language=None):
        """Initialise with API key and remote configuration.

        Args:
            language: OCR language code (default from config).

        Raises:
            MinerUAuthError: If ``MINERU_API_KEY`` is not set.
        """
        from dotenv import load_dotenv
        load_dotenv()
        self.api_key = os.getenv("MINERU_API_KEY")
        if not self.api_key:
            raise MinerUAuthError(err_code="-NO_TOKEN", api_msg="MINERU_API_KEY not found in environment")
        self.language = language or config.get("mineru.language", "ch")
        self.remote_cfg = config.get("mineru.remote", {})
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        self.page_limit = config.get("pdf.page_limit", 600)
        self.progress_callback = None
        self._bar = None

    def set_progress_callback(self, callback):
        """Register a progress callback.

        Args:
            callback: ``callback(state, extracted_pages, total_pages, start_time)``
        """
        self.progress_callback = callback

    def _emit_progress(self, state, extracted=0, total=0, start_time=None):
        """Update the tqdm progress bar and emit a ``__MINERU_PROGRESS__`` log marker.

        Args:
            state: MinerU task state (``"pending"``, ``"running"``, etc.).
            extracted: Number of pages processed so far.
            total: Total number of pages.
            start_time: Processing start timestamp from the API.
        """
        phase_map = {
            "waiting-file": "上传中",
            "pending": "排队中",
            "running": "解析中",
            "converting": "格式转换中",
            "uploading": "上传中",
        }
        phase_zh = phase_map.get(state, state)
        pct = int((extracted / total) * 100) if total > 0 else 0

        # Structured marker for frontend parsing — debug level keeps it out of
        # the console (tqdm handles the visual bar) but it still reaches the
        # file handler (DEBUG level) and the frontend's subprocess log via
        # an additional DEBUG StreamHandler in _run_pipeline.py.
        progress_data = {"phase": phase_map.get(state, state), "pct": pct, "extracted": extracted, "total": total}
        logger.debug(f"__MINERU_PROGRESS__{json.dumps(progress_data, ensure_ascii=False)}")

        # Update or create the tqdm progress bar
        if not hasattr(self, '_bar') or self._bar is None:
            self._bar = tqdm(total=total or None, desc=f"MinerU {phase_zh}", unit="页", ncols=80)

        # Update total if it changed (MinerU reports 0 initially)
        if total > 0 and (self._bar.total is None or self._bar.total != total):
            self._bar.total = total
            self._bar.refresh()

        # Update position
        if extracted > self._bar.n:
            self._bar.update(extracted - self._bar.n)

        # Update phase label in description
        self._bar.set_description(f"MinerU {phase_zh}")

        if self.progress_callback:
            self.progress_callback(state, extracted, total, start_time)

    def _split_pdf_if_needed(self, pdf_path: Path, output_dir: Path) -> list:
        """Split a PDF into smaller files if it exceeds the page limit.

        Args:
            pdf_path: Path to the source PDF.
            output_dir: Directory to write split files into.

        Returns:
            List of ``Path`` objects — the original file if under the limit,
            or the split files otherwise.
        """
        reader = PdfReader(pdf_path)
        total_pages = len(reader.pages)
        if total_pages <= self.page_limit:
            return [pdf_path]

        logger.info(f"PDF has {total_pages} pages, exceeding {self.page_limit} limit. Splitting...")
        split_dir = output_dir / "splits"
        split_dir.mkdir(parents=True, exist_ok=True)

        split_files = []
        pages_per_split = min(self.page_limit, 200)
        num_splits = (total_pages + pages_per_split - 1) // pages_per_split

        for i in range(num_splits):
            start_page = i * pages_per_split
            end_page = min((i + 1) * pages_per_split, total_pages)

            writer = PdfWriter()
            for page_num in range(start_page, end_page):
                writer.add_page(reader.pages[page_num])

            split_filename = f"{pdf_path.stem}_part{i+1:02d}.pdf"
            split_path = split_dir / split_filename

            with open(split_path, "wb") as output_file:
                writer.write(output_file)

            split_files.append(split_path)
            logger.info(f"Created {split_filename}: pages {start_page+1}-{end_page}")

        return split_files

    # ── Upload Mode: Signed Upload (file-urls/batch) ──────────────

    def _request_upload_url(self, filename: str) -> dict:
        """Request a signed upload URL from the MinerU batch endpoint.

        Args:
            filename: Name of the file to upload.

        Returns:
            Dict with ``"batch_id"`` and ``"upload_url"``.

        Raises:
            MinerUAPIError: If the API returns a non-zero code.
        """
        file_payload = {
            "name": filename,
            "is_ocr": self.remote_cfg.get("is_ocr", False),
            "enable_formula": self.remote_cfg.get("enable_formula", True),
            "enable_table": self.remote_cfg.get("enable_table", True),
            "language": self.language,
        }
        page_ranges = self.remote_cfg.get("page_ranges", "")
        if page_ranges:
            file_payload["page_ranges"] = page_ranges

        payload = {
            "files": [file_payload],
            "model_version": self.remote_cfg.get("model_version", "vlm"),
        }

        extra_formats = self.remote_cfg.get("extra_formats", [])
        if extra_formats:
            payload["extra_formats"] = extra_formats

        url = f"{MINERU_BASE_URL}/file-urls/batch"
        resp = RetrySession.post(url, headers=self.headers, json=payload)
        data = resp.json()
        if data.get("code") != 0:
            _raise_mineru_error(data.get("code"), data.get("msg", ""))
        result = data["data"]
        return {"batch_id": result["batch_id"], "upload_url": result["file_urls"][0]}

    # ── Upload Mode: URL Submit (extract/task) ────────────────────

    def _submit_by_url(self, file_url: str) -> dict:
        """Submit a remote file URL for parsing via the single-task endpoint.

        Args:
            file_url: Publicly accessible URL of the file to parse.

        Returns:
            Dict with ``"task_id"``.

        Raises:
            MinerUAPIError: If the API returns a non-zero code.
        """
        payload = {
            "url": file_url,
            "model_version": self.remote_cfg.get("model_version", "vlm"),
            "is_ocr": self.remote_cfg.get("is_ocr", False),
            "enable_formula": self.remote_cfg.get("enable_formula", True),
            "enable_table": self.remote_cfg.get("enable_table", True),
            "language": self.language,
        }
        page_ranges = self.remote_cfg.get("page_ranges", "")
        if page_ranges:
            payload["page_ranges"] = page_ranges

        extra_formats = self.remote_cfg.get("extra_formats", [])
        if extra_formats:
            payload["extra_formats"] = extra_formats

        if self.remote_cfg.get("no_cache", False):
            payload["no_cache"] = True
            payload["cache_tolerance"] = self.remote_cfg.get("cache_tolerance", 900)

        url = f"{MINERU_BASE_URL}/extract/task"
        resp = RetrySession.post(url, headers=self.headers, json=payload)
        data = resp.json()
        if data.get("code") != 0:
            _raise_mineru_error(data.get("code"), data.get("msg", ""))
        return {"task_id": data["data"]["task_id"]}

    # ── Polling ───────────────────────────────────────────────────

    def _wait_for_completion(self, batch_id: str = None, task_id: str = None, timeout: int = 1800) -> dict:
        """Poll the MinerU API until processing completes or times out.

        Supply *batch_id* for signed-upload mode or *task_id* for
        URL-submit mode.

        Args:
            batch_id: Batch ID from ``_request_upload_url``.
            task_id: Task ID from ``_submit_by_url``.
            timeout: Maximum seconds to wait (default 1800).

        Returns:
            The file result dict from the API on success.

        Raises:
            MinerUServiceError: On timeout.
            MinerUAPIError: On processing failure.
        """
        if batch_id:
            poll_url = f"{MINERU_BASE_URL}/extract-results/batch/{batch_id}"
        else:
            poll_url = f"{MINERU_BASE_URL}/extract/task/{task_id}"

        start_time = time.time()

        while True:
            elapsed = time.time() - start_time
            if elapsed > timeout:
                raise MinerUServiceError(err_code="-TIMEOUT", api_msg="MinerU conversion timed out")

            resp = RetrySession.get(url=poll_url, headers=self.headers)
            data = resp.json()
            if data.get("code") != 0:
                _raise_mineru_error(data.get("code"), data.get("msg", ""))

            # Extract state and progress depending on endpoint type
            if batch_id:
                results = data["data"].get("extract_result", [])
                if not results:
                    self._emit_progress("pending")
                    time.sleep(10)
                    continue
                file_result = results[0]
            else:
                file_result = data["data"]

            state = file_result.get("state", "unknown")

            if state == "done":
                self._emit_progress("done", total=1, extracted=1)
                if hasattr(self, '_bar') and self._bar is not None:
                    self._bar.close()
                    self._bar = None
                return file_result
            elif state == "failed":
                err_code = file_result.get("err_code", "")
                err_msg = file_result.get("err_msg", "Unknown error")
                progress_data = {"phase": "failed", "err_msg": err_msg, "err_code": str(err_code)}
                logger.debug(f"__MINERU_PROGRESS__{json.dumps(progress_data, ensure_ascii=False)}")
                if hasattr(self, '_bar') and self._bar is not None:
                    self._bar.close()
                    self._bar = None
                _raise_mineru_error(err_code, err_msg)
            else:
                progress = file_result.get("extract_progress", {})
                extracted = progress.get("extracted_pages", 0)
                total = progress.get("total_pages", 0)
                self._emit_progress(state, extracted, total, progress.get("start_time"))
                time.sleep(10)

    # ── Download & Extract ────────────────────────────────────────

    def _download_and_extract(self, zip_url: str, output_dir: Path) -> str:
        """Download the result ZIP and extract the Markdown + images.

        Args:
            zip_url: URL of the result ZIP archive.
            output_dir: Directory to extract into.

        Returns:
            The extracted Markdown text.

        Raises:
            MinerUConversionError: If no Markdown file is found in the archive.
        """
        logger.info("Downloading and extracting results...")
        resp = RetrySession.get(zip_url, timeout=120)
        output_dir.mkdir(parents=True, exist_ok=True)

        zip_path = output_dir / "remote_mineru_output.zip"
        with open(zip_path, "wb") as f_zip:
            f_zip.write(resp.content)
        logger.info(f"Saved raw remote zip to {zip_path}")

        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            zf.extractall(output_dir)
            md_files = list(Path(output_dir).rglob("*.md"))
            if not md_files:
                raise MinerUConversionError("MinerU succeeded but no Markdown file found.")

            md_file = md_files[-1]
            img_dir = md_file.parent / "images"
            if img_dir.exists():
                target_img_dir = output_dir.parent / "images"
                target_img_dir.mkdir(parents=True, exist_ok=True)
                for img in img_dir.glob("*"):
                    if img.is_file():
                        dst = target_img_dir / img.name
                        if img.resolve() != dst.resolve():
                            shutil.copy2(img, dst)

            return md_file.read_text(encoding="utf-8")

    # ── Process single PDF ────────────────────────────────────────

    def _process_single_pdf(self, pdf_path: Path, output_dir: Path) -> str:
        """Upload a single PDF, wait for completion, and return raw Markdown.

        Args:
            pdf_path: Path to the PDF file.
            output_dir: Directory for extracted results.

        Returns:
            Raw Markdown text from MinerU.
        """
        # Always use signed upload for local files
        url_info = self._request_upload_url(pdf_path.name)

        with open(pdf_path, "rb") as f:
            resp = RetrySession.put(url_info["upload_url"], data=f, timeout=300)
            resp.raise_for_status()

        result = self._wait_for_completion(batch_id=url_info["batch_id"])
        zip_url = result["full_zip_url"]

        return self._download_and_extract(zip_url, output_dir / pdf_path.stem)

    def process_by_url(self, file_url: str, output_dir: str) -> Path:
        """Process a remote file URL directly (URL submit mode).

        Args:
            file_url: Publicly accessible URL of the file.
            output_dir: Output directory path.

        Returns:
            Path to the generated ``full.md`` file.
        """
        output_dir = Path(output_dir)
        output_file = output_dir / "full.md"

        if output_file.exists():
            logger.info("Markdown already generated. Skipping conversion.")
            return output_file

        task_info = self._submit_by_url(file_url)
        result = self._wait_for_completion(task_id=task_info["task_id"])
        zip_url = result["full_zip_url"]

        md_text = self._download_and_extract(zip_url, output_dir)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(md_text, encoding="utf-8")
        logger.info(f"Markdown saved to: {output_file}")
        return output_file

    def process(self, pdf_path: str, output_dir: str) -> Path:
        """Convert a PDF to Markdown via the MinerU remote API.

        Splits the PDF if it exceeds the page limit, processes each part,
        and concatenates the results into ``full.md``.

        Args:
            pdf_path: Path to the source PDF.
            output_dir: Output directory path.

        Returns:
            Path to the generated ``full.md`` file.
        """
        pdf_path, output_dir = Path(pdf_path), Path(output_dir)
        output_file = output_dir / "full.md"

        if output_file.exists():
            logger.info("Markdown already generated. Skipping conversion.")
            return output_file

        split_files = self._split_pdf_if_needed(pdf_path, output_dir)
        md_contents = []

        for i, split_file in enumerate(split_files):
            logger.info(f"Processing split {i+1}/{len(split_files)}: {split_file.name}")
            content = self._process_single_pdf(split_file, output_dir)
            if i > 0:
                md_contents.append("\n\n---\n\n")
                md_contents.append(f"# Part {i+1}\n\n")
            md_contents.append(content)

        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text("".join(md_contents), encoding="utf-8")
        logger.info(f"Combined markdown saved to: {output_file}")
        return output_file


# ── Local MinerU Processor ───────────────────────────────────────────

class LocalMinerUProcessor:
    """Stage 1 (local): Convert PDF to Markdown via a local MinerU Gradio server."""

    def __init__(self, language=None):
        """Initialise with local server configuration.

        Args:
            language: OCR language code (default from config).

        Raises:
            MinerUConversionError: If ``gradio_client`` is not installed.
        """
        self.language = language or config.get("mineru.language", "ch")
        self.base_url = config.get("mineru.local.base_url", "http://127.0.0.1:7860").rstrip("/")
        self.backend = config.get("mineru.local.backend", "hybrid-auto-engine")
        self.parse_method = config.get("mineru.local.parse_method", "auto")
        self.formula_enable = config.get("mineru.local.formula_enable", True)
        self.table_enable = config.get("mineru.local.table_enable", True)

        # Load gradual gradio_client
        try:
            from gradio_client import Client, handle_file
            self.Client = Client
            self.handle_file = handle_file
        except ImportError:
            logger.error("gradio_client is required for new Local MinerU API. Please run: pip install gradio_client")
            raise MinerUConversionError("gradio_client not installed")

    def _get_gradio_language(self, short_lang):
        """Map short language codes to the exact Gradio API string.

        Args:
            short_lang: Short code like ``"ch"`` or ``"en"``.

        Returns:
            Full Gradio language string, e.g.
            ``"ch (Chinese, English, Chinese Traditional)"``.
        """
        lang_map = {
            "ch": "ch (Chinese, English, Chinese Traditional)",
            "ch_lite": "ch_lite (Chinese, English, Chinese Traditional, Japanese)",
            "ch_server": "ch_server (Chinese, English, Chinese Traditional, Japanese)",
            "en": "en (English)",
            "korean": "korean (Korean, English)",
            "japan": "japan (Chinese, English, Chinese Traditional, Japanese)",
            "chinese_cht": "chinese_cht (Chinese, English, Chinese Traditional, Japanese)",
            "east_slavic": "east_slavic (Russian, Belarusian, Ukrainian, English)",
            "cyrillic": "cyrillic (Russian, Belarusian, Ukrainian, Serbian (Cyrillic), Bulgarian, Mongolian, Abkhazian, Adyghe, Kabardian, Avar, Dargin, Ingush, Chechen, Lak, Lezgin, Tabasaran, Kazakh, Kyrgyz, Tajik, Macedonian, Tatar, Chuvash, Bashkir, Malian, Moldovan, Udmurt, Komi, Ossetian, Buryat, Kalmyk, Tuvan, Sakha, Karakalpak, English)",
        }
        return lang_map.get(short_lang, "ch (Chinese, English, Chinese Traditional)")

    def process(self, pdf_path: str, output_dir: str) -> Path:
        """Convert a PDF to Markdown via the local MinerU Gradio API.

        Args:
            pdf_path: Path to the source PDF.
            output_dir: Output directory path.

        Returns:
            Path to the generated ``full.md`` file.

        Raises:
            MinerUServiceError: On connection or prediction failure.
            MinerUConversionError: If no Markdown is found in the result.
        """
        pdf_path, output_dir = Path(pdf_path), Path(output_dir)
        output_file = output_dir / "full.md"

        if output_file.exists():
            logger.info("Markdown already generated. Skipping local conversion.")
            return output_file

        logger.info(f"Processing via Local MinerU Gradio API (no splitting): {pdf_path.name}")

        try:
            client = self.Client(self.base_url)
        except Exception as e:
            raise MinerUServiceError(err_code="-LOCAL_CONN", api_msg=f"无法连接到本地 MinerU 服务 ({self.base_url}): {e}")

        # Resolve correct lang parameter
        raw_lang = self.language[0] if isinstance(self.language, list) else self.language
        gradio_lang = self._get_gradio_language(raw_lang)

        logger.info(f"Submitting task to {self.base_url} with backend={self.backend}")
        try:
            result = client.predict(
                file_path=self.handle_file(str(pdf_path)),
                end_pages=1000,
                is_ocr=False,
                formula_enable=self.formula_enable,
                table_enable=self.table_enable,
                language=gradio_lang,
                backend=self.backend,
                url="http://localhost:30000",
                api_name="/convert_to_markdown_stream",
            )
        except Exception as e:
            logger.error(f"Gradio API Prediction failed: {e}")
            raise MinerUServiceError(err_code="-LOCAL_PREDICT", api_msg=f"本地 MinerU 解析失败: {e}")

        # result is a tuple, result[1] is the ZIP filepath
        output_zip_path = result[1] if len(result) > 1 else None

        if not output_zip_path or not os.path.exists(output_zip_path):
            raise MinerUConversionError(f"Gradio API did not return a valid ZIP path: {result}")

        logger.info("Downloading and extracting local results...")
        output_dir.mkdir(parents=True, exist_ok=True)

        zip_path = output_dir / f"{pdf_path.stem}_mineru_output.zip"
        shutil.copy2(output_zip_path, zip_path)
        logger.info(f"Saved raw local zip to {zip_path}")

        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(output_dir)
            md_files = list(Path(output_dir).rglob("*.md"))
            if not md_files:
                raise MinerUConversionError("Local API succeeded but no Markdown file found in the ZIP.")

            md_file = md_files[-1]
            img_dir = md_file.parent / "images"
            if img_dir.exists():
                target_img_dir = output_file.parent / "images"
                target_img_dir.mkdir(parents=True, exist_ok=True)
                for img in img_dir.glob("*"):
                    if img.is_file():
                        dst = target_img_dir / img.name
                        if img.resolve() != dst.resolve():
                            shutil.copy2(img, dst)

            md_text = md_file.read_text(encoding="utf-8")

        output_file.write_text(md_text, encoding="utf-8")
        logger.info(f"Local markdown saved to: {output_file}")
        return output_file


# ── PDF Processor Facade ─────────────────────────────────────────────

class PDFProcessor:
    """Facade that delegates to either the remote or local MinerU processor
    based on ``mineru.api_mode`` in settings.
    """

    def __init__(self, language=None):
        """Select the appropriate processor based on configuration.

        Args:
            language: OCR language code override.
        """
        self.mode = config.get("mineru.api_mode", "remote").lower()
        if self.mode == "local":
            self.processor = LocalMinerUProcessor(language)
        else:
            self.processor = RemoteMinerUProcessor(language)

    def process(self, pdf_path: str, output_dir: str) -> Path:
        """Convert a PDF to Markdown using the configured processor.

        Args:
            pdf_path: Path to the source PDF.
            output_dir: Output directory path.

        Returns:
            Path to the generated ``full.md`` file.
        """
        return self.processor.process(pdf_path, output_dir)
