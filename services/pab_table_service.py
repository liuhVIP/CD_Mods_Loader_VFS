"""PABGB/PABGH 共用解析工具。

本模块收口独立加载器里与 PABGB/PABGH 结构相关的底层解析逻辑，避免
`json_loader.py`、`format3_loader.py` 等服务各自维护一份近似实现。
后续继续迁移 `skill`、`storeinfo`、`buffinfo` 等 table writer 时，也统一
复用这里的入口。
"""

from __future__ import annotations

import struct

# 这些表的 PABGH header 记录数使用 u32 count，保持与参考仓库和当前
# 独立加载器其他链路一致，避免 key_size 推导错误导致 entry header 错位。
UINT_COUNT_TABLES = frozenset(
    {
        "characterappearanceindexinfo",
        "globalstagesequencerinfo",
        "sequencerspawninfo",
        "sheetmusicinfo",
        "spawningpoolautospawninfo",
        "itemuseinfo",
        "terrainregionautospawninfo",
        "textguideinfo",
        "validscheduleaction",
        "stageinfo",
        "questinfo",
        "gimmickeventtableinfo",
        "reviepointinfo",
        "aidialogstringinfo",
        "dialogsetinfo",
        "vibratepatterninfo",
        "platformachievementinfo",
        "levelgimmicksceneobjectinfo",
        "fieldlevelnametableinfo",
        "levelinfo",
        "board",
        "gameplaytrigger",
        "characterchange",
        "materialrelationinfo",
    }
)


def parse_pabgh_index(header: bytes, table_name: str) -> tuple[int, dict[int, int]]:
    """解析 PABGH 的 `key -> PABGB entry offset` 索引。"""
    count_size = 4 if table_name.lower() in UINT_COUNT_TABLES else 2
    if len(header) < count_size:
        return 0, {}
    count = struct.unpack_from("<I" if count_size == 4 else "<H", header, 0)[0]
    if count <= 0:
        return 0, {}
    total_key_bytes = len(header) - count_size - count * 4
    if total_key_bytes <= 0 or total_key_bytes % count:
        return 0, {}

    key_size = total_key_bytes // count
    offsets: dict[int, int] = {}
    pos = count_size
    for _ in range(count):
        if pos + key_size + 4 > len(header):
            break
        key = int.from_bytes(header[pos:pos + key_size], "little")
        offsets[key] = struct.unpack_from("<I", header, pos + key_size)[0]
        pos += key_size + 4
    return key_size, offsets


def parse_entry_name_end(
    body: bytes,
    entry_offset: int,
    key_size: int,
) -> tuple[str, int] | None:
    """解析单个 PABGB entry 的名称，并返回 `name_end` 锚点。"""
    eid_size = 2 if key_size == 2 else 4
    head_size = eid_size + 4
    if entry_offset < 0 or entry_offset + head_size > len(body):
        return None
    name_len = struct.unpack_from("<I", body, entry_offset + eid_size)[0]
    if name_len > 500 or entry_offset + head_size + name_len > len(body):
        return None

    name_start = entry_offset + head_size
    name_end = name_start + name_len
    try:
        name = body[name_start:name_end].decode("utf-8")
    except UnicodeDecodeError:
        return None
    return name, name_end


def build_entry_bounds(
    body: bytes,
    key_size: int,
    offsets: dict[int, int],
) -> dict[int, tuple[int, int, str, int]]:
    """构建 `record key -> (entry_start, entry_end, name, name_end)`。"""
    bounds: dict[int, tuple[int, int, str, int]] = {}
    sorted_offsets = sorted(offsets.items(), key=lambda item: item[1])
    for index, (key, offset) in enumerate(sorted_offsets):
        entry_end = sorted_offsets[index + 1][1] if index + 1 < len(sorted_offsets) else len(body)
        parsed = parse_entry_name_end(body, offset, key_size)
        if parsed is None:
            continue
        name, name_end = parsed
        bounds[key] = (offset, entry_end, name, name_end)
    return bounds
