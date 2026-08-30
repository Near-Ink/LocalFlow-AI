#!/usr/bin/env python3
"""导出 LocalFlow 内置模板为「社区源包」，并生成 manifest.json。

社区源是任意静态托管目录：只要满足两个约定即可被 LocalFlow 当作远程模板源——
  1) 根有一个 manifest.json（清单，含分类与各模板的 url）
  2) templates/<id>.json 是每个模板的完整定义（localflow-workflow 结构）

用法：
    python3 build_manifest.py --base <部署后的根 URL>
    例：python3 build_manifest.py --base https://youruser.github.io/localflow-community

生成：
    ./templates/<id>.json       各模板完整定义
    ./manifest.json             清单（templates[].url 由 --base 拼出）

注意：manifest 里每个模板的 url 必须是部署后的绝对地址，所以托管后要用真实的
线上 base 重跑本脚本（或手动改 manifest.json），否则 LocalFlow 无法拉到模板。
"""

import argparse
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from localflow.core.workflow_market import BUILTIN_TEMPLATES  # noqa: E402

OUT = Path(__file__).resolve().parent


def main() -> None:
    ap = argparse.ArgumentParser(description="导出内置模板为社区源包")
    ap.add_argument("--base", required=True,
                    help="部署后的根 URL，如 https://user.github.io/localflow-community")
    args = ap.parse_args()

    tpl_dir = OUT / "templates"
    tpl_dir.mkdir(exist_ok=True)
    base = args.base.rstrip("/")

    entries = []
    for t in BUILTIN_TEMPLATES:
        fid = t["id"]
        # 复制内置数据（保留节点坐标），补上 name/updated，去掉内部 source 标记
        doc = {k: v for k, v in t.items() if k != "source"}
        doc["name"] = fid
        doc["updated"] = "2026-08-29 00:00:00"
        (tpl_dir / f"{fid}.json").write_text(
            json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        entries.append({
            "id": fid,
            "title": t["title"],
            "category": t["category"],
            "description": t["description"],
            "node_count": len(t["nodes"]),
            "edge_count": len(t["edges"]),
            "url": f"{base}/templates/{fid}.json",
        })

    manifest = {
        "id": "community",
        "name": "LocalFlow 社区模板库",
        "categories": list(dict.fromkeys(t["category"] for t in BUILTIN_TEMPLATES)),
        "templates": entries,
    }
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"已导出 {len(entries)} 个模板 → {tpl_dir}")
    print(f"清单 → {OUT / 'manifest.json'}")
    print(f"部署后根地址={base}，模板 url 均指向 {base}/templates/<id>.json")


if __name__ == "__main__":
    main()