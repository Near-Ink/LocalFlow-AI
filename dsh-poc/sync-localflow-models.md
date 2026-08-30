# LocalFlow → dsh 本地部署模型同步器

把 LocalFlow（Ollama）真实已安装的模型，动态同步进 DeepSeek Harness 的
`local-flow` provider 模型列表，让 dsh 输入区的模型选择器实时出现新模型。

## 用法

```bash
# 同步一次（推荐在 ollama pull 新模型后执行）
node sync-localflow-models.mjs

# 常驻监听，每 60 秒自动同步（ollama pull 后自动出现，无需手动）
node sync-localflow-models.mjs --watch

# 自定义间隔（秒）
node sync-localflow-models.mjs --watch --interval 300
```

## 原理

1. 读取 LocalFlow 后端 `GET /api/models`（即 Ollama 真实已装模型列表）。
2. 读取 dsh `settings.describe` 中 `llm-pi-ai` 命名空间的 `local-flow` provider。
3. 合并：已配置过的模型保留其 `contextWindow / maxTokens / 名称` 等元数据；
   新模型自动生成默认元数据（contextWindow 32768 / maxTokens 4096）。
4. 通过 `settings.update` 深合并写回 `providers.local-flow.models`，
   dsh 的模型目录 `llm.models` 实时刷新 —— **无需重启 dsh**。

## 效果

- dsh 输入区模型选择器（LocalFlow (本机) 分组）实时反映 Ollama 已装模型。
- 新增模型：`ollama pull xxx` → 运行同步 → 选择器里立即可选。
- 已配置模型的手动设置（如 contextWindow）不会被覆盖。

## 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `LOCALFLOW_API` | `http://127.0.0.1:8765` | LocalFlow 后端地址 |
| `DSH_WEB` | `http://127.0.0.1:8080` | dsh Web 地址 |

## 前提

- LocalFlow 后端已启动（8765），且 `/api/models` 可访问。
- dsh 已用 `--patch ./patch-localflow.yml` 启动（注册了 `local-flow` provider）。
