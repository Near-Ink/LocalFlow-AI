"""LangGraph 子任务调度器适配器 — 骨架实现

MVP 阶段先提供一个简化版：用 LLM 拆分子任务，串行执行。
后续用 LangGraph 的图执行引擎增强并行/条件分支。

注：当前为骨架版本，LangGraph 作为依赖在进阶阶段深度集成。
"""

from __future__ import annotations

import uuid
from typing import Callable, List, Optional

from ..ports.engine import ChatMessage, GenerateOptions, LLMEngine
from ..ports.scheduler import SubTask, SubTaskResult, TaskScheduler


SPLIT_PROMPT = """你是一个任务拆解专家。请将用户的任务拆分为若干独立可执行的子任务。
要求：
1. 每个子任务用一句话描述，足够具体可独立执行
2. 子任务之间尽量解耦，可并行执行
3. 输出 JSON 格式：{{"subtasks": [{{"id": "...", "description": "..."}}, ...]}}
4. 子任务数量控制在 2-5 个

任务：{task}
"""


class LangGraphScheduler(TaskScheduler):
    """基于 LangGraph 的子任务调度器（MVP 简化版）"""

    name = "langgraph"

    def __init__(self, engine: LLMEngine):
        self.engine = engine

    async def split_and_run(
        self,
        task: str,
        model: str,
        on_progress: Optional[Callable[[SubTask], None]] = None,
    ) -> List[SubTaskResult]:
        # 1. 让 LLM 拆分子任务
        prompt = SPLIT_PROMPT.format(task=task)
        resp = await self.engine.chat(
            model=model,
            messages=[ChatMessage(role="user", content=prompt)],
            options=GenerateOptions(temperature=0.3, max_tokens=1024),
        )

        subtasks = self._parse_subtasks(resp.content)
        if not subtasks:
            # 解析失败则当作单子任务执行
            subtasks = [SubTask(id=str(uuid.uuid4())[:8], description=task)]

        results = []
        for st in subtasks:
            st.status = "running"
            if on_progress:
                on_progress(st)
            result = await self.run_single(st)
            results.append(result)
            st.status = result.status
            if on_progress:
                on_progress(st)

        return results

    async def run_single(self, subtask: SubTask) -> SubTaskResult:
        try:
            model = subtask.assigned_model or "qwen2.5:7b"
            resp = await self.engine.chat(
                model=model,
                messages=[ChatMessage(role="user", content=subtask.description)],
            )
            return SubTaskResult(
                task_id=subtask.id,
                status="completed",
                output=resp.content,
                usage=resp.usage,
            )
        except Exception as e:
            return SubTaskResult(
                task_id=subtask.id,
                status="failed",
                error=str(e),
            )

    # --- helpers ---

    def _parse_subtasks(self, content: str) -> List[SubTask]:
        import json
        import re
        # 尝试提取 JSON
        match = re.search(r"\{[\s\S]*\}", content)
        if not match:
            return []
        try:
            data = json.loads(match.group())
            items = data.get("subtasks", [])
            result = []
            for item in items:
                result.append(SubTask(
                    id=item.get("id") or str(uuid.uuid4())[:8],
                    description=item.get("description", ""),
                ))
            return result
        except Exception:
            return []