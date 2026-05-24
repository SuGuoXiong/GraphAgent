---
name: repo-deep-dive
type: user
description: 获取 GitHub 每日/每周热榜并生成带中文深度介绍的增强版热榜报告。自动抓取热榜前10项目，读取每个项目的 README，由 LLM 生成中文项目介绍（功能/解决的问题/使用的方法），最终输出替换了原始英文描述的中文热榜 Markdown 报告。
tools:
  - name: mcp__github__fetch_github_trending
    description: 抓取 GitHub Trending 页面热榜信息，保存为 Markdown 文件。参数 output_dir(输出目录)、since(daily/weekly/monthly)、language(编程语言过滤)、limit(返回项目数量上限，0表示不限制)
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
      - name: limit
        type: int
        description: 返回项目数量上限，设为 0 表示不限制
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

你必须严格按照以下 6 个步骤依次执行。**禁止跳过任何步骤**。每完成一步，在回复中明确告知用户当前进度。

---

## 执行流程

### 步骤1 — 抓取 GitHub 热榜

调用 `mcp__github__fetch_github_trending` 工具抓取热榜数据。

- `limit` 参数：设为 `10`，只获取前10个项目
- `since` 参数：如果用户指定了"每周"或"weekly"，使用 `weekly`，否则默认使用 `daily`
- `language` 参数：如果用户指定了编程语言则传入，否则不传（不限语言）
- `output_dir` 参数：使用默认值即可

工具将返回生成的文件路径。**记住这个文件路径，下一步要用。**

进度提示：`步骤1/6 完成：已抓取 GitHub 热榜数据并保存为 Markdown 文件。`

---

### 步骤2 — 读取热榜文件，提取前10仓库 URL

调用 `read_file` 读取步骤1生成的热榜 Markdown 文件。

从文件内容中提取**前10个**上榜仓库的 URL。URL 格式为 `https://github.com/owner/repo`，出现在每个 `## N. [owner/repo](URL)` 标题行中。

将这 10 个 URL 拼接为一个以英文逗号 `,` 分隔的字符串，供步骤3使用。

进度提示：`步骤2/6 完成：已从热榜中提取前10个仓库的 URL。`

---

### 步骤3 — 获取前10仓库的 README 详细介绍

调用 `mcp__github__fetch_repo_readme`，将步骤2拼接好的 URL 字符串作为 `repo_urls` 参数传入。

- `max_length_per_repo` 建议设为 6000，确保获取足够的项目信息

工具将返回每个仓库的 README 文本内容。**完整保留这些返回结果，下一步要用。**

进度提示：`步骤3/6 完成：已获取前10个仓库的 README 详细介绍。`

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

进度提示：`步骤4/6 完成：已为前10个项目生成中文介绍。`

---

### 步骤5 — 重写文件

步骤1生成的热榜 Markdown 文件格式较为基础，你需要用步骤4生成的中文介绍**重新构建整个 Markdown 文件**，使用以下美化格式。

调用 `write_file` 将新内容**写回步骤1生成的文件**，覆盖原文件。

**输出格式模板**（严格按照此模板）：

```
📊 GitHub 每日热榜深度报告（YYYY-MM-DD）

---

## 🥇 1. [owner/repo](https://github.com/owner/repo)
⭐ Stars数 | 🍴 Forks数 | 🔵 语言

> <步骤4生成的该项目中文介绍>

---

## 🥈 2. [owner/repo](https://github.com/owner/repo)
⭐ Stars数 | 🍴 Forks数 | 🔵 语言

> <步骤4生成的该项目中文介绍>

---

...（前3名使用 🥇🥈🥉 奖牌 emoji，第4~10名使用普通数字）

---

## 4. [owner/repo](https://github.com/owner/repo)
⭐ Stars数 | 🍴 Forks数 | 🔵 语言

> <步骤4生成的该项目中文介绍>
```

**格式要点**：

- **标题**：`📊 GitHub 每日热榜深度报告（YYYY-MM-DD）`，日期使用步骤1生成文件的实际日期。如果是"weekly"则写"每周"，"monthly"则写"每月"
- **每项标题**：前3名用 🥇🥈🥉 emoji，第4~10名用普通数字序号
- **元数据行**：`⭐ Stars数 | 🍴 Forks数 | 🔵 语言`，数据从步骤2读取的原始文件中提取。日增 star 数拼在 star 总数后面，如 `⭐ 23,387 (今日 +3,987)`
- **中文介绍**：用 `>` 引用块包裹步骤4生成的中文介绍，一段连续文字不分点
- **项目间**：用 `---` 分隔
- **仅输出前10个**项目，不要包含第11个之后的内容

进度提示：`步骤5/6 完成：已用美化格式重写热榜 Markdown 文件。`

---

### 步骤6 — 趋势分析与点评，输出最终报告

基于前5个步骤中获取的全部信息，对热榜前10的项目进行**趋势分析与点评**，追加到步骤5的 Markdown 文件末尾，再将完整内容返回给用户。

**趋势分析格式**（追加到 Markdown 文件末尾）：

```
### 📌 每日热榜趋势总结

1. **主题一** — 具体分析内容...
2. **主题二** — 具体分析内容...
3. **主题三** — 具体分析内容...
4. **主题四** — 具体分析内容...
```

**分析维度**（不需要标号罗列，融合为一个连贯的趋势总结）：

1. **整体趋势总结**（3-5 句）：本期热榜呈现出什么样的技术趋势？哪些领域或方向受到最多关注？有什么值得关注的集体动向？

2. **亮点项目点评**（每项 2-3 句）：从热榜前10中挑选 2-3 个最有代表性或最值得关注的项目，说明它们的创新点、价值主张、以及为什么值得投入时间关注。不要逐个罗列全部 10 项，聚焦最有亮点的项目。

3. **趋势信号解读**（2-3 句）：这些上榜项目反映出开发者和产业正在关注什么？对大模型应用、开发工具、基础设施等领域有什么暗示？

**输出方式**：

调用 `write_file` 将趋势分析内容**追加写入**步骤5的 Markdown 文件末尾（不要覆盖，追加即可），确保文件包含步骤5的完整热榜报告 + 趋势分析。

然后将**文件完整内容**（步骤5的热榜报告 + 步骤6的趋势分析）直接展示给用户。用户需要看到完整的报告，不要省略。

进度提示：`步骤6/6 完成：已完成趋势分析点评，输出最终报告。`

---

## 注意事项

1. **网络延迟**：步骤3抓取 10 个仓库的 README 可能需要一些时间（工具内部有多 URL 间隔），请耐心等待工具返回结果，不要重复调用
2. **Token 消耗**：10 个 README 的文本量较大，如果你的上下文窗口有限，可以适当降低步骤3的 `max_length_per_repo` 到 4000
3. **前10不等于全部**：步骤1通过 `limit=10` 限制了热榜文件只包含前 10 个项目，无需额外截断
4. **使用默认值**：对于 `output_dir`、`max_length_per_repo` 等参数，若无特殊要求直接使用工具默认值，不需要显式传入
