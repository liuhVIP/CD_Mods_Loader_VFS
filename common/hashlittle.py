"""PAMT/PAPGT 使用的 Bob Jenkins hashlittle 哈希实现。"""

from __future__ import annotations

import sys
import struct

from cdmm.common.constants import HASH_SEED

try:
    from cdumm_native import compute_hashlittle as _native_hashlittle
except ImportError:
    _native_hashlittle = None


def hashlittle(data: bytes, initval: int = 0) -> int:
    """计算 Bob Jenkins hashlittle 哈希。"""
    if _native_hashlittle is not None:
        return _native_hashlittle(data, initval)

    length = len(data)
    a = b = c = (0xDEADBEEF + length + initval) & 0xFFFFFFFF
    block_count = (length - 1) // 12 if length > 12 else 0
    offset = block_count * 12

    if block_count and sys.byteorder == "little":
        # Windows/Crimson Desert 目标环境是小端；用 memoryview 批量按 u32 读，
        # 避免大型 PAZ/PAMT hash 每 4 字节都走一次 struct.unpack_from。
        words = memoryview(data)[:offset].cast("I")
        word_index = 0
        for _index in range(block_count):
            a = (a + words[word_index]) & 0xFFFFFFFF
            b = (b + words[word_index + 1]) & 0xFFFFFFFF
            c = (c + words[word_index + 2]) & 0xFFFFFFFF
            word_index += 3

            a = (a - c) & 0xFFFFFFFF
            a ^= ((c << 4) | (c >> 28)) & 0xFFFFFFFF
            c = (c + b) & 0xFFFFFFFF
            b = (b - a) & 0xFFFFFFFF
            b ^= ((a << 6) | (a >> 26)) & 0xFFFFFFFF
            a = (a + c) & 0xFFFFFFFF
            c = (c - b) & 0xFFFFFFFF
            c ^= ((b << 8) | (b >> 24)) & 0xFFFFFFFF
            b = (b + a) & 0xFFFFFFFF
            a = (a - c) & 0xFFFFFFFF
            a ^= ((c << 16) | (c >> 16)) & 0xFFFFFFFF
            c = (c + b) & 0xFFFFFFFF
            b = (b - a) & 0xFFFFFFFF
            b ^= ((a << 19) | (a >> 13)) & 0xFFFFFFFF
            a = (a + c) & 0xFFFFFFFF
            c = (c - b) & 0xFFFFFFFF
            c ^= ((b << 4) | (b >> 28)) & 0xFFFFFFFF
            b = (b + a) & 0xFFFFFFFF
    else:
        slow_offset = 0
        slow_length = length
        while slow_length > 12:
            a = (a + struct.unpack_from("<I", data, slow_offset)[0]) & 0xFFFFFFFF
            b = (b + struct.unpack_from("<I", data, slow_offset + 4)[0]) & 0xFFFFFFFF
            c = (c + struct.unpack_from("<I", data, slow_offset + 8)[0]) & 0xFFFFFFFF

            a = (a - c) & 0xFFFFFFFF
            a ^= ((c << 4) | (c >> 28)) & 0xFFFFFFFF
            c = (c + b) & 0xFFFFFFFF
            b = (b - a) & 0xFFFFFFFF
            b ^= ((a << 6) | (a >> 26)) & 0xFFFFFFFF
            a = (a + c) & 0xFFFFFFFF
            c = (c - b) & 0xFFFFFFFF
            c ^= ((b << 8) | (b >> 24)) & 0xFFFFFFFF
            b = (b + a) & 0xFFFFFFFF
            a = (a - c) & 0xFFFFFFFF
            a ^= ((c << 16) | (c >> 16)) & 0xFFFFFFFF
            c = (c + b) & 0xFFFFFFFF
            b = (b - a) & 0xFFFFFFFF
            b ^= ((a << 19) | (a >> 13)) & 0xFFFFFFFF
            a = (a + c) & 0xFFFFFFFF
            c = (c - b) & 0xFFFFFFFF
            c ^= ((b << 4) | (b >> 28)) & 0xFFFFFFFF
            b = (b + a) & 0xFFFFFFFF

            slow_offset += 12
            slow_length -= 12
        offset = slow_offset
    length -= offset

    remaining = data[offset:]
    if length > 0:
        padded = remaining + b"\x00" * (12 - len(remaining))
        if length >= 1:
            a = (a + padded[0]) & 0xFFFFFFFF
        if length >= 2:
            a = (a + (padded[1] << 8)) & 0xFFFFFFFF
        if length >= 3:
            a = (a + (padded[2] << 16)) & 0xFFFFFFFF
        if length >= 4:
            a = (a + (padded[3] << 24)) & 0xFFFFFFFF
        if length >= 5:
            b = (b + padded[4]) & 0xFFFFFFFF
        if length >= 6:
            b = (b + (padded[5] << 8)) & 0xFFFFFFFF
        if length >= 7:
            b = (b + (padded[6] << 16)) & 0xFFFFFFFF
        if length >= 8:
            b = (b + (padded[7] << 24)) & 0xFFFFFFFF
        if length >= 9:
            c = (c + padded[8]) & 0xFFFFFFFF
        if length >= 10:
            c = (c + (padded[9] << 8)) & 0xFFFFFFFF
        if length >= 11:
            c = (c + (padded[10] << 16)) & 0xFFFFFFFF
        if length >= 12:
            c = (c + (padded[11] << 24)) & 0xFFFFFFFF

        c ^= b
        c = (c - ((b << 14) | (b >> 18))) & 0xFFFFFFFF
        a ^= c
        a = (a - ((c << 11) | (c >> 21))) & 0xFFFFFFFF
        b ^= a
        b = (b - ((a << 25) | (a >> 7))) & 0xFFFFFFFF
        c ^= b
        c = (c - ((b << 16) | (b >> 16))) & 0xFFFFFFFF
        a ^= c
        a = (a - ((c << 4) | (c >> 28))) & 0xFFFFFFFF
        b ^= a
        b = (b - ((a << 14) | (a >> 18))) & 0xFFFFFFFF
        c ^= b
        c = (c - ((b << 24) | (b >> 8))) & 0xFFFFFFFF

    return c


def compute_pamt_hash(pamt_data: bytes) -> int:
    """计算 PAMT 完整性哈希。"""
    return hashlittle(pamt_data[12:], HASH_SEED)


def compute_papgt_hash(papgt_data: bytes) -> int:
    """计算 PAPGT 完整性哈希。"""
    return hashlittle(papgt_data[12:], HASH_SEED)
