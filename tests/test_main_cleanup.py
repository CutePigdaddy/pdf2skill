"""Tests for main.py restart / --from-stage artifact cleanup.

Locks in: --restart and --from-stage must cascade-clear the Stage 4
sub-checkpoint (stale id-keyed summaries) and, for pdf_conversion, the
Stage 1 outputs that gate its existence check.
"""
from pathlib import Path

from main import _clear_skill_checkpoint, _clear_stage1_artifacts


def test_clear_skill_checkpoint_removes_stale_file(tmp_path):
    skill_dir = tmp_path / "generated_skills"
    skill_dir.mkdir(parents=True)
    ckpt = skill_dir / ".checkpoint.json"
    ckpt.write_text("{}", encoding="utf-8")

    _clear_skill_checkpoint(tmp_path, "test")

    assert not ckpt.exists()


def test_clear_skill_checkpoint_noop_when_absent(tmp_path):
    _clear_skill_checkpoint(tmp_path, "test")  # must not raise


def test_clear_skill_checkpoint_keeps_skill_dir(tmp_path):
    skill_dir = tmp_path / "generated_skills"
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("x", encoding="utf-8")

    _clear_skill_checkpoint(tmp_path, "test")

    assert (skill_dir / "SKILL.md").exists()


def test_clear_stage1_artifacts_removes_outputs(tmp_path):
    (tmp_path / "full.md").write_text("md", encoding="utf-8")
    splits = tmp_path / "splits"
    splits.mkdir()
    (splits / "a.pdf").write_bytes(b"%PDF")
    (tmp_path / "book_mineru_output.zip").write_bytes(b"PK")
    (tmp_path / "remote_mineru_output.zip").write_bytes(b"PK")
    unrelated = tmp_path / "keep.txt"
    unrelated.write_text("keep", encoding="utf-8")

    _clear_stage1_artifacts(tmp_path, "test")

    assert not (tmp_path / "full.md").exists()
    assert not splits.exists()
    assert not (tmp_path / "book_mineru_output.zip").exists()
    assert not (tmp_path / "remote_mineru_output.zip").exists()
    assert unrelated.exists()


def test_clear_stage1_artifacts_noop_when_absent(tmp_path):
    _clear_stage1_artifacts(tmp_path, "test")  # must not raise
    assert tmp_path.exists()
