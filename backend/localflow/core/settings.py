"""应用设置注册表 — 让 Agent（与任何调用方）统一读写 LocalFlow 设置

设计要点（可扩展性）：
- 新增功能只需  register 一个 Setting 到 SettingsRegistry，
  Agent 的 get_setting / set_setting 即自动获得该设置的查询与修改能力，
  无需再为每个字段硬编码特殊逻辑。
- setter 返回 (ok, msg)；persist=True 时 apply 成功后自动落盘到 config.json。
- 只读设置（getter 有、setter 无）仅可查询；Agent 修改会如实告知只读。

分组 group 用于归类显示：模型/推理、云服务、运行/安全、外观/体验、其他。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional


@dataclass
class Setting:
    key: str
    group: str
    name: str
    desc: str
    type: str = "string"                 # string / int / float / bool / select
    options: Optional[list] = None       # type=select 时的合法取值
    persist: bool = False                # 修改后是否落盘持久化
    getter: Optional[Callable] = None    # (app) -> Any；缺省则 value=None
    setter: Optional[Callable] = None    # (app, value) -> (ok: bool, msg: str)


class SettingsRegistry:
    """可扩展设置注册表。self.app 为 LocalFlowApp 容器。"""

    def __init__(self, app) -> None:
        self.app = app
        self._items: dict = {}

    def register(self, s: Setting) -> None:
        self._items[s.key] = s

    def keys(self) -> List[str]:
        return list(self._items.keys())

    def get(self, key: str) -> Optional[dict]:
        s = self._items.get(key)
        if s is None:
            return None
        try:
            val = s.getter(self.app) if s.getter else None
        except Exception as e:  # getter 异常不阻塞查询
            val = f"<读取失败：{e}>"
        return {
            "key": s.key, "group": s.group, "name": s.name, "desc": s.desc,
            "type": s.type, "options": s.options, "persist": s.persist,
            "writable": s.setter is not None, "value": val,
        }

    def all(self) -> List[dict]:
        return [self.get(k) for k in self._items]

    def apply(self, key: str, value: Any):
        s = self._items.get(key)
        if s is None:
            return False, f"未知设置 key：{key}。可用：{', '.join(self._items) or '（空）'}"
        if s.setter is None:
            return False, f"设置 {key} 为只读，仅可查询不可修改"
        try:
            ok, msg = s.setter(self.app, value)
        except Exception as e:
            return False, f"修改 {key} 失败：{e}"
        if ok and s.persist:
            try:
                self.app._save_runtime_config()
            except Exception as e:
                return True, f"{msg or '已修改'}（但持久化失败：{e}）"
        return ok, msg


# ---------------------------------------------------------------------------
# 内置核心设置注册（模型/推理 + 云端 + 运行/安全）。新增功能在此追加注册即可。
# ---------------------------------------------------------------------------

def _reg_default_model(app) -> Optional[dict]:
    return app.config.default_model


def _set_default_model(app, value) -> tuple:
    v = str(value or "").strip()
    if not v:
        return False, "default_model 不能为空"
    app.config.default_model = v
    return True, f"默认模型已更新为：{v}"


def _reg_enable_cache(app) -> Optional[dict]:
    return bool(app.config.enable_cache)


def _set_enable_cache(app, value) -> tuple:
    v = bool(value)
    if v == bool(app.config.enable_cache):
        return True, f"缓存开关已为 {v}，无需变更"
    app.config.enable_cache = v
    # 现场重建缓存对象，热生效
    if v:
        from ..adapters.sqlite_cache import SQLiteCache
        app.cache = SQLiteCache(db_path=app.config.data_dir / "cache.db")
    else:
        app.cache = None
    # 同步给 Ollama 引擎，使其 chat 结果可被缓存/失效
    if getattr(app, "local_engine", None) is not None:
        try:
            app.local_engine.cache = app.cache
        except Exception:
            pass
    return True, f"缓存已{'开启' if v else '关闭'}"


def _reg_enable_agent(app) -> Optional[dict]:
    return bool(app.config.enable_agent)


def _set_enable_agent(app, value) -> tuple:
    v = bool(value)
    app.config.enable_agent = v
    return True, f"Agent 开关已设为 {v}（注：运行时对象是否装配仍取决于启动时配置，彻底变更需重启后生效的报告将如实说明）"


def _reg_engine(app) -> Optional[dict]:
    if app.cloud_engine is not None and app.engine is app.cloud_engine:
        b = app._active_binding() or {}
        return {"type": "cloud", "provider": b.get("provider", ""),
                "model": b.get("model", ""), "binding_id": b.get("id", "")}
    return {"type": "local", "model": app.config.default_model}


def _reg_cloud_bindings(app) -> Optional[dict]:
    return [{
        "id": b.get("id"), "provider": b.get("provider", ""),
        "base_url": b.get("base_url"), "model": b.get("model"),
        "context_size": b.get("context_size", 0),
        "active": b.get("id") == app.config.cloud_active_id,
    } for b in app.config.cloud_bindings]


def _reg_version(app) -> Optional[dict]:
    return getattr(app, "version", "0.2.0")


def _reg_api_key(app) -> Optional[dict]:
    key = (app.config.api_key or "").strip()
    return {"set": bool(key), "has_secret": True}


def _set_api_key(app, value) -> tuple:
    v = str(value or "").strip()
    if v and len(v) < 8:
        return False, "API Key 至少 8 位"
    app.config.api_key = v
    return True, ("已设置对外 API 密钥" if v else "已清除对外 API 密钥（本机免鉴权）")


def _reg_identity_inject(app) -> Optional[dict]:
    return bool(app.config.openai_identity_inject)


def _set_identity_inject(app, value) -> tuple:
    v = bool(value)
    if v == bool(app.config.openai_identity_inject):
        return True, f"模型身份注入已为 {v}，无需变更"
    app.config.openai_identity_inject = v
    return True, f"模型身份注入已{'开启' if v else '关闭'}"


def register_core_settings(app) -> SettingsRegistry:
    reg = SettingsRegistry(app)
    reg.register(Setting(
        key="default_model", group="模型/推理", name="默认模型",
        desc="新会话默认使用的本地模型名（如 llama3.1:8b、qwen2.5vl:7b）",
        type="string", persist=True, getter=_reg_default_model, setter=_set_default_model,
    ))
    reg.register(Setting(
        key="enable_cache", group="模型/推理", name="双层缓存开关",
        desc="是否启用会话缓存（开启可显著降低重复提问的 Token 消耗）",
        type="bool", persist=True, getter=_reg_enable_cache, setter=_set_enable_cache,
    ))
    reg.register(Setting(
        key="enable_agent", group="运行/安全", name="Agent 总开关",
        desc="是否启用本地 Agent（近期发布再默认开启，当前为灰度开关）",
        type="bool", persist=True, getter=_reg_enable_agent, setter=_set_enable_agent,
    ))
    reg.register(Setting(
        key="openai_api_key", group="运行/安全", name="对外 AI 密钥",
        desc="其他应用调用本机 AI 时需要携带的 Bearer Key；留空则本机免鉴权",
        type="string", persist=True, getter=_reg_api_key, setter=_set_api_key,
    ))
    reg.register(Setting(
        key="openai_identity_inject", group="运行/安全", name="模型身份注入",
        desc="被外部经 OpenAI 兼容接口调用时，自动注入系统提示让模型自报「由 LocalFlow 提供 + 实际模型名」；关闭则不注入",
        type="bool", persist=True, getter=_reg_identity_inject, setter=_set_identity_inject,
    ))
    reg.register(Setting(
        key="engine", group="模型/推理", name="当前引擎",
        desc="当前使用的推理引擎：本地 Ollama 或绑定的云端 API（只读）",
        type="string", getter=_reg_engine,
    ))
    reg.register(Setting(
        key="cloud_bindings", group="云服务", name="云端 API 绑定",
        desc="已绑定的云端绑定点列表（含激活状态；修改云端绑定见 manage_cloud 工具）",
        type="list", getter=_reg_cloud_bindings,
    ))
    reg.register(Setting(
        key="version", group="运行/安全", name="应用版本",
        desc="LocalFlow AI 版本号（只读）", type="string", getter=_reg_version,
    ))
    return reg