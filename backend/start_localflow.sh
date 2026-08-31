#!/usr/bin/env bash
# LocalFlow AI 正式后端启动脚本（持久实例）
# 如需改端口/主机，只改下面这一行；本文件在 backend 目录下运行。
HOST="127.0.0.1"
PORT="8765"
LOG="$HOME/.localflow/backend.log"

cd "$(dirname "$0")" || exit 1
PY="$(command -v python3 || command -v python || true)"
if [ -z "$PY" ]; then
  echo "未找到 python3，请先安装 Python 3.10+ 并加入 PATH。" >&2
  exit 1
fi
echo "启动 LocalFlow 后端 -> http://$HOST:$PORT  (日志: $LOG)"
LOCALFLOW_ENABLE_AGENT=1 \
  "$PY" \
  -m uvicorn localflow.main:app \
  --host "$HOST" --port "$PORT" \
  > "$LOG" 2>&1