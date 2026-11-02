# pdf2skill

将 PDF 教材、文档和书籍自动转换为结构化知识索引与可复用技能库。

## 功能特性

- **PDF 解析**：基于 MinerU 引擎，支持 remote API 和 local Gradio 双模式，保留公式、表格与图片
- **语义切分**：LLM 驱动的策略性分块，保护不可分割的逻辑单元（习题集、定理证明、代码块）
- **树状合并**：递归剥离大块内容，Levenshtein 模糊匹配定位子标题，直至每块在 token 阈值内
- **技能生成**：为每个 chunk 提取关键词标签，生成 SKILL.md 主索引和独立参考文件
- **OpenAI 兼容**：所有 LLM 请求走 OpenAI Chat Completions 格式，支持任意兼容供应商
- **断点续传**：Stage 2/3 独立 checkpoint；Peeling 按 chunk 缓存已完成子树，`--from-stage` 选择性重跑、`--restart` 全部重来
- **纯前端模式**：TypeScript SDK（`@pdf2skill/sdk`）在浏览器内完成全部流水线（OPFS 存储、断点续传、zip 导出），后端只做 LLM / MinerU 代理
- **Web 界面**：Vite + TS 演示界面，配置/运行/输出一站式操作；`python frontend/server.py` 单入口托管构建后的界面与代理 API

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

- `llm_chunking`：保留 Stage 1，重跑分块；内容未变化的 chunk 会复用 `peeling_cache/`，不会重做 Peeling
- `tree_merging`：保留分块，只补齐尚未完成的 Peeling 子树
- `pdf_conversion` / `--restart`：同时清空 `peeling_cache/`，避免旧 markdown 的剥离结果混入

Stage 2 本身是一次 LLM 规划调用（失败重试一次），没有可切的中间进度；Stage 3 的 Peeling 才按子树缓存。

**Web 界面（单入口）**

```bash
npm install
npm run build            # 构建新版工作台到 packages/app/dist
python frontend/server.py
# 浏览器访问 http://localhost:8501
```

`server.py` 同时托管构建后的新版工作台与 `/api/*` 代理（LLM / MinerU 转发）。API Key 只保存在服务端：在「配置」面板设置后会写入项目根目录 `.env` 并立即生效，重启后仍保留；Key 不会进入浏览器。前端开发时改用下文的 Vite 开发模式。

「配置」面板的 Provider、模型、思考强度、PDF 与 MinerU 参数会先保存到浏览器 localStorage，点「保存配置」时再同步写入 `config/settings.yaml`；「从服务端导入」可把服务端配置完整恢复到当前浏览器。各参数也可继续直接编辑这两个文件。

新版工作台的「运行」页可直接选择当前浏览器 OPFS 中已完成 Stage 1 的任务继续，无需重新上传原文件；「输出」页也提供「继续任务」入口。浏览器任务按 origin 隔离（`127.0.0.1:8501`、`localhost:8501`、`5173` 各自独立）。

CLI 写在项目根目录 `outputs/` 的任务不会自动出现在浏览器列表里，但可以在「运行」页点「从磁盘导入任务」：服务端 `GET /api/jobs` 列出可续跑的磁盘任务，`GET /api/jobs/{id}/bundle` 打包 checkpoint、`full.md`、图片、分块与技能文件，浏览器解压写入 OPFS 后即可按原有阶段数继续。打包会排除 `splits/`、MinerU 分段目录与原始 zip（Stage 1 已完成，不需要它们）。同名任务不会被覆盖，需先在「输出」页删除旧任务。

「运行」页日志默认只显示阶段与关键状态，带时间戳和级别；勾选「详细」可展开逐 chunk、锚点与缓存日志，日志最多保留 500 行。

## 纯前端模式（SDK + 演示界面）

除 Python CLI 外，项目提供 TypeScript SDK：流水线四阶段（分块 / 树剥离 / 技能生成）、PDF 校验、checkpoint 断点续传、文件存储（OPFS）、zip 导出**全部在浏览器内完成**。后端瘦身为纯代理，只做两件事：

1. **LLM 转发**（`POST /api/llm/chat`）——持有 API Key，Key 永不进入浏览器
2. **MinerU 转发**（`/api/mineru/*`）——官方 API 与自部署 Gradio 全部经代理访问

### 启动（开发模式）

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
const jobs = await pdf2skill.listJobs(); // completedStages + resumable
const tree = await pdf2skill.listOutputTree(jobs[0].jobId);
const zip = await pdf2skill.exportZip(jobs[0].jobId); // 整包输出 zip（保留 outputs/ 布局）
const skill = await pdf2skill.exportSkillZip(jobs[0].jobId); // { blob, skillName }
// skill.blob 下载文件名使用 SKILL.md 的 name；压缩包根目录直接是 SKILL.md + references/

// 不重新上传原文件，直接从已有任务的 checkpoint 继续
if (jobs[0].resumable) {
  await pdf2skill.resume(jobs[0].jobId, { fromStage: 'tree_merging' });
}

// 把服务端 outputs/ 中的 CLI 任务导入 OPFS，再按 checkpoint 继续
const resp = await fetch(`/api/jobs/${encodeURIComponent('my-book')}/bundle`);
const imported = await pdf2skill.importJob(
  'my-book',
  new Uint8Array(await resp.arrayBuffer()),
);
console.log(imported.completedStages); // ['pdf_conversion', 'llm_chunking', ...]
```

SDK 通过同源后端 `/api/llm/chat` 与 `/api/mineru/*` 调用 LLM 与 MinerU，部署时需保证页面与代理后端同源（或同域反代）。中断后重新运行同一文件自动从 checkpoint 续跑；`abort()`、`resume()`、`importJob()`、`deleteJob()` 语义与 CLI 一致。`resume()` 只依赖 OPFS 中的 Stage 1 markdown，因此 `listJobs()` 用 `resumable` 标记哪些任务可以不再上传原文件直接继续。

### SDK 测试

```bash
npm test   # vitest，167 个用例（分块 / 剥离与缓存 / 技能生成 / 流水线 / 任务导入 / 双 zip 导出 / 门面 / 命名校验 / 与 Python 录制回放逐字节对齐）
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
| `chunking_reasoning_effort` | Stage 2 的思考强度（可选，`reasoning_effort`） |
| `peeling_reasoning_effort` | Stage 3 的思考强度（可选） |
| `skill_engine_reasoning_effort` | Stage 4 的思考强度（可选） |

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
      # 可选：OpenAI Chat Completions 的 reasoning_effort
      chunking_reasoning_effort: low
      peeling_reasoning_effort: high
      skill_engine_reasoning_effort: high
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

**思考强度**（可选）：每个阶段可单独设置 OpenAI Chat Completions 的 `reasoning_effort`，内置预设为 `none` / `minimal` / `low` / `medium` / `high` / `xhigh` / `max`；留空表示不发送该字段（由 Provider/模型使用默认值）。不同模型支持的档位不同，不支持的档位会被 Provider 拒绝（HTTP 400）。Provider 的 `/models` 接口通常不返回该元数据，因此 Web 配置页直接提供上述预设。

Python CLI 的覆盖优先级：

1. `{STAGE}_REASONING_EFFORT` 环境变量（如 `PEELING_REASONING_EFFORT`）
2. `{PROVIDER_NAME}_{STAGE}_REASONING_EFFORT` 环境变量（如 `GOOGLE_PEELING_REASONING_EFFORT`）
3. `settings.yaml` 中 `llm.providers.{provider}.{stage}_reasoning_effort`
4. 留空不发送

**Provider 字段环境变量覆盖**：格式为 `{PROVIDER_NAME}_{FIELD}`（连字符转下划线）：

```bash
SILICONFLOW_BASE_URL="https://custom-proxy.example.com/v1"
GOOGLE_CHUNKING_MODEL="gemini-2.0-flash"
GOOGLE_PEELING_REASONING_EFFORT="high"
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
    ├── peeling_cache/          # Peeling 子树缓存（内部状态，不打包进 zip）
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
  server.py              # 单入口：托管 packages/app/dist + 挂载代理 API（8501）
  proxy_server.py        # 瘦代理后端（LLM / MinerU 转发；开发模式 8502）
  job_bridge.py          # 磁盘 outputs/ 任务列表与打包（供浏览器导入 OPFS）
packages/
  sdk/                   # @pdf2skill/sdk：浏览器端流水线 SDK（Vite lib mode）
    src/
      pipeline.ts        # 四阶段编排（对应 main.py run_pipeline）
      pdf2skill.ts       # Pdf2Skill 门面类
      stages/            # llmChunker / treeMerger / peelCache / skillEngine
      mineru/            # MinerU 客户端 + zip 解压（经代理）
      storage/           # OPFS 封装 + checkpoint + importJob（磁盘任务导入）
      export/zip.ts      # 整包 zip + 按 SKILL.md name 打包的 Skill zip
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
# Python 单元测试（89 passed，核心逻辑 + 代理安全 + 任务桥接 + 回归）
python -m pytest tests -q

# SDK 单元测试（167 个用例，vitest）
npm test

# 手工脚本（非 pytest 用例，需配置 .env，会调用真实 API）
python tests/test_e2e_real.py
python tests/test_mineru_stage.py
```

## 许可证

[MIT](LICENSE)
