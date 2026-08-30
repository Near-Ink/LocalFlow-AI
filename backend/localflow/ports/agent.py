"""Agent 运行时接口 — Port

Agent：让模型不仅「能说话」，还能「动工具做事」。本端口定义 agent 运行时
的统一契约；ReAct 工具循环为默认 adapter（adapters/react_agent.py，阶段 2
落地），可替换。

职责边界：scheduler 端口负责高层任务分解，agent 端口负责工具执行，
二者职责分离、互不耦合。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

from .tool import ToolContext, ToolResult


# ---- 事件类型常量（阶段 0 占位，稳定 schema，不破坏既有事件类型） ----
# Agent 每步写入现有事件溯源，附 phase/status/tool/result 便于前端渲染时间线
EVT_AGENT_STEP = "agent_step"      # agent 决策/收尾步骤
EVT_TOOL_CALL = "tool_call"       # 工具调用发起
EVT_TOOL_RESULT = "tool_result"   # 工具调用结果
EVT_AGENT_FINAL = "agent_final"   # 最终答复


@dataclass
class AgentStep:
    """Agent 单步记录（写入事件溯源，可回放）"""
    phase: str = ""        # model_call / tool_call / tool_result / final / error
    status: str = ""       # running / ok / failed / needs_confirm / stopped
    tool: str = ""         # 工具名（tool_call 场景）
    args: dict = field(default_factory=dict)
    result: Optional[dict] = None
    error: Optional[str] = None
    usage: dict = field(default_factory=dict)


@dataclass
class AgentRunResult:
    """Agent 一次任务执行的结果"""
    ok: bool
    final_answer: str = ""
    steps: List[AgentStep] = field(default_factory=list)
    error: Optional[str] = None
    usage: dict = field(default_factory=dict)   # 累计 token
    trimmed: bool = False          # 是否因步数/超时触顶被收敛（可回溯提示）
    # 需要用户确认的高危动作（非空表示循环已暂停，待 confirm_tool/reject_tool 恢复）
    needs_confirm: Optional[dict] = None  # {"token","tool","args"}


class AgentRuntime(ABC):
    """Agent 运行时 Port"""

    name: str = "base"

    @abstractmethod
    async def run_once(
        self,
        task: str,
        model: str,
        tools: Optional[list] = None,          # Tool 实例列表（来自 ToolRegistry）
        on_progress: Optional[Callable[[AgentStep], None]] = None,
        max_steps: int = 12,
        ctx: Optional[ToolContext] = None,
    ) -> AgentRunResult:
        """执行一次 Agent 任务。

        让模型在「思考 → 决定调工具 → 拿结果 → 再思考」间循环，直到
        给出最终答案或触定上限。每步通过 on_progress / 事件溯源可回放。
        爬上限或异常时收敛为可回退的普通结果，不留半截任务挂起。
        """
        ...

    @abstractmethod
    async def confirm_tool(
        self,
        confirm_token: str,
        tool_name: str,
        args: dict,
        ctx: Optional[ToolContext] = None,
    ) -> ToolResult:
        """用户批准后执行此前待确认的高危工具动作"""
        ...