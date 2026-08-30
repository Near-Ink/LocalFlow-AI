#!/usr/bin/env bash
# LocalFlow 社区源 — 发布脚本
# 用法: 托管后用最终线上地址重跑，生成匹配的 manifest 并给出接入命令
#   ./publish.sh https://your-deployed-name.netlify.app

set -e
cd "$(dirname "$0")"
BASE="${1:-}"

SYS_PY="$(command -v python3 || true)"
TRAE_PY="/Users/zhanghaoran/Library/Application Support/TRAE SOLO CN/ModularData/ai-agent/vm/tools/opt/python@3.10/3.10.20_3/libexec/bin/python3"
PY="$SYS_PY"
[ -n "$PY" ] || PY="$TRAE_PY"

if [ -z "$BASE" ]; then
  echo "用法: ./publish.sh <托管后的根URL>"
  echo "例:   ./publish.sh https://abc-xyz.netlify.app"
  echo "说明: 先把本目录(templates/ + manifest.json)托管上线，再把它的根URL填到这里。"
  exit 1
fi

"$PY" build_manifest.py --base "$BASE"

echo ""
echo "=============================================================="
echo " 下一步：把更新后的 manifest.json（和 templates/）重新上传覆盖"
echo " 你的托管站点，让线上的清单与本地一致。"
echo ""
echo " 然后在【系统终端】接入正式后端："
echo "--------------------------------------------------------------"
BACKEND="/Users/zhanghaoran/Documents/LocalFlow AI/backend"
echo "  kill \$(lsof -t -iTCP:8765 -sTCP:LISTEN) && cd \"$BACKEND\" && \\"
echo "  LOCALFLOW_WORKFLOW_SOURCES='[{\"id\":\"community\",\"name\":\"LocalFlow 社区\",\"url\":\"$BASE/manifest.json\"}]' \\"
echo "  nohup ./start_localflow.sh &"
echo "=============================================================="
echo " 启动后验证: curl http://127.0.0.1:8765/api/workflow/sources"
echo " 应看到 community(remote)，画布「🏪 模板广场」切到「🌐 LocalFlow 社区」即可用。"