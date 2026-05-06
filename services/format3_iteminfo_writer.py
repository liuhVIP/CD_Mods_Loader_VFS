"""Format 3 ItemInfo 专用字节补丁生成器。

当前独立加载器先支持真实常见的
``prefab_data_list[N].tribe_gender_list`` 写入，不依赖完整管理器的数据库
或 crimson_rs 整表解析器，直接在 vanilla PABGB entry 内定位数组并生成
传统 byte patch。
"""

from __future__ import annotations

import re
import struct
from typing import Any

# Format 3 字段路径：装备可穿戴种族/性别数组。
PREFAB_TRIBE_GENDER_FIELD_RE = re.compile(
    r"^prefab_data_list\[(?P<index>\d+)]\.tribe_gender_list$"
)

# 单个 entry 内数组数量的安全上限，用于快速拒绝错位读取。
MAX_REASONABLE_ARRAY_COUNT = 1_000_000

# 扫描 fallback 只接受较小的 PrefabData 数量，降低误命中普通 u32 的风险。
MAX_PREFAB_SCAN_COUNT = 128

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
        change = _build_single_change(vanilla_body, key_size, entry_bounds, intent)
        if change is None:
            skipped += 1
        else:
            changes.append(change)
    return changes, skipped


def _build_single_change(
    body: bytes,
    key_size: int,
    entry_bounds: dict[int, tuple[int, int, str, int]],
    intent: dict[str, Any],
) -> dict | None:
    """定位单条 intent 的 tribe_gender_list 并生成 replace change。"""
    if intent.get("op", "set") != "set":
        return None
    field = intent.get("field")
    if not isinstance(field, str):
        return None
    match = PREFAB_TRIBE_GENDER_FIELD_RE.match(field)
    if match is None:
        return None
    values = intent.get("new")
    if not _is_u32_list(values):
        return None
    key = intent.get("key")
    if isinstance(key, bool) or not isinstance(key, int):
        return None
    bounds = entry_bounds.get(key)
    if bounds is None:
        return None

    entry_off, entry_end, entry_name, name_end = bounds
    payload_off = _payload_offset(body, entry_off, key_size)
    if payload_off is None:
        return None
    prefab_list_off = _walk_fields(body, payload_off, entry_end, ITEMINFO_FIELDS_BEFORE_PREFAB)
    prefab_index = int(match.group("index"))
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
        return None

    start, end = tribe_range
    patched = struct.pack("<I", len(values)) + b"".join(struct.pack("<I", item) for item in values)
    original = body[start:end]
    if original == patched:
        return None
    return {
        "entry": entry_name or str(intent.get("entry") or ""),
        "rel_offset": start - name_end,
        "original": original.hex(),
        "patched": patched.hex(),
        "label": f"{intent.get('entry', key)}.{field}",
    }


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
