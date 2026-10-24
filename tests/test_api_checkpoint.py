"""
Unit tests for the checkpoint API: GET /api/checkpoint and the
from_stage/restart handling in POST /api/run.

Calls endpoint functions directly — no HTTP server required.
"""

import json
import sys
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "frontend"))

import server as fe_server  # noqa: E402


# ── GET /api/checkpoint ────────────────────────────────────────────

def test_checkpoint_none():
    result = fe_server.get_checkpoint("outputs/does-not-exist")
    assert result["exists"] is False
    assert result["completed_stages"] == []


def test_checkpoint_empty():
    assert fe_server.get_checkpoint("")["exists"] is False


def test_checkpoint_reads_stages(tmp_path, monkeypatch):
    out = tmp_path / "outputs" / "mybook"
    out.mkdir(parents=True)
    (out / ".checkpoint.json").write_text(
        json.dumps({"version": 1, "completed_stages": ["pdf_conversion", "llm_chunking"], "data": {}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(fe_server, "OUTPUTS_DIR", tmp_path / "outputs")
    monkeypatch.setattr(fe_server, "PROJECT_ROOT", tmp_path)

    result = fe_server.get_checkpoint("outputs/mybook")
    assert result["exists"] is True
    assert result["version"] == 1
    assert result["completed_stages"] == ["pdf_conversion", "llm_chunking"]


def test_checkpoint_corrupt(tmp_path, monkeypatch):
    out = tmp_path / "outputs" / "bad"
    out.mkdir(parents=True)
    (out / ".checkpoint.json").write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(fe_server, "OUTPUTS_DIR", tmp_path / "outputs")
    monkeypatch.setattr(fe_server, "PROJECT_ROOT", tmp_path)

    result = fe_server.get_checkpoint("outputs/bad")
    assert result["exists"] is False
    assert result["completed_stages"] == []


def test_checkpoint_rejects_outside_outputs(tmp_path, monkeypatch):
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / ".checkpoint.json").write_text(json.dumps({"completed_stages": ["pdf_conversion"]}), encoding="utf-8")
    monkeypatch.setattr(fe_server, "OUTPUTS_DIR", tmp_path / "outputs")
    monkeypatch.setattr(fe_server, "PROJECT_ROOT", tmp_path)

    result = fe_server.get_checkpoint("../secret")
    assert result["exists"] is False
    assert result["completed_stages"] == []


# ── POST /api/run from_stage / restart ─────────────────────────────

def _run_start_pipeline(tmp_path, monkeypatch, body_extra):
    """Call start_pipeline with a stubbed subprocess, capture the cmd."""
    captured = {}

    class FakeProc:
        returncode = 0

    def fake_popen(cmd, **kw):
        captured["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr(fe_server.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(fe_server, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(fe_server, "INPUTS_DIR", tmp_path / "inputs")
    monkeypatch.setattr(fe_server, "OUTPUTS_DIR", tmp_path / "outputs")

    inp = tmp_path / "inputs"
    inp.mkdir(exist_ok=True)
    (inp / "book.pdf").write_bytes(b"%PDF-1.4 fake")

    body = {
        "input_file": "book.pdf",
        "mode": "pdf",
        "output_dir": "outputs/book",
        "chunk_provider": "siliconflow",
        "peel_provider": "siliconflow",
        "skill_provider": "siliconflow",
        "chunk_model": "m1",
        "peel_model": "m2",
        "skill_model": "m3",
        "request_interval": "1",
    }
    body.update(body_extra)

    res = fe_server.start_pipeline(body)
    # Clear the running registry that start_pipeline created
    fe_server._running.clear()
    return res, captured["cmd"]


def test_start_pipeline_accepts_from_stage(tmp_path, monkeypatch):
    res, cmd = _run_start_pipeline(tmp_path, monkeypatch, {"from_stage": "llm_chunking"})
    assert res["status"] == "running"
    assert "--from-stage" in cmd
    assert cmd[cmd.index("--from-stage") + 1] == "llm_chunking"


def test_start_pipeline_restart(tmp_path, monkeypatch):
    res, cmd = _run_start_pipeline(tmp_path, monkeypatch, {"restart": True})
    assert res["status"] == "running"
    assert "--restart" in cmd


def test_start_pipeline_continue_no_flags(tmp_path, monkeypatch):
    res, cmd = _run_start_pipeline(tmp_path, monkeypatch, {})
    assert res["status"] == "running"
    assert "--restart" not in cmd
    assert "--from-stage" not in cmd


def test_start_pipeline_rejects_invalid_stage(tmp_path, monkeypatch):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        _run_start_pipeline(tmp_path, monkeypatch, {"from_stage": "bogus"})
    assert ei.value.status_code == 400
