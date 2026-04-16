# 📘 pdf2skill

[![Python Version](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![MinerU](https://img.shields.io/badge/Engine-MinerU-orange.svg)](https://mineru.net/)

**pdf2skill** 是一个自动化工具，用于将 PDF 文档（如教材、技术文档或学术书籍）转换为结构化的技能点和知识索引库，便于大语言模型（如 Claude、Cursor、Gemini）高效利用。

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

在项目根目录创建 `.env` 文件，并按需配置以下环境变量：

```bash
# MinerU API 密钥
MINERU_API_KEY="your_mineru_api_key"

# LLM 服务密钥（任选其一）
SILICONFLOW_API_KEY="your_siliconflow_key"
GOOGLE_API_KEY="your_google_key"
```

### 运行工具

```bash
python main.py "path/to/your_book.pdf" --output "outputs"
```

默认输出目录为 `outputs/`。每本书会生成独立子目录，包含中间切片、合并后的结果和最终技能文件。

## 配置

配置文件路径：`config/settings.yaml`

| 配置项 | 说明 | 默认值 |
| :--- | :--- | :--- |
| `pdf.chunk_max_tokens` | 单个 Chunk 的最大长度 | `10000` |
| `pdf.chunk_min_threshold` | 触发合并的最小字符长度 | `1000` |
| `llm.routers` | 模型供应商配置 | `google` 或 `siliconflow` |
| `mineru.language` | PDF 语言类型 | `ch`（中文）或 `en`（英文） |

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
