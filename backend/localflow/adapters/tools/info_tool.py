"""环境信息工具适配器 — 返回系统与环境基本信息

只读、无高危动作，直接放行（is_hazardous=False）。
"""

from __future__ import annotations

import os
import platform
import sys
from datetime import datetime
from typing import Optional

from ...ports.tool import Tool, ToolContext, ToolResult


class InfoTool(Tool):
    name = "info"
    description = (
        "获取系统与环境基本信息：操作系统、Python 版本、当前工作目录、当前时间。"
        "无需参数。"
    )
    is_hazardous = False

    async def run(
        self,
        params: dict,
        ctx: Optional[ToolContext] = None,
    ) -> ToolResult:
        lines = [
            f"platform: {platform.system()} {platform.release()} ({platform.machine()})",
            f"python: {sys.version.split()[0]}",
            f"cwd: {os.getcwd()}",
            f"datetime: {datetime.now().isoformat(timespec='seconds')}",
        ]
        return ToolResult(ok=True, output="\n".join(lines), data={"cwd": os.getcwd()})