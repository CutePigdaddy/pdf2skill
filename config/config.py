import os
import yaml
from pathlib import Path

class Config:
    _instance = None
    
    def __new__(cls, config_path=None):
        if cls._instance is None:
            cls._instance = super(Config, cls).__new__(cls)
            cls._instance._init_config(config_path)
        return cls._instance
        
    def _init_config(self, config_path):
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
            
        # LLM Stage Providers
        if "CHUNKING_PROVIDER" in os.environ:
            self._config['llm']['routers']['chunking_provider'] = os.environ["CHUNKING_PROVIDER"]
        if "PEELING_PROVIDER" in os.environ:
            self._config['llm']['routers']['peeling_provider'] = os.environ["PEELING_PROVIDER"]
        if "SKILL_ENGINE_PROVIDER" in os.environ:
            self._config['llm']['routers']['skill_engine_provider'] = os.environ["SKILL_ENGINE_PROVIDER"]
            
        # Specific model overrides
        for provider in ['siliconflow', 'google', 'openai', 'anthropic']:
            chunk_env = f"{provider.upper()}_CHUNKING_MODEL"
            if chunk_env in os.environ:
                self._config['llm']['providers'][provider]['chunking_model'] = os.environ[chunk_env]
                
            peel_env = f"{provider.upper()}_PEELING_MODEL"
            if peel_env in os.environ:
                self._config['llm']['providers'][provider]['peeling_model'] = os.environ[peel_env]
                
            skill_env = f"{provider.upper()}_SKILL_ENGINE_MODEL"
            if skill_env in os.environ:
                self._config['llm']['providers'][provider]['skill_engine_model'] = os.environ[skill_env]
            
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
