"""ReAct 工具循环适配器 — Agent 运行时默认实现

让模型在「思考 → 决定调某个工具 → 拿到结果 → 再思考」之间反复，直到给出最终
答案或触定上限。要点：

- 调用哪个模型由外层用 ``_resolve_model`` 决定后传入，保证与聊天选择一致。
- 确认流程：高危工具返回 ``require_confirm=True`` 时循环暂停并向上返回
  ``needs_confirm``；由 ``confirm_tool`` / ``reject_tool`` 在用户表态后恢复。
- 每一步（模型调用 / 工具调用 / 工具结果 / 终稿）写入事件溯源并回调
  on_progress，供前端渲染时间线。
- 稳定的内存内工具消息结构 ``{id, name, arguments(dict)}``；转成各厂商 wire
  格式的工作收归引擎适配器 ``_msg_to_dict``，不在 agent 层硬编码。

阶段 2 收敛：单 agent、默认 ≤12 步、工具结果截断，异常一律收敛为可回退结果。
"""

from __future__ import annotations

import json
import re
import time
import uuid
from typing import Callable, Dict, List, Optional

from ..ports.agent import (
    EVT_AGENT_FINAL,
    EVT_AGENT_STEP,
    EVT_TOOL_CALL,
    EVT_TOOL_RESULT,
    AgentRunResult,
    AgentRuntime,
    AgentStep,
)
from ..ports.engine import ChatMessage, ChatResponse, GenerateOptions, LLMEngine
from ..ports.event import SessionEvent
from ..ports.tool import Tool, ToolContext, ToolRegistry

SYSTEM_PROMPT = (
    "你是 LocalFlow 的本地助手，可以调用工具来真正做事。\n"
    "1. 需要信息或动作时直接决定调用工具，不必解释；\n"
    "2. 拿到工具结果后判断任务是否完成，完成则给出简洁的最终回答；\n"
    "3. 若工具被拒绝或不可用，如实说明，不要编造结果。"
)

MAX_TOOL_RESULT_CHARS = 4000   # 工具结果塞回消息前的截断，控上下文占用

# 参数类失败的识别：命中后向模型注入参数 schema 纠错提示，引导用正确参数名重试
_PARAM_ERR = re.compile(
    r"缺少|需要|必须|未知参数|参数|unknown argument|invalid|"
    r"must (provide|specify|be)|required",
    re.I,
)


def _param_hint(tool: Tool) -> str:
    """从工具的 input_schema 生成一行参数说明，供模型纠错。"""
    schema = getattr(tool, "input_schema", None) or {}
    props = schema.get("properties") or {}
    if not props:
        return ""
    req = set(schema.get("required") or [])
    fields = []
    for k, v in props.items():
        typ = v.get("type", "string")
        part = f"{k}*" if k in req else k
        part += f"({typ}"
        if v.get("enum"):
            part += " ∈ " + ",".join(str(e) for e in v["enum"])
        if v.get("description"):
            part += ": " + v["description"]
        part += ")"
        fields.append(part)
    return tool.name + "( " + "；".join(fields) + " )"


# --- 文本式 ReAct 降级：模型不支持原生 tools 时，用 [TOOL_CALL]{...}[/TOOL_CALL] 文本块表达工具调用 ---
_TXT_TOOL_RE = re.compile(r"\[TOOL_CALL\]\s*(\{.*?\})\s*\[/TOOL_CALL\]", re.S)


def _parse_text_tool_calls(content: str) -> list:
    """从模型纯文本回复中提取文本式工具调用 → 归一化 tool_calls"""
    calls = []
    for m in _TXT_TOOL_RE.finditer(content or ""):
        try:
            obj = json.loads(m.group(1))
            name = obj.get("name")
            args = obj.get("arguments") or obj.get("args") or {}
            if not name:
                continue
            calls.append({
                "id": f"call_{uuid.uuid4().hex[:8]}",
                "name": name,
                "arguments": args,
            })
        except Exception:
            continue
    return calls


def _schemas_to_text(schemas: list) -> str:
    """把 OpenAI 兼容 tools schemas 折叠成便于给仅文本模型的工具说明"""
    lines = []
    for s in schemas or []:
        fn = (s.get("function") or {}) if isinstance(s, dict) else {}
        name = fn.get("name", "?")
        desc = fn.get("description", "")
        params = fn.get("parameters") or {}
        props = params.get("properties") or {}
        req = set(params.get("required") or [])
        parts = []
        for k, v in props.items():
            parts.append(f"{k}{'*' if k in req else ''}({v.get('type','string')})")
        argline = (" " + " ".join(parts)) if parts else ""
        lines.append(f"- {name}{argline}: {desc}")
    return "\n".join(lines)


class ReactAgent(AgentRuntime):
    """ReAct 工具循环 Agent（MVP 单 agent）"""

    name = "react"

    def __init__(
        self,
        get_engine,
        tool_registry: ToolRegistry,
        event_store=None,
    ) -> None:
        self.get_engine = get_engine          # () -> LLMEngine，始终读当前引擎
        self.tool_registry = tool_registry
        self.event_store = event_store
        self._run: Optional["_RunState"] = None

    # ------------------------------------------------------------------
    # Port 接口
    # ------------------------------------------------------------------

    async def run_once(
        self,
        task: str,
        model: str,
        tools: Optional[list] = None,
        on_progress: Optional[Callable[[AgentStep], None]] = None,
        max_steps: int = 12,
        ctx: Optional[ToolContext] = None,
        images: Optional[list] = None,
    ) -> AgentRunResult:
        tool_list = list(tools) if tools is not None else self.tool_registry.list()
        messages: List[ChatMessage] = [
            ChatMessage(role="system", content=SYSTEM_PROMPT),
            ChatMessage(role="user", content=task, images=images or None),
        ]
        state = _RunState(
            agent=self,
            model=model,
            task=task,
            messages=messages,
            schemas=self.tool_registry.schemas(),
            ctx=ctx,
            max_steps=max_steps,
            on_progress=on_progress or (lambda _s: None),
        )
        self._run = state
        res = await state.loop()
        return await self._persist_final(state, res)

    async def confirm_tool(
        self,
        confirm_token: str,
        tool_name: str,
        args: dict,
        ctx: Optional[ToolContext] = None,
    ):
        """用户批准高危动作：校验令牌后执行并恢复循环。"""
        state = self._active_run()
        if state is None or not state.pending:
            return AgentRunResult(ok=False, error="没有待确认的工具调用")
        if state.pending["confirm_token"] != confirm_token:
            return AgentRunResult(ok=False, error="确认令牌不匹配，已拒绝执行")
        pend = state.pending
        res = await state.resume(pend["tool"], pend["args"], approved=True)
        return await self._persist_final(state, res)

    async def reject_tool(
        self,
        confirm_token: str,
        tool_name: str,
        args: dict,
        ctx: Optional[ToolContext] = None,
    ):
        """用户拒绝高危动作：注入拒绝结果并恢复循环。"""
        state = self._active_run()
        if state is None or not state.pending:
            return AgentRunResult(ok=False, error="没有待确认的工具调用")
        if state.pending["confirm_token"] != confirm_token:
            return AgentRunResult(ok=False, error="确认令牌不匹配")
        pend = state.pending
        res = await state.resume(pend["tool"], pend["args"], approved=False)
        return await self._persist_final(state, res)

    async def _persist_final(self, state: "_RunState", res: AgentRunResult) -> AgentRunResult:
        """把 agent 任务的「入/出」写入会话历史，使其可回溯。

        记为 user_message(task) + assistant_message(终稿，带 agent 标记与 usage)。
        这样该 session 的历史里会出现一条可回看的 Agent 任务记录，同时 usage
        会被 session_usage 计入上下文占用（与聊天一致）。
        """
        if res.needs_confirm or state.final_persisted:
            return res
        if state.ctx is None or self.event_store is None or not state.ctx.session_id:
            return res
        sid = state.ctx.session_id
        if not state.user_persisted:
            await self.event_store.append(SessionEvent(
                session_id=sid, event_type="user_message", timestamp=time.time(),
                payload={"content": state.task, "model": state.model, "agent": True}))
            state.user_persisted = True
        await self.event_store.append(SessionEvent(
            session_id=sid, event_type="assistant_message", timestamp=time.time(),
            payload={"content": res.final_answer or res.error or "",
                     "model": state.model, "usage": state.usage, "agent": True,
                     "steps": len(state.steps)}))
        state.final_persisted = True
        return res

    def _active_run(self) -> Optional["_RunState"]:
        if self._run is not None and not self._run.done:
            return self._run
        return None


class _RunState:
    """一次 agent 运行的内部状态机（可暂停/恢复）"""

    def __init__(
        self,
        agent: ReactAgent,
        model: str,
        task: str,
        messages: List[ChatMessage],
        schemas: list,
        ctx: Optional[ToolContext],
        max_steps: int,
        on_progress: Callable[[AgentStep], None],
    ) -> None:
        self.agent = agent
        self.model = model
        self.task = task
        self.messages = messages
        self.schemas = schemas
        self.ctx = ctx
        self.max_steps = max_steps
        self.on_progress = on_progress
        self.steps: List[AgentStep] = []
        self.pending: Optional[dict] = None   # {"tool","args","confirm_token"}
        self.user_persisted = False   # 本次运行的任务是否已写入会话历史
        self.final_persisted = False  # 终稿是否已写入会话历史
        self.text_tool_flushed = False  # 是否已注入文本式 ReAct 指令（仅文本降级时）
        self.done = False
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    # ------------------------------------------------------------------

    async def loop(self) -> AgentRunResult:
        # max_steps 限制"发起模型调用"次数；工具执行不消耗预算，
        # 否则最后一次迭代产出的 tool_calls 会在收敛时被丢弃、工具不落地。
        i = 0
        while True:
            if i >= self.max_steps:
                self.done = True
                self._emit(EVT_AGENT_STEP, phase="final", status="stopped",
                           result={"note": "已达最大步数，任务被收敛中止"})
                return AgentRunResult(ok=False,
                                      final_answer="（已达最大步数，任务被收敛中止）",
                                      steps=self.steps, usage=self.usage, trimmed=True)
            i += 1
            resp = await self._model_step()
            if resp is None:
                self.done = True
                return AgentRunResult(ok=False, error="模型调用失败",
                                      steps=self.steps, usage=self.usage, trimmed=True)
            if resp.tool_calls:
                outcome = await self._execute(resp.tool_calls)
                if outcome["pause"]:              # 高危待确认
                    return self._pause_result(**outcome["confirm"])
                continue
            # 无工具调用 -> 终稿
            self.done = True
            self._finish(resp.content)
            return AgentRunResult(ok=True, final_answer=resp.content,
                                  steps=self.steps, usage=self.usage, trimmed=False)

    # ------------------------------------------------------------------

    async def resume(self, tool: Tool, args: dict, approved: bool) -> AgentRunResult:
        """用户对暂停中的工具表态后恢复并继续。"""
        if approved:
            result = await tool.confirm_run(self.pending["confirm_token"], args, ctx=self.ctx)
            content = result.output or result.error or str(result.ok)
            self.messages.append(ChatMessage(role="tool", content=content, name=tool.name))
            # 确认执行也走一遍工具结果步骤，携带产物元信息供前端渲染
            self._emit(EVT_TOOL_RESULT, phase="tool_result", tool=tool.name,
                       status="ok" if result.ok else "failed",
                       result={"output": result.output, "error": result.error,
                               "artifact": (result.data or {}).get("artifact")})
            self.pending = None
        else:
            note = "用户拒绝了该工具执行。请如实告知并视情况收尾。"
            self.messages.append(ChatMessage(role="tool", content=note, name=tool.name))
            self.pending = None
        return await self.loop()

    # ------------------------------------------------------------------
    # 内部步骤
    # ------------------------------------------------------------------

    async def _model_step(self) -> Optional[ChatResponse]:
        engine = self.agent.get_engine()
        use_tools = True
        sup = getattr(engine, "supports_tools", None)
        if sup:
            try:
                use_tools = await sup(self.model)
            except Exception:
                use_tools = True
        if not use_tools and not self.text_tool_flushed:
            self._flush_text_tool_prompt()
            self.text_tool_flushed = True
        opts = GenerateOptions(
            temperature=0.5,
            max_tokens=1500,
            tools=self.schemas if use_tools and self.schemas else None,
        )
        try:
            resp = await engine.chat(self.model, list(self.messages), opts)
        except Exception as e:
            self._emit(EVT_AGENT_STEP, phase="model_call", status="failed", error=str(e))
            return None
        for k in self.usage:
            self.usage[k] += resp.usage.get(k, 0)
        # 文本式 ReAct：模型不支持原生 tools 时，从纯文本回复解析工具调用
        if not use_tools:
            calls = _parse_text_tool_calls(resp.content or "")
            if calls:
                resp.tool_calls = calls
        return resp

    def _flush_text_tool_prompt(self) -> None:
        """向消息流注入文本式工具调用说明（仅对不支持原生 tools 的模型启用一次）"""
        tool_text = _schemas_to_text(self.schemas)
        instructions = (
            "【文本式工具调用】当前模型不支持原生函数调用。需要执行工具时，"
            "请输出单独一行如下格式（括号内为 JSON，不可省略），不要写任何解释：\n"
            "[TOOL_CALL]{\"name\":\"工具名\",\"arguments\":{...}}[/TOOL_CALL]\n"
            f"可用工具：\n{tool_text or '（无可用工具）'}\n"
            "不需要工具时直接输出最终回答即可。"
        )
        self.messages.append(ChatMessage(role="system", content=instructions))

    async def _execute(self, tool_calls: list) -> dict:
        """执行模型请求的工具调用。返回 {"pause": bool, "confirm": {...}}"""
        for tc in tool_calls:
            name = tc.get("name", "")
            args = tc.get("arguments") or {}
            tool = self.agent.tool_registry.get(name)
            self._emit(EVT_TOOL_CALL, phase="tool_call", tool=name, status="running", args=args)

            # 回填 assistant 工具调用消息（适配器负责转 wire 格式）
            self.messages.append(ChatMessage(
                role="assistant", content="", tool_calls=[{
                    "id": tc.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                    "name": name, "arguments": args,
                }],
            ))

            if tool is None:
                available = ", ".join(t.name for t in self.agent.tool_registry.list())
                note = f"未知工具：{name}（可用：{available}）"
                self.messages.append(ChatMessage(role="tool", content=note, name=name))
                self._emit(EVT_TOOL_RESULT, phase="tool_result", tool=name,
                           status="failed", result={"output": note})
                continue

            result = await tool.run(args, self.ctx)
            if result.require_confirm:
                token = result.confirm_token or f"confirm_{uuid.uuid4().hex[:10]}"
                self.pending = {"tool": tool, "args": args, "confirm_token": token}
                self._emit(EVT_TOOL_CALL, phase="tool_call", tool=name,
                           status="needs_confirm", args=args,
                           result={"confirm_token": token})
                return {"pause": True, "confirm": {"token": token, "tool": name, "args": args}}

            content = result.output or result.error or str(result.ok)
            err = result.error or ""
            param_hint = ""
            if not result.ok and _PARAM_ERR.search(err):
                param_hint = _param_hint(tool)
                if param_hint:
                    content = (err + f"\n\n请使用正确的参数名重新调用 {name} 工具，参数说明：\n{param_hint}").strip()
            self.messages.append(ChatMessage(role="tool", content=content, name=name))
            self._emit(EVT_TOOL_RESULT, phase="tool_result", tool=name,
                       status="ok" if result.ok else "failed",
                       result={"output": result.output, "error": err,
                               "artifact": (result.data or {}).get("artifact"),
                               **({"param_hint": param_hint} if param_hint else {})})
        return {"pause": False, "confirm": None}

    def _pause_result(self, token: str, tool: str, args: dict) -> AgentRunResult:
        msg = f"需要你确认执行：{tool} {args}"
        return AgentRunResult(ok=False, final_answer=msg, steps=self.steps,
                              usage=self.usage, error="needs_confirm",
                              needs_confirm={"token": token, "tool": tool, "args": args})

    def _finish(self, content: str) -> None:
        self._emit(EVT_AGENT_FINAL, phase="final", status="ok",
                   result={"content": content})

    # ------------------------------------------------------------------

    def _emit(self, event_type: str, *, phase: str, status: str,
              tool: str = "", args: Optional[dict] = None,
              result: Optional[dict] = None, error: Optional[str] = None) -> None:
        step = AgentStep(phase=phase, status=status, tool=tool,
                         args=args or {}, result=result, error=error)
        self.steps.append(step)
        self.on_progress(step)

        if self.agent.event_store is None:
            return
        session_id = self.ctx.session_id if self.ctx else ""
        try:
            loop = _running_loop()
        except RuntimeError:
            return
        loop.create_task(self.agent.event_store.append(SessionEvent(
            session_id=session_id,
            event_type=event_type,
            timestamp=time.time(),
            payload={
                "phase": phase, "status": status, "tool": tool,
                "args": args or {}, "result": result,
            },
        )))


def _running_loop():
    import asyncio
    return asyncio.get_running_loop()