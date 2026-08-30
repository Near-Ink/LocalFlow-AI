"""硬件识别库 — 部署向导用

把本机探测到的硬件与「已知硬件库」匹配，识别具体型号与能力档位，
避免小白部署时硬件不识别导致推荐失败：
- 命中库：给出型号、能力档位（tier）、显存/统一内存、模型档位建议；
- 未命中：按显存/内存走通用规则兜底，绝不因识别失败而中断部署。

库内容覆盖主流 NVIDIA / AMD / Intel Arc 独显与 Apple Silicon 芯片，
后续可经数据文件 / 插件扩展。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


# ── 能力档位 ────────────────────────────────────────────────────────────────
TIER_MAX = "max"       # 旗舰：可跑 32B+ 大模型
TIER_HIGH = "high"     # 高性能：可流畅 14B，跑 32B 偏紧
TIER_MID = "mid"       # 中端：可流畅 7B-8B
TIER_LOW = "low"       # 入门：可跑 3B-7B
TIER_ENTRY = "entry"   # 轻量：仅小模型 / CPU
TIER_UNKNOWN = "unknown"

TIER_LABEL = {
    TIER_MAX: "旗舰级", TIER_HIGH: "高性能", TIER_MID: "中端",
    TIER_LOW: "入门级", TIER_ENTRY: "轻量级", TIER_UNKNOWN: "未知",
}


@dataclass
class HwMatch:
    """一次硬件识别结果"""
    recognized: bool = False       # 是否命中硬件库
    kind: str = "unknown"          # apple-silicon / nvidia / amd / intel-arc / integrated / cpu-only / unknown
    gpu_label: str = ""            # 识别出的型号（未命中则用原始名称）
    gpu_vendor: str = ""           # 厂商名
    tier: str = TIER_UNKNOWN       # 能力档位
    vram_gb: float = 0.0           # 显存 / 统一内存（GB）
    cpu_label: str = ""            # CPU 描述
    cpu_cores: int = 0             # 核心数
    capability: str = ""           # 能力描述
    model_guide: str = ""          # 推荐模型档位说明
    note: str = ""                 # 命中 / 兜底说明


# ── Apple Silicon 芯片库 ─────────────────────────────────────────────────────
# key: brand_string 子串（小写）；按最长匹配优先
APPLE_CHIPS: List[dict] = [
    # M4 系列
    {"sub": "m4 ultra", "tier": TIER_MAX, "cap": "Apple M4 Ultra 旗舰芯片，可运行 32B 级大模型"},
    {"sub": "m4 max",   "tier": TIER_MAX, "cap": "Apple M4 Max 旗舰芯片，可运行 32B 级大模型"},
    {"sub": "m4 pro",   "tier": TIER_HIGH, "cap": "Apple M4 Pro，可流畅运行 14B 级模型"},
    {"sub": "m4",       "tier": TIER_HIGH, "cap": "Apple M4，可流畅运行 7B-14B 级模型"},
    # M3 系列
    {"sub": "m3 ultra", "tier": TIER_MAX, "cap": "Apple M3 Ultra 旗舰芯片，可运行 32B 级大模型"},
    {"sub": "m3 max",   "tier": TIER_MAX, "cap": "Apple M3 Max 旗舰芯片，可运行 32B 级大模型"},
    {"sub": "m3 pro",   "tier": TIER_HIGH, "cap": "Apple M3 Pro，可流畅运行 14B 级模型"},
    {"sub": "m3",       "tier": TIER_MID, "cap": "Apple M3，可流畅运行 7B 级模型"},
    # M2 系列
    {"sub": "m2 ultra", "tier": TIER_MAX, "cap": "Apple M2 Ultra 旗舰芯片，可运行 32B 级大模型"},
    {"sub": "m2 max",   "tier": TIER_HIGH, "cap": "Apple M2 Max，可流畅运行 14B 级模型"},
    {"sub": "m2 pro",   "tier": TIER_HIGH, "cap": "Apple M2 Pro，可流畅运行 14B 级模型"},
    {"sub": "m2",       "tier": TIER_MID, "cap": "Apple M2，可流畅运行 7B 级模型"},
    # M1 系列
    {"sub": "m1 ultra", "tier": TIER_HIGH, "cap": "Apple M1 Ultra，可流畅运行 14B 级模型"},
    {"sub": "m1 max",   "tier": TIER_HIGH, "cap": "Apple M1 Max，可流畅运行 14B 级模型"},
    {"sub": "m1 pro",   "tier": TIER_MID, "cap": "Apple M1 Pro，可流畅运行 7B 级模型"},
    {"sub": "m1",       "tier": TIER_MID, "cap": "Apple M1，可流畅运行 7B 级模型"},
    {"sub": "apple",    "tier": TIER_MID, "cap": "Apple Silicon 芯片，可流畅运行 7B 级模型"},
]

# ── NVIDIA 显卡库 ────────────────────────────────────────────────────────────
# sub: 名称子串（小写）；vram_gb 为该型号典型显存（部分型号有变体，匹配时优先用探测值）
NVIDIA_GPUS: List[dict] = [
    {"sub": "rtx 5090", "vram": 32, "tier": TIER_MAX, "cap": "RTX 5090 旗舰卡，可运行 32B 级大模型"},
    {"sub": "rtx 4090", "vram": 24, "tier": TIER_MAX, "cap": "RTX 4090 旗舰卡，可运行 32B 级大模型"},
    {"sub": "rtx 4080 super", "vram": 16, "tier": TIER_HIGH, "cap": "RTX 4080 Super，可流畅运行 14B 级模型"},
    {"sub": "rtx 4080", "vram": 16, "tier": TIER_HIGH, "cap": "RTX 4080，可流畅运行 14B 级模型"},
    {"sub": "rtx 4070 ti super", "vram": 16, "tier": TIER_HIGH, "cap": "RTX 4070 Ti Super，可流畅运行 14B 级模型"},
    {"sub": "rtx 4070 ti", "vram": 12, "tier": TIER_HIGH, "cap": "RTX 4070 Ti，可流畅运行 14B 级模型"},
    {"sub": "rtx 4070 super", "vram": 12, "tier": TIER_MID, "cap": "RTX 4070 Super，可流畅运行 7B-14B 级模型"},
    {"sub": "rtx 4070", "vram": 12, "tier": TIER_MID, "cap": "RTX 4070，可流畅运行 7B-14B 级模型"},
    {"sub": "rtx 4060 ti", "vram": 8, "tier": TIER_MID, "cap": "RTX 4060 Ti，可流畅运行 7B 级模型"},
    {"sub": "rtx 4060", "vram": 8, "tier": TIER_MID, "cap": "RTX 4060，可流畅运行 7B 级模型"},
    {"sub": "rtx 3090 ti", "vram": 24, "tier": TIER_HIGH, "cap": "RTX 3090 Ti，可流畅运行 14B 级模型"},
    {"sub": "rtx 3090", "vram": 24, "tier": TIER_HIGH, "cap": "RTX 3090，可流畅运行 14B 级模型"},
    {"sub": "rtx 3080 ti", "vram": 12, "tier": TIER_HIGH, "cap": "RTX 3080 Ti，可流畅运行 14B 级模型"},
    {"sub": "rtx 3080", "vram": 10, "tier": TIER_HIGH, "cap": "RTX 3080，可流畅运行 14B 级模型"},
    {"sub": "rtx 3070 ti", "vram": 8, "tier": TIER_MID, "cap": "RTX 3070 Ti，可流畅运行 7B 级模型"},
    {"sub": "rtx 3070", "vram": 8, "tier": TIER_MID, "cap": "RTX 3070，可流畅运行 7B 级模型"},
    {"sub": "rtx 3060 ti", "vram": 8, "tier": TIER_MID, "cap": "RTX 3060 Ti，可流畅运行 7B 级模型"},
    {"sub": "rtx 3060", "vram": 12, "tier": TIER_MID, "cap": "RTX 3060，可流畅运行 7B 级模型"},
    {"sub": "rtx 2080 ti", "vram": 11, "tier": TIER_MID, "cap": "RTX 2080 Ti，可流畅运行 7B 级模型"},
    {"sub": "rtx 2080", "vram": 8, "tier": TIER_MID, "cap": "RTX 2080，可流畅运行 7B 级模型"},
    {"sub": "rtx 2070 super", "vram": 8, "tier": TIER_MID, "cap": "RTX 2070 Super，可流畅运行 7B 级模型"},
    {"sub": "rtx 2070", "vram": 8, "tier": TIER_MID, "cap": "RTX 2070，可流畅运行 7B 级模型"},
    {"sub": "rtx 2060 super", "vram": 8, "tier": TIER_MID, "cap": "RTX 2060 Super，可流畅运行 7B 级模型"},
    {"sub": "rtx 2060", "vram": 6, "tier": TIER_LOW, "cap": "RTX 2060，可流畅运行 3B-7B 级模型"},
    {"sub": "gtx 1660 super", "vram": 6, "tier": TIER_LOW, "cap": "GTX 1660 Super，可流畅运行 3B-7B 级模型"},
    {"sub": "gtx 1660 ti", "vram": 6, "tier": TIER_LOW, "cap": "GTX 1660 Ti，可流畅运行 3B-7B 级模型"},
    {"sub": "gtx 1660", "vram": 6, "tier": TIER_LOW, "cap": "GTX 1660，可流畅运行 3B-7B 级模型"},
    {"sub": "gtx 1080 ti", "vram": 11, "tier": TIER_MID, "cap": "GTX 1080 Ti，可流畅运行 7B 级模型"},
    {"sub": "gtx 1080", "vram": 8, "tier": TIER_MID, "cap": "GTX 1080，可流畅运行 7B 级模型"},
    {"sub": "gtx 1070 ti", "vram": 8, "tier": TIER_MID, "cap": "GTX 1070 Ti，可流畅运行 7B 级模型"},
    {"sub": "gtx 1070", "vram": 8, "tier": TIER_MID, "cap": "GTX 1070，可流畅运行 7B 级模型"},
    {"sub": "gtx 1060", "vram": 6, "tier": TIER_LOW, "cap": "GTX 1060，可流畅运行 3B-7B 级模型"},
    {"sub": "gtx 1650", "vram": 4, "tier": TIER_ENTRY, "cap": "GTX 1650，建议 3B 级小模型"},
    {"sub": "gtx 1050", "vram": 4, "tier": TIER_ENTRY, "cap": "GTX 1050，建议 3B 级小模型"},
]

# ── AMD 显卡库 ───────────────────────────────────────────────────────────────
AMD_GPUS: List[dict] = [
    {"sub": "rx 7900 xtx", "vram": 24, "tier": TIER_HIGH, "cap": "RX 7900 XTX，可流畅运行 14B 级模型"},
    {"sub": "rx 7900 xt", "vram": 20, "tier": TIER_HIGH, "cap": "RX 7900 XT，可流畅运行 14B 级模型"},
    {"sub": "rx 7800 xt", "vram": 16, "tier": TIER_HIGH, "cap": "RX 7800 XT，可流畅运行 14B 级模型"},
    {"sub": "rx 7700 xt", "vram": 12, "tier": TIER_MID, "cap": "RX 7700 XT，可流畅运行 7B 级模型"},
    {"sub": "rx 7600", "vram": 8, "tier": TIER_MID, "cap": "RX 7600，可流畅运行 7B 级模型"},
    {"sub": "rx 6800 xt", "vram": 16, "tier": TIER_HIGH, "cap": "RX 6800 XT，可流畅运行 14B 级模型"},
    {"sub": "rx 6800", "vram": 16, "tier": TIER_HIGH, "cap": "RX 6800，可流畅运行 14B 级模型"},
    {"sub": "rx 6700 xt", "vram": 12, "tier": TIER_MID, "cap": "RX 6700 XT，可流畅运行 7B 级模型"},
    {"sub": "rx 6600", "vram": 8, "tier": TIER_MID, "cap": "RX 6600，可流畅运行 7B 级模型"},
]

# ── Intel Arc 显卡库 ─────────────────────────────────────────────────────────
INTEL_ARCS: List[dict] = [
    {"sub": "arc a770", "vram": 16, "tier": TIER_MID, "cap": "Intel Arc A770，可流畅运行 7B 级模型"},
    {"sub": "arc a750", "vram": 12, "tier": TIER_MID, "cap": "Intel Arc A750，可流畅运行 7B 级模型"},
    {"sub": "arc a580", "vram": 8, "tier": TIER_MID, "cap": "Intel Arc A580，可流畅运行 7B 级模型"},
    {"sub": "arc a380", "vram": 6, "tier": TIER_LOW, "cap": "Intel Arc A380，可流畅运行 3B-7B 级模型"},
]

# ── CPU 库（Intel / AMD 型号识别，用于展示与档位参考） ─────────────────────
INTEL_CPUS: List[dict] = [
    {"sub": "core i9", "tier": TIER_HIGH, "cap": "Intel Core i9 高端处理器"},
    {"sub": "core i7", "tier": TIER_MID, "cap": "Intel Core i7 处理器"},
    {"sub": "core i5", "tier": TIER_MID, "cap": "Intel Core i5 处理器"},
    {"sub": "core i3", "tier": TIER_LOW, "cap": "Intel Core i3 处理器"},
    {"sub": "xeon", "tier": TIER_HIGH, "cap": "Intel Xeon 服务器处理器"},
]
AMD_CPUS: List[dict] = [
    {"sub": "ryzen 9", "tier": TIER_HIGH, "cap": "AMD Ryzen 9 高端处理器"},
    {"sub": "ryzen 7", "tier": TIER_MID, "cap": "AMD Ryzen 7 处理器"},
    {"sub": "ryzen 5", "tier": TIER_MID, "cap": "AMD Ryzen 5 处理器"},
    {"sub": "ryzen 3", "tier": TIER_LOW, "cap": "AMD Ryzen 3 处理器"},
    {"sub": "epyc", "tier": TIER_HIGH, "cap": "AMD EPYC 服务器处理器"},
]


def _norm(name: str) -> str:
    """归一化硬件名称：小写、压缩空白、去常见前缀符号"""
    return " ".join(str(name or "").lower().split())


def _match_list(sub: str, entries: List[dict], generic: Optional[str] = None) -> Optional[dict]:
    """按子串命中条目；多个命中时取最长子串（最精确）。

    generic 为泛匹配兜底子串（如 "apple"）：仅当没有任何具体条目命中时才返回它，
    避免泛匹配（长度更长）压过具体型号匹配。
    """
    specific = [e for e in entries if e.get("sub") != generic]
    hits = [e for e in specific if e["sub"] in sub]
    if hits:
        return max(hits, key=lambda e: len(e["sub"]))
    if generic:
        for e in entries:
            if e.get("sub") == generic and generic in sub:
                return e
    return None


def match_apple_chip(raw_name: str) -> Optional[dict]:
    return _match_list(_norm(raw_name), APPLE_CHIPS, generic="apple")


def match_nvidia(raw_name: str) -> Optional[dict]:
    return _match_list(_norm(raw_name), NVIDIA_GPUS)


def match_amd(raw_name: str) -> Optional[dict]:
    return _match_list(_norm(raw_name), AMD_GPUS)


def match_intel_arc(raw_name: str) -> Optional[dict]:
    return _match_list(_norm(raw_name), INTEL_ARCS)


def match_cpu(cpu_label: str) -> Optional[dict]:
    if not cpu_label:
        return None
    n = _norm(cpu_label)
    return _match_list(n, INTEL_CPUS) or _match_list(n, AMD_CPUS)


def _tier_by_vram(vram_gb: float) -> str:
    """未命中独显库时的通用显存分档"""
    if vram_gb >= 20:
        return TIER_MAX
    if vram_gb >= 14:
        return TIER_HIGH
    if vram_gb >= 8:
        return TIER_MID
    if vram_gb >= 5:
        return TIER_LOW
    return TIER_ENTRY


def _guide(tier: str) -> str:
    """档位 → 推荐模型档位说明"""
    return {
        TIER_MAX: "推荐 14B-32B 级模型（如 qwen3:32b / deepseek-r1:32b）",
        TIER_HIGH: "推荐 7B-14B 级模型（如 qwen2.5:14b / mistral-nemo:12b）",
        TIER_MID: "推荐 3B-8B 级模型（如 qwen2.5:7b / llama3.1:8b）",
        TIER_LOW: "推荐 3B 级小模型（如 qwen2.5:3b / llama3.2:3b）",
        TIER_ENTRY: "建议 1B-3B 级轻量模型，或绑定云端 API",
        TIER_UNKNOWN: "建议绑定云端 API，或先用 3B 级小模型尝试",
    }[tier]


def classify(info, has_nvidia: bool, has_apple: bool, cpu_label: str = "") -> HwMatch:
    """根据探测到的硬件信息生成识别结果（命中库 / 通用兜底，绝不抛错）"""
    m = HwMatch()
    mem_total_gb = (info.mem_total_mb or 0) / 1024.0
    m.cpu_cores = info.cpu_cores or 0
    m.cpu_label = cpu_label or f"{m.cpu_cores} 核处理器"

    gpu = info.gpus[0] if info.gpus else None
    raw_name = (gpu.name if gpu else "") or ""
    vram_mb = (gpu.vram_total_mb if gpu else 0) or 0
    vram_gb = vram_mb / 1024.0

    # 1) Apple Silicon（芯片库命中即识别）
    if has_apple:
        chip = match_apple_chip(raw_name)
        m.kind = "apple-silicon"
        m.gpu_label = chip["cap"] if chip else (raw_name or "Apple Silicon")
        m.gpu_vendor = "Apple"
        m.tier = chip["tier"] if chip else TIER_MID
        m.vram_gb = vram_gb or mem_total_gb
        m.recognized = bool(chip)
        m.capability = chip["cap"] if chip else "Apple Silicon 统一内存芯片（未命中细分型号，按中端档位兜底）"
        m.model_guide = _guide(m.tier)
        m.note = ("已命中 Apple 芯片库" if chip else "未命中芯片细分型号，按通用规则兜底（不影响部署）")
        return m

    # 2) NVIDIA
    if has_nvidia:
        hit = match_nvidia(raw_name)
        m.kind = "nvidia"
        m.gpu_vendor = "NVIDIA"
        if hit:
            m.gpu_label = "NVIDIA " + (raw_name or "").strip() or "NVIDIA GPU"
            m.tier = hit["tier"]
            m.vram_gb = vram_gb or float(hit["vram"])
            m.recognized = True
            m.capability = hit["cap"]
        else:
            m.gpu_label = raw_name or "NVIDIA GPU"
            m.tier = _tier_by_vram(vram_gb)
            m.vram_gb = vram_gb
            m.capability = f"未命中显卡库，按显存 {m.vram_gb:.0f}GB 通用档位评估"
        m.model_guide = _guide(m.tier)
        m.note = "已命中 NVIDIA 显卡库" if hit else "未命中显卡库，按显存通用档位兜底（不影响部署）"
        return m

    # 3) AMD / Intel Arc（非 NVIDIA 独显：AMD 卡或 Arc 卡名称直接识别）
    if raw_name:
        amd = match_amd(raw_name)
        if amd:
            m.kind = "amd"
            m.gpu_vendor = "AMD"
            m.gpu_label = raw_name
            m.tier = amd["tier"]
            m.vram_gb = vram_gb or float(amd["vram"])
            m.recognized = True
            m.capability = amd["cap"]
            m.model_guide = _guide(m.tier)
            m.note = "已命中 AMD 显卡库"
            return m
        arc = match_intel_arc(raw_name)
        if arc:
            m.kind = "intel-arc"
            m.gpu_vendor = "Intel"
            m.gpu_label = raw_name
            m.tier = arc["tier"]
            m.vram_gb = vram_gb or float(arc["vram"])
            m.recognized = True
            m.capability = arc["cap"]
            m.model_guide = _guide(m.tier)
            m.note = "已命中 Intel Arc 显卡库"
            return m
        # 有独显名称但库中无此型号 → 按显存兜底
        m.kind = "discrete-gpu"
        m.gpu_vendor = ""
        m.gpu_label = raw_name
        m.tier = _tier_by_vram(vram_gb)
        m.vram_gb = vram_gb
        m.capability = f"独立显卡（{raw_name}）未在库中，按显存 {vram_gb:.0f}GB 通用档位评估"
        m.model_guide = _guide(m.tier)
        m.note = "独立显卡未命中库，按显存通用档位兜底（不影响部署）"
        return m

    # 4) 无 GPU（核显 / 纯 CPU）
    m.kind = "cpu-only" if not raw_name else "integrated"
    m.gpu_vendor = ""
    m.gpu_label = "核显 / 纯 CPU" if not raw_name else raw_name
    if mem_total_gb >= 24:
        m.tier = TIER_MID
        m.capability = f"无独显，{mem_total_gb:.0f}GB 内存可走 CPU 推理 7B 级模型"
    elif mem_total_gb >= 12:
        m.tier = TIER_LOW
        m.capability = f"无独显，{mem_total_gb:.0f}GB 内存可走 CPU 推理 3B-7B 级模型"
    else:
        m.tier = TIER_ENTRY
        m.capability = f"无独显，{mem_total_gb:.0f}GB 内存仅建议轻量模型，或绑定云端 API"
    m.vram_gb = 0.0
    m.model_guide = _guide(m.tier)
    m.note = "未检测到独立 GPU，按内存通用规则兜底（不影响部署）"
    return m


def to_dict(m: HwMatch) -> dict:
    """HwMatch → JSON 可序列化 dict（供 /api/wizard/analyze 返回）"""
    return {
        "recognized": m.recognized,
        "kind": m.kind,
        "gpu_label": m.gpu_label,
        "gpu_vendor": m.gpu_vendor,
        "tier": m.tier,
        "tier_label": TIER_LABEL.get(m.tier, m.tier),
        "vram_gb": round(m.vram_gb, 1),
        "cpu_label": m.cpu_label,
        "cpu_cores": m.cpu_cores,
        "capability": m.capability,
        "model_guide": m.model_guide,
        "note": m.note,
    }
