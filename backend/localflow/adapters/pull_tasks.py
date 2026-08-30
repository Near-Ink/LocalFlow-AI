"""后台模型拉取任务管理器

把「拉取 Ollama 模型」做成后台异步任务：
- 提交后立即返回 task_id，前端轮询进度，不阻塞接口连接；
- 任务串行执行（同一时间只拉一个），避免 Ollama 并发冲突；
- 流式解析下载进度，断连时标记 error 提示可续传；
- 适配不同环境：引擎地址经 engine_factory 注入（可用已配置的 Ollama 地址，
  而非硬编码 localhost），跨 OS / 跨 Ollama 版本 / 慢网均可行。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Callable, Dict, Optional


class PullTask:
    __slots__ = (
        "id", "model", "status", "progress", "downloaded", "total",
        "error", "created_at", "finished_at",
    )

    def __init__(self, task_id: str, model: str):
        self.id = task_id
        self.model = model
        self.status = "pending"      # pending / running / success / error
        self.progress = 0
        self.downloaded = 0
        self.total = 0
        self.error = ""
        self.created_at = time.time()
        self.finished_at: Optional[float] = None

    def snapshot(self) -> dict:
        return {
            "task_id": self.id,
            "model": self.model,
            "status": self.status,
            "progress": self.progress,
            "downloaded": self.downloaded,
            "total": self.total,
            "error": self.error,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }


class PullTaskManager:
    """后台拉取任务管理器（进程内单例，每个 app 一个）"""

    def __init__(self, engine_factory: Callable[[], object]):
        self._engine_factory = engine_factory
        self._tasks: Dict[str, PullTask] = {}
        self._queue: asyncio.Queue = asyncio.Queue()
        self._worker: Optional[asyncio.Task] = None

    def enqueue(self, model: str) -> str:
        """提交一个拉取任务，返回 task_id（幂等合并：同名在跑/排队则复用现有任务）"""
        model = (model or "").strip()
        # 避免重复排队同一模型
        for t in self._tasks.values():
            if t.model == model and t.status in ("pending", "running"):
                return t.id
        tid = uuid.uuid4().hex[:12]
        task = PullTask(tid, model)
        self._tasks[tid] = task
        self.ensure_started()
        self._queue.put_nowait(tid)
        return tid

    def ensure_started(self):
        if self._worker is None or self._worker.done():
            self._worker = asyncio.get_running_loop().create_task(self._worker_loop())

    def snapshot(self, task_id: str) -> Optional[dict]:
        t = self._tasks.get(task_id)
        return t.snapshot() if t else None

    def list_tasks(self, limit: int = 20) -> list:
        arr = [t.snapshot() for t in self._tasks.values()]
        arr.sort(key=lambda x: x["created_at"], reverse=True)
        return arr[: max(1, min(limit, 100))]

    async def _worker_loop(self):
        while True:
            tid = await self._queue.get()
            task = self._tasks.get(tid)
            if task is None:
                continue
            task.status = "running"
            task.progress = 0
            try:
                engine = self._engine_factory()
                async for ev in engine.pull_stream(task.model):
                    if ev.get("status") == "error":
                        task.status = "error"
                        task.error = str(ev.get("error", "未知错误"))[:500]
                        break
                    if ev.get("status") == "success":
                        task.status = "success"
                        task.progress = 100
                        break
                    # downloading 进度
                    total = ev.get("total") or 0
                    completed = ev.get("completed") or 0
                    if total and task.total != total:
                        task.total = total
                    task.downloaded = completed
                    if total > 0:
                        task.progress = min(99, round(completed / total * 100))
                else:
                    # 生成器正常走完但没成功/失败标志（理论不会），按断连处理
                    if task.status == "running":
                        task.status = "error"
                        task.error = "下载中断：未完成（重新拉取可自动续传）"
            except asyncio.CancelledError:
                task.status = "error"
                task.error = "任务被取消"
                raise
            except Exception as e:  # noqa: BLE001
                task.status = "error"
                task.error = str(e)[:500]
            finally:
                task.finished_at = time.time()