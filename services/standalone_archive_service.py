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
) -> list[StandaloneArchive]:
    """收集 mods/*/NNNN/0.paz + 0.pamt，并分配不会冲突的新目录。"""
    mods_dir = game_dir / MODS_DIR_NAME
    if not mods_dir.exists():
        return []

    used_dirs = _collect_used_dirs(game_dir)
    if reserved_dirs:
        used_dirs.update(int(item) for item in reserved_dirs if _is_dir_name(item))

    result: list[StandaloneArchive] = []
    for source_dir in _iter_standalone_dirs(mods_dir):
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


def _iter_standalone_dirs(mods_dir: Path) -> list[Path]:
    """按稳定顺序枚举 standalone archive 目录。"""
    result: list[Path] = []
    for mod_dir in sorted((item for item in mods_dir.iterdir() if item.is_dir()), key=_path_sort_key):
        for child in sorted(
            (item for item in mod_dir.iterdir() if item.is_dir() and _is_dir_name(item.name)),
            key=_path_sort_key,
        ):
            if (child / OVERLAY_PAZ_NAME).is_file() and (child / OVERLAY_PAMT_NAME).is_file():
                result.append(child)
    return result


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


def _path_sort_key(path: Path) -> str:
    """统一路径排序，保证分配结果稳定。"""
    return path.as_posix().lower()
