"""StoreInfo Format 3 writer for the current 1.18 table layout."""

from __future__ import annotations

import logging
import struct
from collections import defaultdict
from dataclasses import replace
from typing import Any

from cdmm.services.pab_table_service import build_entry_bounds, parse_pabgh_index
from cdmm.services.storeinfo_native_parser import (
    STOCK_CONST_OFFSET,
    StockRecord,
    StoreinfoParseError,
    parse_stock_list,
    serialize_stock_list,
)

logger = logging.getLogger(__name__)

StockTemplateIndex = dict[int, list[StockRecord]]


class StoreinfoWriteRefused(ValueError):
    """The target cannot be changed inside the verified StoreInfo layout."""


def build_storeinfo_changes(
    vanilla_body: bytes,
    vanilla_header: bytes,
    intents: list[Any],
) -> tuple[list[dict], dict | None]:
    """Apply all requested StoreInfo edits in memory and rebuild PABGH once."""
    key_size, offsets = parse_pabgh_index(vanilla_header, "storeinfo")
    if key_size not in (2, 4) or not offsets:
        raise StoreinfoWriteRefused("storeinfo.pabgh 索引无效")
    bounds = build_entry_bounds(vanilla_body, key_size, offsets)
    by_name = {item[2]: key for key, item in bounds.items() if item[2]}
    templates_by_store, templates_by_item, generic_templates = _build_template_indexes(
        vanilla_body,
        bounds,
    )

    grouped: dict[int, list[Any]] = defaultdict(list)
    for intent in intents:
        key = _resolve_intent_key(intent, offsets, by_name)
        grouped[key].append(intent)

    replacements: dict[int, bytes] = {}
    deltas: list[tuple[int, int]] = []
    for key, entry_intents in grouped.items():
        if key not in bounds:
            raise StoreinfoWriteRefused(f"store entry {key} 边界无效")
        start, end, _name, _name_end = bounds[key]
        original_entry = vanilla_body[start:end]
        patched_entry = _patch_store_entry(
            original_entry,
            key,
            key_size,
            entry_intents,
            templates_by_store.get(key, []),
            templates_by_item,
            generic_templates,
        )
        if patched_entry == original_entry:
            continue
        replacements[start] = patched_entry
        deltas.append((start, len(patched_entry) - len(original_entry)))

    if not replacements:
        return [], None

    patched_body = bytearray(vanilla_body)
    for start in sorted(replacements, reverse=True):
        end = next(item[1] for item in bounds.values() if item[0] == start)
        patched_body[start:end] = replacements[start]

    patched_header = _rebuild_header(vanilla_header, key_size, deltas)
    body_change = {
        "offset": 0,
        "original": vanilla_body.hex(),
        "patched": bytes(patched_body).hex(),
        "label": "storeinfo whole-table rebuild",
    }
    header_change = None
    if patched_header != vanilla_header:
        header_change = {
            "offset": 0,
            "original": vanilla_header.hex(),
            "patched": patched_header.hex(),
            "label": "storeinfo.pabgh offset rebuild",
        }
    return [body_change], header_change


def _resolve_intent_key(
    intent: Any,
    offsets: dict[int, int],
    by_name: dict[str, int],
) -> int:
    key = getattr(intent, "key", None)
    if isinstance(key, int) and key in offsets:
        return key
    entry = getattr(intent, "entry", "") or ""
    resolved = by_name.get(entry)
    if resolved is None:
        raise StoreinfoWriteRefused(f"store key={key!r} / entry={entry!r} 未命中")
    return resolved


def _patch_store_entry(
    entry: bytes,
    key: int,
    key_size: int,
    intents: list[Any],
    store_templates: list[StockRecord],
    templates_by_item: StockTemplateIndex,
    generic_templates: list[StockRecord],
) -> bytes:
    count_offset, list_end, vanilla_records = _locate_stock_list(entry, key)
    output_records = list(vanilla_records)
    replace_list = False
    structural_change = False
    raw_c_changes: list[tuple[int, int]] = []
    scalar_changes: list[tuple[str, int]] = []
    template_replays = 0
    generic_replays = 0
    rejected_records = 0

    for intent in intents:
        field = (getattr(intent, "field", "") or "").strip()
        op = getattr(intent, "op", "set") or "set"
        value = getattr(intent, "new", None)
        if field in {"stock_data_list", "_exchangeItemInfoListForSell"}:
            if op == "set" and isinstance(value, list):
                output_records = []
                for item in value:
                    requested = _record_from_json(item)
                    replayed, replay_kind = _replay_current_stock_template(
                        requested,
                        key,
                        store_templates,
                        templates_by_item,
                        generic_templates,
                    )
                    if replayed is None:
                        rejected_records += 1
                        continue
                    output_records.append(replayed)
                    template_replays += replay_kind == "item"
                    generic_replays += replay_kind == "generic"
                replace_list = True
                structural_change = True
            elif op == "array_append" and isinstance(value, dict):
                requested = _record_from_json(value)
                replayed, replay_kind = _replay_current_stock_template(
                    requested,
                    key,
                    store_templates,
                    templates_by_item,
                    generic_templates,
                )
                if replayed is None:
                    rejected_records += 1
                    continue
                output_records.append(replayed)
                template_replays += replay_kind == "item"
                generic_replays += replay_kind == "generic"
                structural_change = True
            else:
                raise StoreinfoWriteRefused(f"{field} 不支持 op={op!r} / value={type(value).__name__}")
            continue
        if field in {"buyable_stock_count", "sellable_stock_count", "exchange_item_info_for_buy"}:
            if op != "set" or isinstance(value, bool) or not isinstance(value, int):
                raise StoreinfoWriteRefused(f"{field} 必须是整数 set")
            scalar_changes.append((field, value))
            continue
        index = _raw_c_index(field)
        if index is not None:
            if op != "set" or isinstance(value, bool) or not isinstance(value, int):
                raise StoreinfoWriteRefused(f"{field} 必须是整数 set")
            raw_c_changes.append((index, value))
            continue
        raise StoreinfoWriteRefused(f"不支持字段 {field!r}")

    for index, value in raw_c_changes:
        if not 0 <= index < len(output_records):
            raise StoreinfoWriteRefused(
                f"stock_data_list[{index}].raw_c 越界，当前记录数 {len(output_records)}"
            )
        output_records[index].raw_c = value

    patched = bytearray(entry)
    try:
        new_list = serialize_stock_list(output_records)
    except StoreinfoParseError as exc:
        raise StoreinfoWriteRefused(f"stock list 序列化失败：{exc}") from exc
    patched[count_offset:list_end] = new_list
    delta = len(new_list) - (list_end - count_offset)

    payload = _entry_payload_offset(entry, key_size)
    for field, value in scalar_changes:
        if field == "buyable_stock_count":
            offset = count_offset - 9
            if structural_change:
                value = len(output_records)
        elif field == "sellable_stock_count":
            offset = count_offset - 5
        else:
            offset = payload
        if offset < 0 or offset + 4 > len(entry):
            raise StoreinfoWriteRefused(f"{field} 定位越界")
        if offset >= list_end:
            offset += delta
        struct.pack_into("<I", patched, offset, value & 0xFFFFFFFF)

    logger.info(
        "storeinfo writer: store %d stock list %d -> %d records%s; "
        "current-item templates=%d generic-current templates=%d rejected=%d",
        key,
        len(vanilla_records),
        len(output_records),
        " (replace)" if replace_list else "",
        template_replays,
        generic_replays,
        rejected_records,
    )
    return bytes(patched)


def _build_template_indexes(
    body: bytes,
    bounds: dict[int, tuple[int, int, str, int]],
) -> tuple[dict[int, list[StockRecord]], StockTemplateIndex, list[StockRecord]]:
    """Index only stock records that round-trip from the current vanilla table."""
    by_store: dict[int, list[StockRecord]] = {}
    by_item: StockTemplateIndex = defaultdict(list)
    all_records: list[StockRecord] = []
    for key, (start, end, _name, _name_end) in bounds.items():
        try:
            _count_offset, _list_end, records = _locate_stock_list(body[start:end], key)
        except StoreinfoWriteRefused:
            continue
        by_store[key] = records
        all_records.extend(records)
        for record in records:
            by_item[record.body].append(record)
    return by_store, dict(by_item), all_records


def _replay_current_stock_template(
    requested: StockRecord,
    store_key: int,
    store_templates: list[StockRecord],
    templates_by_item: StockTemplateIndex,
    generic_templates: list[StockRecord],
) -> tuple[StockRecord | None, str]:
    """Rebuild one exported record from current-version vanilla semantics.

    A known item must retain its current discriminator.  New items that have no
    vanilla StoreInfo record inherit a compatible record from the target store;
    only the store identity, item identity, stock count and ordering fields are
    changed.  This keeps 1.18-only value fields out of older Format 3 exports.
    """
    item_templates = templates_by_item.get(requested.body, [])
    same_disc = [item for item in item_templates if item.disc == requested.disc]
    if item_templates and not same_disc:
        logger.warning(
            "storeinfo writer: store %d item %d requested disc=%d, but current "
            "vanilla only has disc=%s; replaying as a discriminator-distinct "
            "generic current record",
            store_key,
            requested.body,
            requested.disc,
            sorted({item.disc for item in item_templates}),
        )

    if same_disc:
        template = next(
            (item for item in same_disc if item.lookup_a == store_key),
            same_disc[0],
        )
        if template.is_restore_item and template.lookup_a != store_key:
            logger.warning(
                "storeinfo writer: store %d item %d only has a RestoreItem "
                "template from store %d; rejected to preserve global uniqueness",
                store_key,
                requested.body,
                template.lookup_a,
            )
            return None, "rejected"
        replayed = replace(
            template,
            lookup_a=store_key,
        )
        return replayed, "item"

    compatible = [
        item
        for item in store_templates
        if item.disc == requested.disc
        and (item.sub_data is None) == (requested.sub_data is None)
        and not item.is_restore_item
    ]
    if not compatible:
        compatible = [
            item
            for item in generic_templates
            if item.disc == requested.disc
            and (item.sub_data is None) == (requested.sub_data is None)
            and not item.is_restore_item
        ]
        if not compatible:
            raise StoreinfoWriteRefused(
                f"当前原版没有非 RestoreItem 的 disc={requested.disc} / "
                f"sub_data={requested.sub_data is not None} 的 stock 模板"
            )
    template = compatible[0]
    replayed = replace(
        template,
        lookup_a=store_key,
        body=requested.body,
        value_raw_q=requested.body,
    )
    return replayed, "generic"


def _locate_stock_list(entry: bytes, key: int) -> tuple[int, int, list[StockRecord]]:
    """Locate the unique verified stock chain by its key and preceding count."""
    candidates: list[tuple[int, int, list[StockRecord]]] = []
    key_format = "<H" if key <= 0xFFFF else "<I"
    key_width = 2 if key_format == "<H" else 4
    for record_start in range(4, len(entry) - 118):
        if record_start + key_width > len(entry):
            break
        if struct.unpack_from(key_format, entry, record_start)[0] != key:
            continue
        if entry[record_start + STOCK_CONST_OFFSET] != 1:
            continue
        count_offset = record_start - 4
        count = struct.unpack_from("<I", entry, count_offset)[0]
        if not 0 < count < 10000:
            continue
        try:
            records, _start, list_end = parse_stock_list(entry, count_offset)
        except (StoreinfoParseError, struct.error, IndexError):
            continue
        if len(records) == count:
            candidates.append((count_offset, list_end, records))
    unique = {(start, end): records for start, end, records in candidates}
    if len(unique) != 1:
        raise StoreinfoWriteRefused(
            f"store entry {key}: stock list 未唯一定位，候选 {len(unique)}"
        )
    (start, end), records = next(iter(unique.items()))
    return start, end, records


def _record_from_json(value: object) -> StockRecord:
    if not isinstance(value, dict):
        raise StoreinfoWriteRefused("stock record 必须是 object")
    nested = value.get("value")
    if not isinstance(nested, dict):
        raise StoreinfoWriteRefused("stock record.value 必须是 object")
    payload = nested.get("payload")
    if not isinstance(payload, dict):
        raise StoreinfoWriteRefused("stock record.value.payload 必须是 object")
    disc = _integer(nested, "disc", 0)
    if disc not in (0, 3):
        raise StoreinfoWriteRefused(f"新增 stock record disc={disc} 未验证")
    payload_type = payload.get("type")
    if payload_type not in (None, f"Disc{disc}"):
        raise StoreinfoWriteRefused(
            f"stock payload type {payload_type!r} 与 disc={disc} 不一致"
        )
    effects = value.get("effect_list") or []
    if effects:
        raise StoreinfoWriteRefused("stock record effect_list 非空，当前布局未验证")
    return StockRecord(
        lookup_a=_integer(value, "lookup_a"),
        raw_a=_integer(value, "raw_a"),
        raw_b=_integer(value, "raw_b"),
        raw_c=_integer(value, "raw_c"),
        order_index_113=_integer(value, "order_index_113", 0xFFFFFFFF),
        raw_d=_integer(value, "raw_d"),
        raw_e=_integer(value, "raw_e"),
        low_price_threshold_count_116=_integer(
            value, "low_price_threshold_count_116", 0xFFFFFFFF
        ),
        flag_a=_integer(value, "flag_a"),
        flag_b=_integer(value, "flag_b"),
        flag_c=_integer(value, "flag_c"),
        is_restore_item=_integer(value, "is_restore_item"),
        body=_integer(payload, "body"),
        value_lookup_a=_integer(nested, "lookup_a"),
        disc=disc,
        value_lookup_b=_integer(nested, "lookup_b"),
        value_lookup_c=_integer(nested, "lookup_c"),
        value_raw_a=_integer(nested, "raw_a"),
        value_raw_b=_integer(nested, "raw_b"),
        value_raw_d=_integer(nested, "raw_d"),
        value_raw_e=_integer(nested, "raw_e"),
        value_raw_f=_integer(nested, "raw_f"),
        value_raw_g=_integer(nested, "raw_g", 0xFFFF),
        value_raw_q=_integer(nested, "raw_q", _integer(payload, "body")),
        lookup_b=_integer(value, "lookup_b"),
        lookup_c=_integer(value, "lookup_c"),
        sub_data=value.get("sub_data"),
        effect_list=[],
    )


def _integer(mapping: dict, field: str, default: int = 0) -> int:
    value = mapping.get(field, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise StoreinfoWriteRefused(f"{field}={value!r} 不是整数")
    return value


def _raw_c_index(field: str) -> int | None:
    prefix = "stock_data_list["
    suffix = "].raw_c"
    if not field.startswith(prefix) or not field.endswith(suffix):
        return None
    try:
        return int(field[len(prefix):-len(suffix)])
    except ValueError:
        return None


def _entry_payload_offset(entry: bytes, key_size: int) -> int:
    if len(entry) < key_size + 4:
        raise StoreinfoWriteRefused("store entry header truncated")
    name_length = struct.unpack_from("<I", entry, key_size)[0]
    name_end = key_size + 4 + name_length
    if name_end > len(entry):
        raise StoreinfoWriteRefused("store entry name out of range")
    return name_end + 1 if name_end < len(entry) and entry[name_end] == 0 else name_end


def _rebuild_header(header: bytes, key_size: int, deltas: list[tuple[int, int]]) -> bytes:
    for candidate in (2, 4):
        if len(header) < candidate:
            continue
        count = struct.unpack_from("<H" if candidate == 2 else "<I", header, 0)[0]
        if candidate + count * (key_size + 4) == len(header):
            count_size = candidate
            break
    else:
        raise StoreinfoWriteRefused("storeinfo.pabgh 长度与 count 不一致")

    output = bytearray(header)
    count = struct.unpack_from("<H" if count_size == 2 else "<I", header, 0)[0]
    pos = count_size
    for _ in range(count):
        old_offset = struct.unpack_from("<I", header, pos + key_size)[0]
        new_offset = old_offset + sum(delta for start, delta in deltas if old_offset > start)
        struct.pack_into("<I", output, pos + key_size, new_offset)
        pos += key_size + 4
    return bytes(output)
