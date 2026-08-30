"""会话管理器

负责会话的创建、消息流转、事件记录。
是连接引擎、事件存储、缓存的核心协调者。
"""

from __future__ import annotations

import time
import uuid
from typing import AsyncIterator, List, Optional

from ..ports.cache import CacheEngine
from ..ports.engine import ChatMessage, ChatResponse, GenerateOptions, LLMEngine
from ..ports.event import EventStore, SessionEvent


class SessionManager:
    """会话管理器"""

    def __init__(
        self,
        engine: LLMEngine,
        event_store: EventStore,
        cache: Optional[CacheEngine] = None,
    ):
        self.engine = engine
        self.event_store = event_store
        self.cache = cache

    def create_session(self) -> str:
        """创建新会话，返回 session_id"""
        return str(uuid.uuid4())

    async def chat(
        self,
        session_id: str,
        message: str,
        model: str,
        system_prompt: Optional[str] = None,
        options: Optional[GenerateOptions] = None,
        images: Optional[List[str]] = None,
    ) -> ChatResponse:
        """发送消息并获取回复

        流程：
        1. 记录用户消息事件
        2. 加载历史消息
        3. 调用引擎
        4. 记录助手回复事件
        5. 返回结果
        """
        # 1. 记录用户消息
        await self.event_store.append(SessionEvent(
            session_id=session_id,
            event_type="user_message",
            timestamp=time.time(),
            payload={"content": message, "model": model},
        ))

        # 2. 构建消息列表
        messages = await self._build_messages(session_id, message, system_prompt, images)

        # 3. 调用引擎
        resp = await self.engine.chat(model, messages, options)

        # 4. 记录助手回复
        await self.event_store.append(SessionEvent(
            session_id=session_id,
            event_type="assistant_message",
            timestamp=time.time(),
            payload={
                "content": resp.content,
                "model": resp.model,
                "usage": resp.usage,
            },
        ))

        return resp

    async def _build_messages(
        self,
        session_id: str,
        message: str,
        system_prompt: Optional[str] = None,
        images: Optional[List[str]] = None,
    ) -> List[ChatMessage]:
        """从会话事件记录构建引擎消息列表"""
        messages: List[ChatMessage] = []
        if system_prompt:
            messages.append(ChatMessage(role="system", content=system_prompt))

        history = await self.event_store.list_by_session(session_id)
        for evt in history:
            if evt.event_type == "user_message":
                messages.append(ChatMessage(
                    role="user",
                    content=evt.payload.get("content", ""),
                ))
            elif evt.event_type == "assistant_message":
                messages.append(ChatMessage(
                    role="assistant",
                    content=evt.payload.get("content", ""),
                ))

        if not messages or messages[-1].role != "user" or messages[-1].content != message:
            messages.append(ChatMessage(role="user", content=message))
        if images:
            # 把图片挂到最新的用户消息上
            messages[-1] = ChatMessage(
                role="user",
                content=messages[-1].content,
                images=list(images),
            )
        return messages

    async def chat_stream(
        self,
        session_id: str,
        message: str,
        model: str,
        system_prompt: Optional[str] = None,
        options: Optional[GenerateOptions] = None,
        images: Optional[List[str]] = None,
    ) -> AsyncIterator[dict]:
        """流式对话：逐块产出 {"chunk","done","content","model","usage"}"""
        await self.event_store.append(SessionEvent(
            session_id=session_id,
            event_type="user_message",
            timestamp=time.time(),
            payload={"content": message, "model": model},
        ))

        messages = await self._build_messages(session_id, message, system_prompt, images)

        parts: List[str] = []
        async for chunk in self.engine.chat_stream(model, messages, options):
            if not chunk:
                continue
            parts.append(chunk)
            yield {"chunk": chunk, "done": False}

        content = "".join(parts)
        # 流式场景下从引擎拿不到精确 token，用成品长度估算 completion_tokens
        est = len(content)
        usage = {"prompt_tokens": 0, "completion_tokens": est, "total_tokens": est}
        await self.event_store.append(SessionEvent(
            session_id=session_id,
            event_type="assistant_message",
            timestamp=time.time(),
            payload={"content": content, "model": model, "usage": usage},
        ))
        yield {"chunk": "", "done": True, "content": content, "model": model, "usage": usage}

    async def chat_continue_stream(
        self,
        session_id: str,
        prefix: str,
        model: str,
        system_prompt: Optional[str] = None,
        options: Optional[GenerateOptions] = None,
    ) -> AsyncIterator[dict]:
        """从中断处续写：把已生成的 prefix 作为半条 assistant 消息喂给模型，接着生成。

        与 chat_stream 不同：不再追加新 user_message，
        而是在历史末尾补上一条 role=assistant、content=prefix 的未完成消息，
        让模型顺着已生成内容往下续写。完成后把 prefix+新内容作为一次性
        assistant_message 事件落库（原流被中断时未落库，避免重复）。
        """
        messages: List[ChatMessage] = []
        if system_prompt:
            messages.append(ChatMessage(role="system", content=system_prompt))

        history = await self.event_store.list_by_session(session_id)
        for evt in history:
            if evt.event_type == "user_message":
                messages.append(ChatMessage(
                    role="user", content=evt.payload.get("content", ""),
                ))
            elif evt.event_type == "assistant_message":
                messages.append(ChatMessage(
                    role="assistant", content=evt.payload.get("content", ""),
                ))

        # 关键：把中断前已生成的内容作为 assistant 半成品接在末尾，让模型续写
        if prefix:
            messages.append(ChatMessage(role="assistant", content=prefix))

        parts: List[str] = []
        async for chunk in self.engine.chat_stream(model, messages, options):
            if not chunk:
                continue
            parts.append(chunk)
            yield {"chunk": chunk, "done": False}

        content = prefix + "".join(parts)
        est = len(content)
        usage = {"prompt_tokens": 0, "completion_tokens": est, "total_tokens": est}
        await self.event_store.append(SessionEvent(
            session_id=session_id,
            event_type="assistant_message",
            timestamp=time.time(),
            payload={"content": content, "model": model, "usage": usage},
        ))
        yield {"chunk": "", "done": True, "content": content, "model": model, "usage": usage}

    async def get_history(self, session_id: str, limit: int = 100) -> List[dict]:
        """获取会话历史"""
        events = await self.event_store.list_by_session(session_id, limit=limit)
        return [
            {
                "id": e.id,
                "type": e.event_type,
                "timestamp": e.timestamp,
                "payload": e.payload,
            }
            for e in events
        ]

    async def session_usage(self, session_id: str) -> dict:
        """估算会话累计上下文占用（token）"""
        events = await self.event_store.list_by_session(session_id)
        used = 0
        count = 0
        for evt in events:
            payload = evt.payload or {}
            if evt.event_type == "user_message":
                used += self._estimate_tokens(payload.get("content", ""))
                count += 1
            elif evt.event_type == "assistant_message":
                usage = payload.get("usage") or {}
                n = usage.get("completion_tokens")
                used += n if isinstance(n, int) and n > 0 else self._estimate_tokens(payload.get("content", ""))
                count += 1
        return {"used_tokens": used, "messages": count}

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        if not text:
            return 0
        cn = sum(1 for c in text if ord(c) > 0x2E80)
        en = max(0, len(text) - cn)
        return max(1, int(cn * 1.1 + en * 0.4))

    async def list_sessions(self, limit: int = 50) -> List[dict]:
        """列出所有会话"""
        return await self.event_store.list_sessions(limit)

    async def delete_session(self, session_id: str) -> int:
        """删除一个会话及其全部事件，返回删除条数"""
        return await self.event_store.delete_session(session_id)

    async def rename_session(self, session_id: str, title: str) -> str:
        """为会话设置自定义标题（持久化为 session_meta 事件）"""
        await self.event_store.append(SessionEvent(
            session_id=session_id,
            event_type="session_meta",
            timestamp=time.time(),
            payload={"title": title},
        ))
        return title

    async def pin_session(self, session_id: str, pinned: bool) -> bool:
        """设置会话置顶状态（持久化为 session_meta 事件，返回最终 pinned）"""
        await self.event_store.append(SessionEvent(
            session_id=session_id,
            event_type="session_meta",
            timestamp=time.time(),
            payload={"pinned": bool(pinned)},
        ))
        return bool(pinned)