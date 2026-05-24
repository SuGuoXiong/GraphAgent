"""GraphAgent 工具模块核心基础类。

提供工具定义、注册和管理功能。
"""
import sys
import importlib
from pathlib import Path
from typing import Any, Callable
from inspect import signature

from langchain_core.tools import StructuredTool


class AgentTool:
    """封装单个工具的元数据和执行逻辑。

    risk_level 决定执行方式：low → 直接执行，medium → 进程隔离，high → Docker 沙箱。
    """

    name: str
    description: str
    parameters: list[dict[str, str]]
    func: Callable[..., str]
    risk_level: str

    def __init__(
        self,
        name: str,
        description: str,
        func: Callable[..., str],
        parameters: list[dict[str, str]],
        risk_level: str = "medium",
    ):
        self.name = name
        self.description = description
        self.func = func
        self.parameters = parameters
        self.risk_level = risk_level

    def run(self, **kwargs: Any) -> str:
        """执行工具逻辑（含 before/after_tool_call Hook 检查点）。

        流程：
        1. before_tool_call Hook 链（含 RBAC 校验）
        2. 根据 risk_level 选择执行方式
        3. after_tool_call Hook 链
        """
        from graph_agent.hook import get_hook_executor, HookContext, HookAction, HookAbortError
        from graph_agent.node.subagent import get_current_agent_name

        executor = get_hook_executor()
        ctx = HookContext(
            checkpoint="before_tool_call",
            tool_name=self.name,
            tool_args=kwargs,
            agent_name=get_current_agent_name(),
        )
        ctx, decision = executor.execute("before_tool_call", ctx)

        if decision and decision.action == HookAction.SKIP:
            return decision.fallback_result or f"工具 '{self.name}' 被 Hook 跳过"
        if decision and decision.action == HookAction.ABORT:
            raise HookAbortError(decision.reason)

        kwargs = ctx.tool_args or kwargs

        try:
            if self.risk_level == "low":
                result = self._run_direct(kwargs)
            elif self.risk_level == "medium":
                result = self._run_isolated(kwargs)
            elif self.risk_level == "high":
                result = self._run_in_sandbox(kwargs)
            else:
                result = self._run_direct(kwargs)
        except Exception as e:
            ctx.tool_result = None
            ctx.tool_error = str(e)
            ctx.checkpoint = "after_tool_call"
            executor.execute("after_tool_call", ctx)
            raise

        ctx.tool_result = result
        ctx.checkpoint = "after_tool_call"
        executor.execute("after_tool_call", ctx)

        return result

    def _run_direct(self, tool_args: dict) -> str:
        """直接在当前进程中执行（低风险工具）。"""
        return self.func(**tool_args)

    def _run_isolated(self, tool_args: dict) -> str:
        """在独立子进程中执行（中风险工具）。

        使用 subprocess 而非 multiprocessing，避免 Windows spawn 模式
        下模块重导入引发的句柄冲突（OSError: WinError 6）。
        """
        import subprocess
        import json
        import os

        # 闭包/lambda 等不可导入函数降级为直接执行
        func_module = getattr(self.func, "__module__", None)
        func_qualname = getattr(self.func, "__qualname__", None)
        if not func_module or not func_qualname or func_module == "__main__":
            return self._run_direct(tool_args)

        input_data = json.dumps({
            "module": func_module,
            "qualname": func_qualname,
            "args_json": json.dumps(tool_args),
        })

        env = os.environ.copy()
        pythonpath = os.pathsep.join(sys.path)
        if "PYTHONPATH" in env:
            env["PYTHONPATH"] = pythonpath + os.pathsep + env["PYTHONPATH"]
        else:
            env["PYTHONPATH"] = pythonpath

        try:
            result = subprocess.run(
                [sys.executable, "-c", _ISOLATED_WORKER_SCRIPT],
                input=input_data,
                capture_output=True,
                text=True,
                timeout=30,
                env=env,
            )
            if result.returncode == 0:
                return result.stdout.strip()
            return f"Error: {result.stderr.strip()}"
        except subprocess.TimeoutExpired:
            return "Error: 工具执行超时（30s）"

    def _run_in_sandbox(self, tool_args: dict) -> str:
        """在 Docker 沙箱中执行（高风险工具）。

        Docker 不可用时降级为进程隔离并发出警告。
        """
        import subprocess
        import json
        import tempfile
        import os
        import warnings

        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', delete=False
        ) as f:
            json.dump({"tool": self.name, "args": tool_args}, f)
            input_file = f.name

        try:
            result = subprocess.run([
                "docker", "run", "--rm",
                "--network", "none",
                "--memory", "256m",
                "--cpus", "1",
                "--read-only",
                "--tmpfs", "/tmp:noexec",
                "-v", f"{input_file}:/input.json:ro",
                "graphagent-sandbox:latest",
                "python", "-m", "sandbox_executor",
                "--input", "/input.json",
            ], capture_output=True, text=True, timeout=60)
            return result.stdout if result.returncode == 0 else f"Sandbox Error: {result.stderr}"
        except subprocess.TimeoutExpired:
            return "Error: 沙箱执行超时（60s）"
        except FileNotFoundError:
            warnings.warn("Docker 不可用，高风险工具降级为进程隔离执行")
            return self._run_isolated(tool_args)
        finally:
            try:
                os.unlink(input_file)
            except OSError:
                pass

    def to_langchain_tool(self) -> StructuredTool:
        """转换为 LangChain 兼容的工具。"""
        return StructuredTool.from_function(
            func=self.func,
            name=self.name,
            description=self.description
        )


class ToolCenter:
    """工具管理中心。

    提供工具的注册、发现和查询功能。
    """

    tools: dict[str, AgentTool]

    def __init__(self) -> None:
        self.tools = {}

    def register(self, tool: AgentTool) -> None:
        """手动注册工具。"""
        self.tools[tool.name] = tool

    def unregister(self, tool_name: str) -> None:
        """注销工具。"""
        if tool_name in self.tools:
            del self.tools[tool_name]

    def get_tool(self, name: str) -> AgentTool:
        """获取指定工具。"""
        return self.tools[name]

    def list_tools(self) -> list[AgentTool]:
        """列出所有已注册工具。"""
        return list(self.tools.values())

    def get_langchain_tools(self) -> list[StructuredTool]:
        """转换为 LangChain 兼容的工具列表。"""
        return [tool.to_langchain_tool() for tool in self.tools.values()]

    def auto_discover(self, tools_package: str = "graph_agent.tools") -> None:
        """自动扫描并注册 tools 目录下所有使用 @tool 装饰器的工具。"""
        try:
            package = importlib.import_module(tools_package)
        except ImportError:
            package_path = Path(__file__).parent
            sys.path.insert(0, str(package_path.parent.parent))
            package = importlib.import_module(tools_package)

        package_path = Path(package.__file__).parent

        for file_path in package_path.glob("*.py"):
            if file_path.name.startswith("_") or file_path.name == "base.py":
                continue

            module_name = f"{tools_package}.{file_path.stem}"
            if module_name in sys.modules:
                module = sys.modules[module_name]
            else:
                module = importlib.import_module(module_name)

            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if isinstance(attr, AgentTool):
                    self.register(attr)


def tool(name: str, description: str, risk_level: str = "medium"):
    """工具装饰器，用于定义 Agent 工具。

    Args:
        name: 工具名称
        description: 工具功能描述，供 LLM 理解
        risk_level: 风险等级，low/medium/high，默认 medium
                    low   → 直接执行
                    medium → 进程隔离
                    high  → Docker 沙箱
    """
    def decorator(func: Callable[..., str]) -> AgentTool:
        sig = signature(func)
        parameters = []

        for param in sig.parameters.values():
            parameters.append({
                "name": param.name,
                "type": str(param.annotation) if param.annotation != param.empty else "Any"
            })

        return AgentTool(name, description, func, parameters, risk_level=risk_level)

    return decorator


_ISOLATED_WORKER_SCRIPT = """\
import sys, json, importlib

input_data = json.loads(sys.stdin.read())
module_name = input_data["module"]
qualname = input_data["qualname"]
args = json.loads(input_data["args_json"])

module = importlib.import_module(module_name)
func = module
for part in qualname.split("."):
    func = getattr(func, part)

# @tool 装饰器返回 AgentTool 实例，需解包为原始可调用函数
if hasattr(func, "func") and hasattr(func, "run"):
    func = func.func

try:
    result = func(**args)
    print(result)
except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
"""
