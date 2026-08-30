"""设置工具适配器 — 让 Agent 读写并应用 LocalFlow 自身设置

- get_setting    查询一个或全部设置（只读，直接放行）
- set_setting    修改一个标量设置（高危：需用户确认后经 confirm_run 执行）
- manage_cloud   查看/激活/绑定/解绑云端 API（变更类动作同样高危需确认）

设置的具体字段与能力来自 core.settings.SettingsRegistry，新增设置只需
register 一个 Setting，这些工具即自动覆盖，无需改动本文件。
"""

from __future__ import annotations

import json
from typing import Optional

from ...ports.tool import Tool, ToolContext, ToolResult


def _app(ctx: Optional[ToolContext]):
    app = (ctx.extras or {}).get("app")
    if app is None:
        raise RuntimeError("缺少 app 引用（ToolContext.extras['app']）")
    return app


class SettingListTool(Tool):
    name = "get_setting"
    description = (
        "查询 LocalFlow 应用的设置信息。传 key 查单个设置；不传则列出全部设置 "
        "（含当前值、类型、说明、是否只读）。设置 key 如 default_model、enable_cache。"
    )
    is_hazardous = False
    input_schema = {
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "设置 key；省略则以 JSON 返回全部设置"}
        },
    }

    async def run(self, params: dict, ctx: Optional[ToolContext] = None) -> ToolResult:
        app = _app(ctx)
        reg = app.settings
        key = (params.get("key") or "").strip() or None
        if key:
            item = reg.get(key)
            if item is None:
                return ToolResult(ok=False, error=f"未知设置 key：{key}。可用：{', '.join(reg.keys())}")
            return ToolResult(ok=True, output=json.dumps(item, ensure_ascii=False, indent=2), data=item)
        items = reg.all()
        out = json.dumps(items, ensure_ascii=False, indent=2)
        return ToolResult(ok=True, output=f"LocalFlow 设置（共 {len(items)} 项）：\n{out}", data=items)


class SettingSetTool(Tool):
    name = "set_setting"
    description = (
        "修改 LocalFlow 应用的设置。参数 key 为设置名（如 default_model、enable_cache、enable_agent），"
        "value 为要设置的值（布尔用 true/false）。修改会持久化并尽量热生效。"
        "仅可修改可写设置；只读设置（如 engine、version）会被拒绝。"
        "此操作属敏感变更，需要用户确认后才会实际执行。"
    )
    is_hazardous = True
    input_schema = {
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "设置 key，如 default_model"},
            "value": {"description": "新的设置值（字符串 / 布尔 / 数字）"},
        },
        "required": ["key", "value"],
    }

    async def run(self, params: dict, ctx: Optional[ToolContext] = None) -> ToolResult:
        app = _app(ctx)
        key = (params.get("key") or "").strip()
        if not key:
            return ToolResult(ok=False, error="缺少参数 key")
        item = app.settings.get(key)
        if item is None:
            return ToolResult(ok=False, error=f"未知设置 key：{key}。可用：{', '.join(app.settings.keys())}")
        if not item["writable"]:
            return ToolResult(ok=False, error=f"设置 {key} 为只读，仅可查询")
        # 预览将进行的修改，待用户确认后真正执行
        return ToolResult(
            ok=True,
            require_confirm=True,
            confirm_token=self._token(),
            output=f"待确认：将把设置 {key} 修改为：{params.get('value')}。请确认后执行。",
            data={"key": key, "value": params.get("value")},
        )

    async def confirm_run(self, confirm_token: str, params: dict, ctx: Optional[ToolContext] = None) -> ToolResult:
        app = _app(ctx)
        key = (params.get("key") or "").strip()
        value = params.get("value")
        ok, msg = app.settings.apply(key, value)
        if ok:
            nv = app.settings.get(key)["value"] if app.settings.get(key) else value
            return ToolResult(ok=True, output=f"{msg}（新值：{nv}）")
        return ToolResult(ok=False, error=msg)

    def _token(self) -> str:
        import uuid
        return f"confirm_{uuid.uuid4().hex[:10]}"


class CloudManageTool(Tool):
    name = "manage_cloud"
    description = (
        "管理 LocalFlow 的云端 API 绑定。action 支持："
        "list（列出绑定）、activate（激活某个 binding_id）、"
        "bind（新增/更新绑定，需 base_url/api_key/model）、unbind（解绑指定 id）。"
        "除 list 外均为敏感变更，需用户确认后执行。"
    )
    is_hazardous = True
    input_schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "description": "list / activate / bind / unbind",
                       "enum": ["list", "activate", "bind", "unbind"]},
            "binding_id": {"type": "string", "description": "绑定 id（activate/unbind 用）"},
            "base_url": {"type": "string", "description": "bind 用：云端 API 地址"},
            "api_key": {"type": "string", "description": "bind 用：API 密钥"},
            "model": {"type": "string", "description": "bind 用：默认模型"},
            "provider": {"type": "string", "description": "bind 用：供应商名，如 deepseek / openai"},
            "context_size": {"type": "integer", "description": "bind 用：上下文大小"},
        },
        "required": ["action"],
    }

    async def run(self, params: dict, ctx: Optional[ToolContext] = None) -> ToolResult:
        app = _app(ctx)
        action = (params.get("action") or "").strip().lower()
        if action == "list":
            return self._list(app)
        # 变更类：需用户确认
        return ToolResult(
            ok=True, require_confirm=True, confirm_token=self._token(),
            output=f"待确认：将执行云端操作 {action}（参数：{json.dumps(params, ensure_ascii=False)}）。请确认后执行。",
            data=params,
        )

    async def confirm_run(self, confirm_token: str, params: dict, ctx: Optional[ToolContext] = None) -> ToolResult:
        app = _app(ctx)
        action = (params.get("action") or "").strip().lower()
        if action == "list":
            return self._list(app)
        if action == "activate":
            bid = (params.get("binding_id") or "").strip()
            ids = [b.get("id") for b in app.config.cloud_bindings]
            if bid not in ids:
                return ToolResult(ok=False, error=f"未找到绑定 id：{bid}。现有：{ids}")
            app.config.cloud_active_id = bid
            app._sync_active_cloud()
            app.switch_engine("cloud")
            return ToolResult(ok=True, output=f"已激活云端绑定：{bid}")
        if action == "bind":
            base_url = (params.get("base_url") or "").strip()
            api_key = (params.get("api_key") or "").strip()
            model = (params.get("model") or "").strip()
            if not (base_url and api_key and model):
                return ToolResult(ok=False, error="bind 需 base_url / api_key / model")
            provider = (params.get("provider") or "").strip()
            ctx_size = int(params.get("context_size") or 0)
            r = app.bind_cloud(base_url, api_key, model, activate=True,
                               context_size=ctx_size, provider=provider)
            return ToolResult(ok=bool(r.get("ok")), output=json.dumps(r, ensure_ascii=False),
                              error=r.get("error"))
        if action == "unbind":
            bid = (params.get("binding_id") or "").strip() or None
            app.unbind_cloud(binding_id=bid)
            return ToolResult(ok=True, output="已解绑指定云端绑定（若删光则回到本地）")
        return ToolResult(ok=False, error=f"未知 action：{action}（list/activate/bind/unbind）")

    def _lists(self, app) -> list:
        return [{
            "id": b.get("id"), "provider": b.get("provider", ""),
            "base_url": b.get("base_url"), "model": b.get("model"),
            "context_size": b.get("context_size", 0),
            "active": b.get("id") == app.config.cloud_active_id,
        } for b in app.config.cloud_bindings]

    def _list(self, app) -> ToolResult:
        items = self._lists(app)
        return ToolResult(ok=True, output=json.dumps(items, ensure_ascii=False, indent=2), data=items)

    def _token(self) -> str:
        import uuid
        return f"confirm_{uuid.uuid4().hex[:10]}"