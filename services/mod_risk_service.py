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
from cdmm.services.cdmod_package import (
    CdmodPrefabRiskOperation,
    collect_cdmod_prefab_risk_operations,
)
from cdmm.utils.json_utils import load_json_optional

# 控制台根据此前缀把危险资源操作显示为 CMD 亮红色告警块。
HIGH_RISK_MOD_WARNING_PREFIX = "高风险模组资源（可能导致存档/场景崩溃）："

# standalone 冲突显示层沿用的统一前缀，业务规则仍集中在本模块。
VERIFIED_STANDALONE_CONFLICT_WARNING_PREFIX = "[STANDALONE-CONFLICT]"

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

# White Crow 完整 Prefab 复制失败规则仅匹配已实机验证的精确来源与目标。
WHITE_CROW_HAT_PREFAB_SOURCE = "character/cd_phw_00_hel_00_0164.prefab"
BATTLEFIELD_LIGHT_HAT_PREFAB_TARGET = "character/cd_phw_00_hel_00_0151.prefab"
WHITE_CROW_HAT_PREFAB_SHA256 = (
    "8f51daa6f36a246076b3bf3b36fef7c4c200dea14d9b2cb44fa57a2414252dc6"
)

# 该条件装配表不支持由两个 standalone 同时注册；游戏会重复解析同一最终路径。
CONDITIONAL_PART_PREFAB_TABLE_PATH = (
    "character/descriptors/conditionalpartprefab/conditionalpartprefab_transmog.xml"
)

# 单个告警最多展示的目标数，避免大型模组刷满控制台。
MAX_VISIBLE_RISK_TARGETS = 4


def collect_high_risk_mod_warnings(mods: list[DiscoveredMod]) -> list[str]:
    """为实机确认危险的精确 Prefab 操作生成告警。"""
    warnings: list[str] = []
    for mod in mods:
        matches = [
            (operation, risk_ids)
            for operation in _collect_prefab_operations(mod)
            if (risk_ids := _verified_prefab_risk_ids(operation))
        ]
        if not matches:
            continue
        visible = matches[:MAX_VISIBLE_RISK_TARGETS]
        hidden_count = len(matches) - len(visible)
        target_text = "；".join(_format_risk_operation(operation) for operation, _ids in visible)
        if hidden_count > 0:
            target_text += f"；另有 {hidden_count} 个 prefab 目标"
        matched_risk_ids = {
            risk_id
            for _operation, risk_ids in matches
            for risk_id in risk_ids
        }
        consequence_text = _build_prefab_risk_consequence(matched_risk_ids)
        warning_number = len(warnings) + 1
        warnings.append(
            f"{HIGH_RISK_MOD_WARNING_PREFIX}{warning_number}、【{mod.name}】\n"
            f"{target_text}。{consequence_text}"
        )
    return warnings


def build_verified_standalone_path_conflict_warning(
    final_path: str,
    sources: list[str],
) -> str | None:
    """为已确认不可重复注册的条件装配表生成红字 standalone 告警。"""
    normalized = _normalize_game_path(final_path)
    if normalized != CONDITIONAL_PART_PREFAB_TABLE_PATH or len(sources) < 2:
        return None
    source_text = "; ".join(sources)
    return (
        f"{VERIFIED_STANDALONE_CONFLICT_WARNING_PREFIX}\n"
        "1、【重复条件装配表 conditionalpartprefab_transmog.xml】\n"
        f"最终路径: {normalized}\n"
        f"冲突 archive（加载顺序）: {source_text}\n"
        "Crimson Desert 1.13.01 会把该最终路径重复解析；即使第二份 XML 只新增一条"
        " Condition，也会在数据加载 2/12 报 SourcePartPrefab 重复并退出。"
        "请只保留一份条件表，或改用已验证的目标 Prefab 内等长 PAC 路径替换。"
    )


def _collect_prefab_operations(mod: DiscoveredMod) -> list[CdmodPrefabRiskOperation]:
    """按模组形态收集 prefab 目标及危险操作类型。"""
    if mod.path.is_dir():
        return _collect_loose_prefab_operations(mod.path)
    if mod.path.suffix.lower() == ".cdmod":
        return collect_cdmod_prefab_risk_operations(mod.path)
    if mod.path.suffix.lower() == ".json":
        return _collect_json_prefab_operations(mod.path)
    return []


def _collect_json_prefab_operations(path: Path) -> list[CdmodPrefabRiskOperation]:
    """识别传统 JSON byte patch 中的 prefab 目标。"""
    document = load_json_optional(path)
    if not isinstance(document, dict):
        return []
    return _collect_json_document_prefab_operations(document, "JSON byte patch")


def _collect_json_document_prefab_operations(
    document: dict,
    method: str,
) -> list[CdmodPrefabRiskOperation]:
    """从传统 JSON 文档提取 prefab game_file。"""
    operations: list[CdmodPrefabRiskOperation] = []
    patches = document.get("patches")
    if not isinstance(patches, list):
        return operations
    for patch in patches:
        if not isinstance(patch, dict):
            continue
        target = patch.get("game_file")
        if isinstance(target, str) and _is_prefab(target):
            operations.append(CdmodPrefabRiskOperation(method, target))
    return _dedupe_operations(operations)


def _collect_loose_prefab_operations(mod_dir: Path) -> list[CdmodPrefabRiskOperation]:
    """识别目录型 loose 模组携带的完整 prefab 文件。"""
    operations = [
        CdmodPrefabRiskOperation(
            "loose file replacement",
            path.relative_to(mod_dir).as_posix(),
        )
        for path in sorted(mod_dir.rglob("*"), key=lambda item: item.as_posix().casefold())
        if path.is_file() and _is_prefab(path.name)
    ]
    return _dedupe_operations(operations)


def _is_prefab(target: str) -> bool:
    """判断规范或 loose 路径是否指向 prefab。"""
    return target.replace("\\", "/").casefold().endswith(PREFAB_SUFFIX)


def _is_verified_dangerous_target(target: str) -> bool:
    """兼容 cdmod 规范路径与 files/NNNN loose 前缀，匹配已确认目标。"""
    normalized = target.replace("\\", "/").casefold()
    return any(normalized.endswith(known_target) for known_target in VERIFIED_DANGEROUS_PREFAB_TARGETS)


def _verified_prefab_risk_ids(operation: CdmodPrefabRiskOperation) -> tuple[str, ...]:
    """按精确目标、操作来源或载荷 SHA 匹配已验证风险。"""
    risk_ids: list[str] = []
    if _is_verified_dangerous_target(operation.target):
        risk_ids.append("male-flight-cloak-prefab")
    target = _normalize_game_path(operation.target)
    source = _normalize_game_path(operation.source or "")
    is_exact_copy = (
        operation.method == "resource-transform copy-entry"
        and target == BATTLEFIELD_LIGHT_HAT_PREFAB_TARGET
        and source == WHITE_CROW_HAT_PREFAB_SOURCE
    )
    is_exact_payload = (
        operation.method == "file-replacement"
        and target == BATTLEFIELD_LIGHT_HAT_PREFAB_TARGET
        and operation.payload_sha256 == WHITE_CROW_HAT_PREFAB_SHA256
    )
    if is_exact_copy or is_exact_payload:
        risk_ids.append("white-crow-full-prefab-copy")
    return tuple(risk_ids)


def _format_risk_operation(operation: CdmodPrefabRiskOperation) -> str:
    """生成包含可用来源证据的操作说明。"""
    if operation.source:
        return f"{operation.method} {operation.source} -> {operation.target}"
    return f"{operation.method} -> {operation.target}"


def _build_prefab_risk_consequence(risk_ids: set[str]) -> str:
    """按实际命中的风险类型组合实机后果与安全替代方案。"""
    messages: list[str] = []
    if "male-flight-cloak-prefab" in risk_ids:
        messages.append(
            "这两个男性飞行披风 prefab 已由 1.1/1.2/1.3 三轮实机确认："
            "直接复制、完整替换或字节修改会在 12/12 和 End Load SaveSlot 后崩溃；"
            "请改用 PAC 全 LOD 索引退化方案。"
        )
    if "white-crow-full-prefab-copy" in risk_ids:
        messages.append(
            "White Crow 0164 完整 Prefab 覆盖战场之光 0151 已实机确认会在 "
            "End Load SaveSlot 后崩溃；请保留 0151 的组件布局、UID 与骨骼 socket，"
            "只做已验证的等长主 PAC 路径替换。"
        )
    return "".join(messages)


def _normalize_game_path(value: str) -> str:
    """规范最终游戏路径，供精确风险规则复用。"""
    return value.replace("\\", "/").strip("/").casefold()


def _dedupe_operations(
    operations: list[CdmodPrefabRiskOperation],
) -> list[CdmodPrefabRiskOperation]:
    """保持发现顺序并删除重复操作说明。"""
    return list(dict.fromkeys(operations))
