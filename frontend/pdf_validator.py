"""Pre-upload validation for PDF files.

Performs a series of checks (fastest first) on an uploaded PDF before
saving it to the ``inputs/`` directory.  Each failing check returns a
structured error dict with ``code``, ``message`` (Chinese), and ``advice``.
"""

import io
import os
import re
from typing import Optional

from PyPDF2 import PdfReader

# MinerU remote API enforces a 200 MB limit; mirror it locally.
MAX_FILE_SIZE = 200 * 1024 * 1024  # 200 MB


def validate_pdf(file_bytes: bytes, filename: str, settings: dict) -> Optional[dict]:
    """Validate an uploaded PDF before saving to ``inputs/``.

    Args:
        file_bytes: Raw file content.
        filename: Original filename from the upload form.
        settings: Current settings dict (for ``pdf.page_limit`` etc.).

    Returns:
        ``None`` if all checks pass, or an error dict
        ``{"code": str, "message": str, "advice": str}``.
    """
    # 1. Filename presence
    if not filename:
        return {
            "code": "NAME_MISSING",
            "message": "未提供文件名",
            "advice": "请选择一个文件后上传",
        }

    # 2. Filename extension
    if not filename.lower().endswith(".pdf"):
        return {
            "code": "FORMAT_EXTENSION",
            "message": "文件扩展名不是 .pdf",
            "advice": "请确保上传 .pdf 后缀的文件",
        }

    # 3. Filename safety
    if ".." in filename or "/" in filename or "\\" in filename or "\x00" in filename:
        return {
            "code": "NAME_INVALID",
            "message": "文件名包含非法字符",
            "advice": "请使用仅含字母、数字、中文、下划线和连字符的文件名",
        }

    # 4. File not empty
    if len(file_bytes) == 0:
        return {
            "code": "SIZE_EMPTY",
            "message": "文件为空（0 字节）",
            "advice": "请上传有效的 PDF 文件",
        }

    # 5. File size limit
    size_mb = len(file_bytes) / (1024 * 1024)
    if len(file_bytes) > MAX_FILE_SIZE:
        return {
            "code": "SIZE_EXCEED",
            "message": f"文件大小超过 200MB 限制（当前 {size_mb:.1f}MB）",
            "advice": "请拆分文件后重新上传，或使用本地模式处理",
        }

    # 6. PDF magic bytes
    if not file_bytes.startswith(b"%PDF-"):
        return {
            "code": "FORMAT_INVALID",
            "message": "文件内容不是有效的 PDF 格式",
            "advice": "请确保上传真正的 PDF 文件，而非重命名的其他格式",
        }

    # 7. PDF parseability (slowest check — requires full cross-ref parse)
    try:
        reader = PdfReader(io.BytesIO(file_bytes))
    except Exception:
        return {
            "code": "FORMAT_CORRUPT",
            "message": "PDF 文件已损坏，无法解析",
            "advice": "请尝试用 PDF 阅读器打开确认文件完整性，或重新导出 PDF",
        }

    # 8. Page count limit
    page_limit = settings.get("pdf", {}).get("page_limit", 200)
    page_count = len(reader.pages)
    if page_count > page_limit:
        return {
            "code": "PAGE_EXCEED",
            "message": f"PDF 页数超过限制（当前 {page_count} 页，上限 {page_limit} 页）",
            "advice": "请在配置中调高页数限制，或拆分文件后重新上传",
        }

    # 9. Encrypted PDF
    if reader.is_encrypted:
        return {
            "code": "FORMAT_ENCRYPTED",
            "message": "PDF 文件已加密，无法处理",
            "advice": "请先解除密码保护后重新上传",
        }

    return None


def sanitize_filename(filename: str) -> str:
    """Sanitize a filename: strip path components, remove dangerous chars.

    Returns:
        A safe basename string.

    Raises:
        ValueError: If the filename is empty or obviously dangerous.
    """
    name = os.path.basename(filename)
    if not name or name in (".", "..") or "\x00" in name:
        raise ValueError("Invalid filename")
    # Keep letters, digits, CJK, underscores, hyphens, and dots.
    name = re.sub(r"[^\w.\-]", "_", name, flags=re.UNICODE)
    return name
