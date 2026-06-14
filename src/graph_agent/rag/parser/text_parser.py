"""纯文本和代码文件解析器。

支持编码检测（UTF-8、GBK 等），根据文件扩展名自动推断编程语言，
对代码文件尝试提取文档字符串、注释和导入语句。
"""

import logging
import re
from pathlib import Path

from graph_agent.rag.parser.base_parser import BaseParser

logger = logging.getLogger(__name__)


class TextParser(BaseParser):
    """纯文本和代码文件解析器。

    功能：
    - 自动检测文件编码（优先 UTF-8，其次 GBK，回退 latin-1）。
    - 根据扩展名推断 code_language 元数据。
    - 对代码文件尝试提取：
      - 模块级文档字符串 / 文件头注释
      - import / include 语句列表
      - 函数 / 类 / 方法定义列表
    - 保留段落结构的纯文本文件。
    """

    # ── 扩展名到语言映射 ───────────────────────────────────────────────

    _EXTENSION_TO_LANGUAGE: dict[str, str] = {
        ".py": "python",
        ".pyx": "python",
        ".pyi": "python",
        ".js": "javascript",
        ".mjs": "javascript",
        ".cjs": "javascript",
        ".jsx": "jsx",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".java": "java",
        ".go": "go",
        ".rs": "rust",
        ".c": "c",
        ".cpp": "cpp",
        ".cc": "cpp",
        ".cxx": "cpp",
        ".h": "c",
        ".hpp": "cpp",
        ".hxx": "cpp",
        ".cs": "csharp",
        ".rb": "ruby",
        ".php": "php",
        ".swift": "swift",
        ".kt": "kotlin",
        ".kts": "kotlin",
        ".sh": "bash",
        ".bash": "bash",
        ".zsh": "bash",
        ".ps1": "powershell",
        ".sql": "sql",
        ".r": "r",
        ".m": "matlab",
        ".toml": "toml",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".json": "json",
        ".xml": "xml",
        ".html": "html",
        ".htm": "html",
        ".css": "css",
        ".scss": "scss",
        ".less": "less",
        ".ini": "ini",
        ".cfg": "ini",
        ".conf": "ini",
        ".env": "dotenv",
        ".csv": "csv",
        ".tsv": "tsv",
        ".log": "log",
        ".txt": "text",
        ".md": "markdown",
        ".markdown": "markdown",
        ".rst": "rst",
        ".tex": "latex",
        ".lua": "lua",
        ".dart": "dart",
        ".scala": "scala",
        ".sc": "scala",
        ".groovy": "groovy",
        ".pl": "perl",
        ".pm": "perl",
        ".erl": "erlang",
        ".ex": "elixir",
        ".exs": "elixir",
        ".clj": "clojure",
        ".hs": "haskell",
        ".jl": "julia",
        ".proto": "protobuf",
        ".graphql": "graphql",
        ".gql": "graphql",
        ".vue": "vue",
        ".svelte": "svelte",
        ".tf": "terraform",
        ".hcl": "hcl",
        ".makefile": "makefile",
        ".mk": "makefile",
        ".cmake": "cmake",
        ".dockerfile": "dockerfile",
    }

    # ── 编码检测优先级 ──────────────────────────────────────────────────

    # 按优先级排列的编码尝试列表
    _ENCODING_CANDIDATES = [
        "utf-8",
        "gbk",
        "gb2312",
        "gb18030",
        "big5",
        "shift_jis",
        "euc-kr",
        "latin-1",  # 最后回退方案，不会失败
    ]

    # ── 代码结构提取的正则模式 ──────────────────────────────────────────

    # Python 文档字符串（模块级）
    _PY_DOCSTRING_PATTERN = re.compile(
        r'^\s*(?:\'\'\'|""")(.*?)(?:\'\'\'|""")',
        re.DOTALL | re.MULTILINE,
    )

    # Python 函数定义
    _PY_FUNC_PATTERN = re.compile(
        r'^\s*(?:async\s+)?def\s+(\w+)\s*\(', re.MULTILINE,
    )
    # Python 类定义
    _PY_CLASS_PATTERN = re.compile(
        r'^\s*class\s+(\w+)', re.MULTILINE,
    )
    # Python import 语句
    _PY_IMPORT_PATTERN = re.compile(
        r'^\s*(?:from\s+\S+\s+)?import\s+\S+', re.MULTILINE,
    )

    # JavaScript/TypeScript 函数和类
    _JS_FUNC_PATTERN = re.compile(
        r'(?:async\s+)?(?:function\s+(\w+)|(\w+)\s*=\s*(?:async\s*)?'
        r'(?:function|\([^)]*\)\s*=>))',
        re.MULTILINE,
    )
    _JS_CLASS_PATTERN = re.compile(
        r'class\s+(\w+)', re.MULTILINE,
    )
    _JS_IMPORT_PATTERN = re.compile(
        r'^\s*(?:import\s+.*?from\s+[\'"][^\'"]+[\'"]|'
        r'const\s+\{.*?\}\s*=\s*require|import\s+[\'"][^\'"]+[\'"])',
        re.MULTILINE,
    )

    # Java 方法/类模式
    _JAVA_METHOD_PATTERN = re.compile(
        r'(?:public|private|protected|static|\s)+[\w<>\[\]]+\s+(\w+)\s*\(',
        re.MULTILINE,
    )
    _JAVA_CLASS_PATTERN = re.compile(
        r'(?:public\s+)?class\s+(\w+)', re.MULTILINE,
    )
    _JAVA_IMPORT_PATTERN = re.compile(
        r'^import\s+[\w.]+', re.MULTILINE,
    )

    # Go 函数/类型
    _GO_FUNC_PATTERN = re.compile(
        r'func\s+(?:\([^)]*\)\s+)?(\w+)\s*\(', re.MULTILINE,
    )
    _GO_TYPE_PATTERN = re.compile(
        r'type\s+(\w+)\s+struct', re.MULTILINE,
    )
    _GO_IMPORT_PATTERN = re.compile(
        r'^\s*"[\w./-]+"', re.MULTILINE,
    )

    # C/C++ 函数模式
    _C_FUNC_PATTERN = re.compile(
        r'^\s*[\w*]+\s+(\w+)\s*\([^)]*\)\s*\{', re.MULTILINE,
    )
    _C_INCLUDE_PATTERN = re.compile(
        r'^\s*#include\s+[<"][\w./]+[>"]', re.MULTILINE,
    )

    # 通用注释模式
    _LINE_COMMENT_PATTERNS: dict[str, str] = {
        "python": r'^\s*#\s*(.+)',
        "javascript": r'^\s*//\s*(.+)',
        "typescript": r'^\s*//\s*(.+)',
        "java": r'^\s*//\s*(.+)',
        "go": r'^\s*//\s*(.+)',
        "rust": r'^\s*//\s*(.+)',
        "c": r'^\s*//\s*(.+)',
        "cpp": r'^\s*//\s*(.+)',
        "csharp": r'^\s*//\s*(.+)',
        "ruby": r'^\s*#\s*(.+)',
        "php": r'^\s*//\s*(.+)',
        "swift": r'^\s*//\s*(.+)',
        "kotlin": r'^\s*//\s*(.+)',
        "bash": r'^\s*#\s*(.+)',
        "powershell": r'^\s*#\s*(.+)',
        "sql": r'^\s*--\s*(.+)',
        "lua": r'^\s*--\s*(.+)',
    }

    _BLOCK_COMMENT_START: dict[str, str] = {
        "python": r'^\s*(?:\'\'\'|""")',
        "javascript": r'/\*',
        "typescript": r'/\*',
        "java": r'/\*',
        "go": r'/\*',
        "rust": r'/\*',
        "c": r'/\*',
        "cpp": r'/\*',
        "csharp": r'/\*',
        "ruby": r'=begin',
        "php": r'/\*',
        "swift": r'/\*',
        "kotlin": r'/\*',
    }

    def supported_extensions(self) -> list[str]:
        """返回支持的文件扩展名列表。"""
        return sorted(self._EXTENSION_TO_LANGUAGE.keys())

    # ── 公开方法 ────────────────────────────────────────────────────────

    def parse(self, file_path: str) -> tuple[str, dict]:
        """解析纯文本或代码文件。

        解析流程：
        1. 检测文件编码并读取内容。
        2. 根据扩展名推断语言标识。
        3. 对代码文件提取结构信息（注释/文档字符串、import、函数/类定义）。
        4. 纯文本文件保留段落结构。
        5. 返回 (text_content, metadata) 元组。

        Args:
            file_path: 文件路径。

        Returns:
            (text_content, metadata) 元组。metadata 包含：
                - filename: 文件名
                - encoding: 检测到的编码
                - code_language: 代码语言标识（代码文件）
                - file_size: 文件大小（字节）
                - line_count: 总行数
                - functions: 函数/方法定义列表（代码文件）
                - classes: 类定义列表（代码文件）
                - imports: 导入/引用语句列表（代码文件）
                - docstring: 模块级文档字符串（代码文件）
                - comments_header: 文件头注释块（代码文件）
                - document_type: "code" 或 "text"
        """
        file_path_obj = Path(file_path)
        extension = file_path_obj.suffix.lower()

        # 步骤 1：编码检测与读取
        raw_bytes = file_path_obj.read_bytes()
        content, encoding = self._detect_encoding_and_decode(raw_bytes)

        # 步骤 2：获取语言标识
        code_language = self._EXTENSION_TO_LANGUAGE.get(extension, "")

        # 步骤 3：构建元数据
        metadata: dict = {
            "filename": file_path_obj.name,
            "encoding": encoding,
            "file_size": len(raw_bytes),
            "line_count": content.count('\n') + (1 if content else 0),
            "document_type": "code" if code_language else "text",
        }

        if code_language:
            metadata["code_language"] = code_language
        else:
            metadata["code_language"] = ""

        # 步骤 4：对代码文件提取结构
        if code_language:
            metadata["functions"] = self._extract_functions(content, code_language)
            metadata["classes"] = self._extract_classes(content, code_language)
            metadata["imports"] = self._extract_imports(content, code_language)
            metadata["docstring"] = self._extract_docstring(content, code_language)
            metadata["comments_header"] = self._extract_comments_header(
                content, code_language
            )
        else:
            metadata["functions"] = []
            metadata["classes"] = []
            metadata["imports"] = []

        return content, metadata

    # ── 编码检测 ────────────────────────────────────────────────────────

    def _detect_encoding_and_decode(self, raw_bytes: bytes) -> tuple[str, str]:
        """自动检测文件编码并解码。

        按优先级依次尝试 UTF-8、GBK、GB2312、GB18030 等编码，
        最终回退到 latin-1（不会失败）。

        Args:
            raw_bytes: 文件的原始字节内容。

        Returns:
            (decoded_text, encoding_name) 元组。
        """
        for encoding in self._ENCODING_CANDIDATES:
            try:
                text = raw_bytes.decode(encoding)
                logger.debug("编码检测成功: %s", encoding)
                return text, encoding
            except UnicodeDecodeError:
                continue

        # 理论上不会到达这里（latin-1 不会失败）
        text = raw_bytes.decode("latin-1")
        logger.warning("所有编码尝试失败，回退到 latin-1")
        return text, "latin-1"

    # ── 代码结构提取 ────────────────────────────────────────────────────

    def _extract_functions(self, content: str, language: str) -> list[str]:
        """提取函数/方法定义列表。

        Args:
            content: 源代码文本。
            language: 代码语言标识。

        Returns:
            函数名列表。
        """
        if language == "python":
            return self._PY_FUNC_PATTERN.findall(content)
        elif language in ("javascript", "typescript", "jsx", "tsx"):
            names = []
            for match in self._JS_FUNC_PATTERN.finditer(content):
                name = match.group(1) or match.group(2)
                if name:
                    names.append(name)
            return names
        elif language == "java":
            return self._JAVA_METHOD_PATTERN.findall(content)
        elif language == "go":
            return self._GO_FUNC_PATTERN.findall(content)
        elif language in ("c", "cpp", "csharp"):
            return self._C_FUNC_PATTERN.findall(content)
        elif language == "rust":
            return re.findall(r'fn\s+(\w+)\s*\(', content)
        elif language == "ruby":
            return re.findall(r'def\s+(\w+)', content)
        elif language == "php":
            return re.findall(r'function\s+(\w+)\s*\(', content)
        elif language == "swift":
            return re.findall(r'func\s+(\w+)\s*\(', content)
        elif language == "kotlin":
            return re.findall(r'fun\s+(\w+)\s*\(', content)
        elif language == "lua":
            return re.findall(r'function\s+(\w+)', content)
        elif language == "r":
            # R 函数定义或赋值形式的函数
            funcs = re.findall(r'(\w+)\s*<-\s*function\s*\(', content)
            return funcs
        else:
            return []

    def _extract_classes(self, content: str, language: str) -> list[str]:
        """提取类/类型定义列表。"""
        if language == "python":
            return self._PY_CLASS_PATTERN.findall(content)
        elif language in ("javascript", "typescript", "jsx", "tsx"):
            return self._JS_CLASS_PATTERN.findall(content)
        elif language == "java":
            return self._JAVA_CLASS_PATTERN.findall(content)
        elif language == "go":
            return self._GO_TYPE_PATTERN.findall(content)
        elif language in ("c", "cpp"):
            # struct/class 定义
            structs = re.findall(r'(?:struct|class)\s+(\w+)', content)
            return structs
        elif language == "ruby":
            return re.findall(r'class\s+(\w+)', content)
        elif language == "php":
            return re.findall(r'class\s+(\w+)', content)
        elif language == "swift":
            classes = re.findall(r'class\s+(\w+)', content)
            structs = re.findall(r'struct\s+(\w+)', content)
            return classes + structs
        elif language == "kotlin":
            classes = re.findall(r'class\s+(\w+)', content)
            data_classes = re.findall(r'data\s+class\s+(\w+)', content)
            return classes + data_classes
        elif language == "rust":
            return re.findall(r'(?:struct|enum|trait)\s+(\w+)', content)
        else:
            return []

    def _extract_imports(self, content: str, language: str) -> list[str]:
        """提取 import / include 语句列表。

        返回去重后的 import 语句原文。
        """
        if language == "python":
            return list(dict.fromkeys(self._PY_IMPORT_PATTERN.findall(content)))
        elif language in ("javascript", "typescript", "jsx", "tsx"):
            return list(dict.fromkeys(self._JS_IMPORT_PATTERN.findall(content)))
        elif language == "java":
            return list(dict.fromkeys(self._JAVA_IMPORT_PATTERN.findall(content)))
        elif language == "go":
            imports = self._GO_IMPORT_PATTERN.findall(content)
            return list(dict.fromkeys(imports))
        elif language in ("c", "cpp"):
            return list(dict.fromkeys(self._C_INCLUDE_PATTERN.findall(content)))
        elif language == "rust":
            return list(dict.fromkeys(re.findall(r'^use\s+\S+;', content, re.MULTILINE)))
        elif language == "ruby":
            return list(dict.fromkeys(re.findall(r'^require\s+[\'"][^\'"]+[\'"]', content, re.MULTILINE)))
        elif language == "swift":
            return list(dict.fromkeys(re.findall(r'^import\s+\w+', content, re.MULTILINE)))
        elif language == "php":
            use_stmts = re.findall(r'^use\s+\S+;', content, re.MULTILINE)
            require_stmts = re.findall(r'(?:require|include)\S*\s+[\'"][^\'"]+[\'"]', content, re.MULTILINE)
            return list(dict.fromkeys(use_stmts + require_stmts))
        elif language == "lua":
            return list(dict.fromkeys(re.findall(r'^require\s*\(?\s*[\'"][^\'"]+[\'"]', content, re.MULTILINE)))
        elif language == "bash":
            source_stmts = re.findall(r'^(?:source|\.)\s+\S+', content, re.MULTILINE)
            return list(dict.fromkeys(source_stmts))
        else:
            return []

    def _extract_docstring(self, content: str, language: str) -> str:
        """提取模块级文档字符串（仅 Python）。

        Returns:
            文档字符串内容，不存在时返回空字符串。
        """
        if language == "python":
            match = self._PY_DOCSTRING_PATTERN.search(content)
            if match:
                return match.group(1).strip()
        return ""

    def _extract_comments_header(
        self, content: str, language: str, max_lines: int = 20
    ) -> str:
        """提取文件头部连续注释块。

        扫描文件开头的连续注释行（包括单行注释和块注释），
        用于获取文件描述、许可证声明等信息。

        Args:
            content: 源代码文本。
            language: 代码语言标识。
            max_lines: 最多提取的行数。

        Returns:
            注释头文本。
        """
        lines = content.split('\n')
        header_comment_lines = []
        in_block_comment = False

        if language == "python":
            # 检查三引号块注释
            for line in lines[:max_lines]:
                stripped = line.strip()
                if in_block_comment:
                    header_comment_lines.append(stripped)
                    if stripped.endswith('"""') or stripped.endswith("'''"):
                        in_block_comment = False
                        break
                    continue
                if stripped.startswith('"""') or stripped.startswith("'''"):
                    in_block_comment = True
                    header_comment_lines.append(stripped)
                    if (stripped.endswith('"""') or stripped.endswith("'''")) and len(stripped) > 3:
                        in_block_comment = False
                        break
                    continue
                if stripped.startswith('#'):
                    header_comment_lines.append(stripped)
                elif header_comment_lines and not stripped:
                    # 注释块后的空行，继续
                    pass
                elif header_comment_lines:
                    break
                elif stripped:
                    break

        elif language in ("javascript", "typescript", "java", "go", "rust",
                          "c", "cpp", "csharp", "swift", "kotlin", "php"):
            for line in lines[:max_lines]:
                stripped = line.strip()
                if in_block_comment:
                    header_comment_lines.append(stripped)
                    if "*/" in stripped:
                        in_block_comment = False
                    continue
                if stripped.startswith('/*'):
                    in_block_comment = True
                    header_comment_lines.append(stripped)
                    if "*/" in stripped and not stripped.startswith('/*'):
                        in_block_comment = False
                    continue
                if stripped.startswith('//'):
                    header_comment_lines.append(stripped)
                elif header_comment_lines and not stripped:
                    pass
                elif header_comment_lines:
                    break
                elif stripped:
                    break

        elif language in ("ruby", "bash", "powershell"):
            for line in lines[:max_lines]:
                stripped = line.strip()
                if in_block_comment:
                    header_comment_lines.append(stripped)
                    if "=end" in stripped:
                        in_block_comment = False
                    continue
                if stripped.startswith('=begin'):
                    in_block_comment = True
                    header_comment_lines.append(stripped)
                    continue
                if stripped.startswith('#'):
                    header_comment_lines.append(stripped)
                elif stripped.startswith('!#') or stripped.startswith('::'):
                    # shebang 或 PowerShell 注释
                    header_comment_lines.append(stripped)
                elif header_comment_lines and not stripped:
                    pass
                elif header_comment_lines:
                    break
                elif stripped:
                    break

        elif language == "sql":
            for line in lines[:max_lines]:
                stripped = line.strip()
                if stripped.startswith('--'):
                    header_comment_lines.append(stripped)
                elif header_comment_lines and not stripped:
                    pass
                elif header_comment_lines:
                    break
                elif stripped:
                    break

        elif language == "lua":
            for line in lines[:max_lines]:
                stripped = line.strip()
                if in_block_comment:
                    header_comment_lines.append(stripped)
                    if "]]" in stripped:
                        in_block_comment = False
                    continue
                if stripped.startswith('--[['):
                    in_block_comment = True
                    header_comment_lines.append(stripped)
                    continue
                if stripped.startswith('--'):
                    header_comment_lines.append(stripped)
                elif header_comment_lines and not stripped:
                    pass
                elif header_comment_lines:
                    break
                elif stripped:
                    break

        return '\n'.join(header_comment_lines) if header_comment_lines else ""
