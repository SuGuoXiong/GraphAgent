"""提示词加载器——从 prompts/ 目录读取 Agent 提示词，支持缓存和变量插值。"""

import os
from pathlib import Path


class PromptLoader:
    """从文件系统加载 Agent 提示词，支持热加载（清除缓存后重新读取）。"""

    _FALLBACKS: dict[str, dict[str, str]] = {
        "guard": {
            "intent_analysis": "你是一个 GuardAgent，负责准确理解用户需求并提炼核心意图。",
            "plan_review": "你是一个 GuardAgent，负责审核执行方案的合理性和完整性。",
            "result_review": "你是一个 GuardAgent，负责验收最终结果是否符合用户期望。",
        },
        "plan": {
            "plan_generation": "你是一个 PlanAgent，负责将任务分解为可执行的子任务。",
            "task_dispatch": "你是一个 PlanAgent，负责为每个子任务匹配对应的 SubAgent 并派发。",
            "result_synthesis": "你是一个 PlanAgent，负责汇总各 SubAgent 的执行结果。",
        },
    }

    def __init__(self, prompts_root: str | None = None):
        if prompts_root is None:
            prompts_root = os.getenv("PROMPTS_ROOT")
        if prompts_root is None:
            project_root = Path(__file__).parent.parent.parent.parent
            prompts_root = str(project_root / "prompts")
        self._root = Path(prompts_root)
        self._cache: dict[str, str] = {}

    def load(self, agent_type: str, prompt_name: str) -> str:
        """加载指定 Agent 的提示词。

        加载优先级：
            1. 从 prompts/{agent_type}/{prompt_name}.txt 文件读取
            2. 文件不存在时使用内置 _FALLBACKS 默认值
            3. 无默认值时抛出 FileNotFoundError
        """
        cache_key = f"{agent_type}/{prompt_name}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        file_path = self._root / agent_type / f"{prompt_name}.txt"
        if file_path.exists():
            content = file_path.read_text(encoding="utf-8")
            self._cache[cache_key] = content
            return content

        fallback = self._get_fallback(agent_type, prompt_name)
        self._cache[cache_key] = fallback
        return fallback

    def load_with_context(self, agent_type: str, prompt_name: str,
                          **variables: str) -> str:
        """加载提示词并用变量插值填充。

        Example:
            loader.load_with_context(
                "subagent", "file_agent",
                task_description="读取文件并提取数字",
                available_tools="read_file, calculator",
            )
        """
        template = self.load(agent_type, prompt_name)
        try:
            return template.format(**variables)
        except KeyError as e:
            return template + f"\n\n[警告: 提示词变量 {e} 未提供]"

    def _get_fallback(self, agent_type: str, prompt_name: str) -> str:
        prompts = self._FALLBACKS.get(agent_type, {})
        if prompt_name in prompts:
            return prompts[prompt_name]
        raise FileNotFoundError(
            f"提示词文件不存在且无默认值: prompts/{agent_type}/{prompt_name}.txt"
        )

    def clear_cache(self) -> None:
        """清除缓存，强制下次加载时重新读取文件（热加载）。"""
        self._cache.clear()

    def list_all(self) -> dict[str, list[str]]:
        """列出 prompts/ 目录下所有可用的提示词。"""
        result: dict[str, list[str]] = {}
        if not self._root.exists():
            return result
        for agent_dir in self._root.iterdir():
            if agent_dir.is_dir():
                prompts = [p.stem for p in agent_dir.glob("*.txt")]
                result[agent_dir.name] = prompts
        return result
