#!/usr/bin/env bash
# dsh 会话删除补丁 — 为 DeepSeek Harness 会话列表添加「删除会话」菜单项
#
# 背景：dsh 0.1.1-rc.2 的会话操作菜单只有 重命名/分叉/归档，无删除。
# 本脚本在 @deepseek-ai/dsh-client-ui-workspace 的 client.js 中注入：
#   1. 会话菜单新增「🗑 删除会话」项
#   2. 点击后调用 LocalFlow 后端 POST /api/dsh/sessions/delete 删除会话数据目录
#      （LocalFlow 侧实现见 backend/localflow/api/dsh.py）
#
# 用法：在 dsh-poc 目录执行  ./patch-session-delete.sh
# 幂等：重复执行安全；重启 dsh 后生效。
set -euo pipefail
cd "$(dirname "$0")"

TARGETS=$(ls node_modules/.pnpm/@deepseek-ai+dsh-client-ui-workspace@*/node_modules/@deepseek-ai/dsh-client-ui-workspace/lib/client.js 2>/dev/null || true)
if [ -z "$TARGETS" ]; then
  echo "❌ 未找到 dsh-client-ui-workspace，请先 pnpm install"
  exit 1
fi

for F in $TARGETS; do
  if grep -q 'id: "delete"' "$F"; then
    echo "✔ 已应用补丁（幂等跳过）：$F"
    continue
  fi
  python3 - "$F" <<'PY'
import sys

path = sys.argv[1]
src = open(path, encoding="utf-8").read()

# 1) 菜单项：在 archive 项后追加 delete 项（tab 缩进与原文件一致）
anchor_menu = """				{
					id: "archive",
					label: t("menu.archiveSession"),
					icon: (0, react_jsx_runtime.jsx)(_deepseek_ai_dsh_client_ui_primitives.IconArchiveOutline20, { size: 16 })
				}
			];"""
assert anchor_menu in src, "菜单锚点未命中（包版本可能变化）"
src = src.replace(anchor_menu, """				{
					id: "archive",
					label: t("menu.archiveSession"),
					icon: (0, react_jsx_runtime.jsx)(_deepseek_ai_dsh_client_ui_primitives.IconArchiveOutline20, { size: 16 })
				},
				{
					id: "delete",
					label: "删除会话",
					icon: (0, react_jsx_runtime.jsx)("span", { style: { color: "#dc2626", fontSize: 14 }, children: "🗑" })
				}
			];""", 1)

# 2) 删除处理函数：插在 sessionMenuItems 定义之前
anchor_fn = """			const [menuOpen, setMenuOpen] = (0, react.useState)(false);
			const sessionMenuItems = ["""
assert anchor_fn in src, "函数锚点未命中"
src = src.replace(anchor_fn, """			const [menuOpen, setMenuOpen] = (0, react.useState)(false);
			// LocalFlow 协同：dsh 无会话删除，调用 LocalFlow 后端删除会话数据目录
			const onDeleteSession = async (sessionId) => {
				if (!window.confirm("确定删除该会话？删除后不可恢复。")) return;
				try {
					const r = await fetch("http://127.0.0.1:8765/api/dsh/sessions/delete", {
						method: "POST",
						headers: { "Content-Type": "application/json" },
						body: JSON.stringify({ session_id: sessionId })
					});
					const d = await r.json().catch(() => ({}));
					if (d && d.ok) window.location.reload();
					else window.alert("删除失败：" + ((d && d.detail) || r.status));
				} catch (e) {
					window.alert("删除失败：" + e.message);
				}
			};
			const sessionMenuItems = [""", 1)

# 3) 分发：onSelect 增加 delete 分支
anchor_sel = """								if (id === "archive") onArchive(node.id);
							},"""
assert anchor_sel in src, "onSelect 锚点未命中"
src = src.replace(anchor_sel, """								if (id === "archive") onArchive(node.id);
								if (id === "delete") onDeleteSession(node.id);
							},""", 1)

open(path, "w", encoding="utf-8").write(src)
print("✔ 已打补丁：", path)
PY
done

echo ""
echo "✅ 完成。请重启 dsh（--profile web）使补丁生效。"
echo "   会话删除由 LocalFlow 后端执行：POST http://127.0.0.1:8765/api/dsh/sessions/delete"
