"""系统硬件监控适配器

跨平台硬件信息采集：
- CPU / 内存：psutil（跨平台）
- GPU：优先 pynvml（NVIDIA），其次 Apple Silicon 系统命令，再其次空实现

MVP 阶段先做基础采集，后续逐步增强。
"""

from __future__ import annotations

import platform
import time
from typing import List

from ..ports.hardware import GPUInfo, HardwareInfo, HardwareMonitor


class SystemHardwareMonitor(HardwareMonitor):
    """系统级硬件监控"""

    name = "system"

    def __init__(self):
        self._platform = platform.system()
        self._psutil = None
        self._pynvml = None
        self._nvidia_available = False
        self._apple_silicon = False
        self._init_backends()

    def _init_backends(self):
        # psutil
        try:
            import psutil
            self._psutil = psutil
        except ImportError:
            pass

        # NVIDIA
        try:
            import pynvml
            self._pynvml = pynvml
            pynvml.nvmlInit()
            self._nvidia_available = pynvml.nvmlDeviceGetCount() > 0
        except Exception:
            self._nvidia_available = False

        # Apple Silicon
        if self._platform == "Darwin":
            try:
                import subprocess
                out = subprocess.run(
                    ["sysctl", "-n", "machdep.cpu.brand_string"],
                    capture_output=True, text=True, timeout=2,
                )
                if "Apple" in out.stdout:
                    self._apple_silicon = True
            except Exception:
                pass

    def is_nvidia_available(self) -> bool:
        return self._nvidia_available

    def is_apple_silicon(self) -> bool:
        return self._apple_silicon

    async def detect_gpus(self) -> List[GPUInfo]:
        gpus = []
        if self._nvidia_available and self._pynvml:
            try:
                count = self._pynvml.nvmlDeviceGetCount()
                for i in range(count):
                    handle = self._pynvml.nvmlDeviceGetHandleByIndex(i)
                    name = self._pynvml.nvmlDeviceGetName(handle)
                    if isinstance(name, bytes):
                        name = name.decode("utf-8", errors="ignore")
                    mem_info = self._pynvml.nvmlDeviceGetMemoryInfo(handle)
                    gpus.append(GPUInfo(
                        index=i,
                        name=name,
                        vram_total_mb=mem_info.total / 1024 / 1024,
                        vram_used_mb=mem_info.used / 1024 / 1024,
                    ))
            except Exception:
                pass
        elif self._apple_silicon:
            # Apple Silicon 统一内存：App 层面无独立显存，视总内存为共享显存池
            gpus.append(GPUInfo(
                index=0,
                name=self._chip_name() or "Apple Silicon",
                vram_total_mb=self._mem_total_mb(),
                vram_used_mb=self._mem_used_mb(),
            ))
        return gpus

    def _chip_name(self) -> str:
        try:
            import subprocess
            out = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True, text=True, timeout=2,
            )
            return out.stdout.strip() or None
        except Exception:
            return None

    def _mem_total_mb(self) -> float:
        if self._psutil:
            return self._psutil.virtual_memory().total / 1024 / 1024
        return 0.0

    def _mem_used_mb(self) -> float:
        if self._psutil:
            return self._psutil.virtual_memory().used / 1024 / 1024
        return 0.0

    async def snapshot(self) -> HardwareInfo:
        info = HardwareInfo(platform=self._platform, timestamp=time.time())

        if self._psutil:
            ps = self._psutil
            info.cpu_usage = ps.cpu_percent(interval=0.1)
            info.cpu_cores = ps.cpu_count(logical=True) or 0
            mem = ps.virtual_memory()
            info.mem_total_mb = mem.total / 1024 / 1024
            info.mem_used_mb = mem.used / 1024 / 1024

            # —— 扩展指标（跨平台）——
            try:
                l1, l5, l15 = ps.getloadavg()
                info.load_1m, info.load_5m, info.load_15m = float(l1), float(l5), float(l15)
            except Exception:
                pass
            try:
                du = ps.disk_usage('/')
                info.disk_total_mb = du.total / 1024 / 1024
                info.disk_used_mb = du.used / 1024 / 1024
                info.disk_free_mb = du.free / 1024 / 1024
            except Exception:
                pass
            try:
                info.uptime_s = max(0.0, time.time() - ps.boot_time())
            except Exception:
                pass

        # GPU
        info.gpus = await self.detect_gpus()
        if self._nvidia_available and self._pynvml:
            for i, gpu in enumerate(info.gpus):
                try:
                    handle = self._pynvml.nvmlDeviceGetHandleByIndex(i)
                    # 利用率
                    util = self._pynvml.nvmlDeviceGetUtilizationRates(handle)
                    gpu.utilization = float(util.gpu)
                    # 温度
                    try:
                        temp = self._pynvml.nvmlDeviceGetTemperature(
                            handle, self._pynvml.NVML_TEMPERATURE_GPU
                        )
                        gpu.temperature = float(temp)
                    except Exception:
                        pass
                    # 功耗
                    try:
                        power = self._pynvml.nvmlDeviceGetPowerUsage(handle)
                        gpu.power_w = power / 1000.0
                    except Exception:
                        pass
                except Exception:
                    pass
        elif self._apple_silicon and info.gpus:
            # 统一内存模型：GPU 显存随系统内存实时变动，利用率以当前 CPU 负载近似
            gpu = info.gpus[0]
            gpu.vram_used_mb = self._mem_used_mb()
            gpu.vram_total_mb = self._mem_total_mb()
            gpu.utilization = info.cpu_usage

        return info