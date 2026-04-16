import os
import sys
import time
import zipfile
import io
import requests
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
        
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            zf.extractall(output_dir)
            md_files = list(Path(output_dir).rglob("*.md"))
            if not md_files:
                raise MinerUConversionError("MinerU succeeded but no Markdown file found.")
            return md_files[-1].read_text(encoding="utf-8") # Usually auto folder has the md

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
        self.base_url = config.get("mineru.local.base_url", "http://127.0.0.1:8000").rstrip("/")
        self.backend = config.get("mineru.local.backend", "hybrid-auto-engine")
        self.parse_method = config.get("mineru.local.parse_method", "auto")
        self.formula_enable = config.get("mineru.local.formula_enable", True)
        self.table_enable = config.get("mineru.local.table_enable", True)

    def process(self, pdf_path: str, output_dir: str) -> Path:
        pdf_path, output_dir = Path(pdf_path), Path(output_dir)
        output_file = output_dir / "full.md"
        
        if output_file.exists():
            logger.info("Markdown already generated. Skipping local conversion.")
            return output_file
            
        logger.info(f"Processing via Local MinerU API (no splitting): {pdf_path.name}")
        
        url = f"{self.base_url}/tasks"
        
        with open(pdf_path, "rb") as f:
            files = {"files": (pdf_path.name, f, "application/pdf")}
            data = {
                "lang_list": self.language,
                "backend": self.backend,
                "parse_method": self.parse_method,
                "formula_enable": "true" if self.formula_enable else "false",
                "table_enable": "true" if self.table_enable else "false",
                "return_md": "true",
                "response_format_zip": "true"
            }
            logger.info(f"Submitting task to {url} with backend={self.backend}")
            resp = RetrySession.post(url, files=files, data=data, timeout=300)
            
            try:
                resp.raise_for_status()
            except requests.HTTPError as e:
                logger.error(f"Local task submission failed: {resp.text}")
                raise MinerUConversionError(f"HTTPError on local API: {e}")

        task_data = resp.json()
        task_id = task_data.get("task_id")
        if not task_id:
            raise MinerUConversionError(f"Failed to get task_id. Response: {task_data}")
            
        logger.info(f"Local Task ID: {task_id}, waiting for completion...")
        
        status_url = f"{self.base_url}/tasks/{task_id}"
        timeout = 1800
        start_time = time.time()
        
        while True:
            if time.time() - start_time > timeout:
                raise TimeoutError("Local MinerU conversion timed out.")
                
            resp = RetrySession.get(status_url)
            resp.raise_for_status()
            status_data = resp.json()
            status = status_data.get("status")
            
            if status == "completed":
                logger.info("Local MinerU extraction completed successfully!")
                break
            elif status in ["failed", "error"]:
                raise MinerUConversionError(f"Local extraction failed: {status_data.get('message', 'Unknown error')}")
                
            logger.info(f"Local MinerU State: {status}...")
            time.sleep(10)
            
        # Download Result Zip
        result_url = f"{self.base_url}/tasks/{task_id}/result"
        logger.info("Downloading and extracting local results...")
        resp = RetrySession.get(result_url, timeout=120)
        resp.raise_for_status()
        
        output_dir.mkdir(parents=True, exist_ok=True)
        # Note: If response_format_zip=True, the result endpoint sends ZIP binary.
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            zf.extractall(output_dir)
            md_files = list(Path(output_dir).rglob("*.md"))
            if not md_files:
                raise MinerUConversionError("Local API succeeded but no Markdown file found in the ZIP.")
                
            md_text = md_files[-1].read_text(encoding="utf-8")
            
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

