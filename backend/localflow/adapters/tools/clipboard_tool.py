"""剪贴板工具适配器 — 读 / 写系统剪贴板

- 写剪贴板：零风险，自动放行（不进入确认流程）。
- 读剪贴板：默认保守需确认（is_hazardous=True），避免在不经意间被偷读剪贴板。

跨平台实现：macOS 用 pbcopy/pbpaste，Windows 用 clip，Linux 用 xclip/xsel。
MVP 探测到可用命令即执行；找不到对应工具返回明确错误。
"""

from __future__ import annotations

import platform
import shutil
import subprocess
from typing import List, Optional

from ...ports.tool import Tool, ToolContext, ToolResult


def _platform_cmds() -> List[str]:
    """按平台返回 [复制命令, 粘贴命令]（命令名，交由 shutil.which 探测）"""
    os_name = platform.system().lower()
    if os_name.startswith("darwin"):
        return ["pbcopy", "pbpaste"]
    if os_name.startswith("win"):
        # Windows：复制用 clip；无对应粘贴读命令，用 powershell Get-Clipboard
        return ["clip", "powershell"]
    # Linux
    for c in ("xclip", "xsel"):
        if shutil.which(c):
            return [c, c]
    return ["", ""]


class ClipboardTool(Tool):
    name = "clipboard"
    description = "读写系统剪贴板。action=read 读取剪贴板文本；action=write 写入文本到剪贴板。"
    # read 需确认（防偷读）；write 是零风险自动放行
    is_hazardous = True
    input_schema = {
        "type": "object",
        "title": "ClipboardTool",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["read", "write"],
                "description": "read 读取剪贴板；write 写入文本到剪贴板",
            },
            "text": {
                "type": "string",
                "description": "仅 write 动作需要：要写入剪贴板的文本",
            },
        },
        "required": ["action"],
    }

    def __init__(self) -> None:
        self.copy_cmd, self.paste_cmd = _platform_cmds()

    async def run(
        self,
        params: dict,
        ctx: Optional[ToolContext] = None,
    ) -> ToolResult:
        action = (params.get("action") or "").lower()

        if action == "read":
            if not self.paste_cmd:
                return ToolResult(ok=False, error="当前系统无可用剪贴板读取命令")
            # 读剪贴板：保守需确认，避免无声偷读
            return ToolResult(
                ok=False,
                output="待确认后读取剪贴板",
                require_confirm=True,
                confirm_token="clipboard:read",
                data={"action": "read"},
            )

        if action == "write":
            text = str(params.get("text") or "")
            return self._write(text)

        return ToolResult(ok=False, error=f"未知 action：{action}")

    async def confirm_run(
        self,
        confirm_token: str,
        params: dict,
        ctx: Optional[ToolContext] = None,
    ) -> ToolResult:
        if confirm_token == "clipboard:read":
            return self._read()
        return self._write(str(params.get("text") or ""))

    # ---- 实现 ----

    def _read(self) -> ToolResult:
        try:
            out = subprocess.run(
                [self.paste_cmd], capture_output=True, text=True, timeout=8
            )
        except FileNotFoundError:
            return ToolResult(ok=False, error=f"命令不存在：{self.paste_cmd}")
        except subprocess.TimeoutExpired:
            return ToolResult(ok=False, error="读取剪贴板超时")
        if out.returncode != 0:
            return ToolResult(ok=False, error=f"读取失败 退出码{out.returncode}")
        return ToolResult(ok=True, output=out.stdout, data={"content": out.stdout})

    def _write(self, text: str) -> ToolResult:
        if platform.system().lower() == "windows":
            if not shutil.which("powershell"):
                return ToolResult(ok=False, error="当前系统无可用剪贴板写入命令")
            cmd = ["powershell", "-NoProfile", "-Command",
                   "$t = [Console]::In.ReadToEnd(); Set-Clipboard [string]$t"]
        else:
            if not self.copy_cmd:
                return ToolResult(ok=False, error="当前系统无可用剪贴板写入命令")
            cmd = [self.copy_cmd]
        try:
            proc = subprocess.run(
                cmd,
                input=text,
                capture_output=True,
                text=True,
                timeout=8,
            )
        except FileNotFoundError:
            return ToolResult(ok=False, error=f"命令不存在：{cmd[0]}")
        except subprocess.TimeoutExpired:
            return ToolResult(ok=False, error="写入剪贴板超时")
        if proc.returncode != 0:
            return ToolResult(ok=False, error=f"写入失败 退出码{proc.returncode}")
        return ToolResult(ok=True, output=f"已写入剪贴板 {len(text)} 字符")