---
name: repo-deep-dive
type: user
description: 获取 GitHub 每日/每周热榜并生成带中文深度介绍的增强版热榜报告。自动抓取热榜前10项目，读取每个项目的 README，由 LLM 生成中文项目介绍（功能/解决的问题/使用的方法），最终输出替换了原始英文描述的中文热榜 Markdown 报告。
tools:
  - name: mcp__github__fetch_github_trending
    description: 抓取 GitHub Trending 页面热榜信息，保存为 Markdown 文件。参数 output_dir(输出目录)、since(daily/weekly/monthly)、language(编程语言过滤)
    parameters:
      - name: output_dir
        type: string
        description: Markdown 文件输出目录
        required: false
      - name: since
        type: string
        description: 时间范围 daily/weekly/monthly
        required: false
      - name: language
        type: string
        description: 编程语言过滤，为空表示不限
        required: false
  - name: read_file
    description: 读取指定路径的文件内容
    parameters:
      - name: file_path
        type: string
        description: 文件绝对路径
        required: true
  - name: mcp__github__fetch_repo_readme
    description: 获取一个或多个 GitHub 仓库的 README.md 内容。参数 repo_urls(逗号分隔的仓库URL列表)、max_length_per_repo(每个README最大字符数)
    parameters:
      - name: repo_urls
        type: string
        description: GitHub 仓库 URL 列表，以英文逗号分隔
        required: true
      - name: max_length_per_repo
        type: int
        description: 每个 README 最大返回字符数
        required: false
  - name: write_file
    description: 将内容写入指定路径的文件
    parameters:
      - name: file_path
        type: string
        description: 文件绝对路径
        required: true
      - name: content
        type: string
        description: 要写入的内容
        required: true
max_iterations: 10
---

# GitHub 热榜深度调研技能

## 强制规则

你必须严格按照以下 5 个步骤依次执行。**禁止跳过任何步骤**。每完成一步，在回复中明确告知用户当前进度。

---

## 执行流程

### 步骤1 — 抓取 GitHub 热榜

调用 `mcp__github__fetch_github_trending` 工具抓取热榜数据。

- `since` 参数：如果用户指定了"每周"或"weekly"，使用 `weekly`，否则默认使用 `daily`
- `language` 参数：如果用户指定了编程语言则传入，否则不传（不限语言）
- `output_dir` 参数：使用默认值即可

工具将返回生成的文件路径。**记住这个文件路径，下一步要用。**

进度提示：`步骤1/5 完成：已抓取 GitHub 热榜数据并保存为 Markdown 文件。`

---

### 步骤2 — 读取热榜文件，提取前10仓库 URL

调用 `read_file` 读取步骤1生成的热榜 Markdown 文件。

从文件内容中提取**前10个**上榜仓库的 URL。URL 格式为 `https://github.com/owner/repo`，出现在每个 `## N. [owner/repo](URL)` 标题行中。

将这 10 个 URL 拼接为一个以英文逗号 `,` 分隔的字符串，供步骤3使用。

进度提示：`步骤2/5 完成：已从热榜中提取前10个仓库的 URL。`

---

### 步骤3 — 获取前10仓库的 README 详细介绍

调用 `mcp__github__fetch_repo_readme`，将步骤2拼接好的 URL 字符串作为 `repo_urls` 参数传入。

- `max_length_per_repo` 建议设为 6000，确保获取足够的项目信息

工具将返回每个仓库的 README 文本内容。**完整保留这些返回结果，下一步要用。**

进度提示：`步骤3/5 完成：已获取前10个仓库的 README 详细介绍。`

---

### 步骤4 — LLM 分析 README 并生成中文介绍

你需要仔细阅读步骤3返回的每个仓库 README 内容，为每个项目生成一段**中文介绍**。

**介绍要求**（每个项目 80-150 字）：

1. **项目功能**：这个项目是做什么的？给谁用的？
2. **解决的问题**：它解决了什么痛点或满足了什么需求？
3. **使用的方法**：核心技术栈或实现方式是什么？有什么亮点？

**格式要求**：

- 每个项目一段完整的中文叙述，不分点列举
- 语言自然流畅，像一位技术博主在向读者推荐这个项目
- 避免直接翻译英文描述，要用自己的话重新组织
- 保留关键技术术语的英文原名（如 API、CLI、LLM 等）

进度提示：`步骤4/5 完成：已为前10个项目生成中文介绍。`

---

### 步骤5 — 替换 Description、写回文件并输出最终报告

回到步骤2读取的原始热榜 Markdown 内容，对前10个项目，将每个项目下的英文 `- **Description**: ...` 行**替换**为中文版本：

```markdown
- **中文介绍**: <步骤4生成的中文介绍>
```

同时保留原始的英文 Description 行，但在其前面加 `<!-- 原始 -->` 将其变为 HTML 注释，方便用户对照：

```markdown
<!-- 原始 - **Description**: The original English description... -->
- **中文介绍**: <步骤4生成的中文介绍>
```

**其他字段保持不变**（Stars、Forks、Language 等）。

修改完成后，调用 `write_file` 将完整的 Markdown 内容**写回步骤1生成的文件**，覆盖原文件。这样用户打开热榜文件时就能看到带中文介绍的增强版报告。

最后将完整的 Markdown 内容直接展示给用户。不要省略任何部分，用户需要看到完整报告。

进度提示：`步骤5/5 完成：已生成带中文深度介绍的热榜报告并写回文件。`

---

## 注意事项

1. **网络延迟**：步骤3抓取 10 个仓库的 README 可能需要一些时间（工具内部有多 URL 间隔），请耐心等待工具返回结果，不要重复调用
2. **Token 消耗**：10 个 README 的文本量较大，如果你的上下文窗口有限，可以适当降低步骤3的 `max_length_per_repo` 到 4000
3. **前10不等于全部**：热榜文件可能包含超过 10 个仓库，但你只需要对前10个进行深度介绍，第11个及之后保持原样即可
4. **使用默认值**：对于 `output_dir`、`max_length_per_repo` 等参数，若无特殊要求直接使用工具默认值，不需要显式传入
