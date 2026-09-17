"""ItemGroupInfo PABGB 原生读写实现（2.02.00 wire 布局）。

wire 顺序（`key_size=2`）：

```text
key                     u16
string_key              CString
is_blocked              u8
group_name              LocalizableString (u8 category + u64 index + CString default)
item_group_info_list    CArray<u16>
item_info_list          CArray<u32>
category_type_list      CArray<u8>
order_index             u16
item_cage_type          u8
icon_path               u32
is_show_category_string u8
is_group_item_lockable  u8
is_monster_only_equip   u8
is_always_fold_item_group u8
```

整表恒等回环（1600 条）验证通过；解析失败的记录保留原字节。
"""

from __future__ import annotations

from typing import Any

from cdmm.services.pab_record_codec import (
    Reader as _Reader,
    RecordParseError,
    Writer as _Writer,
    require_uint,
)

ItemGroupParseError = RecordParseError

# 允许模组 patch 直接写入的记录字段。
ITEMGROUPINFO_PATCHABLE_FIELDS = frozenset(
    {
        "string_key",
        "is_blocked",
        "group_name",
        "item_group_info_list",
        "item_info_list",
        "category_type_list",
        "order_index",
        "item_cage_type",
        "icon_path",
        "is_show_category_string",
        "is_group_item_lockable",
        "is_monster_only_equip",
        "is_always_fold_item_group",
    }
)

_ARRAY_FIELDS: tuple[tuple[str, str], ...] = (
    ("item_group_info_list", "u16"),
    ("item_info_list", "u32"),
    ("category_type_list", "u8"),
)

_SCALAR_FIELDS: tuple[tuple[str, str], ...] = (
    ("order_index", "u16"),
    ("item_cage_type", "u8"),
    ("icon_path", "u32"),
    ("is_show_category_string", "u8"),
    ("is_group_item_lockable", "u8"),
    ("is_monster_only_equip", "u8"),
    ("is_always_fold_item_group", "u8"),
)

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


def _parse_localizable(reader: _Reader) -> dict[str, Any]:
    """解析 LocalizableString。"""
    return {
        "category": reader.u8(),
        "index": reader.u64(),
        "default": reader.cstring(),
    }


def _write_localizable(writer: _Writer, value: object) -> None:
    """写回 LocalizableString。"""
    if not isinstance(value, dict):
        raise ItemGroupParseError("itemgroupinfo group_name 必须是对象")
    writer.u8(require_uint(value.get("category"), "group_name.category"))
    writer.u64(require_uint(value.get("index"), "group_name.index"))
    default = value.get("default")
    if not isinstance(default, str):
        raise ItemGroupParseError("itemgroupinfo group_name.default 必须是字符串")
    writer.cstring(default)


def _parse_array(reader: _Reader, kind: str) -> list[int]:
    """解析定宽整型 CArray。"""
    count = reader.u32()
    if count > 500_000:
        raise ItemGroupParseError("itemgroupinfo CArray 数量不可信")
    return [_SCALAR_READERS[kind](reader) for _ in range(count)]


def _write_array(writer: _Writer, kind: str, value: object) -> None:
    """写回定宽整型 CArray。"""
    if not isinstance(value, list):
        raise ItemGroupParseError("itemgroupinfo CArray 必须是数组")
    writer.u32(len(value))
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            raise ItemGroupParseError("itemgroupinfo CArray 元素必须是非负整数")
        _SCALAR_WRITERS[kind](writer, item)


def parse_record(
    data: bytes,
    offset: int,
    end: int | None = None,
    key_size: int = 2,
) -> dict[str, Any]:
    """解析一条 ItemGroupInfo 记录（含 `key` 与原字节范围）。"""
    limit = len(data) if end is None else end
    reader = _Reader(data, offset, limit)
    record: dict[str, Any] = {}
    if key_size == 2:
        record["key"] = reader.u16()
    elif key_size == 4:
        record["key"] = reader.u32()
    else:
        raise ItemGroupParseError(f"itemgroupinfo key_size={key_size} 不可信")
    record["string_key"] = reader.cstring()
    record["is_blocked"] = reader.u8()
    record["group_name"] = _parse_localizable(reader)
    for name, kind in _ARRAY_FIELDS:
        record[name] = _parse_array(reader, kind)
    for name, kind in _SCALAR_FIELDS:
        record[name] = _SCALAR_READERS[kind](reader)
    record["_entry_start"] = offset
    record["_entry_end"] = reader.pos
    return record


def serialize_record(record: dict[str, Any], *, key_size: int = 2) -> bytes:
    """按当前 schema 序列化一条 ItemGroupInfo 记录。"""
    if record.get("_opaque"):
        return bytes(record["bytes"])
    writer = _Writer()
    key = record.get("key")
    if isinstance(key, bool) or not isinstance(key, int) or key < 0:
        raise ItemGroupParseError("itemgroupinfo key 必须是非负整数")
    if key_size == 2:
        writer.u16(key)
    elif key_size == 4:
        writer.u32(key)
    else:
        raise ItemGroupParseError(f"itemgroupinfo key_size={key_size} 不可信")
    string_key = record.get("string_key")
    if not isinstance(string_key, str):
        raise ItemGroupParseError("itemgroupinfo string_key 必须是字符串")
    writer.cstring(string_key)
    writer.u8(require_uint(record.get("is_blocked"), "is_blocked"))
    _write_localizable(writer, record.get("group_name"))
    for name, kind in _ARRAY_FIELDS:
        _write_array(writer, kind, record.get(name))
    for name, kind in _SCALAR_FIELDS:
        raw = record.get(name)
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
            raise ItemGroupParseError(f"itemgroupinfo {name} 必须是非负整数")
        _SCALAR_WRITERS[kind](writer, raw)
    return bytes(writer.buf)


def serialize_table(
    records: list[dict[str, Any]],
    key_size: int = 2,
) -> tuple[bytes, dict[int, int]]:
    """序列化整表并返回 `(body, key -> offset)`。"""
    chunks: list[bytes] = []
    offsets: dict[int, int] = {}
    cursor = 0
    for record in records:
        raw = serialize_record(record, key_size=key_size)
        offsets[record["key"]] = cursor
        chunks.append(raw)
        cursor += len(raw)
    return b"".join(chunks), offsets
