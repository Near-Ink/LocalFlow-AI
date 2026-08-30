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

# CORS（Electron 前端 + 开发用）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
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