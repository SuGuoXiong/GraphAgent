import requests
from bs4 import BeautifulSoup

from graph_agent.tools import tool


@tool("search_web", "通过百度搜索引擎对问题进行检索")
def search_web(query: str) -> str:
    url = f"https://www.baidu.com/s?wd={query}"
    res = requests.get(url, timeout=10)
    soup = BeautifulSoup(res.text, "html.parser")
    results = []
    for a in soup.find_all("a", href=True)[:5]:
        results.append(a.get_text(strip=True))
    return "\n".join(results)

@tool("fetch_web", "获取指定url的网址并解析为字符串返回")
def fetch_web(url: str) -> str:
    res = requests.get(url, timeout=10)
    soup = BeautifulSoup(res.text, "html.parser")
    return soup.get_text(strip=True)[:2000]