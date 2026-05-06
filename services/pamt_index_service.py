"""游戏 PAMT 目标查询服务。

加载器只需要为 loose、JSON、Format 3 找到少量目标 entry。这里不再把
全游戏 entry 写成巨大 JSON，而是：

1. 本次运行内按需解析原版 PAMT，并缓存解析结果；
2. 对已经命中的目标写入很小的目标缓存；
3. 下次加载时只复用这些具体目标的命中结果。
"""

from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cdmm.archive.pamt import derive_pamt_dir, parse_pamt
from cdmm.common.constants import (
    GAME_DIR_NAME_LENGTH,
    OVERLAY_PAMT_NAME,
    OVERLAY_START_DIR,
    PAMT_INDEX_WORKER_COUNT,
    PAMT_TARGET_CACHE_FILE_NAME,
    PAMT_TARGET_CACHE_SCHEMA,
    WORK_DIR_NAME,
)
from cdmm.common.models import PazEntry
from cdmm.utils.path_utils import lower_game_rel_path

logger = logging.getLogger(__name__)


@dataclass
class GamePamtIndex:
    """一次运行内的原版 PAMT 查询上下文。"""

    game_dir: Path
    signature: tuple[tuple[str, int, int], ...]
    by_dir: dict[str, list[PazEntry]]
    target_cache: dict[str, PazEntry | None]
    desired_basenames: set[str]
    desired_exact: set[str]
    target_cache_dirty: bool = False

    def entries_in_dir(self, pamt_dir: str) -> list[PazEntry]:
        """按需读取指定 NNNN 目录下的 entry 列表。"""
        if pamt_dir not in self.by_dir:
            self.by_dir[pamt_dir] = _parse_entries_in_dir(self.game_dir, pamt_dir)
        return self.by_dir.get(pamt_dir, [])

    def find_in_dir(self, pamt_dir: str, target: str) -> PazEntry | None:
        """只在指定目录中查找目标，优先完整路径，其次 basename。"""
        normalized = lower_game_rel_path(target)
        basename = os.path.basename(normalized)
        self.register_target(normalized)
        if pamt_dir not in self.by_dir:
            desired_basenames = {*self.desired_basenames, basename}
            desired_exact = {*self.desired_exact, normalized}
            self.by_dir[pamt_dir] = _parse_filtered_entries_in_dir(
                self.game_dir,
                pamt_dir,
                desired_basenames,
                desired_exact,
            )
        basename_match: PazEntry | None = None
        for entry in self.by_dir.get(pamt_dir, []):
            entry_key = lower_game_rel_path(entry.path)
            if entry_key == normalized:
                return entry
            if os.path.basename(entry_key) == basename:
                basename_match = entry
        return basename_match

    def find_best(
        self,
        target: str,
        *,
        suffix: str | None = None,
        require_unique_best: bool = True,
    ) -> PazEntry | None:
        """按完整路径、basename、gamedata、低编号规则按需查找目标 entry。"""
        normalized = lower_game_rel_path(target)
        if suffix and not normalized.endswith(suffix):
            normalized += suffix
        cache_key = _target_cache_key(normalized, require_unique_best=require_unique_best)
        if cache_key in self.target_cache:
            return self.target_cache[cache_key]

        basename = os.path.basename(normalized)
        self.register_target(normalized)
        exact_matches: list[PazEntry] = []
        basename_matches: list[PazEntry] = []
        self._ensure_all_dirs_loaded(target_basename=basename)
        for dir_name, _mtime, _size in self.signature:
            for entry in self.entries_in_dir(dir_name):
                entry_key = lower_game_rel_path(entry.path)
                if entry_key == normalized:
                    exact_matches.append(entry)
                if os.path.basename(entry_key) == basename:
                    basename_matches.append(entry)

        match = _pick_best(
            exact_matches,
            normalized,
            basename,
            require_unique_best=require_unique_best,
        )
        if match is None:
            match = _pick_best(
                basename_matches,
                normalized,
                basename,
                require_unique_best=require_unique_best,
            )
        self.target_cache[cache_key] = match
        self.target_cache_dirty = True
        return match

    def register_target(self, target: str, *, suffix: str | None = None) -> None:
        """登记本次运行将查询的目标，供冷启动解析时预筛选候选。"""
        normalized = lower_game_rel_path(target)
        if suffix and not normalized.endswith(suffix):
            normalized += suffix
        self.desired_exact.add(normalized)
        self.desired_basenames.add(os.path.basename(normalized))

    def register_targets(self, targets: list[str]) -> None:
        """批量登记本次运行将查询的目标。"""
        for target in targets:
            self.register_target(target)

    def _ensure_all_dirs_loaded(self, *, target_basename: str) -> None:
        """首次全局目标查找时并行读取尚未解析的原版 PAMT。"""
        missing_dirs = [dir_name for dir_name, _mtime, _size in self.signature if dir_name not in self.by_dir]
        if not missing_dirs:
            return
        if not self.desired_basenames:
            self.desired_basenames.add(target_basename)
        worker_count = max(1, min(PAMT_INDEX_WORKER_COUNT, len(missing_dirs)))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            parsed_items = list(
                executor.map(
                    lambda dir_name: (
                        dir_name,
                        _parse_filtered_entries_in_dir(
                            self.game_dir,
                            dir_name,
                            self.desired_basenames,
                            self.desired_exact,
                        ),
                    ),
                    missing_dirs,
                )
            )
        for dir_name, entries in parsed_items:
            self.by_dir[dir_name] = entries
        logger.info(
            "PAMT 目标预筛选：多线程解析 %d 个 PAMT，线程 %d，目标 basename %d 个",
            len(missing_dirs),
            worker_count,
            len(self.desired_basenames),
        )

    def save_target_cache(self) -> None:
        """保存本次新增的目标命中缓存。"""
        if self.target_cache_dirty:
            _save_target_cache(self.game_dir, self.signature, self.target_cache)
            self.target_cache_dirty = False


_GAME_INDEX_CACHE: dict[tuple[str, tuple[tuple[str, int, int], ...]], GamePamtIndex] = {}


def get_game_pamt_index(game_dir: Path) -> GamePamtIndex:
    """获取当前游戏目录的按需 PAMT 查询上下文。"""
    signature = _pamt_signature(game_dir)
    cache_key = (str(game_dir.resolve()), signature)
    cached = _GAME_INDEX_CACHE.get(cache_key)
    if cached is not None:
        return cached

    target_cache = _load_target_cache(game_dir, signature)
    if target_cache:
        logger.info("PAMT 目标缓存：已读取 %d 条命中", len(target_cache))
    index = GamePamtIndex(
        game_dir=game_dir,
        signature=signature,
        by_dir={},
        target_cache=target_cache,
        desired_basenames=set(),
        desired_exact=set(),
    )
    _GAME_INDEX_CACHE[cache_key] = index
    return index


def save_game_pamt_target_cache(game_dir: Path) -> None:
    """保存当前运行内新增的 PAMT 目标命中缓存。"""
    signature = _pamt_signature(game_dir)
    cache_key = (str(game_dir.resolve()), signature)
    index = _GAME_INDEX_CACHE.get(cache_key)
    if index is not None:
        index.save_target_cache()


def register_game_pamt_targets(game_dir: Path, targets: list[str]) -> None:
    """登记本次 apply 即将查询的 PAMT 目标。"""
    get_game_pamt_index(game_dir).register_targets(targets)


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


def _parse_entries_in_dir(game_dir: Path, pamt_dir: str) -> list[PazEntry]:
    """解析单个原版目录的 PAMT。"""
    directory = game_dir / pamt_dir
    pamt_path = directory / OVERLAY_PAMT_NAME
    try:
        return parse_pamt(pamt_path, paz_dir=directory)
    except Exception as exc:
        logger.warning("跳过无法解析的 PAMT：%s (%s)", pamt_path, exc)
        return []


def _parse_filtered_entries_in_dir(
    game_dir: Path,
    pamt_dir: str,
    desired_basenames: set[str],
    desired_exact: set[str],
) -> list[PazEntry]:
    """解析单个 PAMT 后只保留本次目标可能用到的 entry。"""
    entries = _parse_entries_in_dir(game_dir, pamt_dir)
    if not desired_basenames and not desired_exact:
        return entries
    filtered: list[PazEntry] = []
    for entry in entries:
        entry_key = lower_game_rel_path(entry.path)
        if entry_key in desired_exact or os.path.basename(entry_key) in desired_basenames:
            filtered.append(entry)
    return filtered


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


def _cache_path(game_dir: Path) -> Path:
    """返回持久化目标命中缓存路径。"""
    return game_dir / WORK_DIR_NAME / PAMT_TARGET_CACHE_FILE_NAME


def _target_cache_key(normalized: str, *, require_unique_best: bool) -> str:
    """目标缓存 key，区分是否要求唯一最佳候选。"""
    unique_flag = "unique" if require_unique_best else "best"
    return f"{unique_flag}|{normalized}"


def _load_target_cache(
    game_dir: Path,
    signature: tuple[tuple[str, int, int], ...],
) -> dict[str, PazEntry | None]:
    """读取签名一致的目标命中缓存。"""
    path = _cache_path(game_dir)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning("PAMT 目标缓存读取失败，将重建：%s", exc)
        return {}
    if data.get("schema") != PAMT_TARGET_CACHE_SCHEMA:
        return {}
    if _signature_from_json(data.get("signature")) != signature:
        return {}
    raw_targets = data.get("targets")
    if not isinstance(raw_targets, dict):
        return {}

    target_cache: dict[str, PazEntry | None] = {}
    for key, raw_entry in raw_targets.items():
        if not isinstance(key, str):
            return {}
        if raw_entry is None:
            target_cache[key] = None
            continue
        entry = _entry_from_json(raw_entry)
        if entry is None:
            return {}
        target_cache[key] = entry
    return target_cache


def _save_target_cache(
    game_dir: Path,
    signature: tuple[tuple[str, int, int], ...],
    target_cache: dict[str, PazEntry | None],
) -> None:
    """写入目标命中缓存，缓存体只包含实际查询过的目标。"""
    path = _cache_path(game_dir)
    data = {
        "schema": PAMT_TARGET_CACHE_SCHEMA,
        "signature": [list(item) for item in signature],
        "targets": {
            key: (_entry_to_json(entry) if entry is not None else None)
            for key, entry in sorted(target_cache.items())
        },
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(path.suffix + ".tmp")
        temp_path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        temp_path.replace(path)
    except OSError as exc:
        logger.warning("PAMT 目标缓存写入失败，将仅使用本次运行缓存：%s", exc)


def _signature_from_json(raw_signature: Any) -> tuple[tuple[str, int, int], ...] | None:
    """把 JSON 签名还原为不可变 tuple。"""
    if not isinstance(raw_signature, list):
        return None
    signature: list[tuple[str, int, int]] = []
    for item in raw_signature:
        if not isinstance(item, list) or len(item) != 3:
            return None
        dir_name, mtime, size = item
        if not isinstance(dir_name, str) or not isinstance(mtime, int) or not isinstance(size, int):
            return None
        signature.append((dir_name, mtime, size))
    return tuple(signature)


def _entry_to_json(entry: PazEntry) -> dict[str, Any]:
    """序列化一个目标命中的 entry。"""
    return {
        "path": entry.path,
        "paz_file": entry.paz_file,
        "offset": entry.offset,
        "comp_size": entry.comp_size,
        "orig_size": entry.orig_size,
        "flags": entry.flags,
        "paz_index": entry.paz_index,
        "encrypted_override": entry.encrypted_override,
    }


def _entry_from_json(raw_entry: Any) -> PazEntry | None:
    """反序列化一个目标命中的 entry，结构异常时返回 None。"""
    if not isinstance(raw_entry, dict):
        return None
    try:
        path = raw_entry["path"]
        paz_file = raw_entry["paz_file"]
        offset = raw_entry["offset"]
        comp_size = raw_entry["comp_size"]
        orig_size = raw_entry["orig_size"]
        flags = raw_entry["flags"]
        paz_index = raw_entry["paz_index"]
        encrypted_override = raw_entry.get("encrypted_override")
    except KeyError:
        return None
    if not isinstance(path, str) or not isinstance(paz_file, str):
        return None
    if not all(isinstance(value, int) for value in (offset, comp_size, orig_size, flags, paz_index)):
        return None
    if encrypted_override is not None and not isinstance(encrypted_override, bool):
        return None
    return PazEntry(
        path=path,
        paz_file=paz_file,
        offset=offset,
        comp_size=comp_size,
        orig_size=orig_size,
        flags=flags,
        paz_index=paz_index,
        encrypted_override=encrypted_override,
    )


def _is_numbered_game_dir(path: Path) -> bool:
    """判断路径是否为 NNNN 游戏归档目录。"""
    return path.is_dir() and path.name.isdigit() and len(path.name) == GAME_DIR_NAME_LENGTH
