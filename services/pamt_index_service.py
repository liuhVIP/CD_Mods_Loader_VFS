"""游戏 PAMT 目标索引缓存服务。

加载模组时会大量按完整路径或 basename 查找游戏 entry。以前每个目标都会
重新遍历所有 ``NNNN/0.pamt``，在模组多、PAMT 大时会明显拖慢 apply。
本模块把一次运行中的游戏 PAMT entry 建成索引，供 loose、JSON、Format 3
共用，保持原有匹配优先级但减少重复 I/O 和重复结构解析。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from cdmm.archive.pamt import derive_pamt_dir, parse_pamt
from cdmm.common.constants import GAME_DIR_NAME_LENGTH, OVERLAY_PAMT_NAME, OVERLAY_START_DIR
from cdmm.common.models import PazEntry
from cdmm.utils.path_utils import lower_game_rel_path

logger = logging.getLogger(__name__)


@dataclass
class GamePamtIndex:
    """按目录、完整路径、basename 聚合后的 PAMT 查询索引。"""

    by_dir: dict[str, list[PazEntry]]
    by_exact: dict[str, list[PazEntry]]
    by_basename: dict[str, list[PazEntry]]

    def entries_in_dir(self, pamt_dir: str) -> list[PazEntry]:
        """读取指定 NNNN 目录下的 entry 列表。"""
        return self.by_dir.get(pamt_dir, [])

    def find_best(
        self,
        target: str,
        *,
        suffix: str | None = None,
        require_unique_best: bool = True,
    ) -> PazEntry | None:
        """按旧规则查找目标 entry，可选择是否要求最佳候选唯一。"""
        normalized = lower_game_rel_path(target)
        if suffix and not normalized.endswith(suffix):
            normalized += suffix
        basename = os.path.basename(normalized)

        exact = _pick_best(
            self.by_exact.get(normalized, []),
            normalized,
            basename,
            require_unique_best=require_unique_best,
        )
        if exact is not None:
            return exact
        return _pick_best(
            self.by_basename.get(basename, []),
            normalized,
            basename,
            require_unique_best=require_unique_best,
        )


# 缓存 key 包含所有 live 0.pamt 的 mtime/size，用户恢复或重新 apply 后会自动失效。
_GAME_INDEX_CACHE: dict[tuple[str, tuple[tuple[str, int, int], ...]], GamePamtIndex] = {}


def get_game_pamt_index(game_dir: Path) -> GamePamtIndex:
    """构建或读取 game_dir 下所有 NNNN/0.pamt 的查询索引。"""
    signature = _pamt_signature(game_dir)
    cache_key = (str(game_dir.resolve()), signature)
    cached = _GAME_INDEX_CACHE.get(cache_key)
    if cached is not None:
        return cached

    by_dir: dict[str, list[PazEntry]] = {}
    by_exact: dict[str, list[PazEntry]] = {}
    by_basename: dict[str, list[PazEntry]] = {}
    for dir_name, _mtime, _size in signature:
        directory = game_dir / dir_name
        pamt_path = directory / OVERLAY_PAMT_NAME
        try:
            entries = parse_pamt(pamt_path, paz_dir=directory)
        except Exception as exc:
            logger.warning("跳过无法解析的 PAMT：%s (%s)", pamt_path, exc)
            continue
        by_dir[dir_name] = entries
        for entry in entries:
            normalized = lower_game_rel_path(entry.path)
            by_exact.setdefault(normalized, []).append(entry)
            by_basename.setdefault(os.path.basename(normalized), []).append(entry)

    index = GamePamtIndex(by_dir=by_dir, by_exact=by_exact, by_basename=by_basename)
    _GAME_INDEX_CACHE[cache_key] = index
    return index


def _pamt_signature(game_dir: Path) -> tuple[tuple[str, int, int], ...]:
    """生成原版 PAMT 文件状态签名，用于缓存失效。"""
    items: list[tuple[str, int, int]] = []
    for directory in sorted(game_dir.iterdir(), key=lambda item: item.name):
        if not _is_numbered_game_dir(directory):
            continue
        if int(directory.name) >= OVERLAY_START_DIR:
            continue
        pamt_path = directory / OVERLAY_PAMT_NAME
        if not pamt_path.exists():
            continue
        stat = pamt_path.stat()
        items.append((directory.name, stat.st_mtime_ns, stat.st_size))
    return tuple(items)


def _pick_best(
    matches: list[PazEntry],
    normalized: str,
    basename: str,
    *,
    require_unique_best: bool,
) -> PazEntry | None:
    """按完整路径、gamedata 语义和低编号目录评分选择候选。"""
    if not matches:
        return None
    scored = sorted(((_match_score(entry, normalized, basename), entry) for entry in matches), key=lambda item: item[0])
    if not require_unique_best:
        return scored[0][1]
    if len(scored) == 1 or scored[0][0] < scored[1][0]:
        return scored[0][1]
    return None


def _match_score(entry: PazEntry, normalized: str, basename: str) -> tuple[int, int, int]:
    """通用目标匹配排序：完整路径、gamedata 路径、低编号目录优先。"""
    entry_key = lower_game_rel_path(entry.path)
    try:
        pamt_number = int(derive_pamt_dir(entry.paz_file))
    except ValueError:
        pamt_number = 9999
    exact_score = 0 if entry_key == normalized else 1
    gamedata_score = 0 if entry_key.startswith("gamedata/") else 1
    basename_score = 0 if os.path.basename(entry_key) == basename else 1
    return exact_score, gamedata_score + basename_score, pamt_number


def _is_numbered_game_dir(path: Path) -> bool:
    """判断路径是否为 NNNN 游戏归档目录。"""
    return path.is_dir() and path.name.isdigit() and len(path.name) == GAME_DIR_NAME_LENGTH
