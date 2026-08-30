"""Shell 工具适配器 — 受限 shell

只允许白名单内的只读 / 打包 / 信息类命令，黑名单命令一律拒绝。
由 Sandbox 统一做命令治理、超时与输出截断。Shell 本身是高危动作，
统一走确认后执行流程。
"""

from __future__ import annotations

from typing import Optional

from ...ports.tool import Tool, ToolContext, ToolResult
from ..sandbox import Sandbox


class ShellTool(Tool):
    name = "shell"
    description = (
        "在受限沙箱中执行本地 shell 命令。只允许只读/打包类命令（如 ls、grep、"
        "find、cat、head、tail、tar、zip、date、wc、echo 等），删除/格式化/特权/网络/"
        "执行任意代码类命令一律拒绝。执行需用户确认。"
    )
    input_schema = {
        "type": "object",
        "title": "ShellTool",
        "properties": {
            "command": {
                "type": "string",
                "description": "要在受限沙箱执行的只读/打包类命令，如 'ls -la'、'date'、'grep 关键词 文件路径'",
            },
        },
        "required": ["command"],
    }
    is_hazardous = True  # shell 一律需确认

    def __init__(self, sandbox: Sandbox) -> None:
        self.sandbox = sandbox

    async def run(
        self,
        params: dict,
        ctx: Optional[ToolContext] = None,
    ) -> ToolResult:
        cmd = str(params.get("command") or "").strip()
        if not cmd:
            return ToolResult(ok=False, error="缺少 command 参数")

        # 先做一次预检，把拒绝命令在你看到「确认框」之前就告诉你
        ok, reason, _ = self.sandbox.check_command(cmd)
        if not ok:
            return ToolResult(ok=False, error=reason or "命令被拒绝")

        cwd = params.get("cwd")
        if cwd and not self.sandbox.is_allowed_path(str(cwd)):
            return ToolResult(ok=False, error=f"工作目录越界：{cwd}")

        return ToolResult(
            ok=False,
            output=f"待确认后执行：{cmd}",
            require_confirm=True,
            confirm_token=f"shell:run:{cmd}",
            data={"command": cmd, "cwd": cwd or ""},
        )

    async def confirm_run(
        self,
        confirm_token: str,
        params: dict,
        ctx: Optional[ToolContext] = None,
    ) -> ToolResult:
        cmd = str(params.get("command") or "").strip()
        cwd = str(params.get("cwd") or "")
        return self.sandbox.run_command(cmd, cwd=cwd)