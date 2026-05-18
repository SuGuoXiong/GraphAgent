"""OrchestrationTracer — 全局可观测性管理器。

通过环境变量 GRAPHAGENT_LOG_LEVEL 或构造函数参数控制日志级别：
  - off:      不输出任何信息
  - phases:   仅输出阶段切换信息
  - llm_io:   输出阶段 + LLM 输入/输出（默认）
  - full:     输出阶段 + LLM I/O + 工具调用详情
"""

import os
import sys
from typing import Optional

from graph_agent.llm.base import LLMFactory
from graph_agent.tracer.llm_callback import LLMCallbackHandler
from graph_agent.tracer.format import (
    print_phase_header, print_phase_end,
    print_decision, print_error,
)


class OrchestrationTracer:
    """全局可观测性管理器（单例）。"""

    _instance: Optional["OrchestrationTracer"] = None
    _installed: bool = False

    def __new__(cls, level: Optional[str] = None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, level: Optional[str] = None):
        if self._initialized:
            if level is not None:
                self.log_level = level
                self._llm_callback.log_level = level
            return

        if level is None:
            level = os.getenv("GRAPHAGENT_LOG_LEVEL", "llm_io")
        level = level.lower()
        if level not in ("off", "phases", "llm_io", "full"):
            print(f"[Tracer] 未知日志级别 '{level}'，回退到 'llm_io'", file=sys.stderr)
            level = "llm_io"

        self.log_level = level
        self._llm_callback = LLMCallbackHandler(log_level=level)
        self._call_sequence = 0
        self._initialized = True

        if level != "off":
            print(f"[Tracer] 日志级别: {level}", file=sys.stderr)

    def install(self) -> None:
        """将 LLM callback 注册到 LLMFactory，使所有 LLM 调用被自动拦截。"""
        if OrchestrationTracer._installed:
            return
        LLMFactory.register_callback(self._llm_callback)
        OrchestrationTracer._installed = True

    @property
    def show_phases(self) -> bool:
        return self.log_level in ("phases", "llm_io", "full")

    def trace_phase(self, phase_name: str, agent_name: str, detail: str = "") -> None:
        """记录阶段切换。

        Args:
            phase_name: 阶段名称，如 "意图分析"
            agent_name: 执行该阶段的 Agent 名称，如 "GuardAgent"
            detail: 额外描述
        """
        if not self.show_phases:
            return
        self._call_sequence += 1
        print_phase_header(phase_name, agent_name, detail)

    def trace_decision(self, agent_name: str, decision: str, reason: str = "") -> None:
        """记录 Agent 决策结果。

        Args:
            agent_name: 做出决策的 Agent 名称
            decision: 决策描述，如 "方案审核通过"
            reason: 决策理由/反馈
        """
        if not self.show_phases:
            return
        print_decision(agent_name, decision, reason)

    def trace_error(self, agent_name: str, error: str) -> None:
        """记录错误信息。"""
        if self.log_level == "off":
            return
        print_error(agent_name, error)

    def trace_phase_end(self, status: str = "完成") -> None:
        """记录阶段结束。"""
        if not self.show_phases:
            return
        print_phase_end(status)

    def get_llm_callback(self) -> LLMCallbackHandler:
        """获取 LLM callback handler（供高级用法）。"""
        return self._llm_callback


def get_tracer() -> OrchestrationTracer:
    """获取全局 tracer 实例。"""
    return OrchestrationTracer()
