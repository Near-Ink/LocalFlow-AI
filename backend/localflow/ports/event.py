"""事件溯源接口 — Port

Session Event 全链路记录，支持回放。
这是 DSH 核心能力之一，但 schema 由本地定义，adapter 可替换。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class SessionEvent:
    """会话事件（稳定 schema）

    注意：这里的字段是本地领域模型，不硬编码 DSH 私有字段。
    adapter 可在 raw 里存后端原始数据，但业务层只用稳定字段。
    """
    id: Optional[str] = None
    session_id: str = ""
    event_type: str = ""     # user_message / assistant_message / tool_call / subtask_start / subtask_end / ...
    timestamp: float = 0.0
    payload: dict = field(default_factory=dict)  # 事件内容（稳定字段集合）
    raw: Optional[dict] = None                   # adapter 原始数据，业务层尽量不用


class EventStore(ABC):
    """事件存储 Port"""

    name: str = "base"

    @abstractmethod
    async def append(self, event: SessionEvent) -> str:
        """追加事件，返回事件 ID"""
        ...

    @abstractmethod
    async def list_by_session(
        self,
        session_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> List[SessionEvent]:
        """按会话列出事件（按时间正序）"""
        ...

    @abstractmethod
    async def get(self, event_id: str) -> Optional[SessionEvent]:
        """获取单条事件"""
        ...

    @abstractmethod
    async def list_sessions(self, limit: int = 50) -> List[dict]:
        """列出会话列表
        返回: [{"session_id": "...", "first_event": ..., "last_event": ..., "event_count": ...}, ...]
        """
        ...

    @abstractmethod
    async def delete_session(self, session_id: str) -> int:
        """删除一个会话的全部事件，返回删除条数"""
        ...