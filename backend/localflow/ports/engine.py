"""LLM 引擎接口 — Port

所有推理后端（Ollama / OpenAI / vLLM / llama.cpp 等）都实现此接口。
业务层只依赖本接口，不关心具体后端。

设计要点：
- 字段用稳定领域命名（model / messages / temperature…）
- 不暴露任何特定后端的私有参数
- 流式与非流式都通过同一接口提供
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator, List, Optional


@dataclass
class ChatMessage:
    """一条聊天消息"""
    role: str  # system / user / assistant / tool
    content: str
    name: Optional[str] = None  # tool call 场景用
    tool_calls: Optional[list] = None  # 工具调用列表（可选，后端支持时填）
    images: Optional[List[str]] = None  # 多模态图片输入：data URI 列表（"data:image/...;base64,<b64>"）


@dataclass
class GenerateOptions:
    """生成参数（稳定字段）"""
    temperature: float = 0.7
    top_p: float = 0.9
    max_tokens: Optional[int] = None
    stop: Optional[List[str]] = None
    stream: bool = False
    # 工具调用：工具栏的 OpenAI 兼容嵌套结构（由 agent 层构造，引擎透传）
    tools: Optional[list] = None
    # 扩展位：adapter 可读取但业务层不直接依赖
    extras: dict = field(default_factory=dict)


@dataclass
class ChatResponse:
    """聊天返回结果"""
    content: str
    model: str
    usage: dict  # {"prompt_tokens": ..., "completion_tokens": ..., "total_tokens": ...}
    finish_reason: str = "stop"
    # 归一化的工具调用列表（有则非空）：
    # [{"id": "...", "name": "...", "arguments": {...}}, ...]；无工具调用则为 None
    tool_calls: Optional[list] = None
    # 原始响应，adapter 特有信息放这里，业务层尽量不读
    raw: Optional[dict] = None


class LLMEngine(ABC):
    """LLM 引擎 Port — 所有推理后端的统一接口"""

    name: str = "base"  # adapter 名称，如 "ollama" / "openai"

    @abstractmethod
    async def chat(
        self,
        model: str,
        messages: List[ChatMessage],
        options: Optional[GenerateOptions] = None,
    ) -> ChatResponse:
        """非流式聊天补全"""
        ...

    @abstractmethod
    async def chat_stream(
        self,
        model: str,
        messages: List[ChatMessage],
        options: Optional[GenerateOptions] = None,
    ) -> AsyncIterator[str]:
        """流式聊天补全，yield 文本片段"""
        ...

    @abstractmethod
    async def list_models(self) -> List[dict]:
        """列出可用模型
        返回: [{"id": "...", "name": "...", "size": "...", ...}, ...]
        """
        ...

    @abstractmethod
    async def health(self) -> bool:
        """引擎是否可用"""
        ...