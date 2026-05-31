import json
import ast
import re
import time
import os
from config.config import config
from utils.logger import logger, LLMParsingError
from utils.retry_client import RetrySession

class LLMClient:
    """
    Client for interacting with any OpenAI-compatible LLM provider.
    Provider configuration is fully driven by settings.yaml — no provider names are hardcoded.
    """
    def __init__(self, stage: str = "skill_engine"):
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
        self.request_interval = float(os.getenv("REQUEST_INTERVAL", config.get("llm.request_interval", 0.0)))
        logger.info(f"Initialized LLMClient [{self.stage}] -> provider: {self.provider}, model: {self.model}, base_url: {self.base_url}, interval: {self.request_interval}s")

    def chat(self, prompt: str, is_json: bool = False, max_tokens: int = None) -> str:
        """Sends a prompt to the configured LLM provider and returns the response."""
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
        resp = RetrySession.post(url, headers=self.headers, json=payload, timeout=self.timeout)
        return resp.json()["choices"][0]["message"]["content"]

    def parse_json_response(self, text: str) -> dict:
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