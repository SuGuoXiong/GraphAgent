# GraphAgent 文本转PPT功能设计文档

## 1. 概述

### 1.1 设计背景

在日常工作中，用户经常需要将大段文字（会议纪要、调研报告、教案、演讲稿等）快速转换为结构清晰的PPT演示文稿。LLM天然擅长文本理解和结构化整理，而python-pptx提供了成熟的PPTX生成能力。将二者结合，可以大幅降低用户制作PPT的时间成本。

### 1.2 核心设计理念

```
用户输入（原始文本）
    → Skill 阶段一：文本解析（LLM 拆分大纲、梳理层级、提炼要点）
    → 中间产物：结构化 Markdown 文件（约定格式）
    → Skill 阶段二：PPT 生成（调用 generate_pptx tool，python-pptx 渲染）
    → 最终产物：.pptx 文件
```

**为什么分两阶段？**

1. **可审查性**：中间产出的 Markdown 文件可被用户审阅和手动调整，用户可以修改后再触发PPT生成，也可以跳过阶段一直接提供自己写的 Markdown
2. **职责清晰**：LLM的强项是文本理解/结构化（阶段一），python-pptx的强项是渲染排版（阶段二），各司其职
3. **可复用**：`generate_pptx` 工具可被其他 Skill 或流程直接调用（只要有符合约定的 Markdown 文件）

### 1.3 与现有系统的关系

- 新增工具 `generate_pptx` 注册到 `ToolCenter`，与其他工具地位相同，受 RBAC 和审计系统约束
- 新增内置 Skill `pptx-generation` 遵循现有 Skill 规范（YAML frontmatter + Markdown body）
- 不修改任何现有模块，纯增量开发

## 2. 目录结构

```
src/graph_agent/tools/
    └── pptx_tools.py              # 新增：generate_pptx 工具实现

prompts/skills/
    └── pptx-generation.md         # 新增：PPT 生成 Skill 定义

docs/
    └── 14.文本转PPT功能设计.md     # 本文档
```

## 3. Markdown → PPTX 约定格式

`generate_pptx` 工具解析以下 Markdown 语法并映射到 PPTX 元素：

| Markdown 语法 | PPTX 映射 | 说明 |
|---|---|---|
| `# 标题` | 封面页（Title Slide） | 仅第一个 `#`，居中大标题 + 副标题（可选） |
| `## 章节名` | 章节分隔页（Section Slide） | 居中章节标题，用于PPT大板块切换 |
| `### 页面标题` | 内容页标题 | 作为当前幻灯片的主标题 |
| `- 条目` / `* 条目` | 正文要点（Bullet Points） | 缩进层级 `-` = 一级，`  -` = 二级，`    -` = 三级 |
| `1. 条目` | 编号列表 | 有序列表 |
| `![描述](路径)` | 图片 | 嵌入本地图片（路径相对于 Markdown 文件所在目录） |
| `---` | 显式分页 | 强制另起一张幻灯片 |
| `> 引用文字` | 引用文本框 | 特殊格式的引用/备注区块 |
| 普通段落 | 正文文本框 | 作为幻灯片正文内容 |

### 3.1 分页规则

1. 每个 `###` 开始一张新的内容页幻灯片
2. `---` 显式分页符，其后内容另起一张幻灯片
3. `##` 章节标题独占一张章节分隔页
4. 第一个 `#` 独占一张封面页

### 3.2 示例 Markdown 输入

```markdown
# 2025年度市场分析报告
## 汇报人：张三

---

## 第一部分：市场概况

### 行业现状
- 全球市场规模：约500亿美元
- 年复合增长率：12.3%
- 主要参与者：
  - 头部企业A（市场份额 23%）
  - 头部企业B（市场份额 18%）
  - 其他（市场份额 59%）

### 竞争格局
- 竞争维度分析：
  1. 产品创新能力
  2. 渠道覆盖广度
  3. 客户服务满意度
- 新进入者威胁：中等

---

## 第二部分：战略建议

### 核心战略方向
- 加大研发投入，聚焦AI赋能
- 拓展海外市场，重点关注东南亚
- 建立生态合作伙伴体系

### 执行路线图
- Q1：完成技术可行性验证
- Q2：MVP产品上线
- Q3：首批客户落地
- Q4：规模化推广

---

## 总结与展望
## 谢谢！
```

## 4. generate_pptx 工具设计

### 4.1 工具注册

```python
# src/graph_agent/tools/pptx_tools.py

@tool(
    "generate_pptx",
    "将结构化Markdown文件转换为PPTX演示文稿。参数 markdown_path: Markdown文件路径, output_path: 输出PPTX路径（可选，默认与markdown同目录同名）",
    risk_level="medium",
)
def generate_pptx(markdown_path: str, output_path: str = "") -> str:
    ...
```

### 4.2 核心流程

```
generate_pptx(markdown_path, output_path)
  │
  ├─ 1. 读取 Markdown 文件内容
  │
  ├─ 2. 逐行解析 Markdown 结构
  │     ├─ 识别标题层级（#, ##, ###）
  │     ├─ 识别列表项（-, *, 1.）
  │     ├─ 识别分页符（---）
  │     └─ 识别图片/引用等特殊元素
  │
  ├─ 3. 构建幻灯片数据结构
  │     slides = [
  │       {"type": "title", "title": "...", "subtitle": "..."},
  │       {"type": "section", "title": "..."},
  │       {"type": "content", "title": "...", "items": [...]},
  │       ...
  │     ]
  │
  ├─ 4. 使用 python-pptx 逐页渲染
  │     ├─ 封面页：标题 + 副标题，居中
  │     ├─ 章节页：章节标题，居中
  │     └─ 内容页：标题 + 正文列表/段落
  │
  ├─ 5. 保存 PPTX 文件
  │
  └─ 6. 返回结果（文件路径 + 页数统计）
```

### 4.3 幻灯片布局

| 幻灯片类型 | 使用的 Slide Layout | 排版规则 |
|---|---|---|
| 封面页 | `Title Slide` (layout 0) | Title = 主标题, Subtitle = 副标题 |
| 章节页 | `Section Header` (layout 2) | Title = 章节名 |
| 内容页（纯文字） | `Title and Content` (layout 1) | Title = 页面标题, Content = 列表/段落 |
| 内容页（含图片） | `Two Content` (layout 3) 或自定义 | 左侧文字，右侧图片 |

### 4.4 样式配置

工具内置一套默认样式，保证视觉效果统一：

| 元素 | 默认样式 |
|---|---|
| 封面标题 | 字号 44pt，加粗，深蓝色 (#1a3a5c)，居中 |
| 封面副标题 | 字号 24pt，浅灰色 (#666666)，居中 |
| 章节标题 | 字号 36pt，加粗，深蓝色，居中 |
| 内容页标题 | 字号 32pt，加粗，深蓝色，左对齐 |
| 一级要点 | 字号 20pt，黑色，左对齐，圆点符号 |
| 二级要点 | 字号 18pt，深灰色，缩进一级 |
| 正文段落 | 字号 16pt，黑色，1.5倍行距 |
| 引用文字 | 字号 14pt，斜体，灰色，左侧加竖线 |

### 4.5 安全与风险控制

- **风险等级**：medium（文件读写操作，不涉及网络或系统命令）
- **路径校验**：与现有文件工具使用同一 `_resolve_path` 函数，拒绝路径遍历攻击
- **文件大小限制**：读取 Markdown 文件上限 1MB
- **输出路径**：仅允许写入到项目工作目录范围内

### 4.6 错误处理

| 错误场景 | 返回信息 |
|---|---|
| 文件不存在 | `错误: Markdown文件不存在 - {path}` |
| python-pptx 未安装 | `错误: 缺少依赖库 python-pptx，请执行 pip install python-pptx` |
| Markdown 格式无法解析 | `错误: Markdown文件解析失败 - {reason}` |
| 输出路径无写入权限 | `错误: 无法写入输出文件 - {path}` |
| 文件过大 | `错误: Markdown文件超过1MB大小限制` |

## 5. pptx-generation Skill 设计

### 5.1 Skill 元数据（frontmatter）

```yaml
---
name: pptx-generation
type: builtin
description: 将文字内容转换为PPT演示文稿。包含文本解析（将原始文字拆分为大纲和要点）和PPT生成（将结构化markdown渲染为pptx文件）两个核心功能
tools:
  - read_file        # 读取待转换文本文件
  - write_file       # 写入中间产物 Markdown 文件
  - generate_pptx    # 核心：Markdown → PPTX 转换
  - search_content   # 辅助：搜索已有内容
  - ask_user         # 用户交互：确认大纲、指定样式等
max_iterations: 10
---
```

### 5.2 执行流程

```
用户请求："把这篇会议纪要转成PPT"
  │
  ├─ 场景一：用户提供文本/文件路径
  │     ├─ 1. read_file 读取原始文本
  │     ├─ 2. LLM 分析文本 → 梳理结构 → 生成约定格式的 Markdown
  │     ├─ 3. write_file 保存中间 Markdown 文件
  │     ├─ 4. （可选）ask_user 询问用户是否审阅/修改 Markdown
  │     ├─ 5. generate_pptx 将 Markdown 转换为 PPTX
  │     └─ 6. 返回 PPTX 文件路径给用户
  │
  ├─ 场景二：用户直接提供 Markdown 文件
  │     └─ 1. generate_pptx 将 Markdown 转换为 PPTX
  │
  └─ 场景三：用户要求调整已有 PPT 内容
        ├─ 1. read_file 读取已有 Markdown
        ├─ 2. LLM 根据用户要求修改 Markdown
        ├─ 3. write_file 保存更新后的 Markdown
        └─ 4. generate_pptx 重新生成 PPTX
```

### 5.3 LLM 文本解析提示词要点

Skill body 中将包含详细的提示词指引，要求 LLM 在解析文本时遵循以下原则：

1. **层级梳理**：
   - 识别文本的自然段落和逻辑边界
   - 提取核心主题作为 `#` 一级标题（封面）
   - 将大板块映射为 `##` 章节标题
   - 将每个子主题映射为 `###` 页面标题

2. **要点提炼**：
   - 每个要点控制在 20 字以内（PPT 展示不宜过多文字）
   - 每页幻灯片建议 3-7 个要点
   - 数据/数字优先提取为要点
   - 保留关键名词和动词，删除冗余修饰

3. **格式规范**：
   - 严格遵循约定的 Markdown 格式
   - 使用 `---` 在章节之间分页
   - 封面页包含标题和可选副标题
   - 避免在一个 `###` 下堆砌过多内容（超过 10 个要点应拆分）

## 6. 数据流图

```
┌──────────┐   原始文本    ┌──────────────┐   结构化 Markdown    ┌──────────────┐
│  用户输入  │ ──────────→ │  LLM 文本解析  │ ──────────────────→ │  write_file  │
│ (文本/文件) │             │  (阶段一)     │                     │  (中间产物)   │
└──────────┘             └──────────────┘                     └──────┬───────┘
                                                                     │
                                                              ┌──────▼───────┐
                                                              │  Markdown    │
                                                              │  (可审阅)     │
                                                              └──────┬───────┘
                                                                     │
                                                              ┌──────▼───────┐
                                                              │ generate_pptx│
                                                              │  (阶段二)     │
                                                              │ python-pptx  │
                                                              └──────┬───────┘
                                                                     │
                                                              ┌──────▼───────┐
                                                              │   .pptx 文件  │
                                                              │  (最终产物)   │
                                                              └──────────────┘
```

## 7. 依赖与配置

### 7.1 Python 依赖

```txt
python-pptx>=1.0.0
```

### 7.2 工具注册

`generate_pptx` 放置在 `src/graph_agent/tools/pptx_tools.py`，由 `ToolCenter.auto_discover()` 自动发现注册，无需额外配置。

### 7.3 Skill 注册

`pptx-generation.md` 放置在 `prompts/skills/`，由 `SkillLoader.load_all()` 自动加载，无需额外配置。

## 8. 扩展性考虑

### 8.1 自定义模板

后续可在 `generate_pptx` 中增加 `template_path` 参数，允许用户指定自定义的 .pptx 模板文件作为母版，工具在模板基础上填充内容而非从零创建。

### 8.2 多格式输出

工具架构与输出格式解耦，未来可扩展支持 Google Slides API、Keynote 等格式，只需新增工具（如 `generate_google_slides`），Skill 的文本解析阶段无需修改。

### 8.3 图表支持

后续可在 Markdown 约定格式中增加图表语法（如 ` ```chart ``` `），`generate_pptx` 解析后使用 python-pptx 的 chart 功能生成柱状图、饼图等。

## 9. 实施计划

| 阶段 | 任务 | 产出物 |
|------|------|--------|
| 1 | 实现 `generate_pptx` 工具（Markdown 解析 + python-pptx 渲染） | `src/graph_agent/tools/pptx_tools.py` |
| 2 | 编写 `pptx-generation` Skill 定义（frontmatter + 执行步骤 + 提示词） | `prompts/skills/pptx-generation.md` |
| 3 | 集成测试：端到端验证"原始文本 → PPTX 文件"全流程 | 测试用例 |
| 4 | 文档完善：更新 README 中的技能列表 | README.md |
