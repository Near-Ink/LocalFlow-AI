"""FastAPI 应用入口

启动方式：
    uvicorn localflow.main:app --reload --port 8765
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __app_name__, __version__
from .api import agent, attach, cache, chat, cloud, context, dsh, hardware, models, openai, plugins, sessions, settings, wizard, workflow, workspace
from .deps import get_app


@asynccontextmanager
async def lifespan(app: FastAPI):
    """生命周期管理（FastAPI 0.93+ 推荐方式）"""
    # startup
    lf_app = get_app()
    await lf_app.startup()
    yield
    # shutdown
    await lf_app.shutdown()


app = FastAPI(
    title=f"{__app_name__} API",
    version=__version__,
    description="LocalFlow AI — 本地 AI 桌面平台后端 API",
    lifespan=lifespan,
)

# CORS：只允许本机可信源（Electron 渲染进程的 file://、本机回环）。
# 关键安全约束：跨站网页（如 https://evil.com）的 Origin 不匹配正则 → 被拒绝，
# 从而杜绝「任意网页驱动本机 Agent（文件/命令工具）」的攻击面。
# 仅用 Bearer Token 鉴权，不依赖 cookie，故不开启 allow_credentials。
# 仅放行本机可信源：file://（Electron 渲染进程，前缀匹配含完整路径）、null、
# 以及本机回环 127.0.0.1 / localhost。跨站网页（如 https://evil.com）不匹配 → 被拒绝。
_ALLOWED_ORIGIN_REGEX = (
    r"^(file://.*|null$|http://127\.0\.0\.1(:[0-9]+)?$|"
    r"http://localhost(:[0-9]+)?)$"
)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=_ALLOWED_ORIGIN_REGEX,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health():
    """服务健康检查"""
    return {"status": "ok", "version": __version__, "app": __app_name__}


# 路由注册
app.include_router(models.router)
app.include_router(chat.router)
app.include_router(attach.router)
app.include_router(agent.router)
app.include_router(cloud.router)
app.include_router(context.router)
app.include_router(hardware.router)
app.include_router(cache.router)
app.include_router(settings.router)
app.include_router(wizard.router)
app.include_router(sessions.router)
app.include_router(plugins.router)
app.include_router(workflow.router)
app.include_router(openai.router)
app.include_router(workspace.router)
app.include_router(dsh.router)