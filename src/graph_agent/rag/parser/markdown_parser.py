"""Markdown 文档解析器。

支持 YAML frontmatter 自动检测与解析，提取标题层级、代码块、
表格、链接和图片等结构化信息。

使用 Python 内置库实现（re + yaml），不依赖第三方 Markdown 解析库。
"""

import logging
import re
from pathlib import Path

import yaml

from graph_agent.rag.parser.base_parser import BaseParser

logger = logging.getLogger(__name__)


class MarkdownParser(BaseParser):
    """Markdown 文档解析器。

    功能：
    - 检测并解析 YAML frontmatter（两个 `---` 之间的内容）作为元数据，
      frontmatter 内容不进入正文。
    - 提取标题层级（H1-H6），构建标题路径。
    - 识别围栏代码块及其语言标识。
    - 识别 Markdown 表格（收集表头和行数据）。
    - 识别链接和图片引用。
    - 支持 .md、.markdown、.mdx 文件。
    """

    # ── 正则模式 ──────────────────────────────────────────────────────

    # YAML frontmatter: 文件以 --- 开头，到第二个 --- 结束
    # 支持 Unix (\\n) 和 Windows (\\r\\n) 换行，末尾可无换行
    _FRONTMATTER_PATTERN = re.compile(
        r'^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)', re.DOTALL
    )

    # 标题行: # 开头，1-6 个 #（ATX 风格）
    _HEADING_PATTERN = re.compile(r'^(#{1,6})\s+(.+?)(?:\s+#+)?$', re.MULTILINE)

    # 围栏代码块: ``` 或 ~~~ 开闭（行首锚定，兼容 Windows 换行）
    _FENCED_CODE_PATTERN = re.compile(
        r'^(?P<fence>```|~~~)(?P<lang>\w*)[ \t]*\r?\n'
        r'(?P<code>.*?)'
        r'\r?\n(?P=fence)[ \t]*\r?$',
        re.DOTALL | re.MULTILINE
    )

    # 缩进代码块（4 空格或 1 tab）：需要按块匹配，这里用简单策略
    # 保留以备后续使用
    _INDENTED_CODE_PATTERN = re.compile(
        r'(?:^|\n)((?: {4,}|\t).*(?:\n(?: {4,}|\t).*)*)', re.MULTILINE
    )

    # Markdown 表格：
    #   表头行 | th1 | th2 |
    #   分隔行 | --- | --- |
    #   数据行 | td1 | td2 |
    _TABLE_PATTERN = re.compile(
        r'^\|(.+)\|\s*\n'      # 表头
        r'^\|[\s\-:.]+\|\s*\n'  # 分隔符
        r'((?:^\|.+\|\s*\n?)*)',  # 数据行
        re.MULTILINE
    )

    # 链接: [text](url "title")
    _LINK_PATTERN = re.compile(
        r'\[([^\]]*)\]\(([^)\s]+)(?:\s+"([^"]*)")?\)'
    )

    # 图片: ![alt](src "title")
    _IMAGE_PATTERN = re.compile(
        r'!\[([^\]]*)\]\(([^)\s]+)(?:\s+"([^"]*)")?\)'
    )

    # HTML 注释（需要在生成纯文本时移除）
    _HTML_COMMENT_PATTERN = re.compile(r'<!--.*?-->', re.DOTALL)

    # Setext 风格标题（下划线型）：H1 用 ===，H2 用 ---
    _SETEXT_H1_PATTERN = re.compile(r'^(.+)\n={3,}\s*$', re.MULTILINE)
    _SETEXT_H2_PATTERN = re.compile(r'^(.+)\n-{3,}\s*$', re.MULTILINE)

    def supported_extensions(self) -> list[str]:
        """返回支持的 Markdown 文件扩展名。"""
        return [".md", ".markdown", ".mdx"]

    # ── 公开方法 ──────────────────────────────────────────────────────

    def parse(self, file_path: str) -> tuple[str, dict]:
        """解析 Markdown 文件。

        解析流程：
        1. 读取文件内容。
        2. 检测并提取 YAML frontmatter 作为元数据。
        3. 解析正文，提取标题、代码块、表格、链接、图片等结构信息。
        4. 生成纯文本内容（移除 frontmatter，保留结构标记）。
        5. 返回 (text_content, metadata) 元组。

        Args:
            file_path: Markdown 文件路径。

        Returns:
            (text_content, metadata) 元组。metadata 包含：
                - filename: 文件名
                - frontmatter: 解析后的 YAML 元数据（如有）
                - headings: 标题列表 [{level, text, line}]
                - heading_path: 标题路径列表（如 ["# 简介", "## 安装"]）
                - code_blocks: 代码块列表 [{language, code, line_start}]
                - tables: 表格列表 [{headers, rows}]
                - links: 链接列表 [{text, url, title}]
                - images: 图片列表 [{alt, src, title}]
        """
        file_path_obj = Path(file_path)
        content = self._read_file(file_path_obj)

        # 步骤 1：提取 YAML frontmatter
        frontmatter_data: dict = {}
        body_start = 0
        fm_match = self._FRONTMATTER_PATTERN.match(content)
        if fm_match:
            frontmatter_raw = fm_match.group(1)
            body_start = fm_match.end()
            try:
                parsed = yaml.safe_load(frontmatter_raw)
                if isinstance(parsed, dict):
                    frontmatter_data = parsed
                else:
                    logger.debug(
                        "YAML frontmatter 解析结果不是 dict 类型，忽略: %s",
                        file_path_obj.name,
                    )
            except yaml.YAMLError as e:
                logger.warning(
                    "YAML frontmatter 解析失败 (%s): %s",
                    file_path_obj.name, e,
                )

        # 正文内容（去除 frontmatter 后的部分）
        body = content[body_start:]

        # 步骤 2：提取结构信息
        headings = self._extract_headings(body)
        code_blocks = self._extract_code_blocks(body)
        tables = self._extract_tables(body)
        links = self._extract_links(body)
        images = self._extract_images(body)

        # 步骤 3：生成纯文本
        text_content = self._to_plain_text(body, headings, code_blocks, tables)

        # 步骤 4：组装元数据
        heading_path = [
            f"{'#' * h['level']} {h['text']}" for h in headings
        ]
        metadata = {
            "filename": file_path_obj.name,
            "frontmatter": frontmatter_data,
            "headings": headings,
            "heading_path": heading_path,
            "code_blocks": code_blocks,
            "tables": tables,
            "links": links,
            "images": images,
        }

        return text_content, metadata

    # ── 内部方法：文件读取 ──────────────────────────────────────────────

    @staticmethod
    def _read_file(file_path: Path) -> str:
        """读取文件内容，自动尝试 UTF-8 编码。"""
        try:
            return file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # 尝试 GBK
            try:
                return file_path.read_text(encoding="gbk")
            except UnicodeDecodeError:
                # 最后尝试 latin-1（不会失败）
                logger.warning(
                    "无法以 UTF-8/GBK 解码 %s，回退到 latin-1",
                    file_path.name,
                )
                return file_path.read_text(encoding="latin-1")

    # ── 内部方法：结构提取 ──────────────────────────────────────────────

    def _extract_headings(self, text: str) -> list[dict]:
        """提取 ATX 风格标题（# H1 到 ###### H6）。

        同时跳过代码块内的 # 符号（位于行首的 # 在代码块内也应保留），
        但为了保持简单，先提取代码块位置再过滤。

        Returns:
            标题列表，每个元素为 {"level": int, "text": str, "line": int}。
        """
        headings = []
        code_ranges = self._get_code_block_ranges(text)

        for match in self._HEADING_PATTERN.finditer(text):
            pos = match.start()
            # 检查是否在代码块内
            if self._is_in_code_block(pos, code_ranges):
                continue

            hashes = match.group(1)
            title = match.group(2).strip()
            # 计算行号
            line_num = text[:pos].count('\n') + 1
            headings.append({
                "level": len(hashes),
                "text": title,
                "line": line_num,
            })

        return headings

    def _extract_code_blocks(self, text: str) -> list[dict]:
        """提取围栏代码块（``` 或 ~~~）及其语言标识。

        Returns:
            代码块列表，每个元素为：
                {"language": str, "code": str, "line_start": int}。
        """
        code_blocks = []

        # 先找围栏代码块
        # 手动实现以获取准确位置
        lines = text.split('\n')
        i = 0
        while i < len(lines):
            line = lines[i]
            # 检测围栏开始
            fence_match = re.match(r'^(```|~~~)(\w*)', line)
            if fence_match:
                fence_char = fence_match.group(1)
                lang = fence_match.group(2) or ""
                line_start = i + 1  # 1-based

                # 找围栏结束
                code_lines = []
                i += 1
                while i < len(lines):
                    if lines[i].rstrip() == fence_char:
                        break
                    code_lines.append(lines[i])
                    i += 1
                else:
                    # 未找到闭合围栏，回退
                    i = line_start
                    break

                code_blocks.append({
                    "language": lang,
                    "code": '\n'.join(code_lines),
                    "line_start": line_start,
                })

            i += 1

        return code_blocks

    def _extract_tables(self, text: str) -> list[dict]:
        """提取 Markdown 表格。

        Returns:
            表格列表，每个元素为：
                {"headers": [str], "rows": [[str]], "line_start": int}。
        """
        tables = []
        code_ranges = self._get_code_block_ranges(text)

        for match in self._TABLE_PATTERN.finditer(text):
            pos = match.start()
            if self._is_in_code_block(pos, code_ranges):
                continue

            header_line = match.group(1)
            data_block = match.group(2)

            headers = [cell.strip() for cell in header_line.split('|')]
            # 去除首尾空元素（表格两端的 | 会产生空字符串）
            headers = [h for h in headers if h]
            # 如果全部为空，保留原始
            if not headers:
                headers = [cell.strip() for cell in header_line.split('|')]

            rows = []
            for row_line in data_block.strip().split('\n'):
                if not row_line.strip():
                    continue
                cells = [cell.strip() for cell in row_line.split('|')]
                cells = [c for c in cells if c] or cells
                rows.append(cells)

            line_start = text[:pos].count('\n') + 1
            tables.append({
                "headers": headers,
                "rows": rows,
                "line_start": line_start,
            })

        return tables

    def _extract_links(self, text: str) -> list[dict]:
        """提取 Markdown 链接 [text](url "title")。

        排除图片语法（以 ! 开头的）。

        Returns:
            链接列表，每个元素为 {"text": str, "url": str, "title": str}。
        """
        links = []
        code_ranges = self._get_code_block_ranges(text)

        for match in self._LINK_PATTERN.finditer(text):
            pos = match.start()
            if self._is_in_code_block(pos, code_ranges):
                continue
            # 排除图片语法：检查前一个字符是否为 !
            if pos > 0 and text[pos - 1] == '!':
                continue
            links.append({
                "text": match.group(1).strip(),
                "url": match.group(2).strip(),
                "title": (match.group(3) or "").strip(),
            })

        return links

    def _extract_images(self, text: str) -> list[dict]:
        """提取 Markdown 图片 ![alt](src "title")。

        Returns:
            图片列表，每个元素为 {"alt": str, "src": str, "title": str}。
        """
        images = []
        code_ranges = self._get_code_block_ranges(text)

        for match in self._IMAGE_PATTERN.finditer(text):
            pos = match.start()
            if self._is_in_code_block(pos, code_ranges):
                continue
            images.append({
                "alt": match.group(1).strip(),
                "src": match.group(2).strip(),
                "title": (match.group(3) or "").strip(),
            })

        return images

    # ── 内部方法：纯文本生成 ────────────────────────────────────────────

    def _to_plain_text(
        self,
        body: str,
        headings: list[dict],
        code_blocks: list[dict],
        tables: list[dict],
    ) -> str:
        """将 Markdown 正文转换为适合分块的纯文本。

        策略：
        - 移除 HTML 注释。
        - 将代码块替换为 [代码块: language] 标记。
        - 将表格替换为 [表格] 标记。
        - 保留标题和段落结构。
        - 移除围栏标记（```）和表格分隔行（| --- |）。
        - 保留链接和图片的文字描述部分。

        Args:
            body: 去除 frontmatter 后的 Markdown 正文。
            headings: 提取的标题列表。
            code_blocks: 提取的代码块列表。
            tables: 提取的表格列表。

        Returns:
            清理后的纯文本内容。
        """
        text = body

        # 1. 移除 HTML 注释
        text = self._HTML_COMMENT_PATTERN.sub('', text)

        # 2. 替换围栏代码块为标记（保留语言和换行结构）
        text = self._FENCED_CODE_PATTERN.sub(self._replace_code_block, text)

        # 3. 简化表格（保留内容但移除管道符号和分隔行）
        text = self._simplify_tables(text)

        # 4. 移除多余空行（保留段落间距）
        text = re.sub(r'\n{4,}', '\n\n\n', text)

        return text.strip()

    @staticmethod
    def _replace_code_block(match: re.Match) -> str:
        """替换代码块为带语言标记的格式。"""
        lang = match.group("lang") or ""
        code = match.group("code")
        lang_label = f"[代码块: {lang}]" if lang else "[代码块]"
        return f"\n{lang_label}\n{code}\n[/代码块]\n"

    @staticmethod
    def _simplify_tables(text: str) -> str:
        """简化表格：移除分隔行，保留表头和数据行内容。"""
        lines = text.split('\n')
        result = []
        in_table = False

        for line in lines:
            stripped = line.strip()
            # 检测表格行（以 | 开头）
            if stripped.startswith('|') and stripped.endswith('|'):
                # 检查是否为分隔行（只含 | - : 空格）
                if re.match(r'^\|[\s\-:.|]+\|$', stripped):
                    in_table = True
                    continue
                if in_table or re.match(r'^\|[\s\-:.|]+\|$', line.strip()) is None:
                    # 移除管道符号，用空格连接单元格
                    cells = [c.strip() for c in stripped.split('|') if c.strip()]
                    if cells:
                        result.append(' | '.join(cells))
                in_table = True
            else:
                in_table = False
                result.append(line)

        return '\n'.join(result)

    # ── 内部方法：辅助 ──────────────────────────────────────────────────

    def _get_code_block_ranges(self, text: str) -> list[tuple[int, int]]:
        """获取所有围栏代码块的起止位置，用于排除代码块内的语法元素。

        Returns:
            (start, end) 位置元组列表。
        """
        ranges = []
        for match in self._FENCED_CODE_PATTERN.finditer(text):
            ranges.append((match.start(), match.end()))
        return ranges

    @staticmethod
    def _is_in_code_block(pos: int, code_ranges: list[tuple[int, int]]) -> bool:
        """检查给定位置是否落在代码块范围内。"""
        for start, end in code_ranges:
            if start <= pos < end:
                return True
        return False
