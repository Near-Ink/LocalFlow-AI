"""硬件监控接口 — Port

提供 CPU / 内存 / GPU / 显存等硬件状态查询。
不同平台有不同 adapter 实现（Windows / macOS / Linux）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class GPUInfo:
    """GPU 信息"""
    index: int
    name: str = ""
    vram_total_mb: float = 0.0   # 总显存（MB）
    vram_used_mb: float = 0.0    # 已用显存（MB）
    utilization: float = 0.0     # 利用率百分比 0-100
    temperature: float = 0.0     # 温度（°C）
    power_w: float = 0.0         # 功耗（W）


@dataclass
class HardwareInfo:
    """硬件整体信息"""
    cpu_usage: float = 0.0       # CPU 利用率 0-100
    cpu_cores: int = 0           # CPU 核心数
    mem_total_mb: float = 0.0    # 总内存（MB）
    mem_used_mb: float = 0.0     # 已用内存（MB）
    gpus: List[GPUInfo] = field(default_factory=list)
    platform: str = ""           # 操作系统平台
    timestamp: float = 0.0       # 采样时间戳
    # —— 扩展指标（跨平台，psutil 可得）——
    load_1m: float = 0.0         # 1 分钟负载
    load_5m: float = 0.0         # 5 分钟负载
    load_15m: float = 0.0        # 15 分钟负载
    disk_total_mb: float = 0.0   # 磁盘总容量（MB）
    disk_used_mb: float = 0.0    # 磁盘已用（MB）
    disk_free_mb: float = 0.0    # 磁盘剩余（MB）
    uptime_s: float = 0.0        # 系统开机时长（秒）


class HardwareMonitor(ABC):
    """硬件监控 Port"""

    name: str = "base"

    @abstractmethod
    async def snapshot(self) -> HardwareInfo:
        """获取当前硬件状态快照"""
        ...

    @abstractmethod
    async def detect_gpus(self) -> List[GPUInfo]:
        """检测 GPU（启动时调用一次，用于推荐模型）"""
        ...

    @abstractmethod
    def is_nvidia_available(self) -> bool:
        """是否有 NVIDIA GPU"""
        ...

    @abstractmethod
    def is_apple_silicon(self) -> bool:
        """是否 Apple Silicon"""
        ...