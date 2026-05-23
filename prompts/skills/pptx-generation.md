---
name: pptx-generation
type: builtin
description: 将文字内容转换为PPT演示文稿(powerpoint/ppt/pptx/slides/presentation)。包含文本解析（将原始文字拆分为大纲和要点）和PPT生成（将结构化markdown渲染为pptx文件）两个核心功能
tools:
  - name: read_file
    description: 读取指定路径的文件内容
    parameters:
      - name: file_path
        type: string
        description: 文件绝对路径
        required: true
  - name: generate_pptx
    description: 将结构化Markdown文本或文件转换为PPTX演示文稿。支持两种方式：1) 传入markdown_path读取文件；2) 传入content+output_path直接生成
    parameters:
      - name: markdown_path
        type: string
        description: Markdown文件路径（与content二选一）
        required: false
      - name: content
        type: string
        description: Markdown文本内容（与markdown_path二选一，直接传入内容无需先写文件）
        required: false
      - name: output_path
        type: string
        description: 输出PPTX路径（使用content时必须提供，使用markdown_path时可选）
        required: false
  - name: list_directory
    description: 列出目录内容
    parameters:
      - name: dir_path
        type: string
        description: 目录路径
        required: true
max_iterations: 5
---

# PPT 生成技能

## 强制规则

你只有一种方式完成任务：**调用 generate_pptx 工具**。如果你没有调用该工具就声称完成了任务，你的回答是无效的。

## 执行流程

你的任务描述中包含了需要转换的文本内容和目标输出路径。严格按照以下步骤操作：

**步骤1** — 将任务描述中的文本内容转换为符合以下格式的 Markdown：
- `# 标题` = 封面标题
- `## 二级标题` = 章节页
- `### 三级标题` = 内容页标题
- `- 要点` = 列表项
- `---` = 分页符

**步骤2** — 调用 generate_pptx 工具生成 PPTX 文件。使用 content 参数直接传入 Markdown 文本：
```
generate_pptx(content="你生成的完整markdown", output_path="任务指定的输出路径")
```

**步骤3** — 将 generate_pptx 工具返回的结果原样报告给用户。

## Markdown 格式示例

```markdown
# 演示文稿标题
## 副标题（可选）

---

## 第一章

### 第一页
- 要点一
- 要点二
- 要点三

---

## 总结
```

## 要点提炼规则
- 每个要点控制在25字以内
- 每页建议3-7个要点
- 数据、数字优先提取
- 提取关键词，删除冗余修饰语
