import shlex
import subprocess

from graph_agent.tools.base import tool

SAFE_COMMANDS = {"ls", "dir", "pwd", "echo", "cat", "type"}


@tool("run_command", "执行shell命令并返回执行结果，仅支持安全的只读命令")
def run_cmd(cmd: str) -> str:
    try:
        args = shlex.split(cmd)
    except ValueError as e:
        return f"命令解析失败: {e}"

    if not args:
        return "错误: 空命令"

    base_cmd = args[0]
    if base_cmd not in SAFE_COMMANDS:
        return f"禁止命令: {base_cmd}"

    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=10
        )
        output = result.stdout
        if result.stderr:
            output += f"\n错误:\n{result.stderr}"
        return output if output else "(无输出)"
    except FileNotFoundError:
        return f"命令未找到: {base_cmd}"
    except subprocess.TimeoutExpired:
        return f"命令执行超时: {cmd}"
    except Exception as e:
        return f"执行失败: {e}"