"""Agent API — 把本地 agent 的能力暴露给前端

- POST /api/agent/run   发起一次 agent 任务（可能因高危工具暂停返回 needs_confirm）
- POST /api/agent/run_stream  同 run，但以 NDJSON 逐条推送每一步，最后推送 final
- POST /api/agent/confirm  用户批准高危动作后恢复
- POST /api/agent/reject   用户拒绝高危动作后恢复
- GET  /api/agent/status   查看 agent 开关 / 运行时 / 可用工具

agent 是否可用取决于 feature flag（config.enable_agent）。未启用时返回 400。
"""

import asyncio
import json

import base64
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Optional

from ..deps import get_app
from ..core.artifacts import sniff
from ..ports.tool import ToolContext
from .chat import _resolve_model


router = APIRouter(prefix="/api/agent", tags=["agent"])


@router.get("/artifact")
async def agent_artifact(path: str, app=Depends(get_app)):
    """读取 Agent 产生的产物文件，供前端右侧实时预览。

    路径必须落在沙箱允许目录内；图片/音频/视频返回 base64，文本类返回全文。
    """
    sb = app.sandbox
    resolved = sb.resolve_allowed(path) if sb else None
    if resolved is None:
        raise HTTPException(status_code=400, detail="路径越界或不在允许目录内")
    if not resolved.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    art = sniff(resolved) or {"type": "other", "name": resolved.name, "path": str(resolved), "mime": "application/octet-stream"}
    try:
        raw = resolved.read_bytes()
    except OSError as e:
        raise HTTPException(status_code=400, detail=f"读取失败：{e}")
    payload = {"type": art["type"], "name": art["name"], "path": art["path"], "mime": art.get("mime", "application/octet-stream")}
    if art["type"] in ("image", "pdf", "audio", "video"):
        payload["base64"] = base64.b64encode(raw).decode()
        if art["type"] == "image":
            payload["mime"] = art.get("mime", "image/png")
    else:
        payload["text"] = raw[:200_000].decode("utf-8", errors="replace")
    return {"ok": True, "artifact": payload}


class RunRequest(BaseModel):
    task: str
    session_id: Optional[str] = None
    model: Optional[str] = None
    tools: Optional[List[str]] = None      # None=全部；否则按名字过滤
    max_steps: int = 12
    images: Optional[List[str]] = None     # 附加图片（data URI），支持 Agent 读图


class ConfirmRequest(BaseModel):
    confirm_token: str
    tool: str
    args: dict = {}


class AgentResultData(BaseModel):
    ok: bool
    final_answer: str = ""
    error: Optional[str] = None
    model: str = ""
    trimmed: bool = False
    usage: dict = {}
    needs_confirm: Optional[dict] = None
    steps: list = []


def _guard_agent(app):
    if app.agent is None:
        raise HTTPException(status_code=400, detail="agent 未启用（LOCALFLOW_ENABLE_AGENT）")
    return app.agent


def _step_dict(s) -> dict:
    return {
        "phase": s.phase,
        "status": s.status,
        "tool": s.tool,
        "args": s.args,
        "result": s.result,
        "error": s.error,
    }


def _result_dict(res, model: str) -> dict:
    return {
        "ok": res.ok,
        "final_answer": res.final_answer,
        "error": res.error,
        "model": model,
        "trimmed": res.trimmed,
        "usage": res.usage,
        "needs_confirm": res.needs_confirm,
        "steps": [_step_dict(s) for s in res.steps],
    }


def _ctx(app, session_id: Optional[str]) -> ToolContext:
    dirs = [str(d) for d in app.sandbox.allow_dirs] if app.sandbox else []
    return ToolContext(
        allow_dirs=dirs,
        session_id=session_id or "",
        event_store=app.event_store,
        extras={"app": app},
    )


def _resolve_tools(app, tools: Optional[List[str]]):
    if tools is None:
        return None  # 全部
    picked = []
    for name in tools:
        t = app.tool_registry.get(name)
        if t is not None:
            picked.append(t)
    return picked


@router.post("/run", response_model=AgentResultData)
async def agent_run(req: RunRequest, app=Depends(get_app)):
    agent = _guard_agent(app)
    model = _resolve_model(app, req.model)
    res = await agent.run_once(
        task=req.task,
        model=model,
        tools=_resolve_tools(app, req.tools),
        max_steps=req.max_steps,
        ctx=_ctx(app, req.session_id),
        images=req.images,
    )
    return AgentResultData(**_result_dict(res, model))


@router.post("/run_stream")
async def agent_run_stream(req: RunRequest, app=Depends(get_app)):
    """流式运行 agent：每完成一步即推送一条 NDJSON，最后推送 final。

    消息结构（每行一个 JSON）：
      {"type":"step", "step":{...}}   一步（思考/工具调用/工具结果）
      {"type":"final", ...AgentResultData}   最终结果（含 needs_confirm / final_answer）
    """
    agent = _guard_agent(app)
    model = _resolve_model(app, req.model)
    box: asyncio.Queue = asyncio.Queue()
    result: dict = {}
    task_done: asyncio.Event = asyncio.Event()

    async def worker():
        try:
            res = await agent.run_once(
                task=req.task,
                model=model,
                tools=_resolve_tools(app, req.tools),
                max_steps=req.max_steps,
                ctx=_ctx(app, req.session_id),
                images=req.images,
                on_progress=lambda s: box.put_nowait(("step", s)),
            )
            result.update(_result_dict(res, model))
        except Exception as e:  # 收敛为可回退结果，保持流式连接不断开
            result.update({"ok": False, "error": f"agent 运行异常：{e}", "steps": []})
        finally:
            task_done.set()

    async def gen():
        while True:
            kind, payload = await box.get()
            if kind == "step":
                yield json.dumps(
                    {"type": "step", "step": _step_dict(payload)}, ensure_ascii=False
                ) + "\n"
            elif kind == "done":
                break
        yield json.dumps({"type": "final", **result}, ensure_ascii=False) + "\n"

    # worker 立即在后台跑；归位任务在 worker 结束后向队列塞 done——两者与
    # gen() 并行，使每步都能被消费时实时 yield，而不是等全部跑完。
    bg_worker = asyncio.create_task(worker())

    async def _finalize():
        await task_done.wait()
        box.put_nowait(("done", None))

    asyncio.create_task(_finalize())
    return StreamingResponse(
        gen(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/confirm", response_model=AgentResultData)
async def agent_confirm(req: ConfirmRequest, app=Depends(get_app)):
    agent = _guard_agent(app)
    model = _resolve_model(app, None)
    res = await agent.confirm_tool(
        confirm_token=req.confirm_token,
        tool_name=req.tool,
        args=req.args,
        ctx=_ctx(app, None),
    )
    return AgentResultData(**_result_dict(res, model))


@router.post("/reject", response_model=AgentResultData)
async def agent_reject(req: ConfirmRequest, app=Depends(get_app)):
    agent = _guard_agent(app)
    model = _resolve_model(app, None)
    res = await agent.reject_tool(
        confirm_token=req.confirm_token,
        tool_name=req.tool,
        args=req.args,
        ctx=_ctx(app, None),
    )
    return AgentResultData(**_result_dict(res, model))


@router.get("/status")
async def agent_status(app=Depends(get_app)):
    tools = [
        {"name": t.name, "description": t.description, "hazardous": t.is_hazardous}
        for t in app.tool_registry.list()
    ]
    base = app.agent_status() if app.agent is not None else {
        "enabled": False, "runtime": None, "tools": []
    }
    base["tools"] = base["tools"] or []
    return {**base, "tool_details": tools}