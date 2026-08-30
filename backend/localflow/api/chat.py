"""聊天 API"""

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Optional

import json
from ..deps import get_app
from ..ports.engine import ChatMessage, GenerateOptions


router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    session_id: Optional[str] = None
    message: str
    model: Optional[str] = None
    system_prompt: Optional[str] = None
    stream: bool = False
    temperature: float = 0.7
    max_tokens: Optional[int] = None
    images: Optional[List[str]] = None  # 多模态图片：data URI 列表


class ChatContinueRequest(BaseModel):
    session_id: str
    prefix: str  # 已生成但被中止的文本前缀
    model: Optional[str] = None
    system_prompt: Optional[str] = None
    temperature: float = 0.7
    max_tokens: Optional[int] = None


class ChatResponseData(BaseModel):
    content: str
    model: str
    usage: dict
    session_id: str


def _resolve_model(app, req_model=None) -> str:
    """解析实际调用模型：保证「选择与调用一致」。

    云端引擎激活时，一律使用绑定云端保存的 default_model，
    忽略客户端传入的 model，避免用户在前端选中/传入其他型号导致把
    错误的模型名拿去调云端 API；仅本地引擎才采用客户端所选模型。
    """
    if app.cloud_engine is not None and app.engine is app.cloud_engine:
        return app.cloud_engine.default_model
    return req_model or app.config.default_model


@router.post("", response_model=ChatResponseData)
async def chat(req: ChatRequest, app=Depends(get_app)):
    """发送一条消息（非流式）"""
    import uuid
    session_id = req.session_id or str(uuid.uuid4())
    model = _resolve_model(app, req.model)

    options = GenerateOptions(
        temperature=req.temperature,
        max_tokens=req.max_tokens,
        stream=False,
    )

    resp = await app.sessions.chat(
        session_id=session_id,
        message=req.message,
        model=model,
        system_prompt=req.system_prompt,
        options=options,
        images=req.images,
    )

    return ChatResponseData(
        content=resp.content,
        model=resp.model,
        usage=resp.usage,
        session_id=session_id,
    )


@router.post("/stream")
async def chat_stream(req: ChatRequest, app=Depends(get_app)):
    """流式对话（NDJSON 逐块输出）"""
    import uuid
    session_id = req.session_id or str(uuid.uuid4())
    model = _resolve_model(app, req.model)

    options = GenerateOptions(
        temperature=req.temperature,
        max_tokens=req.max_tokens,
        stream=True,
    )

    async def gen():
        async for evt in app.sessions.chat_stream(
            session_id=session_id,
            message=req.message,
            model=model,
            system_prompt=req.system_prompt,
            options=options,
            images=req.images,
        ):
            yield json.dumps(evt, ensure_ascii=False) + "\n"

    return StreamingResponse(
        gen(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/continue/stream")
async def chat_continue_stream(req: ChatContinueRequest, app=Depends(get_app)):
    """从中断处续写（流式）：把已生成 prefix 交给会话管理器接续生成"""
    model = _resolve_model(app, req.model)

    options = GenerateOptions(
        temperature=req.temperature,
        max_tokens=req.max_tokens,
        stream=True,
    )

    async def gen():
        async for evt in app.sessions.chat_continue_stream(
            session_id=req.session_id,
            prefix=req.prefix,
            model=model,
            system_prompt=req.system_prompt,
            options=options,
        ):
            yield json.dumps(evt, ensure_ascii=False) + "\n"

    return StreamingResponse(
        gen(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )