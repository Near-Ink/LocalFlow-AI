"""SQLite 缓存适配器 — MVP 阶段 L1/L2 缓存

轻量、零依赖（Python 自带 sqlite3），MVP 够用。
进阶版可替换为 Redis / 向量库等。
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Optional

from ..ports.cache import CacheEngine, CacheEntry


SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (
    key TEXT NOT NULL,
    namespace TEXT NOT NULL DEFAULT 'default',
    value TEXT NOT NULL,
    ttl INTEGER NOT NULL DEFAULT 3600,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    hits INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (key, namespace)
);
CREATE INDEX IF NOT EXISTS idx_cache_ns ON cache(namespace);
CREATE INDEX IF NOT EXISTS idx_cache_exp ON cache(expires_at);
"""


class SQLiteCache(CacheEngine):
    """SQLite 实现的缓存引擎"""

    name = "sqlite"

    def __init__(self, db_path: str | Path = "localflow_cache.db"):
        self.db_path = str(db_path)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        # 统计
        self._misses = 0

    async def get(self, key: str, namespace: str = "default") -> Optional[CacheEntry]:
        now = time.time()
        cur = self._conn.execute(
            "SELECT * FROM cache WHERE key = ? AND namespace = ? AND expires_at > ?",
            (key, namespace, now),
        )
        row = cur.fetchone()
        if not row:
            self._misses += 1
            return None
        # 命中 +1
        self._conn.execute(
            "UPDATE cache SET hits = hits + 1 WHERE key = ? AND namespace = ?",
            (key, namespace),
        )
        self._conn.commit()
        return CacheEntry(
            key=row["key"],
            value=row["value"],
            namespace=row["namespace"],
            ttl=row["ttl"],
            hits=row["hits"] + 1,
        )

    async def set(
        self,
        key: str,
        value: str,
        namespace: str = "default",
        ttl: int = 3600,
    ) -> None:
        now = time.time()
        expires = now + ttl if ttl > 0 else now + 9999999999
        self._conn.execute(
            """INSERT INTO cache (key, namespace, value, ttl, created_at, expires_at, hits)
               VALUES (?, ?, ?, ?, ?, ?, 0)
               ON CONFLICT(key, namespace) DO UPDATE SET
                   value = excluded.value,
                   ttl = excluded.ttl,
                   expires_at = excluded.expires_at""",
            (key, namespace, value, ttl, now, expires),
        )
        self._conn.commit()

    async def delete(self, key: str, namespace: str = "default") -> bool:
        cur = self._conn.execute(
            "DELETE FROM cache WHERE key = ? AND namespace = ?",
            (key, namespace),
        )
        self._conn.commit()
        return cur.rowcount > 0

    async def clear_namespace(self, namespace: str) -> int:
        cur = self._conn.execute("DELETE FROM cache WHERE namespace = ?", (namespace,))
        self._conn.commit()
        return cur.rowcount

    async def stats(self) -> dict:
        cur = self._conn.execute("SELECT COUNT(*) as total, SUM(hits) as hits FROM cache")
        row = cur.fetchone()
        total = row["total"] or 0
        hits = row["hits"] or 0
        misses = self._misses
        total_req = hits + misses
        hit_rate = (hits / total_req * 100) if total_req > 0 else 0.0
        return {
            "total": total,
            "hits": hits,
            "misses": misses,
            "hit_rate": round(hit_rate, 2),
        }