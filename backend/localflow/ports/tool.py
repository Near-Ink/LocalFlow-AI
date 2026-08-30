"""工具接口 — Port

Agent 动手做事的统一入口。每个工具实现此接口，经 ToolRegistry 登记后
可被 agent 运行时调用。第三方工具同样以插件形式注册到同一端口，
天然落在「可插拔 + feature flag」的既有机制里。

阶段 0 只定义契约与注册表，具体工具适配器（file / shell / clipboard）
在阶段 1 落地于 adapters/tools/。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ToolResult:
    """工具执行结果（稳定 schema）"""
    ok: bool                              # 是否成功
    output: str = ""                      # 人类可读输出
    data: Any = None                      # 结构化数据（可选）
    error: Optional[str] = None
    require_confirm: bool = False         # 动作是否进入待确认流程（衍生自 is_hazardous）
    confirm_token: Optional[str] = None   # 待确认令牌，用户批准后凭此执行


@dataclass
class ToolContext:
    """工具执行上下文：由 agent 运行时装配并注入"""
    allow_dirs: List[str] = field(default_factory=list)  # 路径边界允许目录
    sandbox: Any = None                   # 沙箱执行器（阶段 1 落地）
    session_id: str = ""
    event_store: Any = None               # 事件存储（可回溯写入）
    extras: dict = field(default_factory=dict)


class Tool(ABC):
    """工具 Port — 所有本地工具的统一接口"""

    # 类属性由各适配器覆写，不需实例化时重复赋值
    name: str = "base"          # 工具名，如 "file" / "shell" / "clipboard"
    description: str = ""       # 对模型的自然语言描述（用于 tool-calling 提示）
    input_schema: dict = None   # 参数 JSON Schema（可选，用于构造 tools 清单）
    is_hazardous: bool = False  # 高危动作需进入「用户确认后再执行」流程

    @abstractmethod
    async def run(
        self,
        params: dict,
        ctx: Optional["ToolContext"] = None,
    ) -> ToolResult:
        """执行工具。

        高危工具应返回 require_confirm=True 与 confirm_token，
        交由用户确认后再走 confirm_run，而不是直接执行。
        """
        ...

    async def confirm_run(
        self,
        confirm_token: str,
        params: dict,
        ctx: Optional["ToolContext"] = None,
    ) -> ToolResult:
        """用户批准后执行此前待确认的高危动作。默认等价于直接 run。"""
        return await self.run(params, ctx)


class ToolRegistry:
    """工具注册表 — 集中登记可用工具，供 agent 构建 tools 清单"""

    def __init__(self) -> None:
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if not tool.name:
            raise ValueError("工具 name 不能为空")
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def list(self) -> List[Tool]:
        return list(self._tools.values())

    def schemas(self) -> List[dict]:
        """工具清单的 OpenAI 兼容嵌套结构（供生成 options.tools，引擎透传）"""
        result: List[dict] = []
        for t in self._tools.values():
            fn: dict = {"name": t.name, "description": t.description}
            if t.input_schema:
                fn["parameters"] = t.input_schema
            result.append({"type": "function", "function": fn})
        return result

    def __contains__(self, name: str) -> bool:
        return name in self._tools