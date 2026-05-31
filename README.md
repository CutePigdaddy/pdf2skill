# pdf2skill

将 PDF 教材、文档和书籍自动转换为结构化知识索引与可复用技能库。
**GitHub Description**: PDF to skill pipeline for turning books and documents into structured knowledge chunks and reusable SKILL.md outputs.

## 功能特性

- **PDF 解析**：基于 MinerU 引擎，支持 remote API 和 local Gradio 双模式，自动保留公式、表格与图片
- **语义切分**：LLM 驱动的策略性分块，保护不可分割的逻辑单元（习题集、定理证明、代码块）
- **树状合并**：递归剥离大块内容，通过 Levenshtein 模糊匹配定位子标题，直至每块在 token 阈值内
- **技能生成**：为每个 chunk 提取关键词标签，生成 SKILL.md 主索引和独立参考文件
- **OpenAI 兼容**：所有 LLM 请求统一走 OpenAI Chat Completions 格式，支持任意 OpenAI 兼容供应商
- **供应商可自定义**：Provider 名称完全由 settings.yaml 定义，新增供应商零代码改动
- **断点续传**：基于 checkpoint 机制，中断后从已完成阶段继续执行

## 快速开始

### 1. 安装

```bash
# 创建虚拟环境
python -m venv venv

# Windows
venv\Scripts\activate

# Linux / macOS
# source venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

### 2. 必填配置

在项目根目录创建 .env 文件，填入至少一项 API Key：

| 变量名 | 说明 | 获取方式 |
|--------|------|----------|
| MINERU_API_KEY | MinerU 远程转换服务密钥（remote 模式必填） | [mineru.net](https://mineru.net/) 注册 |
| SILICONFLOW_API_KEY | SiliconFlow LLM 平台密钥 | [siliconflow.cn](https://siliconflow.cn/) 注册 |
| GOOGLE_API_KEY | Google Gemini API 密钥 | [Google AI Studio](https://aistudio.google.com/) 获取 |
| VECTORENGINE_API_KEY | VectorEngine 网关密钥 | 联系平台方获取 |

> **最小配置**：只需一个 LLM Provider 的 Key + MINERU_API_KEY（remote 模式时）。使用 local Gradio 模式时不需要 MINERU_API_KEY。

### 3. 运行

**方式 A：交互式测试（推荐）**

```bash
python run_test.py
```

程序会逐步引导你选择 Provider、确认模型、输入文件路径，然后自动执行全流程。

**方式 B：命令行直接调用**

```bash
python main.py "path/to/your_book.pdf" --output outputs
```

> 使用 main.py 时，Provider 和模型取 settings.yaml 默认值；如需覆盖请通过 .env 设置环境变量。

## 配置详解

### .env vs settings.yaml：何时改哪个

| 场景 | 改哪个 |
|------|--------|
| 存放 API 密钥 | .env |
| 临时切换 Provider 或模型 | .env |
| 覆盖某个默认值而不改仓库 | .env |
| 项目长期默认配置 | settings.yaml |
| 团队统一的基线设置 | settings.yaml |

> **原则**：密钥和临时覆盖放 .env，长期默认放 settings.yaml。.env 已在 .gitignore 中，不会被提交。

### LLM Provider 配置

所有 LLM 请求统一使用 OpenAI Chat Completions 兼容格式。Provider 在 settings.yaml 中自由定义，每个 Provider 需配置：

| 字段 | 说明 | 示例 |
|------|------|------|
| ase_url | OpenAI 兼容 API 的 base URL（不含 /chat/completions，程序自动拼接） | https://api.siliconflow.cn/v1 |
| pi_key_env | 存放 API Key 的环境变量名，程序通过 os.getenv() 读取 | SILICONFLOW_API_KEY |
| chunking_model | Chunking 阶段使用的模型 | deepseek-ai/DeepSeek-R1 |
| peeling_model | Peeling 阶段使用的模型 | deepseek-ai/DeepSeek-V3 |
| skill_engine_model | Skill Engine 阶段使用的模型 | THUDM/GLM-4-9B-0414 |

> **新增 Provider**：只需在 settings.yaml 的 providers: 下加一段配置，在 .env 中加对应 API Key，零 Python 代码改动。

#### settings.yaml 示例

```yaml
llm:
  providers:
    siliconflow:
      base_url: https://api.siliconflow.cn/v1
      api_key_env: SILICONFLOW_API_KEY
      chunking_model: deepseek-ai/DeepSeek-R1
      peeling_model: deepseek-ai/DeepSeek-V3
      skill_engine_model: THUDM/GLM-4-9B-0414
    google:
      base_url: https://generativelanguage.googleapis.com/v1beta/openai
      api_key_env: GOOGLE_API_KEY
      chunking_model: gemini-3-flash-preview
      peeling_model: gemini-3.1-flash-lite-preview
      skill_engine_model: gemini-3.1-flash-lite-preview
    openrouter:
      base_url: https://openrouter.ai/api/v1
      api_key_env: OPENROUTER_API_KEY
      chunking_model: deepseek/deepseek-r1
      peeling_model: deepseek/deepseek-chat
      skill_engine_model: google/gemini-2.0-flash-001
    local-vllm:
      base_url: http://localhost:8000/v1
      api_key_env: LOCAL_API_KEY
      chunking_model: Qwen2.5-72B-Instruct
      peeling_model: Qwen2.5-32B-Instruct
      skill_engine_model: Qwen2.5-7B-Instruct
    custom:
      base_url: https://my-llm-proxy.example.com/v1
      api_key_env: CUSTOM_API_KEY
      chunking_model: my-model-a
      peeling_model: my-model-b
      skill_engine_model: my-model-c
  routers:
    chunking_provider: siliconflow
    peeling_provider: siliconflow
    skill_engine_provider: siliconflow
```

#### Provider 路由

三个处理阶段可独立选择不同 Provider：

| 阶段 | 环境变量 | 作用 |
|------|----------|------|
| 分块（Chunking） | CHUNKING_PROVIDER | 控制 Stage 2 使用哪个 Provider |
| 剥离（Peeling） | PEELING_PROVIDER | 控制 Stage 3 使用哪个 Provider |
| 技能生成（Skill Engine） | SKILL_ENGINE_PROVIDER | 控制 Stage 4 使用哪个 Provider |

#### 环境变量覆盖规则

Provider 的所有字段均支持环境变量覆盖，格式为 {PROVIDER}_{FIELD}：

```bash
# 覆盖 siliconflow 的 base_url
SILICONFLOW_BASE_URL="https://custom-proxy.example.com/v1"

# 覆盖 google 的 chunking 模型
GOOGLE_CHUNKING_MODEL="gemini-2.0-flash"

# 覆盖 openrouter 的 API Key 环境变量名
OPENROUTER_API_KEY_ENV="MY_CUSTOM_KEY_NAME"
```

**模型覆盖优先级**（从高到低）：
1. {STAGE}_MODEL（如 CHUNKING_MODEL）— 全局覆盖，所有 Provider 生效
2. settings.yaml 中 llm.providers.{provider}.{stage}_model
3. 缺失时抛出明确错误，引导用户补全配置

### MinerU 配置

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| MINERU_API_MODE | emote（官方 API）或 local（本地 Gradio 服务） | emote |
| MINERU_LANGUAGE | 语言：ch（中文）、en（英文）、east_slavic（俄语等） | ch |
| MINERU_LOCAL_BASE_URL | 本地 Gradio 服务地址（local 模式必填） | http://localhost:7860 |
| MINERU_LOCAL_BACKEND | 本地解析后端 | lm-auto-engine |
| MINERU_LOCAL_PARSE_METHOD | 解析方法 | uto |
| MINERU_LOCAL_FORMULA_ENABLE | 启用公式识别 | 	rue |
| MINERU_LOCAL_TABLE_ENABLE | 启用表格识别 | 	rue |

> **Local 模式**需要先启动 MinerU Gradio 服务（默认端口 7860），项目会通过 gradio_client 自动连接。

### PDF 处理参数

| 变量名 | 说明 | 默认值 | 调优建议 |
|--------|------|--------|----------|
| PDF_PAGE_LIMIT | 单个 PDF 最大页数，超出会物理切片 | 200 | 大文件可调高，但需注意 API 限制 |
| CHUNK_MERGE_THRESHOLD | 合并分片时的字符阈值 | 5000 | 值越大 chunk 越大，减少 LLM 调用但增加单次 token |
| CHUNK_MIN_THRESHOLD | 触发合并的最小字符长度 | 1000 | 低于此值的相邻片段会合并，避免过度碎片化 |

### 请求控制

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| REQUEST_INTERVAL | API 请求间隔（秒），避免触发限流 | 1.0 |

## 配置示例

**1. 纯 SiliconFlow（最简配置）**

```bash
# .env
MINERU_API_KEY="your_mineru_key"
SILICONFLOW_API_KEY="your_siliconflow_key"
CHUNKING_PROVIDER="siliconflow"
PEELING_PROVIDER="siliconflow"
SKILL_ENGINE_PROVIDER="siliconflow"
```

**2. 混合配置（分块用 SiliconFlow，其余用 Google）**

```bash
# .env
MINERU_API_KEY="your_mineru_key"
SILICONFLOW_API_KEY="your_siliconflow_key"
GOOGLE_API_KEY="your_google_key"
CHUNKING_PROVIDER="siliconflow"
PEELING_PROVIDER="google"
SKILL_ENGINE_PROVIDER="google"
```

**3. 自定义 Provider（如 OpenRouter）**

```yaml
# settings.yaml — 添加 provider
llm:
  providers:
    openrouter:
      base_url: https://openrouter.ai/api/v1
      api_key_env: OPENROUTER_API_KEY
      chunking_model: deepseek/deepseek-r1
      peeling_model: deepseek/deepseek-chat
      skill_engine_model: google/gemini-2.0-flash-001
```

```bash
# .env — 添加 key
OPENROUTER_API_KEY="your_openrouter_key"
CHUNKING_PROVIDER="openrouter"
PEELING_PROVIDER="openrouter"
SKILL_ENGINE_PROVIDER="openrouter"
```

**4. 本地 vLLM 服务**

```yaml
# settings.yaml — 添加 provider
llm:
  providers:
    local-vllm:
      base_url: http://localhost:8000/v1
      api_key_env: LOCAL_API_KEY
      chunking_model: Qwen2.5-72B-Instruct
      peeling_model: Qwen2.5-32B-Instruct
      skill_engine_model: Qwen2.5-7B-Instruct
```

```bash
# .env
LOCAL_API_KEY="any-placeholder"  # vLLM 通常不需要 key，但字段不能为空
CHUNKING_PROVIDER="local-vllm"
PEELING_PROVIDER="local-vllm"
SKILL_ENGINE_PROVIDER="local-vllm"
```

## 输出结构

```
outputs/
└── book_name/
    ├── .checkpoint.json        # 断点文件（支持续传）
    ├── full_chunks_original/   # Stage 2 原始分块
    ├── full_chunks/            # Stage 3 剥离后分块
    │   ├── chunks/
    │   ├── chunks_index.json
    │   └── tree.json
    └── generated_skills/       # Stage 4 生成的技能文件
        ├── SKILL.md            # 主索引
        └── references/         # 各 chunk 参考文件
```

## 项目结构

```
main.py              # 命令行入口
run_test.py          # 交互式测试入口
config/              # settings.yaml + config.py（单例，双层配置合并）
core/                # pdf_processor / llm_chunker / tree_merger / skill_engine
utils/               # logger / llm_client / retry_client / checkpoint
prompts/             # 各阶段 LLM 提示词模板
```

## 许可证

本项目采用 [MIT 许可证](LICENSE)。