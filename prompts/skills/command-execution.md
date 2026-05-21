---
name: command-execution
type: builtin
description: 命令执行，在安全白名单范围内执行只读 shell 命令
tools:
  - name: run_command
    description: 执行安全的只读 shell 命令，白名单包括 ls, dir, pwd, echo, cat, type
    parameters:
      - name: command
        type: string
        description: 要执行的命令字符串
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

# 命令执行技能

## 功能概述
在安全白名单范围内执行只读 shell 命令，获取系统信息。

## 适用场景
- 需要列出目录内容
- 需要查看文件内容
- 需要获取当前工作目录
- 需要输出或回显文本信息

## 执行步骤

### 场景一：执行命令
1. 根据任务描述选择合适的命令
2. 确保命令在白名单范围内（ls, dir, pwd, echo, cat, type）
3. 使用 `run_command` 工具执行命令
4. 返回命令执行结果

## 注意事项
- 需要用户确认命令或提供参数时，必须调用 `ask_user` 工具
- 调用 ask_user 前先检查任务描述和上下文中是否已有所需信息
- 命令必须属于白名单范围，被拒绝时说明原因
