"""示例插件 — 欢迎插件

演示插件的基本结构：install / enable / disable / uninstall 生命周期。
实际插件可以注册工具、模型适配器、工作流节点等。
"""

from localflow.core.plugin import Plugin


class WelcomePlugin(Plugin):
    """欢迎插件 — 演示插件生命周期"""

    name = "welcome"
    version = "0.1.0"
    description = "示例插件：演示插件生命周期与注册机制"

    def install(self):
        print("[plugin:welcome] 已安装 — 欢迎使用 LocalFlow AI 插件系统")

    def enable(self):
        super().enable()
        print("[plugin:welcome] 已启用")

    def disable(self):
        super().disable()
        print("[plugin:welcome] 已禁用")

    def uninstall(self):
        print("[plugin:welcome] 已卸载")