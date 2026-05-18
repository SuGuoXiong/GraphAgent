"""Skill 系统——对 SubAgent 能力的抽象描述。

Skill 不再绑定具体工具。工具在 SubAgent 配置文件和系统提示词中定义。
PlanAgent 通过 Skill 名称匹配 SubAgent，无需知道底层工具细节。
"""

from dataclasses import dataclass, field


@dataclass
class Skill:
    """技能定义"""
    name: str
    description: str
    examples: list[str] = field(default_factory=list)
    input_schema: dict = field(default_factory=dict)
    output_schema: dict = field(default_factory=dict)


class SkillRegistry:
    """技能注册中心。

    提供 Skill 的注册、查找和列表功能。
    预置 Skill 在构造时自动注册。
    """

    def __init__(self):
        self._skills: dict[str, Skill] = {}
        self._register_builtin()

    def register(self, skill: Skill) -> None:
        self._skills[skill.name] = skill

    def get(self, name: str) -> Skill:
        return self._skills[name]

    def find_by_name(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def list_all(self) -> list[Skill]:
        return list(self._skills.values())

    def _register_builtin(self) -> None:
        """注册预置 Skill。"""
        builtin = [
            Skill(
                name="file_management",
                description="文件系统读写与管理（读/写/追/删/列目录/存在检查）",
                examples=["读取 data.txt 的内容", "将结果写入 output.txt", "列出 /tmp 目录下的文件"],
            ),
            Skill(
                name="calculation",
                description="数学计算与数据分析",
                examples=["计算表达式 (3+5)*2", "求平均值"],
            ),
            Skill(
                name="time_query",
                description="获取当前时间信息",
                examples=["获取当前 UTC 时间"],
            ),
            Skill(
                name="command_execution",
                description="安全执行只读 shell 命令",
                examples=["列出当前目录文件", "查看文件内容"],
            ),
            Skill(
                name="web_fetch",
                description="网页内容抓取与搜索",
                examples=["获取某 URL 的内容", "搜索关键词"],
            ),
            Skill(
                name="json_processing",
                description="JSON 序列化与反序列化",
                examples=["将字典转为 JSON", "解析 JSON 文本"],
            ),
            Skill(
                name="text_summary",
                description="文本摘要与关键信息提取",
                examples=["总结文章主旨", "提取关键信息"],
            ),
            Skill(
                name="code_analysis",
                description="代码分析与理解",
                examples=["分析代码逻辑", "找出潜在问题"],
            ),
        ]
        for skill in builtin:
            self.register(skill)
