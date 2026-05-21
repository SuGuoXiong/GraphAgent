---
name: json-processing
type: builtin
description: JSON 数据处理，支持 JSON 对象的序列化与反序列化
tools:
  - name: json_dump
    description: 将对象序列化为 JSON 字符串
    parameters:
      - name: data
        type: string
        description: 要序列化的数据（以字符串表示的字典或列表）
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
max_iterations: 3
---

# JSON 处理技能

## 功能概述
对 JSON 数据进行序列化（对象转 JSON 字符串）和反序列化（JSON 字符串转对象）。

## 适用场景
- 需要将数据转换为 JSON 格式
- 需要解析 JSON 文本提取信息
- 需要验证 JSON 格式的正确性

## 执行步骤

### 场景一：JSON 序列化
1. 根据用户提供的数据，确定要序列化的对象结构
2. 使用 `json_dump` 工具将对象转为 JSON 字符串
3. 返回序列化结果

### 场景二：JSON 解析
1. 接收用户提供的 JSON 字符串
2. 使用 `json_load` 工具解析为对象
3. 提取用户需要的信息并返回

## 注意事项
- 需要用户提供数据或确认处理方式时，必须调用 `ask_user` 工具
- 调用 ask_user 前先检查任务描述和上下文中是否已有所需信息
- 确保 JSON 格式正确，如有问题说明具体原因
