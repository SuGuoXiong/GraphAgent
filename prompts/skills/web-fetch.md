---
name: web-fetch
type: builtin
description: 网页内容抓取与搜索，获取 URL 内容并进行网页搜索
tools:
  - name: fetch_web
    description: 获取指定 URL 的网页内容
    parameters:
      - name: url
        type: string
        description: 目标网页 URL
        required: true
  - name: search_web
    description: 使用搜索引擎搜索关键词
    parameters:
      - name: query
        type: string
        description: 搜索关键词
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

# 网页抓取技能

## 功能概述
获取指定 URL 的网页内容并进行信息提取，支持网页搜索功能。

## 适用场景
- 用户提供了 URL，需要获取其内容
- 需要在线搜索信息
- 需要从网页中提取特定信息

## 执行步骤

### 场景一：获取网页内容
1. 使用 `fetch_web` 工具获取指定 URL 的内容
2. 对获取的内容进行分析和信息提取
3. 返回结构化、准确的结果

### 场景二：网页搜索
1. 使用 `search_web` 工具搜索用户指定的关键词
2. 整理搜索结果
3. 如需获取某个搜索结果的详细内容，使用 `fetch_web` 进一步获取
4. 返回整理后的信息

## 注意事项
- 需要用户指定 URL 或搜索参数时，必须调用 `ask_user` 工具
- 调用 ask_user 前先检查任务描述和上下文中是否已有所需信息
- 对获取的内容准确理解和提取，不歪曲原意
