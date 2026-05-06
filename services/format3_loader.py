"""Format 3 模组识别占位转换入口。"""

from __future__ import annotations

from cdmm.common.models import DiscoveredMod


def collect_format3_warnings(mods: list[DiscoveredMod]) -> list[str]:
    """第一阶段只识别 Format 3，不做语义转换，避免静默误应用。"""
    return [
        f"{mod.name}: 已识别为 Format 3，当前独立加载器第一阶段尚未启用语义转换"
        for mod in mods
    ]
