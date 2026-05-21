"""SkillRegister——Skill 注册中心。"""

from graph_agent.skill.models import SkillDef
from graph_agent.skill.loader import SkillLoader


class SkillRegister:
    """Skill 注册中心——维护所有可用的 Skill。"""

    def __init__(self, loader: SkillLoader | None = None):
        self._skills: dict[str, SkillDef] = {}
        self._loader = loader or SkillLoader()
        self._load()

    def _load(self) -> None:
        self._skills.clear()
        for skill in self._loader.load_all():
            self._skills[skill.meta.name] = skill

    def reload(self) -> None:
        self._loader.reload()
        self._load()

    def get(self, name: str) -> SkillDef:
        if name not in self._skills:
            raise KeyError(f"Skill 未注册: {name}")
        return self._skills[name]

    def find_by_description(self, query: str) -> list[SkillDef]:
        query_lower = query.lower()

        for skill in self._skills.values():
            if skill.meta.name.lower() == query_lower:
                return [skill]

        results = []
        for skill in self._skills.values():
            desc_lower = skill.meta.description.lower()
            summary_lower = skill.summary.lower()
            if query_lower in desc_lower or query_lower in summary_lower:
                results.append(skill)

        return results

    def list_all(self) -> list[SkillDef]:
        return list(self._skills.values())

    def describe_all_for_llm(self) -> str:
        if not self._skills:
            return "（暂无可用 Skill）"

        lines = []
        for skill in self._skills.values():
            lines.append(
                f"- **{skill.meta.name}**: {skill.meta.description}"
            )
        return "\n".join(lines)

    def to_subagent_configs(self) -> list[dict]:
        configs = []
        for skill in self._skills.values():
            tool_names = [t.name for t in skill.meta.tools]

            script_tools = []
            if skill.meta.type == "user" and skill.script_files:
                for script_path in skill.script_files:
                    script_tools.append(script_path.name)

            configs.append({
                "name": skill.meta.name,
                "description": skill.meta.description,
                "skills": [skill.meta.name],
                "tools": tool_names,
                "script_tools": script_tools,
                "max_iterations": skill.meta.max_iterations,
                "llm_config": skill.meta.llm_config,
                "_source": f"skill:{skill.meta.type}",
                "_skill_def": skill,
            })
        return configs
