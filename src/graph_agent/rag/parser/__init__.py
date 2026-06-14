"""文档解析器注册表。

提供 ParserRegistry 类，根据文件扩展名自动选择合适的解析器。
支持 Markdown、纯文本/代码文件的解析。
"""

import logging
from pathlib import Path
from typing import Optional

from graph_agent.rag.parser.base_parser import BaseParser
from graph_agent.rag.rag_types import ParserType

logger = logging.getLogger(__name__)


class ParserRegistry:
    """解析器注册表。

    管理已注册的解析器实例，维护扩展名到解析器的映射关系。
    支持按文件扩展名或 ParserType 枚举自动选择解析器。

    使用方式：
        registry = ParserRegistry()
        registry.register(MarkdownParser())
        registry.register(TextParser())
        # 或使用 auto_register() 一次性注册内置解析器

        parser = registry.get_parser("docs/api.md")
        text, metadata = parser.parse("docs/api.md")
    """

    def __init__(self):
        self._parsers: dict[str, BaseParser] = {}
        # extension -> parser_name 映射
        self._extension_map: dict[str, str] = {}
        # parser_type -> parser_name 映射
        self._type_map: dict[ParserType, str] = {
            ParserType.MARKDOWN: "MarkdownParser",
            ParserType.TEXT: "TextParser",
            ParserType.CODE: "TextParser",  # 代码文件由 TextParser 处理
        }

    # ── 解析器注册 ───────────────────────────────────────────────────────

    def register(self, parser: BaseParser) -> None:
        """注册一个解析器实例。

        将解析器支持的每个扩展名映射到该解析器。
        如果同一扩展名已被注册，后注册的会覆盖前者并记录警告。

        Args:
            parser: 实现了 BaseParser 接口的解析器实例。
        """
        name = parser.get_parser_name()

        if name in self._parsers:
            logger.warning(
                "解析器 '%s' 已被注册，将覆盖现有实例", name,
            )

        self._parsers[name] = parser

        for ext in parser.supported_extensions():
            ext_lower = ext.lower()
            if ext_lower in self._extension_map:
                old_name = self._extension_map[ext_lower]
                logger.debug(
                    "扩展名 '%s' 从解析器 '%s' 重新映射到 '%s'",
                    ext_lower, old_name, name,
                )
            self._extension_map[ext_lower] = name

        logger.info(
            "注册解析器 '%s'，支持 %d 种扩展名: %s",
            name, len(parser.supported_extensions()),
            parser.supported_extensions()[:5],  # 只展示前 5 个
        )

    def auto_register(self) -> None:
        """自动注册所有内置解析器。

        包含：
        - MarkdownParser：处理 .md / .markdown / .mdx 文件
        - TextParser：处理 .txt / .py / .js / .java / .go 等纯文本和代码文件
        """
        from graph_agent.rag.parser.markdown_parser import MarkdownParser
        from graph_agent.rag.parser.text_parser import TextParser

        self.register(MarkdownParser())
        self.register(TextParser())

    # ── 解析器查询 ───────────────────────────────────────────────────────

    def get_parser(
        self,
        file_path: str,
        parser_type: Optional[ParserType] = None,
    ) -> Optional[BaseParser]:
        """根据文件路径和可选的解析器类型获取解析器实例。

        选择逻辑：
        1. 如果指定了 parser_type（且非 AUTO），按类型查找。
        2. 否则按文件扩展名查找。
        3. parser_type 为 AUTO 或 None 时，优先按扩展名匹配。
        4. 扩展名无匹配时，尝试 parser_type 兜底。
        5. 两者都无匹配时返回 None。

        Args:
            file_path: 文件路径字符串。
            parser_type: 强制使用的解析器类型。为 None 或 AUTO 时自动检测。

        Returns:
            匹配的 BaseParser 实例，无匹配时返回 None。

        Raises:
            FileNotFoundError: 当文件路径不存在时。
        """
        path_obj = Path(file_path)
        if not path_obj.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        extension = path_obj.suffix.lower()

        # 情况 1：指定了具体解析器类型
        if parser_type is not None and parser_type != ParserType.AUTO:
            return self._get_parser_by_type(parser_type)

        # 情况 2：AUTO 或无指定，按扩展名匹配
        parser = self._get_parser_by_extension(extension)
        if parser is not None:
            return parser

        # 情况 3：扩展名无匹配，尝试兜底
        # 未知扩展名的文件按 TextParser 处理
        fallback = self._parsers.get("TextParser")
        if fallback:
            logger.debug(
                "扩展名 '%s' 无专用解析器，回退到 TextParser", extension,
            )
        return fallback

    def get_parser_by_extension(self, file_path: str) -> Optional[BaseParser]:
        """仅按文件扩展名查找解析器（便捷方法）。

        Args:
            file_path: 文件路径字符串。

        Returns:
            匹配的 BaseParser 实例，无匹配时返回 None。
        """
        extension = Path(file_path).suffix.lower()
        return self._get_parser_by_extension(extension)

    def get_parser_by_type(self, parser_type: ParserType) -> Optional[BaseParser]:
        """按 ParserType 枚举查找解析器。

        Args:
            parser_type: 解析器类型枚举值。

        Returns:
            匹配的 BaseParser 实例，无匹配时返回 None。
        """
        return self._get_parser_by_type(parser_type)

    # ── 注册表查询 ───────────────────────────────────────────────────────

    def list_parsers(self) -> list[dict]:
        """列出所有已注册的解析器信息。

        Returns:
            解析器信息列表，每项包含 name 和 supported_extensions。
        """
        return [
            {
                "name": name,
                "supported_extensions": parser.supported_extensions(),
            }
            for name, parser in self._parsers.items()
        ]

    def list_extensions(self) -> dict[str, str]:
        """列出所有扩展名到解析器的映射。

        Returns:
            {ext: parser_name} 字典。
        """
        return dict(self._extension_map)

    def is_supported(self, file_path: str) -> bool:
        """检查文件是否被任何已注册的解析器支持。

        Args:
            file_path: 文件路径字符串。

        Returns:
            True 表示至少有一个解析器支持该文件扩展名。
        """
        extension = Path(file_path).suffix.lower()
        return extension in self._extension_map

    # ── 内部方法 ─────────────────────────────────────────────────────────

    def _get_parser_by_extension(self, extension: str) -> Optional[BaseParser]:
        """按文件扩展名查找解析器。"""
        parser_name = self._extension_map.get(extension)
        if parser_name:
            parser = self._parsers.get(parser_name)
            if parser:
                logger.debug(
                    "扩展名 '%s' → 解析器 '%s'", extension, parser_name,
                )
                return parser
        return None

    def _get_parser_by_type(self, parser_type: ParserType) -> Optional[BaseParser]:
        """按 ParserType 枚举查找解析器。"""
        if parser_type == ParserType.AUTO:
            return None

        parser_name = self._type_map.get(parser_type)
        if parser_name:
            parser = self._parsers.get(parser_name)
            if parser:
                logger.debug(
                    "ParserType '%s' → 解析器 '%s'",
                    parser_type.value, parser_name,
                )
                return parser
        return None


# ── 便捷函数 ──────────────────────────────────────────────────────────────


def create_default_registry() -> ParserRegistry:
    """创建并初始化默认解析器注册表。

    自动注册 MarkdownParser 和 TextParser。

    Returns:
        已初始化的 ParserRegistry 实例。
    """
    registry = ParserRegistry()
    registry.auto_register()
    return registry
