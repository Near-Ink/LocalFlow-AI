"""缓存命中统计 API

把 L1/L2 缓存命中情况暴露给前端硬件监控页的「缓存状态」区展示。
命中率是本产品「高命中缓存、省 Token」卖点的可视化落点。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..deps import get_app

router = APIRouter(prefix="/api/cache", tags=["cache"])


@router.get("/stats")
async def cache_stats(app=Depends(get_app)):
    cache = getattr(app, "cache", None)
    enabled = bool(getattr(app.config, "enable_cache", True))
    running = cache is not None

    stats = {
        "total": 0,
        "hits": 0,
        "misses": 0,
        "hit_rate": 0.0,
    }
    if running and hasattr(cache, "stats"):
        try:
            stats = await cache.stats()
        except Exception:
            stats = {
                "total": 0,
                "hits": 0,
                "misses": 0,
                "hit_rate": 0.0,
            }

    return {
        "ok": True,
        "cache_enabled": enabled,
        "running": running,
        "stats": {k: (round(v, 2) if k == "hit_rate" else v) for k, v in stats.items()},
    }