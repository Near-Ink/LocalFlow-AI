"""LocalFlow AI — 本地 AI 桌面平台核心包

架构原则：Port & Adapter（六边形架构）
- ports/     领域接口（稳定层，不随 DSH/底层库更新而变化）
- adapters/  具体实现（变更层，DSH 更新时只换 adapter）
- core/      核心业务逻辑（微内核、会话、部署引导）
- api/       FastAPI 路由层
- db/        数据库与 schema
"""

__version__ = "0.2.4"
__app_name__ = "LocalFlow AI"