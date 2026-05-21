"""Skill 系统——文档驱动的 SubAgent 定义与管理。

将技能定义从"配置驱动"升级为"文档驱动"：
- 内置 Skill：src/graph_agent/skills/*.md（一个 .md 文件 = 一个 Skill）
- 用户自定义 Skill：skills/*/SKILL.md（一个子文件夹 = 一个 Skill）
"""

from graph_agent.skill.models import (
    ToolParamDef,
    SkillToolDef,
    SkillMeta,
    SkillDef,
)
from graph_agent.skill.parser import SkillParser
from graph_agent.skill.loader import SkillLoader
from graph_agent.skill.register import SkillRegister

__all__ = [
    "ToolParamDef",
    "SkillToolDef",
    "SkillMeta",
    "SkillDef",
    "SkillParser",
    "SkillLoader",
    "SkillRegister",
]
