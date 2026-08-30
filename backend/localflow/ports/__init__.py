"""Ports — 稳定领域接口层

本层定义 LocalFlow 的领域契约，是整个系统最稳定的部分。
DSH 的更新、推理后端的更换，都只影响 adapters 层，不影响这里。

设计原则：
- Schema 稳定优先：接口数据模型用本地领域命名，不硬编码 DSH / Ollama 私有字段
- 面向抽象编程：所有业务逻辑依赖 ports，不直接依赖具体 adapter
- 可插拔：每个 port 都可以有多个 adapter 实现，运行时可切换
"""

from .engine import LLMEngine, ChatMessage, ChatResponse, GenerateOptions
from .cache import CacheEngine, CacheEntry
from .event import EventStore, SessionEvent
from .scheduler import TaskScheduler, SubTask, SubTaskResult
from .hardware import HardwareMonitor, HardwareInfo, GPUInfo

__all__ = [
    "LLMEngine", "ChatMessage", "ChatResponse", "GenerateOptions",
    "CacheEngine", "CacheEntry",
    "EventStore", "SessionEvent",
    "TaskScheduler", "SubTask", "SubTaskResult",
    "HardwareMonitor", "HardwareInfo", "GPUInfo",
]