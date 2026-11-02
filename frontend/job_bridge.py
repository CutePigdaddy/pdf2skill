"""Bridge between server-side ``outputs/`` and the browser OPFS job store.

The browser pipeline cannot read the server filesystem, so resuming a CLI job
in the web UI needs an explicit export/import step:

  - ``GET /api/jobs``                 list resumable jobs under ``outputs/``
  - ``GET /api/jobs/{jobId}/bundle``  stream one job as a zip for OPFS import

Only files needed to continue a job are bundled. ``splits/``, per-part MinerU
directories and raw ``*.zip`` artifacts are excluded because they are large
and only relevant to Stage 1, which is already checkpointed.
"""

from __future__ import annotations

import json
import os
import tempfile
import zipfile
from pathlib import Path
from typing import Iterator

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUTS_ROOT = PROJECT_ROOT / "outputs"

STAGE_ORDER = ("pdf_conversion", "llm_chunking", "tree_merging", "skill_generation")

# Root files / directories the browser needs to resume or inspect a job.
INCLUDE_FILES = {".checkpoint.json", "full.md", "input.md"}
INCLUDE_DIRS = {
    "images",
    "full_chunks_original",
    "full_chunks",
    "generated_skills",
    "peeling_cache",
}

MAX_BUNDLE_BYTES = 1_500 * 1024 * 1024

router = APIRouter()


def _error(status: int, code: str, message: str, advice: str = "") -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"ok": False, "error": {"code": code, "message": message, "advice": advice}},
    )


def _find_job(job_id: str) -> Path | None:
    """Resolve an exact, single-segment job directory under OUTPUTS_ROOT.

    The directory must already exist and must not be a symlink; constructing
    the path from user input is safe only because it has to match an entry in
    the outputs root and cannot contain separators.
    """
    if not job_id or job_id in (".", "..") or "/" in job_id or "\\" in job_id:
        return None
    if job_id.startswith("."):
        return None
    root = OUTPUTS_ROOT.resolve()
    candidate = (root / job_id).resolve()
    if candidate.parent != root or candidate.name != job_id:
        return None
    if candidate.is_symlink() or not candidate.is_dir():
        return None
    return candidate


def _read_checkpoint(job: Path) -> dict | None:
    path = job / ".checkpoint.json"
    if path.is_symlink() or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("version") != 1:
        return None
    if not isinstance(data.get("completed_stages"), list):
        return None
    if not isinstance(data.get("data"), dict):
        return None
    return data


def _markdown_name(job: Path, checkpoint: dict) -> str | None:
    stage1 = checkpoint["data"].get("pdf_conversion")
    md_file = stage1.get("md_file") if isinstance(stage1, dict) else None
    if not isinstance(md_file, str) or not md_file:
        return None
    name = md_file.replace("\\", "/").rsplit("/", 1)[-1]
    if name not in ("full.md", "input.md"):
        return None
    path = job / name
    if path.is_symlink() or not path.is_file():
        return None
    return name


def _stages(job: Path, checkpoint: dict) -> list[str]:
    stages = [s for s in STAGE_ORDER if s in checkpoint["completed_stages"]]
    if (job / "generated_skills" / "SKILL.md").is_file():
        stages.append("skill_generation")
    return stages


def _allowed_rel(rel: Path) -> bool:
    parts = rel.parts
    if not parts:
        return False
    if len(parts) == 1:
        return parts[0] in INCLUDE_FILES
    return parts[0] in INCLUDE_DIRS


def _allowed_dir(rel: Path) -> bool:
    return bool(rel.parts) and rel.parts[0] in INCLUDE_DIRS


def _iter_bundle_files(job: Path) -> Iterator[tuple[Path, Path]]:
    for raw_root, dirs, files in os.walk(job, followlinks=False):
        root = Path(raw_root)
        keep = []
        for name in sorted(dirs):
            path = root / name
            rel = path.relative_to(job)
            if not path.is_symlink() and _allowed_dir(rel):
                keep.append(name)
        dirs[:] = keep

        for name in sorted(files):
            path = root / name
            if path.is_symlink() or not path.is_file():
                continue
            rel = path.relative_to(job)
            if _allowed_rel(rel):
                yield path, rel


def _cleanup(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


@router.get("/api/jobs")
def list_disk_jobs() -> JSONResponse:
    jobs = []
    if OUTPUTS_ROOT.is_dir():
        for entry in sorted(OUTPUTS_ROOT.iterdir(), key=lambda p: p.name):
            if entry.is_symlink() or not entry.is_dir() or entry.name.startswith("."):
                continue
            checkpoint = _read_checkpoint(entry)
            if checkpoint is None:
                continue
            md_name = _markdown_name(entry, checkpoint)
            if md_name is None:
                continue
            checkpoint_path = entry / ".checkpoint.json"
            jobs.append(
                {
                    "jobId": entry.name,
                    "inputName": entry.name,
                    "completedStages": _stages(entry, checkpoint),
                    "resumable": True,
                    "updatedAt": int(checkpoint_path.stat().st_mtime * 1000),
                    "mdFile": md_name,
                }
            )
    jobs.sort(key=lambda j: j["updatedAt"], reverse=True)
    return JSONResponse({"ok": True, "jobs": jobs})


@router.get("/api/jobs/{job_id}/bundle")
def download_job_bundle(job_id: str):
    job = _find_job(job_id)
    if job is None:
        return _error(404, "JOB_NOT_FOUND", f"磁盘任务 '{job_id}' 不存在")

    checkpoint = _read_checkpoint(job)
    if checkpoint is None:
        return _error(409, "JOB_INVALID", "任务 checkpoint 缺失或损坏，无法导入")

    if _markdown_name(job, checkpoint) is None:
        return _error(409, "JOB_NOT_RESUMABLE", "任务缺少 Stage 1 Markdown，无法导入续跑")

    files = list(_iter_bundle_files(job))
    total = sum(path.stat().st_size for path, _ in files)
    if total > MAX_BUNDLE_BYTES:
        return _error(
            413,
            "BUNDLE_TOO_LARGE",
            f"任务打包后约 {total // (1024 * 1024)}MB，超过浏览器导入上限",
            "请用命令行继续该任务",
        )

    fd, tmp_name = tempfile.mkstemp(prefix="pdf2skill-bundle-", suffix=".zip")
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp_name, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for path, rel in files:
                zf.write(path, rel.as_posix())
    except Exception:
        _cleanup(tmp_name)
        raise

    return FileResponse(
        tmp_name,
        media_type="application/zip",
        filename=f"{job.name}.zip",
        headers={"X-Pdf2Skill-Original-Size": str(total)},
        background=BackgroundTask(_cleanup, tmp_name),
    )
