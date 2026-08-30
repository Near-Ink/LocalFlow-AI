"""工作流引擎 — 可视化画布 DAG 执行

节点类型：
  - tool：调用已注册工具（FileTool / ShellTool / DateTimeTool / InfoTool /
           ClipboardTool / get_setting / set_setting / manage_cloud），params 为
           工具 input_schema 参数。
  - llm：调用当前引擎（Ollama / 云端）生成文本，params 含 model?/prompt?/temperature?。

边（edges, from→to）决定拓扑执行顺序；参数里可用 {{nodeId.output}} /
{{nodeId.error}} / {{nodeId.ok}} 引用上游节点的运行结果，实现数据流拼接。

安全：高危工具（set_setting / manage_cloud 变更类）在画布中不自动执行——
工具若返回 require_confirm，则节点标记为 need_confirm 并跳过实际执行，
提示用户改到对话中用 Agent 确认，避免画布误改系统设置。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from ..ports.engine import ChatMessage, GenerateOptions
from ..ports.tool import ToolContext

_PH = re.compile(r"\{\{\s*([A-Za-z0-9_.\-]+)\s*\}\}")


class WorkflowError(Exception):
    """工作流校验 / 执行异常"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _resolve_placeholders(value: Any, outputs: Dict[str, dict]) -> Any:
    """把参数中的 {{nodeId.output}} 等引用替换为上游节点的运行结果（递归）"""
    if isinstance(value, dict):
        return {k: _resolve_placeholders(v, outputs) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_placeholders(v, outputs) for v in value]
    if isinstance(value, str):
        def rep(m):
            key = m.group(1)
            node_id, _, field = key.partition(".")
            out = outputs.get(node_id)
            if out is None:
                return m.group(0)  # 未执行的节点 → 保留原样，避免误拼
            if not field or field in ("output", "stdout"):
                return str(out.get("output") or "")
            if field == "ok":
                return "true" if out.get("ok") else "false"
            if field == "error":
                return str(out.get("error") or "")
            data = out.get("data")
            if isinstance(data, dict):
                return json.dumps(data, ensure_ascii=False)
            if isinstance(data, list):
                return json.dumps(data, ensure_ascii=False)
            return str(data) if data is not None else ""
        return _PH.sub(rep, value)
    return value


class WorkflowEngine:
    """工作流执行器：拓扑排序 + 逐节点执行"""

    def __init__(self, app):
        self.app = app

    def _toposort(self, nodes: List[dict], edges: List[dict]) -> List[str]:
        ids = [n["id"] for n in nodes]
        if len(set(ids)) != len(ids):
            raise WorkflowError("节点 id 必须唯一")
        id_set = set(ids)
        indeg = {i: 0 for i in ids}
        adj = {i: [] for i in ids}
        for e in edges:
            f, t = e.get("from"), e.get("to")
            if f not in id_set or t not in id_set:
                raise WorkflowError(f"边引用了不存在的节点：{f} → {t}")
            if f == t:
                raise WorkflowError(f"节点 {f} 存在自环")
            adj[f].append(t)
            indeg[t] += 1
        q = [i for i in ids if indeg[i] == 0]
        if not q and ids:
            raise WorkflowError("图中存在循环，无法执行")
        order: List[str] = []
        while q:
            n = q.pop(0)
            order.append(n)
            for m in adj[n]:
                indeg[m] -= 1
                if indeg[m] == 0:
                    q.append(m)
        if len(order) != len(ids):
            raise WorkflowError("图中存在循环，无法执行")
        return order

    async def execute(
        self,
        nodes: List[dict],
        edges: List[dict],
        ctx: Optional[ToolContext] = None,
    ) -> tuple:
        """执行工作流，返回 (results, order)。

        results: {node_id: WfNodeResult 同构 dict}
        """
        order = self._toposort(nodes, edges)
        node_map = {n["id"]: n for n in nodes}
        outputs: Dict[str, dict] = {}
        results: Dict[str, dict] = {}
        for nid in order:
            node = node_map[nid]
            node_type = node.get("type", "tool")
            name = node.get("name", "")
            resolved = _resolve_placeholders(node.get("params", {}), outputs)
            try:
                if node_type == "llm":
                    results[nid] = await self._run_llm(nid, name, resolved)
                elif node_type == "tool":
                    results[nid] = await self._run_tool(nid, name, resolved, ctx)
                else:
                    raise WorkflowError(f"未知节点类型：{node_type}")
            except WorkflowError as e:
                results[nid] = _err_result("tool", name, e.message)
            except Exception as e:  # 节点级异常不中断整图
                results[nid] = _err_result(node_type, name, f"节点执行异常：{e}")
            outputs[nid] = {}
            for k in ("ok", "output", "error", "data"):
                if results[nid].get(k) is not None:
                    outputs[nid][k] = results[nid].get(k)
        return results, order

    async def _run_tool(self, nid: str, name: str, params: dict, ctx) -> dict:
        tool = self.app.tool_registry.get(name)
        if tool is None:
            raise WorkflowError(f"未知工具：「{name}」。可用：{', '.join(sorted(t.name for t in self.app.tool_registry.list()))}")
        res = await tool.run(params, ctx)
        if res.require_confirm:
            # 画布不自动执行高危动作，提示到对话走 Agent 确认
            return {
                "ok": True, "node_type": "tool", "name": name, "status": "need_confirm",
                "output": res.output or f"「{name}」为高危操作，画布未执行 —— 请在对话中让 Agent 操作并确认。",
                "error": "", "data": res.data,
            }
        return {
            "ok": bool(res.ok), "node_type": "tool", "name": name,
            "status": "done" if res.ok else "error",
            "output": res.output or "", "error": res.error or "", "data": res.data,
        }

    async def _run_llm(self, nid: str, model: str, params: dict) -> dict:
        model = (model or self.app.config.default_model or "").strip()
        prompt = str(params.get("prompt", "") or "").strip()
        if not prompt:
            raise WorkflowError("LLM 节点缺少 prompt 参数")
        temperature = float(params.get("temperature", 0.7) or 0.7)
        messages = [ChatMessage(role="user", content=prompt)]
        resp = await self.app.engine.chat(
            model=model, messages=messages,
            options=GenerateOptions(temperature=temperature),
        )
        return {
            "ok": True, "node_type": "llm", "name": resp.model or model, "status": "done",
            "output": resp.content,
            "error": "", "data": {"model": resp.model, "usage": resp.usage},
        }


def _err_result(node_type: str, name: str, message: str) -> dict:
    return {
        "ok": False, "node_type": node_type, "name": name, "status": "error",
        "output": "", "error": message, "data": None,
    }