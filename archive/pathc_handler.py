"""PATHC 纹理索引读写工具。"""

from __future__ import annotations

import bisect
import io
import struct
from dataclasses import dataclass
from pathlib import Path

from cdmm.common.hashlittle import hashlittle

# PATHC 路径 hash 使用的固定 seed，必须和游戏/JMM 保持一致。
PATHC_HASH_SEED = 0x000C5EDE


@dataclass(slots=True)
class PathcHeader:
    """PATHC 文件头，7 个 uint32 字段。"""

    unknown0: int
    unknown1: int
    dds_record_size: int
    dds_record_count: int
    hash_count: int
    collision_path_count: int
    collision_blob_size: int


@dataclass(slots=True)
class PathcMapEntry:
    """PATHC hash 表对应的纹理映射记录。"""

    selector: int
    m1: int
    m2: int
    m3: int
    m4: int


@dataclass(slots=True)
class PathcCollisionEntry:
    """PATHC hash 冲突路径记录。"""

    path_offset: int
    dds_index: int
    m1: int
    m2: int
    m3: int
    m4: int
    path: str = ""


@dataclass(slots=True)
class PathcFile:
    """解析后的 PATHC 文件结构。"""

    header: PathcHeader
    dds_records: list[bytes]
    key_hashes: list[int]
    map_entries: list[PathcMapEntry]
    collision_entries: list[PathcCollisionEntry]


def read_pathc(path: Path) -> PathcFile:
    """读取并解析 meta/0.pathc。"""
    raw = path.read_bytes()
    if len(raw) < 0x1C:
        raise ValueError(f"{path} 文件过小，无法作为 PATHC 解析")

    header = PathcHeader(*struct.unpack_from("<7I", raw, 0))
    dds_table_off = 0x1C
    hash_table_off = dds_table_off + header.dds_record_size * header.dds_record_count
    map_table_off = hash_table_off + header.hash_count * 4
    collision_table_off = map_table_off + header.hash_count * 20
    collision_blob_off = collision_table_off + header.collision_path_count * 24
    collision_blob_end = collision_blob_off + header.collision_blob_size
    if collision_blob_end > len(raw):
        raise ValueError(f"{path} PATHC section 越界")

    dds_records = [
        raw[
            dds_table_off + index * header.dds_record_size:
            dds_table_off + (index + 1) * header.dds_record_size
        ]
        for index in range(header.dds_record_count)
    ]
    key_hashes = (
        list(struct.unpack_from(f"<{header.hash_count}I", raw, hash_table_off))
        if header.hash_count
        else []
    )
    map_entries = [
        PathcMapEntry(*struct.unpack_from("<IIIII", raw, map_table_off + index * 20))
        for index in range(header.hash_count)
    ]

    blob = raw[collision_blob_off:collision_blob_end]
    collision_entries: list[PathcCollisionEntry] = []
    for index in range(header.collision_path_count):
        path_offset, dds_index, m1, m2, m3, m4 = struct.unpack_from(
            "<6I",
            raw,
            collision_table_off + index * 24,
        )
        end = blob.find(b"\x00", path_offset)
        path_text = blob[path_offset:end].decode("utf-8", errors="replace") if end >= 0 else ""
        collision_entries.append(
            PathcCollisionEntry(path_offset, dds_index, m1, m2, m3, m4, path_text)
        )
    return PathcFile(header, dds_records, key_hashes, map_entries, collision_entries)


def serialize_pathc(pathc: PathcFile) -> bytes:
    """把 PATHC 结构重新序列化，保留 unknown 字段原值。"""
    collision_blob = bytearray()
    collision_rows: list[bytes] = []
    for entry in pathc.collision_entries:
        path_bytes = entry.path.encode("utf-8") + b"\x00"
        path_offset = len(collision_blob)
        collision_blob.extend(path_bytes)
        collision_rows.append(
            struct.pack("<6I", path_offset, entry.dds_index, entry.m1, entry.m2, entry.m3, entry.m4)
        )

    pathc.header.dds_record_count = len(pathc.dds_records)
    pathc.header.hash_count = len(pathc.key_hashes)
    pathc.header.collision_path_count = len(pathc.collision_entries)
    pathc.header.collision_blob_size = len(collision_blob)

    out = io.BytesIO()
    out.write(
        struct.pack(
            "<7I",
            pathc.header.unknown0,
            pathc.header.unknown1,
            pathc.header.dds_record_size,
            pathc.header.dds_record_count,
            pathc.header.hash_count,
            pathc.header.collision_path_count,
            pathc.header.collision_blob_size,
        )
    )
    for record in pathc.dds_records:
        out.write(record)
    if pathc.key_hashes:
        out.write(struct.pack(f"<{len(pathc.key_hashes)}I", *pathc.key_hashes))
    for entry in pathc.map_entries:
        out.write(struct.pack("<IIIII", entry.selector, entry.m1, entry.m2, entry.m3, entry.m4))
    for row in collision_rows:
        out.write(row)
    out.write(collision_blob)
    return out.getvalue()


def normalize_path(path_str: str) -> str:
    """归一化 PATHC 虚拟路径：正斜杠、小写前由调用方处理、强制 leading slash。"""
    return "/" + path_str.replace("\\", "/").strip().lstrip("/").strip("/")


def get_path_hash(path_str: str) -> int:
    """计算 PATHC 路径 hash。"""
    return hashlittle(normalize_path(path_str).lower().encode("utf-8"), PATHC_HASH_SEED)


def make_dds_record(dds_data: bytes, record_size: int) -> bytes:
    """从 DDS bytes 构造 PATHC DDS record，DX10 头会复制 148 字节。"""
    if len(dds_data) < 128 or not dds_data.startswith(b"DDS "):
        raise ValueError("DDS 文件头无效，无法注册 PATHC")
    fourcc = dds_data[84:88] if len(dds_data) >= 88 else b""
    header_size = 148 if fourcc == b"DX10" and len(dds_data) >= 148 else 128
    record = bytearray(record_size)
    copy_len = min(len(dds_data), header_size, record_size)
    record[:copy_len] = dds_data[:copy_len]
    return bytes(record)


def update_entry(
    pathc: PathcFile,
    virtual_path: str,
    dds_index: int,
    m_values: tuple[int, int, int, int],
) -> bool:
    """新增或更新 PATHC 路径映射，返回是否修改了内容。"""
    target_hash = get_path_hash(virtual_path)
    index = bisect.bisect_left(pathc.key_hashes, target_hash)
    selector = 0xFFFF0000 | (dds_index & 0xFFFF)

    if index < len(pathc.key_hashes) and pathc.key_hashes[index] == target_hash:
        current = pathc.map_entries[index]
        if (
            current.selector,
            current.m1,
            current.m2,
            current.m3,
            current.m4,
        ) == (selector, *m_values):
            return False
        current.selector = selector
        current.m1, current.m2, current.m3, current.m4 = m_values
        return True

    pathc.key_hashes.insert(index, target_hash)
    pathc.map_entries.insert(index, PathcMapEntry(selector, *m_values))
    return True
