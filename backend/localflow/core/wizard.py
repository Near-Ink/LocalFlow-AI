"""部署引导 Wizard — 纯规则引擎，零模型依赖

负责首次启动时的环境检测与模型部署引导：
1. 检测硬件（CPU / GPU / 显存 / 内存 / 系统）
2. 根据硬件给出推荐方案
3. 一键下载模型
4. 可选：先绑定云端 API 兜底，后台静默下本地模型

**明确边界：DSH / Agent 不参与环境搭建。**
Wizard 是确定性的规则引擎，不调用任何 LLM。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

from ..ports.hardware import GPUInfo, HardwareInfo, HardwareMonitor
from .. import hardware_lib


@dataclass
class ModelRecommendation:
    """模型推荐项"""
    model_id: str
    name: str
    size_gb: float          # 预估占用显存/内存（GB）
    quant: str              # 量化级别：Q4_K_M / Q5_K_M / FP16 等
    description: str
    recommended: bool = False  # 是否为推荐方案
    reason: str = ""
    vision: bool = False  # 是否支持视觉（看图）


@dataclass
class WizardResult:
    """Wizard 检测结果"""
    hardware: HardwareInfo
    recommendations: List[ModelRecommendation] = field(default_factory=list)
    has_nvidia: bool = False
    has_apple_silicon: bool = False
    can_run_local: bool = True
    fallback_suggestion: str = ""
    hw_match: dict = field(default_factory=dict)  # 硬件库识别结果（命中/兜底）


# 预设模型库：热门可拉取的 Ollama 模型（tag 为其官方默认，Q4_K_M 量化占用为估算值）
# vram_gb 为运行所需内存/显存估算；后续可经插件 / 模型市场扩展
MODEL_LIBRARY = [
    {"id": "qwen3:0.6b", "name": "Qwen3-0.6B", "vram_gb": 0.6, "quant": "Q4_K_M",
     "desc": "极轻量，纯 CPU 也能飞跑，适合老设备或快速试玩"},
    {"id": "qwen2.5:3b", "name": "Qwen2.5-3B", "vram_gb": 2.1, "quant": "Q4_K_M",
     "desc": "轻量快速，中文日常对话够用，省显存"},
    {"id": "qwen2.5:7b", "name": "Qwen2.5-7B", "vram_gb": 4.9, "quant": "Q4_K_M",
     "desc": "通义千问 7B，中文强、通用场景甜点之选"},
    {"id": "qwen2.5:14b", "name": "Qwen2.5-14B", "vram_gb": 9.3, "quant": "Q4_K_M",
     "desc": "更强的中文模型，需要 9GB+ 内存"},
    {"id": "qwen3:32b", "name": "Qwen3-32B", "vram_gb": 20, "quant": "Q4_K_M",
     "desc": "旗舰中文模型，需要 20GB+ 内存"},
    {"id": "llama3.2:3b", "name": "Llama 3.2-3B", "vram_gb": 2.1, "quant": "Q4_K_M",
     "desc": "轻量通用，英文与简单任务快"},
    {"id": "llama3.1:8b", "name": "Llama 3.1-8B", "vram_gb": 5.4, "quant": "Q4_K_M",
     "desc": "Meta 官方 8B，英文与代码表现好"},
    {"id": "llama3.1:70b", "name": "Llama 3.1-70B", "vram_gb": 43, "quant": "Q4_K_M",
     "desc": "超大模型，需 40GB+ 内存，本机多半跑不动"},
    {"id": "gemma2:2b", "name": "Gemma 2-2B", "vram_gb": 1.6, "quant": "Q4_K_M",
     "desc": "Google 轻量模型，省资源"},
    {"id": "gemma2:9b", "name": "Gemma 2-9B", "vram_gb": 6.0, "quant": "Q4_K_M",
     "desc": "Google 9B，均衡通用"},
    {"id": "gemma3:27b", "name": "Gemma 3-27B", "vram_gb": 17, "quant": "Q4_K_M",
     "desc": "Google 多模态大模型，需 17GB+ 内存", "vision": True},
    {"id": "mistral:7b", "name": "Mistral-7B", "vram_gb": 4.6, "quant": "Q4_K_M",
     "desc": "欧洲开源 7B，英文与指令跟随好"},
    {"id": "mistral-nemo:12b", "name": "Mistral-Nemo 12B", "vram_gb": 7.8, "quant": "Q4_K_M",
     "desc": "12B 均衡款，偏大需注意内存"},
    {"id": "llama3.2-vision:11b", "name": "Llama 3.2 Vision 11B", "vram_gb": 8.0, "quant": "Q4_K_M",
     "desc": "Meta 视觉模型，可直接『看图』回答图片内容", "vision": True},
    {"id": "qwen2.5-vl:7b", "name": "Qwen2.5-VL 7B", "vram_gb": 6.7, "quant": "Q4_K_M",
     "desc": "通义视觉语言模型，看图、读图、图文理解强", "vision": True},
    {"id": "llava:7b", "name": "LLaVA 7B", "vram_gb": 4.7, "quant": "Q4_K_M",
     "desc": "经典开源视觉模型，显存友好", "vision": True},
    {"id": "minicpm-v:8b", "name": "MiniCPM-V 8B", "vram_gb": 7.0, "quant": "Q4_K_M",
     "desc": "新一代视觉语言模型，中文图文能力佳", "vision": True},
    {"id": "deepseek-r1:7b", "name": "DeepSeek-R1 7B", "vram_gb": 4.9, "quant": "Q4_K_M",
     "desc": "推理强化小模型，逻辑题擅长"},
    {"id": "deepseek-r1:14b", "name": "DeepSeek-R1 14B", "vram_gb": 9.3, "quant": "Q4_K_M",
     "desc": "更强推理，需 9GB+ 内存"},
    {"id": "deepseek-r1:32b", "name": "DeepSeek-R1 32B", "vram_gb": 20, "quant": "Q4_K_M",
     "desc": "旗舰推理模型，需 20GB+ 内存"},
    {"id": "phi4:14b", "name": "Phi-4 14B", "vram_gb": 9.5, "quant": "Q4_K_M",
     "desc": "微软 14B，数学/代码均衡"},
]


class DeploymentWizard:
    """部署引导向导（纯规则引擎）"""

    def __init__(self, hardware_monitor: HardwareMonitor):
        self.hw = hardware_monitor

    def compute_budget_gb(self, info: HardwareInfo, has_nvidia: bool, has_apple: bool) -> float:
        """根据硬件计算可用的模型内存预算（GB）"""
        if has_nvidia and info.gpus:
            gpu = info.gpus[0]
            available_vram_gb = (gpu.vram_total_mb / 1024) * 0.8  # 留 20% 余量
            return available_vram_gb
        if has_apple:
            # Apple Silicon 统一内存，按总内存一半估算
            return (info.mem_total_mb / 1024) * 0.5
        # 纯 CPU，用内存跑
        return (info.mem_total_mb / 1024) * 0.4

    @staticmethod
    def estimate_model_size(model, quant: str = "") -> Optional[float]:
        """按参数量粗估 Q4 占用（GB）；无法识别返回 None"""
        m = re.search(r"(\d+(?:\.\d+)?)\s*b", model or "", re.IGNORECASE)
        if not m:
            return None
        params_b = float(m.group(1))
        base = params_b * 0.7  # Q4_K_M 约 0.7GB / B
        q = (quant or "").upper()
        if any(x in q for x in ("FP16", "F32", "BF16")):
            base *= 2.0
        elif "Q8" in q:
            base *= 1.5
        elif "Q6" in q:
            base *= 1.4
        elif any(x in q for x in ("Q3", "IQ3")):
            base *= 0.9
        return round(base, 1)

    async def estimate_model_fit(self, model: str, quant: str = "") -> dict:
        """评估一个（可能自定义的）模型在当前硬件上能否运行，并给出建议"""
        info = await self.hw.snapshot()
        has_nvidia = self.hw.is_nvidia_available()
        has_apple = self.hw.is_apple_silicon()
        budget_gb = self.compute_budget_gb(info, has_nvidia, has_apple)

        hit = next((m for m in MODEL_LIBRARY if m["id"] == model), None)
        if hit:
            size_gb = float(hit["vram_gb"])
        else:
            size_gb = self.estimate_model_size(model, quant)

        if size_gb is None:
            return {
                "model": model, "size_gb": None, "budget_gb": round(budget_gb, 1),
                "level": "unknown",
                "suggestion": "无法识别该模型的参数量，无法精确评估。可先用小模型试试；以下模型已按您硬件推荐："
                              + "、".join(m["id"] for m in MODEL_LIBRARY if m["vram_gb"] <= budget_gb)[:200],
            }

        if size_gb <= budget_gb * 0.65:
            level, tip = "ok", "可流畅运行，推荐直接拉取"
        elif size_gb <= budget_gb:
            level, tip = "tight", "可运行，但内存偏紧，建议缩短上下文或选更小的量化版本"
        elif size_gb <= budget_gb * 1.35:
            level, tip = "heavy", "略超本地从容范围，可能明显变慢或需 CPU 卸载，建议改用云端 API"
        else:
            level, tip = "too_big", "本地放不下，建议绑定云端 API，或选择约 "
            tip += f"{budget_gb:.0f}GB 以内的小模型"

        return {
            "model": model, "size_gb": size_gb, "budget_gb": round(budget_gb, 1),
            "level": level,
            "suggestion": f"模型约需 {size_gb:.1f}GB 内存，您设备本地可用于模型的约 {budget_gb:.1f}GB。{tip}",
        }

    async def analyze(self) -> WizardResult:
        """检测硬件并生成推荐方案"""
        info = await self.hw.snapshot()
        has_nvidia = self.hw.is_nvidia_available()
        has_apple = self.hw.is_apple_silicon()

        # 硬件库识别：命中已知硬件库给出准确档位；未命中按通用规则兜底（绝不失败）
        hw_match = hardware_lib.to_dict(hardware_lib.classify(info, has_nvidia, has_apple, self._cpu_label()))

        memory_budget_gb = self.compute_budget_gb(info, has_nvidia, has_apple)

        # 生成推荐
        recs: List[ModelRecommendation] = []
        for m in MODEL_LIBRARY:
            fits = m["vram_gb"] <= memory_budget_gb
            rec = ModelRecommendation(
                model_id=m["id"],
                name=m["name"],
                size_gb=m["vram_gb"],
                quant=m["quant"],
                description=m["desc"],
                recommended=False,
                reason="" if fits else f"显存/内存不足（需要约 {m['vram_gb']:.1f}GB）",
                vision=bool(m.get("vision")),
            )
            recs.append(rec)

        # 标记推荐：选最大但能装下的 7B 级模型
        best = None
        for r in recs:
            if r.reason == "" and r.size_gb >= 4 and r.size_gb <= 8:
                if best is None or r.size_gb > best.size_gb:
                    best = r
        if best:
            best.recommended = True
            best.reason = "最佳平衡：显存占用合理，能力足够日常使用"

        # 判断是否能本地运行
        can_run_local = any(r.reason == "" for r in recs)
        fallback = ""
        if not can_run_local:
            fallback = "本地硬件不足以流畅运行推荐模型，建议先绑定云端 API（DeepSeek / GPT / 通义等），也可选择 3B 以下小模型尝试。"

        return WizardResult(
            hardware=info,
            recommendations=recs,
            has_nvidia=has_nvidia,
            has_apple_silicon=has_apple,
            can_run_local=can_run_local,
            fallback_suggestion=fallback,
            hw_match=hw_match,
        )

    @staticmethod
    def _cpu_label() -> str:
        """跨平台探测 CPU 型号标签（轻量、失败返回空串由硬件库兜底）"""
        try:
            import platform as _platform
            if _platform.system() == "Darwin":
                import subprocess
                out = subprocess.run(
                    ["sysctl", "-n", "machdep.cpu.brand_string"],
                    capture_output=True, text=True, timeout=2,
                )
                return out.stdout.strip() or ""
            label = _platform.processor() or ""
            if label and label.lower() not in ("", "unknown", "x86_64", "amd64", "arm64", "aarch64"):
                return label
        except Exception:
            pass
        return ""

    async def check_ollama(self) -> dict:
        """检查 Ollama 是否可用"""
        from ..adapters.ollama_engine import OllamaEngine
        engine = OllamaEngine()
        ok = await engine.health()
        models = await engine.list_models() if ok else []
        return {
            "available": ok,
            "base_url": engine.base_url,
            "installed_models": len(models),
            "models": models,
        }