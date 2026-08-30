"""内置工作流模板广场 — 开箱即用示例

随产品分发的预置模板，用户可从画布「模板广场」一键载入/安装到自己的模板库。
模板全部用 datetime / LLM 组合，保证任何环境（本地 Ollama 或云端）都能真实运行。

模板结构复用后端模板库落盘格式：
  {kind, version, id, title, description, category, nodes:[{id,type,name,params,x,y}], edges:[{from,to}]}

预留社区分享扩展：source=builtin，未来可加 source=remote 支持远程广场。
"""

CATEGORIES = ["办公自动化", "文本处理"]


def _tpl2(title, category, description, prompt):
    """two-node: datetime -> llm；name 填空让引擎回退默认模型，杜绝写死型号"""
    return {
        "kind": "localflow-workflow",
        "version": 2,
        "source": "builtin",
        "title": title,
        "category": category,
        "description": description,
        "nodes": [
            {"id": "datetime-1", "type": "tool", "name": "datetime", "params": {}, "x": 40, "y": 40},
            {"id": "llm-1", "type": "llm", "name": "", "params": {
                "prompt": prompt, "temperature": 0.7,
            }, "x": 320, "y": 40},
        ],
        "edges": [{"from": "datetime-1", "to": "llm-1"}],
    }


def _llm1(title, category, description, prompt_hint):
    """single-node llm：无上游，用户在画布里填内容"""
    return {
        "kind": "localflow-workflow",
        "version": 2,
        "source": "builtin",
        "title": title,
        "category": category,
        "description": description,
        "nodes": [
            {"id": "llm-1", "type": "llm", "name": "", "params": {
                "prompt": prompt_hint, "temperature": 0.7,
            }, "x": 120, "y": 60},
        ],
        "edges": [],
    }


# id 用作存盘文件名，保持 ASCII，避免小模型/终端中文名干扰
BUILTIN_TEMPLATES = [
    {
        "id": "time-greeting",
        ** _tpl2(
            "时间问候", "办公自动化",
            "datetime 取当前时间 → LLM 改写成一句中文问候语",
            "当前时间是 {{datetime-1.output}}，请用一句通顺、不啰嗦的中文问候语向我问好，只输出问候语本身。",
        ),
    },
    {
        "id": "daily-brief",
        ** _tpl2(
            "今日简报", "办公自动化",
            "datetime 取当前时间 → LLM 生成今日工作简报开头要点",
            "今天是 {{datetime-1.output}}。请写一段 3-4 行的今日工作简报开头，包含日期与一句工作提醒，中文输出。",
        ),
    },
    {
        "id": "email-opener",
        ** _tpl2(
            "邮件开场", "办公自动化",
            "datetime 取当前时间 → LLM 生成一封正式邮件的开场白",
            "当前日期是 {{datetime-1.output}}。请用中文写一句正式商务邮件的开场白，包含日期背景，语气礼貌简洁。",
        ),
    },
    {
        "id": "polish",
        ** _llm1(
            "文案润色", "文本处理",
            "LLM 单节点：把输入文本润色得更通顺、更正式（无上游，需在节点提示词里粘贴文本）",
            "请润色下面这段文本，使其更通顺、更书面、不改变原意，直接输出润色结果：\n【待润色文本，在此粘贴】",
        ),
    },
    {
        "id": "summarize",
        ** _llm1(
            "要点摘要", "文本处理",
            "LLM 单节点：把一段长文本提炼成 3 条要点（无上游，需在节点提示词里粘贴文本）",
            "请把下面这段文字提炼成 3 条精炼要点，用中文、分条列出，只输出要点：\n【待摘要文本，在此粘贴】",
        ),
    },
]

BY_ID = {t["id"]: t for t in BUILTIN_TEMPLATES}


def list_market():
    """仅返回元信息（不含完整节点），供列表展示"""
    return {
        "categories": CATEGORIES,
        "templates": [{
            "id": t["id"],
            "title": t["title"],
            "category": t["category"],
            "description": t["description"],
            "node_count": len(t["nodes"]),
            "edge_count": len(t["edges"]),
            "source": t.get("source", "builtin"),
        } for t in BUILTIN_TEMPLATES],
    }


def get_market_template(tpl_id: str):
    t = BY_ID.get(tpl_id)
    if t is None:
        return None
    return t


def to_user_template(tpl: dict, description: str | None = None) -> dict:
    """把源模板转成可落盘的 user 模板（文件名为 id/name，兼容内置与远程）"""
    return {
        "kind": "localflow-workflow",
        "version": 2,
        "name": str(tpl.get("id") or tpl.get("name") or "未命名工作流"),
        "description": description if description is not None else tpl.get("description", ""),
        "nodes": tpl.get("nodes", []),
        "edges": tpl.get("edges", []),
        "updated": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
    }