"""dsh 协同端点

DeepSeek Harness 0.1.1-rc 的会话列表没有「删除会话」功能（官方设计如此），
本模块由 LocalFlow 协同实现：dsh 前端补丁调用本端点，删除对应会话数据目录。

dsh 数据目录定位顺序：
  1. 环境变量 LOCALFLOW_DSH_HOME
  2. 默认 ~/.dsh
  3. 开发环境回退：backend 同级 dsh-poc/dsh-home
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..deps import get_app

router = APIRouter(prefix="/api/dsh", tags=["dsh"])


class SessionDeleteReq(BaseModel):
    session_id: str


def _resolve_dsh_home() -> Path:
    """解析 dsh 数据目录（sessions 的父目录）"""
    env_home = (os.environ.get("LOCALFLOW_DSH_HOME", "") or "").strip()
    if env_home:
        p = Path(env_home).expanduser()
        if p.is_dir():
            return p
    default = Path.home() / ".dsh"
    if default.is_dir():
        return default
    dev_cand = Path(__file__).resolve().parents[2] / "dsh-poc" / "dsh-home"
    if dev_cand.is_dir():
        return dev_cand
    raise HTTPException(status_code=500, detail="无法定位 dsh 数据目录，请设置 LOCALFLOW_DSH_HOME")


def _normalize_sid(sid: str) -> str:
    sid = (sid or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="缺少 session_id")
    # 允许传入裸 uuid 或带 session- 前缀；拒绝路径穿越
    if not sid.replace("-", "").replace("session", "").isalnum():
        raise HTTPException(status_code=400, detail="非法的 session_id")
    return sid if sid.startswith("session-") else f"session-{sid}"


@router.post("/sessions/delete")
async def delete_session(req: SessionDeleteReq, app=Depends(get_app)):
    """删除一个 dsh 会话（会话数据目录 + 关联项目缓存条目）"""
    sid = _normalize_sid(req.session_id)
    home = _resolve_dsh_home()
    sessions_root = home / "sessions"
    if not sessions_root.is_dir():
        raise HTTPException(status_code=404, detail="dsh 会话目录不存在")

    removed: list[str] = []
    for ws_dir in sessions_root.iterdir():
        if not ws_dir.is_dir():
            continue
        target = ws_dir / sid
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
            if not target.exists():
                removed.append(str(target.relative_to(home)))

    # 清理项目缓存中的会话条目（可选，缺失不影响）
    projcache = home / "storages" / "session_projcache.json"
    if projcache.exists():
        try:
            import json
            data = json.loads(projcache.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                changed = False
                for ws, entries in list(data.items()):
                    if isinstance(entries, dict) and sid in entries:
                        del entries[sid]
                        changed = True
                    elif isinstance(entries, list):
                        new = [e for e in entries if isinstance(e, dict) and e.get("session_id") != sid and str(e.get("id", "")).strip("/").split("/")[-1] != sid]
                        if len(new) != len(entries):
                            entries[:] = new
                            changed = True
                if changed:
                    projcache.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    if not removed:
        raise HTTPException(status_code=404, detail=f"未找到会话 {req.session_id}")
    return {"ok": True, "removed": removed, "dsh_home": str(home)}
