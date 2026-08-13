"""
Unit tests for the frontend server's GET /api/models endpoint.

Calls the endpoint function directly with ``requests.get`` mocked —
no HTTP server or httpx/TestClient required.
"""

import sys
from pathlib import Path

import pytest
import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "frontend"))

import server as fe_server  # noqa: E402


class FakeResponse:
    """Minimal stand-in for requests.Response."""

    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def test_models_success_sorted_and_deduped(monkeypatch):
    """200 with a data array -> ok=True, ids deduped and sorted."""
    monkeypatch.setenv("SILICONFLOW_API_KEY", "sk-test")
    fake = FakeResponse(payload={"data": [{"id": "b-model"}, {"id": "a-model"}, {"id": "b-model"}]})
    monkeypatch.setattr(fe_server.requests, "get", lambda *a, **k: fake)

    result = fe_server.list_models("siliconflow")

    assert result["ok"] is True
    assert result["models"] == ["a-model", "b-model"]
    assert result["count"] == 2
    assert result["provider"] == "siliconflow"


def test_models_unknown_provider(monkeypatch):
    """Provider not in settings.yaml -> UNKNOWN_PROVIDER."""
    result = fe_server.list_models("nonexistent")

    assert result["ok"] is False
    assert result["error"]["code"] == "UNKNOWN_PROVIDER"
    assert result["error"]["message"]
    assert result["error"]["advice"]


def test_models_no_api_key(monkeypatch):
    """No key in env for either resolution path -> NO_API_KEY."""
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)

    result = fe_server.list_models("siliconflow")

    assert result["ok"] is False
    assert result["error"]["code"] == "NO_API_KEY"


def test_models_upstream_401(monkeypatch):
    """Upstream 401 -> AUTH_FAILED with key-reset advice."""
    monkeypatch.setenv("SILICONFLOW_API_KEY", "sk-bad")
    monkeypatch.setattr(fe_server.requests, "get", lambda *a, **k: FakeResponse(status_code=401))

    result = fe_server.list_models("siliconflow")

    assert result["ok"] is False
    assert result["error"]["code"] == "AUTH_FAILED"
    assert "API Key" in result["error"]["advice"]


def test_models_upstream_404(monkeypatch):
    """Upstream 404 -> ENDPOINT_NOT_FOUND (provider may not support /models)."""
    monkeypatch.setenv("SILICONFLOW_API_KEY", "sk-test")
    monkeypatch.setattr(fe_server.requests, "get", lambda *a, **k: FakeResponse(status_code=404))

    result = fe_server.list_models("siliconflow")

    assert result["ok"] is False
    assert result["error"]["code"] == "ENDPOINT_NOT_FOUND"


def test_models_connection_failed(monkeypatch):
    """requests.ConnectionError -> CONNECT_FAILED with network advice."""
    monkeypatch.setenv("SILICONFLOW_API_KEY", "sk-test")

    def fake_get(*a, **k):
        raise requests.ConnectionError("boom")

    monkeypatch.setattr(fe_server.requests, "get", fake_get)

    result = fe_server.list_models("siliconflow")

    assert result["ok"] is False
    assert result["error"]["code"] == "CONNECT_FAILED"
    assert "网络" in result["error"]["advice"]
