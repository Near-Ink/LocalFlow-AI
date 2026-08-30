"""工作流模板源管理 — 内置 + 远程社区源

统一「模板源（source）」抽象：builtin=随产品内置的示例模板；
remote=从配置的 URL 拉取清单与模板（社区/第三方托管）。

远程源协议（manifest.json）：
{
  "id": "community",          # 源 id
  "name": "社区模板广场",      # 展示名
  "categories": ["办公自动化"],
  "templates": [
    {
      "id": "weekly-report",
      "title": "周报",
      "category": "办公自动化",
      "description": "...",
      "url": "https://host/templates/weekly-report.json"   # 完整模板定义（load 结构）
    }
  ]
}

安全：拉取经后端代理（前端不经 fetch 避免 CORS），只允许 http/https scheme；
manifest 与模板结构做基本校验，防 SSRF/畸形数据。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from .workflow_market import list_market as _builtin_list, get_market_template as _builtin_get

BUILTIN_SOURCE_ID = "builtin"

_HTTPX_CFG = {"timeout": httpx.Timeout(15.0), "follow_redirects": True}


def _is_http_url(url: str) -> bool:
    u = urlparse(url)
    return u.scheme in ("http", "https") and bool(u.netloc)


def build_sources(configured: List[dict]) -> List[dict]:
    """由配置组装源列表：builtin + 配置的 remote（去重，非法跳过）"""
    sources = [{"id": BUILTIN_SOURCE_ID, "kind": "builtin", "name": "内置示例"}]
    seen = {BUILTIN_SOURCE_ID}
    if isinstance(configured, list):
        for s in configured:
            sid = str((s or {}).get("id", "")).strip()
            url = str((s or {}).get("url", "")).strip()
            name = str((s or {}).get("name") or sid or "远程源")
            if not sid or not url or not _is_http_url(url) or sid in seen:
                continue
            seen.add(sid)
            sources.append({"id": sid, "kind": "remote", "name": name, "url": url})
    return sources


def _builtin_info() -> dict:
    meta = _builtin_list()
    return {
        "id": BUILTIN_SOURCE_ID, "kind": "builtin", "name": "内置示例",
        "categories": meta["categories"],
        "templates": meta["templates"],
    }


async def _http_get_json(url: str) -> dict:
    if not _is_http_url(url):
        raise ValueError(f"仅允许 http/https 地址：{url}")
    async with httpx.AsyncClient(**_HTTPX_CFG) as client:
        r = await client.get(url)
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict):
            raise ValueError("模板源返回的不是 JSON 对象")
        return data


def _normalize_manifest(data: dict, fallback_id: str, fallback_name: str) -> dict:
    """把远程 manifest 规范化为 {id,name,categories,templates:[meta]}"""
    cats = [str(c) for c in (data.get("categories") or [])]
    raw_tpls = data.get("templates") or []
    templates = []
    for t in raw_tpls:
        if not isinstance(t, dict):
            continue
        tid = str(t.get("id", "")).strip()
        url = str(t.get("url", "")).strip()
        if not tid or not url or not _is_http_url(url):
            continue
        templates.append({
            "id": tid,
            "category": str(t.get("category") or ""),
            "title": str(t.get("title") or tid),
            "description": str(t.get("description") or ""),
            "node_count": int(t.get("node_count") or 0),
            "edge_count": int(t.get("edge_count") or 0),
            "url": url,
        })
    return {
        "id": str(data.get("id")) or fallback_id,
        "name": str(data.get("name")) or fallback_name,
        "categories": cats,
        "templates": templates,
    }


async def fetch_source(source: dict) -> dict:
    """取某源列表：(categories, templates)。builtin 本地直出；remote 拉取并规范化。"""
    if source.get("kind") == "builtin":
        return _builtin_info()
    url = source.get("url", "")
    raw = await _http_get_json(url)
    return _normalize_manifest(raw, source.get("id", "remote"), source.get("name", "远程源"))


async def fetch_template(source: dict, tpl_id: str) -> Optional[dict]:
    """取某源某模板完整定义。builtin 从本地取；remote 先拉 manifest 再按其 url 取模板。"""
    if source.get("kind") == "builtin":
        return _builtin_get(tpl_id)
    manifest = await fetch_source(source)
    tpl = next((t for t in manifest["templates"] if t["id"] == tpl_id), None)
    if tpl is None:
        return None
    data = await _http_get_json(tpl["url"])
    if data.get("kind") != "localflow-workflow" or not isinstance(data.get("nodes"), list):
        raise ValueError("远程模板结构不合法：缺少 kind=localflow-workflow 或 nodes")
    data["source_id"] = source.get("id", "remote")
    return data