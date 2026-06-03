# GraphAgent AI Coding 功能设计文档

## 1. 概述

### 1.1 设计背景

AI Coding 是 GraphAgent 的核心应用层功能之一，旨在让 Agent 具备理解需求、设计方案、编写代码、验证测试的完整软件开发能力。该功能以 Skill 的形式实现，通过精心设计的 Prompt 引导 LLM 按照 **需求澄清 → 方案设计 → 用例设计 → 任务分解 → 代码开发 → 测试验证 → 返回结果** 的七步工作流完成开发任务。

整个 AI Coding 过程严格遵循 **TDD（测试驱动开发）** 原则——先设计测试用例，再实现代码，最后通过测试验证正确性。

### 1.2 核心设计理念

```
用户需求（自然语言描述）
    → 步骤一：需求澄清（理解意图，澄清模糊点）
    → 步骤二：方案设计（业务规则与实现分离，输出设计文档）
    → 步骤三：用例设计（基于业务规则设计 alpha 测试用例）
    → 步骤四：任务分解（复杂任务拆分为多个子任务）
    → 步骤五：代码开发（逐项实现子任务）
    → 步骤六：集成验证（运行完整测试套件，失败则定位修复）
    → 步骤七：返回结果（汇报完成情况 + 修改文件列表）
```

**核心原则**：

1. **TDD 驱动**：先设计用例，再写代码，测试通过才算完成
2. **业务与实现分离**：方案设计阶段明确区分业务规则和具体技术实现
3. **渐进式开发**：复杂任务拆解为子任务，逐项完成、逐项验证
4. **质量门禁**：所有 UT 用例通过才视为开发完成，不通过则持续迭代修改
5. **一次少量代码**：单次写入控制在 200 行以内，读取控制在 150-250 行，保证 LLM 理解质量
6. **完整函数/类读取**：通过缩进格式判断代码边界，确保 LLM 读取完整的代码单元

### 1.3 与现有系统的关系

```
                          ┌──────────────────────┐
                          │   GraphAgent 核心     │
                          └──────────┬───────────┘
                                     │
              ┌──────────────────────┼──────────────────────┐
              │                      │                      │
              ▼                      ▼                      ▼
     ┌────────────────┐   ┌────────────────┐   ┌────────────────┐
     │  ToolCenter     │   │  Skill System  │   │  现有 Skills   │
     │  (工具注册中心)  │   │  (Skill 引擎)   │   │  (chat/calc…) │
     └────────────────┘   └───────┬────────┘   └────────────────┘
                                  │
                          ┌───────▼────────┐
                          │  ai-coding     │  ← 本次新增 Skill
                          │  (Skill 定义)   │
                          └───────┬────────┘
                                  │
                          ┌───────▼────────┐
                          │  新增工具        │
                          │  - glob_file    │
                          │  - grep_context │
                          │  - read_code    │
                          │  - update_code  │
                          │  - write_code   │
                          │  - execute_command  │
                          └─────────────────┘
```

- **不修改** GraphAgent 任何现有核心模块，纯增量开发
- 新增工具注册到 `ToolCenter`，与现有工具地位相同，受 RBAC 和安全体系约束
- 新增内置 Skill `ai-coding` 遵循现有 Skill 规范（YAML frontmatter + Markdown body）

---

## 2. 目录结构

```
prompts/skills/
    └── ai-coding.md                   # 新增：AI Coding Skill Prompt 定义

src/graph_agent/tools/
    └── ai_coding_tools.py             # 新增：glob_file / grep_context / read_code
                                        #       / update_code / write_code / execute_command

docs/applications/
    └── AI Coding功能设计.md           # 本文档
```

---

## 3. AI Coding 工作流设计

### 3.1 流程总览

AI Coding 是一个线性的七步工作流，每步有明确的输入、输出和验证标准。

```
步骤一：需求澄清（Requirement Clarification）
    输入：用户原始指令
    输出：清晰、完整、可执行的需求描述
    验证：所有模糊点已澄清，需求无二义性
      │
      ▼
步骤二：方案设计（Solution Design）
    输入：已澄清的需求描述
    输出：设计文档（业务规则 + 技术实现方案）
    验证：业务规则完整且与技术实现解耦
      │
      ▼
步骤三：用例设计（Test Case Design）
    输入：设计文档（业务规则部分）
    输出：Alpha 测试用例集
    验证：测试用例覆盖所有业务规则和边界条件
      │
      ▼
步骤四：任务分解（Task Decomposition）  ← 可选，简单任务可跳过
    输入：设计文档 + 测试用例
    输出：子任务列表（含依赖关系）
    验证：子任务粒度合理，可独立开发、独立测试
      │
      ▼
步骤五：代码开发（Code Implementation）
    输入：设计文档 + 子任务
    输出：实现代码 + UT 用例
    验证：代码符合设计，编译/语法无误
      │
      ▼
步骤六：测试验证（Test Verification）
    输入：实现代码 + UT 用例
    输出：测试结果（全部通过 / 部分失败）
    验证：所有 UT 用例全部通过
      │
      ├── 通过 ──→ 步骤七：返回结果
      │
      └── 失败 ──→ 回到步骤五（修改代码）
```

### 3.2 步骤一：需求澄清

#### 3.2.1 目标
准确理解用户想要完成的任务，确保需求清晰、完整、无二义性。

#### 3.2.2 执行逻辑

1. **解析用户指令**：从用户消息中提取核心意图、关键实体、约束条件
2. **判断清晰度**：
   - 信息足够 → 进入步骤二
   - 信息不足或存在歧义 → 调用 `ask_user` 工具向用户提问澄清
3. **澄清策略**：
   - 一次最多提 3 个问题（避免用户疲劳）
   - 对不清晰的内容进行精准追问，而非笼统的"请详细描述"
   - 如果用户回答了其中一部分，继续追问未澄清的部分

#### 3.2.3 需要澄清的典型场景

| 场景 | 示例 | 澄清问题 |
|------|------|----------|
| 目标不明 | "帮我写个工具" | "请问你想写什么类型的工具？期望实现哪些功能？" |
| 技术栈未指定 | "写一个 API 接口" | "请问你偏好哪种语言/框架？（如 Python FastAPI / Node.js Express / Go Gin）" |
| 约束条件缺失 | "实现用户登录" | "是否需要支持 OAuth 第三方登录？对密码加密算法有要求吗？" |
| 范围边界模糊 | "优化数据库查询" | "当前遇到性能问题的是哪些表/查询？有具体的慢查询日志吗？" |
| 多义性描述 | "加个缓存" | "你指的是 Redis 缓存、本地内存缓存，还是 HTTP 缓存头？" |

### 3.3 步骤二：方案设计

#### 3.3.1 目标
根据需求输出完整的设计文档，**严格将业务规则和具体实现分开**。

#### 3.3.2 设计文档结构

设计文档必须包含以下两个独立部分：

**第一部分：业务规则（Business Rules）**
- 业务流程描述（用自然语言 + 流程图描述）
- 输入/输出定义（数据格式、字段、约束）
- 业务规则列表（编号，可追溯）
- 边界条件与异常场景
- 与外部系统的交互约定（接口契约，非具体实现）

**第二部分：技术实现方案（Technical Implementation）**
- 技术选型与理由
- 模块/类设计（类图或结构说明）
- 数据模型设计（数据库表结构、内存数据结构）
- 接口/API 定义
- 关键算法说明
- 文件目录规划（新增和修改的文件列表）

#### 3.3.3 设计原则

- **业务规则是"做什么"，技术方案是"怎么做"**
- 同一套业务规则可以对应多种技术实现
- 测试用例基于业务规则编写，不基于技术实现编写
- 如果用户对技术栈无特殊要求，默认使用 Python 3.12+（与 GraphAgent 技术栈保持一致）

#### 3.3.4 产出物约定

设计文档统一保存至以下路径：

```
docs/design/{feature_name}_design.md
```

其中 `{feature_name}` 为功能的英文短横线命名（如 `user_auth`、`data_export`）。该路径约定确保：
- 所有 AI Coding 产出的设计文档集中管理
- 便于后续会话通过记忆系统引用
- 与项目已有的 `docs/` 目录结构兼容

测试用例设计文档（步骤三产出物）保存至：

```
docs/design/{feature_name}_test_cases.md
```

### 3.4 步骤三：用例设计

#### 3.4.1 目标
践行 TDD 原则，在编码之前完成 Alpha 用例设计，用例基于业务规则编写。

#### 3.4.2 用例格式

设计文档中的测试用例文件统一采用以下格式：

```
测试用例文件：{path}/test_{module}.py
框架：pytest（与项目现有测试框架一致）

每个用例应包含：
1. 用例编号（TC-{module}-{seq}）
2. 测试目标（对应的业务规则编号）
3. 前置条件
4. 输入数据
5. 预期输出
6. 边界条件说明
```

#### 3.4.3 用例设计要点

- 每个业务规则至少有 1 个对应的测试用例
- 每个测试用例必须有明确的预期结果（不是"检查是否正确"）
- 覆盖正常流程、边界条件、异常处理
- 用例不需要在这一步实现代码，只要定义清楚输入和预期输出

### 3.5 步骤四：任务分解

#### 3.5.1 判断标准

如果开发任务满足以下**任意一条**，则必须进行任务分解：

1. 预计新增/修改文件超过 3 个
2. 预计代码总量超过 300 行
3. 涉及多个独立功能模块
4. 存在数据库 schema 变更 + 业务逻辑修改
5. 前后端均有修改

简单任务（单文件小修改）可以跳过此步骤。

#### 3.5.2 分解规则

1. 每个子任务是一个独立可完成的开发单元
2. 子任务之间的依赖关系明确（可与现有 SubAgent DAG 并行执行设计联动）
3. 每个子任务产出物清晰（具体到文件和方法）
4. 子任务粒度：每个子任务预计 50-150 行代码

#### 3.5.3 子任务输出格式

```
子任务列表：
┌──────────┬──────────────────────────┬────────────┬──────────────────┐
│ 子任务ID  │ 描述                      │ 依赖        │ 产出物            │
├──────────┼──────────────────────────┼────────────┼──────────────────┤
│ TASK-01  │ 创建 User 数据模型        │ 无          │ models/user.py   │
│ TASK-02  │ 实现 UserService 业务逻辑  │ TASK-01    │ services/user.py │
│ TASK-03  │ 实现 User API 接口        │ TASK-02    │ api/user.py      │
│ TASK-04  │ UT: User 模型单元测试      │ TASK-01    │ tests/test_user  │
│ TASK-05  │ UT: UserService 单元测试   │ TASK-02    │ tests/test_user  │
│ TASK-06  │ UT: User API 集成测试      │ TASK-03    │ tests/test_api   │
└──────────┴──────────────────────────┴────────────┴──────────────────┘
```

### 3.6 步骤五：代码开发

#### 3.6.1 目标
根据设计文档逐个完成子任务的代码实现。

#### 3.6.2 开发流程（单个子任务）

```
┌─────────────┐
│  选择子任务   │
└──────┬──────┘
       │
       ▼
┌─────────────────┐
│ 读取相关代码文件  │  ← 使用 read_code（确保读取完整函数/类）
└──────┬──────────┘
       │
       ▼
┌─────────────────┐
│ 编写 UT 用例      │  ← 根据步骤三的用例设计，先编写测试代码（RED）
│                 │     使用 write_code 创建/追加 UT 文件
└──────┬──────────┘
       │
       ▼
┌─────────────────┐
│ 实现代码         │  ← 使用 update_code（修改已有文件）
│                 │     或 write_code（创建新文件）
│                 │     超过 200 行时分段写入（GREEN）
└──────┬──────────┘
       │
       ▼
┌─────────────────┐
│ 执行测试验证      │  ← 使用 execute_command 运行 pytest
└──────┬──────────┘
       │
       ├─ 通过 ──→ 下一个子任务
       │
       └─ 失败 ──→ 分析原因 → 修改代码 → 重新验证
                   （最多重试 3 次，仍失败则报告用户协助）
```

#### 3.6.3 代码编写规范

- **单次写入限制**：`update_code` 和 `write_code` 单次写入不超过 200 行代码
- **分段写入**：如需写入大量代码，将文件内容拆分为多个逻辑段，依次写入
- **追加模式**：`write_code` 支持 append 模式，用于逐步构建大文件
- **编码风格**：匹配目标文件的现有编码风格（缩进、命名规范、注释密度）

### 3.7 步骤六：测试验证

#### 3.7.1 目标
通过执行 UT 用例验证代码功能正确性。

#### 3.7.2 验证标准

**刚性要求**：所有 UT 用例必须 100% 通过，代码才算开发完成。

#### 3.7.3 失败处理流程

```
测试失败
    → 解析 pytest 输出，按错误类型分类处理：

    【断言失败 AssertionError】→ 逻辑错误
        → 使用 grep_context 搜索失败相关的函数名
        → 使用 read_code 读取相关代码完整上下文
        → 使用 update_code 进行精准修复
        → 使用 execute_command 重新运行测试验证
        → 允许 3 次重试

    【导入/依赖错误 ImportError / ModuleNotFoundError】→ 缺少模块
        → 检查缺少哪个模块，使用 write_code 补充
        → 允许 2 次重试

    【环境错误】（pytest 未安装、Python 版本不匹配等）
        → 立即向用户报告，不重试

    → 全部重试累计不超过 5 次（跨所有失败用例）
    → 超过重试上限仍失败 → 向用户报告当前状态和已排查的方向
```

### 3.8 步骤七：返回结果

#### 3.8.1 目标
向用户清晰汇报任务完成情况。

#### 3.8.2 返回内容

```
✅ 任务完成

## 修改文件列表
| 文件路径 | 操作 | 说明 |
|---------|------|------|
| src/models/user.py | 新增 | 用户数据模型 |
| src/services/user.py | 新增 | 用户业务逻辑 |
| src/api/user.py | 新增 | 用户 API 接口 |
| tests/test_user.py | 新增 | 用户模块单元测试（12 个用例全部通过） |

## 测试结果
- UT 用例总数：12
- 通过：12 ✅
- 失败：0
- 执行时间：2.3s

## 设计文档
详细设计文档已保存至：docs/design/user_module_design.md
```

---

## 4. 新增工具设计

### 4.1 工具总览

| 工具名称 | 用途 | 风险等级 | 所属文件 |
|---------|------|---------|---------|
| `glob_file` | 按 glob 模式查找文件 | low | ai_coding_tools.py |
| `grep_context` | 项目全局关键词/正则检索代码 | low | ai_coding_tools.py |
| `read_code` | 按范围/上下文读取代码 | low | ai_coding_tools.py |
| `update_code` | 精准修改已有文件中的代码 | medium | ai_coding_tools.py |
| `write_code` | 创建新文件或追加写入代码 | medium | ai_coding_tools.py |
| `execute_command` | 执行命令（环境检查/测试验证） | medium | ai_coding_tools.py |

### 4.2 glob_file —— 文件查找

#### 4.2.1 设计说明

在项目中按 glob 模式查找文件，返回匹配的文件路径列表。**纯 Python 实现，禁止调用 `ls`/`find` 等系统命令**，确保跨平台兼容。

#### 4.2.2 工具定义

```python
@tool(
    "glob_file",
    "按 glob 模式查找文件并返回匹配的文件路径列表。"
    "参数 pattern: glob 匹配模式（如 '**/*.py'），必选；"
    "path: 搜索起始目录，可选，默认使用会话当前目录",
    risk_level="low",
)
def glob_file(pattern: str, path: str = ".") -> str:
    ...
```

#### 4.2.3 参数说明

| 参数 | 类型 | 必选 | 说明 |
|------|------|------|------|
| `pattern` | string | 是 | glob 匹配模式，如 `**/*.py`、`src/**/test_*.py`、`*.md` |
| `path` | string | 否 | 搜索起始目录。不填使用会话默认目录 |

#### 4.2.4 核心逻辑

```
glob_file(pattern, path)
    ├─ 1. 解析并规范化搜索目录 path
    ├─ 2. 使用 pathlib.Path.rglob() 递归匹配文件
    ├─ 3. 使用 fnmatch 过滤匹配 pattern 的文件
    ├─ 4. 排除常见的无关目录（.git / __pycache__ / .pytest_cache / node_modules / .venv）
    ├─ 5. 按文件路径排序
    └─ 6. 返回结果（最多返回 200 条，超过则截断并提示）
```

#### 4.2.5 跨平台保证

- 使用 `pathlib.Path` 处理所有路径操作（自带跨平台兼容）
- 路径比较使用 `Path` 对象，不使用字符串拼接
- 排除目录列表同时包含 Unix 和 Windows 风格常见目录名

#### 4.2.6 输出格式

```
找到 15 个匹配 "**/*.py" 的文件：

src/graph_agent/tools/ai_coding_tools.py
src/graph_agent/tools/base.py
src/graph_agent/tools/file_tools.py
...
```

### 4.3 grep_context —— 代码内容检索

#### 4.3.1 设计说明

在项目中搜索代码定义和引用，支持关键词和正则表达式，返回匹配内容所在的文件路径和行号。本次通过 Python 实现，将来考虑引入 LSP 工具实现更高效的语义检索。

#### 4.3.2 工具定义

```python
@tool(
    "grep_context",
    "在项目中全局搜索代码内容，支持关键词和正则表达式检索。"
    "返回匹配内容的文件路径、行号和代码片段。"
    "参数 pattern: 搜索模式（关键词或正则），必选；"
    "path: 搜索目录，可选；"
    "glob: 文件名过滤模式（如 '*.py'），可选；"
    "output_mode: 输出模式（content / files_with_matches / count），可选，默认 content；"
    "max_results: 最大返回结果数，可选，默认 50",
    risk_level="low",
)
def grep_context(
    pattern: str,
    path: str = ".",
    glob: str = "",
    output_mode: str = "content",
    max_results: int = 50,
) -> str:
    ...
```

#### 4.3.3 参数说明

| 参数 | 类型 | 必选 | 说明 |
|------|------|------|------|
| `pattern` | string | 是 | 搜索模式，支持正则。如 `def get_user`、`class.*Repository`、`import.*os` |
| `path` | string | 否 | 搜索目录，默认当前目录 |
| `glob` | string | 否 | 文件名过滤，如 `*.py` 仅搜索 Python 文件。注意：实现时参数命名与 Python 标准库 `glob` 模块冲突，建议在工具内部使用 `file_glob` 变量名或在函数内局部 import |
| `output_mode` | string | 否 | `content`（含代码片段）/ `files_with_matches`（仅文件路径）/ `count`（命中计数）|
| `max_results` | integer | 否 | 最大返回条数，默认 50，最大 200 |

#### 4.3.4 核心逻辑

```
grep_context(pattern, path, glob, output_mode, max_results)
    ├─ 1. 尝试编译正则表达式，无效正则返回错误
    ├─ 2. 在 path 目录下递归遍历所有文件
    ├─ 3. 排除二进制文件和超过 1MB 的文件
    ├─ 4. 按 glob 过滤文件名
    ├─ 5. 逐行搜索匹配内容
    ├─ 6. 根据 output_mode 格式化输出
    └─ 7. 返回结果（最多 max_results 条）
```

#### 4.3.5 设计要点

`grep_context` 与通用文本搜索工具不同，它专为**代码语义检索**设计：

| 特性 | 说明 |
|------|------|
| 定位 | **代码语义检索**——面向函数定义、类定义、变量引用、接口位置等代码实体 |
| 输出增强 | 自动标注匹配类型（函数定义/类定义/变量/import/函数调用） |
| 上下文展示 | 匹配行前后上下文（可选展示前后各 2 行） |
| 多模式输出 | content（含代码片段）/ files_with_matches（仅文件路径）/ count（命中计数） |

#### 4.3.6 输出增强

`grep_context` 在输出时会尝试自动标注匹配类型：

```
src/models/user.py:15: def get_user(user_id: int) -> User:    [函数定义]
src/services/user.py:42: user = get_user(uid)                  [函数调用]
src/api/user.py:23: from models.user import User               [导入语句]
src/models/user.py:8: class User(BaseModel):                   [类定义]
```

**重要说明**：上述标注基于启发式正则匹配（如 `def ` 前缀 → 函数定义、`class ` 前缀 → 类定义），**非语法解析**，存在误报可能。以下场景标注可能不准确：
- 字符串或注释中包含的 `def`/`class` 关键字
- 函数调用的匹配基于函数名出现位置，可能包含同名变量引用
- 装饰器、类型注解中的类名引用可能被误标为 import

LLM 在使用标注结果时，应结合 `read_code` 读取上下文进行二次确认，不应完全依赖标注类型。

### 4.4 read_code —— 代码读取

#### 4.4.1 设计说明

从指定文件读取代码，支持两种读取模式：
- **范围模式**（`context_line=0`）：读取 `start_line` 到 `end_line` 之间的内容
- **上下文模式**（`context_line>0`）：以 `context_line` 为中心，读取前后各约 75 行的内容

**核心特性：自动扩展至完整的函数/类边界**，通过解析缩进格式来判断代码块起始和结束位置，确保 LLM 每次读取到完整的代码单元而非零碎片段。

#### 4.4.2 工具定义

```python
@tool(
    "read_code",
    "从指定文件读取代码片段。支持两种模式："
    "1) 范围模式（context_line=0）：读取 start_line 至 end_line 的内容；"
    "2) 上下文模式（context_line>0）：以 context_line 为中心读取前后代码。"
    "会自动扩展范围至完整函数/类边界。"
    "参数 path: 文件路径，必选；"
    "start_line: 起始行号，可选；"
    "end_line: 终止行号，可选；"
    "context_line: 关键字所在行号，可选，默认 0",
    risk_level="low",
)
def read_code(
    path: str,
    start_line: int = 0,
    end_line: int = 0,
    context_line: int = 0,
) -> str:
    ...
```

#### 4.4.3 参数说明

| 参数 | 类型 | 必选 | 说明 |
|------|------|------|------|
| `path` | string | 是 | 文件路径（绝对或相对） |
| `start_line` | integer | 否 | 读取起始行号（1-based） |
| `end_line` | integer | 否 | 读取终止行号（1-based） |
| `context_line` | integer | 否 | 关键字所在行号（1-based），默认 0 表示使用范围模式 |

#### 4.4.4 读取量控制

| 模式 | 默认范围 | 推荐行数 | 上限 |
|------|---------|---------|------|
| 范围模式 | start_line 至 end_line | 150-250 行 | 600 行 |
| 上下文模式 | context_line ± 75 行 | 150 行 | 600 行 |

**上限检查**：
- 超过 600 行时，自动截断至上限范围
- 超过 50000 字符时，在 50000 字符处截断（保证 JSON 序列化安全）
- 截断时在输出末尾添加提示：`... (内容已截断，共 X 行，显示前 Y 行)`

#### 4.4.5 智能边界扩展算法

```
read_code(path, start_line, end_line, context_line)
    ├─ 1. 读取文件全部内容，按行分割
    ├─ 2. 确定初始读取范围
    │     ├─ context_line > 0: [context_line - 75, context_line + 75]
    │     └─ context_line = 0: [start_line, end_line]
    ├─ 3. 智能边界扩展（关键逻辑——初始窗口仅决定"兴趣区域"，最终范围由边界扩展决定）
    │     ├─ 向上扩展：从初始范围起始行向上逐行扫描
    │     │     ├─ 定位函数/类头部：向上寻找最近的 def / class / async def（同级或更外层缩进）
    │     │     ├─ 找到后将该行作为新的起始行（保留装饰器）
    │     │     └─ 若当前已处于函数定义行（缩进 0 的 def/class），不再向上扩展
    │     ├─ 向下扩展：从初始范围终止行向下逐行扫描
    │     │     ├─ 寻找代码块结束：向下扫描直到遇到缩进级别回退到函数/类定义的同级
    │     │     │   （即下一个顶级 def/class 或文件末尾）
    │     │     └─ 将范围扩展至函数/类体的最后一行（含 return / 尾括号等）
    │     └─ 扩展后再次检查行数上限（不超过 600 行）
    │           └─ 超出上限时优先保留向上扩展的结果（确保函数签名完整），截断尾部
    ├─ 4. 检查字符数上限（不超过 50000 字符）
    ├─ 5. 添加行号前缀（格式：行号|代码内容）
    └─ 6. 返回带标注的代码片段
```

#### 4.4.6 缩进解析说明

- **Python**：使用行首空格数判断缩进级别（4 空格 = 1 级）
- **JavaScript/TypeScript**：使用 `{` `}` 大括号配对判断代码块边界
- **Go/Rust**：使用 `{` `}` 大括号配对
- **通用策略**：优先检测文件扩展名，使用对应语言的缩进规则；未知类型回退到统一缩进检测（首非空字符的列偏移）

#### 4.4.7 输出格式

```
=== read_code: src/services/user.py, 行 45-195 (共 151 行) ===

45| def get_user_by_id(user_id: int) -> Optional[User]:
46|     """根据 ID 查询用户。
47|
48|     Args:
49|         user_id: 用户唯一标识
50|
51|     Returns:
52|         User 对象，不存在时返回 None
53|
54|     Raises:
55|         ValueError: user_id 无效（<=0）
56|     """
57|     if user_id <= 0:
58|         raise ValueError(f"无效的用户ID: {user_id}")
59|
60|     with get_db_session() as session:
61|         user = session.query(User).filter(User.id == user_id).first()
...
195|         return user

=== 文件共 320 行，以上为 45-195 行 ===
```

### 4.5 update_code —— 代码修改

#### 4.5.1 设计说明

精准修改已有文件中的代码，通过**唯一字符串匹配**实现精准替换，专门针对代码场景优化：
- 支持缩进感知的匹配
- 修改后返回修改位置的上下文
- 提供更详细的错误提示

#### 4.5.2 工具定义

```python
@tool(
    "update_code",
    "精准修改已有代码文件。通过 old_string 唯一匹配实现精准替换。"
    "参数 path: 文件路径，必选；"
    "old_string: 待替换的原代码字符串（需唯一匹配），必选；"
    "new_string: 替换后的新代码字符串，必选",
    risk_level="medium",
)
def update_code(path: str, old_string: str, new_string: str) -> str:
    ...
```

#### 4.5.3 参数说明

| 参数 | 类型 | 必选 | 说明 |
|------|------|------|------|
| `path` | string | 是 | 要修改的文件路径 |
| `old_string` | string | 是 | 需要被替换的原代码（必须与文件中文本完全一致，包括缩进和换行） |
| `new_string` | string | 是 | 替换后的新代码 |

#### 4.5.4 安全规则

1. **唯一匹配**：`old_string` 必须在文件中出现且仅出现一次，多次出现时返回错误并要求增加上下文
2. **非空校验**：`old_string` 不能为空
3. **写入量限制**：`new_string` 一次不超过 200 行（约 5000 字符），超过时要求分段修改
4. **路径安全**：复用 `_resolve_path` 拒绝路径遍历攻击
5. **风险等级**：medium（文件写入操作，执行于进程隔离环境）

#### 4.5.5 实现说明

`update_code` 实现核心逻辑：
- 读取目标文件内容
- 校验 `old_string` 在文件中出现且仅出现一次
- 执行字符串精准替换
- 写回文件

在此基础上增加代码专用的增强能力：
- 代码修改量的提示（新增/删除/修改行数统计）
- 修改后返回修改位置附近的代码上下文（前后各 3 行）
- 针对代码缩进的感知验证

#### 4.5.6 输出格式

```
✅ 已修改：src/services/user.py（行 45-57）

变更统计：修改 13 行（删除 3 行，新增 5 行，不变 8 行）

修改后上下文：
42|
43|
44|
45| def get_user_by_id(user_id: int) -> Optional[User]:
46|     """根据 ID 查询用户。"""
47|     if user_id <= 0:
48|         raise ValueError(f"无效的用户ID: {user_id}")
49|
50|     # 新增：缓存查询
51|     cached = cache.get(f"user:{user_id}")
52|     if cached:
53|         return cached
54|
55|     with get_db_session() as session:
56|         user = session.query(User).filter(User.id == user_id).first()
57|
58|
59|
```

### 4.6 write_code —— 代码写入

#### 4.6.1 设计说明

对于需要创建新文件或 `update_code` 无法满足的写入场景（如完全重写一个大文件），通过 `write_code` 实现。支持**覆盖写入**和**追加写入**两种模式。

#### 4.6.2 工具定义

```python
@tool(
    "write_code",
    "创建新文件或写入代码内容。"
    "参数 path: 文件路径，必选；"
    "content: 要写入的代码内容，必选；"
    "mode: 写入模式（'overwrite' 覆盖 / 'append' 追加），可选，默认 'overwrite'",
    risk_level="medium",
)
def write_code(path: str, content: str, mode: str = "overwrite") -> str:
    ...
```

#### 4.6.3 参数说明

| 参数 | 类型 | 必选 | 说明 |
|------|------|------|------|
| `path` | string | 是 | 文件路径（文件不存在时自动创建父目录） |
| `content` | string | 是 | 要写入的代码内容 |
| `mode` | string | 否 | `overwrite`（覆盖写入）/ `append`（追加写入），默认 `overwrite` |

#### 4.6.4 安全规则

1. **分段写入**：单次 `content` 不超过 200 行。如需写入大文件，分多次调用
2. **覆盖保护**：`overwrite` 模式下，如果文件已存在且有内容，记录旧文件行数
3. **追加模式**：`append` 模式自动在内容末尾添加换行（如内容不以换行结尾）
4. **路径安全**：复用 `_resolve_path`，自动创建父目录

#### 4.6.5 分段写入策略

当文件预计超过 200 行时，推荐的分段策略：

```
1. 第一次调用 write_code(path, first_part, mode="overwrite")
   → 写入文件头部（import、类定义、前 N 个方法）

2. 第 N 次调用 write_code(path, next_part, mode="append")
   → 追加后续方法/代码段

3. 最后一次调用 write_code(path, last_part, mode="append")
   → 追加尾部代码
```

#### 4.6.6 设计要点

`write_code` 专门为代码文件写入设计：

| 特性 | 说明 |
|------|------|
| 写入量限制 | 单次 ≤ 200 行，超过需分段写入 |
| 双模式 | `overwrite`（覆盖写入）/ `append`（追加写入） |
| 代码感知 | 写入后返回插入位置的上下文 |
| 自动创建 | 父目录不存在时自动创建 |

### 4.7 execute_command —— 命令执行

#### 4.7.1 设计说明

执行命令来检查环境或执行测试验证代码的正确性。

#### 4.7.2 工具定义

```python
@tool(
    "execute_command",
    "执行命令来检查环境或运行测试验证。"
    "参数 command: 要执行的命令，必选；"
    "working_dir: 工作目录，可选，默认使用项目根目录；"
    "timeout: 超时时间（秒），可选，默认 60 秒",
    risk_level="medium",
)
def execute_command(command: str, working_dir: str = "", timeout: int = 60) -> str:
    ...
```

#### 4.7.3 参数说明

| 参数 | 类型 | 必选 | 说明 |
|------|------|------|------|
| `command` | string | 是 | 要执行的命令（支持管道和重定向） |
| `working_dir` | string | 否 | 命令执行的工作目录，默认项目根目录 |
| `timeout` | integer | 否 | 超时秒数，默认 60，最大 300 |

#### 4.7.4 典型使用场景

```bash
# 运行所有测试
execute_command("pytest tests/ -v")

# 运行特定测试文件
execute_command("pytest tests/test_user.py -v")

# 运行特定测试用例
execute_command("pytest tests/test_user.py::test_get_user_by_id -v")

# 检查环境
execute_command("python --version")
execute_command("pip list | grep pytest")

# 语法检查
execute_command("python -m py_compile src/services/user.py")

# 代码格式化检查
execute_command("ruff check src/")
```

#### 4.7.5 安全限制

- 禁止执行包含 `rm -rf` / `sudo` / `chmod 777` 等危险操作的命令
- 禁止执行网络请求类命令（如 `curl` / `wget`）
- `timeout` 上限 300 秒，防止长时间阻塞
- 返回内容截断至 10000 字符

---

## 5. ai-coding Skill 设计

### 5.1 Skill 元数据（frontmatter）

```yaml
---
name: ai-coding
type: builtin
description: AI Coding 智能编程助手，支持需求分析、方案设计、TDD开发、代码实现与测试验证的完整开发生命周期。适用于新功能开发、Bug修复、代码重构、项目脚手架搭建等编程任务
tools:
  - name: glob_file
    description: 按 glob 模式查找文件，返回匹配的文件路径列表
    parameters:
      - name: pattern
        type: string
        description: glob 匹配模式，如 **/*.py
        required: true
      - name: path
        type: string
        description: 搜索起始目录，默认当前目录
        required: false
  - name: grep_context
    description: 项目中全局搜索代码内容，支持关键词和正则
    parameters:
      - name: pattern
        type: string
        description: 搜索模式（关键词或正则表达式）
        required: true
      - name: path
        type: string
        description: 搜索目录
        required: false
      - name: glob
        type: string
        description: 文件名过滤模式
        required: false
      - name: output_mode
        type: string
        description: 输出模式（content / files_with_matches / count）
        required: false
      - name: max_results
        type: integer
        description: 最大返回结果数
        required: false
  - name: read_code
    description: 从指定文件读取代码，自动扩展至完整函数/类边界
    parameters:
      - name: path
        type: string
        description: 文件路径
        required: true
      - name: start_line
        type: integer
        description: 起始行号
        required: false
      - name: end_line
        type: integer
        description: 终止行号
        required: false
      - name: context_line
        type: integer
        description: 关键字所在行号
        required: false
  - name: update_code
    description: 精准修改已有代码文件（通过唯一字符串匹配替换）
    parameters:
      - name: path
        type: string
        description: 文件路径
        required: true
      - name: old_string
        type: string
        description: 待替换的原代码字符串
        required: true
      - name: new_string
        type: string
        description: 替换后的新代码字符串
        required: true
  - name: write_code
    description: 创建新文件或追加写入代码内容
    parameters:
      - name: path
        type: string
        description: 文件路径
        required: true
      - name: content
        type: string
        description: 要写入的代码内容
        required: true
      - name: mode
        type: string
        description: 写入模式（overwrite / append）
        required: false
  - name: execute_command
    description: 执行命令检查环境或运行测试
    parameters:
      - name: command
        type: string
        description: 要执行的命令
        required: true
      - name: working_dir
        type: string
        description: 工作目录
        required: false
      - name: timeout
        type: integer
        description: 超时秒数
        required: false
  - name: ask_user
    description: 向用户提问澄清需求或请求确认
    parameters:
      - name: question
        type: string
        description: 向用户展示的问题
        required: true
      - name: options
        type: array
        items:
          type: string
        description: 供用户选择的选项列表（字符串数组）
        required: false
      - name: require_approval
        type: boolean
        description: 是否要求用户批准/拒绝
        required: false
max_iterations: 20
llm_config:
  temperature: 0.3
---
```

### 5.2 Skill 执行流程

Skill Body 中包含以下核心 Prompt 指引。实际编写 `ai-coding.md` 时可根据需要调整措辞和补充示例。

---

#### 入口判断：步骤跳过逻辑

```
## 流程入口判断

在开始执行前，先判断用户当前处于哪个阶段，跳过已完成的步骤：

1. 用户直接发出开发指令（如"帮我写一个 xxx"）→ 从阶段一开始
2. 用户提供了完整的设计文档（如"按这个设计实现"）→ 跳过阶段一二，从阶段三开始
   - 仍需要核对设计文档是否包含业务规则和技术方案两部分
3. 用户指定了小范围修改（如"给 xxx 函数加个参数校验"）→ 跳过阶段二三四，从阶段五开始
   - 单文件、单函数、≤ 50 行的修改视为小范围
4. 用户要求调试/修复测试失败 → 直接进入阶段六的失败分析流程
5. 不确定时应从阶段一开始（宁可多做需求澄清，不可跳过）

### 跳步声明
跳过步骤时，必须先向用户声明跳过了哪些步骤及原因：
"检测到您已提供[设计文档/明确需求]，本次将跳过[步骤X]，直接从[步骤Y]开始。"
```

---

#### 阶段一：需求澄清

```
## 阶段一：需求澄清

### 目标
准确理解用户意图，确保需求无二义性后方可进入设计阶段。

### 执行规则
1. 仔细阅读用户指令，提取：核心目标、功能范围、技术约束、交付标准
2. 对照以下"需求完备性检查清单"逐项评估：
   - [ ] 要实现什么功能？（一句话说清核心目标）
   - [ ] 目标用户/使用场景是什么？
   - [ ] 输入是什么？输出是什么？
   - [ ] 有哪些约束条件？（语言、框架、性能、兼容性等）
   - [ ] 边界条件和异常情况有哪些？
3. 如果检查清单中有 ≥2 项无法明确回答，调用 ask_user 向用户澄清
4. 一次最多提 3 个问题；问题必须具体，不得笼统（如"请详细描述"）
5. 澄清轮次上限为 2 轮；超过 2 轮仍有不明确项，对非关键项使用默认值并告知用户
6. 需求确认后，用 2-3 句话总结需求要点，请用户确认（调用 ask_user require_approval=true）
```

---

#### 阶段二：方案设计

```
## 阶段二：方案设计

### 目标
输出完整的设计文档，严格区分业务规则与技术实现。

### 业务规则部分（必须先写）
- 用自然语言描述业务流程（必要时用 ASCII 流程图）
- 列出所有业务规则，编号为 BR-01, BR-02, ...（每条可追溯）
- 定义输入输出的数据格式和字段约束
- 列举所有边界条件和异常场景
- 说明与外部系统的交互契约（只定义接口行为，不涉及实现细节）

### 技术实现部分（后写，基于业务规则）
- 技术选型及理由（用户未指定时默认 Python 3.12+）
- 模块/类设计，描述职责和关系
- 数据模型（数据库表结构或内存数据结构）
- 关键算法说明
- 文件目录规划（新增和修改的文件列表）

### 产出物
- 使用 write_code 将设计文档保存至 docs/design/{feature_name}_design.md

### 红线
- 禁止在业务规则中写"使用 Redis 缓存"（这是实现），应写"查询结果应在 5 秒内返回"（这是规则）
- 禁止在技术方案中重新定义业务逻辑
```

---

#### 阶段三：用例设计

```
## 阶段三：用例设计（Alpha）

### 目标
基于业务规则编写测试用例设计文档，定义每个用例的输入和预期输出。本阶段不写测试代码，只定义用例规范。

### 用例格式
每个用例包含：用例编号（TC-{module}-{seq}）、对应的业务规则编号、前置条件、输入数据、预期输出、边界说明。

### 设计要求
- 每个业务规则（BR-xx）至少对应 1 个测试用例
- 覆盖正常流程、边界条件（空值、极限值）、异常处理（非法输入、超时等）
- 预期结果必须可验证（具体值/状态，不得写"检查是否正确"）
- 用例不需要在步骤三实现代码——只定义清楚"测什么、怎么测、期望什么"

### 产出物
- 使用 write_code 将测试用例设计文档保存至 docs/design/{feature_name}_test_cases.md
```

---

#### 阶段四：任务分解

```
## 阶段四：任务分解（条件执行）

### 触发条件
满足以下任意一条即必须进行任务分解：
1. 预计新增/修改文件超过 3 个
2. 预计代码总量超过 300 行
3. 涉及多个独立功能模块
4. 存在数据库 schema 变更 + 业务逻辑修改

简单任务可跳过本阶段，直接进入阶段五。

### 分解规则
- 每个子任务是一个独立可完成的开发单元（预计 50-150 行代码）
- 明确子任务间的依赖关系
- 每个子任务产出物清晰（具体到文件路径和方法签名）
- 子任务粒度确保可独立测试

### 产出物
- 子任务列表（ID / 描述 / 依赖 / 产出物 / 关联的用例编号）
```

---

#### 阶段五：代码开发

```
## 阶段五：代码开发

### 开发策略
对每个子任务执行以下循环：

1. **了解上下文**：使用 read_code 读取需修改的文件（确保读到完整函数/类）
2. **搜索参考**：使用 grep_context 搜索已有相关函数/类定义
3. **编写测试**：根据阶段三的用例设计，使用 write_code 创建/追加 UT 用例
   → 确认 UT 此时运行会失败（RED）
4. **实现代码**：使用 update_code（修改已有文件）或 write_code（创建新文件）
5. **立即验证**：使用 execute_command 运行该子任务相关的 UT 用例
   → 通过（GREEN）→ 下一个子任务
   → 失败 → 分析原因 → 修改代码 → 重新验证（单子任务最多重试 3 次）

### 分段写入规范
- 单次 update_code / write_code 写入量 ≤ 200 行
- 超过 200 行时，按逻辑边界分段（类定义 → 方法1 → 方法2 → ...）
- 分段写入顺序：1. imports + 类头 → 2. 方法组1 → 3. 方法组2 → ...
- 每段写入后，确认上一段已成功写入再继续
- 使用 glob_file 确认文件的当前状态

### 代码风格
- 匹配目标文件的现有编码风格（缩进宽度、命名规范、注释密度）
- 新文件采用 Python PEP 8 规范
```

---

#### 阶段六：测试验证

```
## 阶段六：测试验证（集成验证）

### 目标
所有子任务完成后，执行完整的测试套件，确保各模块协作正常。

### 执行步骤
1. 使用 execute_command 运行全部 UT：
   execute_command("pytest tests/ -v --tb=short")
2. 分析测试输出：
   - 全部通过 → 进入阶段七
   - 部分失败 → 进入失败分析流程

### 失败分析流程
1. 解析 pytest 输出，按以下策略分类处理：
   - 断言失败（AssertionError）→ 定位逻辑错误
     · 使用 grep_context 搜索失败相关的函数名
     · 使用 read_code 读取相关代码
     · 使用 update_code 精准修复
     · 重新 execute_command 验证（允许 3 次重试）
   - 导入/依赖错误（ImportError/ModuleNotFoundError）
     → 检查缺少哪个模块，使用 write_code 补充
     · 允许 2 次重试
   - 环境错误（如 pytest 未安装、Python 版本不匹配）
     → 立即报告用户，不重试
2. 全部重试累计不超过 5 次（跨所有失败用例）
3. 超过重试上限仍失败 → 进入阶段七，如实报告失败用例和已排查的方向
```

---

#### 阶段七：返回结果

```
## 阶段七：返回结果

### 输出模板
向用户输出以下结构化报告：

**成功时：**
- ✅ 任务完成摘要（1 句话）
- 修改文件列表（文件路径 / 操作类型 / 说明）
- 测试结果（UT 总数 / 通过数 / 失败数 / 执行时间）
- 设计文档位置
- 如有未覆盖的边界条件，列出

**部分失败时：**
- ⚠️ 任务部分完成
- 已完成部分（同成功格式）
- 失败部分（失败用例 / 错误信息 / 已排查方向）
- 建议的后续排查步骤

**全部失败时：**
- ❌ 任务未完成
- 失败原因分析
- 已尝试的修复措施
- 需要用户介入的问题
```

---

#### 工具使用规范

```
## 工具使用规范

### glob_file 使用约束
- 仅用于查找项目内的源代码文件（.py / .js / .ts / .go 等）
- 不要用于搜索 node_modules / .venv / __pycache__ 等目录
- 找不到文件时检查 pattern 是否正确，必要时调整

### grep_context 使用约束
- 优先搜索函数/类定义位置（pattern 使用 def xxx / class xxx）
- 标注结果仅供参考，关键函数务必用 read_code 读取完整定义确认
- 当结果超过 50 条时，缩小搜索范围（添加 glob 过滤或缩小 path）

### read_code 使用约束
- 每次读取推荐 150-250 行，上限 600 行
- 读取前先估算文件大小（用 glob_file 查看文件基本信息）
- context_line 模式优先于 start_line/end_line 模式（更易用）
- 如果返回内容被截断，缩小范围重新读取

### update_code 使用约束
- old_string 必须从文件中逐字复制（包括缩进和换行），不可凭记忆构造
- 先用 read_code 读取目标片段，再从读取结果中复制 old_string
- 遇到"不唯一匹配"错误时增加上下文字符直到唯一

### write_code 使用约束
- write_code 可用于写入代码文件（.py 等）和设计文档（.md），两者均受 ≤ 200 行约束
- 新建文件先用 mode="overwrite"，追加内容用 mode="append"
- 单次写入 ≤ 200 行，超出的内容分多次调用
- 写入后立即用 read_code 验证写入内容的正确性

### execute_command 使用约束
- 仅用于执行测试（pytest）、语法检查（py_compile）、代码检查（ruff）
- 禁止用于安装依赖（pip install）、修改环境、网络操作
- 执行前确认工作目录正确
```
---

## 6. 数据流图

### 6.1 整体数据流

```
┌──────────┐
│ 用户需求   │
│ (自然语言) │
└────┬─────┘
     │
     ▼
┌─────────────────────────────────────────────────────────────────┐
│                     AI Coding Skill                             │
│                                                                 │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐    │
│  │ 步骤一    │   │ 步骤二    │   │ 步骤三    │   │ 步骤四    │    │
│  │ 需求澄清  │──→│ 方案设计  │──→│ 用例设计  │──→│ 任务分解  │    │
│  │          │   │          │   │          │   │(条件执行) │    │
│  └──────────┘   └────┬─────┘   └──────────┘   └────┬─────┘    │
│                      │                             │           │
│              设计文档 │                   子任务列表 │           │
│              (MD文件) │                   (JSON)    │           │
│                      ▼                             ▼           │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐                   │
│  │ 步骤七    │   │ 步骤六    │   │ 步骤五    │                   │
│  │ 返回结果  │←──│ 测试验证  │←──│ 代码开发  │←──────────────────│
│  │          │   │          │   │          │                   │
│  └──────────┘   └──────────┘   └──────────┘                   │
│       │              │              │                          │
│       │        测试报告       代码文件 + UT                     │
│       ▼                                                       │
│  ┌──────────┐                                                  │
│  │ 用户      │                                                  │
│  │ (接收结果) │                                                  │
│  └──────────┘                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 6.2 工具调用关系

```
步骤一：需求澄清
    └── ask_user（向用户提问）
    └── read_code（可选，查看已有代码了解上下文）

步骤二：方案设计
    └── glob_file（了解项目结构）
    └── grep_context（搜索已有相关代码）
    └── read_code（读取关键代码模块）
    └── write_code（保存设计文档到 docs/ 目录）

步骤三：用例设计
    └── read_code（读取设计文档）
    └── write_code（保存测试用例设计文档）

步骤四：任务分解
    └── read_code（读取设计文档）
    └── write_code（保存子任务分解结果）

步骤五：代码开发（每个子任务）
    └── read_code（读取需要修改的代码文件）
    └── grep_context（搜索相关函数/类的定义和引用）
    └── glob_file（查找相关文件）
    └── update_code（修改已有文件）
    └── write_code（创建新文件）
    └── write_code（编写 UT 用例文件）

步骤六：测试验证
    └── execute_command（运行 pytest）
    └── read_code（测试失败时读取相关代码分析原因）
    └── grep_context（定位错误相关代码位置）
    └── update_code（修改代码修复问题）
    └── execute_command（重新运行测试验证）

步骤七：返回结果
    └── （无需工具调用，直接输出结构化结果）
```

### 6.3 失败重试流程

```
┌──────────────────┐
│  execute_command │
│   → pytest ...   │
└────────┬─────────┘
         │
         ├── 全部通过 (exit_code=0) ──→ 步骤七：返回结果 ✅
         │
         └── 部分失败 (exit_code≠0) ──→ ┌──────────────────────┐
                                         │ 分析测试输出           │
                                         │ 定位失败用例           │
                                         └──────────┬───────────┘
                                                    │
                                         ┌──────────▼───────────┐
                                         │ grep_context          │
                                         │ 搜索失败相关的函数/类   │
                                         └──────────┬───────────┘
                                                    │
                                         ┌──────────▼───────────┐
                                         │ read_code             │
                                         │ 读取问题代码           │
                                         └──────────┬───────────┘
                                                    │
                                         ┌──────────▼───────────┐
                                         │ update_code           │
                                         │ 精准修复               │
                                         └──────────┬───────────┘
                                                    │
                                         ┌──────────▼───────────┐
                                         │ execute_command       │
                                         │ 重新执行测试           │
                                         └──────────┬───────────┘
                                                    │
                                         ┌──────────▼───────────┐
                                         │ 重试次数 < 3 ?        │
                                         ├── 是 ──→ 回到分析步骤  │
                                         └── 否 ──→ 向用户报告    │
                                                    │
                                         ┌──────────▼───────────┐
                                         │ 步骤七：返回结果       │
                                         │ （报告当前状态+问题）   │
                                         └──────────────────────┘
```

---

## 7. 安全设计

### 7.1 工具风险等级

| 工具 | 风险等级 | 执行方式 | 说明 |
|------|---------|---------|------|
| `glob_file` | low | 直接执行 | 只读操作，无副作用 |
| `grep_context` | low | 直接执行 | 只读操作，无副作用 |
| `read_code` | low | 直接执行 | 只读操作，无副作用 |
| `update_code` | medium | 进程隔离 | 文件写入，需审计 |
| `write_code` | medium | 进程隔离 | 文件写入，需审计 |
| `execute_command` | medium | 进程隔离 | 命令执行，需参数过滤 |

### 7.2 路径安全检查

所有涉及文件路径的工具统一调用 `_resolve_path` 进行安全检查：

```python
def _resolve_path(path: str) -> Path:
    """解析并规范化路径，拒绝路径遍历攻击和跨平台无效路径。"""
    if sys.platform == "win32" and path.startswith(("/proc/", "/sys/", "/dev/")):
        raise ValueError(f"路径 '{path}' 是 Linux 系统路径，当前环境为 Windows")

    # 1. 先在原始路径上检查 ..（Path.resolve() 会将其消除，须在此之前检测）
    if ".." in Path(path).parts:
        raise ValueError("路径包含非法的上级目录引用")

    # 2. 解析为绝对路径
    resolved = Path(path).resolve()

    # 3. 校验解析后的路径仍在项目工作目录范围内
    project_root = Path(os.getenv("PROJECT_ROOT", Path.cwd())).resolve()
    if not str(resolved).startswith(str(project_root)):
        raise ValueError(f"路径 '{path}' 越过了项目根目录范围")

    return resolved
```

### 7.3 命令执行安全

`execute_command` 需要额外安全检查：

```python
# 危险命令模式黑名单
_DANGEROUS_COMMANDS = [
    r"rm\s+-rf",           # 递归强制删除
    r"sudo\s",             # 提权
    r"chmod\s+777",        # 危险权限
    r"mkfs\.",            # 格式化文件系统
    r"dd\s+if=",           # 磁盘写入
    r":(){ :|:& };:",        # Fork 炸弹
    r"shutdown",           # 关机
    r"reboot",             # 重启
    r"curl\s",             # 网络请求
    r"wget\s",             # 网络下载
]
```

### 7.4 代码写入量限制

防止 LLM 一次生成过多低质量代码：

| 工具 | 单次写入行数上限 | 单次字符数上限 |
|------|----------------|---------------|
| `update_code` | 200 行 | 10000 字符 |
| `write_code` | 200 行 | 10000 字符 |

---

## 8. 与现有系统集成

### 8.1 工具注册

新增工具放置在 `src/graph_agent/tools/ai_coding_tools.py`，由 `ToolCenter.auto_discover()` 自动发现注册，无需额外配置。

### 8.2 Skill 注册

`ai-coding.md` 放置在 `prompts/skills/`，由 `SkillLoader.load_all()` 自动加载，无需额外配置。

### 8.3 与 SubAgent 系统的关系

步骤四（任务分解）输出的子任务列表，理论上可以通过 SubAgent 系统并行执行。但 **AI Coding Skill 的初始版本采用串行执行模式**（在当前 Skill 的迭代循环中逐个完成子任务），原因如下：

1. **上下文依赖**：代码开发存在强上下文依赖，适合串行推进
2. **质量可控**：串行执行便于在每个子任务完成后立即进行测试验证
3. **调试友好**：出错时更容易定位和修复

后续版本可考虑将独立子任务通过 SubAgent 并行派发（需配合已有的 DAG 并行执行能力）。

### 8.4 与记忆系统的关系

AI Coding 执行过程中产生的设计文档和测试报告可存储到记忆系统（参见 `docs/18.记忆系统设计.md`），供后续会话参考：

- 设计文档 → `reference` 类型记忆
- 项目架构认知 → `project` 类型记忆
- 用户编码偏好（如技术栈选择）→ `user` 类型记忆

---

## 9. 扩展性考虑

### 9.1 多语言支持

当前设计以 Python 为主要目标语言（与 GraphAgent 技术栈一致）。后续可扩展支持：

- JavaScript / TypeScript（需增加对应的缩进解析逻辑）
- Go / Rust（需增加大括号配对的代码边界检测）
- Java / Kotlin（需增加类结构解析）

### 9.2 LSP 集成

`grep_context` 工具当前基于正则搜索。未来可引入 LSP（Language Server Protocol）支持，实现：

- 精确的符号查找（go to definition）
- 引用查找（find all references）
- 语义级的代码补全建议
- 实时代码诊断（lint/type check）

LSP 集成点：`src/graph_agent/tools/lsp_tools.py`（新文件，不影响现有 `grep_context`）

### 9.3 代码审查增强

在步骤五和步骤六之间，可增加可选的 **代码审查** 环节（复用已有的 `code-review` Skill）：

```
步骤五：代码开发
    → 代码审查（可选，由用户配置启用）
    → 步骤六：测试验证
```

### 9.4 Git 工作流集成

可增加自动 Git 操作支持：
- 每个子任务完成后自动 commit
- 所有子任务完成后自动创建 PR
- 测试失败时自动回滚

---

## 10. 实施计划

| 阶段 | 任务 | 产出物 | 优先级 |
|------|------|--------|--------|
| 1 | 实现 `glob_file` 工具 | `ai_coding_tools.py`（部分） | P0 |
| 2 | 实现 `grep_context` 工具 | `ai_coding_tools.py`（部分） | P0 |
| 3 | 实现 `read_code` 工具（含智能边界扩展） | `ai_coding_tools.py`（部分） | P0 |
| 4 | 实现 `update_code` 工具 | `ai_coding_tools.py`（部分） | P0 |
| 5 | 实现 `write_code` 工具 | `ai_coding_tools.py`（部分） | P0 |
| 6 | 实现 `execute_command` 工具 | `ai_coding_tools.py`（部分） | P0 |
| 7 | 编写 `ai-coding` Skill Prompt | `prompts/skills/ai-coding.md` | P0 |
| 8 | 工具单元测试 | `tests/unit_tests/test_ai_coding_tools.py` | P1 |
| 9 | 端到端集成测试（模拟完整 AI Coding 流程） | `tests/integration_tests/test_ai_coding.py` | P1 |
| 10 | 文档完善：更新 README 技能列表 | `README.md` | P2 |

---

## 11. 附录

### 11.1 工具实现优先级矩阵

```
高价值 / 低复杂度 → 优先实现
─────────────────────────────────
glob_file        ★★★★  (纯文件遍历，简单)
read_code        ★★★☆  (缩进解析有复杂度)
grep_context     ★★★☆  (正则搜索，中等)
write_code       ★★★   (支持覆盖/追加双模式)
update_code      ★★★   (字符串精准替换)
execute_command  ★★☆   (安全命令执行封装)
```

### 11.2 关键指标

| 指标 | 目标值 |
|------|--------|
| 需求澄清回合数 | ≤ 3 轮 |
| 单次 read_code 代码量 | 150-250 行（推荐），≤ 600 行（上限） |
| 单次 write_code/update_code 代码量 | ≤ 200 行 |
| UT 通过率要求 | 100% |
| 测试失败重试次数 | ≤ 3 次 |
| Skill 最大迭代次数 | 20 次 |
| 命令执行超时 | 60 秒（默认），300 秒（上限） |
