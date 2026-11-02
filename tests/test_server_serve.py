"""Smoke tests for frontend/server.py: built frontend + mounted proxy API."""

import sys
from pathlib import Path

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "frontend"))

import server  # noqa: E402


def make_client(host: str = "127.0.0.1:8501") -> TestClient:
    return TestClient(server.app, base_url=f"http://{host}")


def test_api_mounted_on_8501():
    resp = make_client().get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_foreign_host_rejected():
    resp = make_client("attacker.example:8501").get("/api/health")
    assert resp.status_code == 403


def test_index_explains_missing_build(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "INDEX_FILE", tmp_path / "missing.html")
    resp = make_client().get("/")
    assert resp.status_code == 503
    assert "npm run build" in resp.text
