"""硬件监控 API"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List

from ..deps import get_app


router = APIRouter(prefix="/api/hardware", tags=["hardware"])


class GPUInfoResp(BaseModel):
    index: int
    name: str
    vram_total_mb: float
    vram_used_mb: float
    utilization: float
    temperature: float
    power_w: float


class HardwareResp(BaseModel):
    cpu_usage: float
    cpu_cores: int
    mem_total_mb: float
    mem_used_mb: float
    gpus: List[GPUInfoResp]
    platform: str
    timestamp: float
    load_1m: float = 0.0
    load_5m: float = 0.0
    load_15m: float = 0.0
    disk_total_mb: float = 0.0
    disk_used_mb: float = 0.0
    disk_free_mb: float = 0.0
    uptime_s: float = 0.0


@router.get("/snapshot", response_model=HardwareResp)
async def snapshot(app=Depends(get_app)):
    """获取硬件状态快照"""
    info = await app.hardware.snapshot()
    return HardwareResp(
        cpu_usage=info.cpu_usage,
        cpu_cores=info.cpu_cores,
        mem_total_mb=info.mem_total_mb,
        mem_used_mb=info.mem_used_mb,
        gpus=[GPUInfoResp(
            index=g.index, name=g.name,
            vram_total_mb=g.vram_total_mb, vram_used_mb=g.vram_used_mb,
            utilization=g.utilization, temperature=g.temperature, power_w=g.power_w,
        ) for g in info.gpus],
        platform=info.platform,
        timestamp=info.timestamp,
        load_1m=info.load_1m,
        load_5m=info.load_5m,
        load_15m=info.load_15m,
        disk_total_mb=info.disk_total_mb,
        disk_used_mb=info.disk_used_mb,
        disk_free_mb=info.disk_free_mb,
        uptime_s=info.uptime_s,
    )


@router.get("/detect")
async def detect(app=Depends(get_app)):
    """检测 GPU 列表（用于推荐模型）"""
    gpus = await app.hardware.detect_gpus()
    return {
        "nvidia_available": app.hardware.is_nvidia_available(),
        "apple_silicon": app.hardware.is_apple_silicon(),
        "gpus": [{"index": g.index, "name": g.name, "vram_total_mb": g.vram_total_mb} for g in gpus],
    }