# -*- mode: python ; coding: utf-8 -*-
"""LocalFlow AI 后端 PyInstaller 打包配置

用法（在 backend/ 目录）：
    pyinstaller localflow-backend.spec

产物：dist/localflow-backend/ 单目录可执行（含 Python 运行时），
Electron 启动时通过 extraResources 携带并 spawn。
"""
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

# 动态导入较重的依赖：FastAPI / uvicorn / pydantic / psutil 的隐藏模块
datas, binaries, hiddenimports = [], [], []

for pkg in ("uvicorn", "fastapi", "pydantic", "psutil", "httpx", "multipart", "sse_starlette", "starlette", "anyio", "websockets", "h11", "click"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

# localflow 包自身
lf_datas, lf_bins, lf_hidden = [], [], []
try:
    lf_datas, lf_bins, lf_hidden = collect_all("localflow")
except Exception:
    pass

a = Analysis(
    ["run_server.py"],
    pathex=[os.path.abspath(".")],
    binaries=binaries + lf_bins,
    datas=datas + lf_datas,
    hiddenimports=hiddenimports + lf_hidden + [
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "pandas", "PIL"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="localflow-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # Windows 下不弹黑窗；日志经 stdout 由 Electron 捕获
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name="localflow-backend",
)
