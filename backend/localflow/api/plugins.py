"""插件管理 API"""

from fastapi import APIRouter, Depends
from typing import List

from ..deps import get_app


router = APIRouter(prefix="/api/plugins", tags=["plugins"])


@router.get("")
async def list_plugins(app=Depends(get_app)):
    """列出所有已加载插件"""
    return app.plugins.list_plugins()


@router.post("/{name}/enable")
async def enable_plugin(name: str, app=Depends(get_app)):
    """启用插件"""
    ok = app.plugins.enable(name)
    return {"success": ok}


@router.post("/{name}/disable")
async def disable_plugin(name: str, app=Depends(get_app)):
    """禁用插件"""
    ok = app.plugins.disable(name)
    return {"success": ok}