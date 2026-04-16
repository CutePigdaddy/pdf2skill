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
    Client for interacting with various LLM providers (SiliconFlow, Google Gemini, etc.).
    Supports stage-specific configurations for chunking, peeling, and skill generation.
    """
    def __init__(self, stage: str = "skill_engine"):
        self.stage = stage
        
        # 1. Determine Provider
        # Priority: {STAGE}_PROVIDER > LLM_PROVIDER > config.routers.{stage}_provider
        stage_provider_key = f"{stage.upper()}_PROVIDER"
        self.provider = os.getenv(
            stage_provider_key, 
            os.getenv("LLM_PROVIDER", config.get(f"llm.routers.{stage}_provider", "siliconflow"))
        ).lower()
        
        # 2. Determine Model
        # Priority: {STAGE}_MODEL > {PROVIDER}_{STAGE}_MODEL > config.providers.{provider}.{stage}_model > fallback
        
        # Fallback model mapping per provider and stage
        fallback_map = {
            "siliconflow": {
                "chunking": "deepseek-ai/DeepSeek-R1",
                "peeling": "deepseek-ai/DeepSeek-V3",
                "skill_engine": "Pro/zai-org/GLM-4.7"
            },
            "google": {
                "chunking": "gemini-1.5-pro",
                "peeling": "gemini-1.5-flash",
                "skill_engine": "gemini-3.1-flash-lite-preview"
            },
            "vectorengine": {
                "chunking": "glm-5.1",
                "peeling": "gemini-3-flash",
                "skill_engine": "gpt-5.4-mini-high"
            }
        }
        
        provider_fallbacks = fallback_map.get(self.provider, fallback_map["siliconflow"])
        fallback_model = provider_fallbacks.get(stage, "Pro/zai-org/GLM-4.7")
        
        # Try reading model from environment variables with different priorities
        stage_model_env = f"{stage.upper()}_MODEL"
        # Try reading provider+stage specific model (e.g. GOOGLE_CHUNKING_MODEL)
        provider_stage_model_env = f"{self.provider.upper()}_{stage.upper()}_MODEL"
        
        self.model = os.getenv(
            stage_model_env, 
            os.getenv(
                provider_stage_model_env, 
                config.get(f"llm.providers.{self.provider}.{stage}_model", fallback_model)
            )
        )
        
        # Define max_tokens by stage to handle long documents or complex extractions
        max_tokens_by_stage = {
            "chunking": 20000,      # Initial chunking needs detailed analysis of the whole structure
            "peeling": 16000,       # Peeling generates structured split anchors
            "skill_engine": 16000   # Skill tagging needs concise keywords but may process large previews
        }
        self.max_tokens = int(os.getenv(f"{stage.upper()}_MAX_TOKENS", max_tokens_by_stage.get(stage, 16000)))
        
        self.timeout = config.get("llm.timeout", 120)
        # Request interval to avoid rate limits (Priority: env > config)
        self.request_interval = float(os.getenv("REQUEST_INTERVAL", config.get("llm.request_interval", 0.0)))
        logger.info(f"Initialized LLMClient [{self.stage}] -> provider: {self.provider}, model: {self.model}, interval: {self.request_interval}s")

    def chat(self, prompt: str, is_json: bool = False, max_tokens: int = None) -> str:
        """Sends a prompt to the configured LLM provider and returns the response."""
        if self.request_interval > 0:
            time.sleep(self.request_interval)
            
        messages = [{"role": "user", "content": prompt}]
        tokens = max_tokens if max_tokens is not None else self.max_tokens
        
        if self.provider == "siliconflow":
            return self._request_siliconflow(messages, is_json, tokens)
        elif self.provider == "google":
            return self._request_google(messages, is_json, tokens)
        elif self.provider == "vectorengine":
            return self._request_vectorengine(messages, is_json, tokens)
        else:
            raise ValueError(f"Unsupported LLM provider: {self.provider}")

    def _request_vectorengine(self, messages, is_json, max_tokens):
        api_key = os.getenv("VECTORENGINE_API_KEY")
        if not api_key:
            raise ValueError("VECTORENGINE_API_KEY is not set. Add it to .env or export it.")
            
        # Strip trailing slash. Note: expected base url is 'https://api.vectorengine.ai' (no /v1 ending because it has mixed endpoints)
        base_url = os.getenv("VECTORENGINE_BASE_URL", config.get("llm.providers.vectorengine.base_url", "https://api.vectorengine.ai")).rstrip('/')
        
        model_lower = self.model.lower()
        
        if "claude" in model_lower:
            # 假设按照 Anthropic 原生格式调用 (或者对应网关映射的 messages 接口)
            url = f"{base_url}/v1/messages"
            headers = {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            }
            system_msg = next((m["content"] for m in messages if m["role"] == "system"), "")
            user_msgs = [m for m in messages if m["role"] != "system"]
            if is_json:
                system_msg += " You must output valid JSON only."
            payload = {
                "model": self.model,
                "system": system_msg,
                "messages": user_msgs,
                "max_tokens": max_tokens,
                "temperature": 0.3
            }
            logger.debug(f"Sending native Anthropic request to Vector Engine: {url}")
            resp = RetrySession.post(url, headers=headers, json=payload, timeout=self.timeout)
            return resp.json()["content"][0]["text"]
            
        elif "gemini" in model_lower:
            # 严格按照文档中的 Gemini 原生格式调用
            url = f"{base_url}/v1beta/models/{self.model}:generateContent?key={api_key}"
            headers = {"Content-Type": "application/json"}
            
            prompt_text = "\n\n".join(f"{m.get('role', 'user')}: {m.get('content', '')}" for m in messages)
            if is_json:
                prompt_text = "Respond in valid JSON format ONLY.\n\n" + prompt_text
                
            payload = {
                "contents": [{
                    "role": "user",
                    "parts": [{"text": prompt_text}]
                }],
                "generationConfig": {
                    "temperature": 0.3,
                    "maxOutputTokens": max_tokens
                }
            }
            if is_json:
                payload["generationConfig"]["responseMimeType"] = "application/json"
                
            logger.debug(f"Sending native Gemini request to Vector Engine: {url}")
            resp = RetrySession.post(url, headers=headers, json=payload, timeout=self.timeout)
            data = resp.json()
            try:
                return data["candidates"][0]["content"]["parts"][0]["text"]
            except (KeyError, IndexError) as e:
                logger.error(f"VectorEngine Gemini response parsing failed: {data}")
                raise ValueError(f"Gemini payload error from VectorEngine: {data}")
                
        else:
            # 默认使用 OpenAI /v1/chat/completions 格式
            url = f"{base_url}/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            if is_json:
                if messages[0]["role"] != "system":
                    messages.insert(0, {"role": "system", "content": "You must output valid JSON. No markdown formatting around the JSON block."})
                    
            payload = {
                "model": self.model,
                "messages": messages,
                "temperature": 0.3,
                "max_tokens": max_tokens
            }
            # 针对支持 response_format 的 OpenAI 兼容模型
            if is_json:
                payload["response_format"] = {"type": "json_object"}
                
            logger.debug(f"Sending OpenAI format request to Vector Engine: {url} with model {self.model}")
            resp = RetrySession.post(url, headers=headers, json=payload, timeout=self.timeout)
            return resp.json()["choices"][0]["message"]["content"]

    def _request_siliconflow(self, messages, is_json, max_tokens):
        api_key = os.getenv("SILICONFLOW_API_KEY")
        if not api_key:
            raise ValueError("SILICONFLOW_API_KEY is not set")
            
        base_url = os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1").rstrip('/')
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": max_tokens
        }
        # DeepSeek 系列模型（如 V3/R1）在 SiliconFlow 上通常要求通过 system prompt 引导 JSON 格式，
        # 且 response_format 必须谨慎使用或确保模型支持。
        if is_json:
            # 强化 JSON 引导，避免某些模型版本不支持 response_format
            if messages[0]["role"] != "system":
                messages.insert(0, {"role": "system", "content": "You are a helpful assistant that MUST output only valid JSON. Do not include markdown code block markers in your raw response unless explicitly requested."})
            payload["response_format"] = {"type": "json_object"}
            
        logger.debug(f"Sending request to SiliconFlow: {base_url}/chat/completions with model {self.model}")
        resp = RetrySession.post(f"{base_url}/chat/completions", headers=headers, json=payload, timeout=self.timeout)
        return resp.json()["choices"][0]["message"]["content"]

    def _request_google(self, messages, is_json, max_tokens):
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("GOOGLE_API_KEY is not set")
            
        base_url = os.getenv("GOOGLE_BASE_URL", "https://generativelanguage.googleapis.com/v1beta")
        
        # Google API expects 'parts' list
        # Extract content from messages
        prompt_text = "\n\n".join(m.get("content", "") for m in messages if m.get("role") == "user")
        
        payload = {
            "contents": [{
                "parts": [{"text": prompt_text}]
            }],
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": max_tokens,
            }
        }
        
        # Use simple string for mime type to avoid complex JSON schema requirement in some versions
        if is_json:
            payload["generationConfig"]["responseMimeType"] = "application/json"
            
        logger.debug(f"Sending request to Google [{self.model}]...")
        resp = RetrySession.post(
            f"{base_url}/models/{self.model}:generateContent?key={api_key}", 
            json=payload, 
            timeout=self.timeout
        )
        
        data = resp.json()
        try:
            content = data["candidates"][0]["content"]["parts"][0]["text"]
            return content
        except (KeyError, IndexError) as e:
            logger.error(f"Invalid Google API response structure: {data}")
            raise LLMParsingError(f"Google API parsing failed: {e}")

    def parse_json_response(self, text: str) -> dict:
        # 兼容例如 DeepSeek R1 这种可能包含 <think> 标签的推理模型
        text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
        
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            try:
                # Strip markdown logic
                from_idx = text.find("{")
                to_idx = text.rfind("}")
                if from_idx != -1 and to_idx != -1:
                    clean_text = text[from_idx : to_idx + 1]
                    return ast.literal_eval(clean_text)
            except Exception as e:
                raise LLMParsingError(f"Failed to parse JSON from LLM response: {e}\nResponse:\n{text}")
        raise LLMParsingError("Invalid JSON format from LLM.")
