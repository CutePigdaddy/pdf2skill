import logging
import sys
from pathlib import Path
from colorama import Fore, Style, init
from config.config import config

init(autoreset=True)

class ColoredFormatter(logging.Formatter):
    COLORS = {
        logging.DEBUG: Fore.CYAN,
        logging.INFO: Fore.GREEN,
        logging.WARNING: Fore.YELLOW,
        logging.ERROR: Fore.RED,
        logging.CRITICAL: Fore.RED + Style.BRIGHT,
    }

    def format(self, record):
        log_fmt = f"{self.COLORS.get(record.levelno, '')}%(asctime)s - %(name)s - %(levelname)s - %(message)s{Style.RESET_ALL}"
        formatter = logging.Formatter(log_fmt, datefmt="%Y-%m-%d %H:%M:%S")
        return formatter.format(record)

def setup_logger(name="pdf2skills", log_level=logging.INFO):
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

class LLMParsingError(PDF2SkillsException):
    """Raised when LLM returns unparseable or unexpected format"""
    pass
