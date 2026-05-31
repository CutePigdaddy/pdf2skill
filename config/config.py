import os
import yaml
from pathlib import Path
from dotenv import load_dotenv

class Config:
    _instance = None
    
    def __new__(cls, config_path=None):
        if cls._instance is None:
            cls._instance = super(Config, cls).__new__(cls)
            cls._instance._init_config(config_path)
        return cls._instance
        
    def _init_config(self, config_path):
        load_dotenv()
        if config_path is None:
            config_path = Path(__file__).parent / "settings.yaml"
            
        with open(config_path, 'r', encoding='utf-8') as f:
            self._config = yaml.safe_load(f)
            
        self.merge_env_vars()
        
    def merge_env_vars(self):
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
        keys = key.split('.')
        val = self._config
        for k in keys:
            if isinstance(val, dict) and k in val:
                val = val[k]
            else:
                return default
        return val

config = Config()