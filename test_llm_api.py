import os
import sys
import json
import traceback
from pathlib import Path
from dotenv import load_dotenv

# 将项目根目录加入到sys.path中，以便能够引入 config 和 utils 模块
sys.path.append(str(Path(__file__).parent))

from utils.llm_client import LLMClient
from utils.logger import LLMParsingError

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
        # 强行指定模型
        os.environ["SKILL_ENGINE_MODEL"] = model
    else:
        # 重置掉特定模型绑定的环境变量，确保使用 config 中的基础/测试值
        for k in ["CHUNKING_MODEL", "PEELING_MODEL", "SKILL_ENGINE_MODEL"]:
            if k in os.environ:
                del os.environ[k]

    try:
        # 使用 skill_engine 作为测试 stage，这通常是我们最常用的聊天入口
        print("[INFO] 正在初始化 LLMClient...")
        client = LLMClient(stage="skill_engine")
        print(f"[INFO] 初始化成功 -> Model: {client.model}")
        
        # 设计测试 Prompt
        if test_json_mode:
            prompt = "请返回一段合法的 JSON，必须包含一个键为 'status'，值为 'API_IS_WORKING_PROPERLY' 的数据。不要输出任何其他多余文本。"
            print(f"[INFO] 正在发送强格式 JSON 测试请求 (Max Tokens: 100)...")
        else:
            prompt = "Please reply exactly and only with this string: 'API_IS_WORKING_PROPERLY'"
            print(f"[INFO] 正在发送常规文本测试请求 (Max Tokens: 50)...")
            
        # 发送请求
        response = client.chat(prompt=prompt, is_json=test_json_mode, max_tokens=100)
        
        print("\n✅ [SUCCESS] 成功收到模型返回:")
        print("-" * 40)
        print(response)
        print("-" * 40)
        
        # 如果是 JSON 模式，进行解析测试
        if test_json_mode:
            print("\n[INFO] 正在尝试使用 client.parse_json_response 解析返回值...")
            try:
                parsed_json = client.parse_json_response(response)
                print("✅ [SUCCESS] JSON 解析成功!")
                print(json.dumps(parsed_json, indent=2, ensure_ascii=False))
            except LLMParsingError as pe:
                print(f"❌ [ERROR] JSON 解析失败 LLMParsingError: {pe}")
                print(">> [Action] 模型可能没有严格输出 JSON，或者输出了带有 markdown 语法的代码块被截断出错。")
                
    except ValueError as ve:
        print(f"\n❌ [ERROR] 环境变量或配置错误 ValueError: {ve}")
        print(">> [Action] 请检查 .env 文件中对应的 API_KEY (如 SILICONFLOW_API_KEY / VECTORENGINE_API_KEY) 是否配置正确。")
        
    except Exception as e:
        print(f"\n❌ [ERROR] 发生未捕获异常: {type(e).__name__} -> {str(e)}")
        print(">> [Action] 请检查网络连通性、Base URL 是否可用，或模型名称在该平台是否存在。")
        print("\n--- 异常调用栈 (Traceback) ---")
        traceback.print_exc()
        print("------------------------------")
        
if __name__ == "__main__":
    # 加载本地 .env配置
    load_dotenv()
    
if __name__ == "__main__":
    # 加载本地 .env配置
    load_dotenv()
    
    print("\n💡 [提示] 本脚本用于调试和排查 LLM API 连通性错误。")
    print("💡 [提示] 它将直接调用现有的 utils.llm_client.LLMClient。")
    
    # 获取需要测试的环境变量并展示，有助于 agent 或者您人工诊断错误
    print("\n🔍 当前加载的关键环境变量:")
    for key in ["VECTORENGINE_API_KEY", "VECTORENGINE_BASE_URL", "SILICONFLOW_API_KEY", "SILICONFLOW_BASE_URL", "GOOGLE_API_KEY", "GOOGLE_BASE_URL"]:
        val = os.getenv(key)
        # 脱敏打印 key
        masked_val = f"{val[:5]}...{val[-4:]}" if val and len(val) > 10 else str(val)
        print(f"  - {key}: {masked_val}")

    while True:
        print("\n" + "-"*40)
        print("请选择要测试的 LLM Provider 格式:")
        print("1. VectorEngine (统一使用 OpenAI /chat/completions 接口)")
        print("2. SiliconFlow")
        print("3. 退出")
        choice = input("请输入选项 [1-3]: ").strip()
        
        if choice == "3":
            print("退出测试。")
            break
            
        provider_map = {
            "1": "vectorengine", 
            "2": "siliconflow",
        }
        provider = provider_map.get(choice, "vectorengine")
        
        # 帮用户预填对应的模型名称格式作为指引
        default_model = "claude-sonnet-4-6" # 作为VectorEngine测试的示例
            
        model_input = input(f"请输入要测试的模型名称 (留空则默认 {default_model if default_model else '使用 config 中配置'}): ").strip()
        model = model_input if model_input else (default_model if default_model else None)
        
        test_mode_input = input("是否测试强格式 JSON 输出? (y/N) 默认N: ").strip().lower()
        test_json = (test_mode_input == 'y')
        
        test_api_connection(provider=provider, model=model, test_json_mode=test_json)
        
    print("\n🎉 测试结束。")