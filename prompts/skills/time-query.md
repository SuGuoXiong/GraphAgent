---
name: time-query
type: builtin
description: 时间信息查询，获取当前 UTC 时间并进行时间格式转换
tools:
  - name: get_utc_time
    description: 获取当前的 UTC 时间，返回 ISO 8601 格式
    parameters: []
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
max_iterations: 2
---

# 时间查询技能

## 功能概述
获取当前 UTC 时间并可根据需要进行时区转换和格式处理。

## 适用场景
- 用户需要知道当前的准确时间
- 需要带时间戳的信息
- 需要进行时间格式转换

## 执行步骤

### 场景一：获取当前时间
1. 使用 `get_utc_time` 工具获取当前 UTC 时间
2. 如需转换为特定时区或格式，根据用户需求处理
3. 返回清晰的时间信息

## 注意事项
- 需要用户指定时区或时间格式时，必须调用 `ask_user` 工具
- 调用 ask_user 前先检查任务描述和上下文中是否已有所需信息
