"""缓存引擎接口 — Port

四级缓存都通过此接口抽象：
- L1 工具调用缓存
- L2 子任务缓存
- L3 本地 KV 会话缓存（预留，进阶版实现）
- L4 语义向量缓存（预留，进阶版实现）

缓存 Key 由业务层规范化后传入，保证跨 adapter 稳定。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class CacheEntry:
    """缓存条目（稳定 schema）"""
    key: str          # 规范化后的缓存键（hash 或语义向量 id）
    value: str        # 缓存值（字符串，JSON 序列化后的内容）
    namespace: str    # 命名空间：tool / subtask / session / semantic
    ttl: int = 3600   # 存活秒数，0 表示永不过期
    hits: int = 0     # 命中次数（用于统计命中率）


class CacheEngine(ABC):
    """缓存引擎 Port"""

    name: str = "base"

    @abstractmethod
    async def get(self, key: str, namespace: str = "default") -> Optional[CacheEntry]:
        """获取缓存，未命中返回 None"""
        ...

    @abstractmethod
    async def set(
        self,
        key: str,
        value: str,
        namespace: str = "default",
        ttl: int = 3600,
    ) -> None:
        """写入缓存"""
        ...

    @abstractmethod
    async def delete(self, key: str, namespace: str = "default") -> bool:
        """删除缓存"""
        ...

    @abstractmethod
    async def clear_namespace(self, namespace: str) -> int:
        """清空某个命名空间，返回删除条数"""
        ...

    @abstractmethod
    async def stats(self) -> dict:
        """缓存统计：命中数、未命中数、总条目等
        返回: {"hits": ..., "misses": ..., "total": ..., "hit_rate": ...}
        """
        ...