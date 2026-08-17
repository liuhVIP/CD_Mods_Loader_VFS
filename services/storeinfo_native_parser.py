"""StoreInfo 1.18 stock-list clean-room parser and serializer.

The current table stores each stock record as a fixed 114-byte prefix followed
by an optional 13-byte sub-record and an empty effect-list count. That yields
119-byte records without sub-data and 132-byte records with sub-data.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field


class StoreinfoParseError(ValueError):
    """The bytes do not match the verified StoreInfo 1.18 layout."""


# Regular StoreInfo entries place the stock count at payload +45. Writers use
# dynamic chain discovery because contribution stores have a longer head.
LIST_COUNT_PAYLOAD_OFFSET = 45
STOCK_FIXED_SIZE = 114
STOCK_RECORD_SIZE = 119
STOCK_RECORD_WITH_SUB_SIZE = 132
STOCK_CONST_OFFSET = 42
STOCK_ITEM_KEY_OFFSET = 43
STOCK_RAW_C_OFFSET = 18
STOCK_OPTIONAL_OFFSET = 114


@dataclass
class StockRecord:
    """One StoreInfo stock record using the current exported field names."""

    lookup_a: int = 0
    raw_a: int = 0
    raw_b: int = 0
    raw_c: int = 0
    order_index_113: int = 0xFFFFFFFF
    raw_d: int = 0
    raw_e: int = 0
    low_price_threshold_count_116: int = 0xFFFFFFFF
    flag_a: int = 0
    flag_b: int = 0
    flag_c: int = 0
    is_restore_item: int = 0
    body: int = 0
    value_lookup_a: int = 0
    disc: int = 0
    value_lookup_b: int = 0
    value_lookup_c: int = 0
    value_raw_a: int = 0
    value_raw_b: int = 0
    value_raw_d: int = 0
    value_raw_e: int = 0
    value_raw_f: int = 0
    value_raw_g: int = 0xFFFF
    value_raw_q: int = 0
    lookup_b: int = 0
    lookup_c: int = 0
    sub_data: dict | None = None
    effect_list: list = field(default_factory=list)


def read_stock_record(data: bytes, offset: int = 0) -> tuple[StockRecord, int]:
    """Read one current stock record and return ``(record, end_offset)``."""
    if offset < 0 or offset + STOCK_RECORD_SIZE > len(data):
        raise StoreinfoParseError(f"stock record at {offset} is truncated")
    if data[offset + STOCK_CONST_OFFSET] != 1:
        raise StoreinfoParseError(
            f"stock record at {offset} const={data[offset + STOCK_CONST_OFFSET]}，预期 1"
        )

    values = struct.unpack_from("<HQQIIIII4BBIIBIIQQQQQHIII", data, offset)
    if values[12] != 1:
        raise StoreinfoParseError(f"stock record at {offset} variant const={values[12]}")
    record = StockRecord(
        lookup_a=values[0],
        raw_a=values[1],
        raw_b=values[2],
        raw_c=values[3],
        order_index_113=values[4],
        raw_d=values[5],
        raw_e=values[6],
        low_price_threshold_count_116=values[7],
        flag_a=values[8],
        flag_b=values[9],
        flag_c=values[10],
        is_restore_item=values[11],
        body=values[13],
        value_lookup_a=values[14],
        disc=values[15],
        value_lookup_b=values[16],
        value_lookup_c=values[17],
        value_raw_a=values[18],
        value_raw_b=values[19],
        value_raw_d=values[20],
        value_raw_e=values[21],
        value_raw_f=values[22],
        value_raw_g=values[23],
        value_raw_q=values[24],
        lookup_b=values[25],
        lookup_c=values[26],
    )
    cursor = offset + STOCK_OPTIONAL_OFFSET
    optional_flag = data[cursor]
    cursor += 1
    if optional_flag == 1:
        if cursor + 13 > len(data):
            raise StoreinfoParseError(f"stock record at {offset} sub_data truncated")
        lookup_a, lookup_b, lookup_c, flag = struct.unpack_from("<IIIB", data, cursor)
        record.sub_data = {
            "flag": flag,
            "lookup_a": lookup_a,
            "lookup_b": lookup_b,
            "lookup_c": lookup_c,
        }
        cursor += 13
    elif optional_flag == 0:
        record.sub_data = None
    else:
        raise StoreinfoParseError(
            f"stock record at {offset} sub_data optional flag={optional_flag}"
        )

    if cursor + 4 > len(data):
        raise StoreinfoParseError(f"stock record at {offset} effect count truncated")
    effect_count = struct.unpack_from("<I", data, cursor)[0]
    if effect_count != 0:
        raise StoreinfoParseError(
            f"stock record at {offset} effect_list has {effect_count} element(s)"
        )
    cursor += 4
    return record, cursor


def write_stock_record(record: StockRecord) -> bytes:
    """Serialize one current stock record."""
    if record.effect_list:
        raise StoreinfoParseError("cannot serialize a non-empty effect_list")
    if not 0 <= record.disc <= 0xFF:
        raise StoreinfoParseError(f"disc={record.disc} 超出 u8")
    try:
        output = bytearray(
            struct.pack(
                "<HQQIIIII4BBIIBIIQQQQQHIII",
                record.lookup_a,
                record.raw_a,
                record.raw_b,
                record.raw_c,
                record.order_index_113,
                record.raw_d,
                record.raw_e,
                record.low_price_threshold_count_116,
                record.flag_a,
                record.flag_b,
                record.flag_c,
                record.is_restore_item,
                1,
                record.body,
                record.value_lookup_a,
                record.disc,
                record.value_lookup_b,
                record.value_lookup_c,
                record.value_raw_a,
                record.value_raw_b,
                record.value_raw_d,
                record.value_raw_e,
                record.value_raw_f,
                record.value_raw_g,
                record.value_raw_q,
                record.lookup_b,
                record.lookup_c,
            )
        )
    except struct.error as exc:
        raise StoreinfoParseError(f"stock record field out of range: {exc}") from exc
    if len(output) != STOCK_FIXED_SIZE:
        raise AssertionError(f"unexpected stock fixed size: {len(output)}")

    if record.sub_data is None:
        output.append(0)
    else:
        output.append(1)
        try:
            output += struct.pack(
                "<IIIB",
                int(record.sub_data["lookup_a"]),
                int(record.sub_data["lookup_b"]),
                int(record.sub_data["lookup_c"]),
                int(record.sub_data["flag"]),
            )
        except (KeyError, TypeError, ValueError, struct.error) as exc:
            raise StoreinfoParseError(f"invalid stock sub_data: {record.sub_data!r}") from exc
    output += struct.pack("<I", 0)
    return bytes(output)


def parse_stock_list(data: bytes, count_offset: int) -> tuple[list[StockRecord], int, int]:
    """Parse ``u32 count + stock records`` from ``count_offset``."""
    if count_offset < 0 or count_offset + 4 > len(data):
        raise StoreinfoParseError(f"stock count offset {count_offset} out of range")
    count = struct.unpack_from("<I", data, count_offset)[0]
    if not 0 <= count < 10000:
        raise StoreinfoParseError(f"stock record count 不可信：{count}")
    cursor = count_offset + 4
    records: list[StockRecord] = []
    for _ in range(count):
        record, cursor = read_stock_record(data, cursor)
        records.append(record)
    return records, count_offset, cursor


def serialize_stock_list(records: list[StockRecord]) -> bytes:
    """Serialize ``u32 count + stock records``."""
    output = bytearray(struct.pack("<I", len(records)))
    for record in records:
        output += write_stock_record(record)
    return bytes(output)
