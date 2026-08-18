"""传统 JSON byte patch 加载与 overlay entry 生成。"""

from __future__ import annotations

import logging
import os
import struct
from bisect import bisect_right
from pathlib import Path
from time import perf_counter

from cdmm.archive.pamt import derive_pamt_dir
from cdmm.archive.paz_crypto import decrypt, lz4_decompress
from cdmm.common.models import DiscoveredMod, OverlayInputEntry, PazEntry
from cdmm.services.pabgh_rewrite import rewrite_pabgh_offsets
from cdmm.services.pab_table_service import parse_entry_name_end, parse_pabgh_index
from cdmm.services.pamt_index_service import get_game_pamt_index
from cdmm.services.scanner import MOD_TYPE_CDMOD, load_json_file
from cdmm.storage.vanilla_store import VanillaStore
from cdmm.utils.path_utils import lower_game_rel_path

logger = logging.getLogger(__name__)

# Format 3 的动态 entry-relative 补丁可以因同一 entry 前面的长度变化
# 产生小范围位移，但不能全表扫描到另一个 entry 的相同字节。
DYNAMIC_ENTRY_RELOCATION_WINDOW = 512

DATA_TABLE_SUFFIXES = (".pabgb", ".pabgh", ".pamt")

# DMM 对 8 字节旧版 JSON 补丁会允许小范围最近匹配，超过该距离会跳过以免误写。
NEAR_PATTERN_RELOCATION_LIMIT = 4000

# JSON 阶段耗时超过该阈值时写入细分日志，方便定位慢模组。
JSON_PROFILE_LOG_THRESHOLD_SECONDS = 0.25


def build_json_overlay_entries(
    game_dir: Path,
    mods: list[DiscoveredMod],
    vanilla_store: VanillaStore,
    warnings: list[str],
    errors: list[str],
    base_entries: list[OverlayInputEntry] | None = None,
) -> list[OverlayInputEntry]:
    """读取所有传统 JSON 模组，生成待写入 overlay 的 entry。"""
    started = perf_counter()
    grouped: dict[str, list[tuple[DiscoveredMod, dict]]] = {}
    resolved_game_files: dict[str, str] = {}
    mod_stats: dict[str, dict[str, float | int]] = {}
    for mod in mods:
        mod_started = perf_counter()
        try:
            documents = _load_patch_documents(mod)
        except Exception as exc:
            errors.append(f"{mod.name}: JSON 读取失败：{exc}")
            continue
        mod_patch_count = 0
        mod_change_count = 0
        for data in documents:
            for patch in data.get("patches", []):
                if not _is_patch_block(patch):
                    continue
                patch = dict(patch)
                patch["_allow_partial_apply"] = _allow_partial_apply(data)
                game_file = str(patch["game_file"])
                group_key = lower_game_rel_path(game_file)
                resolved = _find_patch_target_entry(game_file, game_dir)
                if resolved is not None:
                    group_key = lower_game_rel_path(resolved.path)
                    resolved_game_files[group_key] = resolved.path
                grouped.setdefault(group_key, []).append((mod, patch))
                mod_patch_count += 1
                mod_change_count += len(patch.get("changes", []))
        mod_stats[mod.name] = {
            "load_group_seconds": perf_counter() - mod_started,
            "patch_count": mod_patch_count,
            "change_count": mod_change_count,
        }

    grouping_seconds = perf_counter() - started
    overlay_entries = build_patch_overlay_entries(
        game_dir,
        grouped,
        vanilla_store,
        warnings,
        errors,
        base_entries,
        resolved_game_files,
        mod_stats=mod_stats,
    )
    logger.info(
        "JSON 耗时：总计 %.2fs，读取/分组 %.2fs，目标 %d 个，overlay entry %d 个",
        perf_counter() - started,
        grouping_seconds,
        len(grouped),
        len(overlay_entries),
    )
    return overlay_entries


def collect_json_pamt_targets(mods: list[DiscoveredMod]) -> list[str]:
    """收集传统 JSON byte patch 会查询的目标路径。"""
    targets: list[str] = []
    for mod in mods:
        if mod.mod_type == MOD_TYPE_CDMOD:
            # cdmod 的 legacy JSON 目标已包含在轻量组件索引中；这里不能为
            # 收集路径而完整解压数百MB payload。语义目标收集阶段会统一注册。
            continue
        try:
            documents = _load_patch_documents(mod)
        except Exception:
            continue
        for data in documents:
            for patch in data.get("patches", []):
                if not _is_patch_block(patch):
                    continue
                game_file = str(patch["game_file"])
                targets.append(game_file)
                if game_file.lower().endswith(".pabgb"):
                    targets.append(game_file.rsplit(".", 1)[0] + ".pabgh")
    return targets


def _load_patch_documents(mod: DiscoveredMod) -> list[dict]:
    """统一读取旧 JSON 文件和 cdmod 内的 legacy-byte-patch 组件。"""
    if mod.mod_type != MOD_TYPE_CDMOD:
        return [load_json_file(mod.path)]
    from cdmm.services.cdmod_package import load_cdmod_package

    return [dict(document) for document in load_cdmod_package(mod.path).legacy_json_patches]


def build_patch_overlay_entries(
    game_dir: Path,
    grouped: dict[str, list[tuple[DiscoveredMod, dict]]],
    vanilla_store: VanillaStore,
    warnings: list[str],
    errors: list[str],
    base_entries: list[OverlayInputEntry] | None = None,
    resolved_game_files: dict[str, str] | None = None,
    mod_stats: dict[str, dict[str, float | int]] | None = None,
) -> list[OverlayInputEntry]:
    """把已经聚合好的 byte patch block 应用到 vanilla，并生成 overlay entry。"""
    grouped, resolved_game_files = _expand_grouped_patch_items(
        grouped,
        resolved_game_files,
    )
    base_by_entry = {
        entry.entry_path.lower(): entry
        for entry in base_entries or []
    }
    overlay_entries: list[OverlayInputEntry] = []
    auto_companions: dict[str, OverlayInputEntry] = {}
    companion_bodies: dict[str, bytes] = {}
    for group_key, patch_items in grouped.items():
        group_started = perf_counter()
        first_patch = patch_items[0][1]
        original_game_file = str(first_patch["game_file"])
        game_file = (resolved_game_files or {}).get(
            group_key,
            original_game_file,
        )
        entry = _find_patch_target_entry(game_file, game_dir)
        if entry is None:
            errors.append(f"{game_file}: 未在任何 PAMT 中找到目标文件")
            continue

        try:
            vanilla_entry = vanilla_store.ensure_entry_backup(entry)
            plaintext, vanilla_entry = extract_plaintext(vanilla_entry)
        except Exception as exc:
            errors.append(f"{game_file}: vanilla 提取失败：{exc}")
            continue

        base_entry = base_by_entry.get(vanilla_entry.path.lower())
        base_plaintext = base_entry.content if base_entry is not None else plaintext
        modified = bytearray(base_plaintext)
        name_offsets = _build_name_offsets(
            game_dir,
            vanilla_store,
            game_file,
            base_plaintext,
            errors,
            base_by_entry,
        )
        had_mismatch = False
        total_applied = 0
        total_mismatched = 0
        already_patched = 0
        need_already_patched_check = False
        inserts_out: list[tuple[int, int]] = []
        reject_partial_items: list[tuple[DiscoveredMod, dict]] = []

        for mod, patch in patch_items:
            patch_started = perf_counter()
            changes = patch.get("changes", [])
            if not changes:
                continue
            patch_name_offsets = name_offsets
            if _has_dynamic_entry_changes(changes):
                current_header = _build_current_pabgh_for_body(
                    game_dir,
                    vanilla_store,
                    game_file,
                    base_plaintext,
                    bytes(modified),
                    errors,
                    base_by_entry,
                )
                patch_name_offsets = _build_name_offsets(
                    game_dir,
                    vanilla_store,
                    game_file,
                    bytes(modified),
                    errors,
                    base_by_entry,
                    header_override=current_header,
                )
            applied, mismatched, relocated = apply_byte_patches(
                modified,
                changes,
                signature=patch.get("signature"),
                vanilla_data=plaintext,
                inserts_out=inserts_out,
                name_offsets=patch_name_offsets,
            )
            total_applied += applied
            total_mismatched += mismatched
            _record_json_mod_apply_stat(
                mod_stats,
                mod.name,
                perf_counter() - patch_started,
                len(changes),
                applied,
                mismatched,
                relocated,
            )
            _log_slow_json_patch(
                mod.name,
                game_file,
                perf_counter() - patch_started,
                len(changes),
                applied,
                mismatched,
                relocated,
            )
            if applied == 0:
                need_already_patched_check = True
            if relocated:
                logger.info("%s: %s 发生 %d 个偏移重定位", mod.name, game_file, relocated)
            if mismatched:
                had_mismatch = True
                if not bool(patch.get("_allow_partial_apply")):
                    reject_partial_items.append((mod, patch))
                warnings.append(
                    f"{mod.name}: {game_file} 有 {mismatched}/{applied + mismatched} 个补丁未匹配"
                )

        if total_mismatched > 0 and _should_skip_partial_data_table(game_file, reject_partial_items, inserts_out):
            allowed_names = ", ".join(sorted({mod.name for mod, _patch in patch_items}))
            warnings.append(
                f"{game_file}: 数据表补丁存在 {total_mismatched}/{total_applied + total_mismatched} "
                f"个未匹配且存在长度变化，已跳过整个目标以避免游戏闪退；相关模组：{allowed_names}"
            )
            continue
        if had_mismatch:
            allowed_names = ", ".join(sorted({mod.name for mod, _patch in patch_items}))
            warnings.append(
                f"{game_file}: 存在未匹配补丁，已继续半应用，相关模组：{allowed_names}"
            )
        if total_applied == 0:
            warnings.append(f"{game_file}: 没有任何补丁成功应用")
            continue
        if need_already_patched_check:
            already_patched = sum(
                _count_already_patched(patch.get("changes", []), base_plaintext)
                for _mod, patch in patch_items
            )
        if bytes(modified) == base_plaintext and base_entry is None and already_patched <= 0:
            warnings.append(f"{game_file}: 补丁应用后内容未变化")
            continue

        overlay_entries.append(
            OverlayInputEntry(
                content=bytes(modified),
                entry_path=vanilla_entry.path,
                pamt_dir=derive_pamt_dir(vanilla_entry.paz_file),
                compression_type=vanilla_entry.compression_type,
                encrypted=vanilla_entry.encrypted,
                crypto_filename=os.path.basename(vanilla_entry.path),
                resolved_dir_path=vanilla_entry.resolved_dir_path,
            )
        )

        # 如果 .pabgb 发生 insert，必须同步修正 companion .pabgh 的指针。
        if game_file.lower().endswith(".pabgb"):
            companion = _build_pabgh_companion(
                game_dir,
                vanilla_store,
                game_file,
                inserts_out,
                errors,
                base_by_entry,
                body_before=base_plaintext,
                body_after=bytes(modified),
                required=bool(inserts_out),
            )
            if companion is not None:
                overlay_entries.append(companion)
                auto_companions[companion.entry_path.lower()] = companion
                companion_bodies[companion.entry_path.lower()] = bytes(modified)
        _log_slow_json_group(
            game_file,
            perf_counter() - group_started,
            patch_items,
            total_applied,
            total_mismatched,
        )

    # 同路径 PABGH 可能同时存在显式 _pabgh_companion（单模组整表重写）
    # 与自动 companion（基于全部 body insert 生成）。两者只能保留一个：
    # - entry 级变长补丁：自动 companion 合并了所有模组位移，是最终
    #   权威版本；显式 companion 会因先被其他模组改写而失配或只含
    #   单模组位移，必须让位。
    # - 整表 replace（offset 0 覆盖整个 body）：insert 位移模型无法表达
    #   “全部 entry 平移”，自动 companion 校验必然失败，保留显式版本。
    # 这里用「自动 companion 的每个 offset 处是否真的对应该 key」校验，
    # 通过则去掉同路径旧版本只留自动 companion，否则丢弃自动版本。
    if auto_companions:
        valid_autos: dict[str, OverlayInputEntry] = {}
        for path, companion in auto_companions.items():
            body = companion_bodies.get(path)
            if body is not None and _pabgh_matches_body(companion.content, body):
                valid_autos[path] = companion
        auto_ids = {id(companion) for companion in auto_companions.values()}
        # 先移除全部自动 companion；校验通过的随后重新加入并放在最后，
        # 确保它比同路径显式 companion 后写，取得最终覆盖权。
        kept = [
            entry
            for entry in overlay_entries
            if id(entry) not in auto_ids
        ]
        if valid_autos:
            valid_paths = set(valid_autos)
            kept = [
                entry
                for entry in kept
                if entry.entry_path.lower() not in valid_paths
            ]
        kept.extend(valid_autos.values())
        overlay_entries = kept

    _log_json_mod_profile(mod_stats)

    return overlay_entries


def _pabgh_matches_body(pabgh: bytes, body: bytes) -> bool:
    """校验 PABGH 每个 entry 的 offset 处是否真的是对应 key。"""
    count_size, count, key_size = _detect_pabgh_layout(pabgh)
    if count_size is None:
        return False
    row_size = key_size + 4
    if len(pabgh) < count_size + count * row_size:
        return False
    for index in range(count):
        pos = count_size + index * row_size
        key = struct.unpack_from("<I", pabgh, pos)[0] & ((1 << (key_size * 8)) - 1)
        offset = struct.unpack_from("<I", pabgh, pos + key_size)[0]
        if offset + key_size > len(body):
            return False
        actual = struct.unpack_from("<I", body, offset)[0] & ((1 << (key_size * 8)) - 1)
        if actual != key:
            return False
    return True


def extract_plaintext(entry: PazEntry) -> tuple[bytes, PazEntry]:
    """从 PAZ entry 读取并解压/解密出明文内容。"""
    with Path(entry.paz_file).open("rb") as handle:
        handle.seek(entry.offset)
        raw = handle.read(entry.comp_size)
    return decompress_entry(raw, entry)


def decompress_entry(raw: bytes, entry: PazEntry) -> tuple[bytes, PazEntry]:
    """按 PAMT flags 解压单个 entry，必要时自动探测加密。"""
    basename = os.path.basename(entry.path)
    if entry.compression_type == 1:
        header_size = 128
        header = raw[:header_size]
        compressed_body = raw[header_size:]
        body_orig_size = entry.orig_size - header_size
        inner_comp_size = struct.unpack_from("<I", header, 32)[0] if len(header) >= 36 else 0
        lz4_input = (
            compressed_body[:inner_comp_size]
            if 0 < inner_comp_size < len(compressed_body)
            else compressed_body
        )
        for candidate in (lz4_input, compressed_body):
            try:
                return header + lz4_decompress(candidate, body_orig_size), entry
            except Exception:
                try:
                    decrypted = decrypt(candidate, basename)
                    return (
                        header + lz4_decompress(decrypted, body_orig_size),
                        entry.with_encrypted_override(True),
                    )
                except Exception:
                    continue
        logger.info("DDS %s 无法按 LZ4 解压，按原始数据透传", entry.path)
        return raw, entry

    if entry.compressed and entry.compression_type == 2:
        try:
            return lz4_decompress(raw, entry.orig_size), entry
        except Exception:
            decrypted = decrypt(raw, basename)
            return lz4_decompress(decrypted, entry.orig_size), entry.with_encrypted_override(True)

    if entry.encrypted:
        return decrypt(raw, basename), entry
    return raw, entry


def apply_byte_patches(
    data: bytearray,
    changes: list[dict],
    *,
    signature: str | None = None,
    vanilla_data: bytes | None = None,
    inserts_out: list[tuple[int, int]] | None = None,
    name_offsets: dict[str, int] | None = None,
) -> tuple[int, int, int]:
    """应用传统 JSON byte patch，返回 applied/mismatched/relocated 数量。"""
    original_snapshot = bytes(data) if signature else None
    base_offset = _resolve_signature_base(data, signature)
    if base_offset is None:
        return 0, len(changes), 0

    applied = 0
    mismatched = 0
    relocated = 0
    writes: list[tuple[int, int]] = []
    written_replacements: dict[tuple[int, int], bytes] = {}
    total_shift = 0
    write_cursor = 0

    def shift_for(position: int) -> int:
        nonlocal total_shift, write_cursor
        while write_cursor < len(writes) and writes[write_cursor][0] < position:
            total_shift += writes[write_cursor][1]
            write_cursor += 1
        return total_shift

    parsed_changes = []
    for change in changes:
        offset = _parse_change_offset(change, base_offset, name_offsets)
        if offset is None:
            mismatched += 1
            continue
        parsed = _parse_patch_change(change)
        if parsed is None:
            mismatched += 1
            continue
        dynamic_entry_offset = bool(change.get("_dynamic_entry_offset"))
        parsed_changes.append((offset, change, *parsed, dynamic_entry_offset))
    # DMM 的 V1/V2 replace 会保留 JSON 原始顺序；只有长度变化时才需要按 offset 计算位移。
    has_length_delta = any(
        len(patched_bytes) != old_len
        for _offset, _change, patched_bytes, _original_bytes, old_len, _dynamic in parsed_changes
    )
    if has_length_delta:
        parsed_changes.sort(key=lambda item: item[0])

    for original_offset, change, patched_bytes, original_bytes, old_len, dynamic_entry_offset in parsed_changes:
        if writes and not dynamic_entry_offset:
            offset = original_offset + shift_for(original_offset)
        else:
            offset = original_offset
        if change.get("type") == "insert":
            if offset > len(data):
                mismatched += 1
                continue
            if original_bytes is not None and data[offset:offset + old_len] != original_bytes:
                mismatched += 1
                continue
            data[offset:offset] = patched_bytes
            writes.append((original_offset, len(patched_bytes)))
            if inserts_out is not None:
                inserts_out.append((original_offset, len(patched_bytes)))
            applied += 1
            continue

        if offset + old_len > len(data):
            new_offset = (
                _pattern_scan(data, original_offset, original_bytes, vanilla_data)
                if original_bytes is not None
                else None
            )
            if (
                new_offset is None
                or new_offset + old_len > len(data)
                or data[new_offset:new_offset + old_len] != original_bytes
                or (
                    dynamic_entry_offset
                    and not _is_near_dynamic_relocation(original_offset, new_offset)
                )
            ):
                mismatched += 1
                continue
            offset = new_offset
            relocated += 1
        if original_bytes is not None and data[offset:offset + old_len] != original_bytes:
            if data[offset:offset + len(patched_bytes)] == patched_bytes:
                applied += 1
                continue
            prior_replacement = _get_prior_same_range_rewrite(
                data,
                offset,
                original_offset,
                old_len,
                written_replacements,
            )
            new_offset = _pattern_scan(data, original_offset, original_bytes, vanilla_data)
            if prior_replacement is not None:
                if (
                    new_offset is not None
                    and new_offset != offset
                    and new_offset + old_len <= len(data)
                    and data[new_offset:new_offset + old_len] == original_bytes
                    and (
                        not dynamic_entry_offset
                        or _is_near_dynamic_relocation(original_offset, new_offset)
                    )
                ):
                    data[new_offset:new_offset + old_len] = patched_bytes
                    writes.append((original_offset, 0))
                    applied += 1
                    relocated += 1
                    continue
                applied += 1
                continue
            if (
                new_offset is None
                or new_offset + old_len > len(data)
                or data[new_offset:new_offset + old_len] != original_bytes
                or (
                    dynamic_entry_offset
                    and not _is_near_dynamic_relocation(original_offset, new_offset)
                )
            ):
                mismatched += 1
                continue
            offset = new_offset
            relocated += 1
        data[offset:offset + old_len] = patched_bytes
        writes.append((original_offset, len(patched_bytes) - old_len))
        written_replacements[(original_offset, old_len)] = patched_bytes
        if inserts_out is not None and len(patched_bytes) != old_len:
            # 长度变化的 replace 只会移动被替换旧片段之后的 entry。
            # 若把 delta 记在 replace 起点，刚好从 entry 开头替换时会把
            # 当前 entry 的 PABGH 指针也推走，游戏会从记录中间读取 _key。
            inserts_out.append((original_offset + old_len, len(patched_bytes) - old_len))
        applied += 1

    if signature and applied == 0 and mismatched > 0 and original_snapshot is not None:
        # 兼容带过期 signature 但实际仍使用绝对 offset 的旧 JSON。
        data[:] = original_snapshot
        return apply_byte_patches(
            data,
            changes,
            signature=None,
            vanilla_data=vanilla_data,
            inserts_out=inserts_out,
            name_offsets=name_offsets,
        )
    return applied, mismatched, relocated


def _is_near_dynamic_relocation(original_offset: int, new_offset: int) -> bool:
    """限制动态 entry 补丁只能在 entry 附近重定位。"""
    return abs(new_offset - original_offset) <= DYNAMIC_ENTRY_RELOCATION_WINDOW


def _get_prior_same_range_rewrite(
    data: bytearray,
    offset: int,
    original_offset: int,
    old_len: int,
    written_replacements: dict[tuple[int, int], bytes],
) -> bytes | None:
    """识别同一 patch 内重复 offset，后续声明不应覆盖前一次成功写入。"""
    prior = written_replacements.get((original_offset, old_len))
    if prior is None:
        return None
    if data[offset:offset + len(prior)] != prior:
        return None
    return prior


def _parse_patch_change(change: dict) -> tuple[bytes, bytes | None, int] | None:
    """提前解析 JSON change 的 hex 字段，避免应用阶段重复转换。"""
    if change.get("type") == "insert":
        insert_bytes = _bytes_from_hex(change.get("bytes"))
        if insert_bytes is None:
            return None
        original_bytes = _bytes_from_hex(change.get("original"))
        old_len = len(original_bytes) if original_bytes is not None else 0
        return insert_bytes, original_bytes, old_len
    patched_bytes = _bytes_from_hex(change.get("patched"))
    if patched_bytes is None:
        return None
    original_bytes = _bytes_from_hex(change.get("original"))
    old_len = len(original_bytes) if original_bytes is not None else len(patched_bytes)
    return patched_bytes, original_bytes, old_len


def fixup_pabgh_after_inserts(pabgh: bytes, inserts: list[tuple[int, int]]) -> bytes:
    """PABGB 长度变化后修正 PABGH 指针表，支持正负 delta。"""
    if not inserts or len(pabgh) < 2:
        return pabgh
    layout = _detect_pabgh_layout(pabgh)
    if layout is None:
        return pabgh
    count_size, count, key_size = layout
    row_size = key_size + 4
    data = bytearray(pabgh)
    count = min(count, (len(data) - count_size) // row_size)
    sorted_inserts = sorted(inserts, key=lambda item: item[0])
    insert_offsets: list[int] = []
    cumulative_deltas: list[int] = []
    running_delta = 0
    for insert_offset, size in sorted_inserts:
        running_delta += size
        insert_offsets.append(insert_offset)
        cumulative_deltas.append(running_delta)
    for index in range(count):
        pos = count_size + index * row_size
        pointer = struct.unpack_from("<I", data, pos + key_size)[0]
        delta_index = bisect_right(insert_offsets, pointer) - 1
        delta = cumulative_deltas[delta_index] if delta_index >= 0 else 0
        if delta:
            struct.pack_into("<I", data, pos + key_size, pointer + delta)
    return bytes(data)


def _detect_pabgh_layout(data: bytes) -> tuple[int, int, int] | None:
    """返回 PABGH 索引布局 (count_size, count, key_size)。

    同时尝试 u32/u16 count 两种头部，按 key_size = 2/4 且整除校验，
    优先剩余字节更少的候选，避免 u32 count 表被 u16 头部误判
    （旧启发式会把 stageinfo 这类 4+count*8 精确适配的表读成 fmt2，
    修正指针时按错误行宽写入，导致索引 key 被覆盖损坏）。
    """
    n = len(data)
    best: tuple[int, int, int, int, int] | None = None
    for count_size in (4, 2):
        if n < count_size:
            continue
        count = (
            struct.unpack_from("<I", data, 0)[0]
            if count_size == 4
            else struct.unpack_from("<H", data, 0)[0]
        )
        if count <= 0:
            continue
        total_key_bytes = n - count_size - count * 4
        if total_key_bytes <= 0 or total_key_bytes % count:
            continue
        key_size = total_key_bytes // count
        if key_size not in (2, 4):
            continue
        expected = count_size + count * (key_size + 4)
        slack = n - expected
        if slack < 0:
            continue
        candidate = (slack, -count_size, count_size, count, key_size)
        if best is None or candidate < best:
            best = candidate
    if best is None:
        return None
    return best[2], best[3], best[4]


def _build_pabgh_companion(
    game_dir: Path,
    vanilla_store: VanillaStore,
    game_file: str,
    inserts: list[tuple[int, int]],
    errors: list[str],
    base_by_entry: dict[str, OverlayInputEntry] | None = None,
    *,
    body_before: bytes | None = None,
    body_after: bytes | None = None,
    required: bool = True,
) -> OverlayInputEntry | None:
    """构造 insert 场景需要同步输出的 .pabgh companion entry。"""
    pabgh_file = game_file.rsplit(".", 1)[0] + ".pabgh"
    entry = _find_patch_target_entry(pabgh_file, game_dir)
    if entry is None:
        if required:
            errors.append(f"{game_file}: 存在 insert 但找不到 companion PABGH：{pabgh_file}")
        return None
    try:
        vanilla_entry = vanilla_store.ensure_entry_backup(entry)
        plaintext, vanilla_entry = extract_plaintext(vanilla_entry)
    except Exception as exc:
        errors.append(f"{pabgh_file}: 提取失败：{exc}")
        return None
    base_entry = (base_by_entry or {}).get(vanilla_entry.path.lower())
    if base_entry is not None:
        plaintext = base_entry.content
    fixed = fixup_pabgh_after_inserts(plaintext, inserts)
    repaired = _repair_pabgh_offsets_from_body(
        fixed,
        game_file,
        reference_header=plaintext,
        reference_body=body_before,
        final_body=body_after,
    )
    if repaired is not None:
        fixed = repaired
    return OverlayInputEntry(
        content=fixed,
        entry_path=vanilla_entry.path,
        pamt_dir=derive_pamt_dir(vanilla_entry.paz_file),
        compression_type=vanilla_entry.compression_type,
        encrypted=vanilla_entry.encrypted,
        crypto_filename=os.path.basename(vanilla_entry.path),
        resolved_dir_path=vanilla_entry.resolved_dir_path,
    )


def _repair_pabgh_offsets_from_body(
    header: bytes,
    game_file: str,
    *,
    reference_header: bytes,
    reference_body: bytes | None,
    final_body: bytes | None,
) -> bytes | None:
    """用最终 PABGB 记录起点反修 PABGH，兜底复杂 Format 3 变长组合。"""
    if reference_body is None or final_body is None:
        return None
    table_name = Path(game_file.replace("\\", "/")).stem.lower()
    if table_name != "iteminfo":
        return None
    key_size, fixed_offsets = parse_pabgh_index(header, table_name)
    if key_size not in (2, 4) or not fixed_offsets:
        return None

    _, reference_offsets = parse_pabgh_index(reference_header, table_name)
    expected_names: dict[int, str] = {}
    for key, offset in reference_offsets.items():
        parsed = parse_entry_name_end(reference_body, offset, key_size)
        if parsed is not None:
            expected_names[key] = parsed[0]

    repaired_offsets: dict[int, int] = {}
    cursor = 0
    for key, estimated_offset in sorted(fixed_offsets.items(), key=lambda item: item[1]):
        expected_name = expected_names.get(key)
        found = _find_entry_offset_by_key_name(
            final_body,
            key,
            key_size,
            expected_name,
            cursor,
            estimated_offset,
        )
        if found is None:
            return None
        repaired_offsets[key] = found
        cursor = found + key_size

    if repaired_offsets == fixed_offsets:
        return None
    return rewrite_pabgh_offsets(header, table_name, repaired_offsets)


def _find_entry_offset_by_key_name(
    body: bytes,
    key: int,
    key_size: int,
    expected_name: str | None,
    cursor: int,
    estimated_offset: int,
) -> int | None:
    """在最终 body 里按 key + string_key 定位真实 entry 起点。"""
    needle = int(key).to_bytes(key_size, "little", signed=False)
    search_windows = (4096, 65536, 262144, len(body))
    best: tuple[int, int] | None = None
    for window in search_windows:
        start = max(cursor, estimated_offset - window)
        end = min(len(body), estimated_offset + window)
        if window >= len(body):
            start = cursor
            end = len(body)
        pos = body.find(needle, start, end)
        while pos >= 0:
            parsed = parse_entry_name_end(body, pos, key_size)
            if parsed is not None:
                name, _name_end = parsed
                if expected_name is None or name == expected_name:
                    distance = abs(pos - estimated_offset)
                    if best is None or distance < best[0]:
                        best = (distance, pos)
            pos = body.find(needle, pos + 1, end)
        if best is not None:
            return best[1]
    return None


def _count_already_patched(changes: list[dict], data: bytes) -> int:
    """粗略统计当前内容中已经存在 patched bytes 的补丁数量。"""
    count = 0
    for change in changes:
        patched = _bytes_from_hex(change.get("patched"))
        if patched and patched in data:
            count += 1
    return count


def _build_name_offsets(
    game_dir: Path,
    vanilla_store: VanillaStore,
    game_file: str,
    body: bytes,
    errors: list[str],
    base_by_entry: dict[str, OverlayInputEntry] | None = None,
    *,
    header_override: bytes | None = None,
) -> dict[str, int] | None:
    """构建 entry 名称到 name_end 的映射，用于 Format 3 的 entry+rel_offset。"""
    if not game_file.lower().endswith(".pabgb"):
        return None
    pabgh_file = game_file.rsplit(".", 1)[0] + ".pabgh"
    entry = _find_patch_target_entry(pabgh_file, game_dir)
    if entry is None:
        return None
    if header_override is not None:
        header = header_override
    else:
        try:
            vanilla_entry = vanilla_store.ensure_entry_backup(entry)
            header, _ = extract_plaintext(vanilla_entry)
        except Exception as exc:
            errors.append(f"{pabgh_file}: entry 名称索引构建失败：{exc}")
            return None
        base_header = (base_by_entry or {}).get(vanilla_entry.path.lower())
        if base_header is not None:
            header = base_header.content
    table_name = Path(game_file.replace("\\", "/")).stem.lower()
    key_size, offsets = parse_pabgh_index(header, table_name)
    if key_size not in (2, 4) or not offsets:
        return None
    names: dict[str, int] = {}
    for offset in offsets.values():
        parsed = parse_entry_name_end(body, offset, key_size)
        if parsed is None:
            continue
        name, name_end = parsed
        if name:
            names[name] = name_end
            names[name.lower()] = name_end
    return names


def _has_dynamic_entry_changes(changes: list[dict]) -> bool:
    """判断 patch 是否包含需要当前 entry 锚点的 Format 3 change。"""
    return any(bool(change.get("_dynamic_entry_offset")) for change in changes)


def _build_current_pabgh_for_body(
    game_dir: Path,
    vanilla_store: VanillaStore,
    game_file: str,
    reference_body: bytes,
    current_body: bytes,
    errors: list[str],
    base_by_entry: dict[str, OverlayInputEntry] | None = None,
) -> bytes | None:
    """用当前 body 修复 companion PABGH，供动态 entry offset 重新锚定。"""
    if not game_file.lower().endswith(".pabgb"):
        return None
    pabgh_file = game_file.rsplit(".", 1)[0] + ".pabgh"
    entry = _find_patch_target_entry(pabgh_file, game_dir)
    if entry is None:
        return None
    try:
        vanilla_entry = vanilla_store.ensure_entry_backup(entry)
        reference_header, _ = extract_plaintext(vanilla_entry)
    except Exception as exc:
        errors.append(f"{pabgh_file}: 当前 entry 索引构建失败：{exc}")
        return None
    base_header = (base_by_entry or {}).get(vanilla_entry.path.lower())
    if base_header is not None:
        reference_header = base_header.content
    return _repair_pabgh_offsets_from_body(
        reference_header,
        game_file,
        reference_header=reference_header,
        reference_body=reference_body,
        final_body=current_body,
    )


def _find_patch_target_entry(game_file: str, game_dir: Path) -> PazEntry | None:
    """查找 JSON patch 目标，优先低编号 vanilla 目录，避免命中旧 overlay。"""
    normalized = lower_game_rel_path(game_file)
    index = get_game_pamt_index(game_dir)
    match = index.find_best(game_file)
    if match is not None:
        if lower_game_rel_path(match.path) != normalized:
            logger.info("按 basename 匹配 %s -> %s", game_file, match.path)
        return match
    return None


def _resolve_signature_base(data: bytearray, signature: str | None) -> int | None:
    """解析 signature 基准偏移；无 signature 时返回 0。"""
    if not signature:
        return 0
    try:
        sig_bytes = bytes.fromhex(signature)
    except (TypeError, ValueError):
        logger.warning("signature 不是合法 hex，改用绝对 offset")
        return 0
    position = bytes(data).find(sig_bytes)
    if position < 0:
        logger.error("signature 未在目标文件中找到")
        return None
    return position + len(sig_bytes)


def _parse_change_offset(
    change: dict,
    base_offset: int,
    name_offsets: dict[str, int] | None = None,
) -> int | None:
    """解析补丁 offset，支持绝对 offset 以及 Format 3 entry+rel_offset。"""
    entry_name = change.get("entry")
    if name_offsets is not None and isinstance(entry_name, str):
        rel = change.get("rel_offset")
        rel_value = _parse_int_like(rel)
        anchor = name_offsets.get(entry_name) or name_offsets.get(entry_name.lower())
        if rel_value is not None and anchor is not None:
            return anchor + rel_value

    raw = change.get("offset")
    if raw is None:
        raw = change.get("rel_offset")
    if raw is None:
        return None
    value = _parse_int_like(raw)
    return None if value is None else base_offset + value


def _parse_int_like(value: object) -> int | None:
    """解析 JSON 里可能出现的整数、十进制字符串或 hex 字符串。"""
    try:
        return int(value, 0) if isinstance(value, str) else int(value)
    except (TypeError, ValueError):
        try:
            return int(str(value), 16)
        except (TypeError, ValueError):
            return None


def _pattern_scan(
    data: bytearray,
    original_offset: int,
    original_bytes: bytes,
    vanilla_data: bytes | None,
) -> int | None:
    """在游戏更新导致 offset 漂移时尝试定位原始字节。"""
    from cdmm import cdloader_native

    return cdloader_native.pattern_scan(
        data,
        original_offset,
        original_bytes,
        vanilla_data,
    )


def _filter_near_pattern_candidate(
    candidate: int,
    original_offset: int,
    original_bytes: bytes,
) -> int | None:
    """8 字节短模式必须限制漂移距离，避免命中远处同值字段。"""
    if (
        len(original_bytes) == 8
        and abs(candidate - original_offset) > NEAR_PATTERN_RELOCATION_LIMIT
    ):
        return None
    return candidate


def _find_all(data: bytes | bytearray, pattern: bytes | bytearray) -> list[int]:
    """查找所有 pattern 位置。"""
    if not pattern:
        return []
    result: list[int] = []
    pos = 0
    while True:
        index = data.find(pattern, pos)
        if index < 0:
            return result
        result.append(index)
        pos = index + 1


def _record_json_mod_apply_stat(
    mod_stats: dict[str, dict[str, float | int]] | None,
    mod_name: str,
    elapsed: float,
    change_count: int,
    applied: int,
    mismatched: int,
    relocated: int,
) -> None:
    """累加单个 JSON 模组的应用耗时和补丁结果。"""
    if mod_stats is None:
        return
    stats = mod_stats.setdefault(
        mod_name,
        {
            "load_group_seconds": 0.0,
            "patch_count": 0,
            "change_count": 0,
        },
    )
    stats["apply_seconds"] = float(stats.get("apply_seconds", 0.0)) + elapsed
    stats["apply_change_count"] = int(stats.get("apply_change_count", 0)) + change_count
    stats["applied"] = int(stats.get("applied", 0)) + applied
    stats["mismatched"] = int(stats.get("mismatched", 0)) + mismatched
    stats["relocated"] = int(stats.get("relocated", 0)) + relocated


def _log_slow_json_patch(
    mod_name: str,
    game_file: str,
    elapsed: float,
    change_count: int,
    applied: int,
    mismatched: int,
    relocated: int,
) -> None:
    """输出慢 JSON patch 块，便于定位单个模组。"""
    if elapsed < JSON_PROFILE_LOG_THRESHOLD_SECONDS:
        return
    logger.info(
        "JSON 耗时：patch %.2fs，模组=%s，目标=%s，changes=%d，applied=%d，mismatched=%d，relocated=%d",
        elapsed,
        mod_name,
        game_file,
        change_count,
        applied,
        mismatched,
        relocated,
    )


def _log_slow_json_group(
    game_file: str,
    elapsed: float,
    patch_items: list[tuple[DiscoveredMod, dict]],
    total_applied: int,
    total_mismatched: int,
) -> None:
    """输出慢目标分组耗时。"""
    if elapsed < JSON_PROFILE_LOG_THRESHOLD_SECONDS:
        return
    mod_names = ", ".join(sorted({mod.name for mod, _patch in patch_items}))
    change_count = sum(len(patch.get("changes", [])) for _mod, patch in patch_items)
    logger.info(
        "JSON 耗时：目标 %.2fs，target=%s，mods=%s，changes=%d，applied=%d，mismatched=%d",
        elapsed,
        game_file,
        mod_names,
        change_count,
        total_applied,
        total_mismatched,
    )


def _log_json_mod_profile(mod_stats: dict[str, dict[str, float | int]] | None) -> None:
    """输出 JSON 模组耗时 Top 列表。"""
    if not mod_stats:
        return
    rows = []
    for mod_name, stats in mod_stats.items():
        total_seconds = float(stats.get("load_group_seconds", 0.0)) + float(
            stats.get("apply_seconds", 0.0)
        )
        if total_seconds < JSON_PROFILE_LOG_THRESHOLD_SECONDS:
            continue
        rows.append((total_seconds, mod_name, stats))
    for total_seconds, mod_name, stats in sorted(rows, reverse=True)[:10]:
        logger.info(
            "JSON 耗时TOP：%.2fs，模组=%s，读取/分组=%.2fs，应用=%.2fs，patches=%d，changes=%d，applied=%d，mismatched=%d，relocated=%d",
            total_seconds,
            mod_name,
            float(stats.get("load_group_seconds", 0.0)),
            float(stats.get("apply_seconds", 0.0)),
            int(stats.get("patch_count", 0)),
            int(stats.get("apply_change_count", stats.get("change_count", 0))),
            int(stats.get("applied", 0)),
            int(stats.get("mismatched", 0)),
            int(stats.get("relocated", 0)),
        )


def _bytes_from_hex(value: object) -> bytes | None:
    """把 JSON 里的 hex 字符串转为 bytes。"""
    if not isinstance(value, str):
        return None
    try:
        return bytes.fromhex(value)
    except ValueError:
        return None


def _is_patch_block(value: object) -> bool:
    """判断 patches[] 元素是否具备基本结构。"""
    return (
        isinstance(value, dict)
        and isinstance(value.get("game_file"), str)
        and isinstance(value.get("changes"), list)
    )


def _expand_grouped_patch_items(
    grouped: dict[str, list[tuple[DiscoveredMod, dict]]],
    resolved_game_files: dict[str, str] | None,
) -> tuple[dict[str, list[tuple[DiscoveredMod, dict]]], dict[str, str]]:
    """把 companion / 定向 change 预先拆到各自目标文件分组。"""
    expanded: dict[str, list[tuple[DiscoveredMod, dict]]] = {}
    resolved = dict(resolved_game_files or {})
    for group_key, patch_items in grouped.items():
        for mod, patch in patch_items:
            game_file = str(patch["game_file"])
            body_changes, routed_changes = _split_patch_changes(game_file, patch.get("changes", []))
            if body_changes:
                body_patch = dict(patch)
                body_patch["changes"] = body_changes
                expanded.setdefault(group_key, []).append((mod, body_patch))
                resolved.setdefault(group_key, game_file)
            for target_file, target_changes in routed_changes.items():
                target_key = lower_game_rel_path(target_file)
                target_patch = dict(patch)
                target_patch["game_file"] = target_file
                target_patch["changes"] = target_changes
                expanded.setdefault(target_key, []).append((mod, target_patch))
                resolved[target_key] = target_file
    return expanded, resolved


def _split_patch_changes(
    game_file: str,
    raw_changes: object,
) -> tuple[list[dict], dict[str, list[dict]]]:
    """拆分 body change、显式 target change 与 `.pabgh` companion change。"""
    if not isinstance(raw_changes, list):
        return [], {}

    body_changes: list[dict] = []
    routed_changes: dict[str, list[dict]] = {}
    current_target_key = lower_game_rel_path(game_file)
    for raw_change in raw_changes:
        if not isinstance(raw_change, dict):
            continue
        change = dict(raw_change)
        companion_change = change.pop("_pabgh_companion", None)
        explicit_target = _extract_change_target(change.pop("_target_file", None))
        if explicit_target is not None and lower_game_rel_path(explicit_target) != current_target_key:
            routed_changes.setdefault(explicit_target, []).append(change)
        else:
            body_changes.append(change)
        companion_target = _default_companion_target(game_file)
        if companion_target is not None and isinstance(companion_change, dict):
            companion_copy = dict(companion_change)
            explicit_companion_target = _extract_change_target(companion_copy.pop("_target_file", None))
            routed_changes.setdefault(
                explicit_companion_target or companion_target,
                [],
            ).append(companion_copy)
    return body_changes, routed_changes


def _default_companion_target(game_file: str) -> str | None:
    """返回 `.pabgb` 默认 companion `.pabgh` 路径。"""
    normalized = lower_game_rel_path(game_file)
    if not normalized.endswith(".pabgb"):
        return None
    return game_file.rsplit(".", 1)[0] + ".pabgh"


def _extract_change_target(value: object) -> str | None:
    """解析 change 上显式声明的目标文件。"""
    return value if isinstance(value, str) and value else None


def _allow_partial_apply(data: object) -> bool:
    """读取 JSON 顶层或 modinfo 中的半应用显式许可。"""
    if not isinstance(data, dict):
        return False
    if data.get("allow_partial_apply") is True:
        return True
    modinfo = data.get("modinfo")
    return isinstance(modinfo, dict) and modinfo.get("allow_partial_apply") is True


def _should_reject_partial_data_table(
    game_file: str,
    patch_items: list[tuple[DiscoveredMod, dict]],
) -> bool:
    """数据表默认不允许半应用，避免游戏读取错位表后闪退。"""
    if not game_file.lower().endswith(DATA_TABLE_SUFFIXES):
        return False
    return not any(bool(patch.get("_allow_partial_apply")) for _mod, patch in patch_items)


def _should_skip_partial_data_table(
    game_file: str,
    patch_items: list[tuple[DiscoveredMod, dict]],
    inserts: list[tuple[int, int]],
) -> bool:
    """只有数据表长度变化伴随未匹配时才整表跳过；等长冲突可按 DMM 继续半应用。"""
    if not patch_items:
        return False
    if not _should_reject_partial_data_table(game_file, patch_items):
        return False
    return bool(inserts)


