"""
Thin proxy backend for the pure-frontend pdf2skill (port 8502).

The browser SDK runs the whole pipeline client-side; this server only holds
secrets and forwards traffic:

  - POST /api/llm/chat            forward chat completions to an OpenAI-compatible
                                  provider (API key resolved server-side from .env)
  - GET  /api/models              list models for a provider
  - GET  /api/keys                masked API-key status (LLM providers + MinerU)
  - POST /api/keys                set an API key in .env and process memory
  - GET  /api/providers           provider registry from settings.yaml
  - POST /api/providers           add/update/delete providers in settings.yaml
  - GET  /api/config              full web-facing config from settings.yaml
  - POST /api/config              persist providers/routers/pdf/mineru
  - POST /api/mineru/file-urls/batch       MinerU signed-upload URL (remote)
  - PUT  /api/mineru/upload?url=           PUT PDF bytes to the signed URL
  - GET  /api/mineru/extract-results/batch/{batch_id}   poll MinerU batch results
  - GET  /api/mineru/download?url=         download the result ZIP
  - POST /api/mineru/local        convert via a self-hosted MinerU Gradio endpoint
  - GET  /api/jobs                list resumable server-side ``outputs/`` jobs
  - GET  /api/jobs/{id}/bundle    zip one server job for browser OPFS import
  - GET  /api/health              liveness probe

All errors use the structured envelope {ok: false, error: {code, message, advice}}
(mostly with HTTP 200 so the frontend can render them uniformly).
"""

import ipaddress
import os
import re
import shutil
import socket
import tempfile
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests
import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse

load_dotenv(Path(__file__).parent.parent / ".env")

PROJECT_ROOT = Path(__file__).parent.parent
SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.yaml"
ENV_PATH = PROJECT_ROOT / ".env"
MINERU_BASE_URL = "https://mineru.net/api/v4"

PROVIDER_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
ENV_VAR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAX_UPLOAD_BYTES = 200 * 1024 * 1024

# Browsers send Origin on every cross-origin (and same-origin POST/PUT)
# request; a disallowed page could otherwise drive the proxy with a
# no-preflight text/plain POST and exfiltrate keys. Non-browser clients
# (curl) send no Origin and pass. Extend via PDF2SKILL_ALLOWED_ORIGINS.
ALLOWED_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]
ALLOWED_ORIGINS += [
    o.strip()
    for o in os.getenv("PDF2SKILL_ALLOWED_ORIGINS", "").split(",")
    if o.strip()
]

# Host header allowlist — DNS rebinding defense. The same-origin exemption in
# the Origin check below is only safe when the Host itself is trusted: a
# rebound page sends Host=attacker.com:8502 which matches its own Origin, so
# "Origin == Host" alone must never be the gate.
# 8502 = standalone dev proxy (Vite on 5173); 8501 = mounted inside server.py,
# which serves the built frontend from the same origin.
ALLOWED_HOSTS = {
    "127.0.0.1:8501", "localhost:8501",
    "127.0.0.1:8502", "localhost:8502",
}
ALLOWED_HOSTS |= {
    h.strip().lower()
    for h in os.getenv("PDF2SKILL_ALLOWED_HOSTS", "").split(",")
    if h.strip()
}

app = FastAPI(title="pdf2skill-proxy")

try:
    from .job_bridge import router as job_bridge_router
except ImportError:  # `python frontend/proxy_server.py`
    from job_bridge import router as job_bridge_router

app.include_router(job_bridge_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _check_host_and_origin(request: Request, call_next):
    # Applies to every method: a rebound page is same-origin for the browser,
    # so GET responses are readable too.
    host = (request.headers.get("host") or "").lower()
    if host not in ALLOWED_HOSTS:
        return JSONResponse(
            status_code=403,
            content=_err_payload(
                "FORBIDDEN_HOST",
                f"Host '{host or '(缺失)'}' 不在允许列表中",
                "请通过 127.0.0.1/localhost 的 8501（server.py）或 8502（proxy_server.py）访问；"
                "如经反向代理换端口，请用 PDF2SKILL_ALLOWED_HOSTS 追加",
            ),
        )
    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        origin = request.headers.get("origin")
        if origin and origin not in ALLOWED_ORIGINS:
            # Host is already allowlisted, so same-host requests are covered
            # above; any other Origin must be on the explicit allowlist.
            if urlparse(origin).netloc != host:
                return JSONResponse(
                    status_code=403,
                    content=_err_payload(
                        "FORBIDDEN_ORIGIN",
                        f"来源 {origin} 未被允许访问代理后端",
                        "请从与代理后端同源的页面访问，或用 PDF2SKILL_ALLOWED_ORIGINS 追加允许的来源",
                    ),
                )
    return await call_next(request)


def _is_unsafe_host(host: str) -> bool:
    """True for loopback/LAN/link-local/reserved/multicast targets.

    Non-standard numeric hostnames (127.1, 0x7f000001, 2130706433) fail
    ip_address() parsing but still resolve to loopback on common platforms,
    so they are rejected via inet_aton as well."""
    host = (host or "").lower()
    if not host:
        return True
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        try:
            socket.inet_aton(host)
        except (OSError, AttributeError):
            return False
        return True
    return (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_reserved or ip.is_multicast or ip.is_unspecified
    )


def _reject_unsafe_outbound_url(url: str) -> None:
    """Outbound relay targets (MinerU signed upload/result URLs) must be
    public — never loopback/LAN — so the proxy can't be used as an SSRF
    pivot. DNS names are not resolved here (rebinding is out of scope)."""
    host = urlparse(url).hostname or ""
    if _is_unsafe_host(host):
        raise HTTPException(400, _err_payload(
            "INVALID_URL_TARGET",
            f"拒绝访问内网地址: {host}",
            "该端点仅用于转发 MinerU 签发的公网链接",
        ))


_ALLOW_PRIVATE_LLM_BASE_URL = os.getenv(
    "PDF2SKILL_ALLOW_PRIVATE_BASE_URL", ""
).strip().lower() not in ("", "0", "false", "no")


def _check_llm_base_url_allowed(provider: str, base_url: str) -> None:
    """Providers live behind the proxy's real API keys, so by default their
    base_url must not point back into loopback/LAN (SSRF read amplifier).
    Local LLM servers (Ollama 等) opt in via PDF2SKILL_ALLOW_PRIVATE_BASE_URL."""
    if _ALLOW_PRIVATE_LLM_BASE_URL:
        return
    if _is_unsafe_host(urlparse(base_url).hostname or ""):
        raise HTTPException(422, _err_payload(
            "PRIVATE_BASE_URL",
            f"Provider '{provider}' 的 base_url 指向内网/本机地址",
            "代理持有真实 API Key，默认禁止内网 base_url；"
            "如确需本地 LLM 服务，请在服务端环境设置 PDF2SKILL_ALLOW_PRIVATE_BASE_URL=1",
        ))


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


def _config_error(code: str, message: str, advice: str) -> HTTPException:
    return HTTPException(422, detail=_err_payload(code, message, advice))


def _write_env_value(env_var: str, value: str) -> None:
    """Persist one key in .env, preserving comments and unrelated lines.

    Values are double-quoted. python-dotenv always expands ``${...}`` on load
    (even inside quotes), so such values cannot round-trip and are rejected.
    """
    if any(ch in value for ch in ("\r", "\n", "\x00")):
        raise _config_error(
            "INVALID_KEY_VALUE",
            "API Key 不能包含换行或空字符",
            "请检查是否误粘贴了多行内容",
        )
    if "${" in value:
        raise _config_error(
            "INVALID_KEY_VALUE",
            "API Key 不能包含 ${...} 片段",
            ".env 会把它当作变量展开，请确认复制的内容正确",
        )

    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    assignment = f'{env_var}="{escaped}"'
    try:
        original = (
            ENV_PATH.read_text(encoding="utf-8-sig") if ENV_PATH.exists() else ""
        )
    except OSError as e:
        raise HTTPException(500, detail=_err_payload(
            "ENV_READ_FAILED", f"无法读取 .env: {e}",
            "请检查项目根目录 .env 的读取权限"))

    lines = original.splitlines()
    if not lines:
        lines = ["# pdf2skill configuration"]
    assignment_re = re.compile(rf"^\s*(?:export\s+)?{re.escape(env_var)}\s*=")
    found = False
    for i, line in enumerate(lines):
        if assignment_re.match(line):
            lines[i] = assignment
            found = True
    if not found:
        if lines[-1].strip():
            lines.append("")
        lines.append(assignment)
    new_text = "\n".join(lines) + "\n"

    tmp_name: Optional[str] = None
    try:
        fd, tmp_name = tempfile.mkstemp(prefix=".env-", dir=str(ENV_PATH.parent))
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(new_text)
        os.replace(tmp_name, ENV_PATH)
        tmp_name = None
        try:
            os.chmod(ENV_PATH, 0o600)
        except OSError:
            pass
    except OSError as e:
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
        raise HTTPException(500, detail=_err_payload(
            "ENV_WRITE_FAILED", f"无法写入 .env: {e}",
            "请检查项目根目录的写入权限或磁盘空间"))


def _require_str(
    value, field: str, *, max_len: int = 128, pattern: Optional[str] = None
) -> str:
    if not isinstance(value, str):
        raise _config_error("INVALID_CONFIG", f"{field} 必须是字符串", "请检查配置输入")
    value = value.strip()
    if not value:
        raise _config_error("INVALID_CONFIG", f"{field} 不能为空", "请检查配置输入")
    if len(value) > max_len:
        raise _config_error(
            "INVALID_CONFIG", f"{field} 过长（上限 {max_len} 字符）", "请检查配置输入")
    if pattern and not re.match(pattern, value):
        raise _config_error("INVALID_CONFIG", f"{field} 格式不合法", "请检查配置输入")
    return value


def _require_bool(value, field: str) -> bool:
    if not isinstance(value, bool):
        raise _config_error("INVALID_CONFIG", f"{field} 必须是布尔值", "请检查配置输入")
    return value


def _require_number(
    value, field: str, *, minimum: float, maximum: float, integer: bool
):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _config_error("INVALID_PDF_CONFIG", f"{field} 必须是数字", "请检查 PDF 配置")
    if integer and int(value) != value:
        raise _config_error("INVALID_PDF_CONFIG", f"{field} 必须是整数", "请检查 PDF 配置")
    number = int(value) if integer else float(value)
    if not (minimum <= number <= maximum):
        raise _config_error(
            "INVALID_PDF_CONFIG",
            f"{field} 超出范围（{minimum} ~ {maximum}）",
            "请检查 PDF 配置",
        )
    return number


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
    _check_llm_base_url_allowed(provider, base_url)
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

    Body: {provider, model, messages, temperature?, max_tokens?,
           response_format?, reasoning_effort?}.
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
    llm_cfg = settings.get("llm", {}) or {}
    try:
        timeout = max(10, min(int(llm_cfg.get("timeout") or 300), 600))
    except (TypeError, ValueError):
        timeout = 300
    try:
        max_retries = max(1, min(int(llm_cfg.get("max_retries") or 3), 6))
    except (TypeError, ValueError):
        max_retries = 3

    payload = {
        "model": model,
        "messages": messages,
        "temperature": body.get("temperature", 0.3),
        "max_tokens": body.get("max_tokens", 16000),
    }
    if body.get("response_format"):
        payload["response_format"] = body["response_format"]
    reasoning_effort = body.get("reasoning_effort")
    if isinstance(reasoning_effort, str) and reasoning_effort.strip():
        payload["reasoning_effort"] = reasoning_effort.strip()

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
            time.sleep(2 ** attempt)
    raise HTTPException(200, last_error or _err_payload(
        "LLM_UNKNOWN", "LLM 请求失败且无详细错误", "请检查服务端日志后重试"))


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


def _registered_api_key_envs() -> set:
    """api_key_env names declared in settings.yaml providers."""
    try:
        data = _load_settings()
    except HTTPException:
        return set()
    providers = data.get("llm", {}).get("providers", {}) or {}
    return {cfg.get("api_key_env", "") for cfg in providers.values() if cfg.get("api_key_env")}


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
    # A free-form env write would let a compromised page repoint ALL_PROXY /
    # HTTP_PROXY and route every outbound request (with real Bearer keys)
    # through an attacker proxy. Only key-shaped variables may be written.
    if not (env_var.endswith("_API_KEY") or env_var in _registered_api_key_envs()):
        raise HTTPException(422, detail=_err_payload(
            "FORBIDDEN_ENV_VAR", f"拒绝写入环境变量 '{env_var}'",
            "只允许写入 *_API_KEY 形式的密钥变量，或 settings.yaml 中注册的 api_key_env"))
    _write_env_value(env_var, value)
    os.environ[env_var] = value
    return {"ok": True, "persisted": True}


# ── provider registry (settings.yaml llm section) ───────────────────

@app.get("/api/providers")
def get_providers():
    data = _load_settings()
    return {"ok": True, "providers": data.get("llm", {}).get("providers", {}),
            "routers": data.get("llm", {}).get("routers", {}),
            "llm": {k: v for k, v in data.get("llm", {}).items()
                    if k not in ("providers", "routers")}}


def _apply_provider_updates(current: dict, body: dict) -> None:
    """Validate and apply provider/router edits to ``current`` in memory."""
    llm = current.setdefault("llm", {})
    providers = llm.setdefault("providers", {})
    routers = llm.setdefault("routers", {})

    if "providers" in body:
        updates = body["providers"]
        if not isinstance(updates, dict):
            raise _config_error("INVALID_PROVIDERS", "providers 必须是对象", "请检查配置输入")
        for name, cfg in updates.items():
            if not isinstance(cfg, dict):
                raise _config_error(
                    "INVALID_PROVIDERS", f"Provider '{name}' 的配置必须是对象", "请检查配置输入")
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
                    _check_llm_base_url_allowed(name, v)
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

    if "routers" in body:
        if not isinstance(body["routers"], dict):
            raise _config_error("INVALID_ROUTERS", "routers 必须是对象", "请检查配置输入")
        for stage, name in body["routers"].items():
            if stage not in ("chunking", "peeling", "skillEngine"):
                raise _config_error(
                    "INVALID_ROUTERS", f"未知的阶段 '{stage}'",
                    "阶段只能是 chunking / peeling / skillEngine")
            if name and name not in providers:
                raise HTTPException(422, detail=_err_payload(
                    "ROUTER_UNKNOWN_PROVIDER", f"阶段 {stage} 引用了不存在的 Provider '{name}'",
                    "请选择配置中已有的 Provider"))
        # Merge the three web stages so CLI-only aliases (_provider keys)
        # already present in settings.yaml survive a web save; keep those
        # aliases in sync when they exist.
        alias_map = {
            "chunking": "chunking_provider",
            "peeling": "peeling_provider",
            "skillEngine": "skill_engine_provider",
        }
        for stage, name in body["routers"].items():
            routers[stage] = name
            alias = alias_map[stage]
            if alias in routers:
                routers[alias] = name

    # Routers are applied first so a single save can switch stages away from
    # a provider and delete it in the same request.
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


@app.post("/api/providers")
def update_providers(body: dict):
    """Add/update/delete providers in settings.yaml (llm.providers only).

    Validation matches server.py's /api/config rules: provider name pattern,
    base_url scheme, api_key_env pattern, router-reference delete block,
    last-provider delete block. All validated before writing.
    """
    current = _load_settings()
    _apply_provider_updates(current, body)
    _save_settings(current)
    return {"ok": True}


# ── full web config (settings.yaml pdf/mineru sections) ─────────────

_PDF_LIMITS = {
    "page_limit": (1, 100_000, True),
    "request_interval": (0.0, 3600.0, False),
    "chunk_merge_threshold": (1, 10_000_000, True),
    "chunk_min_threshold": (1, 10_000_000, True),
}


def _apply_pdf_config(current: dict, payload) -> None:
    if not isinstance(payload, dict):
        raise _config_error("INVALID_PDF_CONFIG", "pdf 必须是对象", "请检查 PDF 配置")
    pdf = current.setdefault("pdf", {})
    for key, (minimum, maximum, integer) in _PDF_LIMITS.items():
        if key in payload:
            pdf[key] = _require_number(
                payload[key], f"pdf.{key}",
                minimum=minimum, maximum=maximum, integer=integer)


def _apply_mineru_config(current: dict, payload) -> None:
    if not isinstance(payload, dict):
        raise _config_error("INVALID_MINERU_CONFIG", "mineru 必须是对象", "请检查 MinerU 配置")
    mineru = current.setdefault("mineru", {})

    if "api_mode" in payload:
        mode = payload["api_mode"]
        if mode not in ("local", "remote"):
            raise _config_error(
                "INVALID_MINERU_CONFIG", "mineru.api_mode 只能是 local 或 remote",
                "请选择本地或远程模式")
        mineru["api_mode"] = mode
    if "language" in payload:
        mineru["language"] = _require_str(
            payload["language"], "mineru.language",
            max_len=32, pattern=r"^[A-Za-z0-9_-]+$")

    local = payload.get("local")
    if local is not None:
        if not isinstance(local, dict):
            raise _config_error(
                "INVALID_MINERU_CONFIG", "mineru.local 必须是对象", "请检查 MinerU 配置")
        target = mineru.setdefault("local", {})
        if "base_url" in local:
            url = _require_str(
                local["base_url"], "mineru.local.base_url", max_len=512)
            if not url.startswith(("http://", "https://")):
                raise _config_error(
                    "INVALID_MINERU_CONFIG",
                    "mineru.local.base_url 必须以 http:// 或 https:// 开头",
                    "示例: http://localhost:7860")
            target["base_url"] = url
        if "backend" in local:
            target["backend"] = _require_str(
                local["backend"], "mineru.local.backend",
                max_len=64, pattern=r"^[A-Za-z0-9._-]+$")
        if "parse_method" in local:
            target["parse_method"] = _require_str(
                local["parse_method"], "mineru.local.parse_method",
                max_len=32, pattern=r"^[A-Za-z0-9._-]+$")
        for key in ("formula_enable", "table_enable"):
            if key in local:
                target[key] = _require_bool(local[key], f"mineru.local.{key}")

    remote = payload.get("remote")
    if remote is not None:
        if not isinstance(remote, dict):
            raise _config_error(
                "INVALID_MINERU_CONFIG", "mineru.remote 必须是对象", "请检查 MinerU 配置")
        target = mineru.setdefault("remote", {})
        if "model_version" in remote:
            target["model_version"] = _require_str(
                remote["model_version"], "mineru.remote.model_version",
                max_len=64, pattern=r"^[A-Za-z0-9._-]+$")
        if "page_ranges" in remote:
            value = remote["page_ranges"]
            if (
                not isinstance(value, str)
                or len(value) > 256
                or any(ch in value for ch in ("\r", "\n", "\x00"))
            ):
                raise _config_error(
                    "INVALID_MINERU_CONFIG", "mineru.remote.page_ranges 不合法",
                    "页码范围应为逗号分隔的字符串，例如 1-10,20-30")
            target["page_ranges"] = value.strip()
        for key in ("is_ocr", "enable_formula", "enable_table"):
            if key in remote:
                target[key] = _require_bool(remote[key], f"mineru.remote.{key}")
        if "extra_formats" in remote:
            formats = remote["extra_formats"]
            if (
                not isinstance(formats, list)
                or len(formats) > 16
                or any(not isinstance(x, str) or len(x) > 16 for x in formats)
            ):
                raise _config_error(
                    "INVALID_MINERU_CONFIG", "mineru.remote.extra_formats 不合法",
                    "应为字符串数组，例如 [\"docx\"]")
            target["extra_formats"] = formats


@app.get("/api/config")
def get_config():
    data = _load_settings()
    llm = data.get("llm", {}) or {}
    return {
        "ok": True,
        "providers": llm.get("providers", {}) or {},
        "routers": llm.get("routers", {}) or {},
        "pdf": data.get("pdf", {}) or {},
        "mineru": data.get("mineru", {}) or {},
    }


@app.post("/api/config")
def update_config(body: dict):
    """Persist the full web-facing config, preserving unrelated sections."""
    current = _load_settings()
    if "providers" in body or "delete_providers" in body or "routers" in body:
        _apply_provider_updates(current, body)
    if "pdf" in body:
        _apply_pdf_config(current, body["pdf"])
    if "mineru" in body:
        _apply_mineru_config(current, body["mineru"])
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
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, _err_payload(
            "INVALID_URL", "上传地址不合法", "请传入 MinerU 返回的 http(s) 上传地址"))
    _reject_unsafe_outbound_url(url)
    content_length = request.headers.get("content-length", "")
    if content_length.isdigit() and int(content_length) > _MAX_UPLOAD_BYTES:
        raise HTTPException(413, _err_payload(
            "PAYLOAD_TOO_LARGE",
            f"上传内容超过 {_MAX_UPLOAD_BYTES // (1024 * 1024)}MB 上限",
            "请压缩或拆分 PDF 后重试"))
    body = await request.body()
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
        raise HTTPException(400, _err_payload(
            "INVALID_URL", "下载地址不合法", "请传入 MinerU 返回的 http(s) 下载地址"))
    _reject_unsafe_outbound_url(url)
    try:
        resp = requests.get(url, timeout=(5, 300), stream=True)
    except requests.RequestException as e:
        raise HTTPException(200, _err_payload("MINERU_DOWNLOAD", f"下载 MinerU 结果失败: {e}",
                                              "请检查网络连接后重试"))
    if resp.status_code != 200:
        resp.close()
        raise HTTPException(200, _err_payload("MINERU_DOWNLOAD",
                                              f"下载 MinerU 结果失败 (HTTP {resp.status_code})", "请重试"))
    return StreamingResponse(resp.iter_content(chunk_size=256 * 1024),
                             media_type="application/zip")


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


# The Gradio endpoint fully controls the paths in `result`; reads must stay
# inside these roots (where gradio_client caches downloads) so a malicious or
# compromised endpoint can't make the proxy read arbitrary local files.
_ALLOWED_ZIP_ROOTS = (
    Path(tempfile.gettempdir()).resolve(),
    (Path.home() / ".cache").resolve(),
    (Path.home() / ".gradio").resolve(),
)


def _zip_path_confined(zip_path: Path) -> bool:
    try:
        resolved = zip_path.resolve()
    except OSError:
        return False
    return any(
        resolved.is_relative_to(root) for root in _ALLOWED_ZIP_ROOTS
    )


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
        raise HTTPException(400, _err_payload(
            "INVALID_BASE_URL", "本地 MinerU 地址不合法", "该地址应以 http:// 或 https:// 开头"))
    # base_url is deliberately unrestricted here: self-hosted MinerU
    # instances commonly live on the LAN, which is the feature this
    # endpoint exists for.

    tmp_dir = tempfile.mkdtemp(prefix="pdf2skill_mineru_")
    try:
        tmp_pdf = Path(tmp_dir) / Path(file.filename or "input.pdf").name
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
        zp = Path(str(zip_path))
        if not _zip_path_confined(zp):
            raise HTTPException(200, _err_payload(
                "MINERU_LOCAL_RESULT",
                f"本地 MinerU 返回的文件路径不在允许目录内: {zip_path}",
                "结果文件应位于系统临时目录或 Gradio 缓存目录，已拒绝读取其他路径"))
        with open(zp, "rb") as f:
            zip_bytes = f.read()
        if zip_bytes[:4] != b"PK\x03\x04":
            raise HTTPException(200, _err_payload("MINERU_LOCAL_RESULT",
                                                  "本地 MinerU 返回的内容不是有效的 ZIP 文件",
                                                  "请检查自部署 MinerU 服务版本是否匹配"))
        return Response(content=zip_bytes, media_type="application/zip")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8502)
