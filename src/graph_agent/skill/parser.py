"""SKILL.md 解析器——解析 frontmatter 和正文。"""

import re
import yaml
from pathlib import Path

from graph_agent.skill.models import SkillMeta, SkillToolDef, ToolParamDef


class SkillParser:
    """SKILL.md 文件解析器。"""

    _FRONTMATTER_RE = re.compile(r'^---\s*\n(.*?)\n---\s*\n', re.DOTALL)

    def parse(self, file_path: Path) -> tuple[SkillMeta, str, str]:
        content = file_path.read_text(encoding="utf-8")

        m = self._FRONTMATTER_RE.match(content)
        if not m:
            raise ValueError(f"{file_path}: 缺少有效的 YAML frontmatter (--- ... ---)")

        raw_yaml = m.group(1)
        body = content[m.end():].strip()

        meta = self._parse_frontmatter(raw_yaml)
        summary = self._extract_summary(body)

        return meta, body, summary

    def _parse_frontmatter(self, raw_yaml: str) -> SkillMeta:
        data = yaml.safe_load(raw_yaml)

        if not data:
            raise ValueError("frontmatter 为空")
        if "name" not in data:
            raise ValueError("frontmatter 缺少必填字段 'name'")
        if "type" not in data:
            raise ValueError("frontmatter 缺少必填字段 'type'（必须为 'builtin' 或 'user'）")
        if data["type"] not in ("builtin", "user"):
            raise ValueError(f"无效的 Skill 类型 '{data['type']}'，必须为 'builtin' 或 'user'")
        if "description" not in data:
            raise ValueError("frontmatter 缺少必填字段 'description'")

        tools = []
        for t in data.get("tools", []):
            params = [
                ToolParamDef(
                    name=p["name"],
                    type=p.get("type", "string"),
                    description=p.get("description", ""),
                    required=p.get("required", True),
                )
                for p in t.get("parameters", [])
            ]
            tools.append(SkillToolDef(
                name=t["name"],
                description=t.get("description", ""),
                parameters=params,
            ))

        return SkillMeta(
            name=data["name"],
            type=data["type"],
            description=data["description"],
            tools=tools,
            max_iterations=data.get("max_iterations", 5),
            llm_config=data.get("llm_config"),
        )

    def _extract_summary(self, body: str) -> str:
        overview_match = re.search(
            r'##\s*功能概述\s*\n+(.+?)(?=\n##|\Z)',
            body, re.DOTALL
        )
        if overview_match:
            return overview_match.group(1).strip()

        scenario_match = re.search(
            r'##\s*适用场景\s*\n+(.+?)(?=\n##|\Z)',
            body, re.DOTALL
        )
        if scenario_match:
            return scenario_match.group(1).strip()

        return body[:200].strip()
