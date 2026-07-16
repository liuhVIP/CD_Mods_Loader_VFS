"""集中扫描已由实机证据确认的高风险模组操作并生成用户可见告警。

本模块是高风险规则的唯一业务入口。新增规则前必须先在 ``AGENTS.md`` 和
对应经验文档记录实机证据、适用版本、精确匹配边界与安全替代方案；规则应按
最终规范游戏路径、明确操作或必要字节签名精确匹配，禁止仅按资源后缀或大类
泛化。扫描必须保持轻量，不在此处解压大型载荷；颜色显示由
``utils/console_alert.py`` 统一负责。本模块只提示，不改变加载顺序、不自动
禁用或阻止模组，也不替代真实游戏验证。
"""

from __future__ import annotations

from pathlib import Path

from cdmm.common.models import DiscoveredMod
from cdmm.services.cdmod_package import collect_cdmod_prefab_risk_targets
from cdmm.utils.json_utils import load_json_optional

# 控制台根据此前缀把危险资源操作显示为 CMD 亮红色告警块。
HIGH_RISK_MOD_WARNING_PREFIX = "高风险模组资源（可能导致存档/场景崩溃）："

# prefab 文件后缀，仅用于从不同模组容器中提取候选目标。
PREFAB_SUFFIX = ".prefab"

# 已确认风险规则必须附带实机证据和明确的安全边界，新增规则不得只按后缀泛化。
# 仅这两个男性飞行披风 prefab 已由 1.1/1.2/1.3 三轮实机确认会卡存档。
# 不能把结论泛化到头部、身体、饰品等已经稳定使用的其他 prefab。
VERIFIED_DANGEROUS_PREFAB_TARGETS = frozenset(
    {
        "character/cd_phm_00_cloak_flight_0001.prefab",
        "character/cd_phm_00_cloak_flight_0001_index01.prefab",
    }
)

# 单个告警最多展示的目标数，避免大型模组刷满控制台。
MAX_VISIBLE_RISK_TARGETS = 4


def collect_high_risk_mod_warnings(mods: list[DiscoveredMod]) -> list[str]:
    """只为实机确认危险的男性飞行披风 prefab 覆盖生成告警。"""
    warnings: list[str] = []
    for mod in mods:
        targets = [
            target
            for target in _collect_prefab_targets(mod)
            if _is_verified_dangerous_target(target[1])
        ]
        if not targets:
            continue
        visible = targets[:MAX_VISIBLE_RISK_TARGETS]
        hidden_count = len(targets) - len(visible)
        target_text = "；".join(f"{method} -> {target}" for method, target in visible)
        if hidden_count > 0:
            target_text += f"；另有 {hidden_count} 个 prefab 目标"
        warning_number = len(warnings) + 1
        warnings.append(
            f"{HIGH_RISK_MOD_WARNING_PREFIX}{warning_number}、【{mod.name}】\n"
            f"{target_text}。"
            "这两个男性飞行披风 prefab 已由 1.1/1.2/1.3 三轮实机确认："
            "直接复制、完整替换或字节修改会在 12/12 和 End Load SaveSlot 后崩溃。"
            "请改用已验证的 PAC 全 LOD 索引退化方案。"
        )
    return warnings


def _collect_prefab_targets(mod: DiscoveredMod) -> list[tuple[str, str]]:
    """按模组形态收集 prefab 目标及危险操作类型。"""
    if mod.path.is_dir():
        return _collect_loose_prefab_targets(mod.path)
    if mod.path.suffix.lower() == ".cdmod":
        return _collect_cdmod_prefab_targets(mod.path)
    if mod.path.suffix.lower() == ".json":
        return _collect_json_prefab_targets(mod.path)
    return []


def _collect_cdmod_prefab_targets(path: Path) -> list[tuple[str, str]]:
    """轻量读取 cdmod 组件 JSON，禁止在扫描阶段解压大型资源载荷。"""
    return collect_cdmod_prefab_risk_targets(path)


def _collect_json_prefab_targets(path: Path) -> list[tuple[str, str]]:
    """识别传统 JSON byte patch 中的 prefab 目标。"""
    document = load_json_optional(path)
    if not isinstance(document, dict):
        return []
    return _collect_json_document_prefab_targets(document, "JSON byte patch")


def _collect_json_document_prefab_targets(
    document: dict,
    method: str,
) -> list[tuple[str, str]]:
    """从传统 JSON 文档提取 prefab game_file。"""
    targets: list[tuple[str, str]] = []
    patches = document.get("patches")
    if not isinstance(patches, list):
        return targets
    for patch in patches:
        if not isinstance(patch, dict):
            continue
        target = patch.get("game_file")
        if isinstance(target, str) and _is_prefab(target):
            targets.append((method, target))
    return _dedupe_targets(targets)


def _collect_loose_prefab_targets(mod_dir: Path) -> list[tuple[str, str]]:
    """识别目录型 loose 模组携带的完整 prefab 文件。"""
    targets = [
        ("loose file replacement", path.relative_to(mod_dir).as_posix())
        for path in sorted(mod_dir.rglob("*"), key=lambda item: item.as_posix().casefold())
        if path.is_file() and _is_prefab(path.name)
    ]
    return _dedupe_targets(targets)


def _is_prefab(target: str) -> bool:
    """判断规范或 loose 路径是否指向 prefab。"""
    return target.replace("\\", "/").casefold().endswith(PREFAB_SUFFIX)


def _is_verified_dangerous_target(target: str) -> bool:
    """兼容 cdmod 规范路径与 files/NNNN loose 前缀，匹配已确认目标。"""
    normalized = target.replace("\\", "/").casefold()
    return any(normalized.endswith(known_target) for known_target in VERIFIED_DANGEROUS_PREFAB_TARGETS)


def _dedupe_targets(targets: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """保持发现顺序并删除重复操作说明。"""
    return list(dict.fromkeys(targets))
