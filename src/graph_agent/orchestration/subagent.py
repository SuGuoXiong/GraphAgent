"""SubAgent 注册中心——全部配置来自 SkillRegister。

Skill 系统是 SubAgent 的唯一来源，系统提示词由 build_skill_system_prompt() 动态生成。
"""

from dataclasses import dataclass

from graph_agent.skill.models import SkillDef


@dataclass
class SubAgentConfig:
    """SubAgent 配置——全部来自 Skill 系统。"""
    name: str
    description: str
    skills: list[str]
    tools: list[str]
    llm_config: dict | None = None
    max_iterations: int = 5
    _skill_def: SkillDef | None = None

    def load_system_prompt(self, **variables) -> str:
        """根据 SkillDef 动态构建系统提示词。"""
        return build_skill_system_prompt(
            self._skill_def,
            variables.get("task_description", ""),
        )


class SubAgentRegistry:
    """SubAgent 注册中心——全部配置来自 SkillRegister。"""

    def __init__(self, skill_register=None):
        self._agents: dict[str, SubAgentConfig] = {}

        if skill_register is not None:
            self._skill_register = skill_register
        else:
            from graph_agent.skill.register import SkillRegister
            self._skill_register = SkillRegister()

        self._load_from_skills()

    def _load_from_skills(self) -> None:
        self._agents.clear()
        for skill_config in self._skill_register.to_subagent_configs():
            all_tools = list(skill_config["tools"])
            all_tools.extend(skill_config.get("script_tools", []))

            config = SubAgentConfig(
                name=skill_config["name"],
                description=skill_config["description"],
                skills=skill_config["skills"],
                tools=all_tools,
                max_iterations=skill_config.get("max_iterations", 5),
                llm_config=skill_config.get("llm_config"),
                _skill_def=skill_config.get("_skill_def"),
            )
            self._agents[config.name] = config

    def reload(self) -> None:
        self._skill_register.reload()
        self._load_from_skills()

    def get(self, name: str) -> SubAgentConfig:
        return self._agents[name]

    def find_by_skill(self, skill: str) -> list[SubAgentConfig]:
        """多级匹配：精确匹配 → 名称包含 → 描述包含。"""
        if not skill:
            return []
        skill_lower = skill.lower().replace("_", "-").replace(" ", "-")

        # Level 1: 精确名称匹配（忽略大小写、下划线/连字符归一化）
        for agent in self._agents.values():
            for s in agent.skills:
                if skill_lower == s.lower().replace("_", "-").replace(" ", "-"):
                    return [agent]

        # Level 2: 技能名称包含查询词（查询词至少3字符）
        if len(skill_lower) >= 3:
            matches = []
            for agent in self._agents.values():
                for s in agent.skills:
                    s_norm = s.lower().replace("_", "-").replace(" ", "-")
                    if skill_lower in s_norm or s_norm in skill_lower:
                        matches.append(agent)
                        break
            if matches:
                return matches

        # Level 3: 描述文本包含查询词
        for agent in self._agents.values():
            desc_lower = agent.description.lower()
            if skill_lower in desc_lower or any(
                word in desc_lower for word in skill_lower.split("-") if len(word) >= 2
            ):
                matches.append(agent)
        return matches

    def list_all(self) -> list[SubAgentConfig]:
        return list(self._agents.values())

    def describe_all_for_llm(self) -> str:
        if not self._agents:
            return "（暂无可用 SubAgent）"
        lines = []
        for agent in self._agents.values():
            skills_str = ", ".join(agent.skills)
            lines.append(f"- **{agent.name}**: {agent.description}（技能: {skills_str}）")
        return "\n".join(lines)


def build_skill_system_prompt(skill_def: SkillDef, task_description: str) -> str:
    """根据 SkillDef 构建 SubAgent 的系统提示词。"""
    meta = skill_def.meta

    tools_desc = ""
    if meta.tools:
        tool_lines = []
        for t in meta.tools:
            params_desc = ", ".join(
                f"{p.name}: {p.type}" + ("?" if not p.required else "")
                for p in t.parameters
            )
            tool_lines.append(f"- **{t.name}**({params_desc}): {t.description}")
        tools_desc = "## 可用工具\n" + "\n".join(tool_lines)

    scripts_desc = ""
    if meta.type == "user" and skill_def.script_files:
        script_lines = []
        for sf in skill_def.script_files:
            script_lines.append(
                f"- **{sf.name}**: 位于 `skills/{meta.name}/scripts/{sf.name}` 的定制脚本，"
                f"通过 `run_command` 工具调用"
            )
        scripts_desc = "\n## 定制脚本\n" + "\n".join(script_lines)

    ref_desc = ""
    if skill_def.reference_files:
        ref_paths = "\n".join(
            f"  - skills/{meta.name}/reference/{f.name}"
            for f in skill_def.reference_files
        )
        ref_desc = (
            f"\n## 参考文档\n"
            f"如果需要更多信息，可以使用 `read_file` 工具读取以下参考文档：\n{ref_paths}"
        )

    return f"""你是一个 {meta.name} 专家。

## 能力说明
{meta.description}

## 执行指南
{skill_def.body}

{tools_desc}{scripts_desc}{ref_desc}

## 当前任务
{task_description}

## 关键规则
1. 如果当前任务是纯文本对话、问候、闲聊或简单的知识问答，直接使用文字回答，无需调用任何工具。
2. 如果任务需要产生实际产出（文件读写、命令执行、数据转换、网页抓取等），必须调用相应的工具完成操作，绝不能仅凭文字描述声称已完成。
3. 判断标准：任务完成后用户能拿到一个文件/数据/命令结果吗？如果不能（比如只是聊天），直接用文字回答即可。如果能，必须调用对应工具。
"""


def register_script_tools(skill_def: SkillDef, tool_center) -> list[str]:
    """将 Skill 的 scripts/ 中的脚本注册为临时工具。"""
    import subprocess
    from graph_agent.tools.base import AgentTool

    registered = []
    for script_path in skill_def.script_files:
        tool_name = script_path.name

        def make_script_func(sp):
            def script_func(command: str = "") -> str:
                full_cmd = f"{sp} {command}".strip()
                result = subprocess.run(
                    full_cmd, shell=True, capture_output=True, text=True, timeout=60
                )
                return result.stdout or result.stderr
            return script_func

        tool = AgentTool(
            name=tool_name,
            description=f"执行 Skill '{skill_def.meta.name}' 的定制脚本: {tool_name}",
            func=make_script_func(script_path),
            parameters=[{
                "name": "command",
                "type": "string",
                "description": "传递给脚本的额外命令行参数",
            }],
            risk_level="high",
        )
        tool_center.register(tool)
        registered.append(tool_name)

    return registered
