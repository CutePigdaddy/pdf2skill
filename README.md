# pdf2skill

将 PDF 教材、文档和书籍自动转换为结构化知识索引与可复用技能库。

**GitHub Description**: PDF to skill pipeline for turning books and documents into structured knowledge chunks and reusable `SKILL.md` outputs.

## 功能特性

- **PDF 解析**：基于 MinerU 引擎，支持 remote API 和 local Gradio 双模式，自动保留公式、表格与图片
- **语义切片**：LLM 驱动的策略性分块，保护不可分割的逻辑单元（习题集、定理证明、代码块）
- **树状合并**：递归剥离大块内容，通过 Levenshtein 模糊匹配定位子标题，直至每块在 token 阈值内
- **技能生成**：为每个 chunk 提取关键词标签，生成 `SKILL.md` 主索引和独立参考文件
- **多模型支持**：三阶段（Chunking / Peeling / Skill Engine）可独立选择 SiliconFlow、Google Gemini 或 VectorEngine
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

在项目根目录创建 `.env` 文件，填入以下至少一项 API Key：

| 变量名 | 说明 | 获取方式 |
|--------|------|----------|
| `MINERU_API_KEY` | MinerU 远程转换服务密钥（remote 模式必填） | [mineru.net](https://mineru.net/) 注册 |
| `SILICONFLOW_API_KEY` | SiliconFlow LLM 平台密钥 | [siliconflow.cn](https://siliconflow.cn/) 注册 |
| `GOOGLE_API_KEY` | Google Gemini API 密钥 | [Google AI Studio](https://aistudio.google.com/) 获取 |
| `VECTORENGINE_API_KEY` | VectorEngine 网关密钥 | 联系平台方获取 |

> **最少配置**：只需一个 LLM Provider 的 Key + `MINERU_API_KEY`（remote 模式时）。使用 local Gradio 模式时不需要 `MINERU_API_KEY`。

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

> 使用 `main.py` 时，Provider 和模型读取 `settings.yaml` 默认值；如需覆盖请通过 `.env` 设置环境变量。

## 配置详解

### .env vs settings.yaml：何时改哪个

| 场景 | 改哪个 |
|------|--------|
| 存放 API 密钥 | `.env` |
| 临时切换 Provider 或模型 | `.env` |
| 覆盖某个默认值而不改仓库 | `.env` |
| 项目长期默认配置 | `settings.yaml` |
| 团队统一的基线设置 | `settings.yaml` |

> **原则**：密钥和临时覆盖放 `.env`，长期默认放 `settings.yaml`。`.env` 已在 `.gitignore` 中，不会被提交。

### LLM Provider 配置

本项目的三个阶段可以独立选择不同的 LLM Provider：

| 阶段 | 环境变量 | 作用 |
|------|----------|------|
| 分块（Chunking） | `CHUNKING_PROVIDER` | 控制 Stage 2 使用哪个 Provider |
| 剥离（Peeling） | `PEELING_PROVIDER` | 控制 Stage 3 使用哪个 Provider |
| 技能生成（Skill Engine） | `SKILL_ENGINE_PROVIDER` | 控制 Stage 4 使用哪个 Provider |

每个 Provider 需要的 Key：

| Provider | 必填 Key | 可选 Base URL |
|----------|----------|----------------|
| `siliconflow` | `SILICONFLOW_API_KEY` | `SILICONFLOW_BASE_URL`（默认 `https://api.siliconflow.cn/v1`） |
| `google` | `GOOGLE_API_KEY` | `GOOGLE_BASE_URL`（默认 Google 官方） |
| `vectorengine` | `VECTORENGINE_API_KEY` | `VECTORENGINE_BASE_URL`（默认 `https://api.vectorengine.ai`） |

> **注意**：`settings.yaml` 中 `routers` 默认值为 `siliconflow`，而 `run_test.py` 交互菜单选项 1 默认为 `vectorengine`。使用 `run_test.py` 时会通过环境变量覆盖，二者不冲突；使用 `main.py` 直接调用时走 `settings.yaml` 默认值。

### MinerU 配置

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `MINERU_API_KEY` | 远程 API 密钥（remote 模式必填） | 无 |
| `MINERU_API_MODE` | `remote`（官方 API）或 `local`（本地 Gradio 服务） | `remote` |
| `MINERU_LANGUAGE` | 语言：`ch`（中文）、`en`（英文）、`east_slavic`（俄语等） | `ch` |
| `MINERU_LOCAL_BASE_URL` | 本地 Gradio 服务地址（local 模式必填） | `http://localhost:7860` |
| `MINERU_LOCAL_BACKEND` | 本地解析后端 | `vlm-auto-engine` |
| `MINERU_LOCAL_PARSE_METHOD` | 解析方法 | `auto` |
| `MINERU_LOCAL_FORMULA_ENABLE` | 启用公式识别 | `true` |
| `MINERU_LOCAL_TABLE_ENABLE` | 启用表格识别 | `true` |

> **Local 模式**需要先启动 MinerU Gradio 服务（默认端口 7860），项目会通过 `gradio_client` 自动连接。

### PDF 处理参数

| 变量名 | 说明 | 默认值 | 调优建议 |
|--------|------|--------|----------|
| `PDF_PAGE_LIMIT` | 单个 PDF 最大页数，超出会物理切分 | `200` | 大文件可调高，但需注意 API 限制 |
| `CHUNK_MERGE_THRESHOLD` | 合并分片时的字符阈值 | `5000` | 值越大 chunk 越大，减少 LLM 调用但增加单次 token |
| `CHUNK_MIN_THRESHOLD` | 触发合并的最小字符长度 | `1000` | 低于此值的相邻片段会合并，避免过度碎片化 |

### 请求控制

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `REQUEST_INTERVAL` | API 请求间隔（秒），避免触发限流 | `1.0` |

### 高级：模型覆盖

如需覆盖具体模型名而不改 `settings.yaml`，使用以下环境变量命名规则：

```
{STAGE}_MODEL                          # 全局覆盖（所有 provider 生效）
{PROVIDER}_{STAGE}_MODEL               # 指定 provider+阶段覆盖
```

例如：

```bash
# 全局：无论哪个 provider，分块都用这个模型
CHUNKING_MODEL="deepseek-ai/DeepSeek-V3"

# 指定：仅 Google 的分块阶段使用此模型
GOOGLE_CHUNKING_MODEL="gemini-2.0-flash"
```

**优先级**（从高到低）：

1. `{STAGE}_MODEL`（如 `CHUNKING_MODEL`）
2. `{PROVIDER}_{STAGE}_MODEL`（如 `GOOGLE_CHUNKING_MODEL`）
3. `settings.yaml` 中 `llm.providers.{provider}.{stage}_model`
4. 代码内置 `fallback_map`（仅在 yaml 项缺失时触发）

> `fallback_map` 中的部分模型名可能落后于 `settings.yaml`，正常情况下 yaml 优先加载，fallback 不会触发。

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

**2. 纯 VectorEngine（统一网关）**

```bash
# .env
VECTORENGINE_API_KEY="your_vectorengine_key"
VECTORENGINE_BASE_URL="https://api.vectorengine.ai"
CHUNKING_PROVIDER="vectorengine"
PEELING_PROVIDER="vectorengine"
SKILL_ENGINE_PROVIDER="vectorengine"
VECTORENGINE_CHUNKING_MODEL="gpt-5.4"
VECTORENGINE_PEELING_MODEL="gpt-5.4-mini"
VECTORENGINE_SKILL_ENGINE_MODEL="gpt-5.4-nano"
```

**3. 混合配置（分块用 SiliconFlow，其余用 Google）**

```bash
# .env
MINERU_API_KEY="your_mineru_key"
SILICONFLOW_API_KEY="your_siliconflow_key"
GOOGLE_API_KEY="your_google_key"
CHUNKING_PROVIDER="siliconflow"
PEELING_PROVIDER="google"
SKILL_ENGINE_PROVIDER="google"
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
        └── references/        # 各 chunk 参考文件
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