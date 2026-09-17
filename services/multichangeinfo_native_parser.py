"""MultiChangeInfo PABGB 原生读写实现（2.02.00 wire 布局）。

wire 顺序（记录以 `u32 key` 起头）：

```text
string_key                          CString
is_blocked                          u8
craft_tool_info                     u16
item_consume_type                   u8
condition_list                      CArray<{condition_info u32, label LocalizableString}>
need_knowledge_info                 u32
craft_tag_name                      CString
is_from_item_info                   u8
is_with_sealed_item                 u8
is_apply_enchant_level              u8
is_material_item_only_same_item_no  u8
is_allow_material_item_self_same    u8
fixed_material_data_list            CArray<FixedMaterialData>
recipe_item_group_info_list         CArray<{item_group_info u16, count u64, enchant_level u16}>
elemental_status_info               u32
elemental_material_state_list       CArray<CString>
name                                LocalizableString
description                         LocalizableString
enchant_recipe_desc                 u32
group_string_info                   u32
sub_group_string_info               u32
complete_description                LocalizableString
result_drop_info_list               CArray<u32>
additional_drop_info_list           CArray<u32>
```

`FixedMaterialData` 是 30 字节定宽：`item_info u32` + `gimmick_info u32` +
`character_info u32` + `count u64` + `coupon_count u64` + `enchant_level u16`。
`LocalizableString` 是 `category u8` + `index u64` + `default CString`。

字段顺序与宽度由 2.02.00 原表整表恒等回环验证（18576 条记录走满全部字节且
与参考实现逐字段一致）；解析失败的记录一律退回 `_opaque` 原字节，保证不会
写出错误结构。
"""

from __future__ import annotations

from typing import Any

from cdmm.services.pab_record_codec import (
    Reader as _Reader,
    RecordParseError,
    Writer as _Writer,
)

# 通用小端读写原语集中在 pab_record_codec，避免每张表复制一套边界检查。
MultichangeParseError = RecordParseError

_SCALAR_READERS = {
    "u8": lambda r: r.u8(),
    "u16": lambda r: r.u16(),
    "u32": lambda r: r.u32(),
    "u64": lambda r: r.u64(),
}
_SCALAR_WRITERS = {
    "u8": lambda w, v: w.u8(v),
    "u16": lambda w, v: w.u16(v),
    "u32": lambda w, v: w.u32(v),
    "u64": lambda w, v: w.u64(v),
}

_FIXED_MATERIAL_FIELDS: tuple[tuple[str, str], ...] = (
    ("item_info", "u32"),
    ("gimmick_info", "u32"),
    ("character_info", "u32"),
    ("count", "u64"),
    ("coupon_count", "u64"),
    ("enchant_level", "u16"),
)

_RECIPE_ITEM_GROUP_FIELDS: tuple[tuple[str, str], ...] = (
    ("item_group_info", "u16"),
    ("count", "u64"),
    ("enchant_level", "u16"),
)

# 允许模组 patch 直接写入的记录字段（不含 key 与内部字段）。
MULTICHANGEINFO_PATCHABLE_FIELDS = frozenset(
    {
        "string_key",
        "is_blocked",
        "craft_tool_info",
        "item_consume_type",
        "condition_list",
        "need_knowledge_info",
        "craft_tag_name",
        "is_from_item_info",
        "is_with_sealed_item",
        "is_apply_enchant_level",
        "is_material_item_only_same_item_no",
        "is_allow_material_item_self_same",
        "fixed_material_data_list",
        "recipe_item_group_info_list",
        "elemental_status_info",
        "elemental_material_state_list",
        "name",
        "description",
        "enchant_recipe_desc",
        "group_string_info",
        "sub_group_string_info",
        "complete_description",
        "result_drop_info_list",
        "additional_drop_info_list",
    }
)

_MAX_LIST = 200_000


def _parse_localizable(reader: _Reader) -> dict[str, Any]:
    """解析 `LocalizableString`（u8 category + u64 index + CString default）。"""
    return {
        "category": reader.u8(),
        "index": reader.u64(),
        "default": reader.cstring(),
    }


def _write_localizable(writer: _Writer, value: object, label: str) -> None:
    """写回 `LocalizableString`。"""
    if not isinstance(value, dict):
        raise MultichangeParseError(f"multichangeinfo {label} 必须是对象")
    category = value.get("category")
    index = value.get("index")
    default = value.get("default")
    if isinstance(category, bool) or not isinstance(category, int):
        raise MultichangeParseError(f"multichangeinfo {label}.category 必须是整数")
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise MultichangeParseError(f"multichangeinfo {label}.index 必须是非负整数")
    if not isinstance(default, str):
        raise MultichangeParseError(f"multichangeinfo {label}.default 必须是字符串")
    writer.u8(category)
    writer.u64(index)
    writer.cstring(default)


def _count(reader: _Reader, label: str) -> int:
    """读取并校验 CArray 元素数量。"""
    count = reader.u32()
    if count > _MAX_LIST:
        raise MultichangeParseError(f"multichangeinfo {label} 数量不可信（{count}）")
    return count


def _parse_fixed_material_list(reader: _Reader) -> list[dict[str, int]]:
    """解析 `CArray<FixedMaterialData>`。"""
    items: list[dict[str, int]] = []
    for _ in range(_count(reader, "fixed_material_data_list")):
        item: dict[str, int] = {}
        for name, kind in _FIXED_MATERIAL_FIELDS:
            item[name] = _SCALAR_READERS[kind](reader)
        items.append(item)
    return items


def _write_fixed_material_list(writer: _Writer, value: object, label: str) -> None:
    """写回 `CArray<FixedMaterialData>`。"""
    if not isinstance(value, list):
        raise MultichangeParseError(f"multichangeinfo {label} 必须是数组")
    writer.u32(len(value))
    for element in value:
        if not isinstance(element, dict):
            raise MultichangeParseError(f"multichangeinfo {label} 元素必须是对象")
        for name, kind in _FIXED_MATERIAL_FIELDS:
            raw = element.get(name)
            if isinstance(raw, bool) or not isinstance(raw, int):
                raise MultichangeParseError(
                    f"multichangeinfo {label}[].{name} 必须是整数"
                )
            _SCALAR_WRITERS[kind](writer, raw)


def _parse_recipe_list(reader: _Reader) -> list[dict[str, int]]:
    """解析 `CArray<recipe_item_group_info_list 元素>`。"""
    items: list[dict[str, int]] = []
    for _ in range(_count(reader, "recipe_item_group_info_list")):
        item: dict[str, int] = {}
        for name, kind in _RECIPE_ITEM_GROUP_FIELDS:
            item[name] = _SCALAR_READERS[kind](reader)
        items.append(item)
    return items


def _write_recipe_list(writer: _Writer, value: object, label: str) -> None:
    """写回 `CArray<recipe_item_group_info_list 元素>`。"""
    if not isinstance(value, list):
        raise MultichangeParseError(f"multichangeinfo {label} 必须是数组")
    writer.u32(len(value))
    for element in value:
        if not isinstance(element, dict):
            raise MultichangeParseError(f"multichangeinfo {label} 元素必须是对象")
        for name, kind in _RECIPE_ITEM_GROUP_FIELDS:
            raw = element.get(name)
            if isinstance(raw, bool) or not isinstance(raw, int):
                raise MultichangeParseError(
                    f"multichangeinfo {label}[].{name} 必须是整数"
                )
            _SCALAR_WRITERS[kind](writer, raw)


def _parse_condition_list(reader: _Reader) -> list[dict[str, Any]]:
    """解析 `CArray<{condition_info u32, label LocalizableString}>`。"""
    items: list[dict[str, Any]] = []
    for _ in range(_count(reader, "condition_list")):
        items.append(
            {
                "condition_info": reader.u32(),
                "label": _parse_localizable(reader),
            }
        )
    return items


def _write_condition_list(writer: _Writer, value: object, label: str) -> None:
    """写回 `CArray<{condition_info u32, label LocalizableString}>`。"""
    if not isinstance(value, list):
        raise MultichangeParseError(f"multichangeinfo {label} 必须是数组")
    writer.u32(len(value))
    for element in value:
        if not isinstance(element, dict):
            raise MultichangeParseError(f"multichangeinfo {label} 元素必须是对象")
        condition_info = element.get("condition_info")
        if isinstance(condition_info, bool) or not isinstance(condition_info, int):
            raise MultichangeParseError(
                f"multichangeinfo {label}[].condition_info 必须是整数"
            )
        writer.u32(condition_info)
        _write_localizable(writer, element.get("label"), f"{label}[].label")


def _parse_string_list(reader: _Reader, label: str) -> list[str]:
    """解析 `CArray<CString>`。"""
    return [reader.cstring() for _ in range(_count(reader, label))]


def _write_string_list(writer: _Writer, value: object, label: str) -> None:
    """写回 `CArray<CString>`。"""
    if not isinstance(value, list):
        raise MultichangeParseError(f"multichangeinfo {label} 必须是数组")
    writer.u32(len(value))
    for element in value:
        if not isinstance(element, str):
            raise MultichangeParseError(f"multichangeinfo {label} 元素必须是字符串")
        writer.cstring(element)


def _parse_u32_list(reader: _Reader, label: str) -> list[int]:
    """解析 `CArray<u32>`。"""
    return [reader.u32() for _ in range(_count(reader, label))]


def _write_u32_list(writer: _Writer, value: object, label: str) -> None:
    """写回 `CArray<u32>`。"""
    if not isinstance(value, list):
        raise MultichangeParseError(f"multichangeinfo {label} 必须是数组")
    writer.u32(len(value))
    for element in value:
        if isinstance(element, bool) or not isinstance(element, int) or element < 0:
            raise MultichangeParseError(f"multichangeinfo {label} 元素必须是非负整数")
        writer.u32(element)


def parse_record(data: bytes, offset: int, end: int | None = None) -> dict[str, Any]:
    """解析一条 MultiChangeInfo 记录（含 `key` 与原字节范围）。"""
    limit = len(data) if end is None else end
    reader = _Reader(data, offset, limit)
    record: dict[str, Any] = {"key": reader.u32()}
    try:
        record["string_key"] = reader.cstring()
        record["is_blocked"] = reader.u8()
        record["craft_tool_info"] = reader.u16()
        record["item_consume_type"] = reader.u8()
        record["condition_list"] = _parse_condition_list(reader)
        record["need_knowledge_info"] = reader.u32()
        record["craft_tag_name"] = reader.cstring()
        record["is_from_item_info"] = reader.u8()
        record["is_with_sealed_item"] = reader.u8()
        record["is_apply_enchant_level"] = reader.u8()
        record["is_material_item_only_same_item_no"] = reader.u8()
        record["is_allow_material_item_self_same"] = reader.u8()
        record["fixed_material_data_list"] = _parse_fixed_material_list(reader)
        record["recipe_item_group_info_list"] = _parse_recipe_list(reader)
        record["elemental_status_info"] = reader.u32()
        record["elemental_material_state_list"] = _parse_string_list(
            reader, "elemental_material_state_list"
        )
        record["name"] = _parse_localizable(reader)
        record["description"] = _parse_localizable(reader)
        record["enchant_recipe_desc"] = reader.u32()
        record["group_string_info"] = reader.u32()
        record["sub_group_string_info"] = reader.u32()
        record["complete_description"] = _parse_localizable(reader)
        record["result_drop_info_list"] = _parse_u32_list(reader, "result_drop_info_list")
        record["additional_drop_info_list"] = _parse_u32_list(
            reader, "additional_drop_info_list"
        )
    except MultichangeParseError as exc:
        raise MultichangeParseError(
            f"multichangeinfo key={record.get('key')} 解析失败：{exc}"
        ) from exc
    record["_entry_start"] = offset
    record["_entry_end"] = reader.pos
    return record


def serialize_record(record: dict[str, Any]) -> bytes:
    """按当前 schema 序列化一条 MultiChangeInfo 记录。"""
    writer = _Writer()
    key = record.get("key")
    if isinstance(key, bool) or not isinstance(key, int) or key < 0:
        raise MultichangeParseError("multichangeinfo key 必须是非负整数")
    writer.u32(key)

    string_key = record.get("string_key")
    if not isinstance(string_key, str):
        raise MultichangeParseError("multichangeinfo string_key 必须是字符串")
    writer.cstring(string_key)

    _write_scalar(writer, record.get("is_blocked"), "u8", "is_blocked")
    _write_scalar(writer, record.get("craft_tool_info"), "u16", "craft_tool_info")
    _write_scalar(writer, record.get("item_consume_type"), "u8", "item_consume_type")
    _write_condition_list(writer, record.get("condition_list"), "condition_list")
    _write_scalar(writer, record.get("need_knowledge_info"), "u32", "need_knowledge_info")

    craft_tag_name = record.get("craft_tag_name")
    if not isinstance(craft_tag_name, str):
        raise MultichangeParseError("multichangeinfo craft_tag_name 必须是字符串")
    writer.cstring(craft_tag_name)

    for name in (
        "is_from_item_info",
        "is_with_sealed_item",
        "is_apply_enchant_level",
        "is_material_item_only_same_item_no",
        "is_allow_material_item_self_same",
    ):
        _write_scalar(writer, record.get(name), "u8", name)
    _write_fixed_material_list(
        writer, record.get("fixed_material_data_list"), "fixed_material_data_list"
    )
    _write_recipe_list(
        writer, record.get("recipe_item_group_info_list"), "recipe_item_group_info_list"
    )
    _write_scalar(writer, record.get("elemental_status_info"), "u32", "elemental_status_info")
    _write_string_list(
        writer, record.get("elemental_material_state_list"), "elemental_material_state_list"
    )
    for name in ("name", "description"):
        _write_localizable(writer, record.get(name), name)
    for name in ("enchant_recipe_desc", "group_string_info", "sub_group_string_info"):
        _write_scalar(writer, record.get(name), "u32", name)
    _write_localizable(writer, record.get("complete_description"), "complete_description")
    for name in ("result_drop_info_list", "additional_drop_info_list"):
        _write_u32_list(writer, record.get(name), name)
    return bytes(writer.buf)


def _write_scalar(writer: _Writer, value: object, kind: str, label: str) -> None:
    """写回单个定宽标量字段。"""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MultichangeParseError(f"multichangeinfo {label} 必须是非负整数")
    _SCALAR_WRITERS[kind](writer, value)


def parse_table(
    body: bytes,
    offsets: dict[int, int] | None = None,
) -> list[dict[str, Any]]:
    """解析整表；给出 PABGH 偏移时按偏移切分，否则顺序走满整个 body。"""
    if offsets:
        ordered = sorted(offsets.items(), key=lambda item: item[1])
        records: list[dict[str, Any]] = []
        for index, (_key, offset) in enumerate(ordered):
            end = ordered[index + 1][1] if index + 1 < len(ordered) else len(body)
            records.append(parse_record(body, offset, end))
        return records

    records = []
    cursor = 0
    while cursor < len(body):
        record = parse_record(body, cursor)
        records.append(record)
        cursor = record["_entry_end"]
    return records


def serialize_table(records: list[dict[str, Any]]) -> tuple[bytes, dict[int, int]]:
    """序列化整表并返回 `(body, key -> offset)`。"""
    chunks: list[bytes] = []
    offsets: dict[int, int] = {}
    cursor = 0
    for record in records:
        raw = serialize_record(record)
        offsets[record["key"]] = cursor
        chunks.append(raw)
        cursor += len(raw)
    return b"".join(chunks), offsets
