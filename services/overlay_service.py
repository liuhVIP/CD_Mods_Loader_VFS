"""overlay PAZ/PAMT 构建与目录分配。"""

from __future__ import annotations

import logging
import struct
from pathlib import Path

from cdmm.common.constants import (
    GAME_DIR_NAME_LENGTH,
    HASH_SEED,
    OVERLAY_PAMT_NAME,
    OVERLAY_PAZ_NAME,
    OVERLAY_START_DIR,
    PAMT_CONSTANT,
    PAZ_ALIGNMENT,
)
from cdmm.archive.paz_crypto import encrypt, lz4_compress
from cdmm.archive.pathc_handler import get_path_hash, read_pathc
from cdmm.common.hashlittle import hashlittle
from cdmm.common.models import BuiltOverlayEntry, OverlayBuildResult, OverlayInputEntry
from cdmm.storage.state_store import load_state

logger = logging.getLogger(__name__)

# DDS 格式 last4 映射，来自 JMM/完整管理器的 PATHC 兼容逻辑。
_DDS_LAST4_BY_FOURCC = {
    b"DXT1": 12,
    b"DXT2": 15,
    b"DXT3": 15,
    b"DXT4": 15,
    b"DXT5": 15,
    b"ATI1": 4,
    b"BC4U": 4,
    b"BC4S": 4,
    b"ATI2": 4,
    b"BC5U": 4,
    b"BC5S": 4,
}

# DX10/DXGI DDS 格式 last4 映射，PATHC DDS record 的末尾字段需要同步。
_DDS_LAST4_BY_DXGI = {
    70: 12,
    71: 12,
    72: 12,
    73: 15,
    74: 15,
    75: 15,
    76: 15,
    77: 15,
    78: 15,
    79: 4,
    80: 4,
    81: 4,
    82: 4,
    83: 4,
    84: 4,
    94: 4,
    95: 4,
    96: 4,
    97: 15,
    98: 15,
    99: 15,
}

# 块压缩 DDS 每个 4x4 block 的字节数。
_DDS_BC_BLOCK_BYTES_BY_FOURCC = {
    b"DXT1": 8,
    b"ATI1": 8,
    b"BC4U": 8,
    b"BC4S": 8,
    b"DXT3": 16,
    b"DXT5": 16,
    b"ATI2": 16,
    b"BC5U": 16,
    b"BC5S": 16,
}

# DX10/DXGI 块压缩格式每个 4x4 block 的字节数。
_DDS_BC_BLOCK_BYTES_BY_DXGI = {
    70: 8,
    71: 8,
    72: 8,
    73: 16,
    74: 16,
    75: 16,
    76: 16,
    77: 16,
    78: 16,
    79: 8,
    80: 8,
    81: 8,
    82: 16,
    83: 16,
    84: 16,
    94: 16,
    95: 16,
    96: 16,
    97: 16,
    98: 16,
    99: 16,
}

_pathc_cache: dict[str, object] = {}

# overlay 构建期间同一个原始 PAMT 可能服务几十个 entry，完整目录映射只需解析一次。
_full_path_map_cache: dict[tuple[str, int, int], dict[str, str]] = {}


def allocate_overlay_dir(game_dir: Path) -> str:
    """分配本次 overlay 目录，优先复用 state 中加载器上次创建的目录。"""
    state = load_state(game_dir)
    old_dir = state.get("overlay_dir")
    if isinstance(old_dir, str) and _is_overlay_dir_name(old_dir):
        path = game_dir / old_dir
        if not path.exists() or _looks_like_loader_overlay(path):
            return old_dir

    used = {
        int(item.name)
        for item in game_dir.iterdir()
        if item.is_dir() and _is_overlay_dir_name(item.name)
    }
    candidate = OVERLAY_START_DIR
    while candidate in used:
        candidate += 1
    return f"{candidate:04d}"


def remove_previous_overlay(game_dir: Path) -> None:
    """清理 state 记录的上次 overlay 目录，仅删除加载器可确认的输出。"""
    state = load_state(game_dir)
    old_dir = state.get("overlay_dir")
    if not isinstance(old_dir, str) or not _is_overlay_dir_name(old_dir):
        return
    target = game_dir / old_dir
    if not target.exists():
        return
    if _looks_like_loader_overlay(target):
        for child in target.iterdir():
            child.unlink()
        target.rmdir()
    else:
        logger.warning("跳过未知来源 overlay 目录：%s", target)


def build_overlay(overlay_dir: str, entries: list[OverlayInputEntry], game_dir: Path) -> OverlayBuildResult:
    """从解压后的 overlay entries 构建 0.paz/0.pamt。"""
    paz_buffer = bytearray()
    built_entries: list[BuiltOverlayEntry] = []
    seen: dict[str, OverlayInputEntry] = {}
    for entry in entries:
        # 同一 entry_path 采用最后写入结果，匹配低序号先加载、后续覆盖的组合语义。
        seen[entry.entry_path.lower()] = entry

    for entry in seen.values():
        filename = entry.entry_path.rsplit("/", 1)[-1]
        dir_path = _resolve_dir_path(entry.entry_path, entry.pamt_dir, game_dir)
        paz_offset = len(paz_buffer)
        if paz_offset > 0xFFFFFFFF:
            raise ValueError("overlay PAZ 超过 4GiB，当前第一阶段不支持拆分输出")

        pathc_virtual_path = f"{dir_path}/{filename}" if dir_path else filename
        payload, comp_size, decomp_size, flags, m_values, last4 = _pack_payload(
            entry,
            filename,
            game_dir,
            pathc_virtual_path,
        )
        paz_buffer.extend(payload)
        padding = (PAZ_ALIGNMENT - (len(paz_buffer) % PAZ_ALIGNMENT)) % PAZ_ALIGNMENT
        if padding:
            paz_buffer.extend(b"\x00" * padding)
        built_entries.append(
            BuiltOverlayEntry(
                entry_path=entry.entry_path,
                dir_path=dir_path,
                filename=filename,
                paz_offset=paz_offset,
                comp_size=comp_size,
                decomp_size=decomp_size,
                flags=flags,
                content=entry.content,
                dds_m_values=m_values,
                dds_last4=last4,
            )
        )

    paz_bytes = bytes(paz_buffer)
    pamt_bytes = _build_multi_pamt(built_entries, len(paz_bytes))
    pamt_buffer = bytearray(pamt_bytes)
    struct.pack_into("<I", pamt_buffer, 16, hashlittle(paz_bytes, HASH_SEED))
    struct.pack_into("<I", pamt_buffer, 0, hashlittle(bytes(pamt_buffer[12:]), HASH_SEED))
    return OverlayBuildResult(
        overlay_dir=overlay_dir,
        paz_bytes=paz_bytes,
        pamt_bytes=bytes(pamt_buffer),
        entries=built_entries,
    )


def overlay_rel_paths(overlay_dir: str) -> tuple[str, str]:
    """返回 overlay PAZ/PAMT 相对路径。"""
    return f"{overlay_dir}/{OVERLAY_PAZ_NAME}", f"{overlay_dir}/{OVERLAY_PAMT_NAME}"


def _pack_payload(
    entry: OverlayInputEntry,
    filename: str,
    game_dir: Path,
    pathc_virtual_path: str,
) -> tuple[bytes, int, int, int, tuple[int, int, int, int] | None, int]:
    """按 overlay PAMT 规则压缩并可选加密 payload。"""
    comp_type = entry.compression_type
    if entry.entry_path.lower().endswith(".dds"):
        comp_type = 1

    if comp_type == 2:
        payload = lz4_compress(entry.content)
        flags = 2
        decomp_size = len(entry.content)
        m_values = None
        last4 = 0
    elif comp_type == 1:
        partial, m_values = _build_dds_payload(entry.content)
        payload_buffer = bytearray(len(entry.content))
        copy_len = min(len(partial), len(payload_buffer))
        payload_buffer[:copy_len] = partial[:copy_len]
        last4 = _get_pathc_last4_for_path(game_dir, pathc_virtual_path) or _get_dds_format_last4(
            entry.content
        )
        if last4 and len(payload_buffer) >= 128:
            struct.pack_into("<I", payload_buffer, 124, last4)
        payload = bytes(payload_buffer)
        flags = 1
        decomp_size = len(payload)
    else:
        payload = entry.content
        flags = 0
        decomp_size = len(payload)
        m_values = None
        last4 = 0

    if entry.encrypted:
        payload = encrypt(payload, entry.crypto_filename or filename)
        flags = (flags & 0x0F) | 0x30
    return payload, len(payload), decomp_size, flags, m_values, last4


def _build_dds_payload(dds_bytes: bytes) -> tuple[bytes, tuple[int, int, int, int]]:
    """构建游戏 overlay 使用的 partial DDS payload，并返回 PATHC m-values。"""
    if len(dds_bytes) < 128 or dds_bytes[:4] != b"DDS ":
        return dds_bytes, (0, 0, 0, 0)

    height, width = struct.unpack_from("<II", dds_bytes, 12)
    depth = struct.unpack_from("<I", dds_bytes, 24)[0]
    mip_count = struct.unpack_from("<I", dds_bytes, 28)[0] or 1
    fourcc = dds_bytes[84:88]
    field_112 = struct.unpack_from("<I", dds_bytes, 112)[0]

    is_dx10 = fourcc == b"DX10" and len(dds_bytes) >= 148
    header_size = 148 if is_dx10 else 128
    dxgi = struct.unpack_from("<I", dds_bytes, 128)[0] if is_dx10 else None
    array_size = struct.unpack_from("<I", dds_bytes, 140)[0] if is_dx10 else 1

    block_bytes = _DDS_BC_BLOCK_BYTES_BY_FOURCC.get(fourcc)
    if block_bytes is None and dxgi is not None:
        block_bytes = _DDS_BC_BLOCK_BYTES_BY_DXGI.get(dxgi)
    if not block_bytes:
        logger.debug("DDS 格式暂不支持 partial payload：fourcc=%r dxgi=%r", fourcc, dxgi)
        return dds_bytes, (0, 0, 0, 0)

    mip_sizes = [0] * max(4, mip_count)
    current_width = max(1, width)
    current_height = max(1, height)
    for index in range(min(len(mip_sizes), mip_count)):
        mip_sizes[index] = (
            max(1, (current_width + 3) // 4)
            * max(1, (current_height + 3) // 4)
            * block_bytes
        )
        current_width = max(1, current_width // 2)
        current_height = max(1, current_height // 2)

    not_dx10_or_array_small = (not is_dx10) or array_size < 2
    multi_chunk_rawable = mip_count > 5 and field_112 == 0 and depth < 2
    use_single_chunk = (not not_dx10_or_array_small) or (not multi_chunk_rawable)

    header = bytearray(dds_bytes[:header_size])
    if depth == 0:
        struct.pack_into("<I", header, 24, 1)

    output = bytearray(header)
    m_values = [0, 0, 0, 0]
    if use_single_chunk:
        first_mip_size = mip_sizes[0]
        first_mip_end = header_size + first_mip_size
        first_mip = bytes(dds_bytes[header_size:first_mip_end])
        compressed = lz4_compress(first_mip)
        chosen = compressed if len(compressed) < len(first_mip) else first_mip
        m_values[0] = len(chosen)
        m_values[1] = first_mip_size
        if mip_count > 1:
            m_values[2] = mip_sizes[1]
        if mip_count > 2:
            m_values[3] = mip_sizes[2]
        output += chosen
        if first_mip_end < len(dds_bytes):
            output += dds_bytes[first_mip_end:]
    else:
        cursor = header_size
        for index in range(min(4, mip_count)):
            size = mip_sizes[index]
            m_values[index] = size
            output += dds_bytes[cursor:cursor + size]
            cursor += size
        if cursor < len(dds_bytes):
            output += dds_bytes[cursor:]

    struct.pack_into("<4I", output, 32, *m_values)
    struct.pack_into("<7I", output, 48, 0, 0, 0, 0, 0, 0, 0)
    return bytes(output), (m_values[0], m_values[1], m_values[2], m_values[3])


def _get_dds_format_last4(dds_bytes: bytes) -> int:
    """根据 DDS 格式推断 PATHC record 的 last4。"""
    if len(dds_bytes) < 92:
        return 0
    fourcc = dds_bytes[84:88]
    if fourcc == b"DX10" and len(dds_bytes) >= 132:
        dxgi = struct.unpack_from("<I", dds_bytes, 128)[0]
        return _DDS_LAST4_BY_DXGI.get(dxgi, 0)
    return _DDS_LAST4_BY_FOURCC.get(fourcc, 0)


def _get_pathc_last4_for_path(game_dir: Path, virtual_path: str) -> int:
    """优先从 vanilla PATHC 读取目标 DDS 的 last4，失败时返回 0。"""
    pathc_path = game_dir / ".cdloader" / "vanilla" / "meta" / "0.pathc"
    if not pathc_path.exists():
        pathc_path = game_dir / "meta" / "0.pathc"
    if not pathc_path.exists():
        return 0
    cache_key = str(pathc_path)
    pathc = _pathc_cache.get(cache_key)
    if pathc is None:
        try:
            pathc = read_pathc(pathc_path)
            _pathc_cache[cache_key] = pathc
        except Exception as exc:
            logger.debug("PATHC last4 读取失败：%s (%s)", pathc_path, exc)
            return 0

    normalized = "/" + virtual_path.replace("\\", "/").strip().lstrip("/")
    path_hash = get_path_hash(normalized)
    import bisect

    index = bisect.bisect_left(pathc.key_hashes, path_hash)
    if index >= len(pathc.key_hashes) or pathc.key_hashes[index] != path_hash:
        return 0
    dds_index = pathc.map_entries[index].selector & 0xFFFF
    if not 0 <= dds_index < len(pathc.dds_records):
        return 0
    record = pathc.dds_records[dds_index]
    if len(record) < 128:
        return 0
    return struct.unpack_from("<I", record, 124)[0]


def _build_multi_pamt(entries: list[BuiltOverlayEntry], paz_data_len: int) -> bytes:
    """构建与游戏/JMM 兼容的单 PAZ overlay PAMT。"""
    unique_dirs = sorted({entry.dir_path for entry in entries})
    folder_bytes = bytearray()
    folder_offsets: dict[str, int] = {}

    for dir_path in unique_dirs:
        parts = dir_path.split("/") if dir_path else [""]
        for depth in range(len(parts)):
            key = "/".join(parts[: depth + 1])
            if key in folder_offsets:
                continue
            folder_offsets[key] = len(folder_bytes)
            if depth == 0:
                parent = 0xFFFFFFFF
                name = parts[0]
            else:
                parent = folder_offsets["/".join(parts[:depth])]
                name = "/" + parts[depth]
            name_bytes = name.encode("utf-8")
            folder_bytes += struct.pack("<I", parent)
            folder_bytes += bytes([len(name_bytes)])
            folder_bytes += name_bytes

    grouped: dict[str, list[tuple[int, BuiltOverlayEntry]]] = {}
    for index, entry in enumerate(entries):
        grouped.setdefault(entry.dir_path, []).append((index, entry))
    for dir_entries in grouped.values():
        dir_entries.sort(key=lambda item: item[1].filename)

    node_bytes = bytearray()
    node_offsets: dict[int, int] = {}
    for dir_path in unique_dirs:
        for index, entry in grouped.get(dir_path, []):
            node_offsets[index] = len(node_bytes)
            name_bytes = entry.filename.encode("utf-8")
            node_bytes += struct.pack("<I", 0xFFFFFFFF)
            node_bytes += bytes([len(name_bytes)])
            node_bytes += name_bytes

    folder_records = bytearray()
    file_index = 0
    for dir_path in unique_dirs:
        count = len(grouped.get(dir_path, []))
        folder_records += struct.pack(
            "<IIII",
            hashlittle(dir_path.encode("utf-8"), HASH_SEED),
            folder_offsets.get(dir_path, 0),
            file_index,
            count,
        )
        file_index += count

    file_records = bytearray()
    for dir_path in unique_dirs:
        for index, entry in grouped.get(dir_path, []):
            file_records += struct.pack(
                "<IIIIHH",
                node_offsets[index],
                entry.paz_offset,
                entry.comp_size,
                entry.decomp_size,
                0,
                entry.flags,
            )

    body = bytearray()
    body += struct.pack("<I", 1)
    body += struct.pack("<I", PAMT_CONSTANT)
    body += struct.pack("<I", 0)
    body += struct.pack("<I", 0)
    body += struct.pack("<I", paz_data_len)
    body += struct.pack("<I", len(folder_bytes)) + folder_bytes
    body += struct.pack("<I", len(node_bytes)) + node_bytes
    body += struct.pack("<I", len(unique_dirs)) + folder_records
    body += struct.pack("<I", file_index) + file_records
    return bytes(bytearray(4) + body)


def _resolve_dir_path(entry_path: str, pamt_dir: str, game_dir: Path) -> str:
    """从 vanilla PAMT 尽量恢复完整目录路径，失败时使用 entry_path 父目录。"""
    path_map = _build_full_path_map(game_dir / pamt_dir / OVERLAY_PAMT_NAME)
    if entry_path in path_map:
        return path_map[entry_path]
    return entry_path.rsplit("/", 1)[0] if "/" in entry_path else ""


def _build_full_path_map(pamt_path: Path) -> dict[str, str]:
    """解析 PAMT folder records，建立 flattened entry_path 到完整目录的映射。"""
    if not pamt_path.exists():
        return {}
    stat = pamt_path.stat()
    cache_key = (str(pamt_path.resolve()), stat.st_mtime_ns, stat.st_size)
    cached = _full_path_map_cache.get(cache_key)
    if cached is not None:
        return cached
    data = pamt_path.read_bytes()
    if len(data) < 24:
        return {}
    try:
        offset = 16
        paz_count = struct.unpack_from("<I", data, 4)[0]
        for index in range(paz_count):
            offset += 8
            if index < paz_count - 1:
                offset += 4
        folder_len = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        folders: dict[int, tuple[int, str]] = {}
        folder_start = offset
        while offset < folder_start + folder_len:
            rel = offset - folder_start
            parent = struct.unpack_from("<I", data, offset)[0]
            name_len = data[offset + 4]
            name = data[offset + 5:offset + 5 + name_len].decode("utf-8", errors="replace")
            folders[rel] = (parent, name)
            offset += 5 + name_len

        def build_folder(ref: int) -> str:
            parts: list[str] = []
            current = ref
            while current != 0xFFFFFFFF and len(parts) < 32:
                if current not in folders:
                    break
                parent, name = folders[current]
                parts.append(name)
                current = parent
            return "".join(reversed(parts))

        node_len = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        nodes: dict[int, tuple[int, str]] = {}
        node_start = offset
        while offset < node_start + node_len:
            rel = offset - node_start
            parent = struct.unpack_from("<I", data, offset)[0]
            name_len = data[offset + 4]
            name = data[offset + 5:offset + 5 + name_len].decode("utf-8", errors="replace")
            nodes[rel] = (parent, name)
            offset += 5 + name_len

        def build_node(ref: int) -> str:
            parts: list[str] = []
            current = ref
            while current != 0xFFFFFFFF and len(parts) < 64:
                if current not in nodes:
                    break
                parent, name = nodes[current]
                parts.append(name)
                current = parent
            return "".join(reversed(parts))

        folder_count = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        folder_records = []
        for _ in range(folder_count):
            _hash, folder_ref, file_index, file_count = struct.unpack_from("<IIII", data, offset)
            folder_records.append((build_folder(folder_ref), file_index, file_count))
            offset += 16
        file_count = struct.unpack_from("<I", data, offset)[0]
        offset += 4

        root = next((name for parent, name in folders.values() if parent == 0xFFFFFFFF), "")
        file_to_folder: dict[int, str] = {}
        for folder_path, start, count in folder_records:
            for index in range(start, start + count):
                file_to_folder[index] = folder_path

        result: dict[str, str] = {}
        for index in range(file_count):
            node_ref = struct.unpack_from("<I", data, offset)[0]
            offset += 20
            filename = build_node(node_ref)
            folder = file_to_folder.get(index)
            if folder is not None:
                flattened = f"{root}/{filename}" if root else filename
                result[flattened] = folder
        _full_path_map_cache[cache_key] = result
        return result
    except Exception:
        return {}


def _is_overlay_dir_name(value: str) -> bool:
    """判断是否为四位数字目录名。"""
    return value.isdigit() and len(value) == GAME_DIR_NAME_LENGTH


def _looks_like_loader_overlay(path: Path) -> bool:
    """确认目录只包含加载器 overlay 输出文件。"""
    if not path.is_dir():
        return False
    names = {item.name for item in path.iterdir()}
    return names.issubset({OVERLAY_PAZ_NAME, OVERLAY_PAMT_NAME})
