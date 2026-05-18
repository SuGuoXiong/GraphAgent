import json
from typing import Any

from graph_agent.tools import tool


@tool("json_load", "将json格式的文本解析为dict")
def json_load(text: str) -> dict[Any, Any]:
    try:
        return dict(json.loads(text))
    except Exception as e:
        raise ValueError(f"解析失败: {str(e)}")

@tool("json_dump", "将对象转换为json格式的文本并返回")
def json_dump(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)