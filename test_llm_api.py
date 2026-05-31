import os
import sys
import json
import traceback
from pathlib import Path
from dotenv import load_dotenv

sys.path.append(str(Path(__file__).parent))

from utils.llm_client import LLMClient
from utils.logger import LLMParsingError
from config.config import config

def check_api_connection(provider: str, model: str = None, test_json_mode: bool = False):
    """
    Test connectivity and functionality of a single LLM Provider
    """
    print(f"\n{'='*60}")
    print(f"Testing Provider: [{provider.upper()}] ".center(60, " "))
    print(f"{'='*60}")
    
    os.environ["LLM_PROVIDER"] = provider
    
    if model:
        os.environ["SKILL_ENGINE_MODEL"] = model
    else:
        for k in ["CHUNKING_MODEL", "PEELING_MODEL", "SKILL_ENGINE_MODEL"]:
            if k in os.environ:
                del os.environ[k]

    try:
        print("[INFO] Initializing LLMClient...")
        client = LLMClient(stage="skill_engine")
        print(f"[INFO] Init OK -> Provider: {client.provider}, Model: {client.model}, Base URL: {client.base_url}")
        
        if test_json_mode:
            prompt = "Return valid JSON with key 'status' and value 'API_IS_WORKING_PROPERLY'. No other text."
            print(f"[INFO] Sending JSON test request (Max Tokens: 100)...")
        else:
            prompt = "Please reply exactly and only with this string: 'API_IS_WORKING_PROPERLY'"
            print(f"[INFO] Sending text test request (Max Tokens: 50)...")
            
        response = client.chat(prompt=prompt, is_json=test_json_mode, max_tokens=100)
        
        print("\n SUCCESS - Response received:")
        print("-" * 40)
        print(response)
        print("-" * 40)
        
        if test_json_mode:
            print("\n[INFO] Attempting JSON parse...")
            try:
                parsed_json = client.parse_json_response(response)
                print(" SUCCESS - JSON parse OK!")
                print(json.dumps(parsed_json, indent=2, ensure_ascii=False))
            except LLMParsingError as pe:
                print(f" ERROR - JSON parse failed: {pe}")
                
    except ValueError as ve:
        print(f"\n ERROR - Config error: {ve}")
        print(">> Check settings.yaml and .env for this provider.")
        
    except Exception as e:
        print(f"\n ERROR - {type(e).__name__}: {str(e)}")
        print(">> Check network, base URL, and model name.")
        print("\n--- Traceback ---")
        traceback.print_exc()
        print("------------------")
        
if __name__ == "__main__":
    load_dotenv()
    
    print("\nLLM API connectivity test script.")
    
    available_providers = list(config.get("llm.providers", {}).keys())
    print(f"\nConfigured providers in settings.yaml: {available_providers}")
    
    print("\nCurrent environment variables:")
    for p in available_providers:
        api_key_env = config.get(f"llm.providers.{p}.api_key_env", f"{p.upper()}_API_KEY")
        val = os.getenv(api_key_env)
        if val and len(val) > 10:
            masked = f"{val[:5]}...{val[-4:]}"
        else:
            masked = str(val)
        print(f"  - {api_key_env} ({p}): {masked}")

    while True:
        print("\n" + "-"*40)
        print("Select LLM Provider to test:")
        for i, p in enumerate(available_providers, 1):
            print(f"{i}. {p}")
        print(f"{len(available_providers) + 1}. Exit")
        
        choice = input(f"Enter choice [1-{len(available_providers) + 1}]: ").strip()
        
        try:
            idx = int(choice)
            if idx == len(available_providers) + 1:
                print("Exiting.")
                break
            if 1 <= idx <= len(available_providers):
                provider = available_providers[idx - 1]
            else:
                print("Invalid choice.")
                continue
        except ValueError:
            if choice in available_providers:
                provider = choice
            else:
                print("Invalid choice.")
                continue
        
        default_model = config.get(f"llm.providers.{provider}.skill_engine_model", "")
        model_input = input(f"Enter model name (default: {default_model}): ").strip()
        model = model_input if model_input else (default_model if default_model else None)
        
        test_mode_input = input("Test JSON output? (y/N): ").strip().lower()
        test_json = (test_mode_input == 'y')
        
        check_api_connection(provider=provider, model=model, test_json_mode=test_json)
        
    print("\nTest complete.")