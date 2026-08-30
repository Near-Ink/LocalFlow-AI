"""会话 API"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..deps import get_app


router = APIRouter(prefix="/api/sessions", tags=["sessions"])


class RenameRequest(BaseModel):
    title: str


class PinRequest(BaseModel):
    pinned: bool


@router.get("")
async def list_sessions(app=Depends(get_app), limit: int = 50):
    """列出所有会话"""
    return await app.sessions.list_sessions(limit=limit)


@router.get("/{session_id}/history")
async def session_history(session_id: str, app=Depends(get_app), limit: int = 100):
    """获取会话历史"""
    return await app.sessions.get_history(session_id, limit=limit)


@router.delete("/{session_id}")
async def delete_session(session_id: str, app=Depends(get_app)):
    """删除一个会话及其全部事件（不可恢复）"""
    deleted = await app.sessions.delete_session(session_id)
    return {"deleted": deleted, "session_id": session_id}


@router.put("/{session_id}/title")
async def rename_session(session_id: str, req: RenameRequest, app=Depends(get_app)):
    """设置会话自定义标题（持久化）"""
    title = (req.title or "").strip()
    if not title:
        return {"session_id": session_id, "title": ""}
    return {"session_id": session_id, "title": await app.sessions.rename_session(session_id, title)}


@router.put("/{session_id}/pin")
async def pin_session(session_id: str, req: PinRequest, app=Depends(get_app)):
    """设置会话置顶状态（持久化，置顶会话排列表前）"""
    pinned = await app.sessions.pin_session(session_id, req.pinned)
    return {"session_id": session_id, "pinned": pinned}