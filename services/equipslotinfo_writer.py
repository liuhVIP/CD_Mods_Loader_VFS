"""equipslotinfo.pabgb 的 Format 3 `entries[N].etl_hashes` writer。

该能力来自参考仓库 GitHub #190 场景：角色创建器女性刺剑/盾牌模组会修改
指定装备槽 entry 内已有 record 的 ETL hash 列表。writer 只增长/缩短已存在
record 的 hash 数组，并原样保留每条 record 后面的不透明固定块与 footer。

已验证的 entry payload 结构：
  u16  unk
  u32  record_count
  record * record_count
  u32  footer_count
  footer_item * footer_count
  u32  const 0xb954d87c

record 结构：
  u32  etl_count
  u32 * etl_count
  fixed block（随游戏版本变化，2026-08-05 当前版本为 67B）
"""

from __future__ import annotations

import logging
import re
import struct
from typing import Any

from cdmm.services.pab_table_service import parse_pabgh_index

logger = logging.getLogger(__name__)

# 每条 equipslot record 中 ETL hashes 后面的不透明固定块候选长度。
# 不同 Crimson Desert 版本会调整该块长度，但块本身仍必须能让 entry
# 精确闭合到 footer + 0xb954d87c terminator，不能仅靠放宽边界猜测。
_FIXED_BLOCK_CANDIDATES = (67, 66, 63, 56)
# footer 中单个不透明元素长度。
_FOOTER_ITEM = 20
# entry 结束魔数，用于确认 parser 没有错位。
_TERMINATOR = 0xB954D87C

# Format 3 字段路径：entries[record_index].etl_hashes。
_FIELD_RE = re.compile(r"^entries\[(\d+)]\.etl_hashes$")


class EquipslotWriteRefused(ValueError):
    """当前 intent 无法在安全边界内应用。"""


def parse_entry_records(
    body: bytes,
    payload: int,
    entry_end: int,
) -> tuple[int, list[tuple[int, list[int], bytes]], bytes]:
    """解析单个 equipslot entry 的 payload。"""
    errors: list[str] = []
    for fixed_block_size in _FIXED_BLOCK_CANDIDATES:
        try:
            return _parse_entry_records_with_fixed_block(
                body,
                payload,
                entry_end,
                fixed_block_size,
            )
        except EquipslotWriteRefused as exc:
            errors.append(f"{fixed_block_size}B: {exc}")
    raise EquipslotWriteRefused("；".join(errors))


def _parse_entry_records_with_fixed_block(
    body: bytes,
    payload: int,
    entry_end: int,
    fixed_block_size: int,
) -> tuple[int, list[tuple[int, list[int], bytes]], bytes]:
    """按指定 fixed block 长度解析，并要求 footer 精确闭合 entry。"""

    def u16(position: int) -> int:
        """读取小端 u16。"""
        return struct.unpack_from("<H", body, position)[0]

    def u32(position: int) -> int:
        """读取小端 u32。"""
        return struct.unpack_from("<I", body, position)[0]

    unk = u16(payload)
    count = u32(payload + 2)
    if not (0 <= count < 1000):
        raise EquipslotWriteRefused(f"record 数量不可信：{count}")
    p = payload + 6
    records = []
    for index in range(count):
        if p + 4 > entry_end:
            raise EquipslotWriteRefused(f"record {index} 越过 entry 边界")
        hash_count = u32(p)
        if hash_count > 64:
            raise EquipslotWriteRefused(
                f"record {index}: ETL hash 数量不可信：{hash_count}"
            )
        if p + 4 + 4 * hash_count + fixed_block_size > entry_end:
            raise EquipslotWriteRefused(f"record {index} 越过 entry 边界")
        hashes = [u32(p + 4 + 4 * item) for item in range(hash_count)]
        fixed = body[p + 4 + 4 * hash_count:p + 4 + 4 * hash_count + fixed_block_size]
        records.append((hash_count, hashes, fixed))
        p += 4 + 4 * hash_count + fixed_block_size
    if p + 4 > entry_end:
        raise EquipslotWriteRefused("footer count 越过 entry 边界")
    footer_count = u32(p)
    footer_end = p + 4 + footer_count * _FOOTER_ITEM + 4
    if footer_end != entry_end:
        raise EquipslotWriteRefused(
            f"footer 无法闭合 entry：footer_count={footer_count}, "
            f"computed={footer_end}, entry_end={entry_end}"
        )
    if u32(footer_end - 4) != _TERMINATOR:
        raise EquipslotWriteRefused(f"entry 结束魔数不匹配：{footer_end - 4}")
    footer = body[p:entry_end]
    return unk, records, footer


def serialize_entry_payload(
    unk: int,
    records: list[tuple[int, list[int], bytes]],
    footer: bytes,
) -> bytes:
    """把 equipslot entry payload 序列化回二进制。"""
    out = bytearray()
    out += struct.pack("<H", unk)
    out += struct.pack("<I", len(records))
    for _count, hashes, fixed in records:
        if len(fixed) not in _FIXED_BLOCK_CANDIDATES:
            raise EquipslotWriteRefused(
                f"fixed block 必须是 {_FIXED_BLOCK_CANDIDATES} 之一，实际 {len(fixed)} 字节"
            )
        out += struct.pack("<I", len(hashes))
        for hash_value in hashes:
            out += struct.pack("<I", hash_value & 0xFFFFFFFF)
        out += fixed
    out += footer
    return bytes(out)


def build_equipslotinfo_changes(
    vanilla_body: bytes,
    vanilla_header: bytes,
    intents: list[Any],
) -> tuple[list[dict], dict | None]:
    """把 `entries[N].etl_hashes` intents 转成传统 byte patch changes。"""
    key_size, offsets = parse_pabgh_index(vanilla_header, "equipslotinfo")
    if not offsets:
        logger.warning("equipslotinfo writer: PABGH 解析失败")
        return [], None
    sorted_offsets = sorted(offsets.values()) + [len(vanilla_body)]

    # Format 3 导出中 key 可能缺省为 0，因此优先按 entry 名称回退解析真实 key。
    name_to_key: dict[str, int] = {}
    for key, offset in offsets.items():
        _entry_id, entry_name, _payload = _parse_entry_header(
            vanilla_body,
            offset,
            key_size,
        )
        if entry_name:
            name_to_key.setdefault(entry_name, key)

    per_key: dict[int, dict[int, list[int]]] = {}
    name_resolved = 0
    for intent in intents:
        field = (getattr(intent, "field", "") or "").strip()
        match = _FIELD_RE.match(field)
        if match is None:
            logger.warning("equipslotinfo writer: 不支持字段 %r，已跳过", field)
            continue
        if (getattr(intent, "op", "set") or "set") != "set":
            logger.warning(
                "equipslotinfo writer: 不支持 op %r，已跳过",
                getattr(intent, "op", None),
            )
            continue
        new = getattr(intent, "new", None)
        key = getattr(intent, "key", None)
        if (
            not isinstance(new, list)
            or not all(isinstance(value, int) for value in new)
            or not isinstance(key, int)
        ):
            logger.warning("equipslotinfo writer: intent 形状非法，key=%r", key)
            continue
        if key not in offsets:
            entry_name = getattr(intent, "entry", "") or ""
            resolved = name_to_key.get(entry_name)
            if resolved is not None:
                key = resolved
                name_resolved += 1
            else:
                logger.warning(
                    "equipslotinfo writer: entry key=%r / entry=%r 未命中，已跳过",
                    key,
                    entry_name,
                )
                continue
        per_key.setdefault(key, {})[int(match.group(1))] = new

    if name_resolved:
        logger.info("equipslotinfo writer: %d 个 intent 通过 entry 名称解析", name_resolved)
    if not per_key:
        return [], None

    replacements: dict[int, tuple[int, int, bytes]] = {}
    for key, index_map in per_key.items():
        offset = offsets[key]
        entry_end = sorted_offsets[sorted_offsets.index(offset) + 1]
        _entry_id, _entry_name, payload = _parse_entry_header(vanilla_body, offset, key_size)
        unk, records, footer = parse_entry_records(vanilla_body, payload, entry_end)
        for index, hashes in index_map.items():
            if not (0 <= index < len(records)):
                raise EquipslotWriteRefused(
                    f"entry {key}: record index {index} 越界，当前只有 {len(records)} 条"
                )
            _count, old_hashes, fixed = records[index]
            merged_hashes = _keep_expanded_hashes_if_new_value_is_subset(old_hashes, hashes)
            records[index] = (len(merged_hashes), merged_hashes, fixed)
        new_payload = serialize_entry_payload(unk, records, footer)
        replacements[key] = (payload, entry_end, new_payload)
        logger.info(
            "equipslotinfo writer: entry %d 更新 %d 条 record，%+d bytes",
            key,
            len(index_map),
            len(new_payload) - (entry_end - payload),
        )

    pabgb_changes: list[dict] = []
    deltas: list[tuple[int, int]] = []
    for key in sorted(replacements, key=lambda item: replacements[item][0]):
        start, end, blob = replacements[key]
        if vanilla_body[start:end] == blob:
            continue
        pabgb_changes.append(
            {
                "offset": start,
                "original": vanilla_body[start:end].hex(),
                "patched": blob.hex(),
                "label": f"equipslot entry {key}.etl_hashes",
            }
        )
        deltas.append((offsets[key], len(blob) - (end - start)))

    if not pabgb_changes:
        return [], None

    new_header = bytearray(vanilla_header)
    count = struct.unpack_from("<H", vanilla_header, 0)[0]
    pos = 2
    changed = False
    for _ in range(count):
        entry_offset = struct.unpack_from("<I", vanilla_header, pos + key_size)[0]
        new_offset = _shifted_offset(entry_offset, deltas)
        if new_offset != entry_offset:
            struct.pack_into("<I", new_header, pos + key_size, new_offset)
            changed = True
        pos += key_size + 4

    pabgh_change = None
    if changed:
        pabgh_change = {
            "offset": 0,
            "original": vanilla_header.hex(),
            "patched": bytes(new_header).hex(),
            "label": "equipslotinfo.pabgh offset rebuild",
        }
    return pabgb_changes, pabgh_change


def _keep_expanded_hashes_if_new_value_is_subset(
    old_hashes: list[int],
    new_hashes: list[int],
) -> list[int]:
    """后续模组只给出当前列表子集时，保留已扩展的 ETL hash 列表。

    Equip Everything V6 会先扩展 equipslotinfo 的可装备集合，而部分旧
    Format 3 模块随后又以较小列表 `set` 同一字段。这里把这种“缩回去”的
    写入视为兼容 no-op；如果后续模组包含新 hash，仍按它的列表写入。
    """
    if len(new_hashes) < len(old_hashes) and set(new_hashes).issubset(set(old_hashes)):
        logger.info(
            "equipslotinfo writer: 后续 intent 是当前 ETL 列表子集，保留已扩展列表（%d -> %d）",
            len(new_hashes),
            len(old_hashes),
        )
        return list(old_hashes)
    return list(new_hashes)


def _parse_entry_header(data: bytes, offset: int, key_size: int) -> tuple[int, str, int]:
    """解析 PABGB entry 头，并返回 payload 起点。"""
    entry_id_format = "<H" if key_size == 2 else "<I"
    entry_id_size = 2 if key_size == 2 else 4
    head_size = entry_id_size + 4
    if offset + head_size > len(data):
        return 0, "", offset

    entry_id = struct.unpack_from(entry_id_format, data, offset)[0]
    name_len = struct.unpack_from("<I", data, offset + entry_id_size)[0]
    if name_len > 500 or offset + head_size + name_len > len(data):
        return entry_id, "", offset + head_size

    name_start = offset + head_size
    name_end = name_start + name_len
    try:
        entry_name = data[name_start:name_end].decode("utf-8") if name_len else ""
    except UnicodeDecodeError:
        entry_name = ""
    payload = name_end + 1 if name_end < len(data) and data[name_end] == 0 else name_end
    return entry_id, entry_name, payload


def _shifted_offset(offset: int, deltas: list[tuple[int, int]]) -> int:
    """根据已修改 entry 的长度变化，计算 PABGH 中的新 offset。"""
    shifted = offset
    for changed_entry_offset, delta in deltas:
        if offset > changed_entry_offset:
            shifted += delta
    return shifted
