# LocalFlow AI Backend

本地 AI 桌面平台后端 — Python + FastAPI（Port & Adapter 六边形架构）。

> 注：子任务调度为自研 LLM 拆分骨架（`adapters/llm_split_scheduler.py`，文件名历史沿用 langgraph），
> 并未依赖 LangGraph 库；LangGraph 作为进阶阶段的可选增强，非当前依赖。

## 架构

```
localflow/
├── ports/         # 稳定领域接口层（Port）
│   ├── engine.py       LLM 引擎接口
│   ├── cache.py        缓存接口
│   ├── event.py        事件溯源接口
│   ├── scheduler.py    子任务调度接口
│   └── hardware.py     硬件监控接口
├── adapters/      # 具体实现层（Adapter）
│   ├── ollama_engine.py     Ollama 本地推理
│   ├── openai_engine.py     OpenAI 兼容云端
│   ├── sqlite_cache.py      SQLite 缓存
│   ├── sqlite_event.py      SQLite 事件存储
│   ├── llm_split_scheduler.py  LLM 拆分调度器（骨架）
│   └── system_hardware.py   系统硬件监控
├── core/          # 核心业务逻辑
│   ├── app.py           应用主容器（装配所有 adapter）
│   ├── plugin.py        插件微内核
│   ├── session.py       会话管理器
│   └── wizard.py        部署引导 Wizard
├── api/           # FastAPI 路由
├── db/            # 数据库 schema
├── config.py      # 配置
├── deps.py        # 依赖注入
└── main.py        # FastAPI 入口
```

**核心原则：Port & Adapter（六边形架构）**
- 业务逻辑只依赖 `ports/`（抽象接口）
- 具体实现都在 `adapters/`，通过 `LocalFlowApp` 装配
- DSH 更新、后端更换时，只增改 adapter，不动 ports 和 core

## 快速开始

```bash
cd backend
pip install -r requirements.txt

# 启动
uvicorn localflow.main:app --reload --port 8765
```

打开 http://localhost:8765/docs 查看 API 文档。

## 配置

通过环境变量配置：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `LOCALFLOW_DATA_DIR` | 数据目录 | `~/.localflow` |
| `LOCALFLOW_OLLAMA_URL` | Ollama 地址 | `http://localhost:11434` |
| `LOCALFLOW_DEFAULT_MODEL` | 默认模型 | `qwen2.5:7b-instruct-q4_K_M` |
| `LOCALFLOW_CLOUD_BASE` | 云端 API Base | 空 |
| `LOCALFLOW_CLOUD_KEY` | 云端 API Key | 空 |
| `LOCALFLOW_CLOUD_MODEL` | 云端模型名 | 空 |
| `LOCALFLOW_CACHE` | 是否启用缓存 | `1` |
| `LOCALFLOW_PLUGIN_DIR` | 插件目录 | `plugins` |