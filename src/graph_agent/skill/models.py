"""Skill 系统数据模型。"""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ToolParamDef:
    """工具参数定义"""
    name: str
    type: str
    description: str
    required: bool = True


@dataclass
class SkillToolDef:
    """Skill 中声明的工具定义"""
    name: str
    description: str
    parameters: list[ToolParamDef] = field(default_factory=list)


@dataclass
class SkillMeta:
    """SKILL.md frontmatter 解析结果"""
    name: str
    type: str
    description: str
    tools: list[SkillToolDef] = field(default_factory=list)
    max_iterations: int = 5
    llm_config: dict | None = None


@dataclass
class SkillDef:
    """完整的 Skill 定义"""
    meta: SkillMeta
    body: str
    summary: str
    folder_path: Path
    reference_dir: Path | None = None
    scripts_dir: Path | None = None
    reference_files: list[Path] = field(default_factory=list)
    script_files: list[Path] = field(default_factory=list)
