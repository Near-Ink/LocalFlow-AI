"""本地后端启动入口（PyInstaller 打包用）

独立脚本，避免 PyInstaller 打包 uvicorn CLI 时的入口依赖问题。
运行后启动 FastAPI 应用并监听 127.0.0.1:8765（可用环境变量 LOCALFLOW_PORT 覆盖）。
"""

import os

import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("LOCALFLOW_PORT", "8765"))
    uvicorn.run(
        "localflow.main:app",
        host=os.environ.get("LOCALFLOW_HOST", "127.0.0.1"),
        port=port,
        log_level="info",
    )
