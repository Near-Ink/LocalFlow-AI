"""部署引导 Wizard API"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List

from ..deps import get_app


router = APIRouter(prefix="/api/wizard", tags=["wizard"])


class PullReq(BaseModel):
    model_id: str


class ModelFitReq(BaseModel):
    model: str
    quant: str = ""


class RecommendationResp(BaseModel):
    model_id: str
    name: str
    size_gb: float
    quant: str
    description: str
    recommended: bool
    reason: str
    score: float = 0.0
    context: int = 0
    vision: bool = False  # 是否支持视觉（看图）


def _score_model(rec, mem_gb: float) -> float:
    """0-5 推荐度：官方推荐=5；否则按模型占用 vs 可用内存裕量打分"""
    if getattr(rec, "recommended", False):
        return 5.0
    need_gb = getattr(rec, "size_gb", 0) or 0
    ratio = mem_gb / max(need_gb * 1.35, 0.1) if need_gb > 0 else 0.0
    neg = ("不足" in str(getattr(rec, "reason", "")) or
           "不够" in str(getattr(rec, "reason", "")) or
           "放不下" in str(getattr(rec, "reason", "")) or
           "无法" in str(getattr(rec, "reason", "")) or
           "insufficient" in str(getattr(rec, "reason", "")))
    if neg or ratio < 0.5:
        return 0.0
    if ratio >= 1.0:
        return 3.5
    if ratio >= 0.7:
        return 2.0
    return 1.0


class WizardResp(BaseModel):
    hardware: dict
    recommendations: List[RecommendationResp]
    has_nvidia: bool
    has_apple_silicon: bool
    can_run_local: bool
    fallback_suggestion: str
    install_dir: str = ""
    data_dir: str = ""
    default_dir: str = ""
    hw_match: dict = {}  # 硬件库识别结果（命中/兜底），供前端展示硬件识别状态


@router.get("/analyze", response_model=WizardResp)
async def analyze(app=Depends(get_app)):
    """检测硬件并给出按推荐度排序的模型推荐方案"""
    result = await app.wizard.analyze()

    mem_gb = (result.hardware.mem_total_mb or 0) / 1024.0
    # 附带上下文(已装模型时实时查询,否则 0)并打分
    async def _with_score(r):
        ctx = 0
        try:
            ctx = await app.local_engine.get_context_length(r.model_id) or 0
        except Exception:
            ctx = 0
        return (r, _score_model(r, mem_gb), ctx)

    try:
        scored = [await _with_score(r) for r in result.recommendations]
    except Exception:
        scored = [(r, _score_model(r, mem_gb), 0) for r in result.recommendations]

    scored.sort(key=lambda x: (-x[1], x[0].size_gb, x[0].model_id))

    st = app.settings_status
    return WizardResp(
        hardware={
            "cpu_usage": result.hardware.cpu_usage,
            "cpu_cores": result.hardware.cpu_cores,
            "mem_total_mb": result.hardware.mem_total_mb,
            "mem_used_mb": result.hardware.mem_used_mb,
            "gpus": [{"index": g.index, "name": g.name, "vram_total_mb": g.vram_total_mb} for g in result.hardware.gpus],
            "platform": result.hardware.platform,
        },
        recommendations=[
            RecommendationResp(
                model_id=r.model_id, name=r.name, size_gb=r.size_gb,
                quant=r.quant, description=r.description,
                recommended=r.recommended, reason=r.reason, score=score, context=ctx, vision=r.vision,
            ) for (r, score, ctx) in scored
        ],
        has_nvidia=result.has_nvidia,
        has_apple_silicon=result.has_apple_silicon,
        can_run_local=result.can_run_local,
        fallback_suggestion=result.fallback_suggestion,
        install_dir=st["install_dir"],
        data_dir=st["data_dir"],
        default_dir=st["default_dir"],
        hw_match=result.hw_match,
    )


@router.get("/ollama-status")
async def ollama_status(app=Depends(get_app)):
    """检查 Ollama 服务状态"""
    return await app.wizard.check_ollama()


@router.post("/model-fit")
async def model_fit(req: ModelFitReq, app=Depends(get_app)):
    """按当前硬件评估一个模型能否在本地运行，并给出建议"""
    return await app.wizard.estimate_model_fit(req.model.strip(), req.quant)


@router.post("/pull")
async def pull(req: PullReq, app=Depends(get_app)):
    """一键下载模型到本地 Ollama"""
    from ..adapters.ollama_engine import OllamaEngine
    engine = OllamaEngine()
    if not await engine.health():
        # 尝试用应用内已装配的 engine（可能指向自定义地址）
        engine = app.engine
        if not await engine.health():
            return {"status": "error", "error": "Ollama 服务不可用，请先在系统终端启动 Ollama。"}
    result = await engine.pull(req.model_id)
    return result