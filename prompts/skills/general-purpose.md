---
name: general-purpose
type: builtin
description: 通用任务执行与文本生成，处理需要工具操作的复杂任务，包括文本生成与分析、文件操作、计算、时间查询、命令执行、网页抓取、JSON处理及多工具组合任务。如果是简单对话/问候/闲聊，请使用 chat 技能
tools:
  - name: read_file
    description: 读取指定路径的文件内容
    parameters:
      - name: path
        type: string
        description: 文件绝对路径
        required: true
  - name: write_file
    description: 将内容写入指定文件
    parameters:
      - name: path
        type: string
        description: 文件绝对路径
        required: true
      - name: content
        type: string
        description: 要写入的内容
        required: true
  - name: list_directory
    description: 列出目录内容
    parameters:
      - name: path
        type: string
        description: 目录路径
        required: true
  - name: file_exists
    description: 检查文件或目录是否存在
    parameters:
      - name: path
        type: string
        description: 文件或目录路径
        required: true
  - name: safe_calculator
    description: 执行安全的数学表达式计算
    parameters:
      - name: expression
        type: string
        description: 数学表达式字符串
        required: true
  - name: get_utc_time
    description: 获取当前的 UTC 时间
    parameters: []
  - name: run_command
    description: 执行安全的只读 shell 命令
    parameters:
      - name: command
        type: string
        description: 要执行的命令字符串
        required: true
  - name: fetch_web
    description: 获取指定 URL 的网页内容
    parameters:
      - name: url
        type: string
        description: 目标网页 URL
        required: true
  - name: json_dump
    description: 将对象序列化为 JSON 字符串
    parameters:
      - name: data
        type: string
        description: 要序列化的数据
        required: true
  - name: json_load
    description: 将 JSON 字符串解析为对象
    parameters:
      - name: json_str
        type: string
        description: JSON 格式的字符串
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
max_iterations: 5
---

# 通用任务执行技能

## 功能概述
作为通用任务执行者，能处理各类常规任务，包括但不限于文本生成、对话、问答、文件操作、计算、时间查询、命令执行、网页抓取、JSON处理等。

## 适用场景
- 用户的任务不明确属于某个特定技能
- 任务需要组合多种技能来完成
- 需要文本生成、摘要、分析等推理能力
- 复杂的多步骤任务

## 执行步骤

### 场景一：通用任务处理
1. 分析任务描述，确定所需的能力和工具
2. 自主选择合适的工具组合完成任务
3. 如需多个工具协作，按合理的顺序逐步执行
4. 汇总结果，返回简洁明确的执行结果

### 场景二：文本生成与分析
1. 根据用户需求进行文本创作、分析或总结
2. 如不需要调用工具，直接生成回答
3. 如需要外部信息，使用对应工具获取后再完成

## 注意事项
- 需要用户提供信息时（如文件路径、参数、确认等），必须调用 `ask_user` 工具
- 调用 ask_user 前先检查任务描述和上下文中是否已有所需信息
- 可组合使用多个工具完成复杂任务
- 任务无法完成时，清晰说明原因
