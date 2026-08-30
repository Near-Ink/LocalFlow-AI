"""核心层 — 业务逻辑

core/ 依赖 ports（抽象），不直接依赖 adapters（实现）。
通过依赖注入在应用入口处装配具体 adapter。
"""

from .app import LocalFlowApp
from .plugin import PluginManager, Plugin
from .session import SessionManager
from .wizard import DeploymentWizard

__all__ = ["LocalFlowApp", "PluginManager", "Plugin", "SessionManager", "DeploymentWizard"]