"""FastAPI 依赖注入"""

from .config import load_config
from .core.app import LocalFlowApp


# 全局 app 单例
_app: LocalFlowApp | None = None


def get_app() -> LocalFlowApp:
    """获取应用单例（FastAPI 依赖注入用）"""
    global _app
    if _app is None:
        config = load_config()
        _app = LocalFlowApp(config=config)
    return _app


async def startup_app():
    """启动钩子"""
    app = get_app()
    await app.startup()


async def shutdown_app():
    """关闭钩子"""
    global _app
    if _app:
        await _app.shutdown()
        _app = None