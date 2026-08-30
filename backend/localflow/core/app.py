"""应用主容器 — LocalFlowApp

负责装配所有 ports 的 adapter，是系统的唯一入口。
所有 adapter 通过依赖注入在此处装配，业务层（core/api）只依赖 ports。

新增 adapter 时只需改这里，不改业务代码。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..adapters.langgraph_scheduler import LangGraphScheduler
from ..adapters.ollama_engine import OllamaEngine
from ..adapters.openai_engine import OpenAIEngine
from ..adapters.react_agent import ReactAgent
from ..adapters.sandbox import Sandbox
from ..adapters.sqlite_cache import SQLiteCache
from ..adapters.sqlite_event import SQLiteEventStore
from ..adapters.system_hardware import SystemHardwareMonitor
from ..adapters.tools.clipboard_tool import ClipboardTool
from ..adapters.tools.datetime_tool import DateTimeTool
from ..adapters.tools.file_tool import FileTool
from ..adapters.tools.info_tool import InfoTool
from ..adapters.tools.shell_tool import ShellTool
from ..adapters.tools.settings_tool import CloudManageTool, SettingListTool, SettingSetTool
from ..adapters.tools.workflow_tool import ListWorkflowTool, RunWorkflowTool, SaveWorkflowTool
from ..ports.agent import AgentRuntime
from ..ports.cache import CacheEngine
from ..ports.engine import LLMEngine
from ..ports.event import EventStore
from ..ports.hardware import HardwareMonitor
from ..ports.scheduler import TaskScheduler
from ..ports.tool import ToolRegistry
from .plugin import PluginManager
from .session import SessionManager
from .wizard import DeploymentWizard


@dataclass
class AppConfig:
    """应用配置"""
    data_dir: Path = Path.home() / ".localflow"
    ollama_base_url: str = "http://localhost:11434"
    default_model: str = "llama3.1:8b"
    cloud_api_base: str = ""
    cloud_api_key: str = ""
    cloud_model: str = ""
    cloud_context_size: int = 0
    # 多云端绑定：可绑定多个 provider，一次仅激活其中一个。
    # 每项 {id, provider, base_url, api_key, model, context_size}
    cloud_bindings: list = field(default_factory=list)
    cloud_active_id: str = ""
    enable_cache: bool = True
    enable_agent: bool = False  # agent 功能灰度开关，默认关闭，验证充分后再默认开
    api_key: str = ""  # 对外 OpenAI 兼容 API 的 Bearer Key；空则不要求鉴权
    openai_identity_inject: bool = True  # 外部调用时自动注入模型身份系统提示，默认开
    plugin_dir: Path = Path("plugins")
    workflow_sources: list = field(default_factory=list)  # 远程模板源 [{id,name,url}]
    install_dir: str = ""
    config_file: str = ""


class LocalFlowApp:
    """LocalFlow 应用主容器

    所有核心组件在此装配，对外提供统一访问接口。
    """

    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or AppConfig()
        self.config.data_dir.mkdir(parents=True, exist_ok=True)
        self.version = "0.1.0"

        # --- Settings 注册表（Agent / API 统一读写应用设置）---
        from .settings import register_core_settings
        self.settings = register_core_settings(self)

        # --- Adapters 装配 ---
        # 本地推理引擎（默认 Ollama）
        self.local_engine: LLMEngine = OllamaEngine(base_url=self.config.ollama_base_url)

        # 云端引擎（有激活绑定才启用）
        self.cloud_engine: Optional[LLMEngine] = None
        bind = self._active_binding()
        if bind:
            self.cloud_engine = OpenAIEngine(
                base_url=bind["base_url"],
                api_key=bind["api_key"],
                default_model=bind["model"],
            )

        # 当前主引擎（默认本地，可切换）
        self.engine: LLMEngine = self.local_engine

        # 事件存储
        self.event_store: EventStore = SQLiteEventStore(
            db_path=self.config.data_dir / "events.db"
        )

        # 缓存
        self.cache: Optional[CacheEngine] = None
        if self.config.enable_cache:
            self.cache = SQLiteCache(
                db_path=self.config.data_dir / "cache.db"
            )

        # 硬件监控
        self.hardware: HardwareMonitor = SystemHardwareMonitor()

        # 子任务调度器
        self.scheduler: TaskScheduler = LangGraphScheduler(engine=self.engine)

        # --- Agent 功能（feature flag 灰度） ---
        # 阶段 0：装配工具注册表与 agent 槽位。阶段 1 注入 file/shell/clipboard
        # 工具，阶段 2 注入 react_agent 运行时。tools 与 agent 是否可用取决于
        # config.enable_agent，后续阶段在此装配面接续，不触碰既有链路。
        self.tool_registry: ToolRegistry = ToolRegistry()
        self.agent: Optional[AgentRuntime] = None
        self.sandbox: Optional[Sandbox] = None
        if self.config.enable_agent:
            # 安全边界：仅允许在应用数据目录内动手（路径边界）
            self.sandbox = Sandbox(allow_dirs=[str(self.config.data_dir)])
            self.tool_registry.register(FileTool(sandbox=self.sandbox))
            self.tool_registry.register(ShellTool(sandbox=self.sandbox))
            self.tool_registry.register(ClipboardTool())
            self.tool_registry.register(DateTimeTool())
            self.tool_registry.register(InfoTool())
            # 设置管理：Agent 可读写 LocalFlow 应用设置（改云端经 manage_cloud）
            self.tool_registry.register(SettingListTool())
            self.tool_registry.register(SettingSetTool())
            self.tool_registry.register(CloudManageTool())
            # 本地工作流：Agent 可把自然语言描述的工作流保存到后端模板库
            self.tool_registry.register(SaveWorkflowTool())
            self.tool_registry.register(ListWorkflowTool())
            self.tool_registry.register(RunWorkflowTool())
            # agent 始终经 get_engine 读取当前引擎，避免切换云端/本地后失效
            self.agent = ReactAgent(
                get_engine=lambda: self.engine,
                tool_registry=self.tool_registry,
                event_store=self.event_store,
            )

        # 会话管理器
        self.sessions = SessionManager(
            engine=self.engine,
            event_store=self.event_store,
            cache=self.cache,
        )

        # 部署向导
        self.wizard = DeploymentWizard(hardware_monitor=self.hardware)

        # 后台模型拉取任务（复用已配置的本地 Ollama 引擎地址，适配自定义 OLLAMA_HOST）
        from ..adapters.pull_tasks import PullTaskManager
        self.pull_tasks = PullTaskManager(
            engine_factory=lambda: self.local_engine
            if hasattr(self.local_engine, "pull_stream")
            else OllamaEngine(base_url=self.config.ollama_base_url)
        )

        # 插件管理器
        self.plugins = PluginManager(plugin_dir=self.config.plugin_dir, app=self)

    async def startup(self):
        """启动时初始化"""
        # 加载插件
        loaded = self.plugins.load_plugins()
        print(f"[LocalFlow] 已加载 {len(loaded)} 个插件: {loaded}")
        print(f"[LocalFlow] 本地引擎: {self.engine.name}")
        print(f"[LocalFlow] 数据目录: {self.config.data_dir}")

    async def shutdown(self):
        """关闭时清理"""
        pass

    def _active_binding(self) -> Optional[dict]:
        """返回当前激活的云端绑定（无则 None）。active_id 失效时回退第一个绑定。"""
        bs = self.config.cloud_bindings
        if not bs:
            return None
        for b in bs:
            if b.get("id") == self.config.cloud_active_id:
                return b
        # active_id 失效或为空 → 回退第一个，并固定下来
        first = bs[0]
        self.config.cloud_active_id = first.get("id", "")
        return first

    def _sync_active_cloud(self) -> Optional[LLMEngine]:
        """按当前激活绑定重建云端引擎并接入会话/Agent"""
        bind = self._active_binding()
        old_cloud = self.cloud_engine
        if not bind:
            self.cloud_engine = None
            if self.engine is not None and self.engine is old_cloud:
                self.switch_engine("local")
            return None
        self.cloud_engine = OpenAIEngine(
            base_url=bind["base_url"],
            api_key=bind["api_key"],
            default_model=bind["model"],
        )
        # 旧单套字段保持与激活绑定一致，兼容其余读取方
        self.config.cloud_api_base = bind["base_url"]
        self.config.cloud_api_key = bind["api_key"]
        self.config.cloud_model = bind["model"]
        self.config.cloud_context_size = int(bind.get("context_size") or 0)
        # 若引擎原本在云端，指向重建后的新对象，避免「engine is cloud_engine」失配
        if self.engine is old_cloud:
            self.engine = self.cloud_engine
            self.sessions.engine = self.engine
        return self.cloud_engine

    def switch_engine(self, target: str = "local") -> bool:
        """切换主引擎：local / cloud / 具体绑定 id"""
        if target == "local":
            self.engine = self.local_engine
            self.sessions.engine = self.engine
            return True
        if target == "cloud":
            bind = self._active_binding()
            if not bind:
                return False
        else:
            # 按绑定 id 切换云端
            if not any(b.get("id") == target for b in self.config.cloud_bindings):
                return False
            self.config.cloud_active_id = target
        self._sync_active_cloud()
        if self.cloud_engine:
            self.engine = self.cloud_engine
            self.sessions.engine = self.engine
            return True
        return False

    # --- 云端绑定管理 ---

    def _save_runtime_config(self):
        """持久化绑定与安装目录到基础 config.json"""
        import json
        cfg_file = Path(self.config.config_file) if self.config.config_file else (self.config.data_dir / "config.json")
        cfg_file.parent.mkdir(parents=True, exist_ok=True)
        # 合并已有配置，避免覆盖其他字段
        existing = {}
        if cfg_file.exists():
            try:
                existing = json.loads(cfg_file.read_text(encoding="utf-8"))
            except Exception:
                existing = {}
        existing.update({
            "cloud_bindings": self.config.cloud_bindings,
            "cloud_active_id": self.config.cloud_active_id,
            # 旧单套字段同步为激活绑定，兼容旧读取方
            "cloud_api_base": self.config.cloud_api_base,
            "cloud_api_key": self.config.cloud_api_key,
            "cloud_model": self.config.cloud_model,
            "cloud_context_size": self.config.cloud_context_size,
            "install_dir": self.config.install_dir,
            "default_model": self.config.default_model,
            "enable_cache": bool(self.config.enable_cache),
            "enable_agent": bool(self.config.enable_agent),
            "api_key": self.config.api_key or "",
            "openai_identity_inject": bool(self.config.openai_identity_inject),
        })
        cfg_file.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")

    def bind_cloud(self, base_url: str, api_key: str, model: str, activate: bool = True, context_size: int = 0, provider: str = "") -> dict:
        """绑定云端 API 到豪华列表并持久化；可激活为当前引擎。同 base+model 复用已有绑定。"""
        if not (base_url and api_key and model):
            return {"ok": False, "error": "base_url / api_key / model 均必填"}
        binding_id = None
        for b in self.config.cloud_bindings:
            if b.get("base_url") == base_url and b.get("model") == model:
                b["api_key"] = api_key
                b["context_size"] = max(0, int(context_size or 0))
                if provider:
                    b["provider"] = provider
                binding_id = b["id"]
                break
        if binding_id is None:
            import uuid
            binding_id = str(uuid.uuid4())
            self.config.cloud_bindings.append({
                "id": binding_id,
                "provider": provider or "",
                "base_url": base_url,
                "api_key": api_key,
                "model": model,
                "context_size": max(0, int(context_size or 0)),
            })
        self.config.cloud_active_id = binding_id if activate else self.config.cloud_active_id
        if activate:
            self._sync_active_cloud()
            self.switch_engine("cloud")
        else:
            self._save_runtime_config()
        return {"ok": True, "id": binding_id}

    def unbind_cloud(self, binding_id: Optional[str] = None) -> dict:
        """解绑指定云端绑定（缺省解绑当前激活）；删光则回到本地"""
        bs = self.config.cloud_bindings
        if not bs:
            return {"ok": True}
        active = self._active_binding()
        if binding_id is None:
            binding_id = active["id"] if active else bs[0]["id"]
        was_active = binding_id == self.config.cloud_active_id
        self.config.cloud_bindings = [b for b in bs if b.get("id") != binding_id]
        if was_active:
            self.config.cloud_active_id = ""
        self._sync_active_cloud()   # 重建(或清空)按剩余绑定的引擎
        self._save_runtime_config()
        if was_active:
            self.switch_engine("cloud" if self.cloud_engine else "local")
        return {"ok": True, "removed": binding_id}

    def cloud_status(self) -> dict:
        """当前云端绑定列表与激活状态（bindings 不含 API Key）"""
        bindings = [
            {k: b.get(k, "") for k in ("id", "provider", "base_url", "model", "context_size")}
            for b in self.config.cloud_bindings
        ]
        active_binding = self._active_binding()
        in_cloud = self.cloud_engine is not None and self.engine is self.cloud_engine
        return {
            "bound": bool(bindings),
            "provider": (active_binding or {}).get("base_url", "—") or "—",
            "base_url": (active_binding or {}).get("base_url", ""),
            "model": (active_binding or {}).get("model", ""),
            "context_size": int((active_binding or {}).get("context_size") or 0),
            "active": ("cloud" if in_cloud else "local"),
            "active_id": (active_binding or {}).get("id", ""),
            "count": len(bindings),
            "bindings": bindings,
        }

    # --- 安装目录 ---

    def update_install_dir(self, path: str) -> dict:
        """设置用户自选的部署/数据目录（重启后生效）；传空则恢复默认"""
        path = (path or "").strip()
        self.config.install_dir = path
        self._save_runtime_config()
        if not path:
            return {"ok": True, "note": "已恢复默认部署目录。", "install_dir": "", "hint": ""}
        models_dir = str(Path(path) / "models")
        return {
            "ok": True,
            "note": "已保存，重启 LocalFlow 后生效。数据将保存到 " + str(Path(path) / ".localflow"),
            "install_dir": path,
            "data_dir": str(Path(path) / ".localflow"),
            "models_dir": models_dir,
            "hint": "Ollama 模型将落在 models 目录，需用“OLLAMA_MODELS=" + models_dir + "”环境变量启动 Ollama 才会写入该处。",
        }

    @property
    def settings_status(self) -> dict:
        install = self.config.install_dir
        models_dir = str(Path(install) / "models") if install else "（默认 Ollama 位置）"
        return {
            "install_dir": install or "",
            "default_dir": str(Path.home() / ".localflow"),
            "data_dir": str(self.config.data_dir),
            "models_dir": models_dir,
        }

    def agent_status(self) -> dict:
        """当前 Agent 功能状态（feature flag 灰度可见）"""
        return {
            "enabled": self.config.enable_agent,
            "runtime": self.agent.name if self.agent else None,
            "tools": sorted(t.name for t in self.tool_registry.list()),
        }