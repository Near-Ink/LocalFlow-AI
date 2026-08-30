"""云端 API 绑定管理"""

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional

from ..deps import get_app
from ..ports.engine import ChatMessage, GenerateOptions
from ..adapters.openai_engine import fetch_remote_models


router = APIRouter(prefix="/api/cloud", tags=["cloud"])


# 内置品牌清单（随包分发）；运行时可用 data_dir/cloud_catalog.json 自定义覆盖
_BUILTIN_CATALOG = Path(__file__).resolve().parent.parent / "cloud_catalog.json"


def _load_catalog(data_dir: Path) -> dict:
    """读取品牌清单：内置兜底，data_dir 同名文件覆盖（用户/脚本可联网更新）"""
    brands: dict = {}
    try:
        if _BUILTIN_CATALOG.exists():
            brands.update(json.loads(_BUILTIN_CATALOG.read_text(encoding="utf-8")).get("brands", {}))
    except Exception:
        pass
    override = Path(data_dir) / "cloud_catalog.json"
    try:
        if override.exists():
            brands.update(json.loads(override.read_text(encoding="utf-8")).get("brands", {}))
    except Exception:
        pass
    return brands


class BindReq(BaseModel):
    base_url: str
    api_key: str
    model: str
    activate: bool = True
    context_size: int = 0
    provider: str = ""


class UnbindReq(BaseModel):
    binding_id: Optional[str] = None   # 缺省解绑当前激活


class SwitchReq(BaseModel):
    target: str  # 'local' | 'cloud' | 具体绑定 id


@router.get("/status")
async def status(app=Depends(get_app)):
    """当前云端绑定与激活状态"""
    return app.cloud_status()


@router.get("/catalog")
async def catalog(app=Depends(get_app)):
    """可用云端品牌与模型清单（实时读盘，支持 data_dir 覆盖）"""
    brands = _load_catalog(app.config.data_dir)
    return {
        "brands": brands,
        "source": "builtin+local",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/catalog/reload")
async def reload_catalog(app=Depends(get_app)):
    """重新加载品牌清单（外部更新 data_dir/cloud_catalog.json 后手动触发）"""
    brands = _load_catalog(app.config.data_dir)
    return {"ok": True, "count": len(brands), "updated_at": datetime.now(timezone.utc).isoformat()}


@router.post("/list-models")
async def list_models(req: BindReq, app=Depends(get_app)):
    """用所填 API Key 调官方 GET /models，返回该账号实际可用的模型 id。

    用于绑定前「一键拉取真实可用模型」，替代静态目录，保证选择与可调用一致。
    失败时按原因分类返回：auth(Key 鉴权未通过) / unsupported(服务不支持) / error(其他)。
    """
    def _classify(exc: Exception):
        resp = getattr(exc, "response", None)
        code = getattr(resp, "status_code", None)
        if code in (401, 403):
            return "auth", f"API Key 未通过该服务鉴权（HTTP {code}）：请核对 Key 是否有效、是否与所选服务匹配、账户是否可用。并对 chat/completions 需要单独校验。"
        if code in (404, 405):
            return "unsupported", f"该服务不支持在线拉取模型（HTTP {code}），你可手动在模型框输入官方型号后继续。"
        return "error", f"拉取模型失败：{exc}"
    try:
        ids = await fetch_remote_models(req.base_url, req.api_key)
    except Exception as e:  # noqa: BLE001
        reason, msg = _classify(e)
        return {"ok": False, "reason": reason, "error": msg, "models": []}
    return {"ok": True, "models": ids, "count": len(ids)}


@router.post("/bind")
async def bind(req: BindReq, app=Depends(get_app)):
    """绑定云端 API（可多绑定共存）；可选做连通性探测并激活"""
    result = app.bind_cloud(
        req.base_url, req.api_key, req.model, activate=req.activate,
        context_size=req.context_size, provider=req.provider,
    )
    if not result["ok"]:
        return result
    # 对「本次绑定」做连通性探测（用临时引擎，不改变当前激活态）
    from ..adapters.openai_engine import OpenAIEngine
    tmp = OpenAIEngine(base_url=req.base_url, api_key=req.api_key, default_model=req.model)
    try:
        await tmp.chat(
            model=req.model,
            messages=[ChatMessage(role="user", content="ping")],
            options=GenerateOptions(max_tokens=1),
        )
        note = ("已保存并激活为当前云端。" if req.activate else "已保存，当前引擎保持。") + " 连通性检测通过。"
    except Exception as e:
        tail = '（当前已切云端，如不可用请切回本地或更换 Key）' if req.activate else '（当前引擎保持本地）'
        note = f'已保存，但连通性检测未通过：{e} {tail}'
    return {"ok": True, "id": result["id"], "note": note, "config": app.cloud_status()}


@router.post("/unbind")
async def unbind(req: UnbindReq, app=Depends(get_app)):
    """解绑云端（可指定绑定 id，缺省解绑当前激活）"""
    app.unbind_cloud(req.binding_id)
    return {"ok": True, "config": app.cloud_status()}


@router.post("/switch")
async def switch(req: SwitchReq, app=Depends(get_app)):
    """切换本地/云端引擎"""
    ok = app.switch_engine(req.target)
    return {"ok": ok, "config": app.cloud_status()}