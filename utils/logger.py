import logging
import sys
from pathlib import Path
from colorama import Fore, Style, init
from config.config import config

init(autoreset=True)

class ColoredFormatter(logging.Formatter):
    """Log formatter that adds ANSI colour codes based on log level."""

    COLORS = {
        logging.DEBUG: Fore.CYAN,
        logging.INFO: Fore.GREEN,
        logging.WARNING: Fore.YELLOW,
        logging.ERROR: Fore.RED,
        logging.CRITICAL: Fore.RED + Style.BRIGHT,
    }

    def format(self, record):
        """Format *record* with a colour prefix and reset suffix."""
        log_fmt = f"{self.COLORS.get(record.levelno, '')}%(asctime)s - %(name)s - %(levelname)s - %(message)s{Style.RESET_ALL}"
        formatter = logging.Formatter(log_fmt, datefmt="%Y-%m-%d %H:%M:%S")
        return formatter.format(record)

def setup_logger(name="pdf2skills", log_level=logging.INFO):
    """Create and configure the application logger.

    Adds a coloured stdout handler (INFO+) and a file handler writing
    to ``logs/app.log`` (DEBUG+).  Returns an existing logger unchanged
    on repeated calls.

    Args:
        name: Logger name.
        log_level: Minimum level for the console handler.

    Returns:
        Configured ``logging.Logger`` instance.
    """
    logger = logging.getLogger(name)
    logger.setLevel(log_level)
    
    if logger.hasHandlers():
        return logger
        
    logs_dir = Path(config.get("paths.logs_dir", "logs"))
    logs_dir.mkdir(parents=True, exist_ok=True)
    
    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(log_level)
    ch.setFormatter(ColoredFormatter())
    
    # File handler
    fh = logging.FileHandler(logs_dir / "app.log", encoding='utf-8')
    fh.setLevel(logging.DEBUG)
    fh_formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    fh.setFormatter(fh_formatter)
    
    logger.addHandler(ch)
    logger.addHandler(fh)
    
    return logger

logger = setup_logger()

# Standard Business Exceptions
class PDF2SkillsException(Exception):
    """Base exception for PDF2Skills"""
    def __init__(self, message, context=None):
        super().__init__(message)
        self.context = context

class MinerUConversionError(PDF2SkillsException):
    """Raised when MinerU conversion fails"""
    pass

class MinerUAPIError(MinerUConversionError):
    """Structured MinerU API error with error code and actionable advice."""

    ERROR_MAP = {
        "A0202":  {"zh": "API Token 错误", "advice": "请检查 API Key 是否正确，或在 API 管理页面重新创建"},
        "A0211":  {"zh": "API Token 过期", "advice": "请在 API 管理页面创建新 Token"},
        "-500":   {"zh": "请求参数错误", "advice": "请检查 MinerU 配置项是否正确"},
        "-60001": {"zh": "生成上传链接失败", "advice": "请稍后重试"},
        "-60002": {"zh": "文件类型不支持", "advice": "请确保文件为 PDF/Word/PPT/Excel/图片，且文件名含正确后缀"},
        "-60003": {"zh": "文件读取失败", "advice": "文件可能已损坏，请重新上传"},
        "-60004": {"zh": "空文件", "advice": "请上传有效文件"},
        "-60005": {"zh": "文件超过 200MB", "advice": "请拆分文件后重新上传"},
        "-60006": {"zh": "文件页数超限", "advice": "请在配置中设置 page_ranges 或拆分文件"},
        "-60007": {"zh": "模型服务不可用", "advice": "请稍后重试"},
        "-60008": {"zh": "文件读取超时", "advice": "请检查文件 URL 是否可访问"},
        "-60009": {"zh": "任务队列已满", "advice": "请稍后重试"},
        "-60010": {"zh": "解析失败", "advice": "请稍后重试，或尝试切换 model_version"},
        "-60011": {"zh": "获取有效文件失败", "advice": "请确保文件已上传"},
        "-60015": {"zh": "文件转换失败", "advice": "请手动转为 PDF 后重新上传"},
        "-60018": {"zh": "每日配额已用完", "advice": "免费额度已用尽，请明日再试"},
        "-NO_TOKEN": {"zh": "API Key 未设置", "advice": "请在配置页面设置 MinerU API Key"},
    }

    def __init__(self, err_code=None, api_msg="", context=None):
        self.err_code = str(err_code) if err_code else ""
        self.api_msg = api_msg
        mapped = self.ERROR_MAP.get(self.err_code, {})
        self.zh_msg = mapped.get("zh", api_msg or "未知错误")
        self.advice = mapped.get("advice", "请稍后重试或检查配置")
        super().__init__(f"{self.zh_msg} — {self.advice}", context=context)

class MinerUAuthError(MinerUAPIError):
    """Token-related errors (A0202, A0211)"""
    pass

class MinerUFileError(MinerUAPIError):
    """File format/read errors (-60002 to -60004)"""
    pass

class MinerUSizeError(MinerUAPIError):
    """File size/page limit errors (-60005, -60006)"""
    pass

class MinerUQuotaError(MinerUAPIError):
    """Quota exceeded errors (-60018)"""
    pass

class MinerUServiceError(MinerUAPIError):
    """Service availability errors (-60007, -60009, -60010)"""
    pass

class LLMParsingError(PDF2SkillsException):
    """Raised when LLM returns unparseable or unexpected format"""
    pass
