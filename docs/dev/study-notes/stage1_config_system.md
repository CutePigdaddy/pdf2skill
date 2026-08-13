# Stage 1: 配置管理系统 (Config System) - 全链路深度拆解

本阶段解析项目如何通过 `Config` 类实现全局配置的统一管理。该系统支持从 YAML 文件读取默认配置，通过环境变量进行动态覆盖，并以单例模式确保全局状态的一致性。

## 第一部分：核心类定义与初始化 (路径: [config/config.py](config/config.py))

```python
class Config:
    _instance = None
    
    def __new__(cls, config_path=None):
        if cls._instance is None:
            cls._instance = super(Config, cls).__new__(cls)
            cls._instance._init_config(config_path)
        return cls._instance
```
- **Line 5-11**: 实现 **Singleton (单例模式)**。
  - **`__new__` (构造方法)**: 这是 Python 实例化对象时**核心的第一个环节**。不同于 `__init__`（初始化方法，此时实例已存在），`__new__` 负责**创建并返回**实例。在单例模式中，我们拦截这个过程，如果 `_instance` 已存在就不再分配新内存，直接返回旧引用。
  - **`super(Config, cls).__new__(cls)`**:
    - `super()`: 调用父类（在 Python 3 中默认是 `object`）的方法。
    - `__new__(cls)`: 向操作系统申请内存空间，创建一个真正的 `Config` 类型对象。
  - **`_instance`**: 这是一个**类属性**（Class Attribute），它存储在类对象的命名空间中，而不是某个具体的实例中，因此所有 `Config()` 调用都能访问到同一个 `_instance`。

```python
    def _init_config(self, config_path):
        if config_path is None:
            config_path = Path(__file__).parent / "settings.yaml"
            
        with open(config_path, 'r', encoding='utf-8') as f:
            self._config = yaml.safe_load(f)
            
        self.merge_env_vars()
```
- **Line 17-18**: **`PyYAML` 库深度应用**。
  - **YAML 原理**: YAML 是一种人类可读的序列化标准。它的核心是通过**缩进**表示嵌套，通过 `:` 表示键值对。
  - **Python 转化结果**: `yaml.safe_load(f)` 会将 YAML 文本映射为 Python 的原生数据结构：
    - 嵌套的映射（Mapping） $\rightarrow$ `dict` (字典)
    - 列表（Sequence） $\rightarrow$ `list` (列表)
    - 标量（Scalar） $\rightarrow$ `str`/`int`/`float`/`bool` 等。
  - **`safe_load` vs `load`**: 这是一个重要的安全防线。`load` 可以反序列化自定义 Python 对象（可能执行恶意代码），而 `safe_load` 仅处理标准的 YAML 类型。

---

## 第二部分：环境变量覆盖逻辑 (路径: [config/config.py](config/config.py))

```python
    def merge_env_vars(self):
        # Override with environment variables if present
        if "PDF_PAGE_LIMIT" in os.environ:
            self._config['pdf']['page_limit'] = int(os.environ["PDF_PAGE_LIMIT"])
```
- **Line 24-25**: **系统环境交互**。
  - **`os.environ`**: 这是 Python 对操作环境（Shell/Windows Environment）中全局变量的一个**映射对象（Mapping Object）**。它并不是一个简单的字典，但表现得很像字典。
  - **数据类型陷阱**: 环境变量在操作系统层面**始终是字符串**。即使你在 shell 里设置 `PDF_PAGE_LIMIT=600`，`os.environ` 拿到的也是 `"600"`。因此，代码中必须显式进行 `int()` 类型转换，否则后续数值计算会抛出 `TypeError`。

```python
        # Specific model overrides
        for provider in ['siliconflow', 'google', 'openai', 'anthropic']:
            chunk_env = f"{provider.upper()}_CHUNKING_MODEL"
            if chunk_env in os.environ:
                self._config['llm']['providers'][provider]['chunking_model'] = os.environ[chunk_env]
```
- **Line 40-44**: **元编程初探**。
  - **动态键名生成**: 利用 Python 字符串的丰富的操作方法（如 `.upper()`），我们可以构建出符合规范的环境变量名。
  - **映射修改**: 这里的 `self._config` 实际上是一个深层嵌套的字典。在 Python 中，字典是**引用传递**的，所以对子字典内容的修改会直接反映在全局 `config` 对象中。

---

## 第三部分：配置项查询接口 (路径: [config/config.py](config/config.py))

```python
    def get(self, key, default=None):
        keys = key.split('.')
        val = self._config
        for k in keys:
            if isinstance(val, dict) and k in val:
                val = val[k]
            else:
                return default
        return val
```
- **Line 53-61**: 实现**链式路径查询**。用户可以通过 `config.get('llm.providers.google.chunking_model')` 访问嵌套极深的配置项。
- **Line 57**: 健壮性检查。如果路径中间某个节点不是字典（`isinstance(val, dict)`）或键不存在，则返回 `default` 值，有效防止了 `KeyError` 导致的程序崩溃。

---

## 课后实战 Lab：配置系统分层训练 (Tiered Practice)

### 🧩 核心 Lab (必备：单例配置器复刻)
**目标**：用最快速度复刻 `Config` 类的**必学**核心：单例模式 + 字典读取。

**动手要求**：在 `docs/study_notes/labs/` 下创建 `lab_stage1_core.py`。
- [x] **复写单例**：使用 `__new__` 和 `super()` 实现一个 `SimpleConfig` 类。
- [x] **基础存取**：类初始化时加载一个硬编码的字典 `{"debug": True}`，并提供简单的 `get(key)` 方法。
- [x] **原理验证**：通过 `c1 = SimpleConfig(); c2 = SimpleConfig()` 并打印 `c1 is c2` 来验证单例。

---

### 🚀 实战 Lab (可选：工业级日志配置器应用)
**目标**：将学到的技术迁移到**差异化场景**，锻炼灵活应用能力。

**动手要求**：在 `docs/study_notes/labs/` 下创建 `lab_stage1_logger.py`（如果已创建可继续完善）。
- [ ] **场景化设计**：实现一个 `LoggerConfig` 配置器，专门管理日志级别（INFO/DEBUG）和存储路径。
- [ ] **异常兜底**：在读取配置时加入 `try-except` 处理。
- [ ] **路径逻辑**：使用 `os.path` 动态生成日志文件夹路径。

---

### 🎓 导师 Review 提示
完成后贴出代码，我将针对你的 **核心 Lab** 进行快速确认（是否掌握了 Python 单例底层），若你提交了 **实战 Lab**，我们将深入探讨如何在异常场景下保证单例配置的健壮性。

---
/study-mentor
