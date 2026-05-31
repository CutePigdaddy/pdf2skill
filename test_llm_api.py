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

def test_api_connection(provider: str, model: str = None, test_json_mode: bool = False):
    """
    测试单个 LLM Provider 的连通性和功能
    """
    print(f"\n{'='*60}")
    print(f"🚀 开始测试 Provider: [{provider.upper()}] ".center(60, " "))
    print(f"{'='*60}")
    
    # 临时覆盖环境变量，强行指定使用该 provider
    os.environ["LLM_PROVIDER"] = provider
    
    if model:
        os.environ["SKILL_ENGINE_MODEL"] = model
    else:
        for k in ["CHUNKING_MODEL", "PEELING_MODEL", "SKILL_ENGINE_MODEL"]:
            if k in os.environ:
                del os.environ[k]

    try:
        print("[INFO] 正在初始化 LLMClient...")
        client = LLMClient(stage="skill_engine")
        print(f"[INFO] 初始化成功 -> Provider: {client.provider}, Model: {client.model}, Base URL: {client.base_url}")
        
        if test_json_mode:
            prompt = "请返回一段合法的 JSON，必须包含一个键为 'status'，值为 'API_IS_WORKING_PROPERLY' 的数据。不要输出任何其他多余文本。"
            print(f"[INFO] 正在发送强格式 JSON 测试请求 (Max Tokens: 100)...")
        else:
            prompt = "Please reply exactly and only with this string: 'API_IS_WORKING_PROPERLY'"
            print(f"[INFO] 正在发送常规文本测试请求 (Max Tokens: 50)...")
            
        response = client.chat(prompt=prompt, is_json=test_json_mode, max_tokens=100)
        
        print("\n✅ [SUCCESS] 成功收到模型返回:")
        print("-" * 40)
        print(response)
        print("-" * 40)
        
        if test_json_mode:
            print("\n[INFO] 正在尝试使用 client.parse_json_response 解析返回值...")
            try:
                parsed_json = client.parse_json_response(response)
                print("✅ [SUCCESS] JSON 解析成功!")
                print(json.dumps(parsed_json, indent=2, ensure_ascii=False))
            except LLMParsingError as pe:
                print(f"❌ [ERROR] JSON 解析失败 LLMParsingError: {pe}")
                
    except ValueError as ve:
        print(f"\n❌ [ERROR] 配置错误 ValueError: {ve}")
        print(">> [Action] 请检查 settings.yaml 中该 provider 的配置，以及 .env 中对应的 API Key。")
        
    except Exception as e:
        print(f"\n❌ [ERROR] 发生未捕获异常: {type(e).__name__} -> {str(e)}")
        print(">> [Action] 请检查网络连通性、Base URL 是否可用，或模型名称在该平台是否存在。")
        print("\n--- 异常调用栈 (Traceback) ---")
        traceback.print_exc()
        print("------------------------------")
        
if __name__ == "__main__":
    load_dotenv()
    
    print("\n💡 [提示] 本脚本用于调试和排查 LLM API 连通性错误。")
    print("💡 [提示] 它将直接调用现有的 utils.llm_client.LLMClient。")
    
    # 显示当前配置的 provider 列表
    available_providers = list(config.get("llm.providers", {}).keys())
    print(f"\n🔍 当前 settings.yaml 中配置的 Providers: {available_providers}")
    
    # 显示相关环境变量
    print("\n🔍 当前加载的环境变量:")
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
        print("请选择要测试的 LLM Provider:")
        for i, p in enumerate(available_providers, 1):
            print(f"{i}. {p}")
        print(f"{len(available_providers) + 1}. 退出")
        
        choice = input(f"请输入选项 [1-{len(available_providers) + 1}]: ").strip()
        
        try:
            idx = int(choice)
            if idx == len(available_providers) + 1:
                print("退出测试。")
                break
            if 1 <= idx <= len(available_providers):
                provider = available_providers[idx - 1]
            else:
                print("无效选项。")
                continue
        except ValueError:
            # Allow direct provider name input
            if choice in available_providers:
                provider = choice
            else:
                print("无效选项。")
                continue
        
        default_model = config.get(f"llm.providers.{provider}.skill_engine_model", "")
        model_input = input(f"请输入要测试的模型名称 (留空则默认 {default_model}): ").strip()
        model = model_input if model_input else (default_model if default_model else None)
        
        test_mode_input = input("是否测试强格式 JSON 输出? (y/N) 默认N: ").strip().lower()
        test_json = (test_mode_input == 'y')
        
        test_api_connection(provider=provider, model=model, test_json_mode=test_json)
        
    print("\n🎉 测试结束。")