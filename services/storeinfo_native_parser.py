"""StoreInfo 2.02.00 stock-table parser and serializer (clean-room).

布局来自 2026-09-17 对真实 2.02.00 归档的行为验证：先用内嵌 parser 作为黑盒，
再对每一条 entry/stock 记录的每个字节做扰动观测，最后用「整表恒等回环」验证。

Entry 结构（``key_size`` 由 ``storeinfo.staticinfoheader`` 的索引宽度决定）::

    key                 u16/u32
    name_length         u32
    name                utf-8（无 NUL 结尾）
    is_blocked          u8
    exchange_item_info_for_buy u32
    sell_count          u32
    sell_items          sell_count x u32
    sell_percents       u64
    store_type          u8
    price_count         u32
    price_increase_percent_list price_count x u64
    sellable_character_condition_logic u8
    pre_reset_extra_111 u8
    has_stock_condition u8
    enter_city_wagon_store_116 u8
    reset_hour          u32
    reset_day           u32
    buyable_stock_count u32
    sellable_stock_count u32
    sellable_type       u8
    stock_count         u32
    stock_data_list     stock_count x stock record
    sale_item_type_list count u32 + count x u8
    not_sale_item_type_list count u32 + count x u8
    custom_mesh_obb_max_length u32（f32 的原始比特）
    fixed_price         u8
    use_housing_gimmick u8
    reduce_price_by_looted_dead_body u8

Stock 记录结构::

    114 字节固定前缀（lookup_a .. lookup_c）
    sub_data 存在标志    u8
    sub_data           13 字节（可选）
    max_refill_condition_201 u64
    effect_list count   u32
    effect_list         count x 12 字节（u32 lookup + u64 raw）

即无 sub_data / 无 effect 时 127 字节，带 sub_data 时 140 字节。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field


class StoreinfoParseError(ValueError):
    """字节流不符合已验证的 StoreInfo 2.02.00 布局。"""


STOCK_PREFIX_SIZE = 114
STOCK_SUB_DATA_FLAG_OFFSET = 114
STOCK_SUB_DATA_SIZE = 13
STOCK_EFFECT_SIZE = 12
STOCK_RECORD_SIZE = 127
STOCK_RECORD_WITH_SUB_SIZE = 140
STOCK_COUNT_MAX = 20000
LIST_COUNT_MAX = 100000


@dataclass
class StockSubData:
    """``stock_data_list[i].sub_data``。"""

    flag: int = 0
    lookup_a: int = 0
    lookup_b: int = 0
    lookup_c: int = 0


@dataclass
class StockEffect:
    """``stock_data_list[i].effect_list[j]``。"""

    lookup: int = 0
    raw: int = 0


@dataclass
class StockPayload:
    """``stock_data_list[i].value.payload``（``type`` 是 disc 的派生态）。"""

    body: int = 0


@dataclass
class StockValue:
    """``stock_data_list[i].value``。"""

    disc: int = 0
    lookup_a: int = 0
    lookup_b: int = 0
    lookup_c: int = 0
    raw_a: int = 0
    raw_b: int = 0
    raw_d: int = 0
    raw_e: int = 0
    raw_f: int = 0
    raw_g: int = 0xFFFF
    raw_q: int = 0
    payload: StockPayload = field(default_factory=StockPayload)


@dataclass
class StockRecord:
    """一条 StoreInfo 库存记录（字段名与 DMM 导出的 JSON 字段一致）。"""

    lookup_a: int = 0
    raw_a: int = 0
    raw_b: int = 0
    raw_c: int = 0
    low_price_threshold_count_116: int = 0xFFFFFFFF
    raw_d: int = 0
    raw_e: int = 0
    order_index_113: int = 0xFFFFFFFF
    flag_a: int = 0
    flag_b: int = 0
    flag_c: int = 0
    is_restore_item: int = 0
    variant_tag: int = 1
    lookup_b: int = 0
    lookup_c: int = 0
    value: StockValue = field(default_factory=StockValue)
    sub_data: StockSubData | None = None
    max_refill_condition_201: int = 0
    effect_list: list[StockEffect] = field(default_factory=list)


@dataclass
class StoreEntry:
    """一条 StoreInfo 商店记录。"""

    key: int = 0
    string_key: str = ""
    is_blocked: int = 0
    exchange_item_info_for_buy: int = 0
    exchange_item_info_list_for_sell: list[int] = field(default_factory=list)
    sell_percents: int = 0
    store_type: int = 0
    price_increase_percent_list: list[int] = field(default_factory=list)
    sellable_character_condition_logic: int = 0
    pre_reset_extra_111: int = 0
    has_stock_condition: int = 0
    enter_city_wagon_store_116: int = 0
    reset_hour: int = 0
    reset_day: int = 0
    buyable_stock_count: int = 0
    sellable_stock_count: int = 0
    sellable_type: int = 0
    stock_data_list: list[StockRecord] = field(default_factory=list)
    sale_item_type_list: list[int] = field(default_factory=list)
    not_sale_item_type_list: list[int] = field(default_factory=list)
    custom_mesh_obb_max_length: int = 0
    fixed_price: int = 0
    use_housing_gimmick: int = 0
    reduce_price_by_looted_dead_body: int = 0


def _require(data: bytes, offset: int, size: int, what: str) -> None:
    if offset < 0 or offset + size > len(data):
        raise StoreinfoParseError(f"{what} 越界：offset={offset} size={size} len={len(data)}")


def read_stock_record(data: bytes, offset: int = 0) -> tuple[StockRecord, int]:
    """读取一条库存记录，返回 ``(record, end_offset)``。"""
    _require(data, offset, STOCK_RECORD_SIZE, "stock record")
    cursor = offset
    record = StockRecord(
        lookup_a=struct.unpack_from("<H", data, cursor)[0],
        raw_a=struct.unpack_from("<Q", data, cursor + 2)[0],
        raw_b=struct.unpack_from("<Q", data, cursor + 10)[0],
        raw_c=struct.unpack_from("<I", data, cursor + 18)[0],
        low_price_threshold_count_116=struct.unpack_from("<I", data, cursor + 22)[0],
        raw_d=struct.unpack_from("<I", data, cursor + 26)[0],
        raw_e=struct.unpack_from("<I", data, cursor + 30)[0],
        order_index_113=struct.unpack_from("<I", data, cursor + 34)[0],
        flag_a=data[cursor + 38],
        flag_b=data[cursor + 39],
        flag_c=data[cursor + 40],
        is_restore_item=data[cursor + 41],
        variant_tag=data[cursor + 42],
        lookup_b=struct.unpack_from("<I", data, cursor + 106)[0],
        lookup_c=struct.unpack_from("<I", data, cursor + 110)[0],
        value=StockValue(
            disc=data[cursor + 51],
            lookup_a=struct.unpack_from("<I", data, cursor + 52)[0],
            lookup_b=struct.unpack_from("<I", data, cursor + 56)[0],
            lookup_c=struct.unpack_from("<I", data, cursor + 60)[0],
            raw_a=struct.unpack_from("<I", data, cursor + 64)[0],
            raw_b=struct.unpack_from("<Q", data, cursor + 68)[0],
            raw_d=struct.unpack_from("<Q", data, cursor + 76)[0],
            raw_e=struct.unpack_from("<Q", data, cursor + 84)[0],
            raw_f=struct.unpack_from("<Q", data, cursor + 92)[0],
            raw_g=struct.unpack_from("<H", data, cursor + 100)[0],
            raw_q=struct.unpack_from("<Q", data, cursor + 43)[0],
            payload=StockPayload(body=struct.unpack_from("<I", data, cursor + 102)[0]),
        ),
    )
    cursor += STOCK_PREFIX_SIZE
    sub_flag = data[cursor]
    cursor += 1
    if sub_flag == 1:
        _require(data, cursor, STOCK_SUB_DATA_SIZE, "stock sub_data")
        record.sub_data = StockSubData(
            lookup_a=struct.unpack_from("<I", data, cursor)[0],
            flag=data[cursor + 4],
            lookup_b=struct.unpack_from("<I", data, cursor + 5)[0],
            lookup_c=struct.unpack_from("<I", data, cursor + 9)[0],
        )
        cursor += STOCK_SUB_DATA_SIZE
    record.max_refill_condition_201 = struct.unpack_from("<Q", data, cursor)[0]
    cursor += 8
    effect_count = struct.unpack_from("<I", data, cursor)[0]
    cursor += 4
    if effect_count:
        _require(data, cursor, effect_count * STOCK_EFFECT_SIZE, "stock effect_list")
        record.effect_list = [
            StockEffect(
                lookup=struct.unpack_from("<I", data, cursor + index * STOCK_EFFECT_SIZE)[0],
                raw=struct.unpack_from("<Q", data, cursor + index * STOCK_EFFECT_SIZE + 4)[0],
            )
            for index in range(effect_count)
        ]
        cursor += effect_count * STOCK_EFFECT_SIZE
    return record, cursor


def write_stock_record(record: StockRecord) -> bytes:
    """序列化一条库存记录。"""
    value = record.value
    output = bytearray()
    try:
        output += struct.pack(
            "<HQQIIIII3BBBQBIIIIQQQQHIII",
            record.lookup_a,
            record.raw_a,
            record.raw_b,
            record.raw_c,
            record.low_price_threshold_count_116,
            record.raw_d,
            record.raw_e,
            record.order_index_113,
            record.flag_a,
            record.flag_b,
            record.flag_c,
            record.is_restore_item,
            record.variant_tag,
            value.raw_q,
            value.disc,
            value.lookup_a,
            value.lookup_b,
            value.lookup_c,
            value.raw_a,
            value.raw_b,
            value.raw_d,
            value.raw_e,
            value.raw_f,
            value.raw_g,
            value.payload.body,
            record.lookup_b,
            record.lookup_c,
        )
    except struct.error as exc:
        raise StoreinfoParseError(f"库存记录字段越界：{exc}") from exc
    if len(output) != STOCK_PREFIX_SIZE:
        raise StoreinfoParseError(f"库存记录前缀长度 {len(output)} != {STOCK_PREFIX_SIZE}")

    if record.sub_data is None:
        output.append(0)
    else:
        output.append(1)
        try:
            output += struct.pack(
                "<IBII",
                record.sub_data.lookup_a,
                record.sub_data.flag,
                record.sub_data.lookup_b,
                record.sub_data.lookup_c,
            )
        except struct.error as exc:
            raise StoreinfoParseError(f"库存记录 sub_data 越界：{exc}") from exc
    try:
        output += struct.pack("<Q", record.max_refill_condition_201)
        output += struct.pack("<I", len(record.effect_list))
        for effect in record.effect_list:
            output += struct.pack("<I", effect.lookup) + struct.pack("<Q", effect.raw)
    except struct.error as exc:
        raise StoreinfoParseError(f"库存记录尾部字段越界：{exc}") from exc
    return bytes(output)


def parse_stock_list(data: bytes, count_offset: int) -> tuple[list[StockRecord], int, int]:
    """解析 ``u32 count + 库存记录链``，返回 ``(records, count_offset, end_offset)``。"""
    _require(data, count_offset, 4, "stock count")
    count = struct.unpack_from("<I", data, count_offset)[0]
    if not 0 <= count < STOCK_COUNT_MAX:
        raise StoreinfoParseError(f"库存记录数不可信：{count}")
    cursor = count_offset + 4
    records: list[StockRecord] = []
    for _ in range(count):
        record, cursor = read_stock_record(data, cursor)
        records.append(record)
    return records, count_offset, cursor


def serialize_stock_list(records: list[StockRecord]) -> bytes:
    """序列化 ``u32 count + 库存记录链``。"""
    output = bytearray(struct.pack("<I", len(records)))
    for record in records:
        output += write_stock_record(record)
    return bytes(output)


def _read_u8_list(data: bytes, offset: int, what: str) -> tuple[list[int], int]:
    _require(data, offset, 4, what)
    count = struct.unpack_from("<I", data, offset)[0]
    if not 0 <= count < LIST_COUNT_MAX:
        raise StoreinfoParseError(f"{what} 数量不可信：{count}")
    offset += 4
    _require(data, offset, count, what)
    return list(data[offset:offset + count]), offset + count


def _read_u32_list(data: bytes, offset: int, what: str) -> tuple[list[int], int]:
    _require(data, offset, 4, what)
    count = struct.unpack_from("<I", data, offset)[0]
    if not 0 <= count < LIST_COUNT_MAX:
        raise StoreinfoParseError(f"{what} 数量不可信：{count}")
    offset += 4
    _require(data, offset, count * 4, what)
    return [struct.unpack_from("<I", data, offset + index * 4)[0] for index in range(count)], offset + count * 4


def _read_u64_list(data: bytes, offset: int, what: str) -> tuple[list[int], int]:
    _require(data, offset, 4, what)
    count = struct.unpack_from("<I", data, offset)[0]
    if not 0 <= count < LIST_COUNT_MAX:
        raise StoreinfoParseError(f"{what} 数量不可信：{count}")
    offset += 4
    _require(data, offset, count * 8, what)
    return [struct.unpack_from("<Q", data, offset + index * 8)[0] for index in range(count)], offset + count * 8


def parse_storeinfo_entry(entry: bytes, key_size: int = 2) -> StoreEntry:
    """解析一条 storeinfo entry。"""
    _require(entry, 0, key_size + 4, "store entry 头")
    cursor = 0
    key = int.from_bytes(entry[cursor:cursor + key_size], "little")
    cursor += key_size
    name_length = struct.unpack_from("<I", entry, cursor)[0]
    cursor += 4
    _require(entry, cursor, name_length, "store entry 名称")
    try:
        name = entry[cursor:cursor + name_length].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StoreinfoParseError(f"store entry {key} 名称不是 UTF-8") from exc
    cursor += name_length
    parsed = StoreEntry(key=key, string_key=name)
    _require(entry, cursor, 1 + 4, "store entry is_blocked")
    parsed.is_blocked = entry[cursor]
    cursor += 1
    parsed.exchange_item_info_for_buy = struct.unpack_from("<I", entry, cursor)[0]
    cursor += 4
    parsed.exchange_item_info_list_for_sell, cursor = _read_u32_list(
        entry, cursor, "exchange_item_info_list_for_sell"
    )
    _require(entry, cursor, 9, "store entry sell_percents")
    parsed.sell_percents = struct.unpack_from("<Q", entry, cursor)[0]
    cursor += 8
    parsed.store_type = entry[cursor]
    cursor += 1
    parsed.price_increase_percent_list, cursor = _read_u64_list(
        entry, cursor, "price_increase_percent_list"
    )
    _require(entry, cursor, 4 + 16 + 1 + 4, "store entry 标量块")
    parsed.sellable_character_condition_logic = entry[cursor]
    parsed.pre_reset_extra_111 = entry[cursor + 1]
    parsed.has_stock_condition = entry[cursor + 2]
    parsed.enter_city_wagon_store_116 = entry[cursor + 3]
    cursor += 4
    parsed.reset_hour = struct.unpack_from("<I", entry, cursor)[0]
    parsed.reset_day = struct.unpack_from("<I", entry, cursor + 4)[0]
    parsed.buyable_stock_count = struct.unpack_from("<I", entry, cursor + 8)[0]
    parsed.sellable_stock_count = struct.unpack_from("<I", entry, cursor + 12)[0]
    parsed.sellable_type = entry[cursor + 16]
    cursor += 17
    records, _count_offset, cursor = parse_stock_list(entry, cursor)
    parsed.stock_data_list = records
    parsed.sale_item_type_list, cursor = _read_u8_list(entry, cursor, "sale_item_type_list")
    parsed.not_sale_item_type_list, cursor = _read_u8_list(
        entry, cursor, "not_sale_item_type_list"
    )
    _require(entry, cursor, 4 + 1 + 1 + 1, "store entry 尾部")
    parsed.custom_mesh_obb_max_length = struct.unpack_from("<I", entry, cursor)[0]
    cursor += 4
    parsed.fixed_price = entry[cursor]
    parsed.use_housing_gimmick = entry[cursor + 1]
    parsed.reduce_price_by_looted_dead_body = entry[cursor + 2]
    cursor += 3
    if cursor != len(entry):
        raise StoreinfoParseError(
            f"store entry {key} 解析后仍有 {len(entry) - cursor} 字节未消费"
        )
    return parsed


def serialize_storeinfo_entry(entry: StoreEntry, key_size: int = 2) -> bytes:
    """序列化一条 storeinfo entry。"""
    name = entry.string_key.encode("utf-8")
    output = bytearray()
    output += int(entry.key).to_bytes(key_size, "little")
    output += struct.pack("<I", len(name))
    output += name
    output += struct.pack("<B", entry.is_blocked)
    output += struct.pack("<I", entry.exchange_item_info_for_buy)
    output += struct.pack("<I", len(entry.exchange_item_info_list_for_sell))
    for item in entry.exchange_item_info_list_for_sell:
        output += struct.pack("<I", item)
    output += struct.pack("<Q", entry.sell_percents)
    output += struct.pack("<B", entry.store_type)
    output += struct.pack("<I", len(entry.price_increase_percent_list))
    for price in entry.price_increase_percent_list:
        output += struct.pack("<Q", price)
    output += struct.pack(
        "<4B", entry.sellable_character_condition_logic,
        entry.pre_reset_extra_111,
        entry.has_stock_condition,
        entry.enter_city_wagon_store_116,
    )
    output += struct.pack(
        "<IIIIB",
        entry.reset_hour,
        entry.reset_day,
        entry.buyable_stock_count,
        entry.sellable_stock_count,
        entry.sellable_type,
    )
    output += serialize_stock_list(entry.stock_data_list)
    output += struct.pack("<I", len(entry.sale_item_type_list))
    output += bytes(value & 0xFF for value in entry.sale_item_type_list)
    output += struct.pack("<I", len(entry.not_sale_item_type_list))
    output += bytes(value & 0xFF for value in entry.not_sale_item_type_list)
    output += struct.pack(
        "<I3B",
        entry.custom_mesh_obb_max_length,
        entry.fixed_price,
        entry.use_housing_gimmick,
        entry.reduce_price_by_looted_dead_body,
    )
    return bytes(output)


def stock_record_to_json(record: StockRecord) -> dict:
    """转成 DMM 导出的 stock record JSON。"""
    value = record.value
    return {
        "lookup_a": record.lookup_a,
        "raw_a": record.raw_a,
        "raw_b": record.raw_b,
        "raw_c": record.raw_c,
        "low_price_threshold_count_116": record.low_price_threshold_count_116,
        "raw_d": record.raw_d,
        "raw_e": record.raw_e,
        "order_index_113": record.order_index_113,
        "flag_a": record.flag_a,
        "flag_b": record.flag_b,
        "flag_c": record.flag_c,
        "is_restore_item": record.is_restore_item,
        "lookup_b": record.lookup_b,
        "lookup_c": record.lookup_c,
        "max_refill_condition_201": record.max_refill_condition_201,
        "sub_data": None
        if record.sub_data is None
        else {
            "flag": record.sub_data.flag,
            "lookup_a": record.sub_data.lookup_a,
            "lookup_b": record.sub_data.lookup_b,
            "lookup_c": record.sub_data.lookup_c,
        },
        "effect_list": [
            {"lookup": effect.lookup, "raw": effect.raw} for effect in record.effect_list
        ],
        "value": {
            "disc": value.disc,
            "lookup_a": value.lookup_a,
            "lookup_b": value.lookup_b,
            "lookup_c": value.lookup_c,
            "payload": {"body": value.payload.body, "type": f"Disc{value.disc}"},
            "raw_a": value.raw_a,
            "raw_b": value.raw_b,
            "raw_d": value.raw_d,
            "raw_e": value.raw_e,
            "raw_f": value.raw_f,
            "raw_g": value.raw_g,
            "raw_q": value.raw_q,
        },
    }


def _int_field(mapping: dict, field: str, default: int = 0) -> int:
    value = mapping.get(field, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise StoreinfoParseError(f"{field}={value!r} 不是整数")
    return value


def stock_record_from_json(value: object) -> StockRecord:
    """从 DMM/AI 导出的 stock record JSON 构造记录。"""
    if not isinstance(value, dict):
        raise StoreinfoParseError("stock record 必须是 object")
    nested = value.get("value")
    if not isinstance(nested, dict):
        raise StoreinfoParseError("stock record.value 必须是 object")
    payload = nested.get("payload")
    if not isinstance(payload, dict):
        raise StoreinfoParseError("stock record.value.payload 必须是 object")
    disc = _int_field(nested, "disc")
    if not 0 <= disc <= 0xFF:
        raise StoreinfoParseError(f"stock record.value.disc={disc} 超出 u8")
    effects_raw = value.get("effect_list") or []
    if not isinstance(effects_raw, list):
        raise StoreinfoParseError("stock record.effect_list 必须是数组")
    effects = []
    for effect in effects_raw:
        if not isinstance(effect, dict):
            raise StoreinfoParseError("stock record.effect_list 元素必须是 object")
        effects.append(
            StockEffect(lookup=_int_field(effect, "lookup"), raw=_int_field(effect, "raw"))
        )
    return StockRecord(
        lookup_a=_int_field(value, "lookup_a"),
        raw_a=_int_field(value, "raw_a"),
        raw_b=_int_field(value, "raw_b"),
        raw_c=_int_field(value, "raw_c"),
        low_price_threshold_count_116=_int_field(
            value, "low_price_threshold_count_116", 0xFFFFFFFF
        ),
        raw_d=_int_field(value, "raw_d"),
        raw_e=_int_field(value, "raw_e"),
        order_index_113=_int_field(value, "order_index_113", 0xFFFFFFFF),
        flag_a=_int_field(value, "flag_a"),
        flag_b=_int_field(value, "flag_b"),
        flag_c=_int_field(value, "flag_c"),
        is_restore_item=_int_field(value, "is_restore_item"),
        variant_tag=_int_field(value, "variant_tag", 1),
        lookup_b=_int_field(value, "lookup_b"),
        lookup_c=_int_field(value, "lookup_c"),
        value=StockValue(
            disc=disc,
            lookup_a=_int_field(nested, "lookup_a"),
            lookup_b=_int_field(nested, "lookup_b"),
            lookup_c=_int_field(nested, "lookup_c"),
            raw_a=_int_field(nested, "raw_a"),
            raw_b=_int_field(nested, "raw_b"),
            raw_d=_int_field(nested, "raw_d"),
            raw_e=_int_field(nested, "raw_e"),
            raw_f=_int_field(nested, "raw_f"),
            raw_g=_int_field(nested, "raw_g", 0xFFFF),
            raw_q=_int_field(nested, "raw_q", _int_field(payload, "body")),
            payload=StockPayload(body=_int_field(payload, "body")),
        ),
        sub_data=sub_data_from_json(value.get("sub_data")),
        max_refill_condition_201=_int_field(value, "max_refill_condition_201"),
        effect_list=effects,
    )


def sub_data_from_json(raw: object) -> StockSubData | None:
    """把 JSON 的 sub_data 投影成 ``StockSubData``。"""
    if raw is None or raw is False:
        return None
    if isinstance(raw, StockSubData):
        return raw
    if not isinstance(raw, dict):
        raise StoreinfoParseError("stock record.sub_data 必须是 object 或 null")
    return StockSubData(
        flag=_int_field(raw, "flag"),
        lookup_a=_int_field(raw, "lookup_a"),
        lookup_b=_int_field(raw, "lookup_b"),
        lookup_c=_int_field(raw, "lookup_c"),
    )
