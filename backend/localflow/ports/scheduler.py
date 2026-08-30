"""子任务调度器接口 — Port

SubAgent 父子任务调度：主模型拆分任务，自动派发给子模型执行。
LangGraph 是默认实现（adapter），未来可替换。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, List, Optional


@dataclass
class SubTask:
    """子任务（稳定 schema）"""
    id: str = ""
    description: str = ""           # 子任务描述
    parent_id: Optional[str] = None  # 父任务 ID，None 表示根
    status: str = "pending"         # pending / running / completed / failed
    result: Optional[str] = None
    error: Optional[str] = None
    assigned_model: Optional[str] = None  # 分配的模型
    extras: dict = field(default_factory=dict)


@dataclass
class SubTaskResult:
    """子任务执行结果"""
    task_id: str
    status: str  # completed / failed
    output: str = ""
    error: Optional[str] = None
    usage: dict = field(default_factory=dict)


class TaskScheduler(ABC):
    """任务调度器 Port"""

    name: str = "base"

    @abstractmethod
    async def split_and_run(
        self,
        task: str,
        model: str,
        on_progress: Optional[Callable[[SubTask], None]] = None,
    ) -> List[SubTaskResult]:
        """拆分任务并并行执行

        Args:
            task: 原始任务描述
            model: 主模型（负责拆分）
            on_progress: 子任务进度回调

        Returns:
            所有子任务的执行结果列表
        """
        ...

    @abstractmethod
    async def run_single(self, subtask: SubTask) -> SubTaskResult:
        """执行单个子任务"""
        ...