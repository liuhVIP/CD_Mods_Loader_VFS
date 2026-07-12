"""旧Format 3与cdmod统一语义加载入口。

所有语义模组严格保持 ``scan_mods()`` 的最终顺序，先编译为VALID计划，再
通过临时Format 3桥接文件复用现有writer。
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from cdmm.common.models import DiscoveredMod, OverlayInputEntry
from cdmm.services.cdmod_build_plan import CDMOD_PLAN_VALID, compile_cdmod_package_plan
from cdmm.services.cdmod_converter import convert_format3_intent
from cdmm.services.cdmod_format3_bridge import write_format3_bridge
from cdmm.services.cdmod_package import CdmodOperation, CdmodPackage, load_cdmod_package
from cdmm.services.format3_loader import build_format3_overlay_entries
from cdmm.services.format3_capabilities import partition_supported_intents
from cdmm.services.format3_parser import parse_format3_file
from cdmm.services.scanner import MOD_TYPE_CDMOD, MOD_TYPE_FORMAT3
from cdmm.storage.vanilla_store import VanillaStore
from cdmm.utils.path_utils import lower_game_rel_path


def collect_semantic_warnings(mods: list[DiscoveredMod]) -> list[str]:
    """生成扫描阶段的统一语义模组提示。"""
    return [
        (
            f"{mod.name}: 已识别为 cdmod，apply 时将执行严格语义合并"
            if mod.mod_type == MOD_TYPE_CDMOD
            else f"{mod.name}: 已识别为 Format 3，apply 时将进入统一语义合并"
        )
        for mod in mods
    ]


def collect_semantic_pamt_targets(mods: list[DiscoveredMod]) -> list[str]:
    """收集统一语义阶段需要查询的PABGB/PABGH目标。"""
    targets: list[str] = []
    for package in _normalize_semantic_packages(mods, errors=[]):
        for operation in package.operations:
            body_target = lower_game_rel_path(operation.target)
            if not body_target.endswith(".pabgb"):
                body_target += ".pabgb"
            targets.extend((body_target, body_target.rsplit(".", 1)[0] + ".pabgh"))
    return list(dict.fromkeys(targets))


def build_semantic_overlay_entries(
    game_dir: Path,
    mods: list[DiscoveredMod],
    vanilla_store: VanillaStore,
    warnings: list[str],
    errors: list[str],
    base_entries: list[OverlayInputEntry] | None = None,
) -> list[OverlayInputEntry]:
    """按唯一加载顺序合并Format 3/cdmod并复用现有writer生成entry。"""
    if not mods:
        return []
    packages = _normalize_semantic_packages(mods, errors, warnings)
    if errors:
        return []
    plan = compile_cdmod_package_plan(tuple(packages), game_dir=game_dir)
    if plan.status != CDMOD_PLAN_VALID:
        errors.extend(f"cdmod语义计划被拒绝：{reason}" for reason in plan.rejection_reasons)
        return []
    warnings.extend(f"cdmod合并：{resolution}" for resolution in plan.resolutions)

    with tempfile.TemporaryDirectory(prefix="cdmod-runtime-") as temp_dir:
        bridge_path = Path(temp_dir) / f"semantic-{plan.plan_hash[:16]}.json"
        write_format3_bridge(plan, bridge_path)
        bridge_mod = DiscoveredMod(
            name=f"cdmod语义计划-{plan.plan_hash[:12]}",
            path=bridge_path,
            mod_type=MOD_TYPE_FORMAT3,
            fingerprint=plan.plan_hash,
        )
        return build_format3_overlay_entries(
            game_dir,
            [bridge_mod],
            vanilla_store,
            warnings,
            errors,
            base_entries,
        )


def _normalize_semantic_packages(
    mods: list[DiscoveredMod],
    errors: list[str],
    warnings: list[str] | None = None,
) -> list[CdmodPackage]:
    """保持扫描顺序，把两种语义来源标准化为同一包模型。"""
    packages: list[CdmodPackage] = []
    for mod in mods:
        try:
            if mod.mod_type == MOD_TYPE_CDMOD:
                packages.append(load_cdmod_package(mod.path))
            elif mod.mod_type == MOD_TYPE_FORMAT3:
                packages.append(_format3_mod_to_package(mod, warnings))
        except (OSError, ValueError) as exc:
            errors.append(f"{mod.name}: 语义模组解析失败：{exc}")
    return packages


def _format3_mod_to_package(
    mod: DiscoveredMod,
    warnings: list[str] | None = None,
) -> CdmodPackage:
    """将旧Format 3内存标准化，不生成中间cdmod文件。"""
    operations: list[CdmodOperation] = []
    for target_spec in parse_format3_file(mod.path):
        table_name = Path(target_spec.target.replace("\\", "/")).stem.lower()
        supported, skipped = partition_supported_intents(table_name, list(target_spec.intents))
        if skipped:
            if warnings is not None:
                warnings.append(
                    f"{mod.name}: {target_spec.target} 含 {len(skipped)} 个未支持字段，"
                    "已按旧Format 3原子规则跳过整个目标"
                )
            continue
        for intent in supported:
            raw_operation, _optimized = convert_format3_intent(target_spec.target, intent)
            operations.append(
                CdmodOperation(
                    target=lower_game_rel_path(target_spec.target),
                    selector=dict(raw_operation["selector"]),
                    path=str(raw_operation["path"]),
                    op=str(raw_operation["op"]),
                    payload=_operation_payload(raw_operation),
                    conversion=str(raw_operation.get("conversion") or "legacy-format3"),
                    index=len(operations),
                )
            )
    return CdmodPackage(
        path=mod.path,
        mod_id=f"legacy-format3-{mod.fingerprint[:24]}",
        name=mod.name,
        version="legacy",
        dependencies=(),
        operations=tuple(operations),
    )


def _operation_payload(operation: dict[str, Any]) -> Any:
    """读取转换后set/list_union的统一payload。"""
    return operation["values"] if operation["op"] == "list_union" else operation["value"]
