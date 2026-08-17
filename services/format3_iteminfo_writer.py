"""Format 3 ItemInfo 专用字节补丁生成器。

当前独立加载器先支持真实常见的
``prefab_data_list[N].tribe_gender_list`` 以及 `drop_default_data` 中少量
原始字段写入，不依赖完整管理器的数据库或 crimson_rs 整表解析器，直接在
vanilla PABGB entry 内定位目标并生成传统 byte patch。
"""

from __future__ import annotations

import math
import re
import struct
from typing import Any

from cdmm.services.format3_parser import Format3Intent
from cdmm.services.format3_runtime import (
    Format3DispatchResult,
    Format3RuntimeContext,
    Format3SkippedIntent,
)
from cdmm.services.format3_iteminfo_whole_writer import (
    build_iteminfo_whole_table_result,
    shape_matches,
    should_use_iteminfo_whole_table,
    _coerce_prefab_data_list,
)
from cdmm.services.format3_iteminfo_record_writer import (
    build_iteminfo_record_result,
    should_use_iteminfo_record_writer,
)
from cdmm.services.format3_iteminfo_price_writer import (
    build_iteminfo_price_result,
    is_iteminfo_price_field,
)
from cdmm.services.iteminfo_native_parser import (
    _Reader,
    _read_EnchantStatData,
    _read_EquipmentBuff,
    _read_ItemPriceInfo,
    parse_iteminfo_drop_default_data,
    parse_iteminfo_prefab_data_list,
    parse_iteminfo_visual_prefab_lists,
    serialize_iteminfo_drop_default_data,
    serialize_iteminfo_prefab_data_list,
    serialize_iteminfo_visual_prefab_lists,
)

# Format 3 字段路径：装备可穿戴种族/性别数组。
PREFAB_TRIBE_GENDER_FIELD_RE = re.compile(
    r"^prefab_data_list\[(?P<index>\d+)]\.tribe_gender_list$"
)

DROP_DEFAULT_FIELD_RE = re.compile(
    r"^drop_default_data\.(?P<field>drop_enchant_level|socket_item_list|"
    r"add_socket_material_item_list|default_sub_item|"
    r"socket_valid_count|use_socket)$"
)

ENCHANT_EQUIP_BUFFS_FIELD_RE = re.compile(
    r"^enchant_data_list\[(?P<index>\d+)]\.equip_buffs$"
)

ITEMINFO_SUPPORTED_FIELD_REASON = (
    "iteminfo 仅支持 prefab_data_list[N].tribe_gender_list、"
    "drop_default_data.drop_enchant_level、"
    "drop_default_data.socket_item_list、"
    "drop_default_data.add_socket_material_item_list、"
    "drop_default_data.default_sub_item、"
    "drop_default_data.socket_valid_count、"
    "drop_default_data.use_socket，"
    "以及已迁入的单记录/whole-table 字段（如 equipable_hash、cooltime 等）"
)

# 单个 entry 内数组数量的安全上限，用于快速拒绝错位读取。
MAX_REASONABLE_ARRAY_COUNT = 1_000_000

# 扫描 fallback 只接受较小的 PrefabData 数量，降低误命中普通 u32 的风险。
MAX_PREFAB_SCAN_COUNT = 128

# DMM V3 导出的 Equip Everything 使用的尾部 prefab 结构。
# 真实字节形态为 count + 多个 legacy element，element 以三个 f32 scale 开头。
LEGACY_PREFAB_SCALE_NEEDLE = struct.pack("<fff", 1.0, 1.0, 1.0)

# 1.18（EXE 1.0.0.2443）起，ItemInfo 的 legacy PrefabData 元素在
# tribe_gender_list 之后新增一个 u32 unk 字段，随后才是 3 个尾字节
# （is_craft_material / use_gimmick_prefab / prefab_data_type）。
# 实测 1.18 原版装备记录中该字段恒为 0xEAC5E173（8 个样本全一致），
# 旧版（1.17 及以前）没有该字段，尾部只有 3 字节。
LEGACY_PREFAB_UNK_MAGIC = 0xEAC5E173
LEGACY_PREFAB_UNK_TAIL_BYTES = 4

# 只在单条 ItemInfo 记录尾部小窗口内扫描 legacy prefab，避免误命中普通数据。
LEGACY_PREFAB_SCAN_TAIL_BYTES = 8192

# ItemInfo 从 payload 起点走到 _prefabDataList 前需要消费的字段。
ITEMINFO_FIELDS_BEFORE_PREFAB: tuple[str, ...] = (
    "u8",
    "u64",
    "LocalizableString",
    "u32",
    "u16",
    "u32",
    "CArray<OccupiedEquipSlotData>",
    "CArray<u32>",
    "u32",
    "CArray<u32>",
    "CArray<u32>",
    "CArray<ItemIconData>",
    "u32",
    "u32",
    "u8",
    "u8",
    "u32",
    "u32",
    "LocalizableString",
    "LocalizableString",
    "u32",
    "u16",
    "u32",
    "u8",
    "u32",
    "CArray<PassiveSkillLevel>",
    "u8",
    "u8",
    "u32",
    "u32",
    "u16",
    "CString",
    "CString",
    "u32",
    "CArray<CString>",
    "u32",
    "u8",
    "u8",
    "CArray<SealableItemInfo>",
    "CArray<SealableItemInfo>",
    "CArray<SealableItemInfo>",
    "CArray<SealableItemInfo>",
    "CArray<SealableItemInfo>",
    "CArray<u32>",
    "u8",
    "u32",
    "u8",
    "CArray<u32>",
    "CArray<u32>",
    "CArray<u16>",
    "u8",
    "CArray<u32>",
    "u8",
    "u8",
    "u8",
    "u8",
    "u8",
    "u8",
    "u8",
    "CArray<ReserveSlotTargetData>",
    "u8",
    "u8",
    "u8",
    "DropDefaultData",
)

# DropDefaultData 位于 prefab_data_list 前一位，后续继续迁 iteminfo
# nested path / whole-table writer 时可以复用这里的边界定义。
ITEMINFO_FIELDS_BEFORE_DROP_DEFAULT = ITEMINFO_FIELDS_BEFORE_PREFAB[:-1]

DROP_DEFAULT_FIELD_LAYOUTS: dict[str, tuple[tuple[str, ...], str]] = {
    "drop_enchant_level": ((), "u16"),
    "socket_item_list": (
        ("u16",),
        "CArray<u32>",
    ),
    "add_socket_material_item_list": (
        ("u16", "CArray<u32>"),
        "CArray<SocketMaterialItem>",
    ),
    "default_sub_item": (
        ("u16", "CArray<u32>", "CArray<SocketMaterialItem>"),
        "SubItem",
    ),
    "socket_valid_count": (
        (
            "u16",
            "CArray<u32>",
            "CArray<SocketMaterialItem>",
            "SubItem",
        ),
        "u8",
    ),
    "use_socket": (
        (
            "u16",
            "CArray<u32>",
            "CArray<SocketMaterialItem>",
            "SubItem",
            "u8",
        ),
        "u8",
    ),
}

PRIMITIVE_WIDTHS: dict[str, int] = {
    "u8": 1,
    "i8": 1,
    "u16": 2,
    "i16": 2,
    "u32": 4,
    "i32": 4,
    "u64": 8,
    "i64": 8,
    "f32": 4,
    "f64": 8,
}

SUBSTRUCT_DEFS: dict[str, tuple[tuple[str, str], ...]] = {
    "OccupiedEquipSlotData": (
        ("equip_slot_name_key", "u32"),
        ("equip_slot_name_index_list", "CArray<u8>"),
    ),
    "ItemIconData": (
        ("icon_path", "u32"),
        ("check_exist_sealed_data", "u8"),
        ("gimmick_state_list", "CArray<u32>"),
    ),
    "PassiveSkillLevel": (
        ("skill", "u32"),
        ("level", "u32"),
    ),
    "ReserveSlotTargetData": (
        ("reserve_slot_info", "u32"),
        ("condition_info", "u32"),
    ),
    "SocketMaterialItem": (
        ("item", "u32"),
        ("value", "u64"),
    ),
    "DropDefaultData": (
        ("drop_enchant_level", "u16"),
        ("socket_item_list", "CArray<u32>"),
        ("add_socket_material_item_list", "CArray<SocketMaterialItem>"),
        ("default_sub_item", "SubItem"),
        ("socket_valid_count", "u8"),
        ("use_socket", "u8"),
    ),
}

TAGGED_VARIANTS: dict[str, dict[int, str]] = {
    "SubItem": {
        0: "u32",
        3: "u32",
        9: "u32",
        14: "",
    },
    "SealableItemInfo": {
        0: "u32",
        1: "u32",
        2: "CString",
        3: "u32",
        4: "u32",
    },
}

# 带 discriminator 的 variant 在 tag 后还会固定携带的字段。
TAGGED_FIXED_PREFIX: dict[str, tuple[str, ...]] = {
    "SubItem": (),
    "SealableItemInfo": ("u32", "u64"),
}


def build_iteminfo_prefab_changes(
    vanilla_body: bytes,
    key_size: int,
    entry_bounds: dict[int, tuple[int, int, str, int]],
    intents: list[dict[str, Any]],
) -> tuple[list[dict], int]:
    """把 ItemInfo Format 3 intents 转成传统 byte patch changes。"""
    changes: list[dict] = []
    skipped = 0
    for intent in intents:
        change, _reason = _build_single_change_with_reason(
            vanilla_body,
            key_size,
            entry_bounds,
            intent,
        )
        if change is None:
            skipped += 1
        else:
            changes.append(change)
    return changes, skipped


def build_iteminfo_prefab_result(
    context: Format3RuntimeContext,
    intents: list[Format3Intent],
) -> Format3DispatchResult:
    """按字段能力自动分流到窄 writer 或 whole-table writer。"""
    if intents and all(is_iteminfo_price_field(intent.field) for intent in intents):
        return build_iteminfo_price_result(context, intents)
    visual_fields = {"prefab_data_list", "gimmick_visual_prefab_data_list"}
    if intents and all(intent.field in visual_fields for intent in intents):
        if any(intent.field == "gimmick_visual_prefab_data_list" for intent in intents):
            return _build_iteminfo_visual_lists_result(context, intents)
        return _build_iteminfo_prefab_list_result(context, intents)
    if should_use_iteminfo_record_writer(intents):
        return build_iteminfo_record_result(context, intents)
    if should_use_iteminfo_whole_table(intents):
        return build_iteminfo_whole_table_result(context, intents)

    changes: list[dict] = []
    skipped: list[Format3SkippedIntent] = []
    drop_default_intents = [
        intent for intent in intents
        if DROP_DEFAULT_FIELD_RE.match(intent.field) is not None
    ]
    enchant_intents = [
        intent for intent in intents
        if ENCHANT_EQUIP_BUFFS_FIELD_RE.match(intent.field) is not None
    ]
    non_drop_default_intents = [
        intent for intent in intents
        if DROP_DEFAULT_FIELD_RE.match(intent.field) is None
        and ENCHANT_EQUIP_BUFFS_FIELD_RE.match(intent.field) is None
    ]
    if drop_default_intents:
        fallback_result = _build_drop_default_record_fallback_result(
            context,
            drop_default_intents,
        )
        changes.extend(fallback_result.changes)
        skipped.extend(fallback_result.skipped)

    if enchant_intents:
        enchant_result = _build_enchant_equip_buffs_result(context, enchant_intents)
        changes.extend(enchant_result.changes)
        skipped.extend(enchant_result.skipped)

    for intent in non_drop_default_intents:
        change, reason = _build_single_change_with_reason(
            context.body,
            context.key_size,
            context.entry_bounds,
            intent.to_legacy_dict(),
        )
        if change is None:
            skipped.append(
                Format3SkippedIntent(
                    intent=intent,
                    reason=reason or "writer 未生成补丁",
                )
            )
            continue
        changes.append(change)
    return Format3DispatchResult(
        changes=tuple(changes),
        skipped=tuple(skipped),
    )


def _build_enchant_equip_buffs_result(
    context: Format3RuntimeContext,
    intents: list[Format3Intent],
) -> Format3DispatchResult:
    """窄写入现有EnchantData数组中的equip_buffs，保留live尾部u32。"""
    grouped: dict[int, tuple[tuple[int, int, str, int], list[Format3Intent]]] = {}
    skipped: list[Format3SkippedIntent] = []
    for intent in intents:
        bounds, reason = _resolve_entry_bounds(context.entry_bounds, intent.to_legacy_dict())
        if bounds is None:
            skipped.append(Format3SkippedIntent(intent, reason or "目标entry未命中"))
            continue
        grouped.setdefault(bounds[0], (bounds, []))[1].append(intent)

    changes: list[dict] = []
    for bounds, entry_intents in grouped.values():
        entry_off, entry_end, entry_name, name_end = bounds
        max_index = max(
            int(ENCHANT_EQUIP_BUFFS_FIELD_RE.match(intent.field).group("index"))  # type: ignore[union-attr]
            for intent in entry_intents
        )
        record = context.body[entry_off:entry_end]
        located = _locate_live_enchant_data(record, name_end - entry_off, max_index + 1)
        if located is None:
            skipped.extend(
                Format3SkippedIntent(intent, "iteminfo live EnchantData数组未唯一定位")
                for intent in entry_intents
            )
            continue
        array_start, array_end, buff_ranges = located
        replacements: list[tuple[int, int, bytes, Format3Intent]] = []
        for intent in entry_intents:
            if intent.op != "set":
                skipped.append(Format3SkippedIntent(intent, "enchant equip_buffs仅支持op=set"))
                continue
            index = int(ENCHANT_EQUIP_BUFFS_FIELD_RE.match(intent.field).group("index"))  # type: ignore[union-attr]
            patched = _pack_equipment_buffs(intent.new)
            if patched is None:
                skipped.append(Format3SkippedIntent(intent, "equip_buffs必须是buff/level u32数组"))
                continue
            start, end = buff_ranges[index]
            original = record[start:end]
            if original == patched:
                skipped.append(Format3SkippedIntent(intent, "目标字节已是期望值"))
                continue
            replacements.append((start, end, patched, intent))
        if not replacements:
            continue
        original_array = record[array_start:array_end]
        patched_array = bytearray(original_array)
        for start, end, patched, _intent in sorted(replacements, reverse=True):
            local_start = start - array_start
            local_end = end - array_start
            patched_array[local_start:local_end] = patched
        changes.append(
            {
                "entry": entry_name or entry_intents[0].entry,
                "rel_offset": entry_off + array_start - name_end,
                "original": original_array.hex(),
                "patched": bytes(patched_array).hex(),
                "label": f"{entry_name}.enchant_data_list.equip_buffs",
                "_dynamic_entry_offset": True,
            }
        )
    return Format3DispatchResult(tuple(changes), tuple(skipped))


def _locate_live_enchant_data(
    record: bytes,
    scan_start: int,
    expected_count: int,
) -> tuple[int, int, list[tuple[int, int]]] | None:
    """定位count+EnchantData数组；每条live记录比旧结构多一个尾部u32。"""
    candidates: list[tuple[int, int, list[tuple[int, int]]]] = []
    for offset in range(max(0, scan_start), len(record) - 4):
        if struct.unpack_from("<I", record, offset)[0] != expected_count:
            continue
        reader = _Reader(record)
        reader.pos = offset + 4
        buff_ranges: list[tuple[int, int]] = []
        try:
            levels: list[int] = []
            for _ in range(expected_count):
                levels.append(reader.u16())
                _read_EnchantStatData(reader)
                reader.carray(_read_ItemPriceInfo)
                buff_start = reader.pos
                reader.carray(_read_EquipmentBuff)
                buff_ranges.append((buff_start, reader.pos))
                reader.u32()
        except (ValueError, struct.error):
            continue
        if levels != list(range(expected_count)):
            continue
        candidates.append((reader.pos - offset, offset, buff_ranges))
    if not candidates:
        return None
    candidates.sort(reverse=True, key=lambda item: item[0])
    if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
        return None
    span, offset, ranges = candidates[0]
    return offset, offset + span, ranges


def _pack_equipment_buffs(value: object) -> bytes | None:
    """序列化CArray<EquipmentBuff>。"""
    if not isinstance(value, list):
        return None
    output = bytearray(struct.pack("<I", len(value)))
    for item in value:
        if not isinstance(item, dict):
            return None
        buff = item.get("buff")
        level = item.get("level")
        if any(isinstance(item_value, bool) or not isinstance(item_value, int) or not 0 <= item_value <= 0xFFFFFFFF for item_value in (buff, level)):
            return None
        output += struct.pack("<II", buff, level)
    return bytes(output)


def _build_iteminfo_prefab_list_result(
    context: Format3RuntimeContext,
    intents: list[Format3Intent],
) -> Format3DispatchResult:
    """快速处理 DMM 扁平导出的整条 prefab_data_list intent。

    Equip Everything 这类模组会一次性提交数千条 `prefab_data_list`
    set intent。整表 whole-table roundtrip 会非常慢，这里只解析被命中的
    单条 ItemInfo 记录，并复用 native parser 保留 prefab 内未知 tribe 块。
    """
    changes: list[dict] = []
    skipped: list[Format3SkippedIntent] = []
    for intent in intents:
        if intent.op != "set":
            skipped.append(Format3SkippedIntent(intent=intent, reason="iteminfo prefab_data_list 仅支持 op=set"))
            continue
        bounds, resolve_reason = _resolve_entry_bounds(context.entry_bounds, intent.to_legacy_dict())
        if bounds is None:
            skipped.append(Format3SkippedIntent(intent=intent, reason=resolve_reason or "目标 entry 未命中"))
            continue

        entry_off, entry_end, entry_name, name_end = bounds
        entry_bytes = context.body[entry_off:entry_end]
        try:
            existing, field_start, field_end = parse_iteminfo_prefab_data_list(entry_bytes)
        except Exception as exc:
            legacy_change, legacy_reason = _build_legacy_prefab_list_change(
                entry_bytes,
                entry_off,
                entry_name,
                name_end,
                intent,
            )
            if legacy_change is not None:
                changes.append(legacy_change)
                continue
            reason = legacy_reason or f"iteminfo prefab_data_list 定位失败：{exc}"
            skipped.append(Format3SkippedIntent(intent=intent, reason=reason))
            continue

        new_value, reason = _coerce_prefab_data_list(existing, intent.new)
        if reason is not None:
            legacy_change, legacy_reason = _build_legacy_prefab_list_change(
                entry_bytes,
                entry_off,
                entry_name,
                name_end,
                intent,
            )
            if legacy_change is not None:
                changes.append(legacy_change)
                continue
            if legacy_reason is not None:
                reason = f"{reason}；legacy fallback 未生效：{legacy_reason}"
            skipped.append(Format3SkippedIntent(intent=intent, reason=reason))
            continue
        if not shape_matches(existing, new_value):
            skipped.append(Format3SkippedIntent(intent=intent, reason="prefab_data_list 新值结构不匹配"))
            continue

        try:
            patched = serialize_iteminfo_prefab_data_list(new_value)
        except Exception as exc:
            skipped.append(Format3SkippedIntent(intent=intent, reason=f"iteminfo prefab_data_list 序列化失败：{exc}"))
            continue
        original = entry_bytes[field_start:field_end]
        if patched == original:
            skipped.append(Format3SkippedIntent(intent=intent, reason="目标字节已是期望值"))
            continue

        changes.append(
            {
                "entry": entry_name or str(intent.entry or intent.key),
                "rel_offset": entry_off + field_start - name_end,
                "original": original.hex(),
                "patched": patched.hex(),
                "label": f"{intent.entry or intent.key}.prefab_data_list",
            }
        )
    return Format3DispatchResult(
        changes=tuple(changes),
        skipped=tuple(skipped),
    )


def _build_iteminfo_visual_lists_result(
    context: Format3RuntimeContext,
    intents: list[Format3Intent],
) -> Format3DispatchResult:
    """按单条记录同时替换 prefab 与 gimmick visual prefab 列表。"""
    grouped: dict[int, tuple[tuple[int, int, str, int], list[Format3Intent]]] = {}
    skipped: list[Format3SkippedIntent] = []
    for intent in intents:
        if intent.op != "set":
            skipped.append(Format3SkippedIntent(intent=intent, reason="visual prefab 列表仅支持 op=set"))
            continue
        bounds, reason = _resolve_entry_bounds(context.entry_bounds, intent.to_legacy_dict())
        if bounds is None:
            skipped.append(Format3SkippedIntent(intent=intent, reason=reason or "目标 entry 未命中"))
            continue
        grouped.setdefault(bounds[0], (bounds, []))[1].append(intent)

    changes: list[dict] = []
    for bounds, entry_intents in grouped.values():
        legacy_copy, legacy_reason = _build_legacy_visual_copy_change(
            context,
            bounds,
            entry_intents,
        )
        if legacy_copy is not None:
            changes.append(legacy_copy)
            continue
        if legacy_reason not in {None, "visual intent 未成对"}:
            skipped.extend(
                Format3SkippedIntent(intent=intent, reason=legacy_reason)
                for intent in entry_intents
            )
            continue
        entry_off, entry_end, entry_name, name_end = bounds
        entry_bytes = context.body[entry_off:entry_end]
        try:
            prefab_values, gimmick_values, field_start, field_end = (
                parse_iteminfo_visual_prefab_lists(entry_bytes)
            )
        except Exception as exc:
            skipped.extend(
                Format3SkippedIntent(intent=intent, reason=f"visual prefab 列表定位失败：{exc}")
                for intent in entry_intents
            )
            continue

        new_prefab = prefab_values
        new_gimmick = gimmick_values
        valid = True
        for intent in entry_intents:
            if intent.field == "prefab_data_list":
                incoming_prefab = intent.new
                if not prefab_values and isinstance(incoming_prefab, list):
                    incoming_prefab = [
                        {"tag_name_hash": 0, **item} if isinstance(item, dict) else item
                        for item in incoming_prefab
                    ]
                new_prefab, reason = _coerce_prefab_data_list(prefab_values, incoming_prefab)
                if reason is not None or not shape_matches(prefab_values, new_prefab):
                    skipped.append(
                        Format3SkippedIntent(
                            intent=intent,
                            reason=reason or "prefab_data_list 新值结构不匹配",
                        )
                    )
                    valid = False
            elif not shape_matches(gimmick_values, intent.new):
                skipped.append(
                    Format3SkippedIntent(
                        intent=intent,
                        reason="gimmick_visual_prefab_data_list 新值结构不匹配",
                    )
                )
                valid = False
            else:
                new_gimmick = intent.new
        if not valid:
            continue
        try:
            patched = serialize_iteminfo_visual_prefab_lists(new_prefab, new_gimmick)
        except Exception as exc:
            skipped.extend(
                Format3SkippedIntent(intent=intent, reason=f"visual prefab 列表序列化失败：{exc}")
                for intent in entry_intents
            )
            continue
        original = entry_bytes[field_start:field_end]
        if patched == original:
            skipped.extend(
                Format3SkippedIntent(intent=intent, reason="目标字节已是期望值")
                for intent in entry_intents
            )
            continue
        changes.append(
            {
                "entry": entry_name or str(entry_intents[0].entry or entry_intents[0].key),
                "rel_offset": entry_off + field_start - name_end,
                "original": original.hex(),
                "patched": patched.hex(),
                "label": f"{entry_name}.visual_prefab_lists",
            }
        )
    return Format3DispatchResult(changes=tuple(changes), skipped=tuple(skipped))


def _build_legacy_visual_copy_change(
    context: Format3RuntimeContext,
    bounds: tuple[int, int, str, int],
    intents: list[Format3Intent],
) -> tuple[dict | None, str | None]:
    """从当前表中唯一使用目标 prefab hash 的记录复制合法 legacy visual 块。"""
    prefab_intent = next((intent for intent in intents if intent.field == "prefab_data_list"), None)
    gimmick_intent = next(
        (intent for intent in intents if intent.field == "gimmick_visual_prefab_data_list"),
        None,
    )
    if prefab_intent is None or gimmick_intent is None:
        return None, "visual intent 未成对"
    prefab_hash = _first_prefab_hash(prefab_intent.new)
    if prefab_hash is None:
        return None, "prefab_data_list 缺少源 prefab hash"
    gimmick_hash = _first_prefab_hash(gimmick_intent.new)
    if gimmick_hash != prefab_hash:
        return None, "prefab 与 gimmick visual hash 不一致"

    target_off, target_end, target_name, target_name_end = bounds
    target_record = context.body[target_off:target_end]
    target_loc = _locate_legacy_prefab_data_list(target_record, prefab_intent.new)
    if target_loc is None:
        return None, "目标记录未找到 legacy visual 块"

    needle = struct.pack("<I", prefab_hash)
    candidates: list[bytes] = []
    for source_off, source_end, _source_name, _source_name_end in context.entry_bounds.values():
        if source_off == target_off:
            continue
        source_record = context.body[source_off:source_end]
        if needle not in source_record:
            continue
        source_loc = _locate_legacy_prefab_data_list(source_record, prefab_intent.new)
        if source_loc is None:
            continue
        source_block = source_record[source_loc[0]:source_loc[1]]
        if source_block.count(needle) >= 2:
            candidates.append(source_block)
    unique_candidates = list(dict.fromkeys(candidates))
    if len(unique_candidates) != 1:
        return None, f"legacy visual 源块不唯一：{len(unique_candidates)}"

    original = target_record[target_loc[0]:target_loc[1]]
    patched = unique_candidates[0]
    if original == patched:
        return None, "legacy visual 已是目标值"
    return (
        {
            "entry": target_name or str(prefab_intent.entry or prefab_intent.key),
            "rel_offset": target_off + target_loc[0] - target_name_end,
            "original": original.hex(),
            "patched": patched.hex(),
            "label": f"{target_name}.legacy_visual_copy",
        },
        None,
    )


def _build_legacy_prefab_list_change(
    entry_bytes: bytes,
    entry_off: int,
    entry_name: str,
    name_end: int,
    intent: Format3Intent,
) -> tuple[dict | None, str | None]:
    """生成 DMM V3 legacy prefab-list 的整段替换补丁。"""
    block = _locate_legacy_prefab_data_list(entry_bytes, intent.new)
    if block is None:
        return None, "未定位到 DMM V3 legacy prefab_data_list 尾部块"

    start, end = block
    # 以原版块实际尾部形态打包：1.18 元素尾部含 u32 unk，旧版没有。
    # 避免把新游戏字节错误地按旧 3 字节尾部生成，或反之。
    has_unk_tail = (
        _consume_legacy_prefab_data_list_with_tail(entry_bytes, start, unk_tail=True)
        is not None
    )
    patched = _pack_legacy_prefab_data_list(intent.new, include_unk_tail=has_unk_tail)
    if patched is None:
        return None, "prefab_data_list 不是 DMM V3 legacy 结构"

    original = entry_bytes[start:end]
    if original == patched:
        return None, "目标字节已是期望值"

    return {
        "entry": entry_name or str(intent.entry or intent.key),
        "rel_offset": entry_off + start - name_end,
        "original": original.hex(),
        "patched": patched.hex(),
        "label": f"{intent.entry or intent.key}.prefab_data_list",
    }, None


def _pack_legacy_prefab_data_list(value: object, *, include_unk_tail: bool = True) -> bytes | None:
    """按 DMM V3 / Equip Everything V6 的 legacy prefab 尾部结构打包。

    1.18（EXE 1.0.0.2443）起每个元素在 tribe_gender_list 后新增 u32
    unk 字段（恒为 0xEAC5E173），再跟 3 个尾字节。旧版没有该字段。
    include_unk_tail=True 时输出 1.18 新版 7 字节尾部；False 输出旧版
    3 字节尾部（保留给旧游戏版本兼容路径）。
    """
    if not isinstance(value, list):
        return None

    out = bytearray(struct.pack("<I", len(value)))
    for item in value:
        if not isinstance(item, dict):
            return None
        prefab_names = item.get("prefab_names") or []
        animation_paths = item.get("animation_path_list") or []
        equip_slots = item.get("equip_slot_list") or []
        tribe_genders = item.get("tribe_gender_list") or []
        craft_material = item.get("is_craft_material", 0)
        use_gimmick_prefab = item.get("use_gimmick_prefab", 0)
        prefab_data_type = item.get("prefab_data_type", 0)
        scale = _pack_legacy_scale(item.get("scale", (1.0, 1.0, 1.0)))
        if scale is None:
            return None
        if not _is_u32_list(prefab_names):
            return None
        if not _is_u32_list(animation_paths):
            return None
        if not _is_u16_list(equip_slots):
            return None
        if not _is_u32_list(tribe_genders):
            return None
        if not all(
            _is_u8(value)
            for value in (craft_material, use_gimmick_prefab, prefab_data_type)
        ):
            return None

        out += scale
        out += _pack_u32_array(prefab_names)
        out += _pack_u32_array(animation_paths)
        out += _pack_u16_array(equip_slots)
        out += _pack_u32_array(tribe_genders)
        if include_unk_tail:
            # 1.18 新增：tribe 后固定 u32 unk 标记。
            out += struct.pack("<I", LEGACY_PREFAB_UNK_MAGIC)
        # 三个尾字节均是实际字段，V8 中 prefab_data_type 大量使用值 3。
        out += struct.pack("<BBB", craft_material, use_gimmick_prefab, prefab_data_type)
    return bytes(out)


def _pack_legacy_scale(value: object) -> bytes | None:
    """校验并序列化 legacy PrefabData 的三个 f32 缩放值。"""
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    if not all(
        isinstance(item, (int, float))
        and not isinstance(item, bool)
        and math.isfinite(float(item))
        for item in value
    ):
        return None
    try:
        return struct.pack("<fff", *(float(item) for item in value))
    except (OverflowError, struct.error):
        return None


def _is_u8(value: object) -> bool:
    """判断值能否安全写入无符号单字节字段。"""
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= 0xFF
    )


def _locate_legacy_prefab_data_list(
    entry_bytes: bytes,
    new_value: object,
) -> tuple[int, int] | None:
    """在单条 ItemInfo 记录尾部定位 DMM V3 legacy prefab 块。"""
    first_hash = _first_prefab_hash(new_value)
    new_count = len(new_value) if isinstance(new_value, list) else 0
    scan_start = max(0, len(entry_bytes) - LEGACY_PREFAB_SCAN_TAIL_BYTES)
    strong_candidates: list[int] = []
    candidates: list[int] = []
    relaxed_strong_candidates: list[int] = []
    relaxed_candidates: list[int] = []
    limit = len(entry_bytes) - len(LEGACY_PREFAB_SCALE_NEEDLE) - 4
    scale_offset = entry_bytes.find(LEGACY_PREFAB_SCALE_NEEDLE, scan_start + 4)
    while scale_offset >= 0:
        offset = scale_offset - 4
        if offset > limit:
            break
        if offset < scan_start or not _looks_like_legacy_prefab_scale_start(entry_bytes, offset):
            scale_offset = entry_bytes.find(LEGACY_PREFAB_SCALE_NEEDLE, scale_offset + 1)
            continue
        if _has_legacy_prefab_strict_prefix(entry_bytes, offset):
            candidates.append(offset)
            if first_hash is not None and _candidate_first_prefab_hash_matches(
                entry_bytes,
                offset,
                first_hash,
            ):
                strong_candidates.append(offset)
        elif _is_relaxed_legacy_prefab_candidate(entry_bytes, offset, new_count):
            relaxed_candidates.append(offset)
            if first_hash is not None and _candidate_first_prefab_hash_matches(
                entry_bytes,
                offset,
                first_hash,
            ):
                relaxed_strong_candidates.append(offset)
        scale_offset = entry_bytes.find(LEGACY_PREFAB_SCALE_NEEDLE, scale_offset + 1)

    if len(strong_candidates) == 1:
        return _legacy_prefab_bounds(entry_bytes, strong_candidates[0])
    if not strong_candidates and len(candidates) == 1:
        return _legacy_prefab_bounds(entry_bytes, candidates[0])
    if candidates:
        return None
    if len(relaxed_strong_candidates) == 1:
        return _legacy_prefab_bounds(entry_bytes, relaxed_strong_candidates[0])
    if not relaxed_strong_candidates and len(relaxed_candidates) == 1:
        return _legacy_prefab_bounds(entry_bytes, relaxed_candidates[0])
    return None


def _legacy_prefab_bounds(entry_bytes: bytes, offset: int) -> tuple[int, int] | None:
    """按 legacy element 结构计算 prefab_data_list 精确边界。"""
    end = _consume_legacy_prefab_data_list(entry_bytes, offset)
    if end is None or end > len(entry_bytes) - 2:
        return None
    return offset, end


def _consume_legacy_prefab_data_list(
    entry_bytes: bytes,
    offset: int,
    *,
    prefer_unk_tail: bool = True,
) -> int | None:
    """消费 DMM V3 legacy prefab_data_list，返回字段结束偏移。

    1.18 起每个元素在 tribe_gender_list 后多一个 u32 unk 字段（值
    0xEAC5E173），尾部从 3 字节变为 7 字节。为兼容旧版（1.17 及以前）
    的 3 字节尾部，先按新版 7 字节消费；若失败再回退旧版 3 字节。
    """
    end = _consume_legacy_prefab_data_list_with_tail(entry_bytes, offset, unk_tail=True)
    if end is not None:
        return end
    if prefer_unk_tail:
        return _consume_legacy_prefab_data_list_with_tail(entry_bytes, offset, unk_tail=False)
    return None


def _consume_legacy_prefab_data_list_with_tail(
    entry_bytes: bytes,
    offset: int,
    *,
    unk_tail: bool,
) -> int | None:
    """按指定尾部形态消费 legacy prefab_data_list。"""
    if offset + 4 > len(entry_bytes):
        return None
    count = struct.unpack_from("<I", entry_bytes, offset)[0]
    if count == 0 or count > MAX_PREFAB_SCAN_COUNT:
        return None
    pos = offset + 4
    for index in range(count):
        if pos + len(LEGACY_PREFAB_SCALE_NEEDLE) > len(entry_bytes):
            return None
        if index == 0 and entry_bytes[pos:pos + len(LEGACY_PREFAB_SCALE_NEEDLE)] != LEGACY_PREFAB_SCALE_NEEDLE:
            return None
        pos += len(LEGACY_PREFAB_SCALE_NEEDLE)
        pos = _consume_legacy_u32_array(entry_bytes, pos)
        if pos is None:
            return None
        pos = _consume_legacy_u32_array(entry_bytes, pos)
        if pos is None:
            return None
        pos = _consume_legacy_u16_array(entry_bytes, pos)
        if pos is None:
            return None
        pos = _consume_legacy_u32_array(entry_bytes, pos)
        if pos is None:
            return None
        if unk_tail:
            if pos + 4 > len(entry_bytes):
                return None
            unk = struct.unpack_from("<I", entry_bytes, pos)[0]
            if unk != LEGACY_PREFAB_UNK_MAGIC:
                return None
            pos += 4
        # is_craft_material、use_gimmick_prefab、prefab_data_type。
        if pos + 3 > len(entry_bytes):
            return None
        pos += 3
    return pos


def _consume_legacy_u32_array(entry_bytes: bytes, offset: int) -> int | None:
    """消费 legacy CArray<u32>。"""
    if offset + 4 > len(entry_bytes):
        return None
    count = struct.unpack_from("<I", entry_bytes, offset)[0]
    if count > MAX_PREFAB_SCAN_COUNT:
        return None
    end = offset + 4 + count * 4
    return end if end <= len(entry_bytes) else None


def _consume_legacy_u16_array(entry_bytes: bytes, offset: int) -> int | None:
    """消费 legacy CArray<u16>。"""
    if offset + 4 > len(entry_bytes):
        return None
    count = struct.unpack_from("<I", entry_bytes, offset)[0]
    if count > MAX_PREFAB_SCAN_COUNT:
        return None
    end = offset + 4 + count * 2
    return end if end <= len(entry_bytes) else None


def _looks_like_legacy_prefab_scale_start(entry_bytes: bytes, offset: int) -> bool:
    """判断 offset 是否具备 legacy prefab-list 的 count + scale 起点形态。"""
    if offset < 4 or offset + 16 > len(entry_bytes):
        return False
    count = struct.unpack_from("<I", entry_bytes, offset)[0]
    if count == 0 or count > MAX_PREFAB_SCAN_COUNT:
        return False
    return entry_bytes[offset + 4:offset + 16] == LEGACY_PREFAB_SCALE_NEEDLE


def _has_legacy_prefab_strict_prefix(entry_bytes: bytes, offset: int) -> bool:
    """判断 legacy prefab-list 起点前是否带常见 ff/zero 尾部标记。"""
    prefix = entry_bytes[max(0, offset - 16):offset]
    return entry_bytes[offset - 4:offset] == b"\x00\x00\x00\x00" and b"\xFF\xFF" in prefix


def _is_relaxed_legacy_prefab_candidate(
    entry_bytes: bytes,
    offset: int,
    new_count: int,
) -> bool:
    """处理少数没有 ff 前缀、但仍是唯一尾部 prefab 块的记录。"""
    if new_count <= 0:
        return False
    count = struct.unpack_from("<I", entry_bytes, offset)[0]
    if count > new_count + 5:
        return False
    # 这批特殊记录的 prefab 块前仍紧邻全零字段，内部误命中通常没有该特征。
    return entry_bytes[offset - 4:offset] == b"\x00\x00\x00\x00"


def _candidate_first_prefab_hash_matches(
    entry_bytes: bytes,
    offset: int,
    first_hash: int,
) -> bool:
    """校验候选块第一个 prefab hash 是否与新值一致。"""
    prefab_count_offset = offset + 4 + len(LEGACY_PREFAB_SCALE_NEEDLE)
    if prefab_count_offset + 8 > len(entry_bytes):
        return False
    prefab_count = struct.unpack_from("<I", entry_bytes, prefab_count_offset)[0]
    if prefab_count == 0 or prefab_count > MAX_PREFAB_SCAN_COUNT:
        return False
    current_hash = struct.unpack_from("<I", entry_bytes, prefab_count_offset + 4)[0]
    return current_hash == first_hash


def _first_prefab_hash(value: object) -> int | None:
    """从 Format 3 prefab_data_list 新值中取第一个 prefab hash。"""
    if not isinstance(value, list):
        return None
    for item in value:
        if not isinstance(item, dict):
            continue
        prefab_names = item.get("prefab_names")
        if not isinstance(prefab_names, list) or not prefab_names:
            continue
        first = prefab_names[0]
        if isinstance(first, int) and not isinstance(first, bool) and 0 <= first <= 0xFFFFFFFF:
            return first
    return None


def _pack_u32_array(values: list[int]) -> bytes:
    """打包 CArray<u32>。"""
    return struct.pack("<I", len(values)) + b"".join(struct.pack("<I", item) for item in values)


def _pack_u16_array(values: list[int]) -> bytes:
    """打包 CArray<u16>。"""
    return struct.pack("<I", len(values)) + b"".join(struct.pack("<H", item) for item in values)


def _build_single_change_with_reason(
    body: bytes,
    key_size: int,
    entry_bounds: dict[int, tuple[int, int, str, int]],
    intent: dict[str, Any],
) -> tuple[dict | None, str | None]:
    """定位单条 intent，并在失败时返回明确原因。"""
    if intent.get("op", "set") != "set":
        return None, "仅支持 op=set"
    field = intent.get("field")
    if not isinstance(field, str):
        return None, "field 不是字符串"
    prefab_match = PREFAB_TRIBE_GENDER_FIELD_RE.match(field)
    drop_default_match = DROP_DEFAULT_FIELD_RE.match(field)
    if prefab_match is None and drop_default_match is None:
        return None, ITEMINFO_SUPPORTED_FIELD_REASON
    key = intent.get("key")
    if isinstance(key, bool) or not isinstance(key, int):
        return None, "key 不是整数"
    bounds, resolve_reason = _resolve_entry_bounds(entry_bounds, intent)
    if bounds is None:
        return None, resolve_reason or "目标 entry 未命中"

    entry_off, entry_end, entry_name, name_end = bounds
    payload_off = _payload_offset(body, entry_off, key_size)
    if payload_off is None:
        return None, "entry payload 定位失败"
    if prefab_match is not None:
        values = intent.get("new")
        if not _is_u32_list(values):
            return None, "new 不是 u32 数组"
        return _build_prefab_change(
            body,
            entry_end,
            entry_name,
            name_end,
            payload_off,
            prefab_match,
            values,
            intent,
        )

    if drop_default_match is not None:
        return _build_drop_default_change(
            body,
            entry_end,
            entry_name,
            name_end,
            payload_off,
            drop_default_match.group("field"),
            intent,
        )
    return None, ITEMINFO_SUPPORTED_FIELD_REASON


def _build_prefab_change(
    body: bytes,
    entry_end: int,
    entry_name: str,
    name_end: int,
    payload_off: int,
    prefab_match: re.Match[str],
    values: list[int],
    intent: dict[str, Any],
) -> tuple[dict | None, str | None]:
    """生成 prefab_data_list[N].tribe_gender_list 的 byte patch。"""
    prefab_list_off = _walk_fields(body, payload_off, entry_end, ITEMINFO_FIELDS_BEFORE_PREFAB)
    prefab_index = int(prefab_match.group("index"))
    tribe_range = None
    if prefab_list_off is not None:
        tribe_range = _locate_prefab_tribe_gender_list(
            body,
            prefab_list_off,
            entry_end,
            prefab_index,
        )
    if tribe_range is None:
        # 当前游戏版本的 ItemInfo 前置字段会随版本/逆向 schema 漂移。
        # 对这个真实 Format 3 需求，目标只是 entry 内唯一的
        # CArray<PrefabData>.tribe_gender_list；walker 失败时用唯一候选扫描兜底。
        tribe_range = _scan_unique_prefab_tribe_gender_list(
            body,
            payload_off,
            entry_end,
            prefab_index,
            values,
        )
    if tribe_range is None:
        return None, "prefab_data_list 目标字段定位失败"

    start, end = tribe_range
    patched = struct.pack("<I", len(values)) + b"".join(struct.pack("<I", item) for item in values)
    original = body[start:end]
    if original == patched:
        return None, "目标字节已是期望值"
    field = intent.get("field")
    key = intent.get("key")
    return {
        "entry": entry_name or str(intent.get("entry") or ""),
        "rel_offset": start - name_end,
        "original": original.hex(),
        "patched": patched.hex(),
        "label": f"{intent.get('entry', key)}.{field}",
    }, None


def _build_drop_default_change(
    body: bytes,
    entry_end: int,
    entry_name: str,
    name_end: int,
    payload_off: int,
    field_name: str,
    intent: dict[str, Any],
) -> tuple[dict | None, str | None]:
    """生成 drop_default_data 原始字段的 entry+rel_offset patch。"""
    drop_default_off = _walk_fields(
        body,
        payload_off,
        entry_end,
        ITEMINFO_FIELDS_BEFORE_DROP_DEFAULT,
    )
    if drop_default_off is None:
        return None, "drop_default_data 起点定位失败"

    prefix_fields, target_descriptor = DROP_DEFAULT_FIELD_LAYOUTS[field_name]
    target_off = drop_default_off
    if prefix_fields:
        target_off = _walk_fields(body, drop_default_off, entry_end, prefix_fields)
        if target_off is None:
            return None, f"{field_name} 前置字段定位失败"

    width = _consume_bytes(target_descriptor, body, target_off, entry_end)
    if width is None:
        return None, f"{field_name} 长度解析失败"
    patched = _pack_descriptor_value(intent.get("new"), target_descriptor)
    if patched is None:
        return None, f"{field_name} 的 new 值类型不合法"

    original = body[target_off:target_off + width]
    if len(original) != width:
        return None, f"{field_name} 原始字节范围越界"
    if original == patched:
        return None, "目标字节已是期望值"
    return {
        "entry": entry_name or str(intent.get("entry") or ""),
        "rel_offset": target_off - name_end,
        "original": original.hex(),
        "patched": patched.hex(),
        "label": f"{intent.get('entry', intent.get('key'))}.drop_default_data.{field_name}",
    }, None


def _build_drop_default_record_fallback_result(
    context: Format3RuntimeContext,
    intents: list[Format3Intent],
) -> Format3DispatchResult:
    """用单条 ItemInfo 记录 roundtrip 处理当前游戏布局下的 drop_default_data。"""
    grouped: dict[int, list[Format3Intent]] = {}
    skipped: list[Format3SkippedIntent] = []
    for intent in intents:
        bounds, resolve_reason = _resolve_entry_bounds(context.entry_bounds, intent.to_legacy_dict())
        if bounds is None:
            skipped.append(Format3SkippedIntent(intent=intent, reason=resolve_reason or "目标 entry 未命中"))
            continue
        grouped.setdefault(bounds[0], []).append(intent)

    changes: list[dict] = []
    bounds_by_offset = {
        bounds[0]: bounds
        for bounds in context.entry_bounds.values()
    }
    for entry_off, entry_intents in grouped.items():
        bounds = bounds_by_offset[entry_off]
        entry_change, entry_skipped = _build_single_record_drop_default_change(
            context.body,
            bounds,
            entry_intents,
        )
        skipped.extend(entry_skipped)
        if entry_change is not None:
            changes.append(entry_change)

    return Format3DispatchResult(changes=tuple(changes), skipped=tuple(skipped))


def _build_single_record_drop_default_change(
    body: bytes,
    bounds: tuple[int, int, str, int],
    intents: list[Format3Intent],
) -> tuple[dict | None, list[Format3SkippedIntent]]:
    """对同一 ItemInfo entry 合并多个 drop_default_data intent。"""
    entry_off, entry_end, entry_name, name_end = bounds
    entry_bytes = body[entry_off:entry_end]
    try:
        drop_default, field_start, field_end = parse_iteminfo_drop_default_data(entry_bytes)
        identity = serialize_iteminfo_drop_default_data(drop_default)
    except Exception as exc:
        return None, [
            Format3SkippedIntent(intent=intent, reason=f"iteminfo drop_default_data 定位失败：{exc}")
            for intent in intents
        ]
    original = entry_bytes[field_start:field_end]
    if identity != original:
        return None, [
            Format3SkippedIntent(intent=intent, reason="iteminfo drop_default_data identity roundtrip 不一致")
            for intent in intents
        ]

    skipped: list[Format3SkippedIntent] = []
    applied = 0
    for intent in intents:
        match = DROP_DEFAULT_FIELD_RE.match(intent.field)
        if match is None:
            skipped.append(Format3SkippedIntent(intent=intent, reason=ITEMINFO_SUPPORTED_FIELD_REASON))
            continue
        if intent.op != "set":
            skipped.append(Format3SkippedIntent(intent=intent, reason="仅支持 op=set"))
            continue
        field_name = match.group("field")
        changed, reason = _apply_drop_default_record_value(
            drop_default,
            field_name,
            intent.new,
        )
        if reason is not None:
            skipped.append(Format3SkippedIntent(intent=intent, reason=reason))
            continue
        if not changed:
            skipped.append(Format3SkippedIntent(intent=intent, reason="目标字节已是期望值"))
            continue
        applied += 1

    if applied == 0:
        return None, skipped

    try:
        patched = serialize_iteminfo_drop_default_data(drop_default)
    except Exception as exc:
        skipped.extend(
            Format3SkippedIntent(intent=intent, reason=f"iteminfo drop_default_data 序列化失败：{exc}")
            for intent in intents
        )
        return None, skipped
    if patched == original:
        return None, skipped

    first_intent = intents[0]
    return {
        "entry": entry_name or str(first_intent.entry or first_intent.key),
        "rel_offset": entry_off + field_start - name_end,
        "original": original.hex(),
        "patched": patched.hex(),
        "label": f"{entry_name or first_intent.key}.drop_default_data ({applied} applied)",
        "_dynamic_entry_offset": True,
    }, skipped


def _apply_drop_default_record_value(
    drop_default: dict,
    field_name: str,
    new_value: object,
) -> tuple[bool, str | None]:
    """校验并写入 drop_default_data 的单记录值。"""
    descriptor = DROP_DEFAULT_FIELD_LAYOUTS[field_name][1]
    if _pack_descriptor_value(new_value, descriptor) is None:
        return False, f"{field_name} 的 new 值类型不合法"
    if field_name in {"socket_valid_count", "use_socket"}:
        return _apply_socket_flag_value(drop_default, field_name, new_value)

    existing = drop_default.get(field_name)
    if field_name == "add_socket_material_item_list" and isinstance(new_value, list):
        coerced = [dict(item) for item in new_value]
    elif field_name == "socket_item_list" and isinstance(new_value, list):
        coerced = list(new_value)
    elif field_name == "default_sub_item" and isinstance(new_value, dict):
        coerced = dict(new_value)
    else:
        coerced = new_value
    if existing == coerced:
        return False, None
    drop_default[field_name] = coerced
    return True, None


def _apply_socket_flag_value(
    drop_default: dict,
    field_name: str,
    new_value: object,
) -> tuple[bool, str | None]:
    """按 DMM 当前布局写入 socket 有效数/启用标记。

    部分 ItemInfo 记录的 `default_sub_item` 使用非 14 的 tagged variant。
    DMM 1.4.9.1 会把 FiveSockets 的 5/1 写入该 variant value 的低两个
    字节，而不是写到 DropDefaultData 末尾两个 u8。照这个布局输出后，
    游戏能正常读取并启用额外孔位。
    """
    if isinstance(new_value, bool) or not isinstance(new_value, int):
        return False, f"{field_name} 的 new 值类型不合法"

    sub_item = drop_default.get("default_sub_item")
    if (
        isinstance(sub_item, dict)
        and sub_item.get("type_id") != 14
        and isinstance(sub_item.get("value"), int)
        and not isinstance(sub_item.get("value"), bool)
    ):
        byte_index = 0 if field_name == "socket_valid_count" else 1
        value_bytes = bytearray(struct.pack("<I", sub_item["value"] & 0xFFFFFFFF))
        if value_bytes[byte_index] == new_value:
            return False, None
        value_bytes[byte_index] = new_value
        sub_item["value"] = struct.unpack("<I", value_bytes)[0]
        return True, None

    existing = drop_default.get(field_name)
    if existing == new_value:
        return False, None
    drop_default[field_name] = new_value
    return True, None


def _resolve_entry_bounds(
    entry_bounds: dict[int, tuple[int, int, str, int]],
    intent: dict[str, Any],
) -> tuple[tuple[int, int, str, int] | None, str | None]:
    """优先按稳定 key 命中，缺省或未命中时回退到 entry 名称。"""
    key = intent.get("key")
    if isinstance(key, int) and not isinstance(key, bool):
        bounds = entry_bounds.get(key)
        if bounds is not None:
            return bounds, None

    entry_name = intent.get("entry")
    if not isinstance(entry_name, str) or not entry_name:
        return None, "目标 entry key 未命中"

    matches = [
        bounds
        for bounds in entry_bounds.values()
        if bounds[2] == entry_name
    ]
    if len(matches) == 1:
        return matches[0], None
    if not matches:
        return None, "目标 entry key/名称 都未命中"
    return None, "目标 entry 名称命中多个记录，存在歧义"


def _payload_offset(body: bytes, entry_off: int, key_size: int) -> int | None:
    """ItemInfo 的 payload 从 name_end 开始，不跳过疑似 null 的首字段。"""
    eid_size = 2 if key_size == 2 else 4
    head_size = eid_size + 4
    if entry_off < 0 or entry_off + head_size > len(body):
        return None
    name_len = struct.unpack_from("<I", body, entry_off + eid_size)[0]
    if name_len > 500 or entry_off + head_size + name_len > len(body):
        return None
    return entry_off + head_size + name_len


def _walk_fields(
    body: bytes,
    start: int,
    end: int,
    fields: tuple[str, ...],
) -> int | None:
    """按 ItemInfo 已知字段顺序消费字节，返回目标字段起点。"""
    cursor = start
    for descriptor in fields:
        consumed = _consume_bytes(descriptor, body, cursor, end)
        if consumed is None:
            return None
        cursor += consumed
    return cursor if cursor <= end else None


def _locate_prefab_tribe_gender_list(
    body: bytes,
    prefab_list_off: int,
    entry_end: int,
    prefab_index: int,
) -> tuple[int, int] | None:
    """定位 CArray<PrefabData> 中第 N 个元素的 tribe_gender_list 字节范围。"""
    if prefab_list_off + 4 > entry_end:
        return None
    count = struct.unpack_from("<I", body, prefab_list_off)[0]
    if count > MAX_REASONABLE_ARRAY_COUNT or prefab_index >= count:
        return None
    cursor = prefab_list_off + 4
    for index in range(count):
        consumed = _consume_bytes("CArray<u32>", body, cursor, entry_end)
        if consumed is None:
            return None
        cursor += consumed
        consumed = _consume_bytes("CArray<u16>", body, cursor, entry_end)
        if consumed is None:
            return None
        cursor += consumed
        tribe_start = cursor
        consumed = _consume_bytes("CArray<u32>", body, cursor, entry_end)
        if consumed is None:
            return None
        cursor += consumed
        if index == prefab_index:
            return tribe_start, cursor
        consumed = _consume_bytes("u8", body, cursor, entry_end)
        if consumed is None:
            return None
        cursor += consumed
    return None


def _scan_unique_prefab_tribe_gender_list(
    body: bytes,
    payload_off: int,
    entry_end: int,
    prefab_index: int,
    new_values: list[int],
) -> tuple[int, int] | None:
    """在单个 ItemInfo entry 内扫描唯一的 PrefabData 数组候选。"""
    matches: list[tuple[int, int]] = []
    new_set = set(new_values)
    for candidate in range(payload_off, max(payload_off, entry_end - 4)):
        parsed = _parse_prefab_array_candidate(body, candidate, entry_end, prefab_index)
        if parsed is not None:
            current_values = _read_u32_array_values(body[parsed[0]:parsed[1]])
            if current_values and set(current_values).issubset(new_set):
                matches.append(parsed)
            if len(matches) > 1:
                return None
    return matches[0] if len(matches) == 1 else None


def _parse_prefab_array_candidate(
    body: bytes,
    candidate_off: int,
    entry_end: int,
    prefab_index: int,
) -> tuple[int, int] | None:
    """尝试把 candidate_off 解析成 CArray<PrefabData> 并返回目标字段范围。"""
    if candidate_off + 4 > entry_end:
        return None
    count = struct.unpack_from("<I", body, candidate_off)[0]
    if count <= prefab_index or count > MAX_PREFAB_SCAN_COUNT:
        return None
    cursor = candidate_off + 4
    target_range: tuple[int, int] | None = None
    for index in range(count):
        consumed = _consume_bytes("CArray<u32>", body, cursor, entry_end)
        if consumed is None:
            return None
        cursor += consumed
        consumed = _consume_bytes("CArray<u16>", body, cursor, entry_end)
        if consumed is None:
            return None
        cursor += consumed
        tribe_start = cursor
        consumed = _consume_bytes("CArray<u32>", body, cursor, entry_end)
        if consumed is None:
            return None
        cursor += consumed
        if index == prefab_index:
            target_range = (tribe_start, cursor)
        consumed = _consume_bytes("u8", body, cursor, entry_end)
        if consumed is None:
            return None
        cursor += consumed
    return target_range


def _read_u32_array_values(raw: bytes) -> list[int] | None:
    """读取一段完整 CArray<u32> 的值，用于扫描候选过滤。"""
    if len(raw) < 4:
        return None
    count = struct.unpack_from("<I", raw, 0)[0]
    if 4 + count * 4 != len(raw):
        return None
    return [struct.unpack_from("<I", raw, 4 + index * 4)[0] for index in range(count)]


def _consume_bytes(descriptor: str, body: bytes, off: int, end: int) -> int | None:
    """消费 PABGB 类型描述对应的字节数。"""
    if off < 0:
        return None
    limit = min(end, len(body))
    primitive = PRIMITIVE_WIDTHS.get(descriptor)
    if primitive is not None:
        return primitive if off + primitive <= limit else None
    if descriptor == "CString":
        if off + 4 > limit:
            return None
        length = struct.unpack_from("<I", body, off)[0]
        if length > 10_000_000 or off + 4 + length > limit:
            return None
        return 4 + length
    if descriptor == "LocalizableString":
        # Crimson Desert 的 LocalizableString 常见布局为
        # u8 flag + u64 hash + CString。部分表也可能存在短布局，
        # 所以保留多形态兼容，但真实 ItemInfo 必须优先按长布局。
        for prefix_size in (1 + 8, 4, 1):
            consumed = _consume_localizable_string(body, off, limit, prefix_size=prefix_size)
            if consumed is not None:
                return consumed
        return None
    if descriptor.startswith("CArray<") and descriptor.endswith(">"):
        inner = descriptor[len("CArray<"):-1]
        if off + 4 > limit:
            return None
        count = struct.unpack_from("<I", body, off)[0]
        if count > MAX_REASONABLE_ARRAY_COUNT:
            return None
        cursor = off + 4
        for _ in range(count):
            consumed = _consume_bytes(inner, body, cursor, end)
            if consumed is None:
                return None
            cursor += consumed
        return cursor - off
    substruct = SUBSTRUCT_DEFS.get(descriptor)
    if substruct is not None:
        cursor = off
        for _name, field_type in substruct:
            consumed = _consume_bytes(field_type, body, cursor, end)
            if consumed is None:
                return None
            cursor += consumed
        return cursor - off
    if descriptor in TAGGED_VARIANTS:
        return _consume_tagged_variant(
            body,
            off,
            end,
            descriptor,
            fixed_prefix=TAGGED_FIXED_PREFIX.get(descriptor, ()),
        )
    return None


def _consume_tagged_variant(
    body: bytes,
    off: int,
    end: int,
    name: str,
    fixed_prefix: tuple[str, ...],
) -> int | None:
    """消费带 u8 discriminator 的简单 tagged variant。"""
    if off + 1 > min(end, len(body)):
        return None
    cursor = off + 1
    for descriptor in fixed_prefix:
        consumed = _consume_bytes(descriptor, body, cursor, end)
        if consumed is None:
            return None
        cursor += consumed
    payload = TAGGED_VARIANTS[name].get(body[off])
    if payload is None:
        return None
    if not payload:
        return cursor - off
    consumed = _consume_bytes(payload, body, cursor, end)
    if consumed is None:
        return None
    return cursor + consumed - off


def _consume_localizable_string(
    body: bytes,
    off: int,
    limit: int,
    prefix_size: int,
) -> int | None:
    """消费 LocalizableString 的一种已知布局。"""
    if off + prefix_size + 4 > limit:
        return None
    length = struct.unpack_from("<I", body, off + prefix_size)[0]
    if length > 10_000_000 or off + prefix_size + 4 + length > limit:
        return None
    return prefix_size + 4 + length


def _is_u32_list(value: object) -> bool:
    """判断新值是否为可写入 CArray<u32> 的列表。"""
    return isinstance(value, list) and all(
        isinstance(item, int) and not isinstance(item, bool) and 0 <= item <= 0xFFFFFFFF
        for item in value
    )


def _is_u16_list(value: object) -> bool:
    """判断新值是否为可写入 CArray<u16> 的列表。"""
    return isinstance(value, list) and all(
        isinstance(item, int) and not isinstance(item, bool) and 0 <= item <= 0xFFFF
        for item in value
    )


def _pack_descriptor_value(value: object, descriptor: str) -> bytes | None:
    """把支持的 Format 3 新值打包成目标描述对应的字节。"""
    if descriptor in {"u8", "u16"}:
        return _pack_unsigned_primitive(value, descriptor)
    if descriptor == "CArray<u32>":
        if not _is_u32_list(value):
            return None
        return struct.pack("<I", len(value)) + b"".join(struct.pack("<I", item) for item in value)
    if descriptor == "CArray<SocketMaterialItem>":
        return _pack_socket_material_item_array(value)
    if descriptor == "SubItem":
        return _pack_sub_item(value)
    return None


def _pack_unsigned_primitive(value: object, descriptor: str) -> bytes | None:
    """把 primitive 新值打包成固定宽度字节。"""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if descriptor == "u8" and 0 <= value <= 0xFF:
        return struct.pack("<B", value)
    if descriptor == "u16" and 0 <= value <= 0xFFFF:
        return struct.pack("<H", value)
    return None


def _pack_socket_material_item_array(value: object) -> bytes | None:
    """把 add_socket_material_item_list 打包为 CArray<SocketMaterialItem>。"""
    if not isinstance(value, list):
        return None

    packed_items: list[bytes] = []
    for item in value:
        if not isinstance(item, dict):
            return None
        raw_item = item.get("item")
        raw_value = item.get("value")
        if (
            isinstance(raw_item, bool)
            or not isinstance(raw_item, int)
            or raw_item < 0
            or raw_item > 0xFFFFFFFF
        ):
            return None
        if (
            isinstance(raw_value, bool)
            or not isinstance(raw_value, int)
            or raw_value < 0
            or raw_value > 0xFFFFFFFFFFFFFFFF
        ):
            return None
        packed_items.append(struct.pack("<I", raw_item) + struct.pack("<Q", raw_value))

    return struct.pack("<I", len(packed_items)) + b"".join(packed_items)


def _pack_sub_item(value: object) -> bytes | None:
    """把 drop_default_data.default_sub_item 打包为 SubItem tagged variant。"""
    if not isinstance(value, dict):
        return None

    raw_type_id = value.get("type_id")
    if isinstance(raw_type_id, bool) or not isinstance(raw_type_id, int):
        return None
    if raw_type_id == 14:
        return struct.pack("<B", raw_type_id)
    if raw_type_id not in {0, 3, 9}:
        return None

    raw_sub_value = value.get("value")
    if (
        isinstance(raw_sub_value, bool)
        or not isinstance(raw_sub_value, int)
        or raw_sub_value < 0
        or raw_sub_value > 0xFFFFFFFF
    ):
        return None
    return struct.pack("<B", raw_type_id) + struct.pack("<I", raw_sub_value)
