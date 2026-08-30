"""配置模块

从环境变量和配置文件读取配置，支持热更新预留。
MVP 阶段用 dataclass + 环境变量，后续可换 pydantic-settings。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .core.app import AppConfig


def _read_runtime_json(cfg_path: Path) -> dict:
    if not cfg_path.exists():
        return {}
    try:
        return json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_runtime_cloud(cfg_path: Path, config: AppConfig) -> AppConfig:
    """从运行时 config.json 读取持久化的云端配置覆盖默认值。

    多绑定优先：读 cloud_bindings 列表 + cloud_active_id；兼容旧单套
    (cloud_api_*) 迁移为首个绑定。
    """
    overrides = _read_runtime_json(cfg_path)
    old_present = all(k in overrides for k in ("cloud_api_base", "cloud_api_key", "cloud_model"))

    raw_bindings = overrides.get("cloud_bindings") or []
    bindings = [b for b in raw_bindings if isinstance(b, dict)] if isinstance(raw_bindings, list) else []
    if bindings:
        config.cloud_bindings = bindings
    elif old_present and overrides.get("cloud_api_base"):
        # 旧单套 → 迁移为单条绑定
        config.cloud_bindings = [{
            "id": "legacy",
            "provider": "",
            "base_url": overrides["cloud_api_base"],
            "api_key": overrides["cloud_api_key"],
            "model": overrides["cloud_model"],
            "context_size": int(overrides.get("cloud_context_size") or 0),
        }]

    config.cloud_active_id = overrides.get("cloud_active_id") or ""

    # 读回持久化的标量设置（Agent / /api/settings 修改后重启仍在；优先于环境变量）
    if overrides.get("default_model"):
        config.default_model = str(overrides["default_model"])
    if "enable_cache" in overrides:
        config.enable_cache = bool(overrides["enable_cache"])
        os.environ.setdefault("LOCALFLOW_CACHE", "1" if config.enable_cache else "0")
    if "enable_agent" in overrides:
        config.enable_agent = bool(overrides["enable_agent"])
        os.environ.setdefault("LOCALFLOW_ENABLE_AGENT", "1" if config.enable_agent else "0")
    if overrides.get("api_key") is not None:
        config.api_key = str(overrides["api_key"])
    if "openai_identity_inject" in overrides:
        config.openai_identity_inject = bool(overrides["openai_identity_inject"])

    # 保持旧单套字段与激活绑定一致，兼容其余读取方
    if config.cloud_bindings:
        ab = next((b for b in config.cloud_bindings if b.get("id") == config.cloud_active_id), config.cloud_bindings[0])
        config.cloud_api_base = ab.get("base_url", "")
        config.cloud_api_key = ab.get("api_key", "")
        config.cloud_model = ab.get("model", "")
        config.cloud_context_size = int(ab.get("context_size") or 0)
    return config


def load_config() -> AppConfig:
    """从环境变量 + 运行时 config.json 加载配置，并按 install_dir 定位数据目录"""
    base = Path(os.environ.get("LOCALFLOW_DATA_DIR", Path.home() / ".localflow"))
    cfg_path = base / "config.json"

    # 解析安装目录：环境变量优先，其次 config.json
    runtime = _read_runtime_json(cfg_path)
    install_dir = (os.environ.get("LOCALFLOW_INSTALL_DIR", "") or "").strip()
    if not install_dir:
        install_dir = str(runtime.get("install_dir", "") or "").strip()

    if install_dir:
        data_dir = Path(install_dir) / ".localflow"
        try:
            data_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            data_dir = base
            install_dir = ""  # 目标不可写则回退默认目录
    else:
        data_dir = base

    config = AppConfig(
        data_dir=data_dir,
        install_dir=install_dir,
        config_file=str(cfg_path),
        ollama_base_url=os.environ.get("LOCALFLOW_OLLAMA_URL", "http://localhost:11434"),
        default_model=os.environ.get("LOCALFLOW_DEFAULT_MODEL", "llama3.1:8b"),
        cloud_api_base=os.environ.get("LOCALFLOW_CLOUD_BASE", ""),
        cloud_api_key=os.environ.get("LOCALFLOW_CLOUD_KEY", ""),
        cloud_model=os.environ.get("LOCALFLOW_CLOUD_MODEL", ""),
        cloud_context_size=int(os.environ.get("LOCALFLOW_CLOUD_CTX", "0") or 0),
        enable_cache=os.environ.get("LOCALFLOW_CACHE", "1") != "0",
        enable_agent=os.environ.get("LOCALFLOW_ENABLE_AGENT", "") not in ("", "0", "false", "False"),
        api_key=os.environ.get("LOCALFLOW_API_KEY", ""),
        plugin_dir=Path(os.environ.get("LOCALFLOW_PLUGIN_DIR", "plugins")),
    )
    # 远程模板源（社区广场）：LOCALFLOW_WORKFLOW_SOURCES=<JSON 数组 [{id,name,url}]>
    _sources_env = os.environ.get("LOCALFLOW_WORKFLOW_SOURCES", "").strip()
    if _sources_env:
        try:
            _parsed = json.loads(_sources_env)
            if isinstance(_parsed, list):
                config.workflow_sources = [s for s in _parsed if isinstance(s, dict)]
        except Exception:
            pass
    data_dir.mkdir(parents=True, exist_ok=True)
    # 云端配置始终从基础 config.json 读取
    return _load_runtime_cloud(cfg_path, config)