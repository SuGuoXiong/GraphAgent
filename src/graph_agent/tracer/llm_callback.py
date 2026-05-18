"""LLMCallbackHandler — LangChain BaseCallbackHandler 实现。

透明拦截所有 LLM 调用和工具调用，自动格式化输出到终端。
"""

import re
from typing import Any
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from graph_agent.tracer.format import (
    print_llm_request, print_llm_response,
    print_tool_call, print_tool_result, print_error,
)


class LLMCallbackHandler(BaseCallbackHandler):
    """LangChain 回调处理器。

    在 llm.invoke() / tool.invoke() 前后自动触发，
    格式化输出每次与 LLM 交互的输入和输出。
    """

    def __init__(self, log_level: str = "llm_io"):
        self.log_level = log_level

    @property
    def _show_llm(self) -> bool:
        return self.log_level in ("llm_io", "full")

    @property
    def _show_tools(self) -> bool:
        return self.log_level == "full"

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        **kwargs: Any,
    ) -> None:
        """LLM 调用前：打印输入消息。

        LangChain 1.0+ 传过来的 prompts 是 List[str]，
        格式为 "System: xxx\\nHuman: xxx" 或 "Human: xxx"。
        """
        if not self._show_llm:
            return

        agent_name = kwargs.get("name", "LLM")

        for prompt_text in prompts:
            system_text, user_text = _parse_prompt_string(prompt_text)
            print_llm_request(agent_name, system_text, user_text)

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        """LLM 调用后：打印响应内容和 token 用量。"""
        if not self._show_llm:
            return

        agent_name = kwargs.get("name", "LLM")

        for generation_list in response.generations:
            for gen in generation_list:
                content = ""
                if hasattr(gen, 'message') and gen.message:
                    content = gen.message.content if hasattr(gen.message, 'content') else str(gen.message)
                elif hasattr(gen, 'text'):
                    content = gen.text

                token_info = ""
                if response.llm_output and "token_usage" in response.llm_output:
                    tu = response.llm_output["token_usage"]
                    total = tu.get("total_tokens", "")
                    prompt = tu.get("prompt_tokens", "")
                    completion = tu.get("completion_tokens", "")
                    if total:
                        token_info = f"总 {total} tokens (入 {prompt} · 出 {completion})"
                elif hasattr(response, "usage_metadata") and response.usage_metadata:
                    um = response.usage_metadata
                    inp = um.get("input_tokens", um.get("prompt_tokens", 0))
                    out = um.get("output_tokens", um.get("completion_tokens", 0))
                    total = inp + out
                    if total:
                        token_info = f"总 {total} tokens (入 {inp} · 出 {out})"

                print_llm_response(agent_name, content, token_info)

    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        """LLM 调用出错。"""
        agent_name = kwargs.get("name", "LLM")
        print_error(agent_name, str(error))

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        **kwargs: Any,
    ) -> None:
        """工具调用前。"""
        if not self._show_tools:
            return

        tool_name = serialized.get("name", "unknown_tool")
        import json
        try:
            tool_input = json.loads(input_str) if input_str else {}
        except (json.JSONDecodeError, TypeError):
            tool_input = {"raw": str(input_str)[:200]}

        print_tool_call(tool_name, tool_input)

    def on_tool_end(self, output: str, **kwargs: Any) -> None:
        """工具调用完成。"""
        if not self._show_tools:
            return

        tool_name = kwargs.get("name", "unknown_tool")
        print_tool_result(tool_name, str(output)[:500])

    def on_tool_error(self, error: BaseException, **kwargs: Any) -> None:
        """工具调用出错。"""
        if not self._show_tools:
            return

        tool_name = kwargs.get("name", "unknown_tool")
        print_error(tool_name, str(error))


def _parse_prompt_string(text: str) -> tuple[str, str]:
    """解析 LangChain 序列化后的 prompt 字符串。

    格式: "System: xxx\\nHuman: xxx" → (system_text, human_text)
    """
    system_text = ""
    user_text = ""

    # 尝试匹配 "System: ..." 前缀
    sys_match = re.search(r'^System:\s*(.*?)(?=\n(?:Human|AI|Tool|System):|$)', text, re.DOTALL)
    if sys_match:
        system_text = sys_match.group(1).strip()

    # 尝试匹配 "Human: ..." 前缀（可能在开头或 System 之后）
    human_match = re.search(r'(?:^|\n)Human:\s*(.*?)(?=\n(?:System|AI|Tool):|$)', text, re.DOTALL)
    if human_match:
        user_text = human_match.group(1).strip()
    elif not system_text:
        # 没有任何前缀的纯文本
        user_text = text.strip()

    return system_text, user_text
