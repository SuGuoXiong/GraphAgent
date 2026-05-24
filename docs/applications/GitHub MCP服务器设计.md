# GraphAgent GitHub MCP Server 设计文档

## 1. 概述

### 1.1 设计背景

在日常工作中，开发者和团队经常需要关注 GitHub 社区的技术趋势，了解当前最受关注的开源项目。GitHub Trending 页面（`https://github.com/trending`）每日汇总了社区中最热门、增长最快的仓库，是技术趋势洞察的重要数据源。此外，在调研具体仓库时，README.md 是了解项目定位、功能特性和使用方式的核心文档，Agent 需要能够自动获取并理解 README 内容。

目前 GraphAgent 已具备 MCP 协议支持能力（详见 `docs/12.MCP协议支持设计.md`），可以通过实现自定义 MCP Server 的方式扩展外部工具。本设计文档描述一个 **GitHub MCP Server**，提供以下两个工具：

1. **`fetch_github_trending`**：抓取 GitHub Trending 页面热榜数据，提取仓库信息并保存为结构化 Markdown 文件
2. **`fetch_repo_readme`**：获取指定仓库的 README.md 内容，通过解析仓库页面的 HTML 提取渲染后的文档内容

### 1.2 核心设计理念

```
工具一（fetch_github_trending）:
  用户请求 → Agent 调用 mcp__github__fetch_github_trending
    → MCP Server 通过 HTTP 抓取 https://github.com/trending 页面
    → BeautifulSoup 解析 HTML，提取仓库信息
    → 生成结构化 Markdown 文件，保存到指定目录
    → 返回结果摘要（文件路径 + 上榜仓库数量）

工具二（fetch_repo_readme）:
  用户请求 → Agent 调用 mcp__github__fetch_repo_readme
    → MCP Server 对每个 URL 发送 HTTP GET 请求获取仓库页面
    → BeautifulSoup 解析 HTML，定位 markdown-body 区域
    → 提取 README 文本内容
    → 汇总返回所有 README 内容
```

**设计原则**：

1. **独立部署**：MCP Server 是一个独立的 Python 进程，通过 stdio transport 与 GraphAgent 通信，不修改 GraphAgent 核心代码
2. **善用现有依赖**：项目已引入 `requests` 和 `beautifulsoup4`（见 `web_tools.py`），MCP Server 直接复用，无需新增第三方包
3. **FastMCP 框架**：使用 `mcp.server.fastmcp.FastMCP` 快速构建，与现有 `test_mcp_server.py` 模式一致
4. **结构化输出**：抓取结果保存为约定的 Markdown 格式，方便用户阅读、存档和后续处理

### 1.3 与现有系统的关系

```
                        ┌─────────────────────┐
                        │    ToolCenter        │
                        │  (统一工具注册中心)    │
                        └┬──────┬──────┬──────┘
                         │      │      │
                 ┌───────┘      │      └───────────┐
                 ▼              ▼                  ▼
          ┌──────────┐  ┌──────────┐  ┌──────────────────────┐
          │ 内置工具  │  │脚本工具   │  │  MCP 工具             │
          │(tools/)  │  │(scripts) │  │  ├─ test-server       │
          └──────────┘  └──────────┘  │  └─ github  │  ← 本次新增
                                      └──────────────────────┘
```

- **不修改** GraphAgent 任何现有模块，纯增量开发
- MCP Server 通过 `mcp_servers.json` 注册，由 `MCPManager` 统一管理生命周期
- 工具命名遵循 `mcp__<server>__<tool>` 规范：
  - `mcp__github__fetch_github_trending`
  - `mcp__github__fetch_repo_readme`
- 受 RBAC 和审计系统约束（默认 risk_level="medium"）
- 可被 Skill 系统引用，组合出更复杂的自动化工作流（如"每日技术早报"Skill、"仓库深度调研"Skill）

---

## 2. 目录结构

```
mcp_servers/
    └── github_server.py             # 新增：GitHub MCP Server

mcp_servers.json                     # 修改：新增 github 配置项

docs/applications/
    └── GitHub热榜MCP服务器设计.md    # 本文档
```

---

## 3. MCP Server 设计

### 3.1 服务器元信息

| 属性 | 值 |
|------|-----|
| Server 名称 | `github` |
| 框架 | `mcp.server.fastmcp.FastMCP` |
| Transport | stdio（本地子进程） |
| Python 解释器 | 项目所使用的 Python（Python 3.13+） |
| 工具数量 | 2 个 |

### 3.2 工具定义：`fetch_github_trending`

```python
@mcp.tool(
    name="fetch_github_trending",
    description=(
        "抓取 GitHub Trending 页面热榜信息，提取上榜仓库的名称、URL、Star 数量、"
        "描述、编程语言等信息，并保存为结构化的 Markdown 文件。"
    ),
)
def fetch_github_trending(
    output_dir: str = "./data/github",
    since: str = "daily",
    language: str = "",
) -> str:
    """抓取 GitHub Trending 数据并保存为 Markdown 文件。

    Args:
        output_dir: Markdown 文件输出目录，默认为 ./data/github
        since: 时间范围，可选 "daily"（今日）、"weekly"（本周）、"monthly"（本月）
        language: 编程语言过滤，为空表示不限语言。
                  可选值如 "python", "javascript", "go", "rust" 等（需与 GitHub URL 路径一致，URL 编码由工具处理）

    Returns:
        执行结果摘要字符串
    """
```

### 3.3 参数说明

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `output_dir` | string | 否 | `"./data/github"` | Markdown 文件输出目录，相对于 GraphAgent 工作目录 |
| `since` | string | 否 | `"daily"` | 时间范围：`daily`（今日）/ `weekly`（本周）/ `monthly`（本月） |
| `language` | string | 否 | `""` | 编程语言过滤，为空表示不限语言 |

### 3.4 核心实现流程

```
fetch_github_trending(output_dir, since, language)
  │
  ├─ 1. 参数校验
  │     ├─ since ∈ {daily, weekly, monthly}，否则返回错误
  │     └─ output_dir 路径合法性校验
  │
  ├─ 2. 构建请求 URL
  │     ├─ 基础 URL: https://github.com/trending
  │     ├─ 若有 language: https://github.com/trending/{language}
  │     └─ Query 参数: ?since={since}
  │
  ├─ 3. 发送 HTTP GET 请求
  │     ├─ 设置 User-Agent（模拟浏览器，避免被拒）
  │     ├─ 设置 Accept / Accept-Language 请求头
  │     └─ 超时时间: 30s
  │
  ├─ 4. 解析 HTML（BeautifulSoup）
  │     ├─ 定位仓库列表容器（<article class="Box-row">）
  │     ├─ 对每个仓库条目提取:
  │     │   ├─ 仓库名称: h2 中的 <a> 标签 href 属性 → /owner/repo
  │     │   ├─ 仓库全名: owner / repo
  │     │   ├─ 仓库 URL: https://github.com/{owner/repo}
  │     │   ├─ 仓库描述: <p> 标签中的文本
  │     │   ├─ 编程语言: 语言 span 中的文本
  │     │   ├─ 总 Star 数: 统计行中的 star 数值
  │     │   └─ 今日新增 Star 数: 统计行中的今日新增数值
  │     └─ 最多提取 25 个仓库（GitHub Trending 单页默认数量）
  │
  ├─ 5. 生成 Markdown 内容
  │     ├─ 文件头部: 标题 + 生成时间 + 筛选条件
  │     ├─ 每个仓库一个 ## 章节
  │     │   ├─ 标题行: 序号 + 仓库全名（带链接）
  │     │   ├─ Star 数 / 今日新增
  │     │   ├─ 描述
  │     │   └─ 编程语言
  │     └─ 文件尾部: 统计摘要
  │
  ├─ 6. 写入文件
  │     ├─ 确保 output_dir 目录存在（mkdir -p）
  │     ├─ 文件名: github_{since}_{YYYY-MM-DD}.md
  │     └─ UTF-8 编码写入
  │
  └─ 7. 返回结果
        └─ 文件路径 + 上榜仓库数量 + 文件大小
```

### 3.5 Markdown 输出格式

```markdown
# GitHub Trending Repositories

> **时间范围**: Daily（今日）
> **编程语言**: 不限
> **生成时间**: 2026-05-24 16:30:00 CST
> **数据来源**: [GitHub Trending](https://github.com/trending?since=daily)

---

## 1. [owner/repo-name](https://github.com/owner/repo-name)

- **Stars**: 12,345 (今日 +1,234)
- **Forks**: 567
- **Language**: Python
- **Description**: A concise description of what this repository does and why it's trending today.

---

## 2. [owner/another-repo](https://github.com/owner/another-repo)

- **Stars**: 8,900 (今日 +890)
- **Forks**: 234
- **Language**: TypeScript
- **Description**: Another trending repository description.

---

*共 25 个上榜仓库*
```

### 3.6 HTML 解析策略

GitHub Trending 页面的核心 DOM 结构（简化）：

```html
<main>
  <article class="Box-row">
    <h2 class="h3 lh-condensed">
      <a href="/owner/repo-name">owner / <strong>repo-name</strong></a>
    </h2>
    <p class="col-9 color-fg-muted my-1 pr-4">
      Repository description text here...
    </p>
    <div class="f6 color-fg-muted mt-2">
      <span itemprop="programmingLanguage">Python</span>
      <a href="/owner/repo-name/stargazers">12,345</a>
      <a href="/owner/repo-name/forks">567</a>
      <span class="d-inline-block float-sm-right">
        1,234 stars today
      </span>
    </div>
  </article>
  <!-- 更多 Box-row ... -->
</main>
```

**解析方案**：

| 步骤 | 操作 | 说明 |
|------|------|------|
| 1 | `soup.select("article.Box-row")` | 主选择器，定位每个仓库条目 |
| 2 | 若步骤 1 返回空，`soup.select("h2.h3 a[href]")` | 备选方案：通过仓库标题链接反向定位 |
| 3 | 若均失败，返回警告信息 + 页面文本摘要（前 2000 字符） | 兜底策略，方便排查 |
| 4 | 对每个条目提取文本后 `strip()` 清洗 | 去除多余空白、换行符，统一格式

### 3.7 工具定义：`fetch_repo_readme`

```python
@mcp.tool(
    name="fetch_repo_readme",
    description=(
        "获取一个或多个 GitHub 仓库的 README.md 内容。"
        "传入 GitHub 仓库主页 URL（多个 URL 用英文逗号分隔），"
        "每个 URL 必须严格遵循 https://github.com/owner/repo 格式（仅 2 段路径），"
        "工具将分别访问每个仓库页面，解析并提取 README 的渲染文本内容并返回。"
    ),
)
def fetch_repo_readme(
    repo_urls: str,
    max_length_per_repo: int = 8000,
) -> str:
    """获取 GitHub 仓库的 README.md 内容。

    Args:
        repo_urls: GitHub 仓库 URL 列表，多个 URL 以英文逗号 `,` 分隔。
                   每个 URL 格式如 https://github.com/owner/repo
        max_length_per_repo: 每个 README 的最大返回字符数，默认 8000。
                             超出部分截断并标注 "...(内容已截断)"

    Returns:
        所有仓库 README 内容的汇总文本
    """
```

### 3.8 参数说明

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `repo_urls` | string | 是 | — | GitHub 仓库主页 URL 列表（逗号分隔）。必须为 `https://github.com/{owner}/{repo}` 格式，不允许多余路径段。如 `https://github.com/A/repo1,https://github.com/B/repo2` |
| `max_length_per_repo` | int | 否 | `8000` | 每个 README 最大返回字符数，超出截断 |

### 3.9 核心实现流程

```
fetch_repo_readme(repo_urls, max_length_per_repo)
  │
  ├─ 1. 参数解析与校验
  │     ├─ 按逗号分割 repo_urls，去除空白
  │     ├─ 校验每个 URL（必须匹配格式: https://github.com/{owner}/{repo}）:
  │     │   ├─ 必须以 https://github.com/ 开头
  │     │   ├─ 路径段数量必须恰好为 2（/owner/repo），拒绝 /owner/repo/... 等多段路径
  │     │   ├─ owner 和 repo 不能为空，不能包含特殊字符（仅允许 [a-zA-Z0-9._-]）
  │     │   └─ 非法 URL 跳过并记录警告，说明具体原因
  │     └─ 去重（相同 URL 仅请求一次，大小写敏感）
  │
  ├─ 2. 对每个有效 URL 串行处理:
  │     │
  │     ├─ 2.1 发送 HTTP GET 请求
  │     │     ├─ 目标 URL: 用户传入的仓库主页 URL（不做拼接）
  │     │     ├─ 请求头: 与 3.11 节通用浏览器头一致
  │     │     └─ 超时时间: 30s
  │     │
  │     ├─ 2.2 检查响应状态
  │     │     ├─ 200: 继续解析
  │     │     ├─ 404: 记录 "仓库不存在或私有: {url}"
  │     │     └─ 其他: 记录 "HTTP {code}: {url}"
  │     │
  │     ├─ 2.3 解析 HTML 提取 README
  │     │     ├─ 主选择器: article.markdown-body.entry-content
  │     │     ├─ 备选选择器: div#readme article.markdown-body
  │     │     ├─ 若均未匹配: 返回 "未找到 README 内容"
  │     │     └─ 提取: soup.get_text(separator="\n", strip=True)
  │     │
  │     ├─ 2.4 文本后处理
  │     │     ├─ 合并连续空行（最多保留一个空行）
  │     │     └─ 按 max_length_per_repo 截断
  │     │
  │     └─ 2.5 请求间隔
  │           └─ 多 URL 间 sleep 1s，降低请求频率
  │
  └─ 3. 汇总返回
        ├─ 每个仓库 README 以分隔标记包裹:
        │   === README: owner/repo ===
        │   (原始 URL: https://github.com/owner/repo)
        │   {README 文本内容}
        └─ 末尾附处理统计: 成功 N 个 / 总计 M 个
```

### 3.10 README 提取策略

GitHub 仓库页面中 README 的 DOM 结构（简化）：

```html
<div id="repository-container-header">...</div>

<div id="readme">
  <article class="markdown-body entry-content container-lg" itemprop="text">
    <h1>Project Title</h1>
    <p>Project description paragraph...</p>
    <h2>Installation</h2>
    <pre><code>pip install ...</code></pre>
    <p>More content...</p>
  </article>
</div>
```

**解析方案**：

| 步骤 | 操作 | 说明 |
|------|------|------|
| 1 | `soup.select_one("article.markdown-body.entry-content")` | 主选择器，定位 README 渲染区域 |
| 2 | 若步骤 1 失败，`soup.select_one("#readme article.markdown-body")` | 备选选择器 |
| 3 | 若均失败，`soup.select_one("div[itemprop='text']")` | 兜底策略 |
| 4 | `element.get_text(separator="\n", strip=True)` | 提取纯文本，保留换行结构 |
| 5 | `re.sub(r'\n{3,}', '\n\n', text)` | 合并过多连续空行 |

**返回格式示例**：

```
=== README: Lum1104/Understand-Anything ===
(原始 URL: https://github.com/Lum1104/Understand-Anything)

# Understand Anything
A comprehensive toolkit for understanding and analyzing various data types.
...

=== 处理统计: 成功 1/1 ===
```

### 3.11 HTTP 请求头设计（通用）

两个工具共用同一套 HTTP 请求头配置，模拟正常浏览器访问：

```python
# 工具模块级常量，fetch_github_trending 和 fetch_repo_readme 共用
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/148.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
```

**说明**：
- 不加 Cookie：GitHub Trending 页面和公开仓库页面无需登录即可访问
- 若后续发现 GitHub 对无 Cookie 请求限流，可扩展为通过环境变量 `GITHUB_COOKIE` 注入认证信息

### 3.12 公共模块设计

两个工具共享以下模块级资源，避免代码重复：

```python
import requests
from bs4 import BeautifulSoup

# 共享 HTTP 请求头（见 3.11 节）
_HEADERS = { ... }

# 共享 HTTP 会话（连接复用，减少 TCP 握手开销）
_session = requests.Session()
_session.headers.update(_HEADERS)

def _get_html(url: str, timeout: int = 30) -> tuple[int, str]:
    """发送 HTTP GET 请求并返回 (status_code, html_text)。

    两个工具的 HTTP 请求均通过此函数发出，统一处理超时、异常和响应大小限制。
    响应体超过 5MB 时截断并记录警告。
    """
    try:
        resp = _session.get(url, timeout=timeout)
        # 限制响应体大小
        content = resp.text[:5_000_000]  # 5MB 上限
        return resp.status_code, content
    except requests.Timeout:
        return -1, ""  # -1 表示超时
    except requests.RequestException as e:
        return -2, str(e)  # -2 表示其他网络错误
```

**说明**：
- 使用 `requests.Session` 而非每次 `requests.get()`，复用底层 TCP 连接，减少多次请求时的握手机开销
- 返回 `(status_code, html_text)` 元组，由各工具自行处理状态码和解析逻辑
- 超时和网络异常统一转为负数状态码，上层调用方无需捕获异常

---

## 4. 配置与注册

### 4.1 mcp_servers.json 配置项

```json
{
  "mcpServers": {
    "test-server": {
      "transport": "stdio",
      "command": "C:/Users/Administrator/AppData/Local/Programs/Python/Python314/python.exe",
      "args": ["tests/unit_tests/test_mcp_server.py"]
    },
    "github": {
      "transport": "stdio",
      "command": "python",
      "args": ["mcp_servers/github_server.py"]
    }
  }
}
```

### 4.2 启动流程

```
GraphAgent 启动
    → MCPManager.setup()
    → 读取 mcp_servers.json
    → 遍历 mcpServers 配置
    → 启动 github: python mcp_servers/github_server.py
    → 通过 stdio 建立 ClientSession
    → session.initialize()
    → session.list_tools()  →  发现 fetch_github_trending, fetch_repo_readme
    → wrap_mcp_tools() → 注册两个工具到 ToolCenter
    → 工具可用
```

### 4.3 工具在 ToolCenter 中的注册名

```
mcp__github__fetch_github_trending
mcp__github__fetch_repo_readme
```

两个工具位于同一 MCP Server（`github`）下，共享连接生命周期。

在 RBAC 中可通过通配符统一授权：

```yaml
# 允许所有 SubAgent 使用 GitHub 相关工具
- subject: "*"
  action: "mcp__github__*"
  resource: "*"
  effect: allow
```

---

## 5. 安全与风险控制

### 5.1 风险等级

两个工具默认风险等级均为：**medium**（与现有的 `fetch_web` 工具一致）

| 工具 | 网络请求 | 文件写入 | 风险等级 |
|------|---------|---------|---------|
| `fetch_github_trending` | 是（github.com） | 是（生成 Markdown 文件） | medium |
| `fetch_repo_readme` | 是（github.com × N） | 否（纯文本返回） | medium |

### 5.2 安全措施

| 措施 | 适用工具 | 说明 |
|------|---------|------|
| **输出目录限制** | trending | 仅允许写入到 `output_dir` 指定的目录，默认 `./data/github/` |
| **路径遍历防护** | trending | 校验 `output_dir` 参数，拒绝包含 `..` 的路径 |
| **URL 白名单校验** | readme | 仅接受 `https://github.com/` 开头的 URL，拒绝其他域名 |
| **URL 数量上限** | readme | 单次调用最多处理 10 个 URL，防止滥用 |
| **请求间隔** | readme | 多 URL 之间间隔 1 秒，降低对 GitHub 的请求压力 |
| **内容长度限制** | readme | 单个 README 默认上限 8000 字符，可配置但不超过 20000 |
| **请求超时** | 两个工具 | HTTP 请求设置 30s 超时，避免长时间挂起 |
| **响应大小限制** | 两个工具 | 限制 HTTP 响应体最大 5MB，防止内存溢出 |
| **文件覆盖** | trending | 同日同名文件将被覆盖，不影响其他文件 |

### 5.3 RBAC 策略建议

```yaml
# 仅允许具备 web_fetch 权限的 agent 使用
- subject: "subagent:web_agent"
  action: "mcp__github__*"
  resource: "*"
  effect: allow

# 其他 agent 默认拒绝（若全局策略为 deny 模式）
- subject: "*"
  action: "mcp__github__*"
  resource: "*"
  effect: deny
```

---

## 6. 错误处理

### 6.1 错误分类与处理策略

#### 6.1.1 fetch_github_trending 错误处理

| 错误场景 | 返回信息 | 处理方式 |
|---------|---------|---------|
| `since` 参数非法 | `错误: since 参数必须为 daily/weekly/monthly，当前值: {value}` | 参数校验阶段拦截 |
| `output_dir` 包含 `..` | `错误: output_dir 包含非法字符，不允许路径遍历` | 安全校验阶段拦截 |
| 网络请求超时（30s） | `错误: 请求 GitHub Trending 超时，请检查网络连接后重试` | 返回错误给 LLM，由 LLM 决定是否重试 |
| HTTP 非 200 响应 | `错误: GitHub 返回状态码 {code}，可能触发了反爬机制` | 返回错误给 LLM |
| HTML 结构变化（解析不到仓库） | `警告: 未能从页面中解析到仓库列表，GitHub 页面结构可能已更新，返回原始页面摘要供排查` | 返回警告 + 部分文本 |
| 输出目录不可写 | `错误: 无法写入到目录 {path}，请检查目录权限` | 返回错误给 LLM |

#### 6.1.2 fetch_repo_readme 错误处理

| 错误场景 | 返回信息 | 处理方式 |
|---------|---------|---------|
| `repo_urls` 为空 | `错误: repo_urls 参数不能为空` | 参数校验阶段拦截 |
| URL 不以 `https://github.com/` 开头 | `警告: 跳过非法 URL（仅支持 GitHub 仓库链接）: {url}` | 跳过该 URL，继续处理其他 |
| URL 路径段数量不为 2 | `警告: 跳过非仓库主页链接（请使用 https://github.com/owner/repo 格式）: {url}` | 跳过该 URL，继续处理其他 |
| URL 数量超过 10 个 | `错误: 单次最多处理 10 个仓库 URL，当前传入 {n} 个` | 参数校验阶段拦截 |
| 单个 URL 请求超时 | `警告: 请求超时 - {url}` | 跳过该 URL，继续处理其他 |
| HTTP 404 | `提示: 仓库不存在或为私有仓库 - {owner/repo}` | 跳过该 URL，继续处理其他 |
| 页面中未找到 README | `提示: 仓库 {owner/repo} 未找到 README.md 文件` | 跳过该 URL，继续处理其他 |
| 所有 URL 均失败 | `错误: 所有仓库 URL 均无法获取 README 内容，请检查链接是否有效` | 返回汇总错误信息 |

### 6.2 容错原则

- **不抛异常**：所有错误通过返回字符串报告，不向 GraphAgent 抛出未捕获异常
- **结构化错误信息**：错误消息包含具体的错误原因和上下文，方便 LLM 理解并给出后续建议
- **部分成功**：
  - `fetch_github_trending`：即使仅解析到部分仓库，也正常保存 Markdown，并在摘要中注明实际数量
  - `fetch_repo_readme`：多 URL 场景下，单个 URL 失败不影响其他 URL，返回时标注每个 URL 的处理状态

---

## 7. 数据流图

### 7.1 fetch_github_trending 数据流

```
┌──────────────┐                        ┌────────────────────────┐
│  用户/Agent   │                        │  github                │
│  工具调用请求  │                        │  MCP Server (stdio)    │
└──────┬───────┘                        └───────────┬────────────┘
       │                                            │
       │  mcp__github__fetch_github_trending         │
       │  (output_dir, since, language)              │
       ├───────────────────────────────────────────▶│
       │                                            │
       │                                  ┌─────────▼─────────┐
       │                                  │ 参数校验 + 构建URL │
       │                                  │ github.com/trending│
       │                                  └─────────┬─────────┘
       │                                            │
       │                                  ┌─────────▼─────────┐
       │                                  │ HTTP GET           │
       │                                  │ (requests.get)     │
       │                                  └─────────┬─────────┘
       │                                            │
       │                                  ┌─────────▼─────────┐
       │                                  │ BeautifulSoup      │
       │                                  │ 解析 HTML          │
       │                                  └─────────┬─────────┘
       │                                            │
       │                                  ┌─────────▼─────────┐
       │                                  │ 提取仓库列表       │
       │                                  │ (≤25 repos)        │
       │                                  └─────────┬─────────┘
       │                                            │
       │                                  ┌─────────▼─────────┐
       │                                  │ 生成 Markdown      │
       │                                  └─────────┬─────────┘
       │                                            │
       │                                  ┌─────────▼─────────┐
       │                                  │ 写入 .md 文件      │
       │                                  │ output_dir/        │
       │                                  └─────────┬─────────┘
       │                                            │
       │  返回结果（文件路径 + 仓库数量 + 文件大小）    │
       │◀───────────────────────────────────────────┘
       │
       ▼
┌──────────────┐
│  文件已保存    │
│  供用户查阅    │
└──────────────┘
```

### 7.2 fetch_repo_readme 数据流

```
┌──────────────┐                        ┌────────────────────────┐
│  用户/Agent   │                        │  github                │
│  工具调用请求  │                        │  MCP Server (stdio)    │
└──────┬───────┘                        └───────────┬────────────┘
       │                                            │
       │  mcp__github__fetch_repo_readme             │
       │  (repo_urls="url1,url2,...")                │
       ├───────────────────────────────────────────▶│
       │                                            │
       │                                  ┌─────────▼─────────┐
       │                                  │ 解析 + 校验 URL    │
       │                                  │ (逗号分隔 → 去重)  │
       │                                  └─────────┬─────────┘
       │                                            │
       │                           ┌────────────────▼────────────────┐
       │                           │  对每个 URL 串行处理:            │
       │                           │                                  │
       │                           │  ┌─ HTTP GET repo page          │
       │                           │  ├─ 检查 HTTP 状态码            │
       │                           │  ├─ BeautifulSoup 解析          │
       │                           │  ├─ 定位 article.markdown-body  │
       │                           │  ├─ 提取纯文本 README 内容      │
       │                           │  ├─ 按长度截断                  │
       │                           │  └─ sleep(1s) 请求间隔          │
       │                           └────────────────┬────────────────┘
       │                                            │
       │                                  ┌─────────▼─────────┐
       │                                  │ 汇总所有 README    │
       │                                  │ + 处理统计          │
       │                                  └─────────┬─────────┘
       │                                            │
       │  返回结果（每个仓库 README 文本 + 处理统计）  │
       │◀───────────────────────────────────────────┘
       │
       ▼
┌──────────────┐
│  Agent 获得   │
│  README 内容  │
└──────────────┘
```

### 7.3 两个工具的组合使用场景

```
┌─────────────────────────────────────────────────────┐
│  典型工作流: "repo-deep-dive" （仓库深度调研）        │
│                                                      │
│  1. fetch_github_trending → 获取热榜 Markdown 文件    │
│  2. Agent 读取文件 → 识别 Top 3 仓库                  │
│  3. fetch_repo_readme → 获取 3 个仓库的 README        │
│  4. LLM 综合分析 → 生成调研报告                       │
└─────────────────────────────────────────────────────┘
```

---

## 8. 扩展性考虑

### 8.1 未来可扩展参数

| 工具 | 扩展参数 | 说明 | 优先级 |
|------|---------|------|--------|
| trending | `spoken_language` | 口语语言过滤（如 `zh` 过滤中文项目） | 低 |
| trending | `max_repos` | 最大提取仓库数（默认 25） | 低 |
| trending | `output_format` | 输出格式扩展：`json`、`csv` 等（当前仅 markdown） | 低 |
| readme | `include_raw` | 是否尝试通过 raw.githubusercontent.com 获取原始 README.md 源码 | 中 |
| readme | `parse_frontmatter` | 自动解析 README 中的 YAML frontmatter（项目元数据） | 低 |

### 8.2 可组合的自动化场景

基于两个工具的组合，可通过 GraphAgent Skill 系统构建以下自动化工作流：

| Skill 名称 | 描述 | 使用的工具 | 核心流程 |
|-----------|------|-----------|---------|
| `daily-tech-briefing` | 每日技术早报 | `fetch_github_trending` | 每天抓取热榜 → LLM 摘要 → 推送到企业微信/邮件 |
| `trend-analysis` | 技术趋势分析 | `fetch_github_trending` | 连续多日抓取 → LLM 分析趋势变化 → 生成趋势报告 |
| `repo-deep-dive` | 热榜仓库深度调研 | `fetch_github_trending` + `fetch_repo_readme` | 抓取热榜 → 选择 Top N → 获取 README → 生成调研报告 |
| `repo-comparison` | 同类仓库对比 | `fetch_repo_readme` | 用户指定 2-5 个同类仓库 → 获取全部 README → LLM 横向对比 → 输出对比报告 |
| `repo-onboarding` | 新仓库快速上手 | `fetch_repo_readme` | 用户提供仓库链接 → 获取 README → LLM 提炼关键信息（安装、API、架构）→ 生成上手指南 |

### 8.3 反爬策略应对

GitHub 可能在将来加强反爬措施。设计上预留以下扩展点：

- **请求频率控制**：两个工具内部均已加入 rate limiting 机制（trending 单次请求，readme 多 URL 间 1s 间隔）
- **Cookie 注入**：通过环境变量 `GITHUB_COOKIE` 注入认证 Cookie，提升请求成功率
- **代理支持**：通过环境变量 `HTTPS_PROXY` 支持代理访问
- **缓存机制**：
  - trending：同一 `since + language + date` 组合在 10 分钟内不重复请求
  - readme：同一 repo URL 在 30 分钟内不重复请求，直接返回缓存内容

---

## 9. 实施计划

| 阶段 | 任务 | 产出物 |
|------|------|--------|
| 1 | 实现 `fetch_github_trending` 工具（HTTP 抓取 Trending 页面 + HTML 解析 + Markdown 生成 + 文件写入） | `mcp_servers/github_server.py` |
| 2 | 实现 `fetch_repo_readme` 工具（多 URL 解析 + 串行请求 + README 区域提取 + 文本截断汇总） | `mcp_servers/github_server.py`（同一文件） |
| 3 | 提取公共模块：`_HEADERS` 常量、`_get_html(url)` 公共函数，两个工具共享 | `mcp_servers/github_server.py`（重构） |
| 4 | 更新 `mcp_servers.json`，注册 `github` Server | `mcp_servers.json`（修改） |
| 5 | 集成测试：启动 GraphAgent → 验证两个工具均自动发现 → 分别调用验证 → 检查产出 | 测试用例 |
| 6 | （可选）编写 `repo-deep-dive` Skill，串联两个工具实现仓库深度调研 | `prompts/skills/repo-deep-dive.md` |

---

## 10. 依赖分析

### 10.1 现有依赖（无需新增）

| 包名 | 用途 | 项目中是否已引入 |
|------|------|-----------------|
| `mcp` | MCP Python SDK，提供 FastMCP 框架 | 是（`pyproject.toml`） |
| `requests` | HTTP 客户端，发送 GET 请求到 github.com | 是（`web_tools.py` 使用） |
| `beautifulsoup4` | HTML 解析，提取 Trending 仓库列表和 README 内容 | 是（`web_tools.py` 使用） |

### 10.2 Python 标准库依赖

| 模块 | 用途 | 使用工具 |
|------|------|---------|
| `datetime` | 生成时间戳，构造文件名 | trending |
| `os` / `pathlib` | 目录创建、路径处理 | trending |
| `re` | HTML 文本清洗、数值提取、URL 校验 | trending, readme |
| `urllib.parse` | URL 参数编码 | trending |
| `time` | 多 URL 请求间隔控制 | readme |

**结论：无需在 `pyproject.toml` 中新增任何依赖项。**
