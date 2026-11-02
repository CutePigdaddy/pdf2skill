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

MAX_BUNDLE_BYTES = 512 * 1024 * 1024

router = APIRouter()


def _error(status: int, code: str, message: str, advice: str = "") -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"ok": False, "error": {"code": code, "message": message, "advice": advice}},
    )


def _busy_error() -> JSONResponse:
    return _error(409, "JOB_BUSY", "任务正在被写入或文件已变化，请稍后重试")


def _find_job(job_id: str) -> Path | None:
    """Resolve an exact, single-segment job directory under OUTPUTS_ROOT.

    The directory must already exist and must not resolve outside the outputs
    root. ``Path.is_symlink()`` alone misses Windows junctions, so containment
    is checked on the resolved path.
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


def _skill_complete(job: Path) -> bool:
    skill_dir = job / "generated_skills"
    if not (skill_dir / "SKILL.md").is_file():
        return False
    checkpoint_path = skill_dir / ".checkpoint.json"
    try:
        data = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return (
        isinstance(data, dict)
        and isinstance(data.get("completed_stages"), list)
        and "skill_name" in data["completed_stages"]
    )


def _stages(job: Path, checkpoint: dict) -> list[str]:
    stages = [s for s in STAGE_ORDER if s in checkpoint["completed_stages"]]
    if _skill_complete(job) and "skill_generation" not in stages:
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


def _inside(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root)
    except OSError:
        return False


def _iter_bundle_files(job: Path) -> Iterator[tuple[Path, Path]]:
    root = job.resolve()
    for raw_root, dirs, files in os.walk(root, followlinks=False):
        base = Path(raw_root)
        keep = []
        for name in sorted(dirs):
            path = base / name
            rel = path.relative_to(root)
            if _allowed_dir(rel) and _inside(path, root):
                keep.append(name)
        dirs[:] = keep

        for name in sorted(files):
            path = base / name
            rel = path.relative_to(root)
            if _allowed_rel(rel) and _inside(path, root) and path.is_file():
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
            job = _find_job(entry.name)
            if job is None:
                continue
            checkpoint = _read_checkpoint(job)
            if checkpoint is None:
                continue
            md_name = _markdown_name(job, checkpoint)
            if md_name is None:
                continue
            try:
                updated_at = int((job / ".checkpoint.json").stat().st_mtime * 1000)
            except OSError:
                updated_at = 0
            jobs.append(
                {
                    "jobId": job.name,
                    "inputName": job.name,
                    "completedStages": _stages(job, checkpoint),
                    "resumable": True,
                    "updatedAt": updated_at,
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

    checkpoint_path = job / ".checkpoint.json"
    try:
        checkpoint_stat = checkpoint_path.stat()
        files = list(_iter_bundle_files(job))
        total = sum(path.stat().st_size for path, _ in files)
    except OSError:
        return _busy_error()

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
        after = checkpoint_path.stat()
    except OSError:
        _cleanup(tmp_name)
        return _busy_error()
    except Exception:
        _cleanup(tmp_name)
        raise

    if (after.st_mtime_ns, after.st_size) != (checkpoint_stat.st_mtime_ns, checkpoint_stat.st_size):
        _cleanup(tmp_name)
        return _busy_error()

    return FileResponse(
        tmp_name,
        media_type="application/zip",
        filename=f"{job.name}.zip",
        headers={"X-Pdf2Skill-Original-Size": str(total)},
        background=BackgroundTask(_cleanup, tmp_name),
    )
