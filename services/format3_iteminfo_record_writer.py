"""ItemInfo Format 3 单记录 writer。

部分武器特效类 Field JSON 只修改同一条 ItemInfo 记录里的少量字段：
冷却、被动技能、gimmick、docking 子物件、充能次数和 respawn。当前
whole-table parser 对少数武器记录还不能完整解码，且冷构建代价较高；
本模块按 `.pabgh` 已知边界切出单条 record，在 record 内用稳定字段锚点
做最窄写入，最后交给主链路整体替换该 record 并刷新 companion `.pabgh`。
"""

from __future__ import annotations

import struct
from typing import Any

from cdmm.services.format3_parser import Format3Intent
from cdmm.services.format3_runtime import (
    Format3DispatchResult,
    Format3RuntimeContext,
    Format3SkippedIntent,
)
from cdmm.services.iteminfo_native_parser import (
    LANTERN_EQ_TYPE,
    _ITEM_FIELDS,
    _Reader,
    _read_ItemInfoSharpnessData,
)

# 本 writer 只声明 DMM 已验证的武器特效字段，避免误处理未知 ItemInfo 结构。
ITEMINFO_RECORD_DIRECT_FIELDS = frozenset(
    {
        "cooltime",
        "unk_post_cooltime_a",
        "unk_post_cooltime_b",
        "docking_child_data",
        "docking_child_data.gimmick_info_key",
        "equip_passive_skill_list",
        "gimmick_info",
        "item_charge_type",
        "max_charged_useable_count",
        "unk_post_max_charged_a",
        "unk_post_max_charged_b",
        "respawn_time_seconds",
    }
)

_DEFAULT_TO_DOCKING_FLAG_BACKTRACK = 34


def should_use_iteminfo_record_writer(intents: list[Format3Intent]) -> bool:
    """判断一批 iteminfo intents 是否可由单记录 writer 处理。"""
    return bool(intents) and all(intent.field in ITEMINFO_RECORD_DIRECT_FIELDS for intent in intents)


def build_iteminfo_record_result(
    context: Format3RuntimeContext,
    intents: list[Format3Intent],
) -> Format3DispatchResult:
    """把 Field JSON intents 合并为单条 record 的整体替换补丁。"""
    grouped: dict[int, list[Format3Intent]] = {}
    skipped: list[Format3SkippedIntent] = []
    for intent in intents:
        bounds, reason = _resolve_bounds(context.entry_bounds, intent)
        if bounds is None:
            skipped.append(_skip(intent, reason or "目标 entry key/名称 都未命中"))
            continue
        grouped.setdefault(bounds[0], []).append(intent)

    changes: list[dict] = []
    bounds_by_start = {bounds[0]: bounds for bounds in context.entry_bounds.values()}
    for entry_start, entry_intents in grouped.items():
        bounds = bounds_by_start[entry_start]
        change, entry_skipped = _build_record_change(context.body, bounds, entry_intents)
        skipped.extend(entry_skipped)
        if change is not None:
            changes.append(change)

    return Format3DispatchResult(changes=tuple(changes), skipped=tuple(skipped))


def _build_record_change(
    body: bytes,
    bounds: tuple[int, int, str, int],
    intents: list[Format3Intent],
) -> tuple[dict | None, list[Format3SkippedIntent]]:
    """对同一条 ItemInfo record 合并写入多个字段。"""
    entry_start, entry_end, entry_name, _name_end = bounds
    original = body[entry_start:entry_end]
    record = bytearray(original)
    skipped: list[Format3SkippedIntent] = []
    applied = 0

    for intent in intents:
        if intent.op != "set":
            skipped.append(_skip(intent, "iteminfo 单记录 writer 当前仅支持 op=set"))
            continue
        changed, reason = _apply_record_intent(record, intent)
        if reason is not None:
            skipped.append(_skip(intent, reason))
            continue
        if not changed:
            skipped.append(_skip(intent, "目标字节已是期望值"))
            continue
        applied += 1

    patched = bytes(record)
    if applied == 0 or patched == original:
        return None, skipped

    first_intent = intents[0]
    return {
        "offset": entry_start,
        "original": original.hex(),
        "patched": patched.hex(),
        "label": f"{entry_name or first_intent.entry or first_intent.key}.iteminfo-record ({applied} applied)",
    }, skipped


def _apply_record_intent(record: bytearray, intent: Format3Intent) -> tuple[bool, str | None]:
    """写入单个已支持字段，返回 `(是否改变, 跳过原因)`。"""
    field = intent.field
    if field == "equip_passive_skill_list":
        offset = _locate_schema_field(record, "equip_passive_skill_list")
        if offset is None:
            return False, "equip_passive_skill_list 定位失败"
        old_end = _consume_passive_skill_list(record, offset)
        if old_end is None:
            return False, "equip_passive_skill_list 原始结构解析失败"
        packed = _pack_passive_skill_list(intent.new)
        if packed is None:
            return False, "equip_passive_skill_list 的 new 值类型不合法"
        return _replace_range(record, offset, old_end, packed), None

    if field == "gimmick_info":
        offset = _locate_schema_field(record, "gimmick_info")
        packed = _pack_u32(intent.new)
        if offset is None:
            return False, "gimmick_info 定位失败"
        if packed is None:
            return False, "gimmick_info 的 new 值类型不合法"
        return _replace_fixed(record, offset, 4, packed), None

    layout = _locate_tail_layout(record)
    if layout is None:
        return False, "iteminfo 武器尾部布局定位失败"

    if field in {"cooltime", "unk_post_cooltime_a", "unk_post_cooltime_b"}:
        index = ("cooltime", "unk_post_cooltime_a", "unk_post_cooltime_b").index(field)
        packed = _pack_i64(intent.new)
        if packed is None:
            return False, f"{field} 的 new 值类型不合法"
        return _replace_fixed(record, layout["cooltime"] + index * 8, 8, packed), None

    if field == "item_charge_type":
        packed = _pack_u8(intent.new)
        if packed is None:
            return False, "item_charge_type 的 new 值类型不合法"
        return _replace_fixed(record, layout["item_charge_type"], 1, packed), None

    if field in {"max_charged_useable_count", "unk_post_max_charged_a", "unk_post_max_charged_b"}:
        index = (
            "max_charged_useable_count",
            "unk_post_max_charged_a",
            "unk_post_max_charged_b",
        ).index(field)
        packed = _pack_u32(intent.new)
        if packed is None:
            return False, f"{field} 的 new 值类型不合法"
        return _replace_fixed(record, layout["max_charged"] + index * 4, 4, packed), None

    if field == "respawn_time_seconds":
        # DMM 1.4.9.1 对该 Field JSON 写入的是 4 字节秒数；后续 0xffff
        # 片段属于相邻尾部字段，不能按旧 parser 的 i64 全覆盖。
        packed = _pack_u32(intent.new)
        if packed is None:
            return False, "respawn_time_seconds 的 new 值类型不合法"
        return _replace_fixed(record, layout["respawn_time_seconds"], 4, packed), None

    if field == "docking_child_data":
        packed = _pack_docking_child_optional(intent.new)
        if packed is None:
            return False, "docking_child_data 的 new 值类型不合法"
        offset = layout["docking_child_data"]
        if offset < 0 or offset + 4 > len(record):
            return False, "docking_child_data 定位越界"
        if record[offset] != 0:
            return False, "docking_child_data 已存在，当前窄 writer 暂不替换非空 optional"
        if bytes(record[offset:offset + 4]) != b"\x00\x00\x00\x00":
            return False, "docking_child_data 空 optional 占位不符合 DMM 写入形态"
        return _replace_range(record, offset, offset + 4, packed), None

    if field == "docking_child_data.gimmick_info_key":
        offset = _locate_existing_docking_child_data(record)
        if offset is None:
            return False, "docking_child_data.gimmick_info_key 定位失败"
        packed = _pack_u32(intent.new)
        if packed is None:
            return False, "docking_child_data.gimmick_info_key 的 new 值类型不合法"
        return _replace_fixed(record, offset + 1, 4, packed), None

    return False, "iteminfo 单记录 writer 不支持该字段"


def _locate_schema_field(record: bytes | bytearray, field_name: str) -> int | None:
    """用 native parser 的前段字段定义定位早期字段。"""
    reader = _Reader(bytes(record), 0, rec_end=len(record))
    parsed: dict[str, Any] = {}
    try:
        for spec in _ITEM_FIELDS:
            name = spec[0]
            if name == field_name:
                return reader.pos
            _consume_iteminfo_spec(reader, spec, parsed)
    except Exception:
        return None
    return None


def _consume_iteminfo_spec(reader: _Reader, spec: tuple, parsed: dict[str, Any]) -> None:
    """消费一个 ItemInfo 字段；只用于定位，不做完整语义校验。"""
    name, kind = spec[0], spec[1]
    if name == "item_desc" and parsed.get("equip_type_info") == LANTERN_EQ_TYPE:
        reader.u32()
        reader.u32()
        reader.u32()

    if kind == "u8":
        parsed[name] = reader.u8()
    elif kind == "u16":
        parsed[name] = reader.u16()
    elif kind == "u32":
        parsed[name] = reader.u32()
    elif kind == "u64":
        parsed[name] = reader.u64()
    elif kind == "i64":
        parsed[name] = reader.i64()
    elif kind == "f32":
        parsed[name] = reader.f32()
    elif kind == "cstring":
        parsed[name] = reader.cstring()
    elif kind == "localizable":
        parsed[name] = reader.localizable()
    elif kind == "carray_u32":
        parsed[name] = reader.carray(_Reader.u32)
    elif kind == "carray_u16":
        parsed[name] = reader.carray(_Reader.u16)
    elif kind == "carray_cstring":
        parsed[name] = reader.carray(_Reader.cstring)
    elif kind == "carray":
        parsed[name] = reader.carray(spec[2])
    elif kind == "struct":
        parsed[name] = spec[2](reader)
    elif kind == "optional":
        flag = reader.u8()
        parsed[name] = spec[2](reader) if flag else None
    else:
        raise ValueError(f"未知 ItemInfo 字段类型：{kind}")


def _locate_tail_layout(record: bytes | bytearray) -> dict[str, int] | None:
    """从武器记录尾部定位 default_sub_item 后的冷却、sharpness 和 max 字段。"""
    data = bytes(record)
    candidates: list[dict[str, int]] = []
    for default_offset in range(max(0, len(data) - 700), max(0, len(data) - 60)):
        type_id = data[default_offset]
        if type_id < 14:
            default_size = 18
        elif type_id < 32:
            default_size = 1
        else:
            continue

        cooltime = default_offset + default_size
        item_charge_type = cooltime + 24
        sharpness = item_charge_type + 1
        try:
            reader = _Reader(data, sharpness, rec_end=len(data))
            _read_ItemInfoSharpnessData(reader)
        except Exception:
            continue
        max_charged = reader.pos + 1
        if max_charged + 12 > len(data):
            continue
        max_values = [
            struct.unpack_from("<I", data, max_charged + index * 4)[0]
            for index in range(3)
        ]
        if not all(value <= 100 for value in max_values):
            continue
        if type_id < 14:
            continue
        respawn = _locate_respawn_from_tail(data, max_charged)
        if respawn is None:
            continue
        candidates.append(
            {
                "default_sub_item": default_offset,
                "cooltime": cooltime,
                "item_charge_type": item_charge_type,
                "max_charged": max_charged,
                "respawn_time_seconds": respawn,
                "docking_child_data": default_offset - _DEFAULT_TO_DOCKING_FLAG_BACKTRACK,
            }
        )

    non_zero_max = [
        item
        for item in candidates
        if any(struct.unpack_from("<I", data, item["max_charged"] + index * 4)[0] > 0 for index in range(3))
    ]
    preferred = non_zero_max or candidates
    if not preferred:
        return None
    # 真实武器记录的 default_sub_item type 通常是 17；若还有多个候选，取最靠后的非零 max。
    type_17 = [item for item in preferred if data[item["default_sub_item"]] == 17]
    selected = type_17 or preferred
    return max(selected, key=lambda item: item["default_sub_item"])


def _locate_respawn_from_tail(data: bytes, max_charged_offset: int) -> int | None:
    """从 max_charged 后继续走尾部 schema，定位 respawn_time_seconds 起点。"""
    field_names = [spec[0] for spec in _ITEM_FIELDS]
    try:
        start_index = field_names.index("hackable_character_group_info_list")
    except ValueError:
        return None
    reader = _Reader(data, max_charged_offset + 12, rec_end=len(data))
    parsed: dict[str, Any] = {}
    try:
        for spec in _ITEM_FIELDS[start_index:]:
            if spec[0] == "respawn_time_seconds":
                return reader.pos
            _consume_iteminfo_spec(reader, spec, parsed)
    except Exception:
        return None
    return None


def _locate_existing_docking_child_data(record: bytes | bytearray) -> int | None:
    """定位已存在的 DockingChildData optional flag 起点。"""
    data = bytes(record)
    socket_pos = data.find(b"Gimmick_Weapon_00_Socket")
    if socket_pos < 0:
        return None
    length_pos = socket_pos - 4
    flag_pos = length_pos - 12 - 1
    if flag_pos < 0 or data[flag_pos] != 1:
        return None
    parent_len = struct.unpack_from("<I", data, length_pos)[0]
    if parent_len != len(b"Gimmick_Weapon_00_Socket"):
        return None
    return flag_pos


def _consume_passive_skill_list(record: bytes | bytearray, offset: int) -> int | None:
    """返回 CArray<PassiveSkillLevel> 结束位置。"""
    if offset + 4 > len(record):
        return None
    count = struct.unpack_from("<I", record, offset)[0]
    end = offset + 4 + count * 8
    if count > 10_000 or end > len(record):
        return None
    return end


def _pack_passive_skill_list(value: object) -> bytes | None:
    """打包 CArray<PassiveSkillLevel>。"""
    if not isinstance(value, list):
        return None
    out = bytearray(struct.pack("<I", len(value)))
    for item in value:
        if not isinstance(item, dict):
            return None
        skill = _coerce_u32(item.get("skill"))
        level = _coerce_u32(item.get("level"))
        if skill is None or level is None:
            return None
        out += struct.pack("<II", skill, level)
    return bytes(out)


def _pack_docking_child_optional(value: object) -> bytes | None:
    """按 DMM 1.4.9.1 的扩展 DockingChildData optional 结构打包。"""
    if not isinstance(value, dict):
        return None
    out = bytearray(b"\x01")
    for name in ("gimmick_info_key", "character_key", "item_key"):
        packed = _pack_u32(value.get(name))
        if packed is None:
            return None
        out += packed
    for name in ("attach_parent_socket_name", "attach_child_socket_name"):
        packed_string = _pack_cstring(value.get(name))
        if packed_string is None:
            return None
        out += packed_string
    tag_hashes = _coerce_fixed_u32_list(value.get("docking_tag_name_hash"), 4)
    if tag_hashes is None:
        return None
    out += b"".join(struct.pack("<I", item) for item in tag_hashes)
    equip_slot = _coerce_u16(value.get("docking_equip_slot_no"))
    spawn = _pack_u32(value.get("spawn_distance_level"))
    if equip_slot is None or spawn is None:
        return None
    out += struct.pack("<H", equip_slot)
    out += spawn
    for name in (
        "is_item_equip_docking_gimmick",
        "send_damage_to_parent",
        "is_body_part",
        "docking_type",
        "is_summoner_team",
        "is_player_only",
    ):
        packed = _pack_u8(value.get(name))
        if packed is None:
            return None
        out += packed
    packed_npc = _pack_u32(value.get("is_npc_only"))
    if packed_npc is None:
        return None
    out += packed_npc
    for name in (
        "is_sync_break_parent",
        "hit_part",
        "detected_by_npc",
        "is_bag_docking",
        "enable_collision",
        "disable_collision_with_other_gimmick",
    ):
        packed = _pack_u8(value.get(name))
        if packed is None:
            return None
        out += packed
    out += struct.pack("<I", _coerce_u32(value.get("unk_docking_108", 0)) or 0)
    packed_slot = _pack_cstring(value.get("docking_slot_key", ""))
    inherit = _pack_u8(value.get("inherit_summoner", 0))
    summon_hashes = _coerce_fixed_u32_list(value.get("summon_tag_name_hash", [0, 0, 0, 0]), 4)
    if packed_slot is None or inherit is None or summon_hashes is None:
        return None
    out += packed_slot
    out += inherit
    out += b"".join(struct.pack("<I", item) for item in summon_hashes)
    return bytes(out)


def _pack_cstring(value: object) -> bytes | None:
    """打包长度前缀 UTF-8 字符串。"""
    if not isinstance(value, str):
        return None
    raw = value.encode("utf-8")
    return struct.pack("<I", len(raw)) + raw


def _pack_i64(value: object) -> bytes | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if not -(2**63) <= value <= 2**63 - 1:
        return None
    return struct.pack("<q", value)


def _pack_u32(value: object) -> bytes | None:
    coerced = _coerce_u32(value)
    return None if coerced is None else struct.pack("<I", coerced)


def _pack_u8(value: object) -> bytes | None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFF:
        return None
    return struct.pack("<B", value)


def _coerce_u32(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFFFFFFFF:
        return None
    return value


def _coerce_u16(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFFFF:
        return None
    return value


def _coerce_fixed_u32_list(value: object, count: int) -> list[int] | None:
    if not isinstance(value, list) or len(value) != count:
        return None
    result: list[int] = []
    for item in value:
        coerced = _coerce_u32(item)
        if coerced is None:
            return None
        result.append(coerced)
    return result


def _replace_fixed(record: bytearray, offset: int, old_size: int, patched: bytes) -> bool:
    """替换固定宽度字段。"""
    if len(patched) != old_size:
        return False
    return _replace_range(record, offset, offset + old_size, patched)


def _replace_range(record: bytearray, start: int, end: int, patched: bytes) -> bool:
    """替换指定 record 范围。"""
    original = bytes(record[start:end])
    if original == patched:
        return False
    record[start:end] = patched
    return True


def _resolve_bounds(
    entry_bounds: dict[int, tuple[int, int, str, int]],
    intent: Format3Intent,
) -> tuple[tuple[int, int, str, int] | None, str | None]:
    """优先按 key 命中，回退唯一 entry 名称。"""
    bounds = entry_bounds.get(intent.key)
    if bounds is not None:
        return bounds, None
    if not intent.entry:
        return None, "目标 entry key 未命中"
    matches = [bounds for bounds in entry_bounds.values() if bounds[2] == intent.entry]
    if len(matches) == 1:
        return matches[0], None
    if not matches:
        return None, "目标 entry key/名称 都未命中"
    return None, "目标 entry 名称命中多个记录，存在歧义"


def _skip(intent: Format3Intent, reason: str) -> Format3SkippedIntent:
    """构造 skipped intent。"""
    return Format3SkippedIntent(intent=intent, reason=reason)
