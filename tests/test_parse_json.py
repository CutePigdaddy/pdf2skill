"""Tests for LLMClient.parse_json_response."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.llm_client import LLMClient
from utils.logger import LLMParsingError
import pytest


def _make_client(monkeypatch):
    """Create a minimal LLMClient with test env."""
    import os
    monkeypatch.setenv("SILICONFLOW_API_KEY", "test-key-12345678")
    monkeypatch.setenv("LLM_PROVIDER", "siliconflow")
    for k in list(os.environ):
        if k.endswith("_PROVIDER") or k.endswith("_MODEL"):
            monkeypatch.delenv(k, raising=False)
    return LLMClient(stage="skill_engine")


def test_normal_json(monkeypatch):
    client = _make_client(monkeypatch)
    result = client.parse_json_response('{"key": "value"}')
    assert result == {"key": "value"}


def test_think_tags_removed(monkeypatch):
    client = _make_client(monkeypatch)
    raw = '<think>reasoning here</think>{"answer": 42}'
    result = client.parse_json_response(raw)
    assert result == {"answer": 42}


def test_markdown_wrapped_json(monkeypatch):
    client = _make_client(monkeypatch)
    raw = '```json\n{"x": 1}\n```'
    result = client.parse_json_response(raw)
    assert result == {"x": 1}


def test_illegal_json_raises(monkeypatch):
    client = _make_client(monkeypatch)
    with pytest.raises(LLMParsingError):
        client.parse_json_response("this is not json at all")
