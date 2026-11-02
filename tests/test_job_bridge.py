"""Tests for the server-side outputs/ -> browser job bridge."""
import io
import json
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from frontend import job_bridge


def make_job(
    outputs,
    name,
    *,
    stages=("pdf_conversion", "llm_chunking"),
    md_name="full.md",
    write_md=True,
    checkpoint=True,
    skills=False,
):
    job = outputs / name
    job.mkdir(parents=True)
    if checkpoint:
        data = {
            "version": 1,
            "completed_stages": list(stages),
            "data": {
                "pdf_conversion": {"md_file": rf"outputs\{name}\{md_name}"},
                "llm_chunking": {"base_chunks": [{"content": "a"}, {"content": "b"}]},
            },
        }
        (job / ".checkpoint.json").write_text(json.dumps(data), encoding="utf-8")
    if write_md and md_name:
        (job / md_name).write_text("# Book\n", encoding="utf-8")
    if skills:
        skill_dir = job / "generated_skills"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# Skill\n", encoding="utf-8")
        (skill_dir / ".checkpoint.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "completed_stages": ["book_overview", "skill_name"],
                    "data": {},
                }
            ),
            encoding="utf-8",
        )
    return job


@pytest.fixture
def env(tmp_path, monkeypatch):
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    monkeypatch.setattr(job_bridge, "OUTPUTS_ROOT", outputs)
    app = FastAPI()
    app.include_router(job_bridge.router)
    return outputs, TestClient(app)


class TestListDiskJobs:
    def test_lists_only_resumable_checkpointed_jobs(self, env):
        outputs, client = env
        make_job(outputs, "book-a")
        make_job(outputs, "broken", checkpoint=False)
        make_job(outputs, "missing-md", md_name="gone.md", write_md=False)
        make_job(outputs, "hidden", md_name="full.md")
        (outputs / "hidden").rename(outputs / ".hidden")

        body = client.get("/api/jobs").json()

        assert body["ok"] is True
        assert [j["jobId"] for j in body["jobs"]] == ["book-a"]
        job = body["jobs"][0]
        assert job["completedStages"] == ["pdf_conversion", "llm_chunking"]
        assert job["resumable"] is True
        assert job["mdFile"] == "full.md"

    def test_marks_skill_generation_when_index_exists(self, env):
        outputs, client = env
        make_job(outputs, "finished", stages=("pdf_conversion", "llm_chunking", "tree_merging"), skills=True)

        job = client.get("/api/jobs").json()["jobs"][0]

        assert job["completedStages"] == [
            "pdf_conversion",
            "llm_chunking",
            "tree_merging",
            "skill_generation",
        ]

    def test_stale_skill_md_without_checkpoint_is_not_complete(self, env):
        outputs, client = env
        job = make_job(
            outputs,
            "stale",
            stages=("pdf_conversion", "llm_chunking", "tree_merging"),
        )
        skill_dir = job / "generated_skills"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# Stale skill\n", encoding="utf-8")

        stages = client.get("/api/jobs").json()["jobs"][0]["completedStages"]

        assert stages == ["pdf_conversion", "llm_chunking", "tree_merging"]

    def test_missing_outputs_dir_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(job_bridge, "OUTPUTS_ROOT", tmp_path / "nope")
        app = FastAPI()
        app.include_router(job_bridge.router)

        assert TestClient(app).get("/api/jobs").json() == {"ok": True, "jobs": []}


class TestJobBundle:
    def test_bundle_contains_resume_files_and_excludes_bulk(self, env):
        outputs, client = env
        job = make_job(outputs, "book-a", skills=True)
        (job / "images").mkdir()
        (job / "images" / "a.png").write_bytes(b"\x89PNG")
        (job / "full_chunks").mkdir()
        (job / "full_chunks" / "chunk_0001.md").write_text("chunk", encoding="utf-8")
        (job / "peeling_cache").mkdir()
        (job / "peeling_cache" / "abc.json").write_text("{}", encoding="utf-8")
        (job / "splits").mkdir()
        (job / "splits" / "part01.pdf").write_bytes(b"%PDF")
        (job / "book_part01").mkdir()
        (job / "book_part01" / "remote_mineru_output.zip").write_bytes(b"zip")

        resp = client.get("/api/jobs/book-a/bundle")

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        names = set(zipfile.ZipFile(io.BytesIO(resp.content)).namelist())
        assert {
            ".checkpoint.json",
            "full.md",
            "images/a.png",
            "full_chunks/chunk_0001.md",
            "generated_skills/SKILL.md",
            "peeling_cache/abc.json",
        } <= names
        assert not any(name.startswith("splits/") for name in names)
        assert not any("remote_mineru_output.zip" in name for name in names)

    def test_unknown_job_returns_404(self, env):
        _, client = env
        resp = client.get("/api/jobs/nope/bundle")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "JOB_NOT_FOUND"

    def test_missing_markdown_returns_409(self, env):
        outputs, client = env
        make_job(outputs, "gone", md_name="gone.md", write_md=False)
        resp = client.get("/api/jobs/gone/bundle")
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "JOB_NOT_RESUMABLE"

    @pytest.mark.parametrize("raw", ["..", "../secret", r"..\secret", "a/b", ".hidden"])
    def test_path_traversal_is_rejected(self, env, raw):
        _, client = env
        resp = client.get(f"/api/jobs/{raw}/bundle")
        assert resp.status_code == 404
        assert job_bridge._find_job(raw) is None

    def test_symlinked_job_dir_is_not_listed(self, env):
        outputs, client = env
        outside = outputs.parent / "outside"
        make_job(outputs.parent, "outside", md_name="full.md", checkpoint=True)
        job = outputs / "link"
        try:
            job.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks unavailable on this platform")

        assert client.get("/api/jobs").json()["jobs"] == []
        assert client.get("/api/jobs/link/bundle").status_code == 404


class TestProxyMount:
    def test_proxy_app_exposes_jobs_endpoint(self, tmp_path, monkeypatch):
        monkeypatch.setattr(job_bridge, "OUTPUTS_ROOT", tmp_path / "empty")
        from frontend.proxy_server import app

        client = TestClient(app, base_url="http://127.0.0.1:8502")
        resp = client.get("/api/jobs")

        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "jobs": []}

