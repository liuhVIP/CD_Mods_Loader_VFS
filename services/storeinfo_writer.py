"""storeinfo.pabgb 的 Format 3 `stock_data_list` writer。

该 writer 用于 HernandPets 等商店库存模组：把 Format 3 中导出的完整
stock list 合成为 `storeinfo.pabgb` 的局部 byte patch，并在记录长度变化时
同步生成 companion `storeinfo.pabgh` offset rebuild change。

安全策略：
1. 与 vanilla 已有记录 identity 匹配的 stock record，直接复用 vanilla 字节。
2. 新记录只允许写入已映射字段；未映射字段非零时拒绝，避免猜错布局。
3. 非空 `effect_list` 暂不支持，直接拒绝。
"""

from __future__ import annotations

import logging
import struct
from typing import Any

from cdmm.services.pab_table_service import parse_pabgh_index
from cdmm.services.storeinfo_native_parser import (
    LIST_COUNT_PAYLOAD_OFFSET,
    StockRecord,
    StoreinfoParseError,
    parse_stock_list,
    serialize_stock_list,
)

logger = logging.getLogger(__name__)

# value struct 内部尚未映射的字段；新记录不能携带非零值。
_UNMAPPED_VALUE_FIELDS = (
    "disc",
    "lookup_a",
    "lookup_b",
    "lookup_c",
    "raw_a",
    "raw_b",
    "raw_c",
    "raw_d",
    "raw_f",
)
# stock record 顶层尚未映射的字段；新记录不能携带非零值。
_UNMAPPED_RECORD_FIELDS = ("lookup_b", "lookup_c")


class StoreinfoWriteRefused(ValueError):
    """当前 storeinfo intent 无法在安全边界内应用。"""


def build_storeinfo_changes(
    vanilla_body: bytes,
    vanilla_header: bytes,
    intents: list[Any],
) -> tuple[list[dict], dict | None]:
    """把 `stock_data_list` intents 转成传统 byte patch changes。"""
    key_size, offsets = parse_pabgh_index(vanilla_header, "storeinfo")
    if not offsets:
        logger.warning("storeinfo writer: PABGH 解析失败")
        return [], None
    sorted_offsets = sorted(offsets.values()) + [len(vanilla_body)]

    # key 缺省为 0 时，按 entry 名称回退解析真实 key。
    name_to_key: dict[str, int] = {}
    for key, offset in offsets.items():
        _entry_id, entry_name, _payload = _parse_entry_header(
            vanilla_body,
            offset,
            key_size,
        )
        if entry_name:
            name_to_key.setdefault(entry_name, key)

    per_key: dict[int, list] = {}
    name_resolved = 0
    for intent in intents:
        field = (getattr(intent, "field", "") or "").strip()
        if field not in ("stock_data_list", "_exchangeItemInfoListForSell"):
            logger.warning("storeinfo writer: 不支持字段 %r，已跳过", field)
            continue
        if (getattr(intent, "op", "set") or "set") != "set":
            logger.warning(
                "storeinfo writer: 不支持 op %r，已跳过",
                getattr(intent, "op", None),
            )
            continue
        new = getattr(intent, "new", None)
        key = getattr(intent, "key", None)
        if not isinstance(new, list) or not isinstance(key, int):
            logger.warning("storeinfo writer: intent 形状非法，key=%r", key)
            continue
        if key not in offsets:
            entry_name = getattr(intent, "entry", "") or ""
            resolved = name_to_key.get(entry_name)
            if resolved is not None:
                key = resolved
                name_resolved += 1
            else:
                logger.warning(
                    "storeinfo writer: store key=%r / entry=%r 未命中，已跳过",
                    key,
                    entry_name,
                )
                continue
        per_key[key] = new

    if name_resolved:
        logger.info("storeinfo writer: %d 个 intent 通过 entry 名称解析", name_resolved)
    if not per_key:
        return [], None

    replacements: dict[int, tuple[int, int, bytes]] = {}
    for key, json_records in per_key.items():
        offset = offsets[key]
        entry_end = sorted_offsets[sorted_offsets.index(offset) + 1]
        _entry_id, _entry_name, payload = _parse_entry_header(vanilla_body, offset, key_size)
        count_offset = payload + LIST_COUNT_PAYLOAD_OFFSET
        try:
            vanilla_records, list_start, list_end = parse_stock_list(
                vanilla_body,
                count_offset,
            )
        except (StoreinfoParseError, struct.error, IndexError) as exc:
            raise StoreinfoWriteRefused(
                f"store entry {key}: vanilla stock list 不符合已验证布局：{exc}"
            ) from exc
        if list_end > entry_end:
            raise StoreinfoWriteRefused(f"store entry {key}: stock list 越过 entry 边界")

        by_body: dict[int, StockRecord] = {}
        for record in vanilla_records:
            by_body.setdefault(record.body, record)

        output_records: list[StockRecord] = []
        new_count = 0
        for index, json_record in enumerate(json_records):
            identity = _record_identity(json_record)
            vanilla_record = by_body.get(identity) if identity is not None else None
            if vanilla_record is not None:
                output_records.append(vanilla_record)
                continue
            output_records.append(_build_new_record(json_record, index))
            new_count += 1

        new_list = serialize_stock_list(output_records)
        replacements[key] = (list_start, list_end, new_list)
        logger.info(
            "storeinfo writer: store %d stock list %d -> %d records (%d new, %+d bytes)",
            key,
            len(vanilla_records),
            len(output_records),
            new_count,
            len(new_list) - (list_end - list_start),
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
                "label": f"store {key}.stock_data_list",
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
            "label": "storeinfo.pabgh offset rebuild",
        }
    return pabgb_changes, pabgh_change


def _record_identity(json_record: dict) -> int | None:
    """返回 stock record 的稳定身份：`value.payload.body`。"""
    try:
        return int(json_record["value"]["payload"]["body"])
    except (KeyError, TypeError, ValueError):
        return None


def _build_new_record(json_record: dict, index: int) -> StockRecord:
    """根据已映射字段构造一个新的 stock record。"""
    _check_new_record_buildable(json_record, index)
    value = json_record.get("value") or {}
    record = StockRecord(
        lookup_a=int(json_record.get("lookup_a") or 0),
        raw_a=int(json_record.get("raw_a") or 0),
        raw_b=int(json_record.get("raw_b") or 0),
        raw_c=int(json_record.get("raw_c") or 0),
        raw_d=int(json_record.get("raw_d") or 0),
        raw_e=int(json_record.get("raw_e") or 0),
        flag_a=int(json_record.get("flag_a") or 0),
        flag_b=int(json_record.get("flag_b") or 0),
        flag_c=int(json_record.get("flag_c") or 0),
        is_restore_item=int(json_record.get("is_restore_item") or 0),
        const33=1,
        body=int(_record_identity(json_record) or 0),
    )
    vgap = bytearray(record.vgap)
    struct.pack_into("<I", vgap, 41, int(value.get("raw_e") or 0))
    struct.pack_into("<H", vgap, 57, int(value.get("raw_g") or 0) & 0xFFFF)
    struct.pack_into("<I", vgap, 59, int(value.get("raw_q") or 0))
    record.vgap = bytes(vgap)

    sub_data = json_record.get("sub_data")
    if sub_data is not None:
        record.sub_data = {
            "flag": int(sub_data.get("flag") or 0),
            "lookup_a": int(sub_data.get("lookup_a") or 0) & 0xFFFFFFFF,
            "lookup_b": int(sub_data.get("lookup_b") or 0) & 0xFFFFFFFF,
            "lookup_c": int(sub_data.get("lookup_c") or 0) & 0xFFFFFFFF,
        }
    return record


def _check_new_record_buildable(json_record: dict, index: int) -> None:
    """确认新增 stock record 不携带尚未映射的非零字段。"""
    value = json_record.get("value") or {}
    for field in _UNMAPPED_VALUE_FIELDS:
        if value.get(field):
            raise StoreinfoWriteRefused(
                f"new stock record [{index}] 设置了未映射 value.{field}={value[field]!r}"
            )
    for field in _UNMAPPED_RECORD_FIELDS:
        if json_record.get(field):
            raise StoreinfoWriteRefused(
                f"new stock record [{index}] 设置了未映射 {field}={json_record[field]!r}"
            )
    if json_record.get("effect_list"):
        raise StoreinfoWriteRefused(f"new stock record [{index}] effect_list 非空，暂不支持")
    if value.get("raw_q") is not None:
        try:
            raw_q = int(value["raw_q"])
        except (TypeError, ValueError) as exc:
            raise StoreinfoWriteRefused(
                f"new stock record [{index}]: value.raw_q={value['raw_q']!r} 不是整数"
            ) from exc
        if _record_identity(json_record) != raw_q:
            raise StoreinfoWriteRefused(
                f"new stock record [{index}]: value.raw_q 与 value.payload.body 不一致"
            )


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
