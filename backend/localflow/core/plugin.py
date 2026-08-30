"""插件微内核 — Plugin Manager

设计：
- 所有扩展（工具、模型适配器、工作流节点）都通过插件加载
- 插件可以热加载 / 热卸载
- 每个插件有生命周期（install / enable / disable / uninstall）
- 插件通过 SPI 接口与内核交互，不直接访问内核内部

MVP 阶段提供基础插件加载，后续逐步增强热加载与隔离能力。
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional


class Plugin(ABC):
    """插件基类 — 所有插件继承此类"""

    name: str = "unnamed_plugin"
    version: str = "0.1.0"
    description: str = ""

    def __init__(self, app: Any = None):
        self.app = app  # LocalFlowApp 实例
        self._enabled = False

    @abstractmethod
    def install(self) -> None:
        """插件安装时调用（注册工具/适配器/节点等）"""
        ...

    def enable(self) -> None:
        """插件启用时调用"""
        self._enabled = True

    def disable(self) -> None:
        """插件禁用时调用"""
        self._enabled = False

    def uninstall(self) -> None:
        """插件卸载时调用（清理资源）"""
        ...

    @property
    def enabled(self) -> bool:
        return self._enabled


class PluginManager:
    """插件管理器"""

    def __init__(self, plugin_dir: str | Path = "plugins", app: Any = None):
        self.plugin_dir = Path(plugin_dir)
        self.app = app
        self._plugins: Dict[str, Plugin] = {}

    def load_plugins(self) -> List[str]:
        """扫描并加载所有插件，返回加载的插件名列表"""
        if not self.plugin_dir.exists():
            return []

        loaded = []
        for entry in sorted(self.plugin_dir.iterdir()):
            if entry.is_dir() and (entry / "__init__.py").exists():
                plugin_name = entry.name
            elif entry.is_file() and entry.suffix == ".py" and entry.stem != "__init__":
                plugin_name = entry.stem
            else:
                continue

            try:
                self._load_plugin(plugin_name, entry)
                loaded.append(plugin_name)
            except Exception as e:
                print(f"[Plugin] 加载失败 {plugin_name}: {e}")

        return loaded

    def _load_plugin(self, name: str, path: Path) -> None:
        # 动态导入
        spec = importlib.util.spec_from_file_location(
            f"plugins.{name}",
            path if path.is_file() else path / "__init__.py",
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"无法加载插件: {name}")

        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)

        # 查找插件类（继承自 Plugin 的类）
        plugin_cls = None
        for attr in dir(module):
            obj = getattr(module, attr)
            if (
                isinstance(obj, type)
                and issubclass(obj, Plugin)
                and obj is not Plugin
            ):
                plugin_cls = obj
                break

        if plugin_cls is None:
            raise ValueError(f"插件 {name} 未找到 Plugin 子类")

        plugin = plugin_cls(app=self.app)
        plugin.install()
        plugin.enable()
        self._plugins[name] = plugin

    def get(self, name: str) -> Optional[Plugin]:
        return self._plugins.get(name)

    def list_plugins(self) -> List[dict]:
        return [
            {
                "name": p.name,
                "version": p.version,
                "description": p.description,
                "enabled": p.enabled,
            }
            for p in self._plugins.values()
        ]

    def enable(self, name: str) -> bool:
        p = self._plugins.get(name)
        if p and not p.enabled:
            p.enable()
            return True
        return False

    def disable(self, name: str) -> bool:
        p = self._plugins.get(name)
        if p and p.enabled:
            p.disable()
            return True
        return False