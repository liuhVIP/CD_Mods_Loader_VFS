"""传统 JSON byte patch 加载与 overlay entry 生成。"""

from __future__ import annotations

import logging
import os
import struct
from pathlib import Path

from cdmm.archive.pamt import derive_pamt_dir
from cdmm.archive.paz_crypto import decrypt, lz4_decompress
from cdmm.common.models import DiscoveredMod, OverlayInputEntry, PazEntry
from cdmm.services.pab_table_service import parse_entry_name_end, parse_pabgh_index
from cdmm.services.pamt_index_service import get_game_pamt_index
from cdmm.services.scanner import load_json_file
from cdmm.storage.vanilla_store import VanillaStore
from cdmm.utils.path_utils import lower_game_rel_path

logger = logging.getLogger(__name__)

DATA_TABLE_SUFFIXES = (".pabgb", ".pabgh", ".pamt")

# DMM 对 8 字节旧版 JSON 补丁会允许小范围最近匹配，超过该距离会跳过以免误写。
NEAR_PATTERN_RELOCATION_LIMIT = 4000


def build_json_overlay_entries(
    game_dir: Path,
    mods: list[DiscoveredMod],
    vanilla_store: VanillaStore,
    warnings: list[str],
    errors: list[str],
    base_entries: list[OverlayInputEntry] | None = None,
) -> list[OverlayInputEntry]:
    """读取所有传统 JSON 模组，生成待写入 overlay 的 entry。"""
    grouped: dict[str, list[tuple[DiscoveredMod, dict]]] = {}
    resolved_game_files: dict[str, str] = {}
    for mod in mods:
        try:
            data = load_json_file(mod.path)
        except Exception as exc:
            errors.append(f"{mod.name}: JSON 读取失败：{exc}")
            continue
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

    return build_patch_overlay_entries(
        game_dir,
        grouped,
        vanilla_store,
        warnings,
        errors,
        base_entries,
        resolved_game_files,
    )


def collect_json_pamt_targets(mods: list[DiscoveredMod]) -> list[str]:
    """收集传统 JSON byte patch 会查询的目标路径。"""
    targets: list[str] = []
    for mod in mods:
        try:
            data = load_json_file(mod.path)
        except Exception:
            continue
        for patch in data.get("patches", []):
            if not _is_patch_block(patch):
                continue
            game_file = str(patch["game_file"])
            targets.append(game_file)
            if game_file.lower().endswith(".pabgb"):
                targets.append(game_file.rsplit(".", 1)[0] + ".pabgh")
    return targets


def build_patch_overlay_entries(
    game_dir: Path,
    grouped: dict[str, list[tuple[DiscoveredMod, dict]]],
    vanilla_store: VanillaStore,
    warnings: list[str],
    errors: list[str],
    base_entries: list[OverlayInputEntry] | None = None,
    resolved_game_files: dict[str, str] | None = None,
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
    for group_key, patch_items in grouped.items():
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

        for mod, patch in patch_items:
            changes = patch.get("changes", [])
            if not changes:
                continue
            applied, mismatched, relocated = apply_byte_patches(
                modified,
                changes,
                signature=patch.get("signature"),
                vanilla_data=plaintext,
                inserts_out=inserts_out,
                name_offsets=name_offsets,
            )
            total_applied += applied
            total_mismatched += mismatched
            if applied == 0:
                need_already_patched_check = True
            if relocated:
                logger.info("%s: %s 发生 %d 个偏移重定位", mod.name, game_file, relocated)
            if mismatched:
                had_mismatch = True
                warnings.append(
                    f"{mod.name}: {game_file} 有 {mismatched}/{applied + mismatched} 个补丁未匹配"
                )

        if total_mismatched > 0 and _should_skip_partial_data_table(game_file, patch_items, inserts_out):
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
                required=bool(inserts_out),
            )
            if companion is not None:
                overlay_entries.append(companion)

    return overlay_entries


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
        parsed_changes.append((offset, change, *parsed))
    # DMM 的 V1/V2 replace 会保留 JSON 原始顺序；只有长度变化时才需要按 offset 计算位移。
    has_length_delta = any(
        len(patched_bytes) != old_len
        for _offset, _change, patched_bytes, _original_bytes, old_len in parsed_changes
    )
    if has_length_delta:
        parsed_changes.sort(key=lambda item: item[0])

    for original_offset, change, patched_bytes, original_bytes, old_len in parsed_changes:
        if writes:
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
            mismatched += 1
            continue
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
            ):
                mismatched += 1
                continue
            offset = new_offset
            relocated += 1
        data[offset:offset + old_len] = patched_bytes
        writes.append((original_offset, len(patched_bytes) - old_len))
        written_replacements[(original_offset, old_len)] = patched_bytes
        if inserts_out is not None and len(patched_bytes) != old_len:
            # 长度变化的 replace 同样会移动后续 entry，需要修正 PABGH 指针。
            inserts_out.append((original_offset, len(patched_bytes) - old_len))
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
    data = bytearray(pabgh)
    ushort_count = struct.unpack_from("<H", data, 0)[0]
    fmt2 = ushort_count > 0 and 2 + ushort_count * 8 <= len(data) and 2 + ushort_count * 8 >= len(data) - 16
    if fmt2:
        count = ushort_count
        prefix = 2
    else:
        if len(data) < 4:
            return pabgh
        count = struct.unpack_from("<I", data, 0)[0]
        prefix = 4
    count = min(count, (len(data) - prefix) // 8)
    sorted_inserts = sorted(inserts, key=lambda item: item[0])
    for index in range(count):
        pos = prefix + index * 8
        pointer = struct.unpack_from("<I", data, pos + 4)[0]
        delta = sum(size for insert_offset, size in sorted_inserts if insert_offset <= pointer)
        if delta:
            struct.pack_into("<I", data, pos + 4, pointer + delta)
    return bytes(data)


def _build_pabgh_companion(
    game_dir: Path,
    vanilla_store: VanillaStore,
    game_file: str,
    inserts: list[tuple[int, int]],
    errors: list[str],
    base_by_entry: dict[str, OverlayInputEntry] | None = None,
    *,
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
    return OverlayInputEntry(
        content=fixed,
        entry_path=vanilla_entry.path,
        pamt_dir=derive_pamt_dir(vanilla_entry.paz_file),
        compression_type=vanilla_entry.compression_type,
        encrypted=vanilla_entry.encrypted,
        crypto_filename=os.path.basename(vanilla_entry.path),
    )


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
) -> dict[str, int] | None:
    """构建 entry 名称到 name_end 的映射，用于 Format 3 的 entry+rel_offset。"""
    if not game_file.lower().endswith(".pabgb"):
        return None
    pabgh_file = game_file.rsplit(".", 1)[0] + ".pabgh"
    entry = _find_patch_target_entry(pabgh_file, game_dir)
    if entry is None:
        return None
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
    try:
        import cdumm_native

        return cdumm_native.pattern_scan(bytes(data), original_offset, original_bytes, vanilla_data)
    except ImportError:
        pass

    data_bytes = bytes(data)
    if vanilla_data and original_offset < len(vanilla_data):
        for context_size in (24, 16, 12, 8):
            start = max(0, original_offset - context_size)
            end = min(len(vanilla_data), original_offset + len(original_bytes) + context_size)
            context = vanilla_data[start:end]
            if len(context) < context_size:
                continue
            matches = _find_all(data_bytes, context)
            if len(matches) == 1:
                candidate = matches[0] + original_offset - start
                if candidate + len(original_bytes) <= len(data):
                    return _filter_near_pattern_candidate(candidate, original_offset, original_bytes)

    scan_start = max(0, original_offset - 512) if len(original_bytes) < 4 else 0
    scan_end = min(len(data_bytes), original_offset + 512) if len(original_bytes) < 4 else len(data_bytes)
    matches = _find_all(data_bytes[scan_start:scan_end], original_bytes)
    if len(matches) == 1:
        return _filter_near_pattern_candidate(
            scan_start + matches[0],
            original_offset,
            original_bytes,
        )
    if len(original_bytes) >= 8 and matches:
        nearest = min(
            (scan_start + match for match in matches),
            key=lambda match: abs(match - original_offset),
        )
        if abs(nearest - original_offset) <= NEAR_PATTERN_RELOCATION_LIMIT:
            return nearest
    return None


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


def _find_all(data: bytes, pattern: bytes) -> list[int]:
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
    if not _should_reject_partial_data_table(game_file, patch_items):
        return False
    return bool(inserts)


