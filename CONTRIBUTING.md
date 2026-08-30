# 贡献指南（Contributing）

感谢你关注 LocalFlow AI！这是一个面向家用 PC 的本地 + 云端混合 AI 工作台。

## 开发环境

```bash
# 后端（端口 8765）
cd backend
pip install -r requirements.txt
uvicorn localflow.main:app --port 8765

# 桌面端（需先启动 dsh，监听 8080）
cd desktop
npm install
npm run dev
```

- 后端需要 Ollama 在 `http://localhost:11434` 运行（或自行安装 `ollama`）。
- 对话与 Agent 能力由 DeepSeek Harness（dsh）提供，需另起 `dsh` 服务（默认 8080）。
  若 dsh 不可达，桌面端对话页会显示安装/启动引导，而非白屏。

## 分支与提交

- 主分支：`main`
- 功能开发请用 `feat/xxx` 分支，发 PR 到 `main`
- 提交信息建议遵循 [Conventional Commits](https://www.conventionalcommits.org/)
- 发版：推送 `v*` tag（如 `v0.1.0`）会触发 GitHub Actions 构建四路安装包并发布 Release

## 代码约定

- 后端遵循 Port & Adapter 架构：`ports/` 是稳定抽象层，**不要**在核心层直接依赖具体实现
- 新能力优先走插件（`backend/plugins/`）或 feature flag
- 缓存 Key、事件、子任务协议使用本地领域模型命名，不硬编码 dsh 私有字段

## 安全

- **不要**将后端绑定到 `0.0.0.0` 且免鉴权地发布/部署
- 跨域（CORS）仅放行本机可信源；如需局域网共享，必须先设置 `LOCALFLOW_API_KEY`
- 报告安全漏洞请私信维护者，勿公开 Issue

## 测试

```bash
cd backend && pip install pytest && pytest tests/
```

CI 会在每次 PR 与发版时运行构建与基础测试。
