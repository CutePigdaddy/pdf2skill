# pdf2skill

将 PDF 教材、文档和书籍自动转换为结构化知识索引与可复用技能库。

## 功能特性

- **PDF 解析**：基于 MinerU 引擎，支持 remote API 和 local Gradio 双模式，保留公式、表格与图片
- **语义切分**：LLM 驱动的策略性分块，保护不可分割的逻辑单元（习题集、定理证明、代码块）
- **树状合并**：递归剥离大块内容，Levenshtein 模糊匹配定位子标题，直至每块在 token 阈值内
- **技能生成**：为每个 chunk 提取关键词标签，生成 SKILL.md 主索引和独立参考文件
- **OpenAI 兼容**：所有 LLM 请求走 OpenAI Chat Completions 格式，支持任意兼容供应商
- **断点续传**：checkpoint 机制支持中断后续传、`--from-stage` 选择性重跑、`--restart` 全部重来
- **纯前端模式**：TypeScript SDK（`@pdf2skill/sdk`）在浏览器内完成全部流水线（OPFS 存储、断点续传、zip 导出），后端只做 LLM / MinerU 代理
- **Web 界面**：Vite + TS 演示界面，配置/运行/输出一站式操作（旧版 FastAPI 全栈界面已弃用，见 `frontend/server.py`）

## Pipeline 概览

```
PDF ──► MinerU ──► Chunking ──► Peeling ──► Skill Engine
  Stage 1      Stage 2       Stage 3      Stage 4
 (MinerU)     (LLM)         (LLM)        (LLM)
              ✓ checkpoint  ✓ checkpoint
```

- Stage 1 (MinerU)：调用 MinerU（remote API 或 local Gradio）将 PDF 转为 Markdown
- Stage 2 (Chunking)：LLM 分析全文目录结构，生成语义分块策略
- Stage 3 (Peeling)：递归剥离超过阈值的大块（TOC 剥离），生成最终 chunk 树
- Stage 4 (Skill Engine)：为每个 chunk 生成 SKILL.md 索引和参考文件（每次都重跑）

## 快速开始

### 1. 安装

```bash
python -m venv venv
# Windows
venv\Scripts\activate
# Linux / macOS
source venv/bin/activate

pip install -r requirements.txt
```

### 2. 配置

首次运行 `python main.py` 会自动启动引导向导，逐步完成配置。也可手动创建 `.env`：

```bash
cp .env.example .env
# 编辑 .env 填入 API Key
```

最小配置只需一个 LLM Provider Key + MINERU_API_KEY（remote 模式时）。

### 3. 运行

**交互式测试（推荐新手）**

```bash
python run_test.py
```

**命令行直接调用**

```bash
python main.py "path/to/book.pdf"
python main.py "path/to/book.pdf" --output outputs
```

**断点续传**

```bash
# 从 Stage 2 重新开始（保留 Stage 1 结果）
python main.py "path/to/book.pdf" --from-stage llm_chunking

# 清除所有 checkpoint，从头运行
python main.py "path/to/book.pdf" --restart
```

`--from-stage` 可选值：`pdf_conversion`、`llm_chunking`、`tree_merging`

**Web 界面**

```bash
python frontend/server.py
# 浏览器访问 http://localhost:8501
```

> ⚠️ 旧版 Web 界面（`frontend/server.py`）已弃用，仅过渡期保留。推荐使用下方的**纯前端模式**。

## 纯前端模式（SDK + 演示界面）

除 Python CLI 外，项目提供 TypeScript SDK：流水线四阶段（分块 / 树剥离 / 技能生成）、PDF 校验、checkpoint 断点续传、文件存储（OPFS）、zip 导出**全部在浏览器内完成**。后端瘦身为纯代理，只做两件事：

1. **LLM 转发**（`POST /api/llm/chat`）——持有 API Key，Key 永不进入浏览器
2. **MinerU 转发**（`/api/mineru/*`）——官方 API 与自部署 Gradio 全部经代理访问

### 启动

```bash
npm install

# ① 启动代理后端（端口 8502，读取 .env 与 config/settings.yaml）
python frontend/proxy_server.py

# ② 启动演示界面（新终端）
npm run dev:app
# 浏览器访问 http://localhost:5173
```

### 在自己的前端项目中使用 SDK

```bash
npm install @pdf2skill/sdk jszip pdf-lib pdfjs-dist
```

```ts
import { Pdf2Skill } from '@pdf2skill/sdk';

const pdf2skill = new Pdf2Skill({
  config: {
    providers: { 'my-provider': { base_url: 'https://api.example.com/v1', api_key_env: 'MY_KEY' } },
    routers: { chunking: 'my-provider', peeling: 'my-provider', skillEngine: 'my-provider' },
  },
});

pdf2skill.on('stage', (e) => console.log(e.stage, e.status, e.percent));
pdf2skill.on('log', (msg) => console.log(msg));

// 运行（PDF 或 Markdown 直接导入）
const result = await pdf2skill.run({
  kind: 'markdown',
  file: new File([text], 'book.md', { type: 'text/markdown' }),
  name: 'book.md',
}, {
  // 可选：指定输出名称（kebab-case），同时用作输出目录名与 SKILL.md 的 name 字段。
  // 不传则从文件名自动派生；校验规则见 validateSkillName()。
  outputName: 'my-book-notes',
});

// 任务管理 / 输出浏览 / 导出
const jobs = await pdf2skill.listJobs();
const tree = await pdf2skill.listOutputTree(jobs[0].jobId);
const zip = await pdf2skill.exportZip(jobs[0].jobId); // Blob，可直接触发下载
```

SDK 通过同源后端 `/api/llm/chat` 与 `/api/mineru/*` 调用 LLM 与 MinerU，部署时需保证页面与代理后端同源（或同域反代）。中断后重新运行同一文件自动从 checkpoint 续跑；`abort()`、`resume()`、`deleteJob()` 语义与 CLI 一致。

### SDK 测试

```bash
npm test   # vitest，145 个用例（分块 / 剥离 / 技能生成 / 流水线 / 门面 / 命名校验 / 与 Python 录制回放逐字节对齐）
```

## 配置

### .env vs settings.yaml

| 场景 | 改哪个 |
|------|--------|
| API 密钥 | .env |
| 临时切换 Provider 或模型 | .env |
| 项目长期默认配置 | settings.yaml |
| 团队统一基线设置 | settings.yaml |

### LLM Provider

项目使用 OpenAI Chat Completions 兼容格式调用 LLM，因此**任何提供 OpenAI 兼容 API 的服务均可接入**。用户应自行寻找 API 来源（官方平台、第三方中转站、自建代理等），然后在 `settings.yaml` 中添加 Provider 配置即可，无需改动任何 Python 代码。

每个 Provider 需配置：

| 字段 | 说明 |
|------|------|
| `base_url` | OpenAI 兼容 API 地址（不含 `/chat/completions`） |
| `api_key_env` | 存放 API Key 的环境变量名 |
| `chunking_model` | Stage 2 使用的模型 |
| `peeling_model` | Stage 3 使用的模型 |
| `skill_engine_model` | Stage 4 使用的模型 |

**内置 Provider 示例：**

| Provider | base_url | api_key_env | 说明 |
|----------|----------|-------------|------|
| siliconflow | `https://api.siliconflow.cn/v1` | `SILICONFLOW_API_KEY` | SiliconFlow 官方平台 |
| google | `https://generativelanguage.googleapis.com/v1beta/openai` | `GOOGLE_API_KEY` | Google Gemini API |
| nova | `http://token.njunova.com:8317/v1` | `NOVA_API_KEY` | NJU 校内服务 |
| vectorengine | `https://api.vectorengine.ai/v1` | `VECTORENGINE_API_KEY` | 第三方 API 中转站 |

> 上表仅为示例。请根据自身可用的 API 来源，在 `settings.yaml` 中添加或修改 Provider 配置。

**添加新 Provider 示例：**

```yaml
# settings.yaml
llm:
  providers:
    my-provider:
      base_url: https://your-api-endpoint.com/v1
      api_key_env: MY_PROVIDER_API_KEY
      chunking_model: model-name-a
      peeling_model: model-name-b
      skill_engine_model: model-name-c
```

```bash
# .env
MY_PROVIDER_API_KEY="your_key_here"
CHUNKING_PROVIDER="my-provider"
PEELING_PROVIDER="my-provider"
SKILL_ENGINE_PROVIDER="my-provider"
```

**Provider 路由**（三个阶段可独立选择不同 Provider）：

| 环境变量 | 作用 |
|----------|------|
| `CHUNKING_PROVIDER` | Stage 2 使用的 Provider |
| `PEELING_PROVIDER` | Stage 3 使用的 Provider |
| `SKILL_ENGINE_PROVIDER` | Stage 4 使用的 Provider |

**模型覆盖**（优先级从高到低）：

1. `{STAGE}_MODEL` 环境变量（如 `CHUNKING_MODEL`）— 全局覆盖
2. `settings.yaml` 中 `llm.providers.{provider}.{stage}_model`
3. 缺失时报错

**Provider 字段环境变量覆盖**：格式为 `{PROVIDER_NAME}_{FIELD}`（连字符转下划线）：

```bash
SILICONFLOW_BASE_URL="https://custom-proxy.example.com/v1"
GOOGLE_CHUNKING_MODEL="gemini-2.0-flash"
LOCAL_VLLM_BASE_URL="http://192.168.1.100:8000/v1"
```

### MinerU 配置

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `MINERU_API_MODE` | `remote`（官方 API）或 `local`（本地 Gradio） | `remote` |
| `MINERU_LANGUAGE` | 语言：`ch` / `en` / `east_slavic` | `ch` |
| `MINERU_LOCAL_BASE_URL` | 本地 Gradio 地址（local 模式） | `http://localhost:7860` |
| `MINERU_REMOTE_UPLOAD_MODE` | 远程上传模式 | `file` |

> Remote 模式需要 `MINERU_API_KEY`；Local 模式需先启动 MinerU Gradio 服务。

### PDF 处理参数

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `PDF_PAGE_LIMIT` | 单 PDF 最大页数 | 200 |
| `CHUNK_MERGE_THRESHOLD` | 合并分片字符阈值 | 5000 |
| `CHUNK_MIN_THRESHOLD` | 触发合并的最小字符长度 | 1000 |
| `REQUEST_INTERVAL` | API 请求间隔（秒） | 1.0 |

## 输出结构

```
outputs/
└── book_name/
    ├── .checkpoint.json        # 断点文件
    ├── full.md                 # Stage 1 输出（Markdown 全文）
    ├── full_chunks_original/   # Stage 2 原始分块
    ├── full_chunks/            # Stage 3 剥离后分块
    │   ├── chunks/
    │   ├── chunks_index.json
    │   └── tree.json
    └── generated_skills/       # Stage 4 技能文件
        ├── SKILL.md            # 主索引
        └── references/         # 各 chunk 参考
```

## 项目结构

```
main.py                  # CLI 入口 + run_pipeline()
run_test.py              # 交互式测试入口
config/
  settings.yaml          # Provider / MinerU / PDF 参数
  config.py              # 配置单例（双层合并：yaml + env）
core/
  pdf_processor.py       # Stage 1: MinerU remote/local
  llm_chunker.py         # Stage 2: Chunking 语义分块
  tree_merger.py         # Stage 3: Peeling TOC 剥离
  skill_engine.py        # Stage 4: Skill Engine 技能生成
  onboarding.py          # 首次运行引导向导
frontend/
  server.py              # 旧版 FastAPI 全栈后端（已弃用，过渡期保留）
  proxy_server.py        # 纯前端模式的瘦代理后端（LLM / MinerU 转发）
  static/index.html      # 旧版 SPA 前端（已弃用）
packages/
  sdk/                   # @pdf2skill/sdk：浏览器端流水线 SDK（Vite lib mode）
    src/
      pipeline.ts        # 四阶段编排（对应 main.py run_pipeline）
      pdf2skill.ts       # Pdf2Skill 门面类
      stages/            # llmChunker / treeMerger / skillEngine
      mineru/            # MinerU 客户端 + zip 解压（经代理）
      storage/           # OPFS 封装 + checkpoint
      export/zip.ts      # zip 导出
    test/                # vitest 单测 + 与 Python 的录制回放对齐测试
  app/                   # @pdf2skill/app：纯前端演示界面（Vite + TS，4 Tab）
utils/
  logger.py              # 日志 + PDF2SkillsException
  llm_client.py          # OpenAI 兼容 LLM 客户端
  retry_client.py        # 带重试的 HTTP 客户端
  checkpoint.py          # 断点续传管理器
prompts/                 # 各阶段 LLM 提示词模板
tests/                   # pytest 测试（testpaths 已限定在此目录）
```

## 测试

```bash
# Python 单元测试（核心逻辑 + 代理安全 + 回归）
python -m pytest tests -q

# SDK 单元测试（145 个用例，vitest）
npm test

# 手工脚本（非 pytest 用例，需配置 .env，会调用真实 API）
python tests/test_e2e_real.py
python tests/test_mineru_stage.py
```

## 许可证

[MIT](LICENSE)
