"""SkillLoader——扫描并加载内置 Skill 和用户自定义 Skill。"""

import os
import logging
from pathlib import Path

from graph_agent.skill.parser import SkillParser
from graph_agent.skill.models import SkillDef

logger = logging.getLogger("graph_agent")


class SkillLoader:
    """Skill 加载器——扫描内置 Skill 和用户自定义 Skill 并加载。

    内置 Skill：prompts/skills/ 下的 .md 文件（一个文件 = 一个 Skill）。
    用户 Skill：项目根目录 skills/ 下的子文件夹（一个文件夹 = 一个 Skill，含 SKILL.md）。

    加载顺序：先加载内置 Skill，再加载用户 Skill。
    如遇同名 Skill，用户 Skill 覆盖内置 Skill。
    """

    def __init__(self, user_skills_root: str | None = None):
        # 内置 Skill 存放在 prompts/skills/，与 prompts/guard/、prompts/plan/ 同级
        _project_root = Path(__file__).parent.parent.parent.parent
        self._builtin_root = _project_root / "prompts" / "skills"

        if user_skills_root is None:
            user_skills_root = os.getenv("SKILLS_ROOT")
        if user_skills_root is None:
            user_skills_root = str(_project_root / "skills")
        self._user_root = Path(user_skills_root)

        self._parser = SkillParser()

    def load_all(self) -> list[SkillDef]:
        skills: dict[str, SkillDef] = {}

        for skill_def in self._scan_builtin():
            skills[skill_def.meta.name] = skill_def

        for skill_def in self._scan_user():
            if skill_def.meta.name in skills:
                logger.info(
                    f"用户 Skill '{skill_def.meta.name}' 覆盖同名的内置 Skill"
                )
            skills[skill_def.meta.name] = skill_def

        return list(skills.values())

    def _scan_builtin(self) -> list[SkillDef]:
        result: list[SkillDef] = []

        if not self._builtin_root.exists():
            return result

        for md_file in sorted(self._builtin_root.glob("*.md")):
            skill_name = md_file.stem
            try:
                meta, body, summary = self._parser.parse(md_file)

                if meta.type != "builtin":
                    logger.warning(
                        f"内置 Skill '{skill_name}' 的 type 字段为 '{meta.type}'，"
                        f"已自动修正为 'builtin'"
                    )
                    meta.type = "builtin"

                if meta.name != skill_name:
                    logger.warning(
                        f"内置 Skill 文件名 '{skill_name}.md' 与 frontmatter name "
                        f"'{meta.name}' 不一致，已以文件名为准"
                    )
                    meta.name = skill_name

                result.append(SkillDef(
                    meta=meta,
                    body=body,
                    summary=summary,
                    folder_path=md_file,
                ))
            except Exception as e:
                logger.warning(f"加载内置 Skill 失败: {skill_name} — {e}")

        return result

    def _scan_user(self) -> list[SkillDef]:
        result: list[SkillDef] = []

        if not self._user_root.exists():
            return result

        for skill_dir in sorted(self._user_root.iterdir()):
            if not skill_dir.is_dir():
                continue

            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue

            try:
                meta, body, summary = self._parser.parse(skill_md)

                if meta.type != "user":
                    logger.warning(
                        f"用户 Skill '{skill_dir.name}' 的 type 字段为 '{meta.type}'，"
                        f"已自动修正为 'user'"
                    )
                    meta.type = "user"

                reference_dir = skill_dir / "reference"
                reference_files = []
                if reference_dir.exists() and reference_dir.is_dir():
                    reference_files = sorted(
                        p for p in reference_dir.iterdir()
                        if p.is_file() and p.suffix in (".md", ".txt")
                    )

                scripts_dir = skill_dir / "scripts"
                script_files = []
                if scripts_dir.exists() and scripts_dir.is_dir():
                    script_files = sorted(
                        p for p in scripts_dir.iterdir()
                        if p.is_file() and p.suffix in (".py", ".sh", ".bat")
                    )

                result.append(SkillDef(
                    meta=meta,
                    body=body,
                    summary=summary,
                    folder_path=skill_dir,
                    reference_dir=reference_dir if reference_files else None,
                    scripts_dir=scripts_dir if script_files else None,
                    reference_files=reference_files,
                    script_files=script_files,
                ))
            except Exception as e:
                logger.warning(f"加载用户 Skill 失败: {skill_dir.name} — {e}")

        return result

    def reload(self) -> list[SkillDef]:
        return self.load_all()

    @property
    def builtin_root(self) -> Path:
        return self._builtin_root

    @property
    def user_root(self) -> Path:
        return self._user_root
