"""Agent 工具：保存/生成本地工作流模板

让本地 Agent 用自然语言描述后，把节点/连线落盘到后端模板库，
前端画布可通过「载入」把它渲染出来运行。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, List, Optional, Tuple

from ...ports.tool import Tool, ToolContext, ToolResult
from ...core.workflow import WorkflowEngine, WorkflowError


def _template_summary(p: Path) -> dict:
    """读一个模板文件，返回 {name, description, node_count, edge_count, updated}"""
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return {
            "name": p.stem,
            "description": data.get("description", ""),
            "node_count": len(data.get("nodes", [])),
            "edge_count": len(data.get("edges", [])),
            "updated": data.get("updated", ""),
        }
    except Exception:
        return None


def _sanitize_name(name: str) -> str:
    name = (name or "").strip() or "未命名工作流"
    name = re.sub(r"[/\\]", "_", name).replace("..", "_")
    return name[:80]


def _workflows_dir(app) -> Path:
    d = Path(app.config.data_dir) / "workflows"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _as_list(v):
    """把 nodes/edges 规范成数组：接受数组、JSON 字符串、单对象。"""
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return []
        try:
            j = json.loads(s)
            if isinstance(j, list):
                return j
            if isinstance(j, dict):
                return [j]
        except Exception:
            pass
        return []
    if isinstance(v, dict):
        return [v]
    return []


class SaveWorkflowTool(Tool):
    """把节点/连线组成的本地工作流保存到后端模板库，供画布加载运行"""

    name = "save_workflow"
    description = (
        "把用户描述的本地工作流保存到 LocalFlow 后端模板库。"
        "工作流由 nodes（工具节点或 llm 节点）和 edges（连线）组成："
        "节点 type=tool 时 name 是已注册工具名，type=llm 时 name 是模型名，params 为该节点参数；"
        "edges 的 from/to 引用节点 id，表示执行顺序。保存后用户能在画布「载入」并运行。"
    )
    is_hazardous = False

    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "工作流模板名，如「每日时长报告」"},
            "description": {"type": "string", "description": "工作流说明"},
            "nodes": {
                "type": "array",
                "description": "节点列表。type=tool/llm；name 为工具名或模型名；params 为节点参数（llm 如 prompt/temperature/model）",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "节点唯一 id，如 datetime-1"},
                        "type": {"type": "string", "enum": ["tool", "llm"]},
                        "name": {"type": "string"},
                        "params": {"type": "object"},
                        "x": {"type": "number", "description": "画布横坐标(0-700)，可选"},
                        "y": {"type": "number", "description": "画布纵坐标(0-460)，可选"},
                    },
                    "required": ["id", "type", "name"],
                },
            },
            "edges": {
                "type": "array",
                "description": "连线列表，from/to 引用 nodes 的 id",
                "items": {
                    "type": "object",
                    "properties": {
                        "from": {"type": "string"},
                        "to": {"type": "string"},
                    },
                    "required": ["from", "to"],
                },
            },
            "overwrite": {
                "type": "boolean", "description": "同名模板已存在时是否覆盖",
            },
        },
        "required": ["name", "nodes"],
    }

    async def run(self, params: dict, ctx: Optional[ToolContext] = None) -> ToolResult:
        app = ctx.extras.get("app") if ctx else None
        if app is None:
            return ToolResult(ok=False, output="无法访问应用上下文：缺少 app 引用")

        name = _sanitize_name(str(params.get("name", "")))
        desc = str(params.get("description", "") or "")
        overwrite = bool(params.get("overwrite"))

        raw_nodes = _as_list(params.get("nodes"))
        if not raw_nodes:
            return ToolResult(ok=False, output="未提供任何节点：nodes 不能为空")

        # 校验工具名是否真实存在
        known = {t.name for t in app.tool_registry.list()}
        nodes = []
        seen = set()
        for i, n in enumerate(raw_nodes):
            if not isinstance(n, dict):
                return ToolResult(ok=False, output=f"节点第 {i} 项不是对象")
            nid = str(n.get("id", "")).replace(" ", "-")
            if not nid:
                nid = f"node-{i+1}"
            if nid in seen:
                nid = f"{nid}-{i+1}"
            seen.add(nid)
            ntype = n.get("type", "tool")
            nname = str(n.get("name", ""))
            if ntype == "tool" and nname not in known:
                avail = "、".join(sorted(known))
                return ToolResult(
                    ok=False,
                    output=f"工具「{nname}」不存在。可用工具：{avail}",
                )
            nodes.append({
                "id": nid,
                "type": ntype,
                "name": nname,
                "params": dict(n.get("params") or {}),
                "x": int(n.get("x") if n.get("x") is not None else 30 + i * 130),
                "y": int(n.get("y") if n.get("y") is not None else 60),
            })

        # 校验链路引用
        ids = {n["id"] for n in nodes}
        edges = []
        for e in _as_list(params.get("edges")):
            if not isinstance(e, dict):
                continue
            f_, to = str(e.get("from", "")), str(e.get("to", ""))
            if f_ not in ids or to not in ids:
                continue
            if f_ == to:
                continue
            edges.append({"from": f_, "to": to})

        data = {
            "kind": "localflow-workflow",
            "version": 2,
            "name": name,
            "description": desc,
            "nodes": nodes,
            "edges": edges,
            "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        p = _workflows_dir(app) / f"{name}.json"
        if p.exists() and not overwrite:
            return ToolResult(
                ok=True,
                output=f"模板「{name}」已存在（{len(nodes)} 节点 / {len(edges)} 连线）。如需覆盖请设 overwrite=true。",
                data={"name": name, "saved": False, "node_count": len(nodes)},
            )
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.rename(p)
        return ToolResult(
            ok=True,
            output=(
                f"已保存工作流模板「{name}」到后端模板库：{len(nodes)} 个节点、{len(edges)} 条连线。"
                "用户可在工作流画布点「下载模板」选择它加载并运行。"
            ),
            data={"name": name, "saved": True, "node_count": len(nodes), "edge_count": len(edges)},
        )


class ListWorkflowTool(Tool):
    """列出后端模板库中所有已保存的工作流模板"""

    name = "list_workflows"
    description = (
        "列出后端模板库中所有已保存的工作流模板（名称、说明、节点/连线数、更新时间），"
        "让用户知道有哪些模板可运行，也可结合 run_workflow 直接运行。"
    )
    is_hazardous = False
    input_schema = {"type": "object", "properties": {}}

    async def run(self, params: dict, ctx: Optional[ToolContext] = None) -> ToolResult:
        app = ctx.extras.get("app") if ctx else None
        if app is None:
            return ToolResult(ok=False, output="无法访问应用上下文：缺少 app 引用")
        items = []
        for p in sorted(_workflows_dir(app).glob("*.json")):
            s = _template_summary(p)
            if s:
                items.append(s)
        items.sort(key=lambda i: i["updated"], reverse=True)
        if not items:
            return ToolResult(
                ok=True,
                output="后端模板库当前为空。可用 save_workflow 创建工作流模板，或在画布搭好后点「保存」。",
                data={"templates": []},
            )
        lines = [f"[{i}] {s['name']}（{s['node_count']} 节点 / {s['edge_count']} 连线，{s['updated']}）"
                 + (f" — {s['description']}" if s["description"] else "")
                 for i, s in enumerate(items, 1)]
        return ToolResult(
            ok=True,
            output=f"后端模板库共有 {len(items)} 个工作流模板：\n" + "\n".join(lines),
            data={"templates": items},
        )


def _template_items(app) -> List[dict]:
    """读取模板库并按更新时间倒序返回 [{name, path, **_template_summary}]"""
    items = []
    for p in _workflows_dir(app).glob("*.json"):
        if p.suffix != ".json":
            continue
        s = _template_summary(p)
        if s:
            s["path"] = str(p)
            items.append(s)
    items.sort(key=lambda i: i["updated"], reverse=True)
    return items


def _match_template(items: List[dict], name: str) -> Tuple[Optional[dict], Optional[str]]:
    """按名称/数字序号/子串匹配模板，返回 (模板, 提示语)。

    - 精确名命中 → 返回该模板
    - 纯数字 N → 排序后第 N 个
    - 子串唯一命中 → 返回该模板；多命中/零命中 → (None, 候选清单提示)
    """
    exact = next((i for i in items if i["name"] == name), None)
    if exact:
        return exact, None
    if name.isdigit():
        idx = int(name)
        if 1 <= idx <= len(items):
            return items[idx - 1], None
    sub = [i for i in items if name in i["name"] or i["name"] in name]
    if len(sub) == 1:
        return sub[0], None
    if len(sub) > 1:
        names = "、".join(i["name"] for i in sub)
        return None, f"有多个模板匹配「{name}」：{names}。请指定更精确的名称或序号。"
    return None, None


class RunWorkflowTool(Tool):
    """运行一个已保存到后端模板库的工作流模板并返回各节点执行结果"""

    name = "run_workflow"
    description = (
        "运行一个已保存到后端模板库的工作流模板（按名称）并返回各节点执行结果，含 LLM 节点输出。"
        "运行前可先用 list_workflows 查看有哪些模板。"
    )
    is_hazardous = False
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "要运行的工作流模板名，或其在 list_workflows 中的数字序号"},
        },
        "required": ["name"],
    }

    async def run(self, params: dict, ctx: Optional[ToolContext] = None) -> ToolResult:
        app = ctx.extras.get("app") if ctx else None
        if app is None:
            return ToolResult(ok=False, output="无法访问应用上下文：缺少 app 引用")
        items = _template_items(app)
        if not items:
            return ToolResult(ok=False, output="后端模板库为空，暂无可运行的模板。可用 save_workflow 先创建。")
        tpl, hint = _match_template(items, _sanitize_name(str(params.get("name", ""))))
        if tpl is None:
            msg = f"找不到模板：{params.get('name', '')}。"
            if not hint:
                hint = f"当前模板库有 {len(items)} 个，可用 list_workflows 查看名称与序号，再通过 exact 名称或序号运行。"
            return ToolResult(ok=False, output=msg + hint)
        name = tpl["name"]
        try:
            data = json.loads(Path(tpl["path"]).read_text(encoding="utf-8"))
        except Exception:
            return ToolResult(ok=False, output=f"模板「{name}」文件损坏，无法运行。")
        nodes = [{"id": n.get("id"), "type": n.get("type", "tool"),
                  "name": n.get("name", ""), "params": n.get("params", {})}
                 for n in (data.get("nodes") or [])]
        edges = [{"from": e.get("from"), "to": e.get("to")}
                 for e in (data.get("edges") or [])]
        if not nodes:
            return ToolResult(ok=False, output=f"模板「{name}」没有节点，无法运行。")
        try:
            engine = WorkflowEngine(app)
            results, order = await engine.execute(nodes, edges, ctx=ctx or _context(app))
        except WorkflowError as e:
            return ToolResult(ok=False, output=f"工作流「{name}」运行失败：{e.message}")
        lines = []
        for nid in order:
            v = results.get(nid, {})
            st = v.get("status", "")
            if st == "error":
                text = f"[错误] {v.get('error', '')}"[:300]
            elif st == "need_confirm":
                text = "[需授权]（该节点需用户确认，本次未执行）"
            else:
                text = str(v.get("output", ""))[:300]
            lines.append(f"· {nid} → {text}")
        return ToolResult(
            ok=True,
            output=f"工作流「{name}」运行完成：\n" + "\n".join(lines),
            data={"name": name, "order": order, "results": results},
        )


def _context(app) -> ToolContext:
    """构建一个最小 ToolContext（供无外部 ctx 时使用），复刻 api/workflow._ctx"""
    dirs = [str(d) for d in app.sandbox.allow_dirs] if app.sandbox else []
    return ToolContext(
        allow_dirs=dirs,
        session_id="agent-workflow",
        event_store=app.event_store,
        extras={"app": app},
    )