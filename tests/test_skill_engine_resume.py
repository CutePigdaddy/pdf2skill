"""Regression tests for core/skill_engine.py checkpoint/resume semantics.

Locks in the fix for: a summary persisted before its reference file was
written must not permanently skip the file on resume (SKILL.md dead link),
and empty-content chunks must still get a reference file.
"""
import pytest

from core.skill_engine import SkillEngine
from core.tree_merger import ChunkNode
from utils.checkpoint import CheckpointManager


class FakeLLM:
    def __init__(self):
        self.calls = []

    def chat(self, prompt):
        self.calls.append(prompt)
        return "tag1 | tag2"


def make_root(*nodes):
    root = ChunkNode(id="master", title="Document", parent_path=[], content="")
    root.children = list(nodes)
    return root


def leaf(nid, content):
    return ChunkNode(id=nid, title=nid, parent_path=[], content=content, start_line=1, end_line=2)


@pytest.fixture
def engine(tmp_path, monkeypatch):
    fake = FakeLLM()
    monkeypatch.setattr("core.skill_engine.LLMClient", lambda stage: fake)
    out = tmp_path / "generated_skills"
    return SkillEngine(out), fake, out


def seed_checkpoint(out, completed, data):
    cp = CheckpointManager(out)
    cp.state["completed_stages"] = completed
    cp.state["data"] = data
    cp.save()


def test_resume_rewrites_missing_reference_file(engine):
    """Summary checkpointed but reference file lost (crash between the two
    writes) — resume must rewrite the file, not skip the chunk forever."""
    eng, fake, out = engine
    seed_checkpoint(
        out,
        completed=["book_overview"],
        data={
            "book_overview": {"overview": "ov", "metadata_desc": "md"},
            "summaries": {"chunk_0001": "old tags"},
        },
    )
    root = make_root(leaf("chunk_0001", "some content here"))

    eng.generate(root, "Test Book")

    # No tagging call for the already-summarized chunk; only the name call
    tagging_calls = [c for c in fake.calls if "technical indexer" in c]
    assert tagging_calls == []
    ref = out / "references" / "chunk_0001.md"
    assert ref.exists()
    assert "some content here" in ref.read_text(encoding="utf-8")


def test_resume_with_file_present_skips_tagging(engine):
    eng, fake, out = engine
    seed_checkpoint(
        out,
        completed=["book_overview"],
        data={
            "book_overview": {"overview": "ov", "metadata_desc": ""},
            "summaries": {"chunk_0001": "old tags"},
        },
    )
    ref_dir = out / "references"
    ref_dir.mkdir(parents=True)
    (ref_dir / "chunk_0001.md").write_text("---\nold\n---\ncontent", encoding="utf-8")

    root = make_root(leaf("chunk_0001", "some content here"))
    eng.generate(root, "Test Book")

    assert [c for c in fake.calls if "content_preview" in c] == []
    # existing file untouched
    assert "old" in (ref_dir / "chunk_0001.md").read_text(encoding="utf-8")


def test_empty_content_chunk_still_gets_reference_file(engine):
    """Blank chunks get 'No explicit content' — the SKILL.md still links to
    references/<id>.md, so the file must exist regardless."""
    eng, fake, out = engine
    root = make_root(leaf("chunk_0001", "   \n  "))

    eng.generate(root, "Test Book")

    ref = out / "references" / "chunk_0001.md"
    assert ref.exists()
    assert "id: chunk_0001" in ref.read_text(encoding="utf-8")

    skill_md = (out / "SKILL.md").read_text(encoding="utf-8")
    assert "(references/chunk_0001.md)" in skill_md


def test_fresh_run_tags_and_writes_all_references(engine):
    eng, fake, out = engine
    root = make_root(leaf("chunk_0001", "alpha"), leaf("chunk_0002", "beta"))

    eng.generate(root, "Test Book")

    tagging_calls = [c for c in fake.calls if "technical indexer" in c]
    assert len(tagging_calls) == 2
    for nid in ("chunk_0001", "chunk_0002"):
        assert (out / "references" / f"{nid}.md").exists()

    cp = CheckpointManager(out)
    assert "chunk_0001" in cp.state["data"]["summaries"]
    assert "chunk_0002" in cp.state["data"]["summaries"]
