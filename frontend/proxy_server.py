"""
Thin proxy backend for the pure-frontend pdf2skill (port 8502).

The browser SDK runs the whole pipeline client-side; this server only holds
secrets and forwards traffic:

  - POST /api/llm/chat            forward chat completions to an OpenAI-compatible
                                  provider (API key resolved server-side from .env)
  - GET  /api/models              list models for a provider
  - GET  /api/keys                masked API-key status (LLM providers + MinerU)
  - POST /api/keys                set an API key in process memory
  - GET  /api/providers           provider registry from settings.yaml
  - POST /api/providers           add/update/delete providers in settings.yaml
  - POST /api/mineru/file-urls/batch       MinerU signed-upload URL (remote)
  - PUT  /api/mineru/upload?url=           PUT PDF bytes to the signed URL
  - GET  /api/mineru/extract-results/batch/{batch_id}   poll MinerU batch results
  - GET  /api/mineru/download?url=         download the result ZIP
  - POST /api/mineru/local        convert via a self-hosted MinerU Gradio endpoint
  - GET  /api/health              liveness probe

All errors use the structured envelope {ok: false, error: {code, message, advice}}
(mostly with HTTP 200 so the frontend can render them uniformly).
"""

import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Optional

import requests
import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

load_dotenv(Path(__file__).parent.parent / ".env")

PROJECT_ROOT = Path(__file__).parent.parent
SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.yaml"
MINERU_BASE_URL = "https://mineru.net/api/v4"

PROVIDER_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
ENV_VAR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

app = FastAPI(title="pdf2skill-proxy")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── settings.yaml helpers (same atomic-write pattern as server.py) ──

def _load_settings() -> dict:
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
    try:
        tmp_path = SETTINGS_PATH.with_suffix(".yaml.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        os.replace(tmp_path, SETTINGS_PATH)
    except OSError as e:
        raise HTTPException(500, detail={"ok": False, "error": {
            "code": "SETTINGS_WRITE_FAILED",
            "message": f"无法写入 config/settings.yaml: {e}",
            "advice": "请检查文件写入权限或磁盘空间",
        }})


def _err_payload(code: str, message: str, advice: str) -> dict:
    return {"ok": False, "error": {"code": code, "message": message, "advice": advice}}


# ── provider/key resolution (mirrors utils/llm_client.py) ───────────

def _resolve_provider(provider: str):
    """Return (base_url, api_key, provider_cfg) or raise an error payload."""
    data = _load_settings()
    providers = data.get("llm", {}).get("providers", {})
    if provider not in providers:
        raise HTTPException(200, _err_payload(
            "UNKNOWN_PROVIDER",
            f"未知 Provider: {provider}",
            "请在服务端 settings.yaml 中注册该 Provider，或通过 /api/providers 添加",
        ))
    cfg = providers[provider]
    base_url = (cfg.get("base_url") or "").rstrip("/")
    if not base_url:
        raise HTTPException(200, _err_payload(
            "NO_BASE_URL",
            f"Provider '{provider}' 缺少 base_url",
            f"请通过 /api/providers 为 '{provider}' 配置 base_url",
        ))
    provider_key_env = f"{provider.upper().replace('-', '_')}_API_KEY"
    api_key = os.getenv(provider_key_env) or os.getenv(cfg.get("api_key_env", ""))
    if not api_key:
        raise HTTPException(200, _err_payload(
            "NO_API_KEY",
            f"Provider '{provider}' 未设置 API Key",
            "请在「配置」面板 API Keys 区设置，或在 .env 中配置后重启服务",
        ))
    return base_url, api_key, cfg


def _map_llm_http_error(provider: str, model: str, status: int, body_text: str) -> dict:
    """Map an upstream HTTP status to the structured envelope (Chinese, aligned
    with utils/llm_client.py's messages)."""
    if status == 401:
        return _err_payload("LLM_AUTH",
                            f"API 认证失败 (HTTP 401) — provider '{provider}' 的 API Key 无效或已过期",
                            "请检查 .env 文件或「配置」面板中的 API Key")
    if status == 403:
        return _err_payload("LLM_FORBIDDEN",
                            f"API 访问被拒绝 (HTTP 403) — API Key 没有访问模型 '{model}' 的权限",
                            "请检查 API Key 权限和模型名称")
    if status == 404:
        return _err_payload("LLM_NOT_FOUND",
                            f"API 端点不存在 (HTTP 404) — base_url 或 model '{model}' 可能不正确",
                            "请检查 base_url 与模型名称")
    if status == 429:
        return _err_payload("LLM_RATE_LIMIT",
                            f"API 请求频率超限 (HTTP 429) — provider '{provider}' 限流",
                            "请稍后重试或降低请求频率（增大 request_interval）")
    if status >= 500:
        return _err_payload("LLM_SERVER",
                            f"LLM 服务端错误 (HTTP {status}) — provider '{provider}' 服务异常",
                            "请稍后重试")
    return _err_payload("LLM_HTTP_ERROR",
                        f"API 请求失败 (HTTP {status}): {body_text[:200]}",
                        "请检查请求参数后重试")


# ── health ───────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {"ok": True, "service": "pdf2skill-proxy"}


# ── LLM chat forwarding ──────────────────────────────────────────────

@app.post("/api/llm/chat")
def llm_chat(body: dict):
    """Forward a chat-completions request to the provider named in the body.

    Body: {provider, model, messages, temperature?, max_tokens?, response_format?}.
    The API key never leaves this process — the browser only names the provider.

    Returns: {ok: true, body: <upstream JSON>} or {ok: false, error: {code,...}}
    (HTTP 200 either way; the SDK unpacks the envelope).
    """
    provider = str(body.get("provider", ""))
    model = str(body.get("model", ""))
    messages = body.get("messages")
    if not provider or not model or not isinstance(messages, list):
        raise HTTPException(400, _err_payload(
            "BAD_REQUEST", "请求缺少 provider / model / messages 字段", "请检查 SDK 请求体"))

    base_url, api_key, _ = _resolve_provider(provider)

    settings = _load_settings()
    llm_cfg = settings.get("llm", {})
    timeout = int(llm_cfg.get("timeout", 300))
    max_retries = int(llm_cfg.get("max_retries", 3))

    payload = {
        "model": model,
        "messages": messages,
        "temperature": body.get("temperature", 0.3),
        "max_tokens": body.get("max_tokens", 16000),
    }
    if body.get("response_format"):
        payload["response_format"] = body["response_format"]

    url = f"{base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    last_error = None
    for attempt in range(max_retries):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=(5, timeout))
        except requests.ConnectionError:
            last_error = _err_payload("LLM_CONNECT", f"无法连接 {base_url}",
                                      "请检查网络连接、代理/VPN 设置，或该服务是否正在运行")
        except requests.Timeout:
            last_error = _err_payload("LLM_TIMEOUT", f"API 请求超时 ({timeout}s) — provider '{provider}' 响应过慢",
                                      "可尝试增大 settings.yaml 中的 llm.timeout 配置")
        else:
            if resp.status_code == 200:
                try:
                    return {"ok": True, "body": resp.json()}
                except ValueError:
                    import json as _json
                    return Response(
                        content=_json.dumps(_err_payload(
                            "LLM_PARSE", "LLM 返回了非 JSON 响应体，无法解析",
                            f"响应前 200 字符: {resp.text[:200]}"), ensure_ascii=False),
                        media_type="application/json")
            err = _map_llm_http_error(provider, model, resp.status_code, resp.text)
            # Only retry transient failures (429/5xx), never auth/config errors
            if resp.status_code in (429,) or resp.status_code >= 500:
                last_error = err
            else:
                raise HTTPException(200, err)
        if attempt < max_retries - 1:
            import time
            time.sleep(2 ** attempt)
    raise HTTPException(200, last_error)


# ── models list (ported from server.py) ─────────────────────────────

@app.get("/api/models")
def list_models(provider: str = ""):
    data = _load_settings()
    providers = data.get("llm", {}).get("providers", {})
    if not provider or provider not in providers:
        return _err_payload("UNKNOWN_PROVIDER", f"未知 Provider: {provider}", "请检查配置面板中的 Provider 名称")
    cfg = providers[provider]
    base_url = cfg.get("base_url", "")
    if not base_url:
        return _err_payload("NO_BASE_URL", f"Provider '{provider}' 缺少 base_url",
                            "请在 config/settings.yaml 中为该 Provider 配置 base_url")
    provider_key_env = f"{provider.upper().replace('-', '_')}_API_KEY"
    api_key = os.getenv(provider_key_env) or os.getenv(cfg.get("api_key_env", ""))
    if not api_key:
        return _err_payload("NO_API_KEY", f"Provider '{provider}' 未设置 API Key",
                            "请在「配置」面板 API Keys 区设置，或在 .env 中配置后重启服务")

    url = f"{base_url.rstrip('/')}/models"
    try:
        resp = requests.get(url, headers={"Authorization": f"Bearer {api_key}"}, timeout=(5, 12))
    except requests.ConnectionError:
        return _err_payload("CONNECT_FAILED", f"无法连接 {base_url}",
                            "请检查网络连接、代理/VPN 设置，或该服务是否正在运行")
    except requests.Timeout:
        return _err_payload("CONNECT_FAILED", f"请求 {base_url} 超时", "网络响应过慢，请稍后重试或检查网络")
    except requests.RequestException as e:
        return _err_payload("CONNECT_FAILED", f"请求失败: {e}", "请检查网络连接后重试")

    if resp.status_code == 401:
        return _err_payload("AUTH_FAILED", f"Provider '{provider}' API Key 认证失败", "请在「配置」面板重新设置 API Key")
    if resp.status_code == 404:
        return _err_payload("ENDPOINT_NOT_FOUND", f"Provider '{provider}' 不支持 /models 端点",
                            "该 Provider 无法获取模型列表，请手动输入模型名称")
    if resp.status_code != 200:
        return _err_payload("UPSTREAM_ERROR", f"上游返回 HTTP {resp.status_code}", "请稍后重试，或手动输入模型名称")

    try:
        raw_models = resp.json().get("data", [])
        models = sorted({m["id"] for m in raw_models if isinstance(m, dict) and m.get("id")})
    except (ValueError, TypeError, KeyError, AttributeError):
        return _err_payload("PARSE_FAILED", "无法解析模型列表响应",
                            "该 Provider 的响应格式与预期不符，请手动输入模型名称")
    return {"ok": True, "provider": provider, "models": models[:200], "count": len(models)}


# ── API keys (masked status + in-memory set) ────────────────────────

@app.get("/api/keys")
def get_keys():
    data = _load_settings()
    providers = data.get("llm", {}).get("providers", {})

    def mask(val: str) -> str:
        return val[:4] + "****" if len(val) > 4 else ("****" if val else "")

    keys = {}
    for name, cfg in providers.items():
        env_var = cfg.get("api_key_env", "")
        val = os.getenv(env_var, "")
        keys[name] = {
            "api_key_env": env_var,
            "api_key_set": bool(val),
            "api_key_preview": mask(val),
        }
    mineru_val = os.getenv("MINERU_API_KEY", "")
    return {
        "ok": True,
        "providers": keys,
        "mineru": {"api_key_set": bool(mineru_val), "api_key_preview": mask(mineru_val)},
    }


@app.post("/api/keys")
def set_key(body: dict):
    env_var = body.get("env_var", "")
    value = body.get("value", "")
    if not env_var or not value:
        raise HTTPException(400, "env_var and value required")
    if not ENV_VAR_RE.match(env_var):
        raise HTTPException(422, detail=_err_payload(
            "INVALID_ENV_VAR", f"环境变量名 '{env_var}' 不合法",
            "环境变量名只能包含字母、数字、下划线，且以字母或下划线开头"))
    os.environ[env_var] = value
    return {"ok": True}


# ── provider registry (settings.yaml llm section) ───────────────────

@app.get("/api/providers")
def get_providers():
    data = _load_settings()
    return {"ok": True, "providers": data.get("llm", {}).get("providers", {}),
            "routers": data.get("llm", {}).get("routers", {}),
            "llm": {k: v for k, v in data.get("llm", {}).items()
                    if k not in ("providers", "routers")}}


@app.post("/api/providers")
def update_providers(body: dict):
    """Add/update/delete providers in settings.yaml (llm.providers only).

    Validation matches server.py's /api/config rules: provider name pattern,
    base_url scheme, api_key_env pattern, router-reference delete block,
    last-provider delete block. All validated before writing.
    """
    current = _load_settings()
    providers = current.setdefault("llm", {}).setdefault("providers", {})
    routers = current["llm"].setdefault("routers", {})

    if "providers" in body:
        for name, cfg in body["providers"].items():
            if not PROVIDER_NAME_RE.match(name):
                raise HTTPException(422, detail=_err_payload(
                    "INVALID_PROVIDER_NAME", f"Provider 名称 '{name}' 不合法",
                    "名称只能包含小写字母、数字、下划线和连字符，且以字母或数字开头"))
            if name not in providers:
                providers[name] = {}
            for k, v in cfg.items():
                if k in ("api_key_set", "api_key_preview"):
                    continue
                if k == "base_url":
                    if not isinstance(v, str):
                        raise HTTPException(422, detail=_err_payload(
                            "INVALID_BASE_URL", f"Provider '{name}' 的 base_url 必须是字符串",
                            "示例: https://api.example.com/v1"))
                    v = v.strip()
                    if not v:
                        raise HTTPException(422, detail=_err_payload(
                            "MISSING_BASE_URL", f"Provider '{name}' 的 base_url 不能为空", "请填写 base_url"))
                    if not v.startswith(("http://", "https://")):
                        raise HTTPException(422, detail=_err_payload(
                            "INVALID_BASE_URL", f"Provider '{name}' 的 base_url 必须以 http:// 或 https:// 开头",
                            "示例: https://api.example.com/v1"))
                elif k == "api_key_env":
                    if not isinstance(v, str):
                        raise HTTPException(422, detail=_err_payload(
                            "INVALID_ENV_VAR", f"Provider '{name}' 的 api_key_env 必须是字符串", ""))
                    v = v.strip()
                    if not v:
                        raise HTTPException(422, detail=_err_payload(
                            "MISSING_ENV_VAR", f"Provider '{name}' 的 api_key_env 不能为空",
                            "请在配置面板填写 API Key 环境变量名"))
                    if not ENV_VAR_RE.match(v):
                        raise HTTPException(422, detail=_err_payload(
                            "INVALID_ENV_VAR", f"Provider '{name}' 的 api_key_env '{v}' 不合法",
                            "环境变量名只能包含字母、数字、下划线，且以字母或下划线开头"))
                providers[name][k] = v

    if "delete_providers" in body:
        for name in body["delete_providers"]:
            if name not in providers:
                continue
            used_by = [stage for stage, p in routers.items() if p == name]
            if used_by:
                raise HTTPException(422, detail=_err_payload(
                    "PROVIDER_IN_USE",
                    f"Provider '{name}' 正被阶段 {', '.join(used_by)} 使用，无法删除",
                    "请先在阶段卡片中将对应阶段切换到其他 Provider"))
            if len(providers) <= 1:
                raise HTTPException(422, detail=_err_payload(
                    "LAST_PROVIDER", f"无法删除最后一个 Provider '{name}'", "至少保留一个 Provider"))
            del providers[name]

    if "routers" in body:
        for stage, name in body["routers"].items():
            if name and name not in providers:
                raise HTTPException(422, detail=_err_payload(
                    "ROUTER_UNKNOWN_PROVIDER", f"阶段 {stage} 引用了不存在的 Provider '{name}'",
                    "请选择配置中已有的 Provider"))
        current["llm"]["routers"] = body["routers"]

    _save_settings(current)
    return {"ok": True}


# ── MinerU proxy (remote API) ────────────────────────────────────────

def _mineru_headers() -> dict:
    api_key = os.getenv("MINERU_API_KEY", "")
    if not api_key:
        raise HTTPException(200, _err_payload(
            "MINERU_AUTH", "MINERU_API_KEY 未在服务端设置",
            "请在 .env 中配置 MINERU_API_KEY 后重启代理服务，或在「配置」面板设置"))
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


@app.post("/api/mineru/file-urls/batch")
async def mineru_file_urls(request: Request):
    """Forward the signed-upload-URL request to MinerU."""
    payload = await request.json()
    try:
        resp = requests.post(f"{MINERU_BASE_URL}/file-urls/batch",
                             headers=_mineru_headers(), json=payload, timeout=(5, 30))
    except requests.RequestException as e:
        raise HTTPException(200, _err_payload("MINERU_CONNECT", f"无法连接 MinerU API: {e}",
                                              "请检查网络连接或稍后重试"))
    return Response(content=resp.text, media_type="application/json")


@app.put("/api/mineru/upload")
async def mineru_upload(request: Request, url: str):
    """Stream the PDF bytes from the browser to MinerU's signed upload URL."""
    body = await request.body()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "invalid url")
    try:
        resp = requests.put(url, data=body, timeout=(5, 300))
    except requests.RequestException as e:
        raise HTTPException(200, _err_payload("MINERU_UPLOAD", f"上传 PDF 到 MinerU 失败: {e}",
                                              "请检查网络连接后重试"))
    if resp.status_code not in (200, 201):
        raise HTTPException(200, _err_payload("MINERU_UPLOAD",
                                              f"MinerU 上传失败 (HTTP {resp.status_code})",
                                              "请重试；若持续失败请检查文件大小是否超限"))
    return {"ok": True, "status": resp.status_code}


@app.get("/api/mineru/extract-results/batch/{batch_id}")
def mineru_results(batch_id: str):
    """Poll the MinerU batch results endpoint."""
    if not re.match(r"^[A-Za-z0-9_-]+$", batch_id):
        raise HTTPException(400, "invalid batch_id")
    try:
        resp = requests.get(f"{MINERU_BASE_URL}/extract-results/batch/{batch_id}",
                            headers=_mineru_headers(), timeout=(5, 30))
    except requests.RequestException as e:
        raise HTTPException(200, _err_payload("MINERU_CONNECT", f"无法连接 MinerU API: {e}",
                                              "请检查网络连接或稍后重试"))
    return Response(content=resp.text, media_type="application/json")


@app.get("/api/mineru/download")
def mineru_download(url: str):
    """Download the result ZIP from MinerU and stream it to the browser."""
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "invalid url")
    try:
        resp = requests.get(url, timeout=(5, 300))
    except requests.RequestException as e:
        raise HTTPException(200, _err_payload("MINERU_DOWNLOAD", f"下载 MinerU 结果失败: {e}",
                                              "请检查网络连接后重试"))
    if resp.status_code != 200:
        raise HTTPException(200, _err_payload("MINERU_DOWNLOAD",
                                              f"下载 MinerU 结果失败 (HTTP {resp.status_code})", "请重试"))
    return Response(content=resp.content, media_type="application/zip")


# ── MinerU proxy (self-hosted Gradio) ───────────────────────────────

_GRADIO_LANG_MAP = {
    "ch": "ch (Chinese, English, Chinese Traditional)",
    "ch_lite": "ch_lite (Chinese, English, Chinese Traditional, Japanese)",
    "ch_server": "ch_server (Chinese, English, Chinese Traditional, Japanese)",
    "en": "en (English)",
    "korean": "korean (Korean, English)",
    "japan": "japan (Chinese, English, Chinese Traditional, Japanese)",
    "chinese_cht": "chinese_cht (Chinese, English, Chinese Traditional, Japanese)",
}


@app.post("/api/mineru/local")
async def mineru_local(
    file: UploadFile = File(...),
    base_url: str = Form("http://127.0.0.1:7860"),
    backend: str = Form("vlm-auto-engine"),
    formula_enable: bool = Form(True),
    table_enable: bool = Form(True),
    language: str = Form("ch"),
):
    """Convert a PDF via a self-hosted MinerU Gradio endpoint.

    The browser uploads the PDF bytes; this proxy invokes the Gradio API with
    gradio_client (same parameters as core/pdf_processor.py) and streams the
    resulting ZIP back.
    """
    try:
        from gradio_client import Client, handle_file
    except ImportError:
        raise HTTPException(200, _err_payload("MINERU_LOCAL_DEP",
                                              "服务端未安装 gradio_client",
                                              "请运行: pip install gradio_client"))

    base_url = base_url.rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        raise HTTPException(400, "invalid base_url")

    tmp_dir = tempfile.mkdtemp(prefix="pdf2skill_mineru_")
    tmp_pdf = Path(tmp_dir) / (file.filename or "input.pdf")
    with open(tmp_pdf, "wb") as f:
        shutil.copyfileobj(file.file, f)

    gradio_lang = _GRADIO_LANG_MAP.get(language, _GRADIO_LANG_MAP["ch"])
    try:
        client = Client(base_url)
    except Exception as e:
        raise HTTPException(200, _err_payload("MINERU_LOCAL_CONN",
                                              f"无法连接到本地 MinerU 服务 ({base_url}): {e}",
                                              "请确认自部署 MinerU 服务正在运行且地址正确"))
    try:
        result = client.predict(
            file_path=handle_file(str(tmp_pdf)),
            end_pages=1000,
            is_ocr=False,
            formula_enable=formula_enable,
            table_enable=table_enable,
            language=gradio_lang,
            backend=backend,
            url="http://localhost:30000",
            api_name="/convert_to_markdown_stream",
        )
    except Exception as e:
        raise HTTPException(200, _err_payload("MINERU_LOCAL_PREDICT", f"本地 MinerU 解析失败: {e}",
                                              "请检查自部署 MinerU 服务日志"))

    zip_path = result[1] if isinstance(result, (tuple, list)) and len(result) > 1 else None
    if not zip_path or not os.path.exists(zip_path):
        raise HTTPException(200, _err_payload("MINERU_LOCAL_RESULT",
                                              f"本地 MinerU 未返回有效的 ZIP: {result}",
                                              "请检查自部署 MinerU 服务版本是否匹配"))
    with open(zip_path, "rb") as f:
        zip_bytes = f.read()
    return Response(content=zip_bytes, media_type="application/zip")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8502)
