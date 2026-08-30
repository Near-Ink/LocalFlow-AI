"""模型管理 API"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List

from ..deps import get_app


router = APIRouter(prefix="/api/models", tags=["models"])


class ModelInfo(BaseModel):
    id: str
    name: str
    size: float = 0
    backend: str = "local"
    vision: bool = False  # 是否支持视觉（多模态可看图）


class PullReq(BaseModel):
    name: str


@router.get("", response_model=List[ModelInfo])
async def list_models(app=Depends(get_app)):
    """列出本地可用模型（含视觉能力标注）"""
    models = await app.engine.list_models()
    out = []
    for m in models:
        mid = m["id"]
        vision = False
        # 仅本地 Ollama 引擎能查能力；云端另走 cloud 列表
        sup = getattr(app.engine, "supports_vision", None)
        if sup:
            try:
                vision = await sup(mid)
            except Exception:
                vision = False
        out.append(ModelInfo(
            id=mid,
            name=m.get("name", mid),
            size=m.get("size", 0),
            backend=app.engine.name,
            vision=vision,
        ))
    return out


@router.post("/pull")
async def pull_model(req: PullReq, app=Depends(get_app)):
    """后台拉取本地 Ollama 模型：立即返回 task_id，不阻塞接口连接。

    前端据此 task_id 轮询 /api/models/pull/{task_id} 获取实时进度。
    适配不同环境：复用应用配置的 Ollama 地址；断连/超时在任务内标记，可续传。
    """
    name = req.name.strip()
    if not name:
        return {"ok": False, "error": "模型名为空"}
    task_id = app.pull_tasks.enqueue(name)
    return {"ok": True, "model": name, "task_id": task_id}


@router.get("/pull/{task_id}")
async def pull_status(task_id: str, app=Depends(get_app)):
    """查询某个后台拉取任务的实时进度/状态"""
    snap = app.pull_tasks.snapshot(task_id)
    if snap is None:
        return {"ok": False, "error": "任务不存在（可能为进程重启前提交，可重新拉取）"}
    return {
        "ok": True,
        "task_id": snap["task_id"],
        "model": snap["model"],
        "status": snap["status"],
        "progress": snap["progress"],
        "downloaded": snap["downloaded"],
        "total": snap["total"],
        "error": snap["error"],
    }


@router.get("/pull-tasks")
async def pull_tasks(app=Depends(get_app), limit: int = 20):
    """列出最近的拉取任务（供历史/调试）"""
    return {"ok": True, "tasks": app.pull_tasks.list_tasks(limit)}


@router.get("/health")
async def health(app=Depends(get_app)):
    """检查引擎健康状态"""
    ok = await app.engine.health()
    return {"status": "ok" if ok else "unavailable", "engine": app.engine.name}