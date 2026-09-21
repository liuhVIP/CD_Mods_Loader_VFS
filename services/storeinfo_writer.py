"""StoreInfo Format 3 writer for the 2.02.00 table layout.

写入模型：按 ``storeinfo.staticinfoheader`` 的 offset 索引取出目标 entry，
用 ``storeinfo_native_parser`` 结构化解析 → 在内存里按 intent 改字段 → 重新序列化
→ 整表拼接 → 按 entry delta 重建 header offset。

2026-09-17 起不再做「用当前原版模板重放」：2.02.00 的 stock 记录布局已完整解析，
模组导出的 stock record JSON 可以逐字段直接写回，产物与 DMM 内嵌 parser
（``dmm_parser.apply_intents``）逐字节一致。
"""

from __future__ import annotations

import logging
import struct
from collections import defaultdict
from dataclasses import fields, is_dataclass
from typing import Any

from cdmm.services.pab_table_service import build_entry_bounds, parse_pabgh_index
from cdmm.services.storeinfo_native_parser import (
    StockEffect,
    StockPayload,
    StockRecord,
    StockValue,
    StoreEntry,
    StoreinfoParseError,
    parse_storeinfo_entry,
    serialize_storeinfo_entry,
    stock_record_from_json,
    sub_data_from_json,
)

logger = logging.getLogger(__name__)

# u32 计数的 entry 级列表字段；这些字段允许整体 set 或按下标 set。
_ENTRY_LIST_FIELDS = frozenset(
    {
        "exchange_item_info_list_for_sell",
        "price_increase_percent_list",
        "sale_item_type_list",
        "not_sale_item_type_list",
    }
)

# 数组整体替换 / 追加的别名：旧 Format 3 导出把 sell 列表写成
# ``_exchangeItemInfoListForSell``，语义上等同于 stock_data_list 的写法。
# StoreEntry 的全部字段名；`add` 只允许作用在这些整数标量上。
_ENTRY_FIELDS = frozenset(item.name for item in fields(StoreEntry))

_STOCK_LIST_FIELDS = frozenset(
    {"stock_data_list", "exchange_item_info_list_for_sell", "_exchangeItemInfoListForSell"}
)
_STOCK_LIST_ALIASES = {
    "_exchangeItemInfoListForSell": "stock_data_list",
    "exchange_item_info_list_for_sell": "stock_data_list",
}


class StoreinfoWriteRefused(ValueError):
    """目标无法在已验证的 StoreInfo 布局内改写。"""


class _Token:
    __slots__ = ("name", "index")

    def __init__(self, name: str, index: int | None = None) -> None:
        self.name = name
        self.index = index


def normalize_store_entry_stock_rows(entry: StoreEntry) -> int:
    """按 DMM 语义恢复 stock 表的「行序 + 按行计票」不变量。

    原版 2.02.00 的 436 条 store entry 全部满足以下三条不变量（已逐条核验）：

    * ``stock_data_list[i].raw_d == i``：``raw_d`` 是行序（DMM 日志把它写作
      "next free row ordinal"）。模组导出时按各自看到的原版行数算这个序号，
      所以两个模组给同一家店补货时会撞号；游戏的商品格子按位置排列，购买处理
      按行序找行，撞号就会出现「格子显示模组物品、买到的却是另一行的原版物品」。
    * ``buyable_stock_count == 行中 flag_c != 0 的数量``
    * ``sellable_stock_count == 行中 flag_b != 0 的数量``

    DMM 从 2.3.3 起在所有模组挂载完成后统一重排：行按位置编号，可买/可卖数量
    由行自身的标记统计，而不是听最后一个写这两个字段的模组。这里复刻同一语义。
    返回被改写的字段数，便于日志与测试断言。
    """
    changed = 0
    for index, record in enumerate(entry.stock_data_list):
        if record.raw_d != index:
            record.raw_d = index
            changed += 1
    buyable = sum(1 for record in entry.stock_data_list if record.flag_c)
    sellable = sum(1 for record in entry.stock_data_list if record.flag_b)
    if entry.buyable_stock_count != buyable:
        entry.buyable_stock_count = buyable
        changed += 1
    if entry.sellable_stock_count != sellable:
        entry.sellable_stock_count = sellable
        changed += 1
    return changed


def _tail_declared_slots(entry: StoreEntry) -> tuple[int, list[int]]:
    """定位「已带模组声明货架位置」的尾部行块，返回 ``(块起始下标, 槽位列表)``。

    只能按载荷识别，不能按 intent 识别：计划层会把同一字段的整表 ``set`` 与
    ``array_append`` 合并成一个整表 ``set``（2026-09-19 探针实测：All Gear 的
    621 行 + OP Arsenal 的 6 次追加最终合成一个 627 行的 set intent），写入器
    根本看不到 append。追加行的特征因此是纯数据面的：

    * 位于列表尾部连续区段；
    * 该区段每一行的 ``raw_d`` 都**不等于**自己的下标，而它前面的行全部等于；
    * ``raw_d`` 仍是模组导出时算出的货架位置（OP Arsenal 写 ``40..45``，即
      「原版 40 行货架之后」），因此全部为正、彼此不重复、且都落在块起点之前。

    不满足时返回 ``(-1, [])``，调用方退化为 DMM 的纯按位置编号。``raw_d = 0``
    是导出工具的缺省值（第三方 ``All Craft Material ... - Append`` 变体的追加行
    全是 0），不能当成「插到货架最前面」，所以被显式排除。
    """
    rows = entry.stock_data_list
    total = len(rows)
    start = total
    while start > 0 and rows[start - 1].raw_d != start - 1:
        start -= 1
    if start >= total:
        return -1, []
    slots = [record.raw_d for record in rows[start:]]
    if any(slot <= 0 for slot in slots):
        return -1, []
    if len(set(slots)) != len(slots):
        return -1, []
    if any(slot >= start for slot in slots):
        return -1, []
    if slots != list(range(slots[0], slots[0] + len(slots))):
        return -1, []
    return start, slots


def reposition_rows_to_declared_slots(entry: StoreEntry) -> int:
    """把模组追加的库存行插回它自己声明的货架位置，返回被移动的行数。

    动机（2026-09-19 实机反馈两轮）：OP Arsenal 追加的 6 行写 ``raw_d=40..45``
    （「原版 40 行之后」）。同一家店被 ``All Craft Material`` 整表 set 成 621 行后，

    * 保留原 ``raw_d`` 会与本店第 40~45 行撞号 —— 格子显示模组物品，购买处理按
      行序找行，于是买到原版物品（第二轮反馈）；
    * 单纯按位置重编号会把这 6 行推到货架最末尾（第 622~626 格），实机表现是
      **店里翻不到**，等于模组不生效（第三轮反馈）。

    这里取两者之间的正解：按模组声明的位置把它们插回去（后面的行整体后移），再由
    ``normalize_store_entry_stock_rows`` 统一按位置重编号。

    * ``raw_d`` 仍然等于行下标（原版不变量），购买处理能找到正确的行；
    * 追加商品的货架位置与「原版货架 + 本模组」时一致，即模组作者预期并验证过的位置；
    * 该结果**与加载顺序无关**，与 DMM 2.3.3 修这个碰撞时追求的 "either load order"
      一致 —— 不管这家店被别的模组撑到多少行，本模组商品都落在同一批格子上。
    """
    start, slots = _tail_declared_slots(entry)
    if start < 0:
        return 0
    rows = entry.stock_data_list
    for offset, slot in enumerate(slots):
        record = rows.pop(start + offset)
        rows.insert(slot, record)
    return len(slots)


def build_storeinfo_changes(
    vanilla_body: bytes,
    vanilla_header: bytes,
    intents: list[Any],
) -> tuple[list[dict], dict | None]:
    """在内存里应用全部 StoreInfo 编辑，并只重建一次 PABGH。"""
    key_size, offsets = parse_pabgh_index(vanilla_header, "storeinfo")
    if key_size not in (2, 4) or not offsets:
        raise StoreinfoWriteRefused("storeinfo.staticinfoheader 索引无效")
    bounds = build_entry_bounds(vanilla_body, key_size, offsets)
    by_name = {item[2]: key for key, item in bounds.items() if item[2]}

    grouped: dict[int, list[Any]] = defaultdict(list)
    for intent in intents:
        grouped[_resolve_intent_key(intent, offsets, by_name)].append(intent)

    replacements: dict[int, bytes] = {}
    deltas: list[tuple[int, int]] = []
    for key, entry_intents in grouped.items():
        if key not in bounds:
            raise StoreinfoWriteRefused(f"store entry {key} 边界无效")
        start, end, _name, _name_end = bounds[key]
        original_entry = vanilla_body[start:end]
        try:
            parsed = parse_storeinfo_entry(original_entry, key_size)
        except StoreinfoParseError as exc:
            raise StoreinfoWriteRefused(
                f"store entry {key} 无法按 2.02 布局解析：{exc}"
            ) from exc
        for intent in entry_intents:
            _apply_intent(parsed, intent, key)
        moved = reposition_rows_to_declared_slots(parsed)
        if moved:
            logger.info(
                "storeinfo writer: entry@%d(%s) 把 %d 条追加行插回声明的货架位置",
                start,
                parsed.string_key or key,
                moved,
            )
        normalized = normalize_store_entry_stock_rows(parsed)
        if normalized:
            logger.info(
                "storeinfo writer: entry@%d(%s) 规范化 %d 个字段（raw_d 行序 / 可买可卖计数）",
                start,
                parsed.string_key or key,
                normalized,
            )
        try:
            patched_entry = serialize_storeinfo_entry(parsed, key_size)
        except (StoreinfoParseError, struct.error) as exc:
            raise StoreinfoWriteRefused(f"store entry {key} 序列化失败：{exc}") from exc
        if patched_entry == original_entry:
            continue
        replacements[start] = patched_entry
        deltas.append((start, len(patched_entry) - len(original_entry)))

    if not replacements:
        return [], None

    patched_body = bytearray(vanilla_body)
    for start in sorted(replacements, reverse=True):
        end = next(item[1] for item in bounds.values() if item[0] == start)
        patched_body[start:end] = replacements[start]
        logger.info(
            "storeinfo writer: entry@%d 改写 %d -> %d 字节",
            start,
            end - start,
            len(replacements[start]),
        )

    patched_header = _rebuild_header(vanilla_header, key_size, deltas)
    body_change = {
        "offset": 0,
        "original": vanilla_body.hex(),
        "patched": bytes(patched_body).hex(),
        "label": "storeinfo whole-table rebuild",
    }
    header_change = None
    if patched_header != vanilla_header:
        header_change = {
            "offset": 0,
            "original": vanilla_header.hex(),
            "patched": patched_header.hex(),
            "label": "storeinfo header offset rebuild",
        }
    return [body_change], header_change


def _resolve_intent_key(
    intent: Any,
    offsets: dict[int, int],
    by_name: dict[str, int],
) -> int:
    key = getattr(intent, "key", None)
    if isinstance(key, int) and key in offsets:
        return key
    entry = getattr(intent, "entry", "") or ""
    resolved = by_name.get(entry)
    if resolved is None:
        raise StoreinfoWriteRefused(f"store key={key!r} / entry={entry!r} 未命中")
    return resolved


def _apply_intent(
    entry: StoreEntry,
    intent: Any,
    store_key: int,
) -> None:
    field = (getattr(intent, "field", "") or "").strip()
    op = (getattr(intent, "op", "set") or "set").strip()
    value = getattr(intent, "new", None)
    if not field:
        raise StoreinfoWriteRefused(
            f"store entry {store_key}: intent 缺少 field（op={op!r}）"
        )
    if field in _STOCK_LIST_FIELDS and "." not in field and "[" not in field:
        target = _STOCK_LIST_ALIASES.get(field, field)
        if op == "set" and isinstance(value, list):
            setattr(entry, target, [_stock_record(item, store_key) for item in value])
            return
        if op == "array_append" and isinstance(value, dict):
            record = _stock_record(value, store_key)
            getattr(entry, target).append(record)
            return
        raise StoreinfoWriteRefused(
            f"store entry {store_key}: {field} 不支持 op={op!r} / "
            f"value={type(value).__name__}"
        )
    if op == "add":
        _add_entry_field(entry, field, value, store_key)
        return
    if op != "set":
        raise StoreinfoWriteRefused(
            f"store entry {store_key}: 字段 {field!r} 不支持 op={op!r}"
        )
    if field == "key":
        raise StoreinfoWriteRefused(
            f"store entry {store_key}: key 是记录身份，不能用 set 改写"
        )
    _set_entry_path(entry, field, value, store_key)


def _add_entry_field(entry: StoreEntry, field: str, value: object, store_key: int) -> None:
    """按 DMM `add` 语义做相对当前值的数值累加。

    只支持 entry 顶层整数标量（例如 `buyable_stock_count`）；嵌套路径与非整数
    先明确拒绝，避免把“加 N”误写成整体覆盖。
    """
    if "." in field or "[" in field:
        raise StoreinfoWriteRefused(
            f"store entry {store_key}: op=add 暂不支持嵌套字段 {field!r}"
        )
    if field == "key":
        raise StoreinfoWriteRefused(
            f"store entry {store_key}: key 是记录身份，不能用 add 累加"
        )
    if field not in _ENTRY_FIELDS:
        raise StoreinfoWriteRefused(
            f"store entry {store_key}: schema 中不存在字段 {field!r}"
        )
    current = getattr(entry, field, None)
    if isinstance(current, bool) or not isinstance(current, int):
        raise StoreinfoWriteRefused(
            f"store entry {store_key}: op=add 只支持整数标量字段"
            f"（{field!r} 当前是 {type(current).__name__}）"
        )
    if isinstance(value, bool) or not isinstance(value, int):
        raise StoreinfoWriteRefused(
            f"store entry {store_key}: op=add 的新值必须是整数（收到 {type(value).__name__}）"
        )
    setattr(entry, field, current + value)


def _stock_record(value: object, store_key: int) -> StockRecord:
    if isinstance(value, StockRecord):
        return value
    try:
        return stock_record_from_json(value)
    except StoreinfoParseError as exc:
        raise StoreinfoWriteRefused(f"store entry {store_key}: stock 记录非法：{exc}") from exc


def _split_path(field: str, store_key: int) -> list[_Token]:
    """把 ``a.b[2].c`` 拆成属性/下标 token 序列。"""
    tokens: list[_Token] = []
    for part in field.split("."):
        name, bracket, remainder = part.partition("[")
        if not name:
            raise StoreinfoWriteRefused(f"store entry {store_key}: 字段 {field!r} 不合法")
        tokens.append(_Token(name))
        while bracket:
            inner, close, remainder = remainder.partition("]")
            if close != "]" or not inner.strip().isdigit():
                raise StoreinfoWriteRefused(
                    f"store entry {store_key}: 字段 {field!r} 下标不合法"
                )
            tokens.append(_Token("", int(inner)))
            if not remainder:
                break
            if not remainder.startswith("["):
                raise StoreinfoWriteRefused(
                    f"store entry {store_key}: 字段 {field!r} 不合法"
                )
            bracket, remainder = "[", remainder[1:]
    return tokens


def _set_entry_path(entry: StoreEntry, field: str, value: object, store_key: int) -> None:
    tokens = _split_path(field, store_key)
    _set_path(entry, tokens, value, field, store_key, "")


def _set_path(
    container: object,
    tokens: list[_Token],
    value: object,
    field: str,
    store_key: int,
    owner: str,
) -> None:
    token = tokens[0]
    rest = tokens[1:]
    if isinstance(container, list):
        if token.index is None:
            raise StoreinfoWriteRefused(
                f"store entry {store_key}: 字段 {field!r} 缺少下标"
            )
        if not 0 <= token.index < len(container):
            raise StoreinfoWriteRefused(
                f"store entry {store_key}: 字段 {field!r} 下标 {token.index} 越界"
                f"（当前 {len(container)} 项）"
            )
        if not rest:
            container[token.index] = _coerce_element(owner, value, store_key, field)
            return
        _set_path(container[token.index], rest, value, field, store_key, owner)
        return

    if not is_dataclass(container):
        raise StoreinfoWriteRefused(f"store entry {store_key}: 字段 {field!r} 无法定位")
    field_names = {item.name for item in fields(container)}
    if token.index is not None:
        raise StoreinfoWriteRefused(
            f"store entry {store_key}: 字段 {field!r} 的下标用在了非数组字段上"
        )
    if token.name not in field_names:
        raise StoreinfoWriteRefused(
            f"store entry {store_key}: {type(container).__name__} 没有字段 {token.name!r}"
        )
    if not rest:
        setattr(container, token.name, _coerce(token.name, value, store_key, field))
        return
    _set_path(
        getattr(container, token.name), rest, value, field, store_key, token.name
    )


def _coerce_element(name: str, value: object, store_key: int, field: str) -> object:
    """把 intent 的值写进数组成员时使用（数组整体 set 走 `_coerce`）。"""
    if name in ("stock_data_list", "stock_data"):
        return _stock_record(value, store_key)
    if name == "effect_list":
        return _coerce(name, [value], store_key, field)[0]
    return _require_int(value, field, store_key)


def _coerce(name: str, value: object, store_key: int, field: str) -> object:
    """把 intent 的 JSON 值转成目标 dataclass 字段需要的类型。"""
    if name == "stock_data_list":
        if not isinstance(value, list):
            raise StoreinfoWriteRefused(
                f"store entry {store_key}: {field!r} 必须是 stock 记录数组"
            )
        return [_stock_record(item, store_key) for item in value]
    if name == "value":
        if isinstance(value, StockValue):
            return value
        if not isinstance(value, dict):
            raise StoreinfoWriteRefused(
                f"store entry {store_key}: {field!r} 必须是 object"
            )
        raise StoreinfoWriteRefused(
            f"store entry {store_key}: {field!r} 只能整体替换时用完整 JSON，"
            "请改用 value.<字段> 逐字段写入"
        )
    if name == "payload":
        if isinstance(value, StockPayload):
            return value
        if not isinstance(value, dict):
            raise StoreinfoWriteRefused(
                f"store entry {store_key}: {field!r} 必须是 object"
            )
        return StockPayload(body=_require_int(value.get("body", 0), field, store_key))
    if name == "sub_data":
        try:
            return sub_data_from_json(value)
        except StoreinfoParseError as exc:
            raise StoreinfoWriteRefused(
                f"store entry {store_key}: {field!r} sub_data 非法：{exc}"
            ) from exc
    if name == "effect_list":
        if not isinstance(value, list):
            raise StoreinfoWriteRefused(
                f"store entry {store_key}: {field!r} 必须是数组"
            )
        output: list[StockEffect] = []
        for item in value:
            if isinstance(item, StockEffect):
                output.append(item)
                continue
            if not isinstance(item, dict):
                raise StoreinfoWriteRefused(
                    f"store entry {store_key}: {field!r} 元素必须是 object"
                )
            if "lookup" not in item or "raw" not in item:
                raise StoreinfoWriteRefused(
                    f"store entry {store_key}: {field!r} 元素缺少 lookup/raw"
                )
            output.append(
                StockEffect(
                    lookup=_require_int(item["lookup"], field, store_key),
                    raw=_require_int(item["raw"], field, store_key),
                )
            )
        return output
    if name in _ENTRY_LIST_FIELDS:
        if not isinstance(value, list):
            raise StoreinfoWriteRefused(
                f"store entry {store_key}: {field!r} 必须是数组"
            )
        return [_require_int(item, field, store_key) for item in value]
    return _require_int(value, field, store_key)


def _require_int(value: object, field: str, store_key: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise StoreinfoWriteRefused(
            f"store entry {store_key}: {field!r} 元素 {value!r} 不是整数"
        )
    return value


def _rebuild_header(header: bytes, key_size: int, deltas: list[tuple[int, int]]) -> bytes:
    """按 entry 位移重算 ``u16/u32 count + count x (key + u32 offset)``。"""
    count_size = 0
    for candidate in (2, 4):
        if len(header) < candidate:
            continue
        count = struct.unpack_from("<H" if candidate == 2 else "<I", header, 0)[0]
        if candidate + count * (key_size + 4) == len(header):
            count_size = candidate
            break
    if not count_size:
        raise StoreinfoWriteRefused("storeinfo header 长度与 count 不一致")

    output = bytearray(header)
    count = struct.unpack_from("<H" if count_size == 2 else "<I", header, 0)[0]
    position = count_size
    for _ in range(count):
        old_offset = struct.unpack_from("<I", header, position + key_size)[0]
        shift = sum(delta for start, delta in deltas if old_offset > start)
        if shift:
            struct.pack_into("<I", output, position + key_size, old_offset + shift)
        position += key_size + 4
    return bytes(output)
