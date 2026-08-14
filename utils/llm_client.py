import json
import ast
import re
import time
import os
import requests
from config.config import config
from utils.logger import logger, LLMParsingError
from utils.retry_client import RetrySession

class LLMClient:
    """
    Client for interacting with any OpenAI-compatible LLM provider.
    Provider configuration is fully driven by settings.yaml — no provider names are hardcoded.
    """
    def __init__(self, stage: str = "skill_engine"):
        """Initialise an LLM client for a specific pipeline stage.

        Provider, model, and API key are resolved in order:
        per-stage env var → global env var → settings.yaml.

        Args:
            stage: Pipeline stage name (``"chunking"``, ``"peeling"``,
                or ``"skill_engine"``).

        Raises:
            ValueError: If provider, base_url, API key, or model is missing.
        """
        self.stage = stage
        
        # 1. Determine Provider
        # Priority: {STAGE}_PROVIDER > LLM_PROVIDER > config.routers.{stage}_provider
        stage_provider_key = f"{stage.upper()}_PROVIDER"
        self.provider = os.getenv(
            stage_provider_key, 
            os.getenv("LLM_PROVIDER", config.get(f"llm.routers.{stage}_provider", ""))
        ).lower()
        
        # 2. Validate provider exists in config
        available = list(config.get("llm.providers", {}).keys())
        if self.provider not in available:
            raise ValueError(
                f"Provider '{self.provider}' not found in config. "
                f"Available providers: {available}"
            )
        
        # 3. Read provider config
        provider_cfg = config.get(f"llm.providers.{self.provider}", {})
        self.base_url = provider_cfg.get("base_url", "").rstrip('/')
        api_key_env = provider_cfg.get("api_key_env", "")
        
        if not self.base_url:
            raise ValueError(
                f"Provider '{self.provider}' is missing 'base_url' in config. "
                f"Please add it to settings.yaml or set {self.provider.upper()}_BASE_URL."
            )
        
        # 4. Resolve API key
        # Priority: {PROVIDER}_API_KEY env > env var named by api_key_env field
        provider_key_env = f"{self.provider.upper().replace('-', '_')}_API_KEY"
        self.api_key = os.getenv(provider_key_env) or os.getenv(api_key_env)
        if not self.api_key:
            raise ValueError(
                f"API key not found for provider '{self.provider}'. "
                f"Set either {provider_key_env} or {api_key_env} in your .env file."
            )
        
        # 5. Determine Model
        # Priority: {STAGE}_MODEL > config.providers.{provider}.{stage}_model
        self.model = os.getenv(
            f"{stage.upper()}_MODEL",
            provider_cfg.get(f"{stage}_model")
        )
        if not self.model:
            raise ValueError(
                f"Model not configured for provider '{self.provider}', stage '{stage}'. "
                f"Set {stage.upper()}_MODEL or add '{stage}_model' under "
                f"llm.providers.{self.provider} in settings.yaml."
            )
        
        # 6. max_tokens by stage
        max_tokens_by_stage = {
            "chunking": 20000,
            "peeling": 16000,
            "skill_engine": 16000
        }
        self.max_tokens = int(os.getenv(f"{stage.upper()}_MAX_TOKENS", max_tokens_by_stage.get(stage, 16000)))
        
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        self.timeout = config.get("llm.timeout", 120)
        # REQUEST_INTERVAL env wins; otherwise read llm.request_interval,
        # falling back to the legacy pdf.request_interval key that the
        # config UI still writes.
        self.request_interval = float(os.getenv(
            "REQUEST_INTERVAL",
            config.get("llm.request_interval", config.get("pdf.request_interval", 0.0)),
        ))
        logger.info(f"Initialized LLMClient [{self.stage}] -> provider: {self.provider}, model: {self.model}, base_url: {self.base_url}, interval: {self.request_interval}s")

    def chat(self, prompt: str, is_json: bool = False, max_tokens: int = None) -> str:
        """Send a prompt to the configured LLM provider and return the response.

        Args:
            prompt: User message to send.
            is_json: If ``True``, requests JSON response format and injects
                a system prompt requiring valid JSON output.
            max_tokens: Override the default max_tokens for this call.

        Returns:
            The assistant's response text.

        Raises:
            LLMConnectionError: If the API endpoint cannot be reached.
            ValueError: If the API returns an authentication or config error.
            LLMParsingError: If the response format is unexpected.
        """
        if self.request_interval > 0:
            time.sleep(self.request_interval)

        messages = [{"role": "user", "content": prompt}]
        tokens = max_tokens if max_tokens is not None else self.max_tokens

        if is_json:
            if messages[0]["role"] != "system":
                messages.insert(0, {"role": "system", "content": "You are a helpful assistant that MUST output only valid JSON. Do not include markdown code block markers in your raw response unless explicitly requested."})

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": tokens
        }

        if is_json:
            payload["response_format"] = {"type": "json_object"}

        url = f"{self.base_url}/chat/completions"
        logger.debug(f"Sending request to provider={self.provider} model={self.model}")

        try:
            resp = RetrySession.post(url, headers=self.headers, json=payload, timeout=self.timeout)
        except requests.ConnectionError as e:
            from utils.retry_client import LLMConnectionError, _diagnose_connection_error
            diagnosis = _diagnose_connection_error(e, url)
            logger.error(f"[{self.stage}] {diagnosis}")
            raise LLMConnectionError(url, e) from e
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else "unknown"
            if status == 401:
                raise ValueError(
                    f"[{self.stage}] API 认证失败 (HTTP 401) — "
                    f"provider '{self.provider}' 的 API Key 无效或已过期。"
                    f"请检查 .env 文件中的 API Key 配置。"
                ) from e
            if status == 403:
                raise ValueError(
                    f"[{self.stage}] API 访问被拒绝 (HTTP 403) — "
                    f"provider '{self.provider}' 的 API Key 没有访问该模型的权限。"
                    f"请检查 API Key 权限和模型名称 '{self.model}'。"
                ) from e
            if status == 404:
                raise ValueError(
                    f"[{self.stage}] API 端点不存在 (HTTP 404) — "
                    f"base_url '{self.base_url}' 或 model '{self.model}' 可能不正确。"
                ) from e
            if status == 429:
                raise ValueError(
                    f"[{self.stage}] API 请求频率超限 (HTTP 429) — "
                    f"provider '{self.provider}' 限流，请稍后重试或降低并发。"
                ) from e
            if status >= 500:
                raise ValueError(
                    f"[{self.stage}] LLM 服务端错误 (HTTP {status}) — "
                    f"provider '{self.provider}' 服务异常，请稍后重试。"
                ) from e
            raise ValueError(
                f"[{self.stage}] API 请求失败 (HTTP {status}): {e}"
            ) from e
        except requests.Timeout as e:
            raise ValueError(
                f"[{self.stage}] API 请求超时 ({self.timeout}s) — "
                f"provider '{self.provider}' 响应过慢，可尝试增大 llm.timeout 配置。"
            ) from e

        # Parse response body
        try:
            body = resp.json()
        except (json.JSONDecodeError, ValueError) as e:
            raise LLMParsingError(
                f"[{self.stage}] LLM 返回了非 JSON 响应体，无法解析。"
                f"响应前 200 字符: {resp.text[:200]}"
            ) from e

        # Validate expected response structure
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            # Check for common API error structures
            if "error" in body:
                err = body["error"]
                err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                raise LLMParsingError(
                    f"[{self.stage}] LLM API 返回错误: {err_msg}"
                ) from e
            raise LLMParsingError(
                f"[{self.stage}] LLM 响应格式异常 — 缺少 choices[0].message.content。"
                f"响应结构: {list(body.keys())}"
            ) from e

        if content is None:
            raise LLMParsingError(
                f"[{self.stage}] LLM 返回了空内容 (content=null)。"
                f"模型 '{self.model}' 可能不支持该请求格式，或 max_tokens 过小。"
            )

        return content

    def parse_json_response(self, text: str) -> dict:
        """Extract a JSON object from an LLM response string.

        Tries ``json.loads`` first, then falls back to extracting the
        outermost ``{…}`` and parsing with ``ast.literal_eval``.
        Strips ``<think>`` tags emitted by reasoning models.

        Args:
            text: Raw LLM response.

        Returns:
            Parsed dictionary.

        Raises:
            LLMParsingError: If the response cannot be parsed as JSON.
        """
        # Compat with reasoning models that may include <think> tags
        text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
        
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            try:
                from_idx = text.find("{")
                to_idx = text.rfind("}")
                if from_idx != -1 and to_idx != -1:
                    clean_text = text[from_idx : to_idx + 1]
                    return ast.literal_eval(clean_text)
            except Exception as e:
                safe_text = text.replace(self.api_key, "***") if self.api_key else text
                raise LLMParsingError(f"Failed to parse JSON from LLM response: {e}\nResponse:\n{safe_text}")
        raise LLMParsingError("Invalid JSON format from LLM.")