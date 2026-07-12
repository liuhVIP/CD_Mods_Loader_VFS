"""PALOC 本地化表的严格解析与确定性序列化。

当前已验证的红色沙漠 PALOC 由一个 ``u32`` 文件头和连续记录组成。转换器
只修改已有记录的 UTF-8 文本，完整保留记录顺序、前缀与尾索引，避免在尚未
确认新增/删除记录规则前生成不安全文件。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, replace

# PALOC 长度和整数均使用小端 u32。
_U32 = struct.Struct("<I")

# 防止损坏文件声明异常长度后造成超大切片或难以理解的越界错误。
_MAX_STRING_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class PalocRecord:
    """PALOC 中一条带稳定字符串键的本地化记录。"""

    prefix: int
    key: str
    value: str
    tail_index: int


@dataclass(frozen=True)
class PalocDocument:
    """一个完整 PALOC 文档。"""

    header: int
    records: tuple[PalocRecord, ...]

    def by_key(self) -> dict[str, PalocRecord]:
        """返回唯一键索引；重复键在解析阶段已经拒绝。"""
        return {record.key: record for record in self.records}

    def replace_values(self, values: dict[str, str]) -> "PalocDocument":
        """只替换已存在键的文本，禁止隐式新增记录。"""
        known = self.by_key()
        missing = sorted(set(values) - set(known))
        if missing:
            preview = ", ".join(missing[:5])
            raise ValueError(f"PALOC 不存在待修改键：{preview}")
        return PalocDocument(
            header=self.header,
            records=tuple(
                replace(record, value=values[record.key]) if record.key in values else record
                for record in self.records
            ),
        )


def parse_paloc(data: bytes) -> PalocDocument:
    """严格解析 PALOC，任何截断、重复键或非法 UTF-8 都会拒绝。"""
    if len(data) < _U32.size:
        raise ValueError("PALOC 文件不足 4 字节")
    header = _read_u32(data, 0, "文件头")
    cursor = _U32.size
    records: list[PalocRecord] = []
    seen_keys: set[str] = set()
    while cursor < len(data):
        record_offset = cursor
        prefix = _read_u32(data, cursor, f"记录@{record_offset} prefix")
        cursor += _U32.size
        key, cursor = _read_text(data, cursor, f"记录@{record_offset} key")
        value, cursor = _read_text(data, cursor, f"记录@{record_offset} value")
        tail_index = _read_u32(data, cursor, f"记录@{record_offset} tail_index")
        cursor += _U32.size
        if not key:
            raise ValueError(f"PALOC 记录@{record_offset} 的 key 为空")
        if key in seen_keys:
            raise ValueError(f"PALOC 存在重复 key：{key}")
        seen_keys.add(key)
        records.append(PalocRecord(prefix, key, value, tail_index))
    return PalocDocument(header=header, records=tuple(records))


def serialize_paloc(document: PalocDocument) -> bytes:
    """按原始记录顺序确定性写回 PALOC。"""
    output = bytearray(_U32.pack(_require_u32(document.header, "header")))
    seen_keys: set[str] = set()
    for index, record in enumerate(document.records):
        if not record.key:
            raise ValueError(f"PALOC records[{index}].key 为空")
        if record.key in seen_keys:
            raise ValueError(f"PALOC 存在重复 key：{record.key}")
        seen_keys.add(record.key)
        output.extend(_U32.pack(_require_u32(record.prefix, "prefix")))
        output.extend(_encode_text(record.key, f"records[{index}].key"))
        output.extend(_encode_text(record.value, f"records[{index}].value"))
        output.extend(_U32.pack(_require_u32(record.tail_index, "tail_index")))
    return bytes(output)


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
