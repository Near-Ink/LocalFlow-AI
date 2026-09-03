# LocalFlow AI

> 面向家用 PC 的开源多模型协同平台 — 本地云端混合调度的桌面级 AI 工作台

**一键部署 · 硬件自适应 · 本地云端混合调度 · 对话页内置 DeepSeek Harness · 对外 OpenAI 兼容 API**

### ⚠️ macOS 用户必读：首次打开被拦截的解决办法

> [!CAUTION]
> 当前安装包**尚未做 Apple 开发者签名 / 公证**（测试阶段暂不购买 $99/年 账号）。在 Mac 上**首次双击打开会被 Gatekeeper 拦截**，你很可能会看到这样一段弹窗：
>
> > **「LocalFlow AI」已损坏，无法打开。你应该将它移到废纸篓。**
>
> **这不是安装包真的损坏，也千万不要点「移到废纸篓」！** macOS 只是对未公证的 App 做了常规拦截，包本身是好的。任选其一即可正常打开：
> 1. **右键** 点击 `LocalFlow AI.app` → 选择「**打开**」→ 弹窗中点「打开」确认（每个新版本首次打开需重复一次）；
> 2. 终端执行下方命令解除隔离标记后重试：
>
> ```bash
> sudo xattr -cr "/Applications/LocalFlow AI.app"
> ```
>
> 绕过一次后即可正常双击打开。正式签名 + 公证将在后续版本补齐。

LocalFlow AI 把「本地推理（Ollama）+ 云端大模型」统一进一个桌面应用：对话页内置 DeepSeek Harness 作为 Agent 工作台，本地模型通过硬件识别库自动推荐部署，并提供 OpenAI 兼容接口让其他 Agent / 工具直接调用本机模型。

## ✨ 核心特性

- **对话页内置 DeepSeek Harness（已内置，零安装）** — 完整的 Agent 工作台（会话、子任务、工具、工作流），聊天统一由 dsh 承担；**dsh 与其便携 Node 运行时已一并打进安装包**。首次启动自动检测：已装则比对版本、不符自动更新，未装则解包内置安装包，全程在对话页显示明确提示与进度条，无需另行安装 dsh 或 Node
- **本地部署向导 + 硬件识别库** — 部署时探测本机硬件并与已知硬件库匹配（Apple Silicon / NVIDIA / AMD / Intel Arc），命中即给出准确能力档位与模型推荐；未命中按显存/内存通用规则兜底，绝不因识别失败而中断
- **对外 OpenAI 兼容 API** — 一键生成 API Key，其他 Agent / 工具（Cursor、Cline、Open WebUI 等）直接以 OpenAI 协议调用本机模型（本地 + 云端绑定），支持 SSE 流式
- **本地云端混合调度** — 本地模型负责日常高频任务，云端 API 兜底复杂推理；下载本地模型期间可先绑定云端开聊
- **硬件实时监控** — 头部迷你条实时显示 CPU / 内存 / GPU / 显存占用，点击进入详情页（含趋势曲线与缓存状态）
- **多层缓存（L1 已实现 · v0.1.0 已接线）** — chat 结果按请求哈希缓存到本地 SQLite（`namespace=llm`，TTL 3600s），显著降低重复提问的 Token 消耗；L2/L3/L4 规划中
- **插件微内核 + Port & Adapter 六边形架构** — 功能可插拔，DSH 更新只换 adapter

## 🏗️ 架构

```
┌─────────────────────────────────────┐
│   UI 桌面层 (Electron)              │  ← 对话页内嵌 DeepSeek Harness
├─────────────────────────────────────┤
│   API 层 (FastAPI)                  │  ← 含 /v1 OpenAI 兼容对外接口
├─────────────────────────────────────┤
│   核心层 (core/)                    │  ← 业务逻辑，只依赖抽象
├─────────────────────────────────────┤
│   Ports 领域接口 (稳定层)           │
├─────────────────────────────────────┤
│   Adapters 实现 (变更层)            │  ← DSH 更新只换这里
└─────────────────────────────────────┘
```

采用 **Port & Adapter（六边形架构）**，追踪 DSH 的「抽象/契约」而非实现，DSH 更新时只更新对应 adapter。详见 [架构设计文档](docs/architecture.md) 与 [DSH 兼容矩阵](docs/dsh_compat.md)。

## 📁 项目结构

```
LocalFlow AI/
├── .github/workflows/     # GitHub Actions：三平台自动打包 + Release
├── backend/               # Python 后端（FastAPI）
│   ├── localflow/
│   │   ├── ports/         # 稳定领域接口
│   │   ├── adapters/      # 具体实现（Ollama / 硬件监控…）
│   │   ├── core/          # 核心业务（Wizard 部署引导、设置注册表…）
│   │   ├── api/           # FastAPI 路由（含 openai.py 对外 /v1）
│   │   ├── hardware_lib.py# 硬件识别库（部署防失败）
│   │   └── main.py        # FastAPI 入口
│   ├── run_server.py      # PyInstaller 打包启动入口
│   ├── localflow-backend.spec
│   └── requirements.txt
├── desktop/               # Electron 桌面端
│   ├── electron-builder.yml
│   ├── scripts/generate-icons.py   # 发布图标生成脚本
│   ├── build/             # 打包资源（icon.icns / icon.ico / icon.png）
│   └── src/
│       ├── main/          # 主进程（含打包后自动拉起后端）
│       └── renderer/      # 渲染进程
├── dsh-poc/               # DeepSeek Harness 集成（provider 配置、模型同步器）
├── docs/                  # 文档
└── README.md
```

## 🚀 快速开始

**前置：本机需运行 [Ollama](https://ollama.com)**（客户端首次启动会自动检测，缺失时引导一键安装；也可 `ollama serve` 手动启动）。

### 方式一：源码运行

```bash
# 1. 启动后端（端口 8765）
cd backend
pip install -r requirements.txt
uvicorn localflow.main:app --port 8765

# 2. 启动桌面端（对话页内置 DeepSeek Harness，需 dsh 运行在 8080）
cd desktop
npm install
npm run dev

# 3. 浏览器直接访问（无需 Electron）
#    http://127.0.0.1:8899  （前端由 python -m http.server 托管时）
```

### 方式二：下载安装包（推荐小白）

从 [GitHub Releases](https://github.com/Near-Ink/LocalFlow-AI/releases) 下载对应平台安装包，双击安装即可。**Python 后端、DeepSeek Harness（dsh）与便携 Node 运行时均已内嵌**。首次启动会自动检测 dsh 是否已装：未装则解包内置安装包（对话页带进度条提示），已装但版本不符则自动更新到最新，已是最新则直接就绪——整个过程无需配环境、无需另行安装 dsh / Node（真正的零安装）。

| 平台 | 文件（v0.2.1） | 适用 |
|------|---------------|------|
| 🍎 macOS · Apple Silicon | `LocalFlow AI-0.2.1-macOS-arm64.dmg` | M1/M2/M3/M4 等新 Mac |
| 🍎 macOS · Intel | `LocalFlow AI-0.2.1-macOS-x64.dmg` | Intel 芯片 Mac |
| 🪟 Windows | `LocalFlow AI-Setup-0.2.1-Windows-x64.exe` | Windows 10/11 64 位 |
| 🐧 Linux | `LocalFlow AI-0.2.1-Linux-x64.AppImage` | 主流 x86_64 发行版 |

> 💡 v0.2.0 起文件名已直接带平台标识（如 `LocalFlow AI-0.2.1-macOS-arm64.dmg`），按上表对应下载即可。
> 🍎 **Intel Mac 用户**：v0.2.0 起同时提供 Apple Silicon（arm64）与 Intel（x64）两种 dmg，直接下载对应文件即可。

> [!WARNING]
> **macOS 打不开？** 安装包**暂未做 Apple 开发者签名 / 公证**（测试阶段暂不购买账号），首次打开会被 Gatekeeper 拦截，弹窗提示 **「LocalFlow AI 已损坏，无法打开，你应该将它移到废纸篓」** —— **这不是包损坏，千万不要点「移到废纸篓」**。按上方「⚠️ macOS 用户必读」任选其一即可正常使用：
> 1. **右键** `LocalFlow AI.app` → 「**打开**」→ 再次确认；
> 2. 终端执行：`sudo xattr -cr "/Applications/LocalFlow AI.app"` 后重试。
> 正式签名 + 公证将在后续版本补齐。

## 🔌 对外 API（让其他 Agent 使用本机模型）

桌面端顶部「🔌 对外 API」页可查看接口信息、生成 / 清除 API Key、复制接入示例。

```bash
# 接口地址：http://127.0.0.1:8765/v1（OpenAI 兼容）
curl http://127.0.0.1:8765/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <YOUR_API_KEY>" \
  -d '{"model":"llama3.1:8b","messages":[{"role":"user","content":"你好"}]}'
```

- 配置了 API Key 后调用需带 `Authorization: Bearer <Key>`；清除 Key 则本机免鉴权。
- Python：`OpenAI(base_url="http://127.0.0.1:8765/v1", api_key="...")`。
- Cursor / Cline / Open WebUI 等以「OpenAI 兼容 Provider」方式添加 Base URL + Key 即可。

## 📦 构建与打包

```bash
# 1. 打包 Python 后端（PyInstaller）
cd backend && pyinstaller --clean --noconfirm localflow-backend.spec

# 2. 生成发布图标（可选，desktop/build 已有占位图标）
python3 desktop/scripts/generate-icons.py --source logo.png

# 3. 打包桌面端（electron-builder）
cd desktop && npm install && npm run build:mac   # 或 build:win / build:linux
```

推送到 GitHub 后，推送 `v*` tag 即触发 [GitHub Actions](.github/workflows/build.yml) 自动构建四路产物并发布 Release；也可在 Actions 页手动触发。

## 🔌 插件开发

详见 [插件开发指南](docs/plugin-dev.md)。

## 📄 开源协议

- **核心引擎**：Apache-2.0（开源）
- **企业级增值模块**：商业授权（闭源）
- **第三方组件**：DeepSeek Harness（[@deepseek-ai/dsh](https://www.npmjs.com/package/@deepseek-ai/dsh)）以 **MIT** 协议随安装包一并分发，实现「下载即用、零安装」；其许可证文件随产物位于安装包 `resources/dsh/LICENSE`，打包流程见 `desktop/scripts/bundle-dsh.mjs`

## 🛣️ 路线图

- **阶段 A**：地基 — 工程底座 + 微内核 + 模型接入 + Wizard + 事件 + SubAgent
- **阶段 B**：缓存 + 云端协同 — L1/L2 缓存 + 单云端协同 + 用量上报
- **阶段 C**：桌面端 + 画布 + 硬件监控 — Electron + 核心 UI + 硬件监控 + 工作流 + 回溯
- **阶段 D**：发布 + 社区 — OpenAI API Layer + 安装包 + 技术博客 + 社区基建

详见 [开发清单](localflow-ai-checklist/README.md)。
