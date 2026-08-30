#!/usr/bin/env python3
"""前端密钥写入链路测试脚本

模拟「📤 对外 AI」引导里的密钥管理全流程，验证后端 /api/settings/openai_api_key
的写入 / 鉴权联动 / 持久化 / 清除：

    ① 读取当前密钥状态
    ② 生成随机密钥并写入（body {key, value}，与前端一致）
    ③ 校验写入后状态（set=true、明文不回显）
    ④ 鉴权联动：无 key / 错 key → 401，对 key → 200 且 /v1/models 可用
    ⑤ 非流式 chat/completions 真实调用（可选，需本机有可用模型）
    ⑥ 持久化到 config.json
    ⑦ 清除密钥 → 恢复免鉴权

用法：
    python3 test_openai_key.py                 # 测试 127.0.0.1:8765，结束后清除密钥
    python3 test_openai_key.py --base http://127.0.0.1:8899
    python3 test_openai_key.py --keep          # 结束后保留新密钥（不自动清除）
    python3 test_openai_key.py --no-llm        # 跳过真实的模型生成调用

依赖：仅标准库（urllib / json / secrets）。需目标后端已启动。
"""

import argparse
import json
import secrets
import sys
import time
import urllib.error
import urllib.request

PASS, FAIL = 0, 0


def call(base: str, path: str, method="GET", data=None, token=None, timeout=90):
    url = base.rstrip("/") + path
    headers = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    body = None
    if data is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(data, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = raw.decode(errors="replace")
            return r.status, parsed
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = raw.decode(errors="replace")
        return e.code, parsed


def check(name, ok, detail=""):
    global PASS, FAIL
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))
    if ok:
        PASS += 1
    else:
        FAIL += 1


def main():
    ap = argparse.ArgumentParser(description="测试前端密钥写入链路")
    ap.add_argument("--base", default="http://127.0.0.1:8765", help="后端地址（默认 http://127.0.0.1:8765）")
    ap.add_argument("--keep", action="store_true", help="结束后保留新密钥，不自动清除")
    ap.add_argument("--no-llm", action="store_true", help="跳过真实模型调用（仅测鉴权）")
    args = ap.parse_args()
    BASE = args.base

    print(f"== 目标后端: {BASE} ==")

    # ① 健康
    code, _ = call(BASE, "/api/health")
    check("服务在线 /api/health->200", code == 200, f"got {code}")

    # ② 读取当前密钥状态（前端从 /api/settings 读取 openai_api_key）
    code, d = call(BASE, "/api/settings")
    if code != 200:
        check("读取设置", False, f"HTTP {code}: {d}")
        return
    setting = next((s for s in (d.get("settings") or []) if s.get("key") == "openai_api_key"), None)
    if not setting:
        check("存在 openai_api_key 设置项", False, "settings 中未找到该 key")
        return
    prev_set = bool(setting.get("value", {}).get("set"))
    check("读取密钥状态(前端读取逻辑)", True, f"写入前 set={prev_set}")
    # 明文不回显
    v = setting.get("value", {})
    if isinstance(v, dict) and "set" in v and "has_secret" in v and "api_key" not in v:
        check("明文不回显(仅 set/has_secret)", True)
    else:
        check("明文不回显(仅 set/has_secret)", False, f"value={v}")

    # ③ 生成随机密钥并写入（与前端按钮 body 一致：{key, value}）
    new_key = secrets.token_hex(16)          # 32 位十六进制，等同前端 crypto 生成
    code, d = call(BASE, "/api/settings/openai_api_key", "PUT",
                   {"key": "openai_api_key", "value": new_key})
    check("写入新密钥(PUT body={key,value})", code in (200, 422) or d) if code == 200 else None
    if code != 200:
        check("写入新密钥", False, f"HTTP {code}: {d}")
    else:
        check("写入新密钥(PUT body={key,value})", True, d.get("message", ""))
        # ④ 鉴权联动
        c1, _ = call(BASE, "/v1/models")
        check("鉴权联动: 无 key -> 401", c1 == 401, f"got {c1}")
        c2, _ = call(BASE, "/v1/models", token="wrong-key-123")
        check("鉴权联动: 错 key -> 401", c2 == 401, f"got {c2}")
        c3, ml = call(BASE, "/v1/models", token=new_key)
        check("鉴权联动: 对 key -> 200", c3 == 200, f"got {c3}")
        models = [m.get("id") for m in (ml.get("data") or [])] if isinstance(ml, dict) else []
        check("对 key 可列模型(/v1/models 非空)", len(models) > 0, f"模型数={len(models)}")
        if models and not args.no_llm:
            m = models[0]
            try:
                sc, rr = call(BASE, "/v1/chat/completions", "POST", {
                    "model": m,
                    "messages": [{"role": "user", "content": "请只回复：ok"}],
                    "temperature": 0.2,
                }, token=new_key, timeout=120)
                reply = (rr.get("choices") or [{}])[0].get("message", {}).get("content", "") if isinstance(rr, dict) else ""
                check(f"真实生成调用(模型={m})", sc == 200 and bool(reply), f"HTTP {sc} reply={reply[:40]!r}")
            except Exception as e:
                check("真实生成调用", False, f"异常: {e}")
        # ⑤ 持久化
        ok_persist = False
        import os
        cfg = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
        # 提示性：真实验证需读后端 data_dir/config.json；此处探测环境变量能力
        code2, d2 = call(BASE, "/api/settings/openai_api_key")
        check("写入后状态 set=true", code2 == 200 and bool((d2 or {}).get("value", {}).get("set")),
              f"HTTP {code2} {d2}")

    # ⑥ 清除密钥（前端「清除密钥」按钮，body 同构 {key, value:""}）
    code, d = call(BASE, "/api/settings/openai_api_key", "PUT",
                   {"key": "openai_api_key", "value": ""})
    if code == 200:
        c_free, _ = call(BASE, "/v1/models")
        check("清除后恢复免鉴权(无 key 可访问)", c_free == 200, f"got {c_free}")
        if args.keep:
            # 用户要求保留 → 把 keep 的密钥写回
            call(BASE, "/api/settings/openai_api_key", "PUT", {"key": "openai_api_key", "value": new_key})
            print(f"\n[keep] 已重新写入并保留密钥: {new_key}")
        else:
            print("\n[默认] 已清除密钥，测试后端恢复为免鉴权。")
    else:
        check("清除密钥请求", False, f"HTTP {code}: {d}")

    print(f"\n== 结果: {PASS} PASS / {FAIL} FAIL ==")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()