# 插件开发指南

LocalFlow AI 的所有扩展能力都通过插件系统实现。
本文档介绍如何开发第一个插件。

## 插件结构

最简单的插件是一个 `.py` 文件，放在 `backend/plugins/` 目录下：

```python
from localflow.core.plugin import Plugin

class HelloPlugin(Plugin):
    name = "hello"
    version = "0.1.0"
    description = "我的第一个插件"

    def install(self):
        print("Hello 插件已安装！")

    def enable(self):
        super().enable()
        print("Hello 插件已启用")

    def disable(self):
        super().disable()
        print("Hello 插件已禁用")

    def uninstall(self):
        print("Hello 插件已卸载")
```

## 生命周期

| 方法 | 调用时机 | 用途 |
|------|---------|------|
| `install()` | 插件被加载时 | 注册工具、适配器、工作流节点等 |
| `enable()` | 插件启用时 | 启动后台任务、建立连接等 |
| `disable()` | 插件禁用时 | 停止后台任务、释放资源 |
| `uninstall()` | 插件卸载时 | 清理数据、移除注册 |

## 访问应用

插件可以通过 `self.app` 访问 `LocalFlowApp` 实例，从而使用所有核心能力：

```python
class MyPlugin(Plugin):
    name = "my_plugin"

    def install(self):
        # 访问引擎
        engine = self.app.engine

        # 访问缓存
        cache = self.app.cache

        # 访问事件存储
        events = self.app.event_store

        # 访问硬件监控
        hw = self.app.hardware
```

## 插件类型

### 1. 工具插件
注册新的工具调用（如搜索、计算、文件操作等）。

### 2. 模型适配器插件
接入新的推理后端（如 Anthropic、Gemini、本地 vLLM 等）。

### 3. 工作流节点插件
为可视化工作流添加新的节点类型。

### 4. 缓存适配器插件
提供新的缓存后端（如 Redis、向量数据库）。

## 发布

插件可以：
1. 放在 `plugins/` 目录下随主项目发布
2. 打包为独立 pip 包，用户安装后自动注册
3. 通过插件市场一键安装（进阶版）

## 最佳实践

- **不要硬编码路径**：用 `self.app.config` 获取配置
- **做好资源清理**：在 `disable()` 和 `uninstall()` 中释放所有资源
- **异常捕获**：插件异常不应影响主程序
- **提供文档**：每个插件附带 README 说明用法