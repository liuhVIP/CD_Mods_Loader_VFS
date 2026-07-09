"""Format 3 语义 JSON 转传统 byte patch 的独立加载器桥接层。

当前独立版还没有迁到完整 GUI 管理器那套“全表 writer + whole-table/
per-intent 混合调度”体系，所以这里先把入口拆成三层：

1. `format3_parser.py` 负责解析和标准化 Format 3 JSON。
2. `format3_loader.py` 负责目标解析、base 合成与桥接调度。
3. 各 table writer 只关心如何把 intents 转成传统 byte patch。

这样后续继续迁移 `skill`、`storeinfo`、`stringinfo`、`dropsetinfo`
时，只需要新增 writer 并注册到 `_FORMAT3_WRITERS`，不用再把整个
Format 3 入口重写一遍。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from cdmm.common.models import DiscoveredMod, OverlayInputEntry, PazEntry
from cdmm.services.format3_buffinfo_writer import build_buffinfo_byte_patch_result
from cdmm.services.format3_capabilities import partition_supported_intents
from cdmm.services.format3_characterinfo_writer import build_characterinfo_byte_patch_result
from cdmm.services.format3_dropset_writer import build_dropsetinfo_result
from cdmm.services.format3_equipslotinfo_writer import build_equipslotinfo_result
from cdmm.services.format3_iteminfo_writer import build_iteminfo_prefab_result
from cdmm.services.format3_multichangeinfo_writer import build_multichangeinfo_result
from cdmm.services.format3_skill_writer import build_skill_whole_table_result
from cdmm.services.format3_stringinfo_writer import build_stringinfo_result
from cdmm.services.format3_storeinfo_writer import build_storeinfo_result
from cdmm.services.format3_parser import Format3Intent, parse_format3_file
from cdmm.services.format3_runtime import (
    Format3DispatchResult,
    Format3RuntimeContext,
    Format3SkippedIntent,
    summarize_skip_reasons,
)
from cdmm.services.json_loader import (
    apply_byte_patches,
    build_patch_overlay_entries,
    extract_plaintext,
    fixup_pabgh_after_inserts,
)
from cdmm.services.pab_table_service import build_entry_bounds, parse_pabgh_index
from cdmm.services.pamt_index_service import get_game_pamt_index
from cdmm.storage.vanilla_store import VanillaStore
from cdmm.utils.path_utils import lower_game_rel_path

logger = logging.getLogger(__name__)

# Format 3 writer 注册表。当前 iteminfo 入口内部会继续按字段分流到
# 窄 writer / whole-table writer，后续迁移新 table 时只需要继续往这里注册。
Format3Writer = Callable[
    [Format3RuntimeContext, list[Format3Intent]],
    Format3DispatchResult,
]


_FORMAT3_WRITERS: dict[str, Format3Writer] = {
    "buffinfo": build_buffinfo_byte_patch_result,
    "characterinfo": build_characterinfo_byte_patch_result,
    "dropsetinfo": build_dropsetinfo_result,
    "equipslotinfo": build_equipslotinfo_result,
    "iteminfo": build_iteminfo_prefab_result,
    "multichangeinfo": build_multichangeinfo_result,
    "skill": build_skill_whole_table_result,
    "stringinfo": build_stringinfo_result,
    "storeinfo": build_storeinfo_result,
}


def collect_format3_warnings(mods: list[DiscoveredMod]) -> list[str]:
    """扫描阶段提示 Format 3 已进入 apply 尝试链路。"""
    return [f"{mod.name}: 已识别为 Format 3，apply 时将尝试语义转换" for mod in mods]


def build_format3_overlay_entries(
    game_dir: Path,
    mods: list[DiscoveredMod],
    vanilla_store: VanillaStore,
    warnings: list[str],
    errors: list[str],
    base_entries: list[OverlayInputEntry] | None = None,
) -> list[OverlayInputEntry]:
    """把 Format 3 模组转换为 overlay entry。"""
    grouped = build_format3_patch_items(
        game_dir,
        mods,
        vanilla_store,
        warnings,
        errors,
        base_entries,
    )
    return build_patch_overlay_entries(
        game_dir,
        grouped,
        vanilla_store,
        warnings,
        errors,
        base_entries,
    )


def build_format3_patch_items(
    game_dir: Path,
    mods: list[DiscoveredMod],
    vanilla_store: VanillaStore,
    warnings: list[str],
    errors: list[str],
    base_entries: list[OverlayInputEntry] | None = None,
) -> dict[str, list[tuple[DiscoveredMod, dict]]]:
    """生成可复用 JSON byte patch 流程的 Format 3 patch block。"""
    grouped: dict[str, list[tuple[DiscoveredMod, dict]]] = {}
    current_body_by_entry = {
        entry.entry_path.lower(): entry.content
        for entry in base_entries or []
    }
    current_header_by_entry = {
        entry.entry_path.lower(): entry.content
        for entry in base_entries or []
    }
    processed_mods = 0
    processed_targets = 0
    generated = 0
    skipped = 0

    for mod in mods:
        try:
            target_specs = parse_format3_file(mod.path)
        except Exception as exc:
            errors.append(f"{mod.name}: Format 3 解析失败：{exc}")
            continue
        processed_mods += 1

        for target_spec in target_specs:
            processed_targets += 1
            resolved = _resolve_format3_target(game_dir, target_spec.target)
            if resolved is None:
                errors.append(f"{mod.name}: Format 3 目标未找到：{target_spec.target}")
                skipped += len(target_spec.intents)
                continue
            game_file, body_entry, header_entry = resolved
            if header_entry is None:
                warnings.append(f"{mod.name}: {game_file} 缺少 companion PABGH，已跳过")
                skipped += len(target_spec.intents)
                continue
            try:
                body_backup = vanilla_store.ensure_entry_backup(body_entry)
                header_backup = vanilla_store.ensure_entry_backup(header_entry)
                vanilla_body, _ = extract_plaintext(body_backup)
                vanilla_header, _ = extract_plaintext(header_backup)
            except Exception as exc:
                errors.append(f"{mod.name}: {game_file} vanilla 提取失败：{exc}")
                skipped += len(target_spec.intents)
                continue

            # Format 3 是最后一层语义补丁，必须叠加在 loose/JSON 已合成的
            # 基底上，否则同目标 iteminfo.pabgb 会覆盖前面已经生效的模组。
            body_key = body_entry.path.lower()
            header_key = header_entry.path.lower()
            current_body = current_body_by_entry.get(body_key, vanilla_body)
            current_header = current_header_by_entry.get(header_key, vanilla_header)
            dispatch_result = _format3_intents_to_result(
                game_file,
                current_body,
                current_header,
                list(target_spec.intents),
            )
            skipped += dispatch_result.skipped_count
            if dispatch_result.skipped:
                warnings.append(
                    _build_format3_skip_warning(
                        mod.name,
                        target_spec.target,
                        dispatch_result.skipped,
                    )
                )
            if not dispatch_result.changes:
                if not dispatch_result.skipped:
                    warnings.append(
                        f"{mod.name}: Format 3 没有可应用 intent，已跳过；目标 {target_spec.target}"
                    )
                continue

            grouped.setdefault(lower_game_rel_path(game_file), []).append(
                (
                    mod,
                    {
                        "game_file": game_file,
                        "changes": list(dispatch_result.changes),
                    },
                )
            )
            composed_body, composed_header = _apply_format3_changes_to_current_base(
                game_file,
                current_body,
                current_header,
                dispatch_result.changes,
            )
            if composed_body is not None:
                current_body_by_entry[body_key] = composed_body
            if composed_header is not None:
                current_header_by_entry[header_key] = composed_header
            generated += dispatch_result.change_count

    if mods:
        logger.info(
            "Format 3 bridge: 处理 %d 个模组、%d 个目标，%d 个生成补丁",
            processed_mods,
            processed_targets,
            generated,
        )
        if skipped:
            logger.info("Format 3 bridge: 跳过 %d 个 intent", skipped)
    return grouped


def _apply_format3_changes_to_current_base(
    game_file: str,
    current_body: bytes,
    current_header: bytes,
    changes: tuple[dict, ...],
) -> tuple[bytes | None, bytes | None]:
    """把已生成的 Format 3 补丁叠加到内存 base，供同目标后续模组继续合成。"""
    if not changes:
        return current_body, current_header

    table_name = Path(game_file.replace("\\", "/")).stem.lower()
    key_size, offsets = parse_pabgh_index(current_header, table_name)
    entry_bounds = build_entry_bounds(current_body, key_size, offsets) if offsets else {}
    name_offsets = _name_offsets_from_bounds(entry_bounds)

    body_changes: list[dict] = []
    header_changes: list[dict] = []
    for change in changes:
        companion = change.get("_pabgh_companion")
        if isinstance(companion, dict):
            header_changes.append(_strip_change_routing(companion))
        if _change_targets_header(change, game_file):
            header_changes.append(_strip_change_routing(change))
        else:
            body_changes.append(_strip_change_routing(change))

    body = bytearray(current_body)
    inserts_out: list[tuple[int, int]] = []
    if body_changes:
        applied, mismatched, _relocated = apply_byte_patches(
            body,
            body_changes,
            inserts_out=inserts_out,
            name_offsets=name_offsets,
        )
        if mismatched or applied != len(body_changes):
            logger.warning(
                "%s: Format 3 内存合成 body 补丁未完全应用：%d/%d",
                game_file,
                applied,
                len(body_changes),
            )
            return None, None

    header = bytearray(current_header)
    if header_changes:
        applied, mismatched, _relocated = apply_byte_patches(header, header_changes)
        if mismatched or applied != len(header_changes):
            logger.warning(
                "%s: Format 3 内存合成 header 补丁未完全应用：%d/%d",
                game_file,
                applied,
                len(header_changes),
            )
            return bytes(body), None
    elif inserts_out and game_file.lower().endswith(".pabgb"):
        header = bytearray(fixup_pabgh_after_inserts(bytes(header), inserts_out))

    return bytes(body), bytes(header)


def _name_offsets_from_bounds(
    entry_bounds: dict[int, tuple[int, int, str, int]],
) -> dict[str, int]:
    """由当前 entry bounds 构造 entry+rel_offset 锚点。"""
    offsets: dict[str, int] = {}
    for _key, (_start, _end, name, name_end) in entry_bounds.items():
        if name:
            offsets[name] = name_end
            offsets[name.lower()] = name_end
    return offsets


def _change_targets_header(change: dict, game_file: str) -> bool:
    """判断 change 是否显式路由到当前 PABGH companion。"""
    target = change.get("_target_file")
    if not isinstance(target, str):
        return False
    expected = lower_game_rel_path(game_file.rsplit(".", 1)[0] + ".pabgh")
    return lower_game_rel_path(target) == expected


def _strip_change_routing(change: dict) -> dict:
    """移除只给路由层使用的字段，便于内存合成直接应用 byte patch。"""
    clean = dict(change)
    clean.pop("_target_file", None)
    clean.pop("_pabgh_companion", None)
    return clean


def collect_format3_pamt_targets(mods: list[DiscoveredMod]) -> list[str]:
    """收集 Format 3 阶段会查询的 PABGB/PABGH 目标。"""
    targets: list[str] = []
    for mod in mods:
        try:
            target_specs = parse_format3_file(mod.path)
        except Exception:
            continue
        for target_spec in target_specs:
            body_target = lower_game_rel_path(target_spec.target)
            if not body_target.endswith(".pabgb"):
                body_target += ".pabgb"
            targets.append(body_target)
            targets.append(body_target.rsplit(".", 1)[0] + ".pabgh")
    return targets


def _format3_intents_to_result(
    game_file: str,
    vanilla_body: bytes,
    vanilla_header: bytes,
    intents: list[Format3Intent],
) -> Format3DispatchResult:
    """按目标表分发 Format 3 intent 写入器。"""
    table_name = Path(game_file.replace("\\", "/")).stem.lower()
    key_size, offsets = parse_pabgh_index(vanilla_header, table_name)
    if key_size not in (2, 4) or not offsets:
        return _skip_all_intents(intents, f"{table_name}.pabgh 索引无效")
    entry_bounds = build_entry_bounds(vanilla_body, key_size, offsets)
    writer = _FORMAT3_WRITERS.get(table_name)
    if writer is None:
        return _skip_all_intents(intents, f"目标表 {table_name} 暂无 writer")
    supported_intents, capability_skipped = partition_supported_intents(table_name, intents)
    if not supported_intents:
        return Format3DispatchResult(
            changes=(),
            skipped=capability_skipped,
        )
    context = Format3RuntimeContext(
        game_file=game_file,
        table_name=table_name,
        body=vanilla_body,
        header=vanilla_header,
        key_size=key_size,
        entry_bounds=entry_bounds,
    )
    writer_result = writer(context, supported_intents)
    return Format3DispatchResult(
        changes=writer_result.changes,
        skipped=capability_skipped + writer_result.skipped,
    )


def _skip_all_intents(
    intents: list[Format3Intent],
    reason: str,
) -> Format3DispatchResult:
    """当目标表整体不可处理时，为全部 intents 生成统一跳过结果。"""
    return Format3DispatchResult(
        changes=(),
        skipped=tuple(
            Format3SkippedIntent(intent=intent, reason=reason)
            for intent in intents
        ),
    )


def _build_format3_skip_warning(
    mod_name: str,
    target: str,
    skipped: tuple[Format3SkippedIntent, ...],
) -> str:
    """把跳过原因压缩成用户可读 warning。"""
    summary = summarize_skip_reasons(skipped)
    if summary:
        return f"{mod_name}: Format 3 目标 {target} 跳过 {len(skipped)} 个 intent；{summary}"
    return f"{mod_name}: Format 3 目标 {target} 跳过 {len(skipped)} 个 intent"


def _resolve_format3_target(game_dir: Path, target: str) -> tuple[str, PazEntry, PazEntry | None] | None:
    """优先从原版低编号 PAMT 查找 Format 3 目标，避免误命中旧 overlay。"""
    body_entry = _find_preferred_game_entry(game_dir, target, suffix=".pabgb")
    if body_entry is None:
        return None
    body_path = body_entry.path
    header_target = body_path.rsplit(".", 1)[0] + ".pabgh"
    header_entry = _find_preferred_game_entry(game_dir, header_target, suffix=".pabgh")
    return body_path, body_entry, header_entry


def _find_preferred_game_entry(game_dir: Path, target: str, suffix: str) -> PazEntry | None:
    """查找目标 entry，完整路径优先、gamedata 优先、低编号目录优先。"""
    normalized = lower_game_rel_path(target)
    if not normalized.endswith(suffix):
        normalized += suffix
    index = get_game_pamt_index(game_dir)
    best = index.find_best(target, suffix=suffix, require_unique_best=False)
    if best is None:
        return None
    if lower_game_rel_path(best.path) != normalized:
        logger.info("按 Format 3 basename 匹配 %s -> %s", target, best.path)
    return best
