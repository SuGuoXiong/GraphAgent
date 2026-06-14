"""文档解析器抽象基类。

定义所有文档解析器必须实现的统一接口。
"""

import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class BaseParser(ABC):
    """文档解析器基类。

    所有文档类型解析器必须继承此类并实现 parse() 和 supported_extensions() 方法。
    """

    @abstractmethod
    def parse(self, file_path: str) -> tuple[str, dict]:
        """解析文档文件，提取纯文本内容和结构化元数据。

        Args:
            file_path: 文档文件的绝对路径或相对路径。

        Returns:
            (text_content, metadata) 元组：
                - text_content: 提取的纯文本内容，用于后续分块和索引。
                - metadata: 结构化元数据字典，可包含标题层级、代码块信息、
                  表格结构、链接列表、图片引用等。这些元数据会附加到每个
                  Chunk 的 metadata 中，支持检索时的元数据过滤。
        """
        ...

    @abstractmethod
    def supported_extensions(self) -> list[str]:
        """返回此解析器支持的文件扩展名列表。

        扩展名格式：包含前导点的小写字符串，如 ".md"、".py"。
        """
        ...

    def get_parser_name(self) -> str:
        """返回解析器名称，用于日志和注册表识别。"""
        return self.__class__.__name__
