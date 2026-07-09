"""PABGH companion 偏移重写服务。

当 whole-table writer 让 `.pabgb` 记录长度变化时，`.pabgh` 内的 offset
也必须同步更新，否则游戏会按旧偏移读取错误 entry。本模块只重写 vanilla
header 中每条记录的 4 字节 offset，保留 count、key 顺序、key 本身和其余
布局不变，供后续 iteminfo/skill 等 whole-table writer 复用。
"""

from __future__ import annotations

import logging
import struct

from cdmm.services.pab_table_service import UINT_COUNT_TABLES

logger = logging.getLogger(__name__)


def rewrite_pabgh_offsets(
    header: bytes,
    table_name: str,
    new_offsets: dict[int, int],
) -> bytes | None:
    """按新 `record key -> offset` 映射重写 `.pabgh`。"""
    count_size = 4 if table_name.lower() in UINT_COUNT_TABLES else 2
    if len(header) < count_size:
        logger.warning("pabgh rewrite: header 过短（%d bytes）", len(header))
        return None

    count = struct.unpack_from("<I" if count_size == 4 else "<H", header, 0)[0]
    if count == 0:
        return bytes(header)

    total_key_bytes = len(header) - count_size - count * 4
    if total_key_bytes <= 0 or total_key_bytes % count != 0:
        logger.warning(
            "pabgh rewrite: %s key_size 推导失败（header=%d, count=%d）",
            table_name,
            len(header),
            count,
        )
        return None

    key_size = total_key_bytes // count
    if key_size not in (2, 4, 8):
        logger.warning("pabgh rewrite: %s key_size=%d 不可信", table_name, key_size)
        return None

    data = bytearray(header)
    pos = count_size
    for _ in range(count):
        if pos + key_size + 4 > len(data):
            logger.warning("pabgh rewrite: %s entry table 被截断", table_name)
            return None
        key = int.from_bytes(data[pos:pos + key_size], "little")
        if key not in new_offsets:
            logger.warning(
                "pabgh rewrite: %s 缺少 key=%d 的新偏移，拒绝部分重写",
                table_name,
                key,
            )
            return None
        struct.pack_into("<I", data, pos + key_size, new_offsets[key])
        pos += key_size + 4
    return bytes(data)
