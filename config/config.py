import os
import yaml
from pathlib import Path
from dotenv import load_dotenv

class Config:
    """Singleton configuration manager.

    Loads settings from ``settings.yaml`` and overrides values with
    environment variables (``.env``).  Access nested keys with dot
    notation, e.g. ``config.get("llm.routers.chunking_provider")``.
    """

    _instance = None

    def __new__(cls, config_path=None):
        """Return the shared singleton instance, initialising on first call."""
        if cls._instance is None:
            cls._instance = super(Config, cls).__new__(cls)
            cls._instance._init_config(config_path)
        return cls._instance

    def _init_config(self, config_path):
        """Load YAML config and apply environment variable overrides."""
        load_dotenv()
        if config_path is None:
            config_path = Path(__file__).parent / "settings.yaml"
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                self._config = yaml.safe_load(f) or {}
        except FileNotFoundError:
            self._config = {}

        self.merge_env_vars()

    def merge_env_vars(self):
        """Override config values with matching environment variables.

        Handles PDF limits, MinerU local/remote settings, LLM provider
        routers, and per-provider field overrides (e.g.
        ``SILICONFLOW_BASE_URL``).
        """
        self._config.setdefault("pdf", {})
        self._config.setdefault("mineru", {})
        self._config["mineru"].setdefault("local", {})
        self._config["mineru"].setdefault("remote", {})
        self._config.setdefault("llm", {})
        self._config["llm"].setdefault("routers", {})
        self._config["llm"].setdefault("providers", {})

        # Override with environment variables if present
        if "PDF_PAGE_LIMIT" in os.environ:
            self._config['pdf']['page_limit'] = int(os.environ["PDF_PAGE_LIMIT"])
        if "CHUNK_MERGE_THRESHOLD" in os.environ:
            self._config['pdf']['chunk_merge_threshold'] = int(os.environ["CHUNK_MERGE_THRESHOLD"])
        if "CHUNK_MIN_THRESHOLD" in os.environ:
            self._config['pdf']['chunk_min_threshold'] = int(os.environ["CHUNK_MIN_THRESHOLD"])

        # MinerU local server settings
        if "MINERU_API_MODE" in os.environ:
            self._config['mineru']['api_mode'] = os.environ["MINERU_API_MODE"]
        if "MINERU_LANGUAGE" in os.environ:
            self._config['mineru']['language'] = os.environ["MINERU_LANGUAGE"]
        if "MINERU_LOCAL_BASE_URL" in os.environ:
            self._config['mineru']['local']['base_url'] = os.environ["MINERU_LOCAL_BASE_URL"]
        if "MINERU_LOCAL_BACKEND" in os.environ:
            self._config['mineru']['local']['backend'] = os.environ["MINERU_LOCAL_BACKEND"]
        if "MINERU_LOCAL_PARSE_METHOD" in os.environ:
            self._config['mineru']['local']['parse_method'] = os.environ["MINERU_LOCAL_PARSE_METHOD"]
        if "MINERU_LOCAL_FORMULA_ENABLE" in os.environ:
            self._config['mineru']['local']['formula_enable'] = os.environ["MINERU_LOCAL_FORMULA_ENABLE"].lower() == "true"
        if "MINERU_LOCAL_TABLE_ENABLE" in os.environ:
            self._config['mineru']['local']['table_enable'] = os.environ["MINERU_LOCAL_TABLE_ENABLE"].lower() == "true"

        # MinerU remote server settings
        if "MINERU_REMOTE_MODEL_VERSION" in os.environ:
            self._config['mineru']['remote']['model_version'] = os.environ["MINERU_REMOTE_MODEL_VERSION"]
        if "MINERU_REMOTE_IS_OCR" in os.environ:
            self._config['mineru']['remote']['is_ocr'] = os.environ["MINERU_REMOTE_IS_OCR"].lower() == "true"
        if "MINERU_REMOTE_ENABLE_FORMULA" in os.environ:
            self._config['mineru']['remote']['enable_formula'] = os.environ["MINERU_REMOTE_ENABLE_FORMULA"].lower() == "true"
        if "MINERU_REMOTE_ENABLE_TABLE" in os.environ:
            self._config['mineru']['remote']['enable_table'] = os.environ["MINERU_REMOTE_ENABLE_TABLE"].lower() == "true"
        if "MINERU_REMOTE_PAGE_RANGES" in os.environ:
            self._config['mineru']['remote']['page_ranges'] = os.environ["MINERU_REMOTE_PAGE_RANGES"]
        if "MINERU_REMOTE_NO_CACHE" in os.environ:
            self._config['mineru']['remote']['no_cache'] = os.environ["MINERU_REMOTE_NO_CACHE"].lower() == "true"
        if "MINERU_REMOTE_UPLOAD_MODE" in os.environ:
            self._config['mineru']['remote']['upload_mode'] = os.environ["MINERU_REMOTE_UPLOAD_MODE"]
            
        # LLM Stage Providers (routers)
        if "CHUNKING_PROVIDER" in os.environ:
            self._config['llm']['routers']['chunking_provider'] = os.environ["CHUNKING_PROVIDER"]
        if "PEELING_PROVIDER" in os.environ:
            self._config['llm']['routers']['peeling_provider'] = os.environ["PEELING_PROVIDER"]
        if "SKILL_ENGINE_PROVIDER" in os.environ:
            self._config['llm']['routers']['skill_engine_provider'] = os.environ["SKILL_ENGINE_PROVIDER"]
            
        # Dynamically override any field for any provider via env vars:
        # Format: {PROVIDER_NAME}_{FIELD} e.g. SILICONFLOW_BASE_URL, GOOGLE_CHUNKING_MODEL
        providers = self._config.get('llm', {}).get('providers', {})
        for provider_name in providers:
            provider_upper = provider_name.upper().replace('-', '_')
            for field in ['base_url', 'api_key_env', 'chunking_model', 'peeling_model', 'skill_engine_model']:
                env_key = f"{provider_upper}_{field.upper()}"
                if env_key in os.environ:
                    providers[provider_name][field] = os.environ[env_key]
            
    def get(self, key, default=None):
        """Retrieve a nested config value by dot-separated key.

        Args:
            key: Dot-separated path, e.g. ``"llm.routers.chunking_provider"``.
            default: Value returned if the key is missing.

        Returns:
            The config value, or *default* if not found.
        """
        keys = key.split('.')
        val = self._config
        for k in keys:
            if isinstance(val, dict) and k in val:
                val = val[k]
            else:
                return default
        return val

config = Config()