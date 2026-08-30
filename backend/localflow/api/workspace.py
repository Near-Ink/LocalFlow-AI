"""工作区（项目）API — 项目文件浏览 + 实时预览

- GET    /api/workspace/list               列出所有项目
- POST   /api/workspace                    新建项目（name + root 文件夹，需存在）
- DELETE /api/workspace/{project_id}       删除项目（仅移除记录，不删物理文件）
- GET    /api/workspace/{project_id}/tree           递归列出项目目录下所有文件
- GET    /api/workspace/{project_id}/artifact?path= 读取项目内某个文件用于预览

设计要点：
- 项目列表持久化到 config.data_dir/workspaces.json。
- 项目根目录由用户显式指定，读取权限以其为边界：任何越界 / 符号链接逃逸一律拒绝。
- 与 agent 的沙箱无关；这里是用户主动选择的工作文件夹，属明确授权范围。
"""

from __future__ import annotations

import base64
import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..deps import get_app
from ..core.artifacts import sniff
from ..core.app import LocalFlowApp

router = APIRouter(prefix="/api/workspace", tags=["workspace"])

MAX_TREE_ENTRIES = 3000     # 递归文件树条目上限，防止超大目录拖垮前端
MAX_READ_BYTES = 2_000_000  # 单文件预览读取上限（2MB）
_SKIP_DIRS = {"node_modules", "dist", "build", ".git", "__pycache__", "venv", ".venv", "target", "runtime_data"}


# ----------------------------------------------------------------------
# 项目持久化
# ----------------------------------------------------------------------

def _store_path(app: LocalFlowApp) -> Path:
    return app.config.data_dir / "workspaces.json"


def _load_projects(app: LocalFlowApp) -> List[dict]:
    try:
        data = json.loads(_store_path(app).read_text("utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_projects(app: LocalFlowApp, projects: List[dict]) -> None:
    _store_path(app).write_text(
        json.dumps(projects, ensure_ascii=False, indent=2), "utf-8"
    )


def _project(app: LocalFlowApp, project_id: str) -> dict:
    for p in _load_projects(app):
        if p.get("id") == project_id:
            return p
    raise HTTPException(status_code=404, detail="项目不存在")


def _resolve_root(root: str) -> Path:
    if not root or not root.strip():
        raise HTTPException(status_code=400, detail="请填写项目文件夹路径")
    try:
        rp = Path(root).expanduser().resolve(strict=True)
    except Exception:
        raise HTTPException(status_code=400, detail=f"路径无法解析：{root}")
    if not rp.is_dir():
        raise HTTPException(status_code=400, detail=f"不是有效的目录：{root}")
    return rp


# ----------------------------------------------------------------------
# 模型
# ----------------------------------------------------------------------

class ProjectCreate(BaseModel):
    name: str
    root: str


# ----------------------------------------------------------------------
# 路由
# ----------------------------------------------------------------------

@router.get("/list")
async def workspace_list(app=Depends(get_app)):
    return {"ok": True, "projects": _load_projects(app)}


@router.post("")
async def workspace_create(req: ProjectCreate, app=Depends(get_app)):
    name = (req.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="请填写项目名称")
    root = _resolve_root(req.root)
    projects = _load_projects(app)
    dup = next((p for p in projects if Path(p.get("root", "")).expanduser().resolve(
        strict=False) == root), None)
    if dup:
        return {"ok": True, "project": dup, "duplicate": True}
    proj = {
        "id": uuid.uuid4().hex[:12],
        "name": name,
        "root": str(root),
        "created": time.time(),
    }
    projects.append(proj)
    _save_projects(app, projects)
    return {"ok": True, "project": proj}


@router.delete("/{project_id}")
async def workspace_delete(project_id: str, app=Depends(get_app)):
    projects = _load_projects(app)
    rest = [p for p in projects if p.get("id") != project_id]
    if len(rest) == len(projects):
        raise HTTPException(status_code=404, detail="项目不存在")
    _save_projects(app, rest)
    return {"ok": True}


# ----------------------------------------------------------------------
# 递归文件树
# ----------------------------------------------------------------------

def _walk_files(root: Path, rel_dir: Path = Path("."), _buf: Optional[list] = None):
    if _buf is None:
        _buf = []
    try:
        names = sorted(os.listdir(root / rel_dir))
    except OSError:
        return _buf
    for n in names:
        if n.startswith("."):
            continue  # 跳过隐藏项
        if n.lower() in _SKIP_DIRS:
            continue  # 跳过常见重型目录，加速浏览
        rel = rel_dir / n
        full = root / rel
        try:
            is_dir = full.is_dir() and not full.is_symlink()
        except OSError:
            continue
        if is_dir:
            if len(_buf) < MAX_TREE_ENTRIES:
                _walk_files(root, rel, _buf)
        else:
            if len(_buf) >= MAX_TREE_ENTRIES:
                return _buf
            _buf.append({
                "name": n,
                "path": str(rel),  # 相对根目录的路径
                "dir": str(rel_dir.parts[-1]) if len(rel.parts) > 1 else "",
                "size": _safe_size(full),
                **(_type_fields(full)),
            })
    return _buf


def _safe_size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def _type_fields(p: Path) -> dict:
    art = sniff(p)
    return {"type": art["type"] if art else "file", "mime": art["mime"] if art else ""}


@router.get("/{project_id}/tree")
async def workspace_tree(project_id: str, app=Depends(get_app)):
    proj = _project(app, project_id)
    root = _resolve_root(proj["root"])
    if not root.exists():
        raise HTTPException(status_code=404, detail="项目文件夹已不存在")
    files = await asyncio.to_thread(_walk_files, root)
    return {"ok": True, "project": proj, "files": files}


# ----------------------------------------------------------------------
# 项目内安全读取（预览）
# ----------------------------------------------------------------------

@router.get("/{project_id}/artifact")
async def workspace_artifact(project_id: str, path: str, app=Depends(get_app)):
    proj = _project(app, project_id)
    root = _resolve_root(proj["root"])
    try:
        target = (root / path).expanduser().resolve(strict=False)
    except Exception:
        raise HTTPException(status_code=400, detail="路径解析失败")
    try:
        if target != root and not target.is_relative_to(root):
            raise ValueError("越界")
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="路径越界，拒绝访问")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")

    art = sniff(target) or {"type": "file", "name": target.name, "path": str(target), "mime": ""}
    payload = {"type": art["type"], "name": art["name"], "path": str(target), "mime": art.get("mime", "")}
    try:
        raw = target.read_bytes()[: MAX_READ_BYTES]
    except OSError as e:
        raise HTTPException(status_code=400, detail=f"读取失败：{e}")

    if art["type"] == "image":
        payload["base64"] = base64.b64encode(raw).decode()
    elif art["type"] in ("pdf", "audio", "video"):
        payload["base64"] = base64.b64encode(raw).decode()
    elif art["type"] == "docx":
        payload["text"] = _extract_docx(raw)
    else:
        payload["text"] = raw.decode("utf-8", errors="replace")
    return {"ok": True, "artifact": payload}


def _extract_docx(data: bytes) -> str:
    """DOCX 本质是 zip，抽取 word/document.xml 的文本（供纯文本预览）"""
    import io
    import re
    import zipfile
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        xml = z.read("word/document.xml").decode("utf-8", errors="ignore")
    except Exception:
        return "（无法解析 Word 文档）"
    paras = re.findall(r"<w:p(?:\s[^>]*)?>.*?</w:p>", xml, re.S) or [xml]
    out = []
    for p in paras:
        out.append("".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", p)))
    return "\n".join(out).strip() or "（文档为空或无可提取文本）"