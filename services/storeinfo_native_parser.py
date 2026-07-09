"""storeinfo.pabgb stock list 的原生解析/序列化工具。

该解析器服务 Format 3 `stock_data_list` writer。storeinfo 表一旦写坏，
游戏打开商店时很容易崩溃，所以本模块只接受已验证的 disc-0 stock record
布局；遇到未知 sub_data flag 或非空 effect_list 时直接拒绝。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field


class StoreinfoParseError(ValueError):
    """二进制不符合当前已验证 storeinfo stock record 布局。"""


# CD 1.11 后 disc-0 stock record 固定头长度为 110。
_HEAD_SIZE = 110
# value struct 内部尚未全部命名，按不透明字节原样保留。
_VGAP_SIZE = _HEAD_SIZE - 39
# stock list 的 u32 count 相对 entry payload 起点的偏移。
LIST_COUNT_PAYLOAD_OFFSET = 44


@dataclass
class StockRecord:
    """一条 disc-0 stock record。字段名沿用 Format 3 JSON 的通用命名。"""

    lookup_a: int = 0
    raw_a: int = 0
    raw_b: int = 0
    raw_c: int = 0
    raw_d: int = 0
    raw_e: int = 0
    flag_a: int = 0
    flag_b: int = 0
    flag_c: int = 0
    is_restore_item: int = 0
    const33: int = 1
    body: int = 0
    vgap: bytes = b"\x00" * _VGAP_SIZE
    sub_data: dict | None = None
    effect_list: list = field(default_factory=list)


class _Reader:
    """带游标的小端读取器。"""

    def __init__(self, data: bytes, pos: int = 0) -> None:
        self.data = data
        self.pos = pos

    def u8(self) -> int:
        """读取 u8。"""
        value = self.data[self.pos]
        self.pos += 1
        return value

    def u16(self) -> int:
        """读取小端 u16。"""
        value = struct.unpack_from("<H", self.data, self.pos)[0]
        self.pos += 2
        return value

    def u32(self) -> int:
        """读取小端 u32。"""
        value = struct.unpack_from("<I", self.data, self.pos)[0]
        self.pos += 4
        return value

    def u64(self) -> int:
        """读取小端 u64。"""
        value = struct.unpack_from("<Q", self.data, self.pos)[0]
        self.pos += 8
        return value

    def raw(self, size: int) -> bytes:
        """读取固定长度原始字节。"""
        value = self.data[self.pos:self.pos + size]
        if len(value) != size:
            raise StoreinfoParseError(f"unexpected EOF at {self.pos} (wanted {size} bytes)")
        self.pos += size
        return value


class _Writer:
    """带缓冲区的小端写入器。"""

    def __init__(self) -> None:
        self.out = bytearray()

    def u8(self, value: int) -> None:
        """写入 u8。"""
        self.out.append(value & 0xFF)

    def u16(self, value: int) -> None:
        """写入小端 u16。"""
        self.out += struct.pack("<H", value)

    def u32(self, value: int) -> None:
        """写入小端 u32。"""
        self.out += struct.pack("<I", value)

    def u64(self, value: int) -> None:
        """写入小端 u64。"""
        self.out += struct.pack("<Q", value)

    def raw(self, data: bytes) -> None:
        """写入原始字节。"""
        self.out += data


def read_stock_record(reader: _Reader) -> StockRecord:
    """从当前游标读取一条 disc-0 stock record。"""
    record = StockRecord()
    record.lookup_a = reader.u16()
    record.raw_a = reader.u64()
    record.raw_b = reader.u64()
    record.raw_c = reader.u32()
    record.raw_d = reader.u32()
    record.raw_e = reader.u32()
    record.flag_a = reader.u8()
    record.flag_b = reader.u8()
    record.flag_c = reader.u8()
    record.is_restore_item = reader.u8()
    record.const33 = reader.u8()
    if record.const33 != 1:
        raise StoreinfoParseError(
            f"record offset 34 const={record.const33}，预期 1，布局可能漂移"
        )
    record.body = reader.u32()
    record.vgap = reader.raw(_VGAP_SIZE)

    sub_flag = reader.u8()
    if sub_flag == 1:
        record.sub_data = {
            "flag": reader.u8(),
            "lookup_a": reader.u32(),
            "lookup_b": reader.u32(),
            "lookup_c": reader.u32(),
        }
    elif sub_flag == 0:
        record.sub_data = None
    else:
        raise StoreinfoParseError(f"sub_data optional flag is {sub_flag}")

    effect_count = reader.u32()
    if effect_count != 0:
        raise StoreinfoParseError(
            f"effect_list has {effect_count} element(s); element layout 未解码"
        )
    record.effect_list = []
    return record


def write_stock_record(writer: _Writer, record: StockRecord) -> None:
    """序列化一条 disc-0 stock record。"""
    if record.effect_list:
        raise StoreinfoParseError("cannot serialize a non-empty effect_list")
    if len(record.vgap) != _VGAP_SIZE:
        raise StoreinfoParseError(f"vgap 必须是 {_VGAP_SIZE} 字节，实际 {len(record.vgap)}")

    writer.u16(record.lookup_a)
    writer.u64(record.raw_a)
    writer.u64(record.raw_b)
    writer.u32(record.raw_c)
    writer.u32(record.raw_d)
    writer.u32(record.raw_e)
    writer.u8(record.flag_a)
    writer.u8(record.flag_b)
    writer.u8(record.flag_c)
    writer.u8(record.is_restore_item)
    writer.u8(record.const33)
    writer.u32(record.body)
    writer.raw(record.vgap)
    if record.sub_data is None:
        writer.u8(0)
    else:
        writer.u8(1)
        writer.u8(record.sub_data["flag"])
        writer.u32(record.sub_data["lookup_a"])
        writer.u32(record.sub_data["lookup_b"])
        writer.u32(record.sub_data["lookup_c"])
    writer.u32(0)


def parse_stock_list(data: bytes, count_offset: int) -> tuple[list[StockRecord], int, int]:
    """解析从 `count_offset` 开始的 stock list。"""
    reader = _Reader(data, count_offset)
    count = reader.u32()
    if not (0 <= count < 10000):
        raise StoreinfoParseError(f"stock record count 不可信：{count}")
    records = [read_stock_record(reader) for _ in range(count)]
    return records, count_offset, reader.pos


def serialize_stock_list(records: list[StockRecord]) -> bytes:
    """序列化完整 stock list：u32 count + records。"""
    writer = _Writer()
    writer.u32(len(records))
    for record in records:
        write_stock_record(writer, record)
    return bytes(writer.out)
