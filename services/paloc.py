"""PALOC 本地化表的严格解析与确定性序列化。

红色沙漠 2.01 起每种语言的本地化文本按逻辑表拆成多个 ``*.paloc``
（``gamedata/item.paloc``、``gamedata/character.paloc`` ...）。文件布局为：

```text
[category:u64][key_len:u32][key:utf8][value_len:u32][value:utf8] * N
[record_count:u32]                                 <- 文件最后 4 字节
```

``category`` 只有最低字节有意义（``0x07`` 是物品名/物品描述，``0x03`` 是
角色名），上 7 字节必须为 0；实测仍有 12 个表使用另一种变体（category 高位
非 0），本模块遇到时必须显式拒绝，不能猜测解析。

记录按 key 升序排列（数字键按数值比较），因此新增记录必须插入到排序位置，
不能简单追加：游戏侧是按顺序查找表的。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, replace

# PALOC 长度和整数均使用小端。
_U32 = struct.Struct("<I")
_U64 = struct.Struct("<Q")

# 防止损坏文件声明异常长度后造成超大切片或难以理解的越界错误。
_MAX_STRING_BYTES = 64 * 1024 * 1024


class PalocLayoutError(ValueError):
    """PALOC 使用当前解析器尚未支持的布局变体。"""


@dataclass(frozen=True)
class PalocRecord:
    """PALOC 中一条带稳定字符串键的本地化记录。"""

    category: int
    key: str
    value: str


@dataclass(frozen=True)
class PalocDocument:
    """一个完整 PALOC 文档。"""

    records: tuple[PalocRecord, ...]

    def by_key(self) -> dict[str, PalocRecord]:
        """返回唯一键索引；重复键在解析阶段已经拒绝。"""
        return {record.key: record for record in self.records}

    def categories(self) -> set[int]:
        """返回文档中出现过的 category 集合。"""
        return {record.category for record in self.records}

    def replace_values(self, values: dict[str, str]) -> "PalocDocument":
        """只替换已存在键的文本，禁止隐式新增记录。"""
        known = self.by_key()
        missing = sorted(set(values) - set(known))
        if missing:
            preview = ", ".join(missing[:5])
            raise ValueError(f"PALOC 不存在待修改键：{preview}")
        return PalocDocument(
            records=tuple(
                replace(record, value=values[record.key]) if record.key in values else record
                for record in self.records
            ),
        )

    def with_inserted(self, records: tuple[PalocRecord, ...]) -> "PalocDocument":
        """按 key 升序插入新记录，拒绝重复键与非法 category。"""
        if not records:
            return self
        known = self.by_key()
        duplicated = sorted({record.key for record in records} & set(known))
        if duplicated:
            preview = ", ".join(duplicated[:5])
            raise ValueError(f"PALOC 已存在待新增键：{preview}")
        merged = list(self.records)
        if any(
            record_sort_key(left.key) > record_sort_key(right.key)
            for left, right in zip(merged, merged[1:])
        ):
            raise ValueError("PALOC 记录顺序非升序，拒绝推断新增位置")
        for record in records:
            if record.category >> 8:
                raise PalocLayoutError(
                    f"PALOC category 超出 u8 范围：0x{record.category:x}"
                )
            merged.insert(_insertion_index(merged, record), record)
        return PalocDocument(records=tuple(merged))


def parse_paloc(data: bytes) -> PalocDocument:
    """严格解析 PALOC，任何截断、重复键或非法 UTF-8 都会拒绝。"""
    if len(data) < _U32.size:
        raise ValueError("PALOC 文件不足 4 字节")
    recorded_count = _read_u32(data, len(data) - _U32.size, "记录数")
    body_end = len(data) - _U32.size
    cursor = 0
    records: list[PalocRecord] = []
    seen_keys: set[str] = set()
    while cursor < body_end:
        record_offset = cursor
        category = _read_u64(data, cursor, f"记录@{record_offset} category")
        cursor += _U64.size
        if category >> 8:
            raise PalocLayoutError(
                f"PALOC 记录@{record_offset} 的 category 高位非 0"
                f"（0x{category:x}），当前解析器不支持该布局变体"
            )
        key, cursor = _read_text(data, cursor, f"记录@{record_offset} key")
        value, cursor = _read_text(data, cursor, f"记录@{record_offset} value")
        if not key:
            raise ValueError(f"PALOC 记录@{record_offset} 的 key 为空")
        if key in seen_keys:
            raise ValueError(f"PALOC 存在重复 key：{key}")
        seen_keys.add(key)
        records.append(PalocRecord(category, key, value))
    if cursor > body_end:
        raise ValueError(f"PALOC 记录区越界：解析到 {cursor}，记录区结束于 {body_end}")
    if cursor != body_end:
        raise ValueError(f"PALOC 记录区尾部残留 {body_end - cursor} 字节")
    if recorded_count != len(records):
        raise ValueError(
            f"PALOC 记录数不一致：文件声明 {recorded_count}，实际解析 {len(records)}"
        )
    return PalocDocument(records=tuple(records))


def serialize_paloc(document: PalocDocument) -> bytes:
    """按原始记录顺序确定性写回 PALOC。"""
    output = bytearray()
    seen_keys: set[str] = set()
    for index, record in enumerate(document.records):
        if not record.key:
            raise ValueError(f"PALOC records[{index}].key 为空")
        if record.key in seen_keys:
            raise ValueError(f"PALOC 存在重复 key：{record.key}")
        seen_keys.add(record.key)
        output.extend(_U64.pack(_require_category(record.category, index)))
        output.extend(_encode_text(record.key, f"records[{index}].key"))
        output.extend(_encode_text(record.value, f"records[{index}].value"))
    output.extend(_U32.pack(_require_u32(len(document.records), "record_count")))
    return bytes(output)


def record_sort_key(key: str) -> tuple[int, int, str]:
    """给出与游戏文件一致稳定的 key 排序键。

    物品/角色等键是 ``(target_id << 32) | tag`` 的十进制字符串，必须按数值比较；
    对话等表使用名称字符串，退化为字典序比较。
    """
    if key.isdigit():
        return (0, int(key), "")
    return (1, 0, key)


def _insertion_index(records: list[PalocRecord], record: PalocRecord) -> int:
    """在已排序记录列表中定位新记录的插入位置。"""
    target = record_sort_key(record.key)
    for index, existing in enumerate(records):
        if record_sort_key(existing.key) > target:
            return index
    return len(records)


def _read_text(data: bytes, cursor: int, label: str) -> tuple[str, int]:
    """读取一个 u32 长度前缀的 UTF-8 字符串。"""
    length = _read_u32(data, cursor, f"{label} 长度")
    cursor += _U32.size
    if length > _MAX_STRING_BYTES:
        raise ValueError(f"{label} 长度异常：{length}")
    end = cursor + length
    if end > len(data):
        raise ValueError(f"{label} 越界：需要 {length} 字节，仅剩 {len(data) - cursor} 字节")
    try:
        return data[cursor:end].decode("utf-8"), end
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label} 不是合法 UTF-8：{exc}") from exc


def _read_u32(data: bytes, cursor: int, label: str) -> int:
    """读取小端 u32，并把底层越界转换为清晰错误。"""
    if cursor + _U32.size > len(data):
        raise ValueError(f"PALOC {label} 越界")
    return _U32.unpack_from(data, cursor)[0]


def _read_u64(data: bytes, cursor: int, label: str) -> int:
    """读取小端 u64，并把底层越界转换为清晰错误。"""
    if cursor + _U64.size > len(data):
        raise ValueError(f"PALOC {label} 越界")
    return _U64.unpack_from(data, cursor)[0]


def _encode_text(value: str, label: str) -> bytes:
    """编码一个带 u32 长度前缀的 UTF-8 字符串。"""
    encoded = value.encode("utf-8")
    if len(encoded) > _MAX_STRING_BYTES:
        raise ValueError(f"{label} 编码后过大：{len(encoded)}")
    return _U32.pack(len(encoded)) + encoded


def _require_u32(value: int, label: str) -> int:
    """校验序列化整数范围。"""
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFFFFFFFF:
        raise ValueError(f"PALOC {label} 不是合法 u32：{value!r}")
    return value


def _require_category(value: int, index: int) -> int:
    """校验 category 只使用低字节。"""
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFF:
        raise ValueError(f"PALOC records[{index}].category 不是合法 u8：{value!r}")
    return value
