# GraphAgent

基于 LangGraph 的通用多 Agent 编排框架，提供三层编排架构、Skill 系统、MCP 协议支持、人机协作、会话管理及 ACP 通信协议。

## 核心能力

| 模块 | 说明 |
|------|------|
| **三层编排** | GuardAgent（意图分析/方案审核/结果验收）→ PlanAgent（任务分解/派发/汇总）→ SubAgent（ReAct 循环执行） |
| **Skill 系统** | 文档驱动（SKILL.md）的技能定义，内置 Skill 在 `prompts/skills/`，用户自定义 Skill 在 `skills/` |
| **MCP 协议** | 支持 stdio 和 Streamable HTTP 两种传输，通过 `mcp_servers.json` 配置，工具自动发现并注册 |
| **工具系统** | `@tool` 装饰器注册，ToolCenter 自动发现，内置文件/计算/命令/网页抓取/JSON/时间查询等工具 |
| **Hook 机制** | 四个检查点（before/after_tool_call、before/after_llm_call），三种类型（MODIFY/CONTROL/OBSERVE），优先级排序 |
| **人机协作** | 支持中断暂停/恢复、ask_user 工具交互、方案与结果审核 |
| **会话管理** | 多轮对话历史、JSON 持久化、两级压缩（优先级裁剪 + LLM 摘要） |
| **ACP 协议** | JSON 信封协议的 Agent 通信层，支持 HTTP+SSE 和 stdio 两种传输 |
| **Web UI** | 独立单页应用，支持会话管理、实时 SSE 事件流、ask_user 交互卡片 |
| **安全体系** | RBAC 鉴权（Subject-Action-Resource-Effect）+ 审计日志（两段式记录），通过 Hook 机制透明集成 |
| **多 LLM** | 可插拔 Provider：OpenAI（兼容 DeepSeek 等）、Anthropic Claude、Ollama |

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
├── docs/                      # 设计文档（13 篇）
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
  → GuardAgent（方案审核）
  → SubAgent × N（ReAct 循环执行子任务）
  → PlanAgent（结果汇总）
  → GuardAgent（结果验收）
  → 返回最终答案
```

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
