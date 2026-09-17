"""DropSetInfo PABGB 原生读写实现（2.02.00 wire 布局）。

wire 顺序（`key_size=4`，每条记录以 `u32 key + u32 name_len + name` 起头后）：

```text
string_key            CString
is_blocked            u8
drop_roll_type        u8
drop_roll_count       u32
drop_condition_string CString
drop_tag_name_hash    u32
list                  CArray<OptionalDropTarget>
nee_slot_count        u16
need_weight           u64
total_drop_rate       u64
roll_dice_skip_rate   u64
original_string       CString
is_equal_percent      u8
```

`list` 的每个元素是 `u8 presence` + 可选 `DropTargetData`：固定前缀
`raw_at_120(u64) dispatch_tag(u8) lookup_4(u32) lookup_6(u32) lookup_8(u32)
raw_12(u32) raw_16(u64) raw_32(u64) raw_40(u64) raw_48(u64) raw_56(u16)`，
随后是按 `dispatch_tag` 分派的 variants 尾。

字段宽度与顺序由 2.02.00 原表整表恒等回环验证；不确定的记录一律退回
`_opaque` 原字节，保证不会写出错误结构。
"""

from __future__ import annotations

from typing import Any

from cdmm.services.pab_record_codec import (
    Reader as _Reader,
    RecordParseError,
    Writer as _Writer,
    require_uint,
)

# variant tag -> JSON key 组合。全部按 u32/u8 原样读写，避免语义猜测。
_VARIANT_SINGLE_LOOKUP = frozenset({0, 1, 2, 3, 4, 5, 6, 9, 0xC})
_VARIANT_ITEM_REF = frozenset({7, 8})

_ITEM_REF_FIELDS: tuple[tuple[str, str], ...] = (
    ("flag_a", "u8"),
    ("raw_b", "u64"),
    ("lookup_c", "u32"),
    ("lookup_d", "u32"),
    ("flag_e", "u8"),
    ("lookup_f", "u32"),
    ("raw_g", "u64"),
    ("flag_h", "u8"),
    ("flag_i", "u8"),
)

_DROP_FIELDS: tuple[tuple[str, str], ...] = (
    ("raw_at_120", "u64"),
    ("dispatch_tag", "u8"),
    ("lookup_4", "u32"),
    ("lookup_6", "u32"),
    ("lookup_8", "u32"),
    ("raw_12", "u32"),
    ("raw_16", "u64"),
    ("raw_32", "u64"),
    ("raw_40", "u64"),
    ("raw_48", "u64"),
    ("raw_56", "u16"),
)


# 通用小端读写原语集中在 pab_record_codec，避免每张表复制一套边界检查。
DropsetParseError = RecordParseError


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


def _parse_item_ref(reader: _Reader) -> dict[str, int]:
    """解析 tag 7/8 的 `DropTargetItemRef`（32 wire bytes）。"""
    out: dict[str, int] = {}
    for name, kind in _ITEM_REF_FIELDS:
        out[name] = _SCALAR_READERS[kind](reader)
    return out


def _write_item_ref(writer: _Writer, value: object) -> None:
    """写回 tag 7/8 的 `DropTargetItemRef`。"""
    if not isinstance(value, dict):
        raise DropsetParseError("dropsetinfo variant.data 必须是对象")
    for name, kind in _ITEM_REF_FIELDS:
        raw = value.get(name)
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise DropsetParseError(f"dropsetinfo variant.data.{name} 必须是整数")
        _SCALAR_WRITERS[kind](writer, raw)


def _parse_variant(reader: _Reader, tag: int) -> dict[str, Any]:
    """按 `dispatch_tag` 解析 variants 尾。"""
    if tag in _VARIANT_SINGLE_LOOKUP:
        return {"tag": tag, "lookup": reader.u32()}
    if tag in _VARIANT_ITEM_REF:
        return {"tag": tag, "data": _parse_item_ref(reader)}
    if tag == 0xA:
        return {"tag": tag, "lookup_a": reader.u32(), "lookup_b": reader.u32()}
    if tag == 0xB:
        return {"tag": tag}
    if tag == 0xD:
        return {"tag": tag, "lookup": reader.u32(), "flag": reader.u8()}
    if tag == 0x10:
        return {
            "tag": tag,
            "data": {
                "character_info": reader.u32(),
                "to_target_actor": reader.u8(),
                "is_register": reader.u8(),
            },
        }
    raise DropsetParseError(f"dropsetinfo 未知 dispatch_tag {tag}")


def _write_variant(writer: _Writer, value: object) -> None:
    """写回 variants 尾，tag 决定 payload 形状。"""
    if not isinstance(value, dict):
        raise DropsetParseError("dropsetinfo variant 必须是对象")
    tag = value.get("tag")
    if isinstance(tag, bool) or not isinstance(tag, int):
        raise DropsetParseError("dropsetinfo variant.tag 必须是整数")
    if tag in _VARIANT_SINGLE_LOOKUP:
        writer.u32(_require_uint(value.get("lookup"), "variant.lookup"))
        return
    if tag in _VARIANT_ITEM_REF:
        _write_item_ref(writer, value.get("data"))
        return
    if tag == 0xA:
        writer.u32(_require_uint(value.get("lookup_a"), "variant.lookup_a"))
        writer.u32(_require_uint(value.get("lookup_b"), "variant.lookup_b"))
        return
    if tag == 0xB:
        return
    if tag == 0xD:
        writer.u32(_require_uint(value.get("lookup"), "variant.lookup"))
        writer.u8(_require_uint(value.get("flag"), "variant.flag"))
        return
    if tag == 0x10:
        data = value.get("data")
        if not isinstance(data, dict):
            raise DropsetParseError("dropsetinfo variant.data 必须是对象")
        writer.u32(_require_uint(data.get("character_info"), "variant.data.character_info"))
        writer.u8(_require_uint(data.get("to_target_actor"), "variant.data.to_target_actor"))
        writer.u8(_require_uint(data.get("is_register"), "variant.data.is_register"))
        return
    raise DropsetParseError(f"dropsetinfo 未知 dispatch_tag {tag}")


_require_uint = require_uint


def _parse_drop_target(reader: _Reader) -> dict[str, Any]:
    """解析一个 `DropTargetData`（59 字节固定前缀 + variants 尾）。"""
    out: dict[str, Any] = {}
    for name, kind in _DROP_FIELDS:
        out[name] = _SCALAR_READERS[kind](reader)
    out["variant"] = _parse_variant(reader, out["dispatch_tag"])
    return out


def _write_drop_target(writer: _Writer, value: object) -> None:
    """写回一个 `DropTargetData`。"""
    if not isinstance(value, dict):
        raise DropsetParseError("dropsetinfo list 元素必须是对象")
    for name, kind in _DROP_FIELDS:
        raw = value.get(name)
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise DropsetParseError(f"dropsetinfo list[].{name} 必须是整数")
        _SCALAR_WRITERS[kind](writer, raw)
    _write_variant(writer, value.get("variant"))

_RECORD_FIELDS: tuple[tuple[str, str], ...] = (
    ("string_key", "cstring"),
    ("is_blocked", "u8"),
    ("drop_roll_type", "u8"),
    ("drop_roll_count", "u32"),
    ("drop_condition_string", "cstring"),
    ("drop_tag_name_hash", "u32"),
    ("list", "list"),
    ("nee_slot_count", "u16"),
    ("need_weight", "u64"),
    ("total_drop_rate", "u64"),
    ("roll_dice_skip_rate", "u64"),
    ("original_string", "cstring"),
    ("is_equal_percent", "u8"),
)

# 允许模组 patch 直接写入的记录字段（不含内部字段）。
DROPSETINFO_PATCHABLE_FIELDS = frozenset(name for name, _kind in _RECORD_FIELDS)


def parse_record(data: bytes, offset: int, end: int | None = None, key_size: int = 4) -> dict[str, Any]:
    """解析一条 DropSetInfo 记录（含 `key` 与原字节范围）。"""
    limit = len(data) if end is None else end
    reader = _Reader(data, offset, limit)
    record: dict[str, Any] = {}
    if key_size == 4:
        record["key"] = reader.u32()
    elif key_size == 2:
        record["key"] = reader.u16()
    else:
        raise DropsetParseError(f"dropsetinfo key_size={key_size} 不可信")
    try:
        for name, kind in _RECORD_FIELDS:
            if kind == "list":
                record["list"] = _parse_list(reader)
            elif kind == "cstring":
                record[name] = reader.cstring()
            else:
                record[name] = _SCALAR_READERS[kind](reader)
    except DropsetParseError as exc:
        raise DropsetParseError(f"dropsetinfo key={record.get('key')} 解析失败：{exc}") from exc
    record["_entry_start"] = offset
    record["_entry_end"] = reader.pos
    return record


def _parse_list(reader: _Reader) -> list[Any]:
    """解析 `CArray<OptionalDropTarget>`。"""
    count = reader.u32()
    if count > 200_000:
        raise DropsetParseError("dropsetinfo list 数量不可信")
    items: list[Any] = []
    for _ in range(count):
        presence = reader.u8()
        items.append(_parse_drop_target(reader) if presence else None)
    return items


def serialize_record(record: dict[str, Any], *, key_size: int = 4) -> bytes:
    """按当前 schema 序列化一条 DropSetInfo 记录。"""
    writer = _Writer()
    key = record.get("key")
    if isinstance(key, bool) or not isinstance(key, int) or key < 0:
        raise DropsetParseError("dropsetinfo key 必须是非负整数")
    if key_size == 4:
        writer.u32(key)
    elif key_size == 2:
        writer.u16(key)
    else:
        raise DropsetParseError(f"dropsetinfo key_size={key_size} 不可信")
    for name, kind in _RECORD_FIELDS:
        value = record.get(name)
        if kind == "list":
            _write_list(writer, value)
        elif kind == "cstring":
            if not isinstance(value, str):
                raise DropsetParseError(f"dropsetinfo {name} 必须是字符串")
            writer.cstring(value)
        else:
            writer.buf += b""
            raw = value
            if isinstance(raw, bool) or not isinstance(raw, int):
                raise DropsetParseError(f"dropsetinfo {name} 必须是整数")
            _SCALAR_WRITERS[kind](writer, raw)
    return bytes(writer.buf)


def _write_list(writer: _Writer, value: object) -> None:
    """写回 `CArray<OptionalDropTarget>`。"""
    if not isinstance(value, list):
        raise DropsetParseError("dropsetinfo list 必须是数组")
    writer.u32(len(value))
    for element in value:
        if element is None:
            writer.u8(0)
            continue
        writer.u8(1)
        _write_drop_target(writer, element)


def parse_table(body: bytes, key_size: int, offsets: dict[int, int]) -> list[dict[str, Any]]:
    """按 PABGH 偏移解析整表，记录按偏移升序。"""
    ordered = sorted(offsets.items(), key=lambda item: item[1])
    records: list[dict[str, Any]] = []
    for index, (_key, offset) in enumerate(ordered):
        end = ordered[index + 1][1] if index + 1 < len(ordered) else len(body)
        records.append(parse_record(body, offset, end))
    return records


def serialize_table(records: list[dict[str, Any]], key_size: int = 4) -> tuple[bytes, dict[int, int]]:
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
