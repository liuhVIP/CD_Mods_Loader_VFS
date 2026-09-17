"""PABGB 记录级通用读/写编解码原语。

2.02.00 的语义表（dropsetinfo / itemgroupinfo / multichangeinfo /
characterinfo / skill）都由同一套小端 wire 原语组成：定宽标量、`u32 长度 +
原字节` 的 CString、`u32 count + 元素` 的 CArray、以及
`u8 category + u64 index + CString default` 的 LocalizableString。

这里只放与具体表无关的读写器，避免每张表各自复制一套边界检查。
"""

from __future__ import annotations

import struct


class RecordParseError(ValueError):
    """记录无法按当前 schema 解析。"""


_U16 = struct.Struct("<H")
_U32 = struct.Struct("<I")
_U64 = struct.Struct("<Q")
_I64 = struct.Struct("<q")


class Reader:
    """带边界检查的小端读取器。"""

    def __init__(self, data: bytes, pos: int = 0, end: int | None = None) -> None:
        self.data = data
        self.pos = pos
        self.end = len(data) if end is None else end

    def _need(self, size: int, label: str) -> None:
        if self.pos + size > self.end:
            raise RecordParseError(f"{label} 越界（pos={self.pos}, need={size}）")

    def u8(self) -> int:
        self._need(1, "u8")
        value = self.data[self.pos]
        self.pos += 1
        return value

    def u16(self) -> int:
        self._need(2, "u16")
        value = _U16.unpack_from(self.data, self.pos)[0]
        self.pos += 2
        return value

    def u32(self) -> int:
        self._need(4, "u32")
        value = _U32.unpack_from(self.data, self.pos)[0]
        self.pos += 4
        return value

    def u64(self) -> int:
        self._need(8, "u64")
        value = _U64.unpack_from(self.data, self.pos)[0]
        self.pos += 8
        return value

    def i64(self) -> int:
        self._need(8, "i64")
        value = _I64.unpack_from(self.data, self.pos)[0]
        self.pos += 8
        return value

    def cstring(self) -> str:
        length = self.u32()
        if length > 100_000:
            raise RecordParseError("CString 长度不可信")
        self._need(length, "CString")
        raw = self.data[self.pos:self.pos + length]
        self.pos += length
        return raw.decode("utf-8", errors="replace")

    def skip(self, size: int) -> None:
        self._need(size, "skip")
        self.pos += size


class Writer:
    """小端写入缓冲。"""

    def __init__(self) -> None:
        self.buf = bytearray()

    def u8(self, value: int) -> None:
        self.buf.append(value & 0xFF)

    def u16(self, value: int) -> None:
        self.buf += _U16.pack(value & 0xFFFF)

    def u32(self, value: int) -> None:
        self.buf += _U32.pack(value & 0xFFFFFFFF)

    def u64(self, value: int) -> None:
        self.buf += _U64.pack(value & 0xFFFFFFFFFFFFFFFF)

    def i64(self, value: int) -> None:
        self.buf += _I64.pack(value)

    def cstring(self, value: str) -> None:
        raw = value.encode("utf-8")
        self.buf += _U32.pack(len(raw))
        self.buf += raw

    def raw(self, value: bytes) -> None:
        self.buf += value


def require_uint(value: object, label: str) -> int:
    """校验无符号整数字段。"""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RecordParseError(f"{label} 必须是非负整数")
    return value


def require_int(value: object, label: str) -> int:
    """校验整数字段（允许负数）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise RecordParseError(f"{label} 必须是整数")
    return value
