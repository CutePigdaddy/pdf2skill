"""Tests for CheckpointManager."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.checkpoint import CheckpointManager


def test_save_and_load(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.checkpoint.CHECKPOINT_VERSION", 1)
    mgr = CheckpointManager(tmp_path)
    mgr.mark_stage_completed("stage_a", {"foo": "bar"})
    mgr2 = CheckpointManager(tmp_path)
    assert mgr2.is_stage_completed("stage_a")
    assert mgr2.get_stage_data("stage_a") == {"foo": "bar"}


def test_corrupt_file_recovery(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.checkpoint.CHECKPOINT_VERSION", 1)
    cp_file = tmp_path / ".checkpoint.json"
    cp_file.write_text("{bad json", encoding="utf-8")
    mgr = CheckpointManager(tmp_path)
    assert mgr.state == {"version": 1, "completed_stages": [], "data": {}}


def test_version_mismatch_resets(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.checkpoint.CHECKPOINT_VERSION", 99)
    cp_file = tmp_path / ".checkpoint.json"
    cp_file.write_text(json.dumps({
        "version": 1,
        "completed_stages": ["old_stage"],
        "data": {"old_stage": {"x": 1}}
    }), encoding="utf-8")
    mgr = CheckpointManager(tmp_path)
    assert not mgr.is_stage_completed("old_stage")
