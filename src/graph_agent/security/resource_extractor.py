"""资源提取器 —— 从工具名和参数中提取操作目标实体。"""

import os


def extract_resource(tool_name: str, tool_args: dict) -> str:
    """从工具名称和参数中提取操作资源标识。

    仅对 file_tools.py 和 cmd_tools.py 中的工具提取 Resource，
    其余工具的 Resource 统一返回 "*"。
    """
    if tool_name in ("read_file", "write_file", "append_file",
                     "delete_file", "file_exists"):
        path = tool_args.get("file_path", "")
        return f"file:{os.path.abspath(path) if path else ''}"

    if tool_name in ("list_directory", "delete_directory"):
        path = tool_args.get("dir_path", "")
        return f"dir:{os.path.abspath(path) if path else ''}"

    if tool_name == "run_command":
        command = tool_args.get("command", "")
        return f"cmd:{command.strip() if command else ''}"

    return "*"
