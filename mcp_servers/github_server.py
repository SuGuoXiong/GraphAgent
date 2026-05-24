"""GitHub MCP Server —— 提供 GitHub Trending 热榜抓取和仓库 README 获取功能。

启动方式:
    python mcp_servers/github_server.py
    # 默认为 stdio transport，由 MCPManager 作为子进程启动

提供工具:
    - fetch_github_trending: 抓取 GitHub Trending 页面并保存为 Markdown 文件
    - fetch_repo_readme: 获取指定仓库的 README.md 内容
"""

import os
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("github")

# ============================================================
# 共享模块
# ============================================================

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/148.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

_session = requests.Session()
_session.headers.update(_HEADERS)

# 可选：从环境变量注入 GitHub Cookie，提升请求成功率
_github_cookie = os.environ.get("GITHUB_COOKIE", "")
if _github_cookie:
    _session.headers["Cookie"] = _github_cookie


def _get_html(url: str, timeout: int = 30) -> "tuple[int, str]":
    """发送 HTTP GET 请求并返回 (status_code, html_text)。

    响应体超过 5MB 时截断。
    超时和网络异常统一转为负数状态码。
    """
    try:
        resp = _session.get(url, timeout=timeout)
        content = resp.text[:5_000_000]
        return resp.status_code, content
    except requests.Timeout:
        return -1, ""
    except requests.RequestException:
        return -2, ""


# ============================================================
# 工具一：fetch_github_trending
# ============================================================

_GITHUB_TRENDING_BASE = "https://github.com/trending"


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
    """抓取 GitHub Trending 数据并保存为 Markdown 文件。"""
    # 1. 参数校验
    if since not in ("daily", "weekly", "monthly"):
        return f"错误: since 参数必须为 daily/weekly/monthly，当前值: {since}"

    if ".." in output_dir or output_dir.startswith("/"):
        return "错误: output_dir 包含非法字符，不允许路径遍历"

    # 2. 构建请求 URL
    if language:
        url = f"{_GITHUB_TRENDING_BASE}/{language}?since={since}"
    else:
        url = f"{_GITHUB_TRENDING_BASE}?since={since}"

    # 3. 发送 HTTP GET 请求
    status_code, html = _get_html(url)
    if status_code == -1:
        return "错误: 请求 GitHub Trending 超时，请检查网络连接后重试"
    if status_code == -2:
        return "错误: 网络连接失败，无法访问 GitHub"
    if status_code != 200:
        return f"错误: GitHub 返回状态码 {status_code}，可能触发了反爬机制"

    # 4. 解析 HTML
    soup = BeautifulSoup(html, "html.parser")
    repos = _parse_trending_articles(soup)

    if not repos:
        # 备选方案：返回页面文本摘要供排查
        text = soup.get_text(strip=True)[:2000]
        return f"警告: 未能从页面中解析到仓库列表，GitHub 页面结构可能已更新。\n页面文本摘要:\n{text}"

    # 5. 生成 Markdown
    cst = datetime.now(timezone(timedelta(hours=8)))
    since_label = {"daily": "Daily（今日）", "weekly": "Weekly（本周）", "monthly": "Monthly（本月）"}[since]
    lang_label = language if language else "不限"

    lines = [
        "# GitHub Trending Repositories",
        "",
        f"> **时间范围**: {since_label}",
        f"> **编程语言**: {lang_label}",
        f"> **生成时间**: {cst.strftime('%Y-%m-%d %H:%M:%S')} CST",
        f"> **数据来源**: [GitHub Trending]({url})",
        "",
        "---",
        "",
    ]

    for i, repo in enumerate(repos, 1):
        lines.append(f"## {i}. [{repo['full_name']}]({repo['url']})")
        lines.append("")
        stars_today = f" (今日 +{repo['stars_today']})" if repo["stars_today"] else ""
        lines.append(f"- **Stars**: {repo['stars']}{stars_today}")
        lines.append(f"- **Forks**: {repo['forks']}")
        if repo["language"]:
            lines.append(f"- **Language**: {repo['language']}")
        if repo["description"]:
            lines.append(f"- **Description**: {repo['description']}")
        lines.append("")
        lines.append("---")
        lines.append("")

    lines.append(f"*共 {len(repos)} 个上榜仓库*")
    lines.append("")

    markdown_content = "\n".join(lines)

    # 6. 写入文件
    try:
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        date_str = cst.strftime("%Y-%m-%d")
        filename = f"github_{since}_{date_str}.md"
        filepath = out_path / filename
        filepath.write_text(markdown_content, encoding="utf-8")
        file_size = filepath.stat().st_size
    except PermissionError:
        return f"错误: 无法写入到目录 {output_dir}，请检查目录权限"

    # 7. 返回结果
    return f"成功: GitHub Trending 数据已保存到 {filepath}\n上榜仓库: {len(repos)} 个\n文件大小: {file_size} 字节"


def _parse_trending_articles(soup: BeautifulSoup) -> list[dict]:
    """解析 GitHub Trending 页面的仓库列表。"""
    repos = []

    # 主选择器: article.Box-row
    articles = soup.select("article.Box-row")
    if not articles:
        articles = soup.find_all("article", class_="Box-row")

    # 备选方案: 通过 h2 中的链接反向定位
    if not articles:
        h2_links = soup.select("h2.h3 a[href]")
        if h2_links:
            articles = []
            for link in h2_links:
                parent = link.find_parent("article")
                if parent:
                    articles.append(parent)
            articles = list(dict.fromkeys(articles))  # 去重保序

    if not articles:
        return repos

    for article in articles:
        try:
            repo = _extract_repo_from_article(article)
            if repo and repo["full_name"]:
                repos.append(repo)
        except Exception:
            continue

    return repos[:25]


def _extract_repo_from_article(article) -> dict | None:
    """从单个 article.Box-row 元素中提取仓库信息。"""
    # 仓库名称和 URL
    h2 = article.find("h2")
    if not h2:
        return None
    link = h2.find("a", href=True)
    if not link:
        return None

    href = link["href"].strip()
    # 清理: /owner/repo → owner/repo
    full_name = href.strip("/")
    url = f"https://github.com/{full_name}"

    # 清理 full_name 中的空白
    parts = full_name.split("/")
    if len(parts) != 2:
        return None

    # 描述
    desc_elem = article.find("p")
    description = desc_elem.get_text(strip=True) if desc_elem else ""

    # 统计信息
    language = ""
    stars = ""
    forks = ""
    stars_today = ""

    # 编程语言
    lang_span = article.find("span", itemprop="programmingLanguage")
    if lang_span:
        language = lang_span.get_text(strip=True)

    # Star / Fork 链接
    stat_links = article.find_all("a", href=True)
    for a in stat_links:
        a_href = a.get("href", "")
        a_text = a.get_text(strip=True)
        if "/stargazers" in a_href and not stars:
            stars = a_text
        elif "/forks" in a_href and not forks:
            forks = a_text

    # 今日新增 Star
    for span in article.find_all("span"):
        text = span.get_text(strip=True)
        if "stars today" in text.lower():
            stars_today = text.replace("stars today", "").strip()
            break
    # 如果没有 "stars today" 文本，尝试找到浮动右侧的 span
    if not stars_today:
        float_spans = article.select("span.float-sm-right, span.d-inline-block.float-sm-right")
        for fs in float_spans:
            text = fs.get_text(strip=True)
            if text and not text.startswith("("):
                stars_today = text
                break

    return {
        "full_name": full_name,
        "url": url,
        "description": description,
        "language": language,
        "stars": stars,
        "forks": forks,
        "stars_today": stars_today,
    }


# ============================================================
# 工具二：fetch_repo_readme
# ============================================================

_REPO_URL_RE = re.compile(
    r"^https://github\.com/([a-zA-Z0-9._-]+)/([a-zA-Z0-9._-]+)$"
)


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
    """获取 GitHub 仓库的 README.md 内容。"""
    # 1. 参数解析与校验
    raw_urls = [u.strip() for u in repo_urls.split(",") if u.strip()]
    if not raw_urls:
        return "错误: repo_urls 参数不能为空"

    if len(raw_urls) > 10:
        return f"错误: 单次最多处理 10 个仓库 URL，当前传入 {len(raw_urls)} 个"

    if max_length_per_repo > 20000:
        max_length_per_repo = 20000

    # 校验并去重
    valid_urls: list[tuple[str, str, str]] = []  # (url, owner, repo)
    seen = set()
    warnings = []

    for url in raw_urls:
        # 去除尾部斜杠
        url = url.rstrip("/")
        m = _REPO_URL_RE.match(url)
        if not m:
            if not url.startswith("https://github.com/"):
                warnings.append(f"警告: 跳过非法 URL（仅支持 GitHub 仓库链接）: {url}")
            else:
                warnings.append(f"警告: 跳过非仓库主页链接（请使用 https://github.com/owner/repo 格式）: {url}")
            continue
        owner, repo = m.group(1), m.group(2)
        if url in seen:
            continue
        seen.add(url)
        valid_urls.append((url, owner, repo))

    if not valid_urls:
        result = "\n".join(warnings) if warnings else ""
        if not result:
            result = "错误: 所有仓库 URL 均无法获取 README 内容，请检查链接是否有效"
        return result

    # 2. 对每个有效 URL 串行处理
    results: list[str] = []
    success_count = 0

    for idx, (url, owner, repo) in enumerate(valid_urls):
        if idx > 0:
            time.sleep(1)  # 请求间隔

        full_name = f"{owner}/{repo}"
        status_code, html = _get_html(url)

        if status_code == -1:
            results.append(f"=== README: {full_name} ===\n(原始 URL: {url})\n\n警告: 请求超时\n")
            continue
        if status_code == -2:
            results.append(f"=== README: {full_name} ===\n(原始 URL: {url})\n\n警告: 网络连接失败\n")
            continue
        if status_code == 404:
            results.append(f"=== README: {full_name} ===\n(原始 URL: {url})\n\n提示: 仓库不存在或为私有仓库\n")
            continue
        if status_code != 200:
            results.append(f"=== README: {full_name} ===\n(原始 URL: {url})\n\n警告: HTTP {status_code}\n")
            continue

        # 3. 解析 HTML 提取 README
        soup = BeautifulSoup(html, "html.parser")
        readme_text = _extract_readme(soup)

        if not readme_text:
            results.append(f"=== README: {full_name} ===\n(原始 URL: {url})\n\n提示: 仓库 {full_name} 未找到 README.md 文件\n")
            continue

        # 4. 文本后处理
        readme_text = re.sub(r"\n{3,}", "\n\n", readme_text)
        if len(readme_text) > max_length_per_repo:
            readme_text = readme_text[:max_length_per_repo] + "\n\n...(内容已截断)"

        results.append(f"=== README: {full_name} ===\n(原始 URL: {url})\n\n{readme_text}\n")
        success_count += 1

    # 5. 汇总返回
    output = "\n".join(warnings) + ("\n\n" if warnings else "")
    output += "\n".join(results)
    output += f"\n=== 处理统计: 成功 {success_count}/{len(valid_urls)} ==="

    if success_count == 0:
        output += "\n错误: 所有仓库 URL 均无法获取 README 内容，请检查链接是否有效"

    return output


def _extract_readme(soup: BeautifulSoup) -> str:
    """从 GitHub 仓库页面 HTML 中提取 README 文本内容。"""
    # 主选择器
    elem = soup.select_one("article.markdown-body.entry-content")
    if not elem:
        # 备选选择器
        elem = soup.select_one("#readme article.markdown-body")
    if not elem:
        # 兜底策略
        elem = soup.select_one("div[itemprop='text']")
    if not elem:
        return ""

    return elem.get_text(separator="\n", strip=True)


# ============================================================
# 启动入口
# ============================================================

if __name__ == "__main__":
    mcp.run(transport="stdio")
