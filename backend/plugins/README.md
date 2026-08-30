# 插件目录

所有第三方插件放置于此，每个插件为一个 `.py` 文件或子目录。

## 插件结构

```python
from localflow.core.plugin import Plugin

class MyPlugin(Plugin):
    name = "my_plugin"
    version = "0.1.0"
    description = "我的第一个插件"

    def install(self):
        # 注册工具、适配器、工作流节点等
        pass

    def enable(self):
        pass

    def disable(self):
        pass

    def uninstall(self):
        pass
```

## 开发文档

详见 `docs/plugin-dev.md`。