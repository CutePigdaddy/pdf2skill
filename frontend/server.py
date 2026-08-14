"""
Minimal FastAPI backend for pdf2skill.
Provides: config CRUD, input file listing, pipeline execution, output browsing.
"""

import json
import os
import re
import requests
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, UploadFile, File
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

# Validation patterns for provider config (shared by config + apikey endpoints)
PROVIDER_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
ENV_VAR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

app = FastAPI(title="pdf2skill")

# ── Pipeline runner state ──────────────────────────────────────────

_running = {}  # job_id -> {status, log_path, process}


def _config_error(code: str, message: str, advice: str) -> HTTPException:
    """Build a 422 with the structured {code, message, advice} error body."""
    return HTTPException(422, detail={"ok": False, "error": {"code": code, "message": message, "advice": advice}})


def _load_settings() -> dict:
    """Read and return the current settings.yaml as a dict.

    Raises:
        HTTPException: 500 with structured error if the file is missing
            or the YAML is corrupt.
    """
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except OSError as e:
        raise HTTPException(500, detail={"ok": False, "error": {
            "code": "SETTINGS_READ_FAILED",
            "message": f"无法读取 config/settings.yaml: {e}",
            "advice": "请检查文件是否存在且可读",
        }})
    except yaml.YAMLError as e:
        raise HTTPException(500, detail={"ok": False, "error": {
            "code": "SETTINGS_CORRUPT",
            "message": f"config/settings.yaml 格式错误: {e}",
            "advice": "请检查 YAML 语法（缩进、冒号）或从备份恢复",
        }})


def _save_settings(data: dict):
    """Write a settings dict to settings.yaml.

    Args:
        data: Full settings dict to persist.

    Raises:
        HTTPException: 500 with structured error if the write fails.
    """
    try:
        tmp_path = SETTINGS_PATH.with_suffix(".yaml.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        os.replace(tmp_path, SETTINGS_PATH)  # atomic: never leave a half-written file
    except OSError as e:
        raise HTTPException(500, detail={"ok": False, "error": {
            "code": "SETTINGS_WRITE_FAILED",
            "message": f"无法写入 config/settings.yaml: {e}",
            "advice": "请检查文件写入权限或磁盘空间",
        }})


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
    """Update settings.yaml with provided values.

    Supports adding new providers (``llm.providers.<name>`` created if
    missing), updating existing ones, and deleting via ``delete_providers``.
    All changes are validated BEFORE anything is written — on error the
    file is left untouched.

    Args:
        body: JSON with any of ``providers``, ``routers``,
            ``delete_providers``, ``pdf``, ``mineru``.

    Raises:
        HTTPException: 422 with ``{code, message, advice}`` on validation
            failure, 500 with structured error on read/write failure.
    """
    current = _load_settings()
    providers = current.setdefault("llm", {}).setdefault("providers", {})

    # 1. Add / update providers (validated field by field)
    if "providers" in body:
        for name, cfg in body["providers"].items():
            if not PROVIDER_NAME_RE.match(name):
                raise _config_error(
                    "INVALID_PROVIDER_NAME",
                    f"Provider 名称 '{name}' 不合法",
                    "名称只能包含小写字母、数字、下划线和连字符，且以字母或数字开头（如 my-provider）",
                )
            if name not in providers:
                providers[name] = {}
            for k, v in cfg.items():
                if k in ("api_key_set", "api_key_preview"):
                    continue  # mask fields from GET /api/config must not be written back
                if k == "base_url":
                    if not isinstance(v, str):
                        raise _config_error("INVALID_BASE_URL", f"Provider '{name}' 的 base_url 必须是字符串",
                                            "示例: https://api.example.com/v1")
                    v = v.strip()
                    if not v:
                        raise _config_error("MISSING_BASE_URL", f"Provider '{name}' 的 base_url 不能为空",
                                            "请在配置面板填写 base_url")
                    if not v.startswith(("http://", "https://")):
                        raise _config_error("INVALID_BASE_URL",
                                            f"Provider '{name}' 的 base_url 必须以 http:// 或 https:// 开头",
                                            "示例: https://api.example.com/v1")
                elif k == "api_key_env":
                    if not isinstance(v, str):
                        raise _config_error("INVALID_ENV_VAR", f"Provider '{name}' 的 api_key_env 必须是字符串",
                                            "环境变量名只能包含字母、数字、下划线，且以字母或下划线开头")
                    v = v.strip()
                    if not v:
                        raise _config_error("MISSING_ENV_VAR", f"Provider '{name}' 的 api_key_env 不能为空",
                                            "请在配置面板填写 API Key 环境变量名")
                    if not ENV_VAR_RE.match(v):
                        raise _config_error("INVALID_ENV_VAR",
                                            f"Provider '{name}' 的 api_key_env '{v}' 不合法",
                                            "环境变量名只能包含字母、数字、下划线，且以字母或下划线开头")
                providers[name][k] = v

    # 2. Validate router references before applying
    if "routers" in body:
        for stage, name in body["routers"].items():
            if name and name not in providers:
                raise _config_error("ROUTER_UNKNOWN_PROVIDER",
                                    f"阶段 {stage} 引用了不存在的 Provider '{name}'",
                                    "请选择配置中已有的 Provider")

    # 3. Delete providers (blocked while referenced by any stage router)
    if "delete_providers" in body:
        routers = current["llm"].setdefault("routers", {})
        for name in body["delete_providers"]:
            if name not in providers:
                continue  # already absent — nothing to do
            used_by = [stage for stage, p in routers.items() if p == name]
            if used_by:
                raise _config_error("PROVIDER_IN_USE",
                                    f"Provider '{name}' 正被阶段 {', '.join(used_by)} 使用，无法删除",
                                    "请先在阶段卡片中将对应阶段切换到其他 Provider")
            if len(providers) <= 1:
                raise _config_error("LAST_PROVIDER",
                                    f"无法删除最后一个 Provider '{name}'",
                                    "至少保留一个 Provider")
            del providers[name]

    if "routers" in body:
        current["llm"]["routers"] = body["routers"]
    if "pdf" in body:
        current.setdefault("pdf", {}).update(body["pdf"])
    if "mineru" in body:
        current.setdefault("mineru", {}).update(body["mineru"])

    _save_settings(current)
    return {"ok": True}


@app.get("/api/models")
def list_models(provider: str = ""):
    """Fetch available model names for an LLM provider.

    Calls the OpenAI-compatible ``GET {base_url}/models`` endpoint with
    a short timeout and no retries (interactive UI call — RetrySession's
    exponential backoff would stall the button for minutes).

    Args:
        provider: Provider name from settings.yaml ``llm.providers``.

    Returns:
        Dict ``{ok, provider, models, count}`` on success, or
        ``{ok: False, error: {code, message, advice}}`` on failure.
        Errors use HTTP 200 so the frontend can show the structured
        ``code/message/advice`` display (matching the upload error card).
    """
    data = _load_settings()
    providers = data.get("llm", {}).get("providers", {})

    if not provider or provider not in providers:
        return {
            "ok": False,
            "error": {
                "code": "UNKNOWN_PROVIDER",
                "message": f"未知 Provider: {provider}",
                "advice": "请检查配置面板中的 Provider 名称",
            },
        }

    cfg = providers[provider]
    base_url = cfg.get("base_url", "")
    if not base_url:
        return {
            "ok": False,
            "error": {
                "code": "NO_BASE_URL",
                "message": f"Provider '{provider}' 缺少 base_url",
                "advice": "请在 config/settings.yaml 中为该 Provider 配置 base_url",
            },
        }

    # Same key resolution order as utils/llm_client.py
    provider_key_env = f"{provider.upper().replace('-', '_')}_API_KEY"
    api_key = os.getenv(provider_key_env) or os.getenv(cfg.get("api_key_env", ""))
    if not api_key:
        return {
            "ok": False,
            "error": {
                "code": "NO_API_KEY",
                "message": f"Provider '{provider}' 未设置 API Key",
                "advice": "请在「配置」面板 API Keys 区设置，或在 .env 中配置后重启服务",
            },
        }

    url = f"{base_url.rstrip('/')}/models"
    try:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=(5, 12),  # connect 5s / read 12s
        )
    except requests.ConnectionError:
        return {
            "ok": False,
            "error": {
                "code": "CONNECT_FAILED",
                "message": f"无法连接 {base_url}",
                "advice": "请检查网络连接、代理/VPN 设置，或该服务是否正在运行",
            },
        }
    except requests.Timeout:
        return {
            "ok": False,
            "error": {
                "code": "CONNECT_FAILED",
                "message": f"请求 {base_url} 超时",
                "advice": "网络响应过慢，请稍后重试或检查网络",
            },
        }
    except requests.RequestException as e:
        return {
            "ok": False,
            "error": {
                "code": "CONNECT_FAILED",
                "message": f"请求失败: {e}",
                "advice": "请检查网络连接后重试",
            },
        }

    if resp.status_code == 401:
        return {
            "ok": False,
            "error": {
                "code": "AUTH_FAILED",
                "message": f"Provider '{provider}' API Key 认证失败",
                "advice": "请在「配置」面板重新设置 API Key",
            },
        }
    if resp.status_code == 404:
        return {
            "ok": False,
            "error": {
                "code": "ENDPOINT_NOT_FOUND",
                "message": f"Provider '{provider}' 不支持 /models 端点",
                "advice": "该 Provider 无法获取模型列表，请手动输入模型名称",
            },
        }
    if resp.status_code != 200:
        return {
            "ok": False,
            "error": {
                "code": "UPSTREAM_ERROR",
                "message": f"上游返回 HTTP {resp.status_code}",
                "advice": "请稍后重试，或手动输入模型名称",
            },
        }

    try:
        raw_models = resp.json().get("data", [])
        models = sorted({m["id"] for m in raw_models if isinstance(m, dict) and m.get("id")})
    except (ValueError, TypeError, KeyError, AttributeError):
        return {
            "ok": False,
            "error": {
                "code": "PARSE_FAILED",
                "message": "无法解析模型列表响应",
                "advice": "该 Provider 的响应格式与预期不符，请手动输入模型名称",
            },
        }

    models = models[:200]  # keep the payload reasonable
    return {"ok": True, "provider": provider, "models": models, "count": len(models)}


@app.post("/api/apikey")
def set_apikey(body: dict):
    """Set an API key as an environment variable (in-memory only).

    Raises:
        HTTPException: 400 if fields missing, 422 with structured error
            if ``env_var`` is not a valid environment variable name.
    """
    env_var = body.get("env_var", "")
    value = body.get("value", "")
    if not env_var or not value:
        raise HTTPException(400, "env_var and value required")
    if not ENV_VAR_RE.match(env_var):
        raise _config_error(
            "INVALID_ENV_VAR",
            f"环境变量名 '{env_var}' 不合法",
            "环境变量名只能包含字母、数字、下划线，且以字母或下划线开头",
        )
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


@app.post("/api/upload")
async def upload_pdf(file: UploadFile = File(...)):
    """Upload a PDF file to the inputs/ directory after validation.

    Accepts a single PDF via multipart form-data.  The file is validated
    (format, size, page count, corruption, encryption) before saving.

    Returns:
        Dict with ``ok`` and ``file`` info on success.

    Raises:
        HTTPException: 422 on validation failure, 409 on name conflict.
    """
    from pdf_validator import validate_pdf, sanitize_filename

    file_bytes = await file.read()
    filename = file.filename or ""

    # Validate
    settings = _load_settings()
    error = validate_pdf(file_bytes, filename, settings)
    if error:
        raise HTTPException(422, detail={"ok": False, "error": error})

    # Sanitize and save
    safe_name = sanitize_filename(filename)
    target = INPUTS_DIR / safe_name
    if target.exists():
        raise HTTPException(409, detail={
            "ok": False,
            "error": {
                "code": "NAME_CONFLICT",
                "message": f"文件 {safe_name} 已存在",
                "advice": "请重命名文件后重新上传，或删除已有文件",
            },
        })

    INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    target.write_bytes(file_bytes)

    return {
        "ok": True,
        "file": {
            "name": safe_name,
            "size": len(file_bytes),
            "relative": safe_name,
        },
    }


@app.delete("/api/inputs/{filename}")
def delete_input(filename: str):
    """Delete a file from the inputs/ directory.

    Args:
        filename: Name of the file to delete.

    Returns:
        Dict with ``ok`` on success.

    Raises:
        HTTPException: 404 if not found, 403 if path escapes inputs/.
    """
    target = (INPUTS_DIR / filename).resolve()
    if not target.is_relative_to(INPUTS_DIR.resolve()):
        raise HTTPException(403, "Path traversal not allowed")
    if not target.exists() or not target.is_file():
        raise HTTPException(404, f"File not found: {filename}")
    target.unlink()
    return {"ok": True}


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
            # Only update the terminal status if the job wasn't stopped
            # manually — stop_pipeline() already set it to "stopped" and
            # terminate() would otherwise surface as a bogus "failed".
            if _running[jid]["status"] == "running":
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
        mineru_error = _parse_pipeline_error(log_content)

        return {
            "job_id": job_id,
            "status": info["status"],
            "returncode": info.get("returncode"),
            "input_file": info["input_file"],
            "output_dir": info["output_dir"],
            "log": log_content,
            "mineru_progress": mineru_progress,
            "mineru_error": mineru_error,
            "pipeline_error": mineru_error,
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


def _parse_pipeline_error(log_text: str):
    """Extract structured error info from log text (any pipeline stage).

    Matches all ``PDF2SkillsException`` subclasses and extracts the
    Chinese message and advice in the ``"消息 — 建议"`` format.
    """
    err_match = re.search(
        r'(?:MinerU(?:Auth|File|Size|Quota|Service)?Error'
        r'|MinerUAPIError|MinerUConversionError'
        r'|PDF2SkillsException'
        r'|PDFSplitError|LLMChunkingError|SkillGenerationError|MergeError'
        r'|LLMParsingError):\s*(.+?)(?:\n|$)',
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
    if not target.is_relative_to(OUTPUTS_DIR.resolve()):
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
