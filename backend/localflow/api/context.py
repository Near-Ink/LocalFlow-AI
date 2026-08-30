"""上下文占用信息 API

返回当前引擎（本地/云端）模型的总上下文容量与当前会话已用 token。
"""

from fastapi import APIRouter, Depends
from typing import Optional

from ..deps import get_app


router = APIRouter(prefix="/api/context", tags=["context"])


@router.get("")
async def context(session_id: Optional[str] = None, model: Optional[str] = None, app=Depends(get_app)):
    """当前引擎模型的上下文容量 + 会话已用；model 可指定(用于本地选中的具体模型)"""
    # 判定当前引擎
    if app.cloud_engine is not None and app.engine is app.cloud_engine:
        source = "cloud"
        target_model = model or app.config.cloud_model
        context_size = app.config.cloud_context_size
    else:
        source = "local"
        target_model = model or app.config.default_model
        context_size = 0
        try:
            context_size = await app.local_engine.get_context_length(target_model)
        except Exception:
            context_size = 0

    used = 0
    messages = 0
    if session_id:
        try:
            u = await app.sessions.session_usage(session_id)
            used = u["used_tokens"]
            messages = u["messages"]
        except Exception:
            used, messages = 0, 0

    return {
        "engine": source,
        "model": target_model,
        "context_size": int(context_size or 0),
        "used_tokens": int(used),
        "messages": int(messages),
    }