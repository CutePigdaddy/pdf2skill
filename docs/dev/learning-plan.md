# Project Learning Plan: pdf2skill

> 该计划通过“关注点分离”原则，将项目拆解为独立的功能单元，旨在帮助开发者从底层基础设施到核心 AI 逻辑系统性地掌握代码库。建议按阶段顺序进行学习。

---

## 📍 第一阶段：基础设施与配置 (Infrastructure)
掌握项目如何管理全局状态、环境加载以及诊断信息。
- [x] **配置管理系统 (Config System)**
  - **描述**: 学习项目如何平衡 YAML 配置文件、环境变量和默认参数。
  - **涉及文件**: [config/config.py](config/config.py), [config/settings.yaml](config/settings.yaml)
  - **关键技术**: Singleton (单例模式), `PyYAML`, `python-dotenv`.
  - **动手练习**: 在 `settings.yaml` 中添加一个自定义字段（如 `max_retries`），并在 `main.py` 中通过 `config` 对象打印它。

- [ ] **日志与诊断 (Logging)**
  - **描述**: 了解标准化的日志记录方式，方便在多阶段流水线中追踪错误。
  - **涉及文件**: [utils/logger.py](utils/logger.py)
  - **关键技术**: Python `logging` 模块, 格式化配置。
  - **动手练习**: 修改 `logger.py` 使其能同时将日志输出到文件 `logs/latest.log`。

---

## 📍 第二阶段：数据流与模型 (Data Focus)
深入理解知识树的内部表示以及中间状态的持久化逻辑。
- [x] **知识树结构 (Tree Representation)**
  - **描述**: 定义 `ChunkNode` 层级结构，表示从大纲至原子段落的转化。
  - **涉及文件**: [core/tree_merger.py](core/tree_merger.py) (搜索 `ChunkNode` 定义)
  - **关键技术**: `dataclasses`, JSON 嵌套序列化。
  - **动手练习**: 编写一个脚本加载 `tree.json`，统计其中 `is_atomic: True` 的叶子节点总数。

---

## 📍 第三阶段：核心业务逻辑 (Core Logic)
项目的“大脑”，学习 PDF 字节如何变为结构化的语义单元。
- [ ] **PDF 预处理与 OCR (Stage 1)**
  - **描述**: 集成 MinerU API 进行高质量 PDF 转 Markdown，并处理超大文件切分。
  - **涉及文件**: [core/pdf_processor.py](core/pdf_processor.py)
  - **关键技术**: MinerU API 调用, `PyPDF2` (页面分割).
  - **动手练习**: 尝试手动调用 `pdf_processor.py` 中的切分逻辑，验证输出的 PDF 片段是否正确。

- [ ] **语义分块与递归剥离 (Stage 2 & 3)**
  - **描述**: 学习如何利用 LLM 规划分块（Conceptual Chunking）以及通过模糊匹配进行递归切分（Recursive Peeling）。
  - **涉及文件**: [core/llm_chunker.py](core/llm_chunker.py), [core/tree_merger.py](core/tree_merger.py)
  - **关键技术**: `Levenshtein` 距离 (模糊匹配), 递归算法, 提示词工程.
  - **动手练习**: 在 `tree_merger.py` 中调整模糊匹配的阈值，观察对“锚点”定位准确性的影响。

---

## 📍 第四阶段：通信与外部集成 (Integration)
管理本地代码与高性能远程模型之间的桥梁。
- [ ] **LLM 客户端抽象 (Model Abstraction)**
  - **描述**: 封装不同提供商（Gemini, SiliconFlow）的 API，处理 System Prompt 和响应格式。
  - **涉及文件**: [utils/llm_client.py](utils/llm_client.py), [utils/retry_client.py](utils/retry_client.py)
  - **关键技术**: 模型工厂模式, HTTP 请求封装, API 适配。
  - **动手练习**: 使用 `llm_client.py` 编写一个独立脚本，向模型发送一段 Markdown 文本并要求提取所有标题。

---

## 📍 第五阶段：健壮性与稳定性 (Reliability)
确保耗时较长的流水线（可能持续数小时）在失败时不会丢失进度。
- [ ] **断点续传机制 (Checkpointing)**
  - **描述**: 实现“检查点-重启”逻辑，允许跳过已完成的阶段。
  - **涉及文件**: [utils/checkpoint.py](utils/checkpoint.py)
  - **关键技术**: 状态机持久化, JSON 状态追踪。
  - **动手练习**: 手动修改 `.checkpoint.json` 中的某个阶段状态，运行 `main.py` 验证是否能正确跳过或重新开始。

---

## 📍 第六阶段：流水线编排 (Orchestration)
将所有模块串联成一个内聚的工作流。
- [ ] **主入口驱动 (Main Entry)**
  - **描述**: 解析 CLI 参数，初始化环境，顺序执行四个处理阶段。
  - **涉及文件**: [main.py](main.py)
  - **关键技术**: `argparse`, 流程控制。
  - **动手练习**: 为 `main.py` 添加一个 `--dry-run` 选项，只打印将要执行的阶段而不进行实际 API 调用。

---

## 📍 第七阶段：质量保证 (Quality)
验证提取的知识是否准确，代码是否健壮。
- [ ] **调试与可视化 (Debugging)**
  - **描述**: 用于检查中间分块内容和输出质量的辅助脚本。
  - **涉及文件**: [debug_chunks.py](debug_chunks.py), [test_sf.py](test_sf.py)
  - **动手练习**: 运行 `debug_chunks.py` 检查生成的 `chunks/` 目录，确认是否有 OCR 错误或分块不合理的地方。
