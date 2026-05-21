---
name: calculation
type: builtin
description: 数学计算与数据分析，支持安全的数学表达式计算和数值统计
tools:
  - name: safe_calculator
    description: 执行安全的数学表达式计算，支持加减乘除、幂运算、括号等
    parameters:
      - name: expression
        type: string
        description: 数学表达式字符串，如 (3+5)*2
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

# 数学计算技能

## 功能概述
使用安全的计算器工具执行数学表达式求值，支持常见的算术运算。

## 适用场景
- 用户需要计算数学表达式的结果
- 需要进行多步数值计算
- 需要验证计算结果的正确性

## 执行步骤

### 场景一：单步计算
1. 解析用户提供的数学表达式
2. 使用 `safe_calculator` 工具执行计算
3. 返回计算结果

### 场景二：多步计算
1. 将复杂计算拆解为多个简单表达式
2. 逐步使用 `safe_calculator` 计算每一步
3. 汇总最终结果并返回

## 注意事项
- 需要用户澄清表达式或提供额外信息时，必须调用 `ask_user` 工具
- 调用 ask_user 前先检查任务描述和上下文中是否已有所需信息
- 如果表达式不合法，说明原因并建议修正
- 确保计算结果的准确性
