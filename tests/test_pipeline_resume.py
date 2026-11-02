"""Regression tests for pipeline checkpoint/resume invariants in main.py.

A fallback Stage 2 run never checkpoints, but Stage 3 may still checkpoint a
tree built from those fallback chunks. That tree must not outlive Stage 2, and
a Stage 3 rebuild must drop Stage 4's id-keyed summaries.
"""
from pathlib import Path

from main import run_pipeline
from utils.checkpoint import CheckpointManager


class FakeChunker:
    def split(self, md_file):
        return {"fallback": False}

    def extract_chunks(self, split_data):
        return [{"start_line": 1, "end_line": 2, "content": "x", "is_atomic": False}]


class FakeProcessor:
    def process(self, pdf_file, out_dir):
        md_file = Path(out_dir) / "full.md"
        md_file.write_text("# Book\n", encoding="utf-8")
        return md_file


class FakeRoot:
    def to_dict(self):
        return {"id": "master", "title": "Document", "parent_path": [], "content": "", "children": []}


class FakeMerger:
    instances = []

    def __init__(self, cache_dir=None):
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None
        self.peel_errors = 0
        self.cache_hits = 0
        self.cache_writes = 0
        self.build_calls = 0
        FakeMerger.instances.append(self)

    def save_original_chunks(self, out_dir, chunks):
        pass

    def build_and_merge(self, chunks):
        self.build_calls += 1
        return FakeRoot()

    def save_results(self, out_dir, root):
        pass


class FakeSkillEngine:
    def __init__(self, out_dir):
        self.out_dir = Path(out_dir)

    def generate(self, root, book_title):
        pass


def seed_checkpoint(out_dir, completed, data):
    cp = CheckpointManager(out_dir)
    cp.state["completed_stages"] = completed
    cp.state["data"] = data
    cp.save()


def prepare(monkeypatch, tmp_path):
    FakeMerger.instances = []
    monkeypatch.setattr("main.PDFProcessor", FakeProcessor)
    monkeypatch.setattr("main.LLMChunker", FakeChunker)
    monkeypatch.setattr("main.TreeMerger", FakeMerger)
    monkeypatch.setattr("main.SkillEngine", FakeSkillEngine)
    out_dir = tmp_path / "outputs"
    out_dir.mkdir()
    (out_dir / "full.md").write_text("# Book\n", encoding="utf-8")
    return out_dir


def test_stage2_fallback_tree_checkpoint_is_reset_on_next_run(monkeypatch, tmp_path):
    out_dir = prepare(monkeypatch, tmp_path)
    seed_checkpoint(
        out_dir,
        ["pdf_conversion", "tree_merging"],
        {
            "pdf_conversion": {"md_file": str(out_dir / "full.md")},
            "tree_merging": {"master_root": {}},
        },
    )

    run_pipeline(str(tmp_path / "book.pdf"), str(out_dir))

    assert FakeMerger.instances[0].build_calls == 1
    cp = CheckpointManager(out_dir)
    assert cp.is_stage_completed("llm_chunking")
    assert cp.is_stage_completed("tree_merging")


def test_malformed_checkpoint_shape_is_reset(tmp_path):
    out_dir = tmp_path / "job"
    out_dir.mkdir()
    (out_dir / ".checkpoint.json").write_text(
        '{"version": 1, "completed_stages": null}', encoding="utf-8"
    )

    cp = CheckpointManager(out_dir)
    assert not cp.is_stage_completed("pdf_conversion")

    cp.mark_stage_completed("pdf_conversion")
    assert CheckpointManager(out_dir).is_stage_completed("pdf_conversion")


def test_stage3_rebuild_drops_stale_skill_checkpoint(monkeypatch, tmp_path):
    out_dir = prepare(monkeypatch, tmp_path)
    seed_checkpoint(
        out_dir,
        ["pdf_conversion", "llm_chunking"],
        {
            "pdf_conversion": {"md_file": str(out_dir / "full.md")},
            "llm_chunking": {"base_chunks": []},
        },
    )
    skill_dir = out_dir / "generated_skills"
    skill_dir.mkdir()
    stale = skill_dir / ".checkpoint.json"
    stale.write_text("{}", encoding="utf-8")

    run_pipeline(str(tmp_path / "book.pdf"), str(out_dir))

    assert FakeMerger.instances[0].build_calls == 1
    assert not stale.exists()


def seed_peel_cache(out_dir):
    cache_dir = out_dir / "peeling_cache"
    cache_dir.mkdir(exist_ok=True)
    (cache_dir / "entry.json").write_text("{}", encoding="utf-8")
    return cache_dir


def _seed_pdf_and_chunking(out_dir):
    seed_checkpoint(
        out_dir,
        ["pdf_conversion", "llm_chunking"],
        {
            "pdf_conversion": {"md_file": str(out_dir / "full.md")},
            "llm_chunking": {"base_chunks": []},
        },
    )


def test_merger_receives_peeling_cache_dir(monkeypatch, tmp_path):
    out_dir = prepare(monkeypatch, tmp_path)
    _seed_pdf_and_chunking(out_dir)

    run_pipeline(str(tmp_path / "book.pdf"), str(out_dir))

    assert FakeMerger.instances[0].cache_dir == out_dir / "peeling_cache"


def test_restart_clears_peeling_cache(monkeypatch, tmp_path):
    out_dir = prepare(monkeypatch, tmp_path)
    cache_dir = seed_peel_cache(out_dir)

    run_pipeline(str(tmp_path / "book.pdf"), str(out_dir), restart=True)

    assert not cache_dir.exists()


def test_from_stage_pdf_conversion_clears_peeling_cache(monkeypatch, tmp_path):
    out_dir = prepare(monkeypatch, tmp_path)
    _seed_pdf_and_chunking(out_dir)
    cache_dir = seed_peel_cache(out_dir)

    run_pipeline(
        str(tmp_path / "book.pdf"), str(out_dir), from_stage="pdf_conversion"
    )

    assert not cache_dir.exists()


def test_from_stage_llm_chunking_keeps_peeling_cache(monkeypatch, tmp_path):
    out_dir = prepare(monkeypatch, tmp_path)
    _seed_pdf_and_chunking(out_dir)
    cache_dir = seed_peel_cache(out_dir)

    run_pipeline(str(tmp_path / "book.pdf"), str(out_dir), from_stage="llm_chunking")

    assert (cache_dir / "entry.json").exists()


def test_from_stage_tree_merging_keeps_peeling_cache(monkeypatch, tmp_path):
    out_dir = prepare(monkeypatch, tmp_path)
    _seed_pdf_and_chunking(out_dir)
    cache_dir = seed_peel_cache(out_dir)

    run_pipeline(str(tmp_path / "book.pdf"), str(out_dir), from_stage="tree_merging")

    assert (cache_dir / "entry.json").exists()
