"""Adapters — 具体实现层

所有 ports 的具体实现都放在这里。
DSH 更新、后端更换时，只增改 adapter，不动 ports 与 core。

当前实现（MVP 阶段）：
- ollama_engine.py    — Ollama 本地推理引擎
- openai_engine.py    — OpenAI 兼容云端引擎
- sqlite_cache.py     — SQLite 缓存（L1/L2）
- sqlite_event.py     — SQLite 事件存储
- langgraph_scheduler — LangGraph 子任务调度器（骨架）
- system_hardware.py  — 系统级硬件监控

进阶版可新增：
- vllm_engine.py      — vLLM 后端
- llamacpp_engine.py  — llama.cpp 后端
- semantic_cache.py   — 语义向量缓存（L4）
- dsh_adapter_xxx.py  — DSH 参考实现的对齐适配器（按版本分文件）
"""

from .ollama_engine import OllamaEngine
from .openai_engine import OpenAIEngine
from .sqlite_cache import SQLiteCache
from .sqlite_event import SQLiteEventStore
from .langgraph_scheduler import LangGraphScheduler
from .system_hardware import SystemHardwareMonitor

__all__ = [
    "OllamaEngine",
    "OpenAIEngine",
    "SQLiteCache",
    "SQLiteEventStore",
    "LangGraphScheduler",
    "SystemHardwareMonitor",
]