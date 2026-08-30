"""工作流 API — 供可视化画布前后端调用"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

import json
import re
import time
from pathlib import Path

from ..core.workflow import WorkflowEngine, WorkflowError
from ..core.workflow_market import to_user_template
from ..core.workflow_sources import build_sources, fetch_source, fetch_template
from ..deps import get_app
from ..ports.tool import ToolContext

router = APIRouter(prefix="/api/workflow", tags=["workflow"])


class WfNode(BaseModel):
    id: str
    type: str = "tool"                 # tool | llm
    name: str = ""                     # 工具名（tool）或模型名（llm）
    params: Dict[str, Any] = {}
    x: Optional[int] = None            # 画布坐标
    y: Optional[int] = None


class WfEdge(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    from_: str = Field(alias="from")
    to: str


class WfRunReq(BaseModel):
    name: str = "未命名工作流"
    nodes: List[WfNode] = []
    edges: List[WfEdge] = []


class WfNodeResult(BaseModel):
    ok: bool
    node_type: str
    name: str
    status: str = "done"              # done | need_confirm | error
    output: str = ""
    error: str = ""
    data: Any = None


class WfRunResp(BaseModel):
    ok: bool
    name: str
    order: List[str] = []
    results: Dict[str, WfNodeResult] = {}
    error: str = ""


class WfSaveReq(BaseModel):
    name: str
    description: str = ""
    nodes: List[WfNode] = []
    edges: List[WfEdge] = []
    overwrite: bool = False


def _sanitize_template_name(name: str) -> str:
    """清洗模板名 → 安全的文件名基名，防止路径穿越。"""
    name = (name or "").strip()
    if not name:
        name = "未命名工作流"
    name = re.sub(r"[/\\]", "_", name).replace("..", "_")
    return name[:80]


def _workflows_dir(app) -> Path:
    d = Path(app.config.data_dir) / "workflows"
    d.mkdir(parents=True, exist_ok=True)
    return d


@router.get("/templates")
async def wf_templates(app=Depends(get_app)):
    """列出后端已保存的工作流模板"""
    items = []
    for p in sorted(_workflows_dir(app).glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        items.append({
            "name": p.stem,
            "description": data.get("description", ""),
            "node_count": len(data.get("nodes", [])),
            "edge_count": len(data.get("edges", [])),
            "updated": data.get("updated", ""),
        })
    items.sort(key=lambda i: i["updated"], reverse=True)
    return {"templates": items}


@router.delete("/template")
async def wf_template_delete(name: str, app=Depends(get_app)):
    """删除一个后端工作流模板（按名）"""
    safe = _sanitize_template_name(name)
    p = _workflows_dir(app) / f"{safe}.json"
    if not p.exists():
        return {"ok": False, "error": f"模板不存在：{name}"}
    p.unlink(missing_ok=True)
    tmp = _workflows_dir(app) / f"{safe}.json.tmp"
    tmp.unlink(missing_ok=True)
    return {"ok": True, "name": safe}


@router.get("/load")
async def wf_load(name: str, app=Depends(get_app)):
    """按名称加载一个模板（缺失返回 ok=false）"""
    safe = _sanitize_template_name(name)
    p = _workflows_dir(app) / f"{safe}.json"
    if not p.exists():
        return {"ok": False, "error": f"模板不存在：{name}"}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"ok": False, "error": "模板文件损坏"}
    return {"ok": True, "template": data}


@router.post("/save")
async def wf_save(req: WfSaveReq, app=Depends(get_app)):
    """保存/覆盖一个工作流模板到后端"""
    safe = _sanitize_template_name(req.name)
    p = _workflows_dir(app) / f"{safe}.json"
    if p.exists() and not req.overwrite:
        return {"ok": False, "error": f"模板「{safe}」已存在（如覆盖请带 overwrite）"}
    nodes = [{
        "id": n.id, "type": n.type, "name": n.name,
        "params": n.params or {},
        "x": int(n.x if n.x is not None else (n.params or {}).get("x", 30)),
        "y": int(n.y if n.y is not None else (n.params or {}).get("y", 30)),
    } for n in req.nodes]
    edges = [{"from": e.from_, "to": e.to} for e in req.edges]
    data = {
        "kind": "localflow-workflow",
        "version": 2,
        "name": safe,
        "description": req.description or "",
        "nodes": nodes,
        "edges": edges,
        "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.rename(p)
    return {"ok": True, "name": safe, "node_count": len(nodes), "edge_count": len(edges)}


def _ctx(app) -> ToolContext:
    dirs = [str(d) for d in app.sandbox.allow_dirs] if app.sandbox else []
    return ToolContext(
        allow_dirs=dirs,
        session_id="workflow",
        event_store=app.event_store,
        extras={"app": app},
    )


@router.get("/sources")
async def wf_sources(app=Depends(get_app)):
    """列出可用模板源（内置 + 已配置的远程社区源）"""
    return {"sources": build_sources(app.config.workflow_sources)}


def _find_source(app, source_id: str) -> Optional[dict]:
    return next((s for s in build_sources(app.config.workflow_sources) if s["id"] == source_id), None)


@router.get("/market/{source_id}")
async def wf_market(source_id: str, app=Depends(get_app)):
    """列出某模板源的分类与模板（builtin 本地；remote 从配置 URL 拉取）"""
    src = _find_source(app, source_id)
    if src is None:
        return {"ok": False, "error": f"模板源不存在：{source_id}。可用 /api/workflow/sources 查看"}
    try:
        data = await fetch_source(src)
    except Exception as e:
        return {"ok": False, "error": f"拉取模板源「{src['name']}」失败：{e}"}
    data["ok"] = True
    data["source_id"] = source_id
    data["source_name"] = src["name"]
    return data


@router.get("/market/{source_id}/{tpl_id}")
async def wf_market_one(source_id: str, tpl_id: str, app=Depends(get_app)):
    """取某源某模板的完整定义（供画布即时载入预览，不写入用户库）"""
    src = _find_source(app, source_id)
    if src is None:
        return {"ok": False, "error": f"模板源不存在：{source_id}"}
    try:
        tpl = await fetch_template(src, tpl_id)
    except Exception as e:
        return {"ok": False, "error": f"获取模板失败：{e}"}
    if tpl is None:
        return {"ok": False, "error": f"模板源「{source_id}」不存在模板：{tpl_id}"}
    return {"ok": True, "template": tpl}


class WfInstallReq(BaseModel):
    id: str
    source: str = "builtin"
    overwrite: bool = False
    description: str = ""


@router.post("/market/install")
async def wf_market_install(req: WfInstallReq, app=Depends(get_app)):
    """把某模板源的一个模板安装到用户的后端模板库"""
    src = _find_source(app, req.source)
    if src is None:
        return {"ok": False, "error": f"模板源不存在：{req.source}"}
    try:
        tpl = await fetch_template(src, req.id)
    except Exception as e:
        return {"ok": False, "error": f"获取模板失败：{e}"}
    if tpl is None:
        return {"ok": False, "error": f"模板源「{req.source}」不存在模板：{req.id}"}
    safe = _sanitize_template_name(req.id)
    p = _workflows_dir(app) / f"{safe}.json"
    if p.exists() and not req.overwrite:
        return {"ok": False, "error": f"模板「{safe}」已存在于你的模板库（安装同名请带 overwrite）", "installed_to": safe}
    user = to_user_template(tpl, req.description or None)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(user, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.rename(p)
    return {"ok": True, "installed_to": safe, "node_count": len(user["nodes"]), "edge_count": len(user["edges"])}


@router.get("/tools")
async def wf_tools(app=Depends(get_app)):
    """列出画布可用的工具节点能力"""
    tools = []
    for t in app.tool_registry.list():
        tools.append({
            "name": t.name,
            "description": t.description,
            "is_hazardous": bool(t.is_hazardous),
            "params_schema": t.input_schema or {"type": "object", "properties": {}},
        })
    return {"tools": tools}


@router.post("/run", response_model=WfRunResp)
async def wf_run(req: WfRunReq, app=Depends(get_app)):
    engine = WorkflowEngine(app)
    nodes = [{"id": n.id, "type": n.type, "name": n.name, "params": n.params} for n in req.nodes]
    edges = [{"from": e.from_, "to": e.to} for e in req.edges]
    try:
        results, order = await engine.execute(nodes, edges, ctx=_ctx(app))
    except WorkflowError as e:
        return WfRunResp(ok=False, name=req.name, error=e.message)
    return WfRunResp(
        ok=True, name=req.name, order=order,
        results={k: WfNodeResult(**v) for k, v in results.items()},
    )