"""传统 JSON byte patch 加载与 overlay entry 生成。"""

from __future__ import annotations

import logging
import os
import struct
from pathlib import Path

from cdmm.archive.pamt import derive_pamt_dir, find_pamt_entry
from cdmm.archive.paz_crypto import decrypt, lz4_decompress
from cdmm.common.models import DiscoveredMod, OverlayInputEntry, PazEntry
from cdmm.services.scanner import load_json_file
from cdmm.storage.vanilla_store import VanillaStore
from cdmm.utils.path_utils import lower_game_rel_path

logger = logging.getLogger(__name__)


def build_json_overlay_entries(
    game_dir: Path,
    mods: list[DiscoveredMod],
    vanilla_store: VanillaStore,
    warnings: list[str],
    errors: list[str],
) -> list[OverlayInputEntry]:
    """读取所有传统 JSON 模组，生成待写入 overlay 的 entry。"""
    grouped: dict[str, list[tuple[DiscoveredMod, dict]]] = {}
    for mod in mods:
        try:
            data = load_json_file(mod.path)
        except Exception as exc:
            errors.append(f"{mod.name}: JSON 读取失败：{exc}")
            continue
        for patch in data.get("patches", []):
            if not _is_patch_block(patch):
                continue
            game_file = str(patch["game_file"])
            grouped.setdefault(lower_game_rel_path(game_file), []).append((mod, patch))

    overlay_entries: list[OverlayInputEntry] = []
    for _, patch_items in grouped.items():
        first_patch = patch_items[0][1]
        game_file = str(first_patch["game_file"])
        entry = find_pamt_entry(game_file, game_dir)
        if entry is None:
            errors.append(f"{game_file}: 未在任何 PAMT 中找到目标文件")
            continue

        try:
            vanilla_entry = vanilla_store.ensure_entry_backup(entry)
            plaintext, vanilla_entry = extract_plaintext(vanilla_entry)
        except Exception as exc:
            errors.append(f"{game_file}: vanilla 提取失败：{exc}")
            continue

        modified = bytearray(plaintext)
        had_mismatch = False
        total_applied = 0
        total_mismatched = 0
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
            )
            total_applied += applied
            total_mismatched += mismatched
            if relocated:
                logger.info("%s: %s 发生 %d 个偏移重定位", mod.name, game_file, relocated)
            if mismatched:
                had_mismatch = True
                warnings.append(
                    f"{mod.name}: {game_file} 有 {mismatched}/{applied + mismatched} 个补丁未匹配"
                )

        if had_mismatch:
            allowed_names = ", ".join(sorted({mod.name for mod, _patch in patch_items}))
            warnings.append(
                f"{game_file}: 存在未匹配补丁，已继续半应用，相关模组：{allowed_names}"
            )
        if total_applied == 0:
            warnings.append(f"{game_file}: 没有任何补丁成功应用")
            continue
        if bytes(modified) == plaintext:
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
        if inserts_out and game_file.lower().endswith(".pabgb"):
            companion = _build_pabgh_companion(game_dir, vanilla_store, game_file, inserts_out, errors)
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

    def shift_for(position: int) -> int:
        return sum(delta for write_pos, delta in writes if write_pos < position)

    parsed_changes = []
    for change in changes:
        offset = _parse_change_offset(change, base_offset)
        if offset is None:
            mismatched += 1
            continue
        parsed_changes.append((offset, change))
    parsed_changes.sort(key=lambda item: item[0])

    for original_offset, change in parsed_changes:
        offset = original_offset + shift_for(original_offset)
        if change.get("type") == "insert":
            insert_bytes = _bytes_from_hex(change.get("bytes"))
            if insert_bytes is None or offset > len(data):
                mismatched += 1
                continue
            data[offset:offset] = insert_bytes
            writes.append((original_offset, len(insert_bytes)))
            if inserts_out is not None:
                inserts_out.append((original_offset, len(insert_bytes)))
            applied += 1
            continue

        patched_bytes = _bytes_from_hex(change.get("patched"))
        if patched_bytes is None:
            mismatched += 1
            continue
        original_bytes = _bytes_from_hex(change.get("original"))
        old_len = len(original_bytes) if original_bytes is not None else len(patched_bytes)
        if offset + old_len > len(data):
            mismatched += 1
            continue
        if original_bytes is not None and data[offset:offset + old_len] != original_bytes:
            if data[offset:offset + len(patched_bytes)] == patched_bytes:
                applied += 1
                continue
            new_offset = _pattern_scan(data, original_offset, original_bytes, vanilla_data)
            if new_offset is None:
                mismatched += 1
                continue
            offset = new_offset
            relocated += 1
        data[offset:offset + old_len] = patched_bytes
        writes.append((original_offset, len(patched_bytes) - old_len))
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
        )
    return applied, mismatched, relocated


def fixup_pabgh_after_inserts(pabgh: bytes, inserts: list[tuple[int, int]]) -> bytes:
    """插入 PABGB 字节后修正 PABGH 指针表。"""
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
) -> OverlayInputEntry | None:
    """构造 insert 场景需要同步输出的 .pabgh companion entry。"""
    pabgh_file = game_file.rsplit(".", 1)[0] + ".pabgh"
    entry = find_pamt_entry(pabgh_file, game_dir)
    if entry is None:
        errors.append(f"{game_file}: 存在 insert 但找不到 companion PABGH：{pabgh_file}")
        return None
    try:
        vanilla_entry = vanilla_store.ensure_entry_backup(entry)
        plaintext, vanilla_entry = extract_plaintext(vanilla_entry)
    except Exception as exc:
        errors.append(f"{pabgh_file}: 提取失败：{exc}")
        return None
    fixed = fixup_pabgh_after_inserts(plaintext, inserts)
    return OverlayInputEntry(
        content=fixed,
        entry_path=vanilla_entry.path,
        pamt_dir=derive_pamt_dir(vanilla_entry.paz_file),
        compression_type=vanilla_entry.compression_type,
        encrypted=vanilla_entry.encrypted,
        crypto_filename=os.path.basename(vanilla_entry.path),
    )


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


def _parse_change_offset(change: dict, base_offset: int) -> int | None:
    """解析补丁 offset，支持十进制 int、十进制字符串和 0x/hex 字符串。"""
    raw = change.get("offset")
    if raw is None:
        raw = change.get("rel_offset")
    if raw is None:
        return None
    try:
        return base_offset + (int(raw, 0) if isinstance(raw, str) else int(raw))
    except (TypeError, ValueError):
        try:
            return base_offset + int(str(raw), 16)
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
                    return candidate

    scan_start = max(0, original_offset - 512) if len(original_bytes) < 4 else 0
    scan_end = min(len(data_bytes), original_offset + 512) if len(original_bytes) < 4 else len(data_bytes)
    matches = _find_all(data_bytes[scan_start:scan_end], original_bytes)
    if len(matches) == 1:
        return scan_start + matches[0]
    return None


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

