"""日期时间工具适配器 — 获取当前日期与时间

只读、无高危动作，直接放行（is_hazardous=False）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from ...ports.tool import Tool, ToolContext, ToolResult


class DateTimeTool(Tool):
    name = "datetime"
    description = "获取当前日期与时间（本地时区）。无需参数。"
    is_hazardous = False

    async def run(
        self,
        params: dict,
        ctx: Optional[ToolContext] = None,
    ) -> ToolResult:
        now = datetime.now()
        return ToolResult(
            ok=True,
            output=f"{now.strftime('%Y-%m-%d %H:%M:%S %A')}（本地时区）",
            data={"iso": now.isoformat(timespec="seconds")},
        )