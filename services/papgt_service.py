"""PAPGT 重建与恢复服务。"""

from __future__ import annotations

import logging
import struct
from pathlib import Path

from cdmm.common.constants import META_DIR_NAME, PAPGT_FILE_NAME
from cdmm.common.hashlittle import compute_pamt_hash, compute_papgt_hash
from cdmm.storage.vanilla_store import VanillaStore

logger = logging.getLogger(__name__)

# PAPGT 每个目录 entry 的固定长度。
ENTRY_SIZE = 12

# 默认语言类型：0x3FFF 表示所有语言。
DEFAULT_LANG_TYPE = 0x3FFF


def build_papgt(
    game_dir: Path,
    vanilla_store: VanillaStore,
    modified_pamts: dict[str, bytes],
    prepend_order: list[str] | None = None,
    normalize_existing_flags: bool = False,
) -> bytes:
    """基于 vanilla PAPGT 重建目录索引，并把新 overlay 目录按指定顺序插到最前。"""
    base_rel = f"{META_DIR_NAME}/{PAPGT_FILE_NAME}"
    vanilla_store.ensure_file_backup(base_rel)
    papgt = bytearray(vanilla_store.read_file(base_rel))
    if len(papgt) < 12:
        raise ValueError("PAPGT 文件过小")

    header_meta0 = bytes(papgt[0:4])
    header_meta8 = bytes(papgt[8:12])
    entry_count = _find_entry_count(papgt)
    string_table_start = 12 + entry_count * ENTRY_SIZE + 4

    parsed_entries: list[tuple[str, int, int]] = []
    for index in range(entry_count):
        position = 12 + index * ENTRY_SIZE
        flags = struct.unpack_from("<I", papgt, position)[0]
        name_offset = struct.unpack_from("<I", papgt, position + 4)[0]
        pamt_hash = struct.unpack_from("<I", papgt, position + 8)[0]
        name = _read_string(papgt, string_table_start, name_offset)
        if name:
            parsed_entries.append((name, flags, pamt_hash))

    modified_names = set(modified_pamts)
    existing = {name for name, _flags, _hash in parsed_entries}
    new_dirs = _order_new_dirs(modified_names, existing, prepend_order)

    live_entries: list[tuple[str, int, int]] = []
    for name, flags, old_hash in parsed_entries:
        if name in modified_names or _should_keep_existing(game_dir, name):
            if normalize_existing_flags:
                flags = encode_flags()
            live_entries.append((name, flags, old_hash))

    all_entries: list[tuple[str, int, int | None]] = []
    default_flags = encode_flags()
    all_entries.extend((name, default_flags, None) for name in new_dirs)
    all_entries.extend(live_entries)

    string_table = bytearray()
    offsets: dict[str, int] = {}
    for name, _flags, _old_hash in all_entries:
        if name not in offsets:
            offsets[name] = len(string_table)
            string_table += name.encode("ascii") + b"\x00"

    result = bytearray()
    result += header_meta0
    result += b"\x00\x00\x00\x00"
    result += header_meta8
    result[8] = len(all_entries) & 0xFF

    for name, flags, old_hash in all_entries:
        if name in modified_pamts:
            pamt_hash = compute_pamt_hash(modified_pamts[name])
        else:
            pamt_hash = old_hash if old_hash is not None else _read_live_pamt_hash(game_dir, name)
        result += struct.pack("<III", flags, offsets[name], pamt_hash)

    result += struct.pack("<I", len(string_table))
    result += string_table
    struct.pack_into("<I", result, 4, compute_papgt_hash(bytes(result)))
    return bytes(result)


def _order_new_dirs(
    modified_names: set[str],
    existing_names: set[str],
    prepend_order: list[str] | None,
) -> list[str]:
    """按调用方指定顺序排列新增目录，剩余目录保持稳定字母序。"""
    unordered = {name for name in modified_names if name not in existing_names}
    ordered: list[str] = []
    if prepend_order:
        for name in prepend_order:
            if name in unordered and name not in ordered:
                ordered.append(name)
    remaining = sorted(name for name in unordered if name not in set(ordered))
    return [*ordered, *remaining]


def encode_flags(is_optional: int = 0, lang_type: int = DEFAULT_LANG_TYPE, zero: int = 0) -> int:
    """编码 PAPGT entry flags。"""
    return (is_optional & 0xFF) | ((lang_type & 0xFFFF) << 8) | ((zero & 0xFF) << 24)


def _find_entry_count(papgt: bytearray) -> int:
    """通过 string table size 字段反推 entry 数量。"""
    size = len(papgt)
    for count in range(1, 256):
        size_pos = 12 + count * ENTRY_SIZE
        if size_pos + 4 > size:
            break
        string_size = struct.unpack_from("<I", papgt, size_pos)[0]
        if size_pos + 4 + string_size == size:
            return count
    logger.warning("无法精确判断 PAPGT entry 数量，使用保守估算")
    return max(0, (size - 16) // ENTRY_SIZE)


def _read_string(papgt: bytearray, string_table_start: int, name_offset: int) -> str | None:
    """读取 string table 中的 null 结尾 ASCII 目录名。"""
    absolute = string_table_start + name_offset
    if absolute >= len(papgt):
        return None
    try:
        end = papgt.index(0, absolute)
    except ValueError:
        end = len(papgt)
    value = papgt[absolute:end].decode("ascii", errors="replace")
    return value or None


def _should_keep_existing(game_dir: Path, name: str) -> bool:
    """保留 vanilla 目录和仍存在 PAMT 的目录，移除消失的旧 mod 目录。"""
    try:
        if int(name) < 36:
            return True
    except (ValueError, TypeError):
        return True
    return (game_dir / name / "0.pamt").exists()


def _read_live_pamt_hash(game_dir: Path, name: str) -> int:
    """读取 live PAMT 并计算 hash，不存在时返回 0。"""
    pamt_path = game_dir / name / "0.pamt"
    if not pamt_path.exists():
        return 0
    try:
        data = pamt_path.read_bytes()
    except OSError:
        return 0
    return compute_pamt_hash(data) if len(data) >= 12 else 0
