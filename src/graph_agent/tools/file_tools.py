"""文件系统读写工具。"""

import os
import shutil
from pathlib import Path

from graph_agent.tools.base import tool


def _resolve_path(path: str) -> Path:
    """解析并规范化路径，拒绝路径遍历攻击。"""
    resolved = Path(path).resolve()
    if ".." in resolved.parts:
        raise ValueError("路径包含非法的上级目录引用")
    return resolved


@tool("read_file", "读取指定文件的内容，返回文本内容。参数 file_path: 文件路径", risk_level="low")
def read_file(file_path: str) -> str:
    """读取文件内容。

    Args:
        file_path: 文件路径

    Returns:
        文件内容字符串
    """
    path = _resolve_path(file_path)
    if not path.exists():
        return f"错误: 文件不存在 - {file_path}"
    if not path.is_file():
        return f"错误: 路径不是文件 - {file_path}"
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"错误: 无法以UTF-8编码读取文件 - {file_path}"
    except Exception as e:
        return f"错误: 读取文件失败 - {e}"


@tool("write_file", "将内容写入指定文件，覆盖已有内容。参数 file_path: 文件路径, content: 要写入的内容", risk_level="medium")
def write_file(file_path: str, content: str) -> str:
    """写入内容到文件。

    Args:
        file_path: 文件路径
        content: 要写入的内容

    Returns:
        操作结果
    """
    path = _resolve_path(file_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"成功写入文件: {file_path} ({len(content)} 字符)"
    except Exception as e:
        return f"错误: 写入文件失败 - {e}"


@tool("append_file", "向指定文件追加内容，文件不存在时会自动创建。参数 file_path: 文件路径, content: 要追加的内容", risk_level="medium")
def append_file(file_path: str, content: str) -> str:
    """向文件追加内容。

    Args:
        file_path: 文件路径
        content: 要追加的内容

    Returns:
        操作结果
    """
    path = _resolve_path(file_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(content)
        return f"成功追加内容到文件: {file_path} ({len(content)} 字符)"
    except Exception as e:
        return f"错误: 追加文件失败 - {e}"


@tool("list_directory", "列出指定目录下的所有文件和子目录。参数 dir_path: 目录路径", risk_level="low")
def list_directory(dir_path: str) -> str:
    """列出目录内容。

    Args:
        dir_path: 目录路径

    Returns:
        目录内容列表
    """
    path = _resolve_path(dir_path)
    if not path.exists():
        return f"错误: 目录不存在 - {dir_path}"
    if not path.is_dir():
        return f"错误: 路径不是目录 - {dir_path}"
    try:
        items = []
        for entry in sorted(path.iterdir()):
            item_type = "DIR" if entry.is_dir() else "FILE"
            size = ""
            if entry.is_file():
                size = f" ({entry.stat().st_size} B)"
            items.append(f"  [{item_type}] {entry.name}{size}")
        if not items:
            return f"目录为空: {dir_path}"
        return f"目录内容 ({dir_path}):\n" + "\n".join(items)
    except Exception as e:
        return f"错误: 列出目录失败 - {e}"


@tool("delete_file", "删除指定文件。参数 file_path: 文件路径", risk_level="high")
def delete_file(file_path: str) -> str:
    """删除文件。

    Args:
        file_path: 文件路径

    Returns:
        操作结果
    """
    path = _resolve_path(file_path)
    if not path.exists():
        return f"错误: 文件不存在 - {file_path}"
    if not path.is_file():
        return f"错误: 路径不是文件 - {file_path}"
    try:
        path.unlink()
        return f"成功删除文件: {file_path}"
    except Exception as e:
        return f"错误: 删除文件失败 - {e}"


@tool("delete_directory", "删除指定目录（包括其所有内容）。参数 dir_path: 目录路径", risk_level="high")
def delete_directory(dir_path: str) -> str:
    """删除目录及其所有内容。

    Args:
        dir_path: 目录路径

    Returns:
        操作结果
    """
    path = _resolve_path(dir_path)
    if not path.exists():
        return f"错误: 目录不存在 - {dir_path}"
    if not path.is_dir():
        return f"错误: 路径不是目录 - {dir_path}"
    try:
        shutil.rmtree(path)
        return f"成功删除目录: {dir_path}"
    except Exception as e:
        return f"错误: 删除目录失败 - {e}"


@tool("file_exists", "检查文件或目录是否存在。参数 path: 要检查的路径", risk_level="low")
def file_exists(check_path: str) -> str:
    """检查文件或目录是否存在。

    Args:
        check_path: 要检查的路径

    Returns:
        是否存在
    """
    path = _resolve_path(check_path)
    if path.exists():
        item_type = "目录" if path.is_dir() else "文件"
        return f"{item_type}存在: {check_path}"
    return f"路径不存在: {check_path}"
