#!/usr/bin/env bash
# LocalFlow AI 正式后端启动脚本（持久实例）
# 如需改端口/主机，只改下面这一行；本文件在 backend 目录下运行。
HOST="127.0.0.1"
PORT="8765"
LOG="$HOME/.localflow/backend.log"

cd "$(dirname "$0")" || exit 1
echo "启动 LocalFlow 后端 -> http://$HOST:$PORT  (日志: $LOG)"
LOCALFLOW_ENABLE_AGENT=1 \
  "/Users/zhanghaoran/Library/Application Support/TRAE SOLO CN/ModularData/ai-agent/vm/tools/opt/python@3.10/3.10.20_3/libexec/bin/python3" \
  -m uvicorn localflow.main:app \
  --host "$HOST" --port "$PORT" \
  > "$LOG" 2>&1