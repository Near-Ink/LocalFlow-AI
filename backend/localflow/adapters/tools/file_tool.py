"""文件工具适配器 — 列目录 / 读 / 写

限定在用户声明的允许目录内；路径越界、``../``、符号链接逃逸由 Sandbox 拦截。
- list（列目录）：默认一层，不递归隐藏目录。
- read（读文本）：限制大小并截断，防止一次性读入超大文件。
- write（写文件）：高危动作，is_hazardous=True，须用户确认后执行。
"""

from __future__ import annotations

import os
from typing import Optional

from ...ports.tool import Tool, ToolContext, ToolResult
from ..sandbox import Sandbox
from ...core.artifacts import sniff

MAX_READ_BYTES = 200_000   # 单文件读取上限（截断）
DEFAULT_RECURSE = False    # 默认不递归，避免扫到隐藏目录


class FileTool(Tool):
    name = "file"
    description = (
        "读取、列出或写入本地文件。需传 action 参数与 path 绝对路径：\n"
        "- list: 列出 path 目录下的条目（不递归隐藏目录）\n"
        "- read: 读取 path 文本文件（超过 200KB 截断）\n"
        "- write: 写入内容到 path（覆盖式，需用户确认）"
    )
    input_schema = {
        "type": "object",
        "title": "FileTool",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "read", "write"],
                "description": "list 列目录；read 读文件；write 写文件(须确认)",
            },
            "path": {
                "type": "string",
                "description": "目标文件/目录的绝对路径；write 时目标内容由 content 提供",
            },
            "content": {
                "type": "string",
                "description": "仅 write 动作需要：要写入文件的文本内容",
            },
        },
        "required": ["action", "path"],
    }
    is_hazardous = True  # 写操作是高危，统一走确认流程

    def __init__(self, sandbox: Sandbox) -> None:
        self.sandbox = sandbox

    async def run(
        self,
        params: dict,
        ctx: Optional[ToolContext] = None,
    ) -> ToolResult:
        action = (params.get("action") or "list").lower()
        path = str(params.get("path") or "").strip()
        resolved = self.sandbox.resolve_allowed(path)
        if resolved is None:
            return ToolResult(ok=False, error=f"路径越界或不在允许目录内：{path}")

        if action == "write":
            # 写是高危：不直接写，返回待确认令牌
            return self._propose_write(params, resolved)
        if action == "read":
            return self._read(resolved)
        if action == "list":
            return self._list(resolved, params.get("recurse", DEFAULT_RECURSE))
        return ToolResult(ok=False, error=f"未知 action：{action}")

    # ---- 写：进入确认流程 ----

    def _propose_write(self, params: dict, resolved: object) -> ToolResult:
        return ToolResult(
            ok=False,
            output=f"待用户确认后才会写入：{resolved}",
            require_confirm=True,
            confirm_token=f"file:write:{resolved}",
            data={"tool": self.name, "action": "write", "path": str(resolved), "content": params.get("content", "")},
        )

    async def confirm_run(
        self,
        confirm_token: str,
        params: dict,
        ctx: Optional[ToolContext] = None,
    ) -> ToolResult:
        """用户批准后执行实际写入"""
        action = (params.get("action") or "write").lower()
        path = str(params.get("path") or "").strip()
        resolved = self.sandbox.resolve_allowed(path)
        if resolved is None:
            return ToolResult(ok=False, error=f"路径越界：{path}")
        if action != "write":
            return ToolResult(ok=False, error="仅支持确认写操作")
        content = params.get("content", "")
        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            with open(resolved, "w", encoding="utf-8") as f:
                f.write(content)
        except OSError as e:
            return ToolResult(ok=False, error=f"写入失败：{e}")
        data = {"path": str(resolved), "size": len(content)}
        art = sniff(resolved)
        if art:
            data["artifact"] = art
        return ToolResult(ok=True, output=f"已写入 {len(content)} 字符到 {resolved}", data=data)

    # ---- 读 ----

    def _read(self, resolved) -> ToolResult:
        try:
            raw = resolved.read_bytes()
        except OSError as e:
            return ToolResult(ok=False, error=f"读取失败：{e}")
        text = raw[:MAX_READ_BYTES].decode("utf-8", errors="replace")
        if len(raw) > MAX_READ_BYTES:
            text += "\n…(已截断)"
        data = {"path": str(resolved), "size": len(raw)}
        art = sniff(resolved)
        if art:
            art["preview"] = text[:4000]
            data["artifact"] = art
        return ToolResult(ok=True, output=text, data=data)

    # ---- 列目录 ----

    def _list(self, resolved, recurse: bool) -> ToolResult:
        try:
            entries = self._scandir(resolved, recurse=bool(recurse))
        except OSError as e:
            return ToolResult(ok=False, error=f"列目录失败：{e}")
        if not entries:
            return ToolResult(ok=True, output="（空目录）", data={"entries": []})
        lines = [f"{e['type']}\t{e['path']}" for e in entries]
        return ToolResult(
            ok=True,
            output="\n".join(lines),
            data={"entries": entries},
        )

    def _scandir(self, root, recurse: bool, _buf=None):
        """列出条目，默认过滤隐藏项；递归时也跳过隐藏目录"""
        if _buf is None:
            _buf = []
        try:
            names = sorted(os.listdir(root))
        except OSError:
            return _buf
        for n in names:
            if n.startswith("."):
                continue  # 隐藏项一律跳过
            p = os.path.join(root, n)
            is_dir = os.path.isdir(p)
            _buf.append({"name": n, "path": p, "type": "dir" if is_dir else "file"})
            if recurse and is_dir and not os.path.islink(p):
                self._scandir(p, recurse, _buf)
        return _buf