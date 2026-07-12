"""PAMT 解析与目标 entry 查找。"""

from __future__ import annotations

import logging
import os
import struct
from pathlib import Path

from cdmm.common.constants import GAME_DIR_NAME_LENGTH, OVERLAY_PAMT_NAME
from cdmm.common.models import PazEntry
from cdmm.cdloader_native import parse_pamt_filtered as native_parse_pamt_filtered
from cdmm.utils.path_utils import lower_game_rel_path

logger = logging.getLogger(__name__)

# 防止损坏 PAMT 声明超大 PAZ 数量导致无意义扫描。
MAX_SANE_PAZ_COUNT = 4096

# 单次运行内 PAMT 会被 loose、JSON、Format 3、overlay 目录恢复反复查询。
# 这里按文件状态缓存解析结果，避免同一个 0.pamt 被重复读盘和重复拆结构。
_PARSED_PAMT_CACHE: dict[tuple[str, int, int, str | None], list[PazEntry]] = {}


def parse_pamt(pamt_path: str | Path, paz_dir: str | Path | None = None) -> list[PazEntry]:
    """解析 PAMT 文件并返回可定位的 PAZ entry 列表。"""
    pamt_path = Path(pamt_path)
    stat = pamt_path.stat()
    cache_key = (
        str(pamt_path.resolve()),
        stat.st_mtime_ns,
        stat.st_size,
        str(Path(paz_dir).resolve()) if paz_dir else None,
    )
    cached = _PARSED_PAMT_CACHE.get(cache_key)
    if cached is not None:
        return list(cached)
    try:
        entries = _parse_pamt_impl(pamt_path, Path(paz_dir) if paz_dir else None)
        _PARSED_PAMT_CACHE[cache_key] = entries
        return list(entries)
    except (struct.error, IndexError) as exc:
        raise ValueError(f"损坏的 PAMT {pamt_path.name}: {exc}") from exc


def parse_pamt_filtered(
    pamt_path: str | Path,
    paz_dir: str | Path | None = None,
    *,
    desired_basenames: set[str] | None = None,
    desired_exact: set[str] | None = None,
) -> list[PazEntry]:
    """解析 PAMT 并只返回目标 basename / 完整路径命中的 entry。"""
    normalized_basenames = {name.lower() for name in desired_basenames or set()}
    normalized_exact = {lower_game_rel_path(path) for path in desired_exact or set()}
    if not normalized_basenames and not normalized_exact:
        return parse_pamt(pamt_path, paz_dir=paz_dir)

    pamt_path = Path(pamt_path)
    try:
        return _parse_pamt_impl(
            pamt_path,
            Path(paz_dir) if paz_dir else None,
            desired_basenames=normalized_basenames,
            desired_exact=normalized_exact,
        )
    except (struct.error, IndexError) as exc:
        raise ValueError(f"损坏的 PAMT {pamt_path.name}: {exc}") from exc


def _parse_pamt_impl(
    pamt_path: Path,
    paz_dir: Path | None,
    *,
    desired_basenames: set[str] | None = None,
    desired_exact: set[str] | None = None,
) -> list[PazEntry]:
    data = pamt_path.read_bytes()
    paz_dir = paz_dir or pamt_path.parent
    pamt_stem = pamt_path.stem

    if desired_basenames or desired_exact:
        native_matches = native_parse_pamt_filtered(
            data,
            desired_basenames or set(),
            desired_exact or set(),
        )
        if native_matches is not None:
            return [
                PazEntry(
                    path=path,
                    paz_file=str(paz_dir / f"{int(pamt_stem) + (flags & 0xFF)}.paz"),
                    offset=paz_offset,
                    comp_size=comp_size,
                    orig_size=orig_size,
                    flags=flags,
                    paz_index=flags & 0xFF,
                    resolved_dir_path=resolved_dir_path,
                )
                for path, paz_offset, comp_size, orig_size, flags, resolved_dir_path in native_matches
            ]

    if len(data) < 32:
        raise ValueError(f"损坏的 PAMT {pamt_path.name}: 文件过小")

    offset = 4
    paz_count = struct.unpack_from("<I", data, offset)[0]
    offset += 4
    if paz_count > MAX_SANE_PAZ_COUNT:
        raise ValueError(f"损坏的 PAMT {pamt_path.name}: paz_count={paz_count}")
    offset += 8

    for index in range(paz_count):
        offset += 8
        if index < paz_count - 1:
            offset += 4

    folder_size = struct.unpack_from("<I", data, offset)[0]
    offset += 4
    _check_section(pamt_path, "folder_size", offset, folder_size, len(data))
    folder_start = offset
    folder_end = offset + folder_size
    folder_prefix = ""
    folders: dict[int, tuple[int, str]] = {}
    while offset < folder_end:
        rel = offset - folder_start
        parent = struct.unpack_from("<I", data, offset)[0]
        name_len = data[offset + 4]
        name = data[offset + 5:offset + 5 + name_len].decode("utf-8", errors="replace")
        folders[rel] = (parent, name)
        if parent == 0xFFFFFFFF:
            folder_prefix = name
        offset += 5 + name_len
    folder_prefix_lower = folder_prefix.lower()

    folder_path_cache: dict[int, str] = {}

    def build_folder_path(folder_ref: int) -> str:
        """还原 folder record 的真实路径，并缓存共享父链。"""
        cached = folder_path_cache.get(folder_ref)
        if cached is not None:
            return cached
        parts: list[str] = []
        current = folder_ref
        while current != 0xFFFFFFFF and len(parts) < 64:
            cached_parent = folder_path_cache.get(current)
            if cached_parent is not None:
                parts.append(cached_parent)
                break
            item = folders.get(current)
            if item is None:
                break
            parent, name = item
            parts.append(name)
            current = parent
        path = "".join(reversed(parts)).rstrip("/\\")
        folder_path_cache[folder_ref] = path
        return path

    node_size = struct.unpack_from("<I", data, offset)[0]
    offset += 4
    _check_section(pamt_path, "node_size", offset, node_size, len(data))
    node_start = offset
    nodes: dict[int, tuple[int, str]] = {}
    while offset < node_start + node_size:
        rel = offset - node_start
        parent = struct.unpack_from("<I", data, offset)[0]
        name_len = data[offset + 4]
        name = data[offset + 5:offset + 5 + name_len].decode("utf-8", errors="replace")
        nodes[rel] = (parent, name)
        offset += 5 + name_len

    path_cache: dict[int, str] = {}

    def build_node_path(node_ref: int) -> str:
        """带父节点缓存地还原 entry 路径，避免冷构建反复拼同一目录链。"""
        cached = path_cache.get(node_ref)
        if cached is not None:
            return cached
        parts: list[str] = []
        current = node_ref
        while current != 0xFFFFFFFF and len(parts) < 64:
            cached_parent = path_cache.get(current)
            if cached_parent is not None:
                parts.append(cached_parent)
                break
            if current not in nodes:
                break
            parent, name = nodes[current]
            parts.append(name)
            current = parent
        node_path = "".join(reversed(parts))
        path_cache[node_ref] = node_path
        return node_path

    folder_count = struct.unpack_from("<I", data, offset)[0]
    offset += 4
    folder_ranges: list[tuple[int, int, str]] = []
    for _ in range(folder_count):
        _hash, folder_ref, file_index, range_count = struct.unpack_from("<IIII", data, offset)
        offset += 16
        folder_ranges.append((file_index, file_index + range_count, build_folder_path(folder_ref)))
    _ = struct.unpack_from("<I", data, offset)[0]
    offset += 4

    entries: list[PazEntry] = []
    exact_targets = desired_exact or set()
    basename_targets = desired_basenames or set()
    should_filter = bool(exact_targets or basename_targets)
    file_index = 0
    folder_range_index = 0
    while offset + 20 <= len(data):
        node_ref, paz_offset, comp_size, orig_size, flags = struct.unpack_from(
            "<IIIII", data, offset
        )
        offset += 20
        paz_index = flags & 0xFF
        while (
            folder_range_index + 1 < len(folder_ranges)
            and file_index >= folder_ranges[folder_range_index][1]
        ):
            folder_range_index += 1
        resolved_dir_path = None
        if folder_ranges:
            range_start, range_end, range_path = folder_ranges[folder_range_index]
            if range_start <= file_index < range_end:
                resolved_dir_path = range_path
        file_index += 1
        node_path = build_node_path(node_ref)
        if should_filter:
            # node_path 是 PAMT 当前 folder 下的文件名主体；这里直接用它判断
            # basename，避免对上百万 entry 反复调用 Windows ntpath.basename。
            node_path_lower = node_path.lower()
            normalized = (
                f"{folder_prefix_lower}/{node_path_lower}"
                if folder_prefix_lower
                else node_path_lower
            )
            if normalized not in exact_targets and node_path_lower not in basename_targets:
                continue
        full_path = f"{folder_prefix}/{node_path}" if folder_prefix else node_path
        paz_num = int(pamt_stem) + paz_index
        entries.append(
            PazEntry(
                path=full_path,
                paz_file=str(paz_dir / f"{paz_num}.paz"),
                offset=paz_offset,
                comp_size=comp_size,
                orig_size=orig_size,
                flags=flags,
                paz_index=paz_index,
                resolved_dir_path=resolved_dir_path,
            )
        )
    return entries


def _check_section(path: Path, name: str, offset: int, size: int, total: int) -> None:
    """校验 PAMT section 边界，避免读取损坏数据。"""
    if size > total or offset + size > total:
        raise ValueError(f"损坏的 PAMT {path.name}: {name} 越界")


def build_pamt_index(game_dir: Path) -> dict[str, PazEntry]:
    """扫描游戏目录下所有 NNNN/0.pamt，构建按完整路径和 basename 查询的索引。"""
    index: dict[str, PazEntry] = {}
    for directory in sorted(game_dir.iterdir()):
        if not _is_numbered_game_dir(directory):
            continue
        pamt_path = directory / OVERLAY_PAMT_NAME
        if not pamt_path.exists():
            continue
        try:
            for entry in parse_pamt(pamt_path, paz_dir=directory):
                normalized = lower_game_rel_path(entry.path)
                index[normalized] = entry
                index[os.path.basename(normalized)] = entry
        except Exception as exc:
            logger.warning("跳过无法解析的 PAMT：%s (%s)", pamt_path, exc)
    return index


def find_pamt_entry(game_file: str, game_dir: Path) -> PazEntry | None:
    """在指定目录的 PAMT 索引里查找目标游戏文件。"""
    index = build_pamt_index(game_dir)
    normalized = lower_game_rel_path(game_file)
    entry = index.get(normalized)
    if entry is not None:
        return entry
    basename = normalized.rsplit("/", 1)[-1]
    entry = index.get(basename)
    if entry is not None:
        logger.info("按 basename 匹配 %s -> %s", game_file, entry.path)
    return entry


def derive_pamt_dir(paz_file: str | Path) -> str:
    """根据 PAZ 路径得到所在的四位数字目录名。"""
    return Path(paz_file).parent.name


def _is_numbered_game_dir(path: Path) -> bool:
    """判断路径是否为四位数字游戏数据目录。"""
    return path.is_dir() and path.name.isdigit() and len(path.name) == GAME_DIR_NAME_LENGTH
