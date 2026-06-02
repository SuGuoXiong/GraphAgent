# GraphAgent

基于 LangGraph 的通用多 Agent 编排框架，提供三层编排架构、Skill 系统、MCP 协议支持、人机协作、会话管理及 ACP 通信协议。

## 我对 AI Agent 的思考

2025 年以来，AI Agent 框架大量涌现。但大多数遵循着同一种简单范式：给 LLM 配上工具，让它循环调用直到任务完成。这种"ReAct 循环"对简单任务足够，但面对复杂工程任务时——比如分析一个项目的代码结构、制定多步骤重构方案、跨多个文件执行修改、最终验证结果——扁平的 ReAct 循环很快会失控。

核心洞察在于：人类组织早已用**层级管理结构**解决了这个问题。CEO 不会亲自执行每一项任务——他们设定战略方向、审核方案、验收结果。中层管理者将战略分解为战术计划并协调执行。一线员工凭借专业技能完成具体任务。

服务于复杂任务的 AI Agent 系统应当对标这种结构。真正的挑战不是"LLM 能不能调用工具"，而是：

1. **上下文窗口有限**：LLM 无法在上下文中高效持有完整代码库 + 对话历史 + 工具输出
2. **注意力稀释**：过多无关上下文会让 LLM 失去对当前任务的聚焦
3. **安全与质量**：没有审核闸门，Agent 可能执行危险操作或产出低质量结果
4. **可观测性**：用户需要理解 Agent 在每一步做什么，而不是苦等许久后突然看见一个最终答案

GraphAgent 正是为解决这些问题而设计的——不是又一个 ReAct 包装器，而是一套借鉴企业组织架构的系统化 Agent 编排框架。

## 核心能力

| 模块 | 说明 |
|------|------|
| **三层编排** | GuardAgent（意图分析/方案审核）→ PlanAgent（任务分解/派发/汇总）→ SubAgent（ReAct 循环，支持 DAG 并行调度） |
| **Skill 系统** | 文档驱动（SKILL.md）的技能定义，内置 Skill 在 `prompts/skills/`，用户自定义 Skill 在 `skills/` |
| **MCP 协议** | 支持 stdio 和 Streamable HTTP 两种传输，通过 `mcp_servers.json` 配置，工具自动发现并注册 |
| **工具系统** | `@tool` 装饰器注册，ToolCenter 自动发现，内置文件/计算/命令/网页抓取/JSON/时间查询等工具 |
| **Hook 机制** | 四个检查点（before/after_tool_call、before/after_llm_call），三种类型（MODIFY/CONTROL/OBSERVE），优先级排序 |
| **人机协作** | 支持中断暂停/恢复、ask_user 工具交互、方案审核与用户确认 |
| **会话管理** | 多轮对话历史、JSON 持久化、两级压缩（优先级裁剪 + LLM 摘要） |
| **ACP 协议** | JSON 信封协议的 Agent 通信层，支持 HTTP+SSE 和 stdio 两种传输 |
| **Web UI** | 独立单页应用，支持会话管理、实时 SSE 流式逐字输出、阶段折叠/展开回溯、ask_user 交互卡片 |
| **安全体系** | RBAC 鉴权（Subject-Action-Resource-Effect）+ 审计日志（两段式记录），通过 Hook 机制透明集成 |
| **多 LLM** | 可插拔 Provider：OpenAI（兼容 DeepSeek 等）、Anthropic Claude、Ollama |

## GraphAgent 的设计哲学

**1. 关注点分离** —— 借鉴企业三级管理：GuardAgent 负责战略把关（意图识别、方案审核），PlanAgent 负责战术落地（任务分解、资源调度、结果汇总），SubAgent 负责专业执行（ReAct 循环 + 工具调用）。每层只关注自身职责，不越界。

**2. 最小权限** —— GuardAgent 不可见、不可调用任何 Tool，仅依赖 LLM 语义推理；PlanAgent 可见 SubAgent 技能列表但不可直接调用工具；SubAgent 仅能访问其声明范围内的工具。从源头杜绝误用和滥用。

**3. 审核闭环** —— 关键决策点（任务计划）必须通过 GuardAgent 审核才能进入执行。审核驳回则退回 PlanAgent 修订，最多重试 3 次。在错误传递到昂贵执行之前将其拦截。

**4. 分层上下文** —— 四层上下文架构：Layer 1 全量持久化 → Layer 2 GuardAgent 战略视图（过滤 + 压缩）→ Layer 3 PlanAgent 战术视图 → Layer 4 SubAgent 执行视图（最小必要上下文）。每层在上层基础上做减法，用对的信息喂对的 Agent。

**5. DAG 并行调度** —— PlanAgent 将任务拆解为有向无环图，通过 `dependencies` 字段表达偏序关系。同层无依赖任务并行执行，跨层按拓扑序串行。最大化吞吐，同时保证依赖正确性。

**6. 流式可观测** —— 每个阶段 LLM 的输出逐 token 实时推送到前端。阶段完成后自动折叠，用户可点击展开回溯任意阶段的完整内容。最终结果始终展开。全链路透明可追踪。

**7. 协议标准化** —— ACP（Agent Communication Protocol）提供 JSON 信封协议，支持 HTTP+SSE 和 stdio 两种传输方式。Agent 间通信、客户端交互、会话管理均通过统一协议完成。

**8. 安全内建** —— RBAC 鉴权（Subject-Action-Resource-Effect）和审计日志（两段式记录）通过 Hook 机制透明集成到工具调用链路中，不是在事后才考虑安全。

## 项目结构

```
GraphAgent/
├── src/graph_agent/           # 主包
│   ├── acp/                   #   ACP 协议层（Server/Client/Session/Transport）
│   ├── hook/                  #   Hook 机制
│   ├── llm/                   #   LLM Provider 抽象层
│   ├── mcp/                   #   MCP 协议支持（配置/管理器/工具适配）
│   ├── message/               #   统一消息格式（MessageBlock ↔ LangChain 双向转换）
│   ├── node/                  #   LangGraph 节点（guard / plan / subagent）
│   ├── orchestration/         #   三层编排引擎
│   ├── security/              #   安全模块（RBAC 鉴权 + 审计日志）
│   ├── session/               #   会话管理（历史/持久化/压缩/Token 估算）
│   ├── skill/                 #   Skill 系统（解析/加载/注册）
│   ├── tools/                 #   内置工具（文件/计算/命令/网页/JSON/时间/ask_user）
│   └── tracer/                #   可观测性（终端输出/事件追踪）
├── prompts/                   # 提示词模板和内置 Skill 定义
│   ├── guard/                 #   GuardAgent 提示词
│   ├── plan/                  #   PlanAgent 提示词
│   └── skills/                #   内置 Skill .md 文件
├── config/                    # YAML 配置文件（ACP / Session / RBAC / Audit）
├── web_ui/                    # Web 控制台（单页应用）
├── docs/                      # 设计文档（18 篇）
├── tests/                     # 单元测试和集成测试
├── mcp_servers.json           # MCP Server 配置
└── skills/                    # 用户自定义 Skill（可选）
```

## 快速开始

### 1. 环境准备

```bash
uv sync --dev
cp .env.example .env
```

编辑 `.env` 配置 LLM 连接：

```env
LLM_PROVIDER=openai
LLM_MODEL=deepseek-chat
LLM_API_KEY=sk-xxxxxxxx
LLM_BASE_URL=https://api.deepseek.com/v1
```

支持的 Provider：`openai` / `anthropic` / `ollama`。OpenAI Provider 兼容所有 OpenAI-API 风格的服务（DeepSeek、Qwen 等）。

### 2. 运行

```bash
# LangGraph 开发模式
uv run langgraph dev

# 或通过 Makefile
make dev
```

### 3. 启用 MCP（可选）

编辑 `mcp_servers.json` 配置外部 MCP Server：

```json
{
  "mcpServers": {
    "github": {
      "transport": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "risk_overrides": {
        "list_issues": "low",
        "search_code": "low",
        "create_pr": "medium"
      }
    }
  }
}
```

GraphAgent 启动时会自动连接并注册 MCP 工具。MCP 工具默认以高风险（Docker 沙箱）执行，可在 `risk_overrides` 中按需降低等级（`low`→直接执行，`medium`→进程隔离，`high`→沙箱执行）。

### 4. 安全配置（可选）

编辑 `config/rbac.yaml` 配置 RBAC 策略：

```yaml
policies:
  - subject: "subagent:code_reviewer"
    action: "read_*"
    resource: "*"
    effect: allow                          # allow（放行）/ deny（拒绝）
    description: "代码审查 Agent 的读取类工具自动放行"

  - subject: "*"
    action: "run_command"
    resource: "cmd:rm*"
    effect: deny                           # 禁止危险命令
    description: "禁止执行 rm 系列命令"
```

策略按列表顺序匹配，首次命中即生效。未匹配任何策略的工具调用会触发用户授权（AskUser）。审计日志自动记录所有调用，输出到 `data/audit/`。

### 5. 添加自定义 Skill（可选）

在项目根目录创建 `skills/` 目录，按子文件夹组织：

```
skills/
└── my-skill/
    ├── SKILL.md              # 必需：技能定义（YAML frontmatter + Markdown 正文）
    ├── reference/            # 可选：参考文档
    └── scripts/              # 可选：定制脚本（.py / .sh）
```

## 三层编排流程

```
用户请求
  → GuardAgent（意图分析）
  → PlanAgent（任务分解，生成 TaskPlan）
  → GuardAgent（方案审核，通过后派发）
  → SubAgent × N（DAG 并行执行子任务）
  → PlanAgent（结果汇总，生成最终答案）
  → 返回最终答案
```

## 效果演示

### 多阶段编排对话

以下展示一次典型的多阶段编排交互——用户提问"帮我分析这个项目的代码结构，看看各模块的职责和依赖关系"：

![普通对话演示](./demo/普通对话.gif)

编排流程包含：意图分析 → 方案制定 → 方案审核 → 任务执行（DAG 并行） → 结果汇总。每个阶段的 LLM 输出**逐字流式打印**到前端，阶段完成后自动折叠，用户可点击展开回溯任意阶段的完整内容。

### Skill 与 MCP 集成

以下展示 GraphAgent 集成自定义 Skill 和 MCP 外部工具的实际效果——Agent 自动发现并调用 MCP Server 提供的工具和用户自定义 Skill 完成复杂任务：

![Skill与MCP集成演示](./demo/集成skill和mcp.gif)

> 💡 上图中 Agent 自动加载了 `mcp_servers.json` 中配置的 GitHub MCP Server 工具，以及 `skills/` 目录下的自定义 Skill，在任务执行阶段按需调度。MCP 工具支持风险等级覆盖（`risk_overrides`），低风险工具直接执行，高风险工具触发用户授权。自定义 Skill 通过 `SKILL.md` 文档驱动，Agent 根据任务需求自动匹配最适合的 Skill。

---

<details>
<summary>📋 编排流程示意（文本版）</summary>

```
用户提问后，Web UI 实时展示编排全流程：

┌──────────────────────────────────────────────────────────────┐
│  👤 帮我分析这个项目的代码结构，看看各模块的职责和依赖关系         │
├──────────────────────────────────────────────────────────────┤
│  ▼ 意图分析 · GuardAgent · 🔄 执行中                           │
│  │  用户希望了解项目的代码组织结构、各模块的职责划分               │
│  │  以及模块间的依赖关系。这是一个代码分析类任务……                │
├──────────────────────────────────────────────────────────────┤
│  ▼ 方案制定 · PlanAgent · 🔄 执行中                            │
│  │  {                                                        │
│  │    "overall_goal": "分析项目代码结构、模块职责和依赖关系",     │
│  │    "sub_tasks": [                                          │
│  │      { "task_id": "task_1", "description": "扫描项目目录结构" },│
│  │      { "task_id": "task_2", "description": "分析核心模块职责" },│
│  │      { "task_id": "task_3", "description": "梳理模块依赖关系" } │
│  │    ]                                                       │
│  │  }                                                         │
├──────────────────────────────────────────────────────────────┤
│  ▼ 方案审核 · GuardAgent · 🔄 执行中                           │
│  │  审核结果：通过。方案覆盖了目录结构、模块职责和依赖关系           │
│  │  三个维度，任务分解合理，无需修改。                            │
├──────────────────────────────────────────────────────────────┤
│  ▼ 任务执行 · 3 个子任务 · 🔄 执行中                            │
│  │  > 调用 list_files("./src/graph_agent/")                    │
│  │  < 返回 8 个模块目录                                         │
│  │  > 调用 read_file("src/graph_agent/orchestration/__init__.py")│
│  │  < 编排模块入口，导出 OrchestrationGraph……                   │
│  │  --- SubAgent-B 开始执行 ---                                │
│  │  > 调用 grep("class.*Agent", "src/")                       │
│  │  < GuardAgent, PlanAgent, SubAgentExecutor……               │
│  │  ...                                                        │
├──────────────────────────────────────────────────────────────┤
│  ▼ 结果汇总 · PlanAgent · 🔄 执行中                            │
│  │  基于 3 个子任务的执行结果，汇总分析如下……                    │
├──────────────────────────────────────────────────────────────┤
│  🤖 最终结果                                                   │
│  ┌──────────────────────────────────────────────────────┐    │
│  │  ## 项目代码结构分析                                    │    │
│  │                                                        │    │
│  │  ### 模块总览                                           │    │
│  │  | 模块 | 职责 | 依赖 |                                 │    │
│  │  |------|------|------|                                │    │
│  │  | orchestration | 三层编排引擎 | node, session |      │    │
│  │  | node | Agent 节点实现 | orchestration, llm |       │    │
│  │  | acp | ACP 协议层 | session |                       │    │
│  │  | session | 会话管理 | message |                     │    │
│  │  | ... | ... | ... |                                  │    │
│  │                                                        │    │
│  │  ### 依赖关系图                                         │    │
│  │  ```                                                   │    │
│  │  orchestration → node → llm                            │    │
│  │              → session → message                        │    │
│  │              → acp → session                            │    │
│  │  ```                                                   │    │
│  └──────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────┘

所有阶段完成后：
- 意图分析、方案制定等前序阶段自动折叠为 ▶ 标题行，点击即可展开查看详细内容
- 最终结果始终展开，方便阅读
- 发送下一条消息后，上轮结果自动转为持久化的 Agent 聊天气泡
```

</details>

## 测试

```bash
make test                 # 单元测试
make integration-tests    # 集成测试（需 ANTHROPIC_API_KEY）
```

## 设计文档

| 编号 | 文档 |
|------|------|
| 1 | LLM 模块设计 |
| 2 | 工具模块设计 |
| 3 | 消息系统设计 |
| 4 | Agent 编排模块设计 |
| 5 | 可观测性系统设计 |
| 6 | 多轮对话系统设计 |
| 7 | ACP 协议层设计 |
| 8 | 会话中断与恢复机制设计 |
| 9 | Hook 机制设计 |
| 10 | 人机协作功能设计 |
| 11 | Skill 系统设计 |
| 12 | MCP 协议支持设计 |
| 13 | 安全体系设计 |
| 14 | SubAgent DAG 并行执行设计 |
| 15 | 上下文工程设计 |
| 16 | 流式响应设计方案 |
| 17 | Web UI 流式交互增强设计 |
| 18 | 记忆系统设计 |
