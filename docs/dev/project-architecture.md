# 项目架构分析 (Project Architecture Analysis)

## 1. 项目概览
该项目 `pdf2skill` 是一套基于大语言模型（LLM）的文档处理与智能转换管线系统（Pipeline）。其核心价值在于将晦涩或长篇幅的离线 PDF 文档自动化解析、进行概念级分块（LLM Conceptual Chunking）、梳理层次树状结构，并最终生成目标平台可直接采用的 **AI 技能 (Skill)** 文件体系（如 Markdown 格式的指令或知识库）。它自带进度状态缓存（Checkpoint）机制，使得处理长耗时大文档时，中断后可无缝断点续传。

## 2. 核心逻辑流 (Flowchart)
以下是从启动到结束的数据流向与主要节点交互（加粗高亮为 `Core Asset` 节点）：

```mermaid
flowchart TD
    Start([CLI: python main.py]) --> LoadConfig(加载全局配置 config/)
    LoadConfig --> CP[CheckpointManger\n(检查断点状态)]
    
    subgraph Pipeline [Core Logic Pipeline]
        CP -.未完成.-> Stage1[::Core Asset::\nCore: PDFProcessor\n(PDF转Markdown)]
        Stage1 --> Checkpoint1(保存 md_file)
        
        Checkpoint1 -.未完成.-> Stage2[::Core Asset::\nCore: LLMChunker\n(LLM语义分块)]
        CP --已完成Stage1--> Stage2
        Stage2 --> Checkpoint2(保存 Base Chunks)
        
        Checkpoint2 -.未完成.-> Stage3[::Core Asset::\nCore: TreeMerger\n(TOC树状合并与精简)]
        CP --已完成Stage2--> Stage3
        Stage3 --> OutputOriginal(输出原始Chunks至目录)
        Stage3 --> Checkpoint3(保存 Master Tree)
        
        Checkpoint3 -.未完成.-> Stage4[::Core Asset::\nCore: SkillEngine\n(Skill生成引擎)]
        CP --已完成Stage3--> Stage4
        Stage4 --> OutputSkill(输出至 generated_skills)
    end
    
    OutputSkill --> Finish([Pipeline Finished])
```

## 3. 技术栈
本项目的开发语言为 **Python**，核心依赖与工具库如下：
*   **PDF 操作与文档解析**: `PyPDF2`, `PyMuPDF (fitz)` – 负责底层的文本抽取与文档结构识别。
*   **API 交互与外部调用**: `requests` 处理大模型接口的 HTTP 请求；`tenacity` 处理请求的自动重试策略。
*   **字符串与算法**: `Levenshtein` 用于模块间的文本距离计算（如树节点内容相似度去重或匹配等）。
*   **配置与工程化**: `PyYAML` 解析 YAML 配置文件；`colorama` 控制台着色与标准输出；`python-dotenv` 环境变量管理。

## 4. 功能模块与依赖
根据扫描的 Codemap，剔除 Third-party 与冗余内容后，系统关键功能模块及其依赖关系如下：

### 核心处理层 (`core/` - **Core Asset**)
*   **`core/pdf_processor.py`**: PDF 解析器核心类。负责执行（Stage 1）阶段任务，将 PDF 转为 Markdown 中间层文本。
*   **`core/llm_chunker.py`**: 大模型语义分块器。负责执行（Stage 2）任务，调用 LLM 接口实现文本内容在语义理解上的重组。
*   **`core/tree_merger.py`**: TOC 树状结构合并器。执行（Stage 3），通过算法将分块重新组织为具备上下文的层级树。
*   **`core/skill_engine.py`**: Skill 引擎。执行（Stage 4），承接整合后的树结构数据，格式化输出大语言模型所需的体系化技能文件（如 `.md`）。
*   *依赖流向：依赖 `utils/` 提供的网络层、重试机制、数据容灾(Checkpoint)和日志输出。*

### 基础设施层 (`utils/` - **Core Asset**)
*   **`utils/llm_client.py` & `utils/retry_client.py`**: 封装与 LLM API 交互的 SDK 层和多级报错重试机制。
*   **`utils/checkpoint.py`**: 管理各流水线步骤之间的数据状态固化，防由于断网或解析失败导致的重新运算。
*   **`utils/logger.py`**: 提供全局标准化、带颜色的终端日志监控记录。
*   *依赖流向：被 `core/` 与 `main.py` 广泛引用，是系统运转的地基。*

### 配置管理中心 (`config/` - **Core Asset**)
*   **`config/config.py`**: 项目全局变量与依赖设定；配合 `settings.yaml` 向上下游提供静态参数映射。

## 5. 目录结构设计
该项目的目录组织遵循经典的“数据与逻辑分离”设计原则：

```text
pdf2skill/
├── main.py              # [入口机制] 项目CLI主入口
├── config/              # [配置中心] - Core Asset，托管环境变量与YAML设置
├── core/                # [业务逻辑] - Core Asset，四阶段转换器管道核心
├── utils/               # [核心工具] - Core Asset，日志、请求重试与Checkpoint组件
├── prompts/             # [业务模板] 统一管理大模型在不同阶段的交互提示词
├── inputs/              # [数据侧] 用户挂载待处理 PDF 文件的目录
├── docs/                # [文档侧] 归档分析报告与知识文档的地方
├── CS/ & vjf/           # [输出侧] <Purify & Merge> 执行输出子集，如原始Chunks与最终Skills
├── test_outputs/        # [输出侧] 测试环境生成产物
└── scan-output/         # Repo-scan 报告结果输出
```

## 6. 启动流程
项目单一执行入口为根目录下的 `main.py`。
从命令被执行（如：`python main.py inputs/book.pdf --output ./CS`）到完成的拆解步骤：

1. **环境初始化**：使用 `argparse` 接管终端输入参数 `pdf_path` 以及 `output`，将项目根目录追加进 `sys.path`，然后触发 `run_pipeline(pdf_path, output_dir)`。
2. **实例状态托管**：在目标目录（如 `output/`）内建立 `CheckpointManager` 实例，检查本地是否存在已执行阶段的快照进度。
3. **Stage 1 (PDF -> Markdown)**：实例化 `PDFProcessor`，对 PDF 元文件解构转换。若已完成，直接通过快照加载 Markdown 内容。
4. **Stage 2 (LLM Conceptual Chunking)**：实例化 `LLMChunker`，将 Markdown 进行段落划分，并利用 LLM 重组上下文语义块 (`base_chunks`)。
5. **阶段性输出 (TOC 保存)**：将 Stage 2 生肉（原始 Chunks）交由 `TreeMerger` 写入 `full_chunks_original/` 持久化。
6. **Stage 3 (TOC Drilling & Merging)**：实例化 `TreeMerger` 处理 `base_chunks`。重构树形结构节点，生成最终提纯合并版 (`master_root`) 写入 `full_chunks/`。
7. **Stage 4 (Skill Generation)**：实例化 `SkillEngine`。读取包含目录与正文完全关联的 `master_root`，生成专属的 Skill 制式 Markdown 到 `generated_skills/` 目录中。
8. **退出**：管道执行完毕，打印 `Pipeline Execution Finished Successfully!` 后正常退出生命周期。