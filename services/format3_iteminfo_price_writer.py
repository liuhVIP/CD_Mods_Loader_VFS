"""Narrow ItemInfo writer for base and enchant purchase prices."""

from __future__ import annotations

import re
import struct
from collections import defaultdict

from cdmm.services.format3_parser import Format3Intent
from cdmm.services.format3_runtime import (
    Format3DispatchResult,
    Format3RuntimeContext,
    Format3SkippedIntent,
)
from cdmm.services.iteminfo_native_parser import (
    _Reader,
    _read_EnchantStatData,
    _read_EquipmentBuff,
    _read_ItemPriceInfo,
)

PRICE_FIELD_RE = re.compile(
    r"^price_list\[(?P<price_index>\d+)]\."
    r"(?P<leaf>key|price\.price|price\.item_info_wrapper)$"
)
ENCHANT_PRICE_FIELD_RE = re.compile(
    r"^enchant_data_list\[(?P<enchant_index>\d+)]\."
    r"buy_price_list\[(?P<price_index>\d+)]\."
    r"(?P<leaf>key|price\.price|price\.item_info_wrapper)$"
)


def is_iteminfo_price_field(field: str) -> bool:
    """Return whether ``field`` belongs to the verified price path family."""
    return PRICE_FIELD_RE.match(field) is not None or ENCHANT_PRICE_FIELD_RE.match(field) is not None


def build_iteminfo_price_result(
    context: Format3RuntimeContext,
    intents: list[Format3Intent],
) -> Format3DispatchResult:
    """Patch all requested prices inside their authoritative record bounds."""
    grouped: dict[int, list[Format3Intent]] = defaultdict(list)
    skipped: list[Format3SkippedIntent] = []
    by_name = {bounds[2]: bounds for bounds in context.entry_bounds.values() if bounds[2]}
    for intent in intents:
        bounds = context.entry_bounds.get(intent.key)
        if bounds is None and intent.entry:
            bounds = by_name.get(intent.entry)
        if bounds is None:
            skipped.append(Format3SkippedIntent(intent, "ItemInfo 价格目标记录未命中"))
            continue
        grouped[bounds[0]].append(intent)

    bounds_by_start = {bounds[0]: bounds for bounds in context.entry_bounds.values()}
    changes: list[dict] = []
    for entry_start, entry_intents in grouped.items():
        entry_start, entry_end, entry_name, name_end = bounds_by_start[entry_start]
        original = context.body[entry_start:entry_end]
        patched = bytearray(original)

        enchant_matches = [
            ENCHANT_PRICE_FIELD_RE.match(intent.field)
            for intent in entry_intents
            if ENCHANT_PRICE_FIELD_RE.match(intent.field) is not None
        ]
        enchant_layout = None
        if enchant_matches:
            expected_count = max(int(match.group("enchant_index")) for match in enchant_matches) + 1  # type: ignore[union-attr]
            enchant_layout = _locate_enchant_prices(
                original,
                name_end - entry_start,
                expected_count,
            )
            if enchant_layout is None:
                skipped.extend(
                    Format3SkippedIntent(intent, "ItemInfo enchant_data_list 价格数组未唯一定位")
                    for intent in entry_intents
                    if ENCHANT_PRICE_FIELD_RE.match(intent.field) is not None
                )

        price_offsets: list[int] | None = None
        if any(PRICE_FIELD_RE.match(intent.field) is not None for intent in entry_intents):
            anchor = enchant_layout[0] if enchant_layout is not None else None
            price_offsets = _locate_price_list(original, name_end - entry_start, anchor)
            if price_offsets is None:
                skipped.extend(
                    Format3SkippedIntent(intent, "ItemInfo price_list 未唯一定位")
                    for intent in entry_intents
                    if PRICE_FIELD_RE.match(intent.field) is not None
                )

        applied = 0
        for intent in entry_intents:
            if intent.op != "set":
                skipped.append(Format3SkippedIntent(intent, "ItemInfo 价格字段仅支持 op=set"))
                continue
            match = PRICE_FIELD_RE.match(intent.field)
            offsets = price_offsets
            if match is None:
                match = ENCHANT_PRICE_FIELD_RE.match(intent.field)
                if match is None:
                    skipped.append(Format3SkippedIntent(intent, "ItemInfo 价格字段路径不支持"))
                    continue
                if enchant_layout is None:
                    continue
                enchant_index = int(match.group("enchant_index"))
                price_index = int(match.group("price_index"))
                if enchant_index >= len(enchant_layout[1]):
                    skipped.append(Format3SkippedIntent(intent, "enchant_data_list 索引越界"))
                    continue
                offsets = enchant_layout[1][enchant_index]
            else:
                price_index = int(match.group("price_index"))

            if offsets is None or price_index >= len(offsets):
                skipped.append(Format3SkippedIntent(intent, "price_list 索引越界"))
                continue
            changed, reason = _patch_price_leaf(
                patched,
                offsets[price_index],
                match.group("leaf"),
                intent.new,
            )
            if reason is not None:
                skipped.append(Format3SkippedIntent(intent, reason))
            elif changed:
                applied += 1

        if applied and bytes(patched) != original:
            changes.append(
                {
                    "offset": entry_start,
                    "original": original.hex(),
                    "patched": bytes(patched).hex(),
                    "label": f"{entry_name}.iteminfo-prices ({applied} applied)",
                }
            )
    return Format3DispatchResult(tuple(changes), tuple(skipped))


def _locate_enchant_prices(
    record: bytes,
    scan_start: int,
    expected_count: int,
) -> tuple[int, list[list[int]]] | None:
    """Locate the live count+EnchantData block and each buy-price element."""
    candidates: list[tuple[int, int, list[list[int]]]] = []
    for offset in range(max(0, scan_start), len(record) - 4):
        if struct.unpack_from("<I", record, offset)[0] != expected_count:
            continue
        reader = _Reader(record)
        reader.pos = offset + 4
        price_offsets: list[list[int]] = []
        levels: list[int] = []
        try:
            for _ in range(expected_count):
                levels.append(reader.u16())
                _read_EnchantStatData(reader)
                count = reader.u32()
                if count > 64:
                    raise ValueError("buy_price_list count out of range")
                current: list[int] = []
                for _ in range(count):
                    current.append(reader.pos)
                    _read_ItemPriceInfo(reader)
                price_offsets.append(current)
                reader.carray(_read_EquipmentBuff)
                reader.u32()
        except (IndexError, struct.error, ValueError):
            continue
        if levels != list(range(expected_count)):
            continue
        candidates.append((reader.pos - offset, reader.pos, price_offsets))
    if not candidates:
        return None
    candidates.sort(reverse=True, key=lambda item: item[0])
    if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
        return None
    _span, end, offsets = candidates[0]
    return end, offsets


def _locate_price_list(
    record: bytes,
    scan_start: int,
    anchor: int | None,
) -> list[int] | None:
    if anchor is not None:
        return _read_price_list_offsets(record, anchor)

    candidates: list[list[int]] = []
    for offset in range(max(0, scan_start), len(record) - 24):
        offsets = _read_price_list_offsets(record, offset)
        if not offsets:
            continue
        key, price, _sym_no, wrapper = _read_price_tuple(record, offsets[0])
        if key == wrapper and key != 0 and price > 0:
            candidates.append(offsets)
    if len(candidates) != 1:
        return None
    return candidates[0]


def _read_price_list_offsets(record: bytes, offset: int) -> list[int] | None:
    if offset < 0 or offset + 4 > len(record):
        return None
    count = struct.unpack_from("<I", record, offset)[0]
    if not 0 < count <= 64 or offset + 4 + count * 20 > len(record):
        return None
    return [offset + 4 + index * 20 for index in range(count)]


def _read_price_tuple(record: bytes, offset: int) -> tuple[int, int, int, int]:
    return (
        struct.unpack_from("<I", record, offset)[0],
        struct.unpack_from("<Q", record, offset + 4)[0],
        struct.unpack_from("<I", record, offset + 12)[0],
        struct.unpack_from("<I", record, offset + 16)[0],
    )


def _patch_price_leaf(
    record: bytearray,
    offset: int,
    leaf: str,
    value: object,
) -> tuple[bool, str | None]:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return False, f"{leaf} 的 new 值必须是非负整数"
    if leaf == "key":
        field_offset, fmt, limit = offset, "<I", 0xFFFFFFFF
    elif leaf == "price.price":
        field_offset, fmt, limit = offset + 4, "<Q", 0xFFFFFFFFFFFFFFFF
    else:
        field_offset, fmt, limit = offset + 16, "<I", 0xFFFFFFFF
    if value > limit:
        return False, f"{leaf} 的 new 值超出字段宽度"
    packed = struct.pack(fmt, value)
    if record[field_offset:field_offset + len(packed)] == packed:
        return False, None
    record[field_offset:field_offset + len(packed)] = packed
    return True, None
