"""使用原版游戏表展开 cdmod 动态选择器。

该服务只读取游戏 PAZ/PAMT，不写入游戏目录。当前仅支持真实插槽模组使用的
``iteminfo.equip_type_info`` match，后续新增匹配字段必须沿用表级窄支持。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from cdmm.services.cdmod_package import CdmodOperation, CdmodPackage
from cdmm.services.format3_loader import (
    collect_iteminfo_match_records,
    iteminfo_record_matches,
)
from cdmm.services.json_loader import extract_plaintext
from cdmm.services.pab_table_service import build_entry_bounds, parse_pabgh_index
from cdmm.services.pamt_index_service import get_game_pamt_index

# 第一版动态选择器只支持 ItemInfo 前缀中可快速读取的装备类型字段。
SUPPORTED_ITEMINFO_MATCH_FIELDS = frozenset({"equip_type_info"})


def resolve_cdmod_dynamic_selectors(
    packages: tuple[CdmodPackage, ...],
    game_dir: Path,
) -> tuple[tuple[CdmodPackage, ...], tuple[str, ...]]:
    """把支持的动态 match 操作展开为具体 key/string_key 操作。"""
    dynamic_operations = [
        operation
        for package in packages
        for operation in package.operations
        if _requires_resolution(operation)
    ]
    if not dynamic_operations:
        return packages, ()

    iteminfo_records, load_errors = _load_iteminfo_match_records(game_dir)
    if load_errors:
        return packages, load_errors

    errors: list[str] = []
    resolved_packages: list[CdmodPackage] = []
    for package in packages:
        resolved_operations: list[CdmodOperation] = []
        next_index = 0
        for operation in package.operations:
            if not _requires_resolution(operation):
                resolved_operations.append(replace(operation, index=next_index))
                next_index += 1
                continue
            match_spec = operation.selector.get("match")
            unsupported = sorted(set(match_spec) - SUPPORTED_ITEMINFO_MATCH_FIELDS)
            if operation.target.rsplit("/", 1)[-1] != "iteminfo.pabgb" or unsupported:
                errors.append(
                    f"{package.mod_id}#{operation.index}: 动态选择器暂不支持 "
                    f"{operation.target} / {', '.join(unsupported) or '该目标表'}"
                )
                resolved_operations.append(replace(operation, index=next_index))
                next_index += 1
                continue
            matches = [
                record
                for record in iteminfo_records
                if iteminfo_record_matches(record, match_spec)
            ]
            if not matches:
                errors.append(f"{package.mod_id}#{operation.index}: 动态选择器未命中 iteminfo 记录")
                resolved_operations.append(replace(operation, index=next_index))
                next_index += 1
                continue
            for record in matches:
                key = record.get("key")
                string_key = record.get("string_key")
                if not isinstance(key, int) or isinstance(key, bool):
                    continue
                selector = {"key": key}
                if isinstance(string_key, str) and string_key:
                    selector["string_key"] = string_key
                resolved_operations.append(
                    replace(
                        operation,
                        selector=selector,
                        index=next_index,
                    )
                )
                next_index += 1
        resolved_packages.append(
            replace(package, operations=tuple(resolved_operations))
        )
    return tuple(resolved_packages), tuple(errors)


def _load_iteminfo_match_records(game_dir: Path) -> tuple[list[dict[str, object]], tuple[str, ...]]:
    """从低编号原版包读取 iteminfo body/header 并提取轻量匹配字段。"""
    index = get_game_pamt_index(game_dir.resolve())
    body_entry = index.find_best("iteminfo.pabgb", suffix=".pabgb", require_unique_best=False)
    if body_entry is None:
        return [], ("无法在游戏 PAMT 中定位 iteminfo.pabgb",)
    header_target = body_entry.path.rsplit(".", 1)[0] + ".pabgh"
    header_entry = index.find_best(header_target, suffix=".pabgh", require_unique_best=False)
    if header_entry is None:
        return [], ("无法在游戏 PAMT 中定位 iteminfo.pabgh",)
    try:
        body, _ = extract_plaintext(body_entry)
        header, _ = extract_plaintext(header_entry)
    except (OSError, ValueError) as exc:
        return [], (f"读取原版 iteminfo 失败：{exc}",)

    key_size, offsets = parse_pabgh_index(header, "iteminfo")
    entry_bounds = build_entry_bounds(body, key_size, offsets)
    if not entry_bounds:
        return [], ("原版 iteminfo.pabgh 未解析出有效记录边界",)
    records = collect_iteminfo_match_records(body, entry_bounds)
    if not records:
        return [], ("原版 iteminfo 未解析出动态匹配前缀",)
    return records, ()


def _requires_resolution(operation: CdmodOperation) -> bool:
    """判断操作是否只有 match 而没有具体记录身份。"""
    selector = operation.selector
    key = selector.get("key")
    has_key = isinstance(key, int) and not isinstance(key, bool) and key != 0
    has_name = isinstance(selector.get("string_key"), str) and bool(selector["string_key"])
    return bool(selector.get("match")) and not has_key and not has_name
