# LocalFlow 社区模板源 — 部署指引

这是为 LocalFlow 提供的「社区模板源」目录。把它托管到任意 **HTTPS 静态站点**，
再把地址填进 LocalFlow 配置，即可作为一个**远程社区源**，供产品和其他用户浏览、
一键「使用」或「安装到我的」。

---

## 目录结构

```
workflow-community-source/
├── manifest.json          # 清单：分类 + 每个模板的 url（托管后须用真实地址生成）
├── templates/             # 每个模板的完整定义（localflow-workflow JSON）
│   ├── daily-brief.json
│   ├── email-opener.json
│   ├── polish.json
│   ├── summarize.json
│   └── time-greeting.json
├── build_manifest.py      # 生成清单的脚本（--base 指定线上根地址）
└── publish.sh             # 一键发布脚本：填线上地址后重跑 manifest 并给出接入命令
```

## 托管步骤

最快的路线是 **Netlify Drop**（免 git / 免命令行，网页拖拽即上线）：

1. 浏览器打开 <https://app.netlify.com/drop>（可免费注册）。
2. 把本目录**整个拖进**浏览器窗口。
3. 十几秒后它会自动部署并分配一个地址，形如 `https://一串随机-xxx.netlify.app`。

> 想国内访问更快、或已有对象存储 / GitHub Pages / Vercel，也可用任意 HTTPS 静态托管，
> 只要把 `templates/` 目录和 `manifest.json` 原样放上、能公网访问即可，后续步骤相同。

### 1. 上传 templates/ 目录
把 `templates/` 整个目录 + `manifest.json` 上传到你选定的静态托管平台，得到一个根地址，
例如 `https://abc-xyz.netlify.app` 或 `https://youruser.github.io/localflow-community`。

部署方式任选其一：

- **GitHub Pages**（被墙环境可改用 gitee page / 公司静态站）：
  `git clone` 该项目 → 推送到 `gh-pages` 分支或开启 Pages 并选根目录。
- **Vercel / Netlify**：导入仓库，直接部署根目录。
- **对象存储 / 任意 NGINX**：把目录原样放上即可。

### 2. 用真实地址重新生成 manifest.json
因为 `manifest.json` 里每个模板的 `url` 必须是**线上的绝对地址**，托管后请用最终线上
base 重新生成（不改的话 LocalFlow 拉不到模板）。最简单是用一键脚本：

```bash
cd "/Users/zhanghaoran/Documents/LocalFlow AI/workflow-community-source" && ./publish.sh https://你的-xxx.netlify.app
```

`publish.sh` 会做两件事：
1. 用你的线上地址重新生成 `manifest.json`（模板 url 指向线上）；
2. **直接打印出正式的「后端接入命令」**，复制执行即可。

等价的手动方式（若无脚本直接重跑也行）：

```bash
cd "/Users/zhanghaoran/Documents/LocalFlow AI/workflow-community-source" && \
python3 build_manifest.py --base https://你的-xxx.netlify.app
```

无论用哪种，重跑后把**更新后的 `manifest.json`** 重新上传覆盖你的托管站点
（Netlify Drop 可直接再拖一份 `manifest.json` 覆盖）。此后网站在线，即可被消费。

## 在 LocalFlow 中接入该社区源

重启 LocalFlow 后端时带上环境变量（JSON 数组，可配多个远程源）：

```bash
kill "$(lsof -t -iTCP:8765 -sTCP:LISTEN)" && cd "/Users/zhanghaoran/Documents/LocalFlow AI/backend" && \
LOCALFLOW_WORKFLOW_SOURCES='[{"id":"community","name":"LocalFlow 社区","url":"https://youruser.github.io/localflow-community/manifest.json"}]' \
nohup ./start_localflow.sh &
```

随后在画布点「🏪 模板广场」，顶部会出现「内置示例 / 🌐 LocalFlow 社区」两个源，
切到社区源即可浏览/使用/安装模板。

## 协议说明（供社区维护者 / 二次开发者参考）

- `manifest.json` 结构：

```jsonc
{
  "id": "community",        // 源 id（唯一）
  "name": "LocalFlow 社区模板库", // 展示名
  "categories": ["办公自动化", "文本处理"],
  "templates": [
    {
      "id": "time-greeting",
      "title": "时间问候",
      "category": "办公自动化",
      "description": "…",
      "node_count": 2,
      "edge_count": 1,
      "url": "https://host/templates/time-greeting.json"  // 完整模板定义地址
    }
  ]
}
```

- 每个 `templates/<id>.json` 是完整模板定义，须满足 `kind == "localflow-workflow"`
  且含 `nodes`（`type: tool | llm`）与 `edges`（`{from,to}`）。
- LocalFlow 经**后端代理**拉取（前端不直连，避免 CORS），仅允许 `http/https`，
  并对清单/模板做结构校验；非法地址会被拒绝。

## 新增 / 修改模板

把模板 JSON 放进 `templates/`，在 `manifest.json` 中登记一条记录，
再用 `build_manifest.py --base <你的真实线上地址>` 重新生成并上传即可。