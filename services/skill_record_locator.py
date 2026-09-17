"""Skill PABGB 记录定位器（2.02.00 wire 布局）。

`skill` 记录里真正会被模组改写的只有 `buff_level_list`；但该列表的每个元素是
`BuffDataOptional = [u8 absent_flag][BuffData]`，而 `BuffData` 的 variants 尾
长度取决于 `tag`，共有上百种形态。逐 tag 移植 variants 读写器成本过高，因此
这里改用**结构自洽定位**：

1. 按 `u32 key + CString string_key + u8 is_blocked + u32 cooltime` 进入
   `buff_level_list`；
2. 对每个非空 buff 解析 `BuffDataBase`（定宽 + `CString asset_path` +
   两个 `CArray<u32>`，因此 base 结束位置可精确算出）；
3. variants 尾长度未知，用“剩余结构必须正好走满本条记录的 `.pabgh` 边界”
   这一约束做回溯搜索；
4. 多个候选长度都能走满时，用 post-buff 段的语义健全性（`1 <= max_level <=
   512`、`apply_type` 合理、未额外出现 dev_extra 尾部字段）消歧。

`post-buff` wire 顺序（`skill_group_key` 起、`video_path` 止）由 2.02.00 原表
整表核对：`read_post` 必须正好落在记录末尾。

定位结果只用于**字节级拼接**（把新的 `carray_u16` 数组替换回原记录），因此
调用方在拼接后必须复验，避免在消歧失败时写坏记录。
"""

from __future__ import annotations

import struct

from cdmm.services.pab_record_codec import Reader, RecordParseError

# variants 尾长度回溯上限；2.02.00 原表最大尾长度远小于此值。
MAX_VARIANT_TAIL = 4096
_MAX_LEVELS = 64
_MAX_BUFFS = 64
_MAX_LIST = 1000


# 2.02.00 原表反推得到的 `BuffData` variants 尾固定长度表（tag -> 字节数）。
# 覆盖本机 skill 表中所有“长度恒定”的 tag；未列出的 tag 属于变长 payload
# （含 CString / CArray），仍走回溯搜索。
# 该表随游戏版本可能变化：一旦某条记录的固定长度不再成立，整条记录会走
# 不到 `.pabgh` 边界，定位失败并跳过，不会写坏数据。
_FIXED_VARIANT_TAIL: dict[int, int] = {
    4: 26,
    9: 12,
    11: 1,
    14: 12,
    23: 12,
    25: 24,
    26: 10,
    38: 9,
    39: 0,
    41: 0,
    42: 4,
    46: 8,
    47: 0,
    48: 6,
    55: 12,
    57: 9,
    58: 17,
    59: 4,
    61: 16,
    66: 8,
    67: 5,
    72: 2,
    74: 8,
    75: 8,
    76: 0,
    80: 18,
    81: 16,
    82: 1,
    83: 12,
    84: 5,
    85: 0,
    86: 1,
    90: 16,
    91: 27,
    92: 4,
    93: 4,
    96: 53,
    100: 12,
    101: 8,
    102: 12,
    103: 17,
    110: 4,
    111: 0,
    113: 8,
    116: 0,
    160: 53,
    192: 53,
    195: 15,
    213: 34,
    224: 53,
    234: 16,
}


class SkillLocateError(ValueError):
    """skill 记录无法按当前布局定位。"""


def _read_asset_path(reader: Reader) -> None:
    """读取并校验 `asset_path`：长度受限且必须是合法 UTF-8。

    该字段是记录内唯一带长度的文本，严格校验能显著降低 variants 尾长度
    歧义时误判的命中率（随机字节极少构成合法 UTF-8）。
    """
    length = reader.u32()
    if length > 512:
        raise SkillLocateError(f"asset_path 长度不可信（{length}）")
    reader._need(length, "asset_path")
    raw = reader.data[reader.pos:reader.pos + length]
    reader.pos += length
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SkillLocateError("asset_path 不是合法 UTF-8") from exc


def _read_base(data: bytes, pos: int, end: int) -> tuple[int, int, int, int]:
    """解析 `BuffDataBase`，返回 `(tag, array_start, array_end, base_end)`。"""
    reader = Reader(data, pos, end)
    tag = reader.u8()
    reader.u32()
    reader.u32()
    reader.u8()
    reader.u8()
    reader.u64()
    reader.u64()
    reader.u64()
    _read_asset_path(reader)
    reader.u32()
    reader.u8()
    reader.u32()
    reader.u32()
    reader.u32()
    reader.u32()
    reader.u8()
    reader.u8()
    reader.u32()
    reader.u32()
    array_start = reader.pos
    count = reader.u32()
    if count > 100_000:
        raise SkillLocateError("carray_u16 数量不可信")
    reader.skip(count * 4)
    array_end = reader.pos
    reader.u32()
    reader.u32()
    reader.u32()
    reader.u32()
    reader.u32()
    count = reader.u32()
    if count > 100_000:
        raise SkillLocateError("carray_u32 数量不可信")
    reader.skip(count * 4)
    reader.u8()
    reader.u32()
    return tag, array_start, array_end, reader.pos


def _read_post(data: bytes, pos: int, end: int) -> tuple[int, bool]:
    """解析 post-buff 段，返回 `(结束位置, 语义是否健全)`。"""
    reader = Reader(data, pos, end)
    reader.u32()
    reader.u32()
    reader.u32()
    apply_type = reader.u8()
    reader.u32()
    reader.u32()
    reader.skip(28)
    reader.skip(28)
    for _ in range(2):
        count = reader.u32()
        if count > _MAX_LIST:
            raise SkillLocateError("post-buff u32 列表数量不可信")
        reader.skip(count * 4)
    reader.u32()
    reader.u32()
    for _ in range(2):
        count = reader.u32()
        if count > _MAX_LIST:
            raise SkillLocateError("post-buff ResourceStat 列表数量不可信")
        reader.skip(count * 22)
    count = reader.u32()
    if count > _MAX_LIST:
        raise SkillLocateError("post-buff ResourceItem 列表数量不可信")
    reader.skip(count * 12)
    reader.u64()
    reader.skip(7)
    count = reader.u32()
    if count > _MAX_LIST:
        raise SkillLocateError("reserve_slot_info_list 数量不可信")
    reader.skip(count * 4)
    max_level = reader.u32()
    count = reader.u32()
    if count > _MAX_LIST:
        raise SkillLocateError("skill_group_key_list 数量不可信")
    reader.skip(count * 2)
    reader.u32()
    reader.cstring()
    reader.cstring()
    has_extras = False
    if end - reader.pos == 4:
        reader.u32()
    elif end - reader.pos > 4:
        has_extras = True
        reader.cstring()
        reader.cstring()
        reader.u32()
    sane = 1 <= max_level <= 512 and apply_type <= 32 and not has_extras
    return reader.pos, sane


def locate_record(data: bytes, start: int, end: int) -> dict:
    """定位一条 skill 记录；失败抛 `SkillLocateError`。"""
    reader = Reader(data, start, end)
    try:
        key = reader.u32()
        string_key = reader.cstring()
        reader.u8()
        reader.u32()
        levels = reader.u32()
    except RecordParseError as exc:
        raise SkillLocateError(f"skill 记录头解析失败：{exc}") from exc
    if levels > _MAX_LEVELS:
        raise SkillLocateError(f"buff_level_list 层数不可信（{levels}）")
    levels_start = reader.pos

    memo: set[tuple[int, int, int, bool]] = set()

    def search(pos: int, level: int, buff: int, count: int | None, sizes: list[int],
               require_sane: bool) -> list[int] | None:
        state = (pos, level, buff, require_sane)
        if state in memo:
            return None
        if level >= levels:
            try:
                post_end, sane = _read_post(data, pos, end)
            except (RecordParseError, SkillLocateError):
                memo.add(state)
                return None
            if post_end != end or (require_sane and not sane):
                memo.add(state)
                return None
            return list(sizes)
        if buff == 0:
            inner = Reader(data, pos, end)
            try:
                count = inner.u32()
            except (RecordParseError, SkillLocateError):
                memo.add(state)
                return None
            if count > _MAX_BUFFS:
                memo.add(state)
                return None
            pos = inner.pos
        if buff >= count:
            result = search(pos, level + 1, 0, None, sizes, require_sane)
            if result is None:
                memo.add(state)
            return result
        inner = Reader(data, pos, end)
        try:
            flag = inner.u8()
        except (RecordParseError, SkillLocateError):
            memo.add(state)
            return None
        if flag:
            return search(inner.pos, level, buff + 1, count, sizes, require_sane)
        try:
            _tag, _start, _stop, base_end = _read_base(data, inner.pos, end)
        except (RecordParseError, SkillLocateError):
            memo.add(state)
            return None
        known = _FIXED_VARIANT_TAIL.get(_tag)
        candidates = (
            (known,) if known is not None and known <= end - base_end
            else range(0, min(MAX_VARIANT_TAIL, end - base_end) + 1)
        )
        for size in candidates:
            sizes.append(size)
            result = search(base_end + size, level, buff + 1, count, sizes, require_sane)
            if result is not None:
                return result
            sizes.pop()
        memo.add(state)
        return None

    chosen = None
    for require_sane in (True, False):
        memo.clear()
        chosen = search(levels_start, 0, 0, None, [], require_sane)
        if chosen is not None:
            break
    if chosen is None:
        raise SkillLocateError(f"skill key={key} 无法走满记录边界")

    spans: list[list[tuple[int, int, int] | None]] = []
    cursor = Reader(data, levels_start, end)
    index = 0
    for _ in range(levels):
        count = cursor.u32()
        level_spans: list[tuple[int, int, int] | None] = []
        for _ in range(count):
            flag = cursor.u8()
            if flag:
                level_spans.append(None)
                continue
            tag, array_start, array_end, base_end = _read_base(data, cursor.pos, end)
            level_spans.append((tag, array_start, array_end))
            cursor.pos = base_end + chosen[index]
            index += 1
        spans.append(level_spans)

    _require_known_tails(spans)
    return {
        "key": key,
        "string_key": string_key,
        "levels": levels,
        "levels_start": levels_start,
        "spans": spans,
    }


def _require_known_tails(
    spans: list[list[tuple[int, int, int] | None]],
) -> None:
    """拒绝 variants 尾长度存在歧义的记录。

    固定长度表能钉住绝大多数 tag；变长 tag 只有在它是**整条记录最后一个
    buff** 时才安全，因为此时尾长度由 post-buff 段的唯一边界反推。其余情况
    都可能存在多个自洽解，宁可跳过也不写坏数据。
    """
    ordered = [span for level in spans for span in level if span is not None]
    if not ordered:
        return
    for tag, _start, _stop in ordered[:-1]:
        if tag not in _FIXED_VARIANT_TAIL:
            raise SkillLocateError(
                f"BuffData tag={tag} 的 variants 尾长度不固定，记录存在多种自洽解析"
            )


def read_u32_array(data: bytes, start: int, end: int) -> list[int]:
    """读取 `start` 处的 `u32 count + N×u32` 数组。"""
    count = struct.unpack_from("<I", data, start)[0]
    if count == 0:
        return []
    return list(struct.unpack_from("<%dI" % count, data, start + 4))


def encode_u32_array(values: list[int]) -> bytes:
    """编码 `u32 count + N×u32` 数组。"""
    return struct.pack("<I", len(values)) + b"".join(
        struct.pack("<I", value) for value in values
    )


def locate_array(data: bytes, start: int, end: int, level: int, buff: int) -> tuple[int, int]:
    """定位 `buff_level_list[level][buff].base.carray_u16` 的 `(start, end)`。"""
    record = locate_record(data, start, end)
    spans = record["spans"]
    if level >= len(spans):
        raise SkillLocateError(f"buff_level_list 只有 {len(spans)} 层，缺少第 {level} 层")
    level_spans = spans[level]
    if buff >= len(level_spans):
        raise SkillLocateError(
            f"buff_level_list[{level}] 只有 {len(level_spans)} 个元素，缺少第 {buff} 个"
        )
    span = level_spans[buff]
    if span is None:
        raise SkillLocateError(f"buff_level_list[{level}][{buff}] 是空槽，没有 base")
    return span[1], span[2]
