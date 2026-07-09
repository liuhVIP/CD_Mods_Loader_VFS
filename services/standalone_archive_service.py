"""standalone PAZ/PAMT 模组分配与暂存服务。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cdmm.common.constants import (
    GAME_DIR_NAME_LENGTH,
    MODS_DIR_NAME,
    OVERLAY_PAMT_NAME,
    OVERLAY_PAZ_NAME,
    OVERLAY_START_DIR,
)
from cdmm.common.models import DiscoveredMod


@dataclass(frozen=True)
class StandaloneArchive:
    """待写入游戏根目录的 standalone PAZ/PAMT 模组。"""

    mod_name: str
    source_dir: Path
    assigned_dir: str
    paz_bytes: bytes
    pamt_bytes: bytes


def collect_standalone_archives(
    game_dir: Path,
    reserved_dirs: set[str] | None = None,
    previous_items: list[dict[str, str]] | None = None,
    ordered_mods: list[DiscoveredMod] | None = None,
) -> list[StandaloneArchive]:
    """收集 standalone PAZ/PAMT，并优先复用上次分配目录。"""
    mods_dir = game_dir / MODS_DIR_NAME
    if not mods_dir.exists():
        return []

    used_dirs = _collect_used_dirs(game_dir)
    if reserved_dirs:
        used_dirs.update(int(item) for item in reserved_dirs if _is_dir_name(item))
    previous_by_source = _previous_assigned_dirs(game_dir, previous_items or [])

    result: list[StandaloneArchive] = []
    for source_dir in _iter_standalone_dirs(mods_dir, ordered_mods):
        source_key = _source_key(game_dir, source_dir)
        assigned_dir = previous_by_source.get(source_key)
        if assigned_dir is None or not _can_reuse_assigned_dir(game_dir, assigned_dir, used_dirs):
            assigned_dir = _next_free_dir(used_dirs)
        used_dirs.add(int(assigned_dir))
        result.append(
            StandaloneArchive(
                mod_name=source_dir.parent.name,
                source_dir=source_dir,
                assigned_dir=assigned_dir,
                paz_bytes=(source_dir / OVERLAY_PAZ_NAME).read_bytes(),
                pamt_bytes=(source_dir / OVERLAY_PAMT_NAME).read_bytes(),
            )
        )
    return result


def cleanup_stale_standalone_dirs(
    game_dir: Path,
    previous_items: list[dict[str, str]] | None,
    current_archives: list[StandaloneArchive],
) -> list[str]:
    """清理上次记录但本次不再使用的 standalone 输出目录。"""
    current_dirs = {archive.assigned_dir for archive in current_archives}
    removed: list[str] = []
    for item in previous_items or []:
        if not isinstance(item, dict):
            continue
        assigned_dir = item.get("assigned_dir")
        if not isinstance(assigned_dir, str) or assigned_dir in current_dirs:
            continue
        target = game_dir / assigned_dir
        if _looks_like_standalone_output(target):
            for child in target.iterdir():
                child.unlink()
            target.rmdir()
            removed.append(assigned_dir)
    return removed


def standalone_state_items(archives: list[StandaloneArchive]) -> list[dict[str, str]]:
    """把 standalone 分配结果写成 state.json 中的可恢复结构。"""
    return [
        {
            "mod_name": archive.mod_name,
            "source_dir": archive.source_dir.as_posix(),
            "assigned_dir": archive.assigned_dir,
        }
        for archive in archives
    ]


def _iter_standalone_dirs(
    mods_dir: Path,
    ordered_mods: list[DiscoveredMod] | None = None,
) -> list[Path]:
    """按稳定顺序枚举 standalone archive 目录。"""
    result: list[Path] = []
    for mod_dir in _iter_ordered_mod_dirs(mods_dir, ordered_mods):
        for child in sorted(
            (item for item in mod_dir.iterdir() if item.is_dir() and _is_dir_name(item.name)),
            key=_path_sort_key,
        ):
            if (child / OVERLAY_PAZ_NAME).is_file() and (child / OVERLAY_PAMT_NAME).is_file():
                result.append(child)
    return result


def _iter_ordered_mod_dirs(
    mods_dir: Path,
    ordered_mods: list[DiscoveredMod] | None,
) -> list[Path]:
    """按 scan_mods 解析出的加载顺序枚举目录型模组，其余目录按名称补齐。"""
    all_dirs = sorted((item for item in mods_dir.iterdir() if item.is_dir()), key=_path_sort_key)
    if not ordered_mods:
        return all_dirs

    by_resolved = {path.resolve(): path for path in all_dirs}
    ordered: list[Path] = []
    used: set[Path] = set()
    for mod in ordered_mods:
        if not mod.path.is_dir():
            continue
        mod_dir = by_resolved.get(mod.path.resolve())
        if mod_dir is None or mod_dir in used:
            continue
        ordered.append(mod_dir)
        used.add(mod_dir)
    ordered.extend(path for path in all_dirs if path not in used)
    return ordered


def _collect_used_dirs(game_dir: Path) -> set[int]:
    """收集游戏根目录已有的四位数字目录编号。"""
    return {int(item.name) for item in game_dir.iterdir() if item.is_dir() and _is_dir_name(item.name)}


def _next_free_dir(used_dirs: set[int]) -> str:
    """从 overlay 起始目录开始分配空闲目录。"""
    candidate = OVERLAY_START_DIR
    while candidate in used_dirs:
        candidate += 1
    return f"{candidate:0{GAME_DIR_NAME_LENGTH}d}"


def _is_dir_name(value: str) -> bool:
    """判断是否为四位数字游戏目录名。"""
    return len(value) == GAME_DIR_NAME_LENGTH and value.isdigit()


def _previous_assigned_dirs(game_dir: Path, previous_items: list[dict[str, str]]) -> dict[str, str]:
    """读取 state 中的 standalone 源目录 -> 分配目录映射。"""
    result: dict[str, str] = {}
    for item in previous_items:
        if not isinstance(item, dict):
            continue
        source_dir = item.get("source_dir")
        assigned_dir = item.get("assigned_dir")
        if not isinstance(source_dir, str) or not isinstance(assigned_dir, str):
            continue
        if not _is_dir_name(assigned_dir):
            continue
        result[_source_key(game_dir, Path(source_dir))] = assigned_dir
    return result


def _can_reuse_assigned_dir(game_dir: Path, assigned_dir: str, used_dirs: set[int]) -> bool:
    """判断 state 中的目录号是否可以安全复用。"""
    if not _is_dir_name(assigned_dir):
        return False
    assigned_number = int(assigned_dir)
    target = game_dir / assigned_dir
    if target.exists() and not _looks_like_standalone_output(target):
        return False
    return assigned_number not in used_dirs or _looks_like_standalone_output(target)


def _looks_like_standalone_output(path: Path) -> bool:
    """确认目录只包含 standalone 输出文件，才允许复用或清理。"""
    if not path.is_dir():
        return False
    names = {item.name for item in path.iterdir()}
    return names.issubset({OVERLAY_PAZ_NAME, OVERLAY_PAMT_NAME})


def _source_key(game_dir: Path, source_dir: Path) -> str:
    """规范化 standalone 源目录路径，用于跨运行复用目录号。"""
    if not source_dir.is_absolute():
        source_dir = game_dir / source_dir
    try:
        return source_dir.resolve().relative_to(game_dir.resolve()).as_posix().lower()
    except ValueError:
        return source_dir.resolve().as_posix().lower()


def _path_sort_key(path: Path) -> str:
    """统一路径排序，保证分配结果稳定。"""
    return path.as_posix().lower()
