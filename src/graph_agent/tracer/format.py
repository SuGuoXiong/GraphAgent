"""终端格式化工具——分隔线、缩进输出。"""

import sys

_SEPARATOR_WIDTH = 72

# 阶段用的双线框
_PHASE_TOP = "╔══ {title} {pad}╗"
_PHASE_MID = "║  {text} {pad}║"
_PHASE_BOT = "╚══ {pad2}╝"

# LLM 请求/响应用的框
_LLM_TOP = "  ┌─ {title} ─{pad}┐"
_LLM_BOT = "  └─{pad2}┘"


def _pad(text: str, width: int) -> str:
    """右侧填充到指定宽度，考虑中文字符宽度。"""
    vis = 0
    for ch in text:
        vis += 2 if '一' <= ch <= '鿿' or '　' <= ch <= '〿' or '＀' <= ch <= '￯' else 1
    need = width - vis - 2  # 2 for left border spaces
    return " " * max(0, need)


def print_phase_header(phase_name: str, agent_name: str, detail: str = "") -> None:
    """打印阶段开始的双线框。

    Example:
        ╔══ 阶段: 意图分析 · GuardAgent ═══════════════════════╗
        ║  分析用户输入意图                                     ║
        ╚═══════════════════════════════════════════════════════╝
    """
    title = f"阶段: {phase_name} · {agent_name}"
    width = _SEPARATOR_WIDTH

    # 顶框
    title_part = title
    pad = "═" * max(0, width - len(title_part) - 4)
    print(f"\n{_PHASE_TOP.format(title=title_part, pad=pad)}", file=sys.stderr)

    # 中间描述
    if detail:
        desc = detail[:width - 6]
        pad2 = _pad(desc, width)
        print(_PHASE_MID.format(text=desc, pad=pad2), file=sys.stderr)

    # 底框
    print(_PHASE_BOT.format(pad2="═" * (width - 4)), file=sys.stderr)
    sys.stderr.flush()


def print_phase_end(status: str = "完成") -> None:
    """打印阶段结束标记。"""
    print(f"  ── {status} ──\n", file=sys.stderr)
    sys.stderr.flush()


def print_llm_request(agent_name: str, system_prompt: str, user_text: str,
                      max_system_chars: int = 600, max_user_chars: int = 500) -> None:
    """打印 LLM 请求信息。

    Example:
          ┌─ LLM 请求 → GuardAgent ────────────────────────────
          │ System: 你是一个意图分析专家...
          │ User: 帮我查一下天气
          └─────────────────────────────────────────────────────
    """
    width = _SEPARATOR_WIDTH
    title = f"LLM 请求 → {agent_name}"
    pad = "─" * max(0, width - len(title) - 5)
    print(f"\n{_LLM_TOP.format(title=title, pad=pad)}", file=sys.stderr)

    sys_text = system_prompt[:max_system_chars]
    if len(system_prompt) > max_system_chars:
        sys_text += f"... (共 {len(system_prompt)} 字符)"
    for line in sys_text.split("\n")[:12]:
        print(f"  │ System: {line[:width - 14]}", file=sys.stderr)

    user_text_disp = user_text[:max_user_chars]
    if len(user_text) > max_user_chars:
        user_text_disp += f"... (共 {len(user_text)} 字符)"
    for line in user_text_disp.split("\n")[:8]:
        print(f"  │ User: {line[:width - 11]}", file=sys.stderr)

    print(_LLM_BOT.format(pad2="─" * (width - 4)), file=sys.stderr)
    sys.stderr.flush()


def print_llm_response(agent_name: str, content: str, token_info: str = "",
                       max_chars: int = 1500) -> None:
    """打印 LLM 响应信息。

    Example:
          ┌─ LLM 响应 ← GuardAgent · 234 tokens ─────────────
          │ {"intent": "查询天气", "complexity": 2}
          └─────────────────────────────────────────────────────
    """
    width = _SEPARATOR_WIDTH
    title = f"LLM 响应 ← {agent_name}"
    if token_info:
        title += f" · {token_info}"
    pad = "─" * max(0, width - len(title) - 5)
    print(f"\n{_LLM_TOP.format(title=title, pad=pad)}", file=sys.stderr)

    text = content[:max_chars]
    if len(content) > max_chars:
        text += f"... (共 {len(content)} 字符)"
    for line in text.split("\n")[:30]:
        print(f"  │ {line[:width - 6]}", file=sys.stderr)

    print(_LLM_BOT.format(pad2="─" * (width - 4)), file=sys.stderr)
    sys.stderr.flush()


def print_decision(agent_name: str, decision: str, reason: str = "") -> None:
    """打印决策结果。"""
    symbol = "✓" if "通过" in decision or "approved" in decision.lower() else "✗"
    msg = f"  {symbol} {decision}"
    if reason:
        msg += f" — {reason[:120]}"
    print(msg, file=sys.stderr)
    sys.stderr.flush()


def print_tool_call(tool_name: str, tool_input: dict) -> None:
    """打印工具调用信息。"""
    input_str = str(tool_input)
    if len(input_str) > 300:
        input_str = input_str[:300] + "..."
    print(f"  🔧 {tool_name}({input_str})", file=sys.stderr)
    sys.stderr.flush()


def print_tool_result(tool_name: str, output: str) -> None:
    """打印工具返回结果。"""
    out = output[:200]
    if len(output) > 200:
        out += "..."
    print(f"  ← {tool_name}: {out}", file=sys.stderr)
    sys.stderr.flush()


def print_error(agent_name: str, error: str) -> None:
    """打印错误信息。"""
    print(f"\n  ⚠ {agent_name} 错误: {error[:500]}", file=sys.stderr)
    sys.stderr.flush()
