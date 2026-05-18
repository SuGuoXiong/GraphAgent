"""LLM 提供商实现模块。

包含各 LLM 提供商的具体实现，自动注册到 LLMFactory。
"""
import importlib
from pathlib import Path


def auto_discover_providers() -> None:
    """自动扫描并注册 providers 目录下的所有提供商。"""
    package_path = Path(__file__).parent

    for file_path in package_path.glob("*.py"):
        if file_path.name.startswith("_"):
            continue

        module_name = f"graph_agent.llm.providers.{file_path.stem}"
        importlib.import_module(module_name)


# 自动发现并注册所有提供商
auto_discover_providers()
