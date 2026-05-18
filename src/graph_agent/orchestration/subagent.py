"""SubAgent 注册中心——从 config/subagents.yaml 加载配置。

SubAgent 元数据全部来自配置文件，代码中不硬编码任何 SubAgent 定义。
SubAgent 执行节点位于 graph_agent.node.subagent。
"""

import yaml
from pathlib import Path
from dataclasses import dataclass

from graph_agent.orchestration.prompt_loader import PromptLoader


@dataclass
class SubAgentConfig:
    """SubAgent 配置——从 config/subagents.yaml 加载。"""
    name: str
    description: str
    skills: list[str]
    tools: list[str]
    prompt_file: str
    llm_config: dict | None = None
    max_iterations: int = 5

    def load_system_prompt(self, loader: PromptLoader, **variables) -> str:
        """加载系统提示词，工具列表通过 {available_tools} 注入。"""
        variables.setdefault("available_tools", ", ".join(self.tools))
        return loader.load_with_context("subagent", self.prompt_file, **variables)


class SubAgentRegistry:
    """SubAgent 注册中心——从 config/subagents.yaml 加载。

    支持热加载：调用 reload() 重新读取配置文件。
    """

    def __init__(self, config_path: str | None = None,
                 prompt_loader: PromptLoader | None = None):
        self._agents: dict[str, SubAgentConfig] = {}
        self._prompt_loader = prompt_loader or PromptLoader()
        self._config_path = config_path or self._default_config_path()
        self._load_from_config()

    @staticmethod
    def _default_config_path() -> str:
        project_root = Path(__file__).parent.parent.parent.parent
        return str(project_root / "config" / "subagents.yaml")

    def _load_from_config(self) -> None:
        """从 YAML 配置文件加载 SubAgent 定义。"""
        path = Path(self._config_path)
        if not path.exists():
            return
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        self._agents.clear()
        for item in data.get("subagents", []):
            config = SubAgentConfig(
                name=item["name"],
                description=item["description"],
                skills=item["skills"],
                tools=item["tools"],
                prompt_file=item["prompt_file"],
                max_iterations=item.get("max_iterations", 5),
                llm_config=item.get("llm_config"),
            )
            self._agents[config.name] = config

    def reload(self) -> None:
        """热加载：重新读取配置文件。"""
        self._prompt_loader.clear_cache()
        self._load_from_config()

    def get(self, name: str) -> SubAgentConfig:
        return self._agents[name]

    def find_by_skill(self, skill: str) -> list[SubAgentConfig]:
        """根据技能标签查找匹配的 SubAgent。"""
        return [agent for agent in self._agents.values() if skill in agent.skills]

    def list_all(self) -> list[SubAgentConfig]:
        return list(self._agents.values())

    def describe_all_for_llm(self) -> str:
        """生成供 PlanAgent 使用的 SubAgent 清单（仅含名称/描述/技能，不含工具）。"""
        if not self._agents:
            return "（暂无可用 SubAgent）"
        lines = []
        for agent in self._agents.values():
            skills_str = ", ".join(agent.skills)
            lines.append(f"- **{agent.name}**: {agent.description}（技能: {skills_str}）")
        return "\n".join(lines)
