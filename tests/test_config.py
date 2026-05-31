"""Tests for Config.merge_env_vars and safe initialization."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.config import Config


def test_env_override_int(monkeypatch, tmp_path):
    """Environment variable PDF_PAGE_LIMIT should override config as int."""
    monkeypatch.setenv("PDF_PAGE_LIMIT", "50")
    Config._instance = None
    cfg = Config(config_path=tmp_path / "nonexistent.yaml")
    cfg._config = {"pdf": {"page_limit": 200, "chunk_merge_threshold": 5000, "chunk_min_threshold": 1000},
                   "mineru": {"api_mode": "remote", "language": "en", "local": {}},
                   "llm": {"routers": {}, "providers": {}}}
    cfg.merge_env_vars()
    assert cfg.get("pdf.page_limit") == 50


def test_env_override_bool(monkeypatch, tmp_path):
    """Boolean env vars should be parsed correctly."""
    monkeypatch.setenv("MINERU_LOCAL_FORMULA_ENABLE", "true")
    Config._instance = None
    cfg = Config(config_path=tmp_path / "nonexistent.yaml")
    cfg._config = {"pdf": {"page_limit": 200, "chunk_merge_threshold": 5000, "chunk_min_threshold": 1000},
                   "mineru": {"api_mode": "remote", "language": "en", "local": {"formula_enable": False, "table_enable": False, "base_url": "", "backend": "", "parse_method": ""}},
                   "llm": {"routers": {}, "providers": {}}}
    cfg.merge_env_vars()
    assert cfg.get("mineru.local.formula_enable") is True


def test_missing_yaml_no_crash(tmp_path):
    """Config should not crash if settings.yaml is missing."""
    Config._instance = None
    cfg = Config(config_path=tmp_path / "nonexistent.yaml")
    assert isinstance(cfg._config, dict)
    assert cfg.get("nonexistent.key", "default") == "default"


def test_config_singleton_reset(tmp_path):
    """Config singleton can be reset for testing."""
    Config._instance = None
    cfg1 = Config(config_path=tmp_path / "nonexistent.yaml")
    cfg1._config = {"test": 1}
    assert cfg1.get("test") == 1
    Config._instance = None
