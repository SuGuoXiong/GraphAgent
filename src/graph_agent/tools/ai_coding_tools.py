"""AI Coding 工具集 —— 代码搜索、读取、修改、写入和命令执行。

提供 AI Coding Skill 所需的全部工具：
- glob_file: 按 glob 模式查找文件
- grep_context: 项目全局关键词/正则检索代码
- read_code: 按范围/上下文读取代码（含智能边界扩展）
- update_code: 精准修改已有文件中的代码
- write_code: 创建新文件或追加写入代码
- execute_command: 执行命令检查环境或运行测试验证
"""

import os
import re
import subprocess
import sys
from fnmatch import fnmatch
from pathlib import Path

from graph_agent.tools.base import tool

# =============================================================================
# 路径安全检查
# =============================================================================

# Windows 上无意义的 Linux 虚拟/系统路径前缀
_LINUX_ONLY_PREFIXES = ("/proc/", "/sys/", "/dev/")

# 需要排除的无关目录
_EXCLUDED_DIRS = {
    ".git", "__pycache__", ".pytest_cache", "node_modules", ".venv",
    "venv", ".tox", ".mypy_cache", ".ruff_cache", "dist", "build",
    ".idea", ".vscode",
}


def _resolve_path(path: str) -> Path:
    """解析并规范化路径，拒绝路径遍历攻击和跨平台无效路径。"""
    if sys.platform == "win32" and path.startswith(_LINUX_ONLY_PREFIXES):
        raise ValueError(
            f"路径 '{path}' 是 Linux 系统路径，当前运行环境为 Windows"
        )

    # 1. 先在原始路径上检查 ..（Path.resolve() 会将其消除，须在此之前检测）
    raw_path = Path(path)
    if ".." in raw_path.parts:
        raise ValueError("路径包含非法的上级目录引用")

    # 2. 解析为绝对路径
    resolved = raw_path.resolve()

    # 3. 校验解析后的路径仍在项目工作目录范围内
    project_root = Path(os.getenv("PROJECT_ROOT", Path.cwd())).resolve()
    if not str(resolved).startswith(str(project_root)):
        raise ValueError(f"路径 '{path}' 越过了项目根目录范围")

    return resolved


def _is_text_file(file_path: Path) -> bool:
    """判断文件是否为可读的文本文件（非二进制）。"""
    text_extensions = {
        ".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java",
        ".kt", ".c", ".cpp", ".h", ".hpp", ".cs", ".rb", ".php",
        ".swift", ".scala", ".sh", ".bash", ".zsh", ".bat", ".ps1",
        ".md", ".txt", ".rst", ".json", ".yaml", ".yml", ".toml",
        ".xml", ".html", ".css", ".scss", ".less", ".sql",
        ".cfg", ".ini", ".conf", ".env", ".gitignore", ".dockerfile",
        ".pyi", ".pyx", ".proto",
    }
    if file_path.suffix.lower() in text_extensions:
        return True
    # 无后缀的小文件也当作文本（如 Makefile、Dockerfile）
    if not file_path.suffix and file_path.stat().st_size < 1024 * 1024:
        return True
    return False


# =============================================================================
# 1. glob_file —— 文件查找
# =============================================================================

@tool(
    "glob_file",
    "按 glob 模式查找文件并返回匹配的文件路径列表。"
    "参数 pattern: glob 匹配模式（如 '**/*.py'），必选；"
    "path: 搜索起始目录，可选，默认使用当前目录",
    risk_level="low",
)
def glob_file(pattern: str, path: str = ".") -> str:
    """按 glob 模式查找文件。

    Args:
        pattern: glob 匹配模式，如 **/*.py、src/**/test_*.py
        path: 搜索起始目录，默认当前目录

    Returns:
        匹配的文件路径列表
    """
    root = _resolve_path(path)
    if not root.exists():
        return f"错误: 目录不存在 - {path}"
    if not root.is_dir():
        return f"错误: 路径不是目录 - {path}"

    results = []
    try:
        for file_path in root.rglob("*"):
            if not file_path.is_file():
                continue

            # 排除无关目录
            if any(d in _EXCLUDED_DIRS for d in file_path.parts):
                continue

            try:
                rel = file_path.relative_to(root)
            except (OSError, ValueError):
                continue

            # 使用 fnmatch 匹配 pattern
            if fnmatch(str(rel), pattern) or fnmatch(file_path.name, pattern):
                results.append(str(rel))
    except (PermissionError, OSError) as e:
        return f"错误: 搜索失败 - {e}"

    if not results:
        return f"未找到匹配 '{pattern}' 的文件"

    results.sort()
    total = len(results)
    if total > 200:
        results = results[:200]
        results.append(f"(共 {total} 个结果，仅显示前 200 个)")

    header = f"找到 {total} 个匹配 \"{pattern}\" 的文件：\n\n"
    return header + "\n".join(results)


# =============================================================================
# 2. grep_context —— 代码内容检索
# =============================================================================

# 启发式匹配类型标注规则
_MATCH_TYPE_RULES = [
    (re.compile(r'^\s*(async\s+)?def\s+\w+'), "[函数定义]"),
    (re.compile(r'^\s*class\s+\w+'), "[类定义]"),
    (re.compile(r'^\s*(from\s+\S+\s+)?import\s+'), "[导入语句]"),
    (re.compile(r'^\s*@\w+'), "[装饰器]"),
    (re.compile(r'^\s*\w+\s*=\s*\w+'), "[变量赋值]"),
]

_MAX_GREP_FILE_SIZE = 1024 * 1024  # 1MB


def _guess_match_type(line: str) -> str:
    """启发式标注匹配行的代码类型（非语法解析，可能误报）。"""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return ""
    for regex, label in _MATCH_TYPE_RULES:
        if regex.search(line):
            return f" {label}"
    # 如果看起来像函数调用（包含括号，行首有小写字母）
    if re.match(r'^\s*\w+\.?\w*\s*\(', line) and not line.strip().startswith(
        ("def ", "class ", "if ", "for ", "while ", "with ", "try ", "except")
    ):
        return " [函数调用]"
    return ""


@tool(
    "grep_context",
    "在项目中全局搜索代码内容，支持关键词和正则表达式检索。"
    "返回匹配内容的文件路径、行号和代码片段。"
    "参数 pattern: 搜索模式（关键词或正则），必选；"
    "path: 搜索目录，可选；"
    "file_glob: 文件名过滤模式（如 '*.py'），可选；"
    "output_mode: 输出模式（content / files_with_matches / count），可选，默认 content；"
    "max_results: 最大返回结果数，可选，默认 50",
    risk_level="low",
)
def grep_context(
    pattern: str,
    path: str = ".",
    file_glob: str = "",
    output_mode: str = "content",
    max_results: int = 50,
) -> str:
    """在项目中搜索代码内容。

    Args:
        pattern: 搜索模式（支持正则表达式）
        path: 搜索目录
        file_glob: 文件名过滤模式（内部使用 file_glob 避免与 Python glob 模块冲突）
        output_mode: content / files_with_matches / count
        max_results: 最大返回条数（默认 50，最大 200）

    Returns:
        搜索结果
    """
    try:
        regex = re.compile(pattern)
    except re.error as e:
        return f"错误: 无效的正则表达式 - {e}"

    max_results = min(max_results, 200)
    root = _resolve_path(path)
    if not root.exists():
        return f"错误: 路径不存在 - {path}"

    total_matches = 0
    output_lines = []

    try:
        for file_path in root.rglob("*"):
            if not file_path.is_file():
                continue
            if any(d in _EXCLUDED_DIRS for d in file_path.parts):
                continue
            if not _is_text_file(file_path):
                continue
            if file_path.stat().st_size > _MAX_GREP_FILE_SIZE:
                continue

            try:
                content = file_path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, PermissionError, OSError):
                continue

            # 文件名过滤
            if file_glob and not fnmatch(file_path.name, file_glob):
                continue

            file_matches = list(regex.finditer(content))
            if not file_matches:
                continue

            total_matches += len(file_matches)
            try:
                rel_path = file_path.relative_to(root)
            except (OSError, ValueError):
                rel_path = file_path

            if output_mode == "files_with_matches":
                if str(rel_path) not in [l for l in output_lines]:
                    output_lines.append(str(rel_path))
                if len(output_lines) >= max_results:
                    break
                continue

            if output_mode == "count":
                output_lines.append(f"{rel_path}: {len(file_matches)} 条匹配")
                if len(output_lines) >= max_results:
                    break
                continue

            # content mode
            lines = content.split("\n")
            for m in file_matches:
                if total_matches > max_results:
                    break
                line_no = content[:m.start()].count("\n") + 1
                line_text = lines[line_no - 1].strip()
                type_label = _guess_match_type(line_text)
                output_lines.append(
                    f"{rel_path}:{line_no}: {line_text}{type_label}"
                )
                if len(output_lines) >= max_results:
                    break

    except (PermissionError, OSError) as e:
        return f"错误: 搜索失败 - {e}"

    if not output_lines:
        return f"未找到匹配 '{pattern}' 的内容"

    if total_matches > max_results:
        output_lines.append(
            f"(结果已截断，共 {total_matches} 条匹配，仅显示前 {max_results} 条)"
        )

    return "\n".join(output_lines)


# =============================================================================
# 3. read_code —— 代码读取（含智能边界扩展）
# =============================================================================

# Python 缩进检测正则（函数/类定义）
_PY_DEF_RE = re.compile(r'^(\s*)(async\s+)?(def|class)\s+')

# C 风格语言（JS/TS/Go/Rust/Java/C/C++）函数/方法/类检测
_CStyle_BLOCK_START_RE = re.compile(
    r'^(\s*)((async\s+)?function|class|interface|struct|enum|impl|fn|func|'
    r'public|private|protected|static|virtual|override|export)'
)

_CStyle_BRACE_RE = re.compile(r'[{}]')


def _detect_language(file_path: str) -> str:
    """根据文件扩展名检测编程语言类型。"""
    ext = Path(file_path).suffix.lower()
    lang_map = {
        ".py": "python",
        ".pyi": "python",
        ".js": "javascript",
        ".mjs": "javascript",
        ".cjs": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".jsx": "javascript",
        ".go": "go",
        ".rs": "rust",
        ".java": "java",
        ".kt": "kotlin",
        ".c": "c",
        ".cpp": "cpp",
        ".h": "c",
        ".hpp": "cpp",
        ".cs": "csharp",
    }
    return lang_map.get(ext, "unknown")


def _get_indent_level(line: str) -> int:
    """获取行的缩进级别（空格数）。"""
    return len(line) - len(line.lstrip(" "))


def _expand_boundaries_python(
    lines: list[str], start_idx: int, end_idx: int
) -> tuple[int, int]:
    """Python 缩进感知的边界扩展。"""
    total = len(lines)

    # 向上扩展：找到函数/类定义头部
    while start_idx > 0:
        line = lines[start_idx - 1]
        if not line.strip():
            start_idx -= 1
            continue
        stripped = line.strip()
        indent = _get_indent_level(line)

        # 遇到装饰器 → 继续向上
        if stripped.startswith("@"):
            start_idx -= 1
            continue

        # 遇到 def / class 定义（缩进为 0 或更外层）
        m = _PY_DEF_RE.match(line)
        if m and indent == 0:
            start_idx -= 1
            break

        # 如果当前位置已经是函数/类定义行，停止
        if _PY_DEF_RE.match(line) and start_idx > 0:
            break

        # 遇到顶级缩进的代码（模块级变量/import）→ 停止
        if indent == 0 and not stripped.startswith("@"):
            break

        start_idx -= 1

    # 向下扩展：找到代码块结束
    # 确定函数/类的顶级缩进
    top_indent = None
    for i in range(start_idx, min(start_idx + 5, total)):
        stripped = lines[i].strip()
        if stripped and not stripped.startswith("@") and not stripped.startswith("#"):
            top_indent = _get_indent_level(lines[i])
            if _PY_DEF_RE.match(lines[i]):
                break
            if top_indent == 0:
                break

    if top_indent is None:
        top_indent = 0

    while end_idx < total:
        line = lines[end_idx - 1] if end_idx > 0 else ""
        stripped = line.strip()

        # 空行继续
        if not stripped:
            end_idx += 1
            if end_idx > total:
                break
            continue

        indent = _get_indent_level(line)

        # 缩进回退到顶级且非空行 → 代码块结束
        if indent <= top_indent and stripped:
            # 检查是否是下一个 def/class
            if _PY_DEF_RE.match(line):
                break
            # 检查是否是注释分隔符
            if stripped.startswith("#") and end_idx > start_idx + 2:
                break
            break

        end_idx += 1
        if end_idx > total:
            break

    return start_idx, min(end_idx, total)


def _expand_boundaries_cstyle(
    lines: list[str], start_idx: int, end_idx: int
) -> tuple[int, int]:
    """C 风格语言的边界扩展（基于大括号配对）。"""
    total = len(lines)

    # 向上扩展：找到函数/类的开始声明
    while start_idx > 0:
        line = lines[start_idx - 1]
        stripped = line.strip()
        if not stripped:
            start_idx -= 1
            continue
        # 找到包含 { 的行或声明行
        if "{" in stripped or _CStyle_BLOCK_START_RE.match(line):
            start_idx -= 1
            if _CStyle_BLOCK_START_RE.match(line) and _get_indent_level(line) == 0:
                break
            if "{" in stripped:
                break
        start_idx -= 1
        # 安全上限
        if start_idx <= 0:
            break

    # 向下扩展：通过大括号配对找到代码块结束
    brace_count = 0
    started = False
    for i in range(start_idx, total):
        line = lines[i]
        open_count = line.count("{")
        close_count = line.count("}")
        brace_count += open_count - close_count
        if open_count > 0:
            started = True
        if started and brace_count == 0:
            end_idx = i + 1
            break
        end_idx = i + 1

    return start_idx, min(end_idx, total)


def _expand_boundaries(
    file_path: str, lines: list[str], start_idx: int, end_idx: int
) -> tuple[int, int]:
    """根据语言类型进行智能边界扩展。"""
    lang = _detect_language(file_path)

    if lang == "python":
        return _expand_boundaries_python(lines, start_idx, end_idx)
    elif lang in ("javascript", "typescript", "go", "rust", "java", "kotlin", "c", "cpp", "csharp"):
        return _expand_boundaries_cstyle(lines, start_idx, end_idx)
    else:
        # 未知语言：简单的空白行边界扩展
        while start_idx > 0 and lines[start_idx - 1].strip():
            start_idx -= 1
        while end_idx < len(lines) and lines[end_idx - 1].strip() if end_idx < len(lines) else False:
            end_idx += 1
        return start_idx, end_idx


@tool(
    "read_code",
    "从指定文件读取代码片段。支持两种模式："
    "1) 范围模式（context_line=0）：读取 start_line 至 end_line 的内容；"
    "2) 上下文模式（context_line>0）：以 context_line 为中心读取前后代码。"
    "会自动扩展范围至完整函数/类边界。"
    "参数 path: 文件路径，必选；"
    "start_line: 起始行号，可选；"
    "end_line: 终止行号，可选；"
    "context_line: 关键字所在行号，可选，默认 0",
    risk_level="low",
)
def read_code(
    path: str,
    start_line: int = 0,
    end_line: int = 0,
    context_line: int = 0,
) -> str:
    """读取代码片段，自动扩展至完整函数/类边界。

    Args:
        path: 文件路径
        start_line: 起始行号（1-based），context_line=0 时使用
        end_line: 终止行号（1-based），context_line=0 时使用
        context_line: 关键字所在行号（1-based），>0 时使用上下文模式

    Returns:
        带行号的代码片段
    """
    file_path = _resolve_path(path)
    if not file_path.exists():
        return f"错误: 文件不存在 - {path}"
    if not file_path.is_file():
        return f"错误: 路径不是文件 - {path}"

    try:
        content = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"错误: 无法以UTF-8编码读取文件 - {path}"
    except Exception as e:
        return f"错误: 读取文件失败 - {e}"

    lines = content.split("\n")
    total_lines = len(lines)
    if total_lines == 0:
        return f"文件为空: {path}"

    # 确定初始读取范围
    if context_line > 0:
        context_line = min(context_line, total_lines)
        start_idx = max(0, context_line - 1 - 75)
        end_idx = min(total_lines, context_line - 1 + 76)
    else:
        if start_line <= 0:
            start_line = 1
        if end_line <= 0 or end_line > total_lines:
            end_line = total_lines
        start_idx = start_line - 1
        end_idx = end_line

    # 智能边界扩展
    start_idx, end_idx = _expand_boundaries(path, lines, start_idx, end_idx)

    # 上限检查
    line_count = end_idx - start_idx
    MAX_LINES = 600
    if line_count > MAX_LINES:
        # 超出上限时优先保留向上扩展的结果（确保函数签名完整），截断尾部
        end_idx = start_idx + MAX_LINES
        truncated = True
    else:
        truncated = False

    # 字符数上限检查
    MAX_CHARS = 50000
    selected_lines = lines[start_idx:end_idx]
    selected_text = "\n".join(selected_lines)
    if len(selected_text) > MAX_CHARS:
        # 截断到字符上限
        char_count = 0
        new_end = start_idx
        for i, line in enumerate(selected_lines):
            char_count += len(line) + 1  # +1 for newline
            if char_count > MAX_CHARS:
                new_end = start_idx + i
                break
        else:
            new_end = end_idx
        end_idx = new_end
        truncated = True

    # 构建输出
    output_lines = []
    header = (
        f"=== read_code: {path}, "
        f"行 {start_idx + 1}-{end_idx} (共 {end_idx - start_idx} 行) ===\n"
    )
    output_lines.append(header)

    for i in range(start_idx, end_idx):
        output_lines.append(f"{i + 1}| {lines[i]}")

    if truncated or end_idx < total_lines:
        suffix = f"\n=== 文件共 {total_lines} 行，以上为 {start_idx + 1}-{end_idx} 行 ==="
        if truncated:
            suffix += " (内容已截断)"
        output_lines.append(suffix)
    elif line_count < total_lines:
        output_lines.append(
            f"\n=== 文件共 {total_lines} 行，以上为 {start_idx + 1}-{end_idx} 行 ==="
        )

    return "\n".join(output_lines)


# =============================================================================
# 4. update_code —— 代码修改
# =============================================================================

@tool(
    "update_code",
    "精准修改已有代码文件。通过 old_string 唯一匹配实现精准替换。"
    "参数 path: 文件路径，必选；"
    "old_string: 待替换的原代码字符串（需唯一匹配），必选；"
    "new_string: 替换后的新代码字符串，必选",
    risk_level="medium",
)
def update_code(path: str, old_string: str, new_string: str) -> str:
    """精准修改已有文件中的代码。

    Args:
        path: 文件路径
        old_string: 待替换的原代码（须与文件内容完全一致）
        new_string: 替换后的新代码

    Returns:
        修改结果（含变更统计和上下文）
    """
    if not old_string:
        return "错误: old_string 不能为空"

    file_path = _resolve_path(path)
    if not file_path.exists():
        return f"错误: 文件不存在 - {path}"
    if not file_path.is_file():
        return f"错误: 路径不是文件 - {path}"

    try:
        original = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"错误: 无法以UTF-8编码读取文件 - {path}"
    except Exception as e:
        return f"错误: 读取文件失败 - {e}"

    # 唯一匹配校验
    count = original.count(old_string)
    if count == 0:
        return (
            f"错误: 在文件中未找到要替换的内容。"
            f"请确保 old_string 与文件中的文本完全一致（包括缩进和换行）"
        )
    if count > 1:
        return (
            f"错误: old_string 在文件中出现了 {count} 次，不是唯一匹配。"
            f"请增加上下文使 old_string 更长、更具唯一性，确保只匹配想要修改的那一处。"
        )

    # 写入量限制
    new_lines = new_string.count("\n") + 1
    if new_lines > 200:
        return (
            f"错误: new_string 有 {new_lines} 行，超过单次写入上限 200 行。"
            f"请将修改拆分为多次调用。"
        )

    # 执行替换
    old_lines_count = old_string.count("\n") + 1
    new_content = original.replace(old_string, new_string, 1)

    try:
        file_path.write_text(new_content, encoding="utf-8")
    except Exception as e:
        return f"错误: 写入文件失败 - {e}"

    # 计算变更统计
    added = max(0, new_lines - old_lines_count)
    deleted = max(0, old_lines_count - new_lines)
    unchanged = min(old_lines_count, new_lines)

    # 查找修改位置
    match_pos = original.find(old_string)
    line_start = original[:match_pos].count("\n") + 1
    line_end = line_start + old_lines_count - 1

    # 提取修改后上下文（前后各 3 行）
    final_lines = new_content.split("\n")
    ctx_start = max(0, line_start - 1 - 3)
    ctx_end = min(len(final_lines), line_start - 1 + new_lines + 3)

    ctx_text = "\n".join(
        f"{i + 1}| {final_lines[i]}"
        for i in range(ctx_start, ctx_end)
    )

    return (
        f"[OK] 已修改：{path}（行 {line_start}-{line_end}）\n\n"
        f"变更统计：涉及 {old_lines_count} 行"
        f"（删除 {deleted} 行，新增 {added} 行，不变 {unchanged} 行）\n\n"
        f"修改后上下文：\n{ctx_text}"
    )


# =============================================================================
# 5. write_code —— 代码写入
# =============================================================================

@tool(
    "write_code",
    "创建新文件或写入代码内容。"
    "参数 path: 文件路径，必选；"
    "content: 要写入的代码内容，必选；"
    "mode: 写入模式（'overwrite' 覆盖 / 'append' 追加），可选，默认 'overwrite'",
    risk_level="medium",
)
def write_code(path: str, content: str, mode: str = "overwrite") -> str:
    """创建新文件或写入代码内容。

    Args:
        path: 文件路径
        content: 要写入的代码内容
        mode: overwrite（覆盖写入）/ append（追加写入）

    Returns:
        写入结果
    """
    if mode not in ("overwrite", "append"):
        return f"错误: 无效的写入模式 '{mode}'，请使用 'overwrite' 或 'append'"

    # 写入量限制
    content_lines = content.count("\n") + 1
    if content_lines > 200:
        return (
            f"错误: content 有 {content_lines} 行，超过单次写入上限 200 行。"
            f"请将内容拆分为多次写入。"
        )

    file_path = _resolve_path(path)

    # 覆盖保护：记录旧文件信息
    old_lines = 0
    if file_path.exists():
        try:
            old_lines = len(file_path.read_text(encoding="utf-8").split("\n"))
        except Exception:
            old_lines = 0

    # 确保父目录存在
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return f"错误: 创建父目录失败 - {e}"

    # 追加模式：自动在内容末尾添加换行
    if mode == "append" and not content.endswith("\n"):
        content += "\n"

    try:
        if mode == "overwrite":
            file_path.write_text(content, encoding="utf-8")
        else:
            # append 模式
            if not file_path.exists():
                file_path.write_text(content, encoding="utf-8")
            else:
                with file_path.open("a", encoding="utf-8") as f:
                    f.write(content)
    except Exception as e:
        return f"错误: 写入文件失败 - {e}"

    # 写入后验证
    try:
        final_lines = len(file_path.read_text(encoding="utf-8").split("\n"))
    except Exception:
        final_lines = content_lines

    action = "创建" if (mode == "overwrite" and old_lines == 0) else "写入"
    mode_desc = "覆盖" if mode == "overwrite" else "追加"

    return (
        f"[OK] 已{action}：{path}\n"
        f"模式：{mode_desc} | 本次写入：{content_lines} 行 | "
        f"文件总行数：{final_lines} 行"
        + (f"（原 {old_lines} 行）" if old_lines > 0 and mode == "overwrite" else "")
    )


# =============================================================================
# 6. execute_command —— 命令执行
# =============================================================================

# 危险命令黑名单（正则匹配）
_DANGEROUS_COMMANDS = [
    (re.compile(r'rm\s+.*-r'), "递归删除"),
    (re.compile(r'sudo\s'), "提权操作"),
    (re.compile(r'chmod\s+.*777'), "危险权限修改"),
    (re.compile(r'mkfs\.'), "格式化文件系统"),
    (re.compile(r'dd\s+if='), "磁盘写入"),
    (re.compile(r':\(\)\s*\{'), "Fork 炸弹"),
    (re.compile(r'shutdown'), "关机"),
    (re.compile(r'reboot'), "重启"),
    (re.compile(r'(curl|wget)\s'), "网络请求"),
]


def _check_command_safety(command: str) -> str | None:
    """检查命令安全性，返回危险原因或 None。"""
    for regex, reason in _DANGEROUS_COMMANDS:
        if regex.search(command):
            return reason
    return None


@tool(
    "execute_command",
    "执行命令来检查环境或运行测试验证。"
    "参数 command: 要执行的命令，必选；"
    "working_dir: 工作目录，可选，默认使用项目根目录；"
    "timeout: 超时时间（秒），可选，默认 60 秒",
    risk_level="medium",
)
def execute_command(command: str, working_dir: str = "", timeout: int = 60) -> str:
    """执行命令检查环境或运行测试。

    Args:
        command: 要执行的命令
        working_dir: 工作目录，默认项目根目录
        timeout: 超时秒数（默认 60，最大 300）

    Returns:
        命令执行输出
    """
    # 安全检查
    danger = _check_command_safety(command)
    if danger:
        return f"错误: 命令被拒绝 — 检测到危险操作: {danger}"

    # 超时限制
    timeout = min(timeout, 300)

    # 工作目录
    if working_dir:
        cwd = _resolve_path(working_dir)
    else:
        cwd = Path.cwd()

    if not cwd.exists():
        return f"错误: 工作目录不存在 - {working_dir}"

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd),
        )
    except subprocess.TimeoutExpired:
        return f"错误: 命令执行超时（{timeout} 秒）"
    except FileNotFoundError:
        return f"错误: 命令未找到 — 请确认已安装相关工具"
    except Exception as e:
        return f"错误: 命令执行失败 - {e}"

    output = result.stdout.strip()
    stderr = result.stderr.strip()

    # 截断返回内容
    MAX_OUTPUT = 10000
    if len(output) > MAX_OUTPUT:
        output = output[:MAX_OUTPUT] + f"\n... (输出已截断，共 {len(result.stdout)} 字符)"

    parts = []
    if output:
        parts.append(output)
    if stderr:
        stderr_preview = stderr[:2000]
        if len(stderr) > 2000:
            stderr_preview += f"\n... (stderr 已截断，共 {len(stderr)} 字符)"
        parts.append(f"[stderr]\n{stderr_preview}")

    result_text = "\n".join(parts) if parts else "(无输出)"
    status = "[OK]" if result.returncode == 0 else f"[FAIL] (exit_code={result.returncode})"
    return f"{status} 执行: {command}\n\n{result_text}"
