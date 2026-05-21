---
name: file-management
type: builtin
description: 文件系统读写与管理，支持文本文件的读取、写入、追加、删除，目录遍历，文件存在性检查
tools:
  - name: read_file
    description: 读取指定路径的文件内容，支持 UTF-8 / GBK 编码
    parameters:
      - name: path
        type: string
        description: 文件绝对路径
        required: true
  - name: write_file
    description: 将内容写入指定文件，如文件不存在则创建
    parameters:
      - name: path
        type: string
        description: 文件绝对路径
        required: true
      - name: content
        type: string
        description: 要写入的内容
        required: true
  - name: append_file
    description: 将内容追加到文件末尾
    parameters:
      - name: path
        type: string
        description: 文件绝对路径
        required: true
      - name: content
        type: string
        description: 要追加的内容
        required: true
  - name: list_directory
    description: 列出目录内容
    parameters:
      - name: path
        type: string
        description: 目录路径
        required: true
  - name: delete_file
    description: 删除指定文件
    parameters:
      - name: path
        type: string
        description: 文件绝对路径
        required: true
  - name: delete_directory
    description: 删除指定目录
    parameters:
      - name: path
        type: string
        description: 目录绝对路径
        required: true
  - name: file_exists
    description: 检查文件或目录是否存在
    parameters:
      - name: path
        type: string
        description: 文件或目录路径
        required: true
  - name: ask_user
    description: 向用户提问或请求确认
    parameters:
      - name: question
        type: string
        description: 向用户展示的问题
        required: true
      - name: options
        type: array
        description: 供用户选择的选项列表
        required: false
      - name: require_approval
        type: boolean
        description: 是否要求用户批准/拒绝
        required: false
max_iterations: 3
---

# 文件管理技能

## 功能概述
对本地文件系统进行安全的读写操作，支持文本文件的常见操作和目录管理。

## 适用场景
- 需要读取文件内容并提取信息
- 需要将生成的结果保存到文件
- 需要列出目录结构或检查文件是否存在
- 需要执行文件删除或批量文件操作

## 执行步骤

### 场景一：读取并分析文件内容
1. 使用 `read_file` 工具读取目标文件内容
2. 分析文件内容，提取用户需要的信息
3. 如果文件编码异常，尝试不同编码重新读取
4. 将分析结果返回

### 场景二：写入文件
1. 确认目标目录是否存在，必要时使用 `list_directory` 检查
2. 使用 `write_file` 工具将内容写入指定路径
3. 使用 `read_file` 工具验证写入结果
4. 返回写入结果确认

### 场景三：删除文件或目录
1. 使用 `ask_user` 工具请求用户确认删除操作（require_approval=true）
2. 用户批准后，使用 `delete_file` 或 `delete_directory` 执行删除
3. 返回操作结果

## 注意事项
- 需要用户提供文件路径或确认危险操作时，必须调用 `ask_user` 工具
- 调用 ask_user 前先检查任务描述和上下文中是否已有所需信息
- 不会删除包含重要系统文件的目录
- 写入前确认目标路径的安全性和合法性
