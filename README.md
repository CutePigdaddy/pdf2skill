# pdf2skill

将 PDF 教材、文档和书籍自动转换为结构化知识索引与可复用技能库。

[![Python Version](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![MinerU](https://img.shields.io/badge/Engine-MinerU-orange.svg)](https://mineru.net/)

**pdf2skill** 会把 PDF 解析、切片、合并并生成 `SKILL.md`，适合把教材、技术文档、学术书籍整理成适合大模型检索和复用的知识库。

**GitHub 仓库简介**：PDF to skill pipeline for turning books and documents into structured knowledge chunks and reusable `SKILL.md` outputs.

## 核心功能

- **文档处理**：支持 PDF 转 Markdown，并保留后续分块所需的结构信息。
- **语义切片**：
  - 保护不可分割的逻辑单元（如练习题、代码块、定理）。
  - 自动合并小片段，确保每个 Chunk 具备足够的语义信息。
  - 在章节标题处精准切分，避免内容粘连。
- **智能命名**：根据内容自动生成复合标题，提升检索精度。
- **多模型支持**：支持 Google Gemini、SiliconFlow 等模型供应商。
- **断点续传**：通过 checkpoint 机制支持从中断步骤继续执行。

## 工作流程

1. **输入 PDF**：支持单文件或批量处理。
2. **预处理**：根据页面阈值进行物理切分（可选）。
3. **转换与切片**：
   - 使用 MinerU 将 PDF 转换为 Markdown。
   - 通过 LLM 进行策略性分块和语义切片。
4. **树合并与技能生成**：生成层级化的 chunk 结构和最终 `SKILL.md`。

## 快速开始

### 环境准备

```bash
# 创建虚拟环境
python -m venv venv

# Windows
venv\Scripts\activate

# 安装依赖
python -m pip install -r requirements.txt

# Linux/macOS
# source venv/bin/activate
# python -m pip install -r requirements.txt
```

### 配置鉴权

在项目根目录创建 `.env` 文件。程序启动时会自动读取它，然后覆盖 `settings.yaml` 里的部分默认值。

```bash
# MinerU API 密钥
MINERU_API_KEY="your_mineru_api_key"

# LLM 服务密钥（按你实际使用的供应商填写）
SILICONFLOW_API_KEY="your_siliconflow_key"
GOOGLE_API_KEY="your_google_key"
VECTORENGINE_API_KEY="your_vectorengine_key"

# 可选：VectorEngine 网关地址
VECTORENGINE_BASE_URL="https://api.vectorengine.ai"

# 可选：覆盖配置文件中的默认值
CHUNKING_PROVIDER="siliconflow"
PEELING_PROVIDER="siliconflow"
SKILL_ENGINE_PROVIDER="siliconflow"
PDF_PAGE_LIMIT="200"
CHUNK_MERGE_THRESHOLD="5000"
CHUNK_MIN_THRESHOLD="1000"

# 可选：覆盖具体模型名
SILICONFLOW_CHUNKING_MODEL="deepseek-ai/DeepSeek-R1"
SILICONFLOW_PEELING_MODEL="deepseek-ai/DeepSeek-V3"
SILICONFLOW_SKILL_ENGINE_MODEL="THUDM/GLM-4-9B-0414"
```

说明：

- `MINERU_API_KEY`：远程 MinerU 转换必填。
- `SILICONFLOW_API_KEY` / `GOOGLE_API_KEY` / `VECTORENGINE_API_KEY`：LLM 平台密钥，按你在 `settings.yaml` 里选择的 provider 准备。
- `VECTORENGINE_BASE_URL`：当你通过 VectorEngine 网关调用时使用，默认值见 `settings.yaml`。
- `CHUNKING_PROVIDER`、`PEELING_PROVIDER`、`SKILL_ENGINE_PROVIDER`：分别控制分块、剥皮/树合并、技能生成使用哪个供应商。
- `PDF_PAGE_LIMIT`、`CHUNK_MERGE_THRESHOLD`、`CHUNK_MIN_THRESHOLD`：覆盖 `settings.yaml` 中的 PDF 阈值配置。
- `*_CHUNKING_MODEL`、`*_PEELING_MODEL`、`*_SKILL_ENGINE_MODEL`：覆盖具体模型名，适合你想临时切换模型而不改 YAML。

### 运行工具

```bash
python main.py "path/to/your_book.pdf" --output "outputs"
```

默认输出目录为 `outputs/`。每本书会生成独立子目录，包含中间切片、合并后的结果和最终技能文件。

## 配置

配置文件路径：`config/settings.yaml`

推荐的配置方式是：`settings.yaml` 放默认值，`.env` 放密钥和临时覆盖项。

### `settings.yaml` 里能改什么

| 配置项 | 说明 | 默认值 |
| :--- | :--- | :--- |
| `mineru.api_mode` | MinerU 模式，`remote` 或 `local` | `remote` |
| `mineru.language` | 传给 MinerU 的语言参数 | `ch` |
| `mineru.local.base_url` | 本地 MinerU 服务地址 | `http://localhost:7860` |
| `mineru.local.backend` | 本地 MinerU 后端名 | `vlm-auto-engine` |
| `llm.routers.chunking_provider` | 分块阶段使用的供应商 | `siliconflow` |
| `llm.routers.peeling_provider` | Tree merge / peeling 阶段使用的供应商 | `siliconflow` |
| `llm.routers.skill_engine_provider` | SKILL 生成阶段使用的供应商 | `siliconflow` |
| `llm.providers.google.*` | Google 模型名 | 见 `settings.yaml` |
| `llm.providers.siliconflow.*` | SiliconFlow 模型名 | 见 `settings.yaml` |
| `llm.providers.vectorengine.*` | VectorEngine 模型名和 base url | 见 `settings.yaml` |
| `llm.max_concurrency` | 并发请求数 | `5` |
| `llm.max_retries` | 失败重试次数 | `3` |
| `llm.timeout` | 单次请求超时时间（秒） | `300` |
| `pdf.chunk_merge_threshold` | 合并分片时的阈值 | `5000` |
| `pdf.chunk_min_threshold` | 触发合并的最小字符长度 | `1000` |
| `pdf.page_limit` | 单个 PDF 最多处理页数，超出会切分 | `200` |

### 什么时候改 `.env`，什么时候改 `settings.yaml`

- 改 `.env`：密钥、临时换模型、临时切换 provider、想不改仓库默认值的时候。
- 改 `settings.yaml`：项目的长期默认配置，比如你团队固定用哪个供应商、默认语言、PDF 页数阈值。
- 不要把真实密钥提交到 GitHub。

### 统一按 OpenAI compatible 网关配置

如果你希望把三段 LLM 配置尽量统一，可以直接把 VectorEngine 当成一个 OpenAI compatible 网关来用。代码里会优先按这个思路调用：

- 模型名不包含 `claude` 时，默认走 `POST /v1/chat/completions`
- 模型名包含 `gemini` 时，走 Gemini 兼容接口
- 模型名包含 `claude` 时，走 Anthropic 兼容接口

如果你只想统一成一套最简单的配置，通常只需要下面这些项：

- `.env` 里的 `VECTORENGINE_API_KEY`
- `.env` 里的 `VECTORENGINE_BASE_URL`（可选）
- `settings.yaml` 里的 `llm.providers.vectorengine.base_url`
- `settings.yaml` 里的 `llm.providers.vectorengine.chunking_model`、`peeling_model`、`skill_engine_model`
- `.env` 里的 `CHUNKING_PROVIDER`、`PEELING_PROVIDER`、`SKILL_ENGINE_PROVIDER`（如果你要让某个阶段走 VectorEngine）

推荐写法：

```bash
VECTORENGINE_API_KEY="your_vectorengine_key"
VECTORENGINE_BASE_URL="https://api.vectorengine.ai"
CHUNKING_PROVIDER="vectorengine"
PEELING_PROVIDER="vectorengine"
SKILL_ENGINE_PROVIDER="vectorengine"
VECTORENGINE_CHUNKING_MODEL="gpt-5.4"
VECTORENGINE_PEELING_MODEL="gpt-5.4-mini"
VECTORENGINE_SKILL_ENGINE_MODEL="gpt-5.4-nano"
```

如果你更偏向把默认值写进仓库配置，也可以直接改 `settings.yaml`：

```yaml
llm:
  routers:
    chunking_provider: vectorengine
    peeling_provider: vectorengine
    skill_engine_provider: vectorengine
  providers:
    vectorengine:
      base_url: https://api.vectorengine.ai
      chunking_model: gpt-5.4
      peeling_model: gpt-5.4-mini
      skill_engine_model: gpt-5.4-nano
```

### 常见配置示例

**1. 只用 SiliconFlow**

```bash
MINERU_API_KEY="your_mineru_api_key"
SILICONFLOW_API_KEY="your_siliconflow_key"
CHUNKING_PROVIDER="siliconflow"
PEELING_PROVIDER="siliconflow"
SKILL_ENGINE_PROVIDER="siliconflow"
```

**2. 只改 PDF 切分阈值**

```bash
PDF_PAGE_LIMIT="120"
CHUNK_MERGE_THRESHOLD="4000"
CHUNK_MIN_THRESHOLD="800"
```

**3. 直接改 `settings.yaml` 的模型**

```yaml
llm:
  routers:
    chunking_provider: google
    peeling_provider: google
    skill_engine_provider: google
  providers:
    google:
      chunking_model: gemini-3-flash-preview
      peeling_model: gemini-3.1-flash-lite-preview
      skill_engine_model: gemini-3.1-flash-lite-preview
```

## 输出结构

```text
outputs/
└── [book_name]/
  ├── checkpoint.json
  ├── full_chunks_original/
  │   └── chunks/
  ├── full_chunks/
  │   └── chunks/
  └── generated_skills/
    └── SKILL.md
```

## 项目结构

```text
main.py                 # 命令行入口
config/                 # 配置加载与默认设置
core/                   # PDF 处理、分块、树合并、技能生成
utils/                  # 日志、重试与 checkpoint 工具
```

## 许可证

本项目采用 [MIT 许可证](LICENSE)。
