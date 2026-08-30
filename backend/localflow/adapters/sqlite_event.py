"""SQLite 事件存储适配器 — Session Event 溯源

MVP 阶段用 SQLite，进阶版可替换。
Schema 稳定：payload 为 JSON，存本地领域模型，不硬编码 DSH 字段。
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import List, Optional

from ..ports.event import EventStore, SessionEvent


SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    timestamp REAL NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    raw TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
"""


class SQLiteEventStore(EventStore):
    """SQLite 事件存储"""

    name = "sqlite"

    def __init__(self, db_path: str | Path = "localflow_events.db"):
        self.db_path = str(db_path)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    async def append(self, event: SessionEvent) -> str:
        if not event.id:
            event.id = str(uuid.uuid4())
        if not event.timestamp:
            event.timestamp = time.time()
        self._conn.execute(
            "INSERT INTO events (id, session_id, event_type, timestamp, payload, raw) VALUES (?, ?, ?, ?, ?, ?)",
            (
                event.id,
                event.session_id,
                event.event_type,
                event.timestamp,
                json.dumps(event.payload, ensure_ascii=False),
                json.dumps(event.raw, ensure_ascii=False) if event.raw else None,
            ),
        )
        self._conn.commit()
        return event.id

    async def list_by_session(
        self,
        session_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> List[SessionEvent]:
        cur = self._conn.execute(
            "SELECT * FROM events WHERE session_id = ? ORDER BY timestamp ASC LIMIT ? OFFSET ?",
            (session_id, limit, offset),
        )
        rows = cur.fetchall()
        return [self._row_to_event(r) for r in rows]

    async def get(self, event_id: str) -> Optional[SessionEvent]:
        cur = self._conn.execute("SELECT * FROM events WHERE id = ?", (event_id,))
        row = cur.fetchone()
        return self._row_to_event(row) if row else None

    @staticmethod
    def _read_session_meta(payload_str):
        p = (payload_str and json.loads(payload_str)) or {}
        return p if isinstance(p, dict) else {}

    async def list_sessions(self, limit: int = 50) -> List[dict]:
        """列出会话，附带 title（自定义优先，否则取首条用户消息前 24 字）与 pinned（置顶），置顶会话排前"""
        cur = self._conn.execute(
            """SELECT session_id,
                      MIN(timestamp) as first_event,
                      MAX(timestamp) as last_event,
                      COUNT(*) as event_count
               FROM events
               WHERE event_type != 'session_meta'
               GROUP BY session_id
               ORDER BY last_event DESC
               LIMIT ?""",
            (limit,),
        )
        rows = [dict(r) for r in cur.fetchall()]
        metas = {}
        pin = {}
        # 按插入顺序(rowid 递增)取每个会话最新的 session_meta，避免 float 时间戳并列导致排序不确定
        for r in self._conn.execute(
            "SELECT session_id, payload FROM events WHERE event_type = 'session_meta' ORDER BY rowid DESC"
        ):
            p = self._read_session_meta(r["payload"])
            sid = r["session_id"]
            if sid not in metas and p.get("title") is not None and str(p["title"]).strip():
                metas[sid] = str(p["title"]).strip()
            if sid not in pin and p.get("pinned") is not None:
                pin[sid] = bool(p["pinned"])
        previews = {}
        for r in self._conn.execute(
            "SELECT session_id, payload FROM events WHERE event_type = 'user_message' ORDER BY timestamp ASC"
        ):
            sid = r["session_id"]
            if sid in previews:
                continue
            p = self._read_session_meta(r["payload"])
            previews[sid] = str((p.get("content") or "")).strip()
        sess_rows = []
        for row in rows:
            sid = row["session_id"]
            row["title"] = (metas.get(sid) or previews.get(sid, "")[:24]
                            or f"{row['event_count']} 条消息")
            row["pinned"] = bool(pin.get(sid, False))
            sess_rows.append(row)
        sess_rows.sort(key=lambda r: 0 if r["pinned"] else 1)
        return sess_rows

    async def delete_session(self, session_id: str) -> int:
        cur = self._conn.execute(
            "DELETE FROM events WHERE session_id = ?", (session_id,)
        )
        self._conn.commit()
        return cur.rowcount

    # --- helpers ---

    def _row_to_event(self, row: sqlite3.Row) -> SessionEvent:
        return SessionEvent(
            id=row["id"],
            session_id=row["session_id"],
            event_type=row["event_type"],
            timestamp=row["timestamp"],
            payload=json.loads(row["payload"] or "{}"),
            raw=json.loads(row["raw"]) if row["raw"] else None,
        )