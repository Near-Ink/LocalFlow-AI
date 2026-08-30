# DSH 兼容矩阵 (dsh_compat)

> 追踪 DeepSeek Harness 各版本与 LocalFlow AI 的能力对齐状态。
> CI 监控 DSH release，新 release 触发「对齐评估」issue。

**核心原则：追踪 DSH 的抽象/契约，而非实现。同步发生在概念语义层。**

---

## 当前状态

| DSH 版本 | 评估日期 | 插件微内核 | 事件溯源 | SubAgent 调度 | L1 工具缓存 | L2 子任务缓存 | L3 KV 缓存 | L4 语义缓存 | 多 API 聚合 | 备注 |
|----------|----------|-----------|----------|---------------|-------------|---------------|-----------|------------|------------|------|
| DSH 参考（当前） | 2026.08 | ⚠️ 设计对齐 | ⚠️ 设计对齐 | ⚠️ 骨架实现 | ✅ 已实现 | ✅ 已实现 | ⏳ 预留 | ⏳ 预留 | ⏳ 规划中 | MVP 阶段对齐前两级 |

**状态说明：**
- ✅ 已对齐：功能实现且语义一致
- ⚠️ 部分对齐：有实现但细节需评估
- ⏳ 规划中：已纳入路线图，尚未实现
- ❌ 未对齐：存在已知差异

---

## 能力清单与对齐状态

### 1. 插件化微内核
- **DSH 概念**：Cordis 微内核，所有功能皆可插拔
- **本地实现**：`core/plugin.py` + `PluginManager`，支持 install/enable/disable/uninstall 生命周期
- **对齐状态**：⚠️ 设计对齐（MVP 阶段热加载/沙箱隔离待增强）
- **差异点**：DSH 使用 Cordis 框架，本地为自研轻量实现

### 2. Session Event 全链路回溯
- **DSH 概念**：Event Sourcing 事件溯源，全链路可回放
- **本地实现**：`ports/event.py` 接口 + `SQLiteEventStore` 适配器
- **对齐状态**：⚠️ 基础对齐（确定性回放待实现）
- **Schema 稳定性**：使用本地领域模型 `SessionEvent`，payload 为 JSON，不硬编码 DSH 字段 ✅

### 3. SubAgent 父子任务调度
- **DSH 概念**：嵌套、并行 spawn、结果复用
- **本地实现**：`ports/scheduler.py` 接口 + `LangGraphScheduler` 骨架
- **对齐状态**：⚠️ 骨架对齐（并行 spawn / 结果复用待增强）
- **进阶计划**：深度集成 LangGraph 图执行引擎

### 4. 四级缓存
| 层级 | DSH 概念 | 本地实现 | 状态 |
|------|---------|---------|------|
| L1 | 工具调用缓存 | `SQLiteCache` + `namespace=tool` | ✅ |
| L2 | 子任务缓存 | `SQLiteCache` + `namespace=subtask` | ✅ |
| L3 | KV 会话缓存 | ports 已定义，adapter 预留 | ⏳ |
| L4 | 语义向量缓存 | ports 已定义，adapter 预留 | ⏳ |

### 5. 智能调度
- 难度路由：⏳ 规划中（MVP 用规则路由）
- 多 API 并行广播：⏳ 规划中（进阶版）
- 方案聚合：⏳ 规划中（进阶版）
- 预算熔断：⚠️ 基础实现

---

## DSH 同步流程

```
DSH 发布新版本
    ↓
CI 检测到新 tag
    ↓
自动创建「DSH 对齐评估」issue
    ├── 阅读 changelog / release notes
    ├── 逐项更新此兼容矩阵
    ├── 识别需要新增/更新的 adapter
    └── 排期到对应里程碑
    ↓
开发：新增/修改 adapter（不动 ports）
    ↓
测试：回归验证 + 兼容性验证
    ↓
发版：更新 dsh_compat 文档
```

---

## Port & Adapter 设计要点

- **ports/ 是稳定层**：除非领域模型本身变化，否则不改
- **adapters/ 是变更层**：DSH 更新时只改对应 adapter
- **Schema 稳定优先**：缓存 Key、Event、子任务协议用本地领域命名
- **新能力走 feature flag**：可灰度、可回退，不污染主链路