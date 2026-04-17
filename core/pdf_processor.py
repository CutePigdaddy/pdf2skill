import os
import sys
import time
import zipfile
import io
import requests
import shutil
from pathlib import Path
from PyPDF2 import PdfReader, PdfWriter
from config.config import config
from utils.logger import logger, MinerUConversionError
from utils.retry_client import RetrySession
from dotenv import load_dotenv

load_dotenv()

MINERU_API_KEY = os.getenv("MINERU_API_KEY")
MINERU_BASE_URL = "https://mineru.net/api/v4"

class RemoteMinerUProcessor:
    def __init__(self, language=None):
        self.api_key = MINERU_API_KEY
        if not self.api_key:
            logger.warning("MINERU_API_KEY not found in environment for Remote API")
        self.language = language or config.get("mineru.language", "ch")
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        self.page_limit = config.get("pdf.page_limit", 600)

    def _split_pdf_if_needed(self, pdf_path: Path, output_dir: Path) -> list:
        """Physical splitting of PDF if pages exceed limit."""
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

    def _request_upload_url(self, filename: str) -> dict:
        url = f"{MINERU_BASE_URL}/file-urls/batch"
        payload = {
            "files": [{
                "name": filename,
                "is_ocr": True,
                "enable_formula": True,
                "enable_table": True,
                "language": self.language,
            }]
        }
        resp = RetrySession.post(url, headers=self.headers, json=payload)
        data = resp.json()
        if data.get("code") != 0:
            raise MinerUConversionError(f"API error: {data.get('msg')}")
        result = data["data"]
        return {"batch_id": result["batch_id"], "upload_url": result["file_urls"][0]}

    def _upload_file(self, upload_url: str, file_path: Path):
        logger.info(f"Uploading file: {file_path.name}")
        with open(file_path, "rb") as f:
            resp = RetrySession.post(upload_url, data=f, timeout=300) # Large timeout for files
        logger.info("Upload complete!")
        return True

    def _wait_for_completion(self, batch_id: str, timeout: int = 1800) -> dict:
        url = f"{MINERU_BASE_URL}/extract-results/batch/{batch_id}"
        start_time = time.time()
        
        while True:
            elapsed = time.time() - start_time
            if elapsed > timeout:
                raise TimeoutError("MinerU conversion timed out.")
                
            resp = RetrySession.get(url, headers=self.headers)
            data = resp.json()
            if data.get("code") != 0:
                raise MinerUConversionError(f"API error: {data.get('msg')}")
                
            results = data["data"].get("extract_result", [])
            if not results:
                time.sleep(10)
                continue
                
            file_result = results[0]
            state = file_result.get("state", "unknown")
            
            if state == "done":
                return file_result
            elif state == "failed":
                raise MinerUConversionError(f"Extraction failed: {file_result.get('err_msg')}")
            else:
                progress = file_result.get("extract_progress", {})
                logger.info(f"MinerU State: {state} | Progress: {progress.get('extracted_pages', 0)}/{progress.get('total_pages', '?')}")
                time.sleep(10)

    def _download_and_extract(self, zip_url: str, output_dir: Path) -> str:
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
                        shutil.copy2(img, target_img_dir / img.name)
                        
            return md_file.read_text(encoding="utf-8") # Usually auto folder has the md

    def _process_single_pdf(self, pdf_path: Path, output_dir: Path) -> str:
        """Upload, wait, download and return raw markdown string."""
        url_info = self._request_upload_url(pdf_path.name)
        
        with open(pdf_path, "rb") as f:
            resp = requests.put(url_info["upload_url"], data=f)
            resp.raise_for_status()

        result = self._wait_for_completion(url_info["batch_id"])
        zip_url = result["full_zip_url"]
        
        return self._download_and_extract(zip_url, output_dir / pdf_path.stem)

    def process(self, pdf_path: str, output_dir: str) -> Path:
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


class LocalMinerUProcessor:
    def __init__(self, language=None):
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
        """Map short lang codes to exact Gradio string required by the API."""
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
        pdf_path, output_dir = Path(pdf_path), Path(output_dir)
        output_file = output_dir / "full.md"

        if output_file.exists():
            logger.info("Markdown already generated. Skipping local conversion.")
            return output_file

        logger.info(f"Processing via Local MinerU Gradio API (no splitting): {pdf_path.name}")

        try:
            client = self.Client(self.base_url)
        except Exception as e:
            raise MinerUConversionError(f"Failed to connect to Gradio server {self.base_url}: {e}")

        # Resolve correct lang parameter
        raw_lang = self.language[0] if isinstance(self.language, list) else self.language
        gradio_lang = self._get_gradio_language(raw_lang)

        logger.info(f"Submitting task to {self.base_url} with backend={self.backend}")
        try:
            result = client.predict(
                file_path=self.handle_file(str(pdf_path)),
                end_pages=10000,
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
            raise MinerUConversionError(f"Prediction failed on local Gradio API: {e}")

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
                        shutil.copy2(img, target_img_dir / img.name)

            md_text = md_file.read_text(encoding="utf-8")
            
        output_file.write_text(md_text, encoding="utf-8")
        logger.info(f"Local markdown saved to: {output_file}")
        return output_file


class PDFProcessor:
    def __init__(self, language=None):
        # Read the global configuration mode (default is 'remote')
        self.mode = config.get("mineru.api_mode", "remote").lower()
        if self.mode == "local":
            self.processor = LocalMinerUProcessor(language)
        else:
            self.processor = RemoteMinerUProcessor(language)
            
    def process(self, pdf_path: str, output_dir: str) -> Path:
        return self.processor.process(pdf_path, output_dir)

