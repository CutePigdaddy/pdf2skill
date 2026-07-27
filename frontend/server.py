"""
Minimal FastAPI backend for pdf2skill.
Provides: config CRUD, input file listing, pipeline execution, output browsing.
"""

import json
import os
import re
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

# Load .env at startup so API keys are available for status checks
load_dotenv(Path(__file__).parent.parent / ".env")

PROJECT_ROOT = Path(__file__).parent.parent
SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.yaml"
INPUTS_DIR = PROJECT_ROOT / "inputs"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
LOGS_DIR = PROJECT_ROOT / "logs"
FRONTEND_DIR = Path(__file__).parent

app = FastAPI(title="pdf2skill")

# ── Pipeline runner state ──────────────────────────────────────────

_running = {}  # job_id -> {status, log_path, process}


def _load_settings() -> dict:
    """Read and return the current settings.yaml as a dict."""
    with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _save_settings(data: dict):
    """Write a settings dict to settings.yaml.

    Args:
        data: Full settings dict to persist.
    """
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


# ── API Routes ─────────────────────────────────────────────────────

@app.get("/api/config")
def get_config():
    """Return current settings.yaml content (API keys masked)."""
    data = _load_settings()
    providers = data.get("llm", {}).get("providers", {})
    masked = {}
    for name, cfg in providers.items():
        entry = dict(cfg)
        env_var = entry.get("api_key_env", "")
        val = os.getenv(env_var, "")
        entry["api_key_set"] = bool(val)
        entry["api_key_preview"] = val[:4] + "****" if len(val) > 4 else ("****" if val else "")
        masked[name] = entry
    # MinerU API key status (separate from LLM providers)
    mineru_key_val = os.getenv("MINERU_API_KEY", "")
    mineru_key_status = {
        "api_key_set": bool(mineru_key_val),
        "api_key_preview": mineru_key_val[:4] + "****" if len(mineru_key_val) > 4 else ("****" if mineru_key_val else ""),
    }

    return {"settings": data, "providers_masked": masked, "mineru_key_status": mineru_key_status}


@app.post("/api/config")
def update_config(body: dict):
    """Update settings.yaml with provided values."""
    current = _load_settings()
    if "providers" in body:
        for name, cfg in body["providers"].items():
            if name not in current.setdefault("llm", {}).setdefault("providers", {}):
                current["llm"]["providers"][name] = {}
            for k, v in cfg.items():
                if k not in ("api_key_set", "api_key_preview"):
                    current["llm"]["providers"][name][k] = v
    if "routers" in body:
        current["llm"]["routers"] = body["routers"]
    if "pdf" in body:
        current.setdefault("pdf", {}).update(body["pdf"])
    if "mineru" in body:
        current.setdefault("mineru", {}).update(body["mineru"])
    _save_settings(current)
    return {"ok": True}


@app.post("/api/apikey")
def set_apikey(body: dict):
    """Set an API key as an environment variable (in-memory only)."""
    env_var = body.get("env_var", "")
    value = body.get("value", "")
    if not env_var or not value:
        raise HTTPException(400, "env_var and value required")
    os.environ[env_var] = value
    return {"ok": True}


@app.get("/api/inputs")
def list_inputs():
    """List files in the inputs/ directory.

    Returns:
        Dict with ``files`` list of ``{name, relative, suffix, size}``.
    """
    if not INPUTS_DIR.exists():
        return {"files": []}
    files = []
    for f in sorted(INPUTS_DIR.rglob("*")):
        if f.is_file():
            files.append({
                "name": f.name,
                "relative": str(f.relative_to(INPUTS_DIR)),
                "suffix": f.suffix.lower(),
                "size": f.stat().st_size,
            })
    return {"files": files}


@app.post("/api/run")
def start_pipeline(body: dict):
    """Start the pipeline as a subprocess and track its status.

    Args:
        body: JSON with ``input_file``, ``mode``, ``output_dir``,
            provider/model overrides, and ``request_interval``.

    Returns:
        Dict with ``job_id`` and ``status``.

    Raises:
        HTTPException: 409 if already running, 400/404 on bad input.
    """
    for job_id, info in _running.items():
        if info["status"] == "running":
            raise HTTPException(409, f"Pipeline already running (job {job_id})")

    input_file = body.get("input_file", "")
    mode = body.get("mode", "pdf")
    output_dir = body.get("output_dir", "")
    from_stage = body.get("from_stage", "")
    restart = body.get("restart", False)

    # Validate from_stage against allowed stage names
    VALID_STAGES = ["pdf_conversion", "llm_chunking", "tree_merging"]
    if from_stage and from_stage not in VALID_STAGES:
        raise HTTPException(400, f"Invalid from_stage. Valid: {VALID_STAGES}")
    chunk_provider = body.get("chunk_provider", "")
    peel_provider = body.get("peel_provider", "")
    skill_provider = body.get("skill_provider", "")
    chunk_model = body.get("chunk_model", "")
    peel_model = body.get("peel_model", "")
    skill_model = body.get("skill_model", "")
    request_interval = body.get("request_interval", "1.0")

    if not input_file:
        raise HTTPException(400, "input_file required")

    resolved = INPUTS_DIR / Path(input_file).name
    if not resolved.exists():
        matches = list(INPUTS_DIR.rglob(Path(input_file).name))
        if matches:
            resolved = matches[0]
        else:
            raise HTTPException(404, f"File not found in inputs/: {input_file}")

    if not output_dir:
        output_dir = str(OUTPUTS_DIR / resolved.stem)

    env = os.environ.copy()
    env["CHUNKING_PROVIDER"] = chunk_provider
    env["PEELING_PROVIDER"] = peel_provider
    env["SKILL_ENGINE_PROVIDER"] = skill_provider
    env["CHUNKING_MODEL"] = chunk_model
    env["PEELING_MODEL"] = peel_model
    env["SKILL_ENGINE_MODEL"] = skill_model
    env["REQUEST_INTERVAL"] = str(request_interval)

    job_id = uuid.uuid4().hex[:8]
    log_path = LOGS_DIR / f"frontend_{job_id}.log"
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable, "-u", str(PROJECT_ROOT / "frontend" / "_run_pipeline.py"),
        "--input", str(resolved),
        "--output", output_dir,
        "--mode", mode,
    ]

    if restart:
        cmd.append("--restart")
    elif from_stage:
        cmd.extend(["--from-stage", from_stage])

    log_f = open(log_path, "w", encoding="utf-8", buffering=1)  # line-buffered so progress markers flush immediately
    proc = subprocess.Popen(
        cmd, env=env, stdout=log_f, stderr=subprocess.STDOUT,
        cwd=str(PROJECT_ROOT),
    )

    _running[job_id] = {
        "status": "running",
        "process": proc,
        "log_path": log_path,
        "log_fd": log_f,
        "input_file": str(resolved),
        "output_dir": output_dir,
    }

    def _watch(pid, jid):
        pid.wait()
        if jid in _running:
            _running[jid]["status"] = "done" if pid.returncode == 0 else "failed"
            _running[jid]["returncode"] = pid.returncode
            try:
                _running[jid]["log_fd"].close()
            except Exception:
                pass

    t = threading.Thread(target=_watch, args=(proc, job_id), daemon=True)
    t.start()

    return {"job_id": job_id, "status": "running"}


@app.get("/api/status")
def get_status(job_id: Optional[str] = None):
    """Get pipeline status + logs + structured MinerU progress/error."""
    if not _running:
        return {"running": False, "jobs": []}

    if job_id and job_id in _running:
        info = _running[job_id]
        log_content = ""

        # Flush the log file descriptor so the latest output is visible on disk
        try:
            info["log_fd"].flush()
        except Exception:
            pass

        if info["log_path"].exists():
            log_content = info["log_path"].read_text(encoding="utf-8", errors="replace")

        # Parse latest MinerU progress marker from log
        mineru_progress = _parse_mineru_progress(log_content)

        # Parse MinerU structured error from log
        mineru_error = _parse_mineru_error(log_content)

        return {
            "job_id": job_id,
            "status": info["status"],
            "returncode": info.get("returncode"),
            "input_file": info["input_file"],
            "output_dir": info["output_dir"],
            "log": log_content,
            "mineru_progress": mineru_progress,
            "mineru_error": mineru_error,
        }

    jobs = [{"job_id": jid, "status": info["status"]} for jid, info in _running.items()]
    return {"running": any(j["status"] == "running" for j in jobs), "jobs": jobs}


def _parse_mineru_progress(log_text: str):
    """Extract the latest __MINERU_PROGRESS__{...} marker from log text."""
    matches = re.findall(r'__MINERU_PROGRESS__(\{.*?\})', log_text)
    if not matches:
        return None
    try:
        return json.loads(matches[-1])
    except json.JSONDecodeError:
        return None


def _parse_mineru_error(log_text: str):
    """Extract MinerU error info from log text (err_code + Chinese message)."""
    # Match MinerUAPIError output: "MinerUAPIError: <zh_msg> — <advice>"
    err_match = re.search(
        r'MinerU(?:Auth|File|Size|Quota|Service)?Error:\s*(.+?)(?:\n|$)',
        log_text,
    )
    if not err_match:
        return None
    message = err_match.group(1).strip()
    # Try to extract err_code from earlier in the same log section
    code_match = re.search(r'err_code[=:]\s*["\']?(-?\w+)', log_text[err_match.start()-500:err_match.start()] if err_match.start() >= 500 else log_text[:err_match.start()])
    err_code = code_match.group(1) if code_match else ""

    # Split "中文消息 — 建议" format
    parts = message.split(" — ", 1)
    zh_msg = parts[0].strip()
    advice = parts[1].strip() if len(parts) > 1 else ""

    return {"err_code": err_code, "message": zh_msg, "advice": advice}


@app.get("/api/progress")
def get_progress(job_id: str = ""):
    """Get MinerU progress for a specific job (convenience endpoint)."""
    if not job_id or job_id not in _running:
        return {"available": False}
    info = _running[job_id]

    # Flush so latest output is on disk
    try:
        info["log_fd"].flush()
    except Exception:
        pass

    if not info["log_path"].exists():
        return {"available": False}
    log_content = info["log_path"].read_text(encoding="utf-8", errors="replace")
    progress = _parse_mineru_progress(log_content)
    if progress:
        return {"available": True, **progress}
    return {"available": False}


@app.post("/api/stop")
def stop_pipeline(body: dict):
    """Terminate a running pipeline job.

    Args:
        body: JSON with ``job_id``.

    Returns:
        Dict with ``ok`` and optional ``msg``.

    Raises:
        HTTPException: 404 if job not found.
    """
    job_id = body.get("job_id", "")
    if job_id not in _running:
        raise HTTPException(404, "Job not found")
    info = _running[job_id]
    if info["status"] != "running":
        return {"ok": True, "msg": "Already stopped"}
    info["process"].terminate()
    info["status"] = "stopped"
    try:
        info["log_fd"].close()
    except Exception:
        pass
    return {"ok": True}


@app.get("/api/outputs")
def list_outputs():
    """Return the outputs/ directory as a tree structure.

    Returns:
        Dict with ``tree`` containing nested ``{name, type, children, size}``.
    """
    if not OUTPUTS_DIR.exists():
        return {"tree": {}}

    def build_tree(path: Path) -> dict:
        result = {"name": path.name, "type": "dir" if path.is_dir() else "file"}
        if path.is_dir():
            children = []
            for child in sorted(path.iterdir()):
                if child.is_dir() and child.name == "images":
                    count = sum(1 for _ in child.iterdir())
                    children.append({"name": "images/", "type": "dir", "children": [], "count": count})
                elif child.is_dir():
                    children.append(build_tree(child))
                else:
                    children.append({"name": child.name, "type": "file", "size": child.stat().st_size})
            result["children"] = children
        else:
            result["size"] = path.stat().st_size
        return result

    return {"tree": build_tree(OUTPUTS_DIR)}


@app.get("/api/outputs/file")
def read_output_file(path: str = ""):
    """Read a specific file under outputs/ with path-traversal protection.

    Args:
        path: Relative path under ``outputs/``.

    Returns:
        Text content, image file response, or binary metadata.

    Raises:
        HTTPException: 400 if no path, 403 if path escapes outputs/,
            404 if not found.
    """
    if not path:
        raise HTTPException(400, "path query param required")
    target = (OUTPUTS_DIR / path).resolve()
    if not str(target).startswith(str(OUTPUTS_DIR.resolve())):
        raise HTTPException(403, "Access denied")
    if not target.exists():
        raise HTTPException(404, "File not found")

    suffix = target.suffix.lower()
    if suffix in (".md", ".txt", ".json", ".yaml", ".yml", ".py", ".log"):
        content = target.read_text(encoding="utf-8", errors="replace")
        return {"path": path, "type": "text", "content": content}
    elif suffix in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
        return FileResponse(str(target), media_type=f"image/{suffix.strip('.')}")
    else:
        return {"path": path, "type": "binary", "size": target.stat().st_size}


# ── Serve frontend SPA ─────────────────────────────────────────────

app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR / "static")), name="static")


@app.get("/")
def index():
    """Serve the frontend SPA index page."""
    html = (FRONTEND_DIR / "static" / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8501)
