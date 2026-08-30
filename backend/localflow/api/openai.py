"""对外统一 OpenAI 兼容 API

让任何 OpenAI SDK / 第三方 Agent 用标准协议调用 LocalFlow 的本地/云端模型：
    base_url = http://127.0.0.1:8765/v1
鉴权（可选）：若配置了 LOCALFLOW_API_KEY，则调用须带 `Authorization: Bearer <key>`。

端点：
    GET  /v1/models                  列出可用模型（本机 Ollama + 云端绑定）
    POST /v1/chat/completions        对话补全（支持 stream=true 的 SSE）

路由：按请求里的 model 名匹配——云端绑定模型走云端引擎，其余走本地 Ollama。
未知 model 返回 404（列出可用模型供客户端选择）。
"""

from __future__ import annotations

import json
import time
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, Header
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..deps import get_app
from ..ports.engine import ChatMessage, GenerateOptions

router = APIRouter(prefix="/v1", tags=["openai-compat"])

_ID_PREFIX = "chatcmpl"
_MODEL_UUID = uuid.uuid4().hex[:12]


def _auth_guard(app, authorization: Optional[str]) -> Optional[str]:
    """返回错误字符串；鉴权通过返回 None。"""
    key = (app.config.api_key or "").strip()
    if not key:
        return None  # 未配置 Key → 本机模式，不做鉴权
    if not authorization:
        return "缺少鉴权头：请带 Authorization: Bearer <API_KEY>"
    token = authorization.strip()
    if token.lower().startswith("bearer "):
        token = token.split(" ", 1)[1].strip()
    if token != key:
        return "鉴权失败：API Key 不正确"
    return None


def _resolve_engine(app, model: str):
    """返回 (engine, model_name)。按 model 名路由到本地或云端引擎。"""
    cloud = app.cloud_engine
    # 云端绑定模型集合：各绑定 model + 激活默认
    cloud_models: set = set()
    if app.config.cloud_bindings:
        cloud_models = {str(b.get("model", "")).strip() for b in app.config.cloud_bindings if b.get("model")}
    if cloud is not None and cloud.default_model:
        cloud_models.add(str(cloud.default_model).strip())

    if model in cloud_models and cloud is not None:
        return cloud, cloud.default_model
    return app.local_engine, model


async def _model_known(app, model: str) -> Optional[bool]:
    """判断 model 是否可用：云端绑定→True；本地查 /api/tags→True/False；列表获取失败→None(放行)。"""
    if app.cloud_engine is not None and app.cloud_engine.default_model == model:
        return True
    if app.config.cloud_bindings:
        if any(str(b.get("model", "")).strip() == model for b in app.config.cloud_bindings if b.get("model")):
            return True
    try:
        local = await app.local_engine.list_models()
    except Exception:
        return None
    return any(m.get("id") == model or m.get("name") == model for m in local)


def _parse_tool_calls(wire_calls) -> Optional[list]:
    """把 OpenAI wire 格式的 tool_calls 转成引擎稳定结构 [{id,name,arguments(dict)}]"""
    if not wire_calls:
        return None
    result = []
    for i, c in enumerate(wire_calls):
        if not isinstance(c, dict):
            continue
        fn = c.get("function") or {}
        args = fn.get("arguments", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        result.append({
            "id": c.get("id") or f"call_{i}",
            "name": fn.get("name", ""),
            "arguments": args,
        })
    return result or None


def _to_wire_tool_calls(stable_calls) -> Optional[list]:
    """把引擎稳定结构工具调用转回 OpenAI wire 格式（arguments 为 JSON 字符串）"""
    if not stable_calls:
        return None
    wire = []
    for c in stable_calls:
        if not isinstance(c, dict):
            continue
        wire.append({
            "id": c.get("id"),
            "type": "function",
            "function": {
                "name": c.get("name", ""),
                "arguments": json.dumps(c.get("arguments", {}) or {}, ensure_ascii=False),
            },
        })
    return wire or None


def _parse_content(item) -> tuple[str, List[str]]:
    """把 OpenAI 的 content（字符串或 parts 数组）转成 (text, image_data_uris)"""
    text_parts: List[str] = []
    images: List[str] = []
    if isinstance(item, str):
        text_parts.append(item)
    elif isinstance(item, list):
        for part in item:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "image_url":
                raw = part.get("image_url")
                url = raw.get("url", "") if isinstance(raw, dict) else str(raw or "")
                if url and not url.startswith("data:"):
                    url = url  # 远程图片暂不支持，忽略
                if url.startswith("data:"):
                    images.append(url)
            else:
                t = part.get("text")
                if t:
                    text_parts.append(str(t))
    return "\n".join(text_parts), images


def _to_messages(messages: List[dict]) -> List[ChatMessage]:
    out: List[ChatMessage] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role", "user"))
        content, images = _parse_content(m.get("content"))
        if role == "assistant" and m.get("tool_calls"):
            out.append(ChatMessage(role=role, content=content or "",
                                   tool_calls=_parse_tool_calls(m.get("tool_calls")),
                                   images=images or None))
        elif role == "tool":
            out.append(ChatMessage(role=role, content=content,
                                   name=m.get("tool_call_id"),
                                   images=images or None))
        else:
            out.append(ChatMessage(role=role, content=content or "", images=images or None))
    return out


class ChatCompletionReq(BaseModel):
    model: str = ""
    messages: List[dict] = []
    temperature: float = 0.7
    top_p: float = 0.9
    max_tokens: Optional[int] = None
    stream: bool = False
    stop: Optional[List[str]] = None
    tools: Optional[List[dict]] = None


class RespMeta(BaseModel):  # noqa: F401 (schema anchor)
    role: str = "assistant"


@router.get("/models")
async def list_models(app=Depends(get_app), authorization: Optional[str] = Header(default=None)):
    err = _auth_guard(app, authorization)
    if err:
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail=err)
    data = []
    # 本机 Ollama 模型
    try:
        local = await app.local_engine.list_models()
        for m in local:
            data.append({"id": m.get("id", m.get("name", "")), "object": "model",
                         "owned_by": "localflow-local"})
    except Exception:
        pass
    # 云端绑定模型
    for b in app.config.cloud_bindings or []:
        mid = str(b.get("model", "")).strip()
        if mid and not any(x.get("id") == mid for x in data):
            data.append({"id": mid, "object": "model", "owned_by": "localflow-cloud"})
    return {"object": "list", "data": data}


@router.post("/chat/completions")
async def chat_completions(req: ChatCompletionReq, app=Depends(get_app),
                           authorization: Optional[str] = Header(default=None)):
    from fastapi import HTTPException
    err = _auth_guard(app, authorization)
    if err:
        raise HTTPException(status_code=401, detail=err)

    if not req.messages:
        raise HTTPException(status_code=400, detail="messages 不能为空")

    model = (req.model or "").strip() or app.config.default_model
    known = await _model_known(app, model)
    if known is False:
        raise HTTPException(status_code=404,
                            detail=f"模型不存在：{model}。可用 GET /v1/models 查看当前可用模型")
    engine, resolved = _resolve_engine(app, model)
    messages = _to_messages(req.messages)
    # 模型身份注入：让外部客户端连接时，模型如实自报由谁提供 + 实际模型名
    if bool(getattr(app.config, "openai_identity_inject", True)):
        inject_sys = (
            f"System note: 你当前正通过 LocalFlow 的 OpenAI 兼容接口被调用，"
            f"实际运行的模型是「{resolved}」。当用户在对话中问你'你是哪个模型'、"
            f"'谁在提供推理服务'之类时，请如实回答：由 LocalFlow 提供，模型为 {resolved}。"
        )
        messages.insert(0, ChatMessage(role="system", content=inject_sys))
    options = GenerateOptions(
        temperature=req.temperature,
        top_p=req.top_p,
        max_tokens=req.max_tokens,
        stop=req.stop,
        stream=req.stream,
        tools=req.tools,
    )

    if not req.stream:
        resp = await engine.chat(resolved, messages, options)
        message = {"role": "assistant", "content": resp.content}
        if resp.tool_calls:
            message["tool_calls"] = _to_wire_tool_calls(resp.tool_calls)
        finish_reason = "tool_calls" if resp.tool_calls else (resp.finish_reason or "stop")
        return {
            "id": f"{_ID_PREFIX}-{_MODEL_UUID}-{int(time.time())}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": resp.model,
            "choices": [{
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
            }],
            "usage": resp.usage or {},
        }

    # SSE 流式
    async def gen():
        cid = f"{_ID_PREFIX}-{_MODEL_UUID}-{int(time.time())}"
        usage = {}
        async for chunk in engine.chat_stream(resolved, messages, options):
            if not chunk:
                continue
            usage["completion_tokens"] = usage.get("completion_tokens", 0) + 1
            yield "data: " + json.dumps({
                "id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
                "model": resolved,
                "choices": [{"index": 0, "delta": {"content": chunk}, "finish_reason": None}],
            }, ensure_ascii=False) + "\n\n"
        # 结束 chunk（含 finish_reason）
        usage.setdefault("prompt_tokens", 0)
        usage.setdefault("total_tokens", usage.get("completion_tokens", 0) + usage.get("prompt_tokens", 0))
        yield "data: " + json.dumps({
            "id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
            "model": resolved,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": usage,
        }, ensure_ascii=False) + "\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )