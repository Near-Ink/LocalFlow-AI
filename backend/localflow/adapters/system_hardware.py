"""系统硬件监控适配器

跨平台硬件信息采集：
- CPU / 内存：psutil（跨平台）
- GPU：优先 pynvml（NVIDIA），其次 Apple Silicon 系统命令，再其次空实现

MVP 阶段先做基础采集，后续逐步增强。
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import time
from typing import List, Optional

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

        # NVIDIA：优先进程内 pynvml（NVML）；失败则退回 nvidia-smi CLI 兜底
        try:
            import pynvml
            self._pynvml = pynvml
            pynvml.nvmlInit()
            self._nvidia_available = pynvml.nvmlDeviceGetCount() > 0
        except Exception:
            self._nvidia_available = False
        # nvidia-smi 兜底路径：即便 NVML 进程内初始化失败（打包后端常见），
        # 只要驱动在，nvidia-smi 仍能给出型号/显存/利用率，保证「检测得到设备」
        self._nvidia_smi = self._find_nvidia_smi()

        # Apple Silicon
        if self._platform == "Darwin":
            try:
                out = subprocess.run(
                    ["sysctl", "-n", "machdep.cpu.brand_string"],
                    capture_output=True, text=True, timeout=2,
                )
                if "Apple" in out.stdout:
                    self._apple_silicon = True
            except Exception:
                pass

    def _find_nvidia_smi(self) -> Optional[str]:
        """定位 nvidia-smi 可执行文件（驱动自带，不依赖 NVML 进程内初始化）。"""
        for cand in ("nvidia-smi", "nvidia-smi.exe"):
            p = shutil.which(cand)
            if p:
                return p
        # Windows 常见固定路径（PATH 未含时兜底）
        if self._platform == "Windows":
            base = os.environ.get("ProgramFiles") or r"C:\Program Files"
            cand = os.path.join(base, "NVIDIA Corporation", "NVSMI", "nvidia-smi.exe")
            if os.path.exists(cand):
                return cand
        return None

    def _detect_nvidia_smi(self) -> List[GPUInfo]:
        """用 nvidia-smi 解析 GPU 列表（pynvml 失败时的兜底，覆盖 Windows/Linux）。"""
        if not self._nvidia_smi:
            return []
        try:
            out = subprocess.run(
                [self._nvidia_smi,
                 "--query-gpu=index,name,memory.total,memory.used,utilization.gpu,temperature.gpu,power.draw",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
            ).stdout
            gpus: List[GPUInfo] = []
            for line in out.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) < 7:
                    continue
                try:
                    gpus.append(GPUInfo(
                        index=int(parts[0]),
                        name=parts[1],
                        vram_total_mb=float(parts[2]),
                        vram_used_mb=float(parts[3]),
                        utilization=float(parts[4]),
                        temperature=float(parts[5]),
                        power_w=float(parts[6]),
                    ))
                except (ValueError, TypeError):
                    continue
            return gpus
        except Exception:
            return []

    def is_nvidia_available(self) -> bool:
        # pynvml 进程内 NVML 可用，或 nvidia-smi CLI 兜底可用（驱动在即视为有 NVIDIA）
        return self._nvidia_available or bool(self._nvidia_smi)

    def is_apple_silicon(self) -> bool:
        return self._apple_silicon

    async def detect_gpus(self) -> List[GPUInfo]:
        # 1) 进程内 NVML（最实时：利用率/温度/功耗可逐秒拉取）
        if self._nvidia_available and self._pynvml:
            gpus = []
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
                if gpus:
                    return gpus
            except Exception:
                pass
        # 2) nvidia-smi 兜底：NVML 初始化失败（打包后端常见）时仍能识别设备
        smi = self._detect_nvidia_smi()
        if smi:
            return smi
        # 3) Apple Silicon 统一内存：无独立显存，视总内存为共享显存池
        if self._apple_silicon:
            return [GPUInfo(
                index=0,
                name=self._apple_gpu_name(),
                vram_total_mb=self._mem_total_mb(),
                vram_used_mb=self._mem_used_mb(),
            )]
        return []

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

    def _apple_gpu_name(self) -> str:
        """用 system_profiler 取真实 GPU/SoC 型号名（如 Apple M4 / M4 Pro）。"""
        try:
            import re, subprocess
            out = subprocess.run(
                ["system_profiler", "SPDisplaysDataType"],
                capture_output=True, text=True, timeout=3,
            ).stdout
            m = re.search(r"Apple\s+M\d+\s*(?:Pro|Max|Ultra)?", out)
            if m:
                return m.group(0)
        except Exception:
            pass
        return self._chip_name() or "Apple Silicon"

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