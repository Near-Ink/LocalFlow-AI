"""应用设置 API：读全部 / 改单个（复用 SettingsRegistry，与 Agent 工具同源）"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Any, Optional

from ..deps import get_app


router = APIRouter(prefix="/api/settings", tags=["settings"])


class InstallDirReq(BaseModel):
    install_dir: str


class SetSettingReq(BaseModel):
    key: str
    value: Any = None


@router.get("")
async def settings(app=Depends(get_app)):
    return {"version": app.version, "settings": app.settings.all()}


@router.get("/{key}")
async def get_one(key: str, app=Depends(get_app)):
    item = app.settings.get(key)
    if item is None:
        raise HTTPException(status_code=404, detail=f"未知设置 key：{key}")
    return item


@router.put("/{key}")
async def set_one(key: str, req: SetSettingReq, app=Depends(get_app)):
    if key != req.key:
        raise HTTPException(status_code=400, detail="路径 key 与请求体 key 不一致")
    ok, msg = app.settings.apply(key, req.value)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"ok": True, "key": key, "message": msg, "value": app.settings.get(key)["value"]}


@router.put("/install-dir")
async def update_install_dir(req: InstallDirReq, app=Depends(get_app)):
    return app.update_install_dir(req.install_dir)