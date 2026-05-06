"""files/NNNN loose game files 转 overlay entry 服务。"""

from __future__ import annotations

import logging
from time import perf_counter
from pathlib import Path

from cdmm.archive.pamt import derive_pamt_dir
from cdmm.common.constants import (
    GAME_DIR_NAME_LENGTH,
    KNOWN_GAME_TOP_DIRS,
    LOOSE_FILES_DIR_NAME,
    MODS_DIR_NAME,
    OVERLAY_PAMT_NAME,
    OVERLAY_PAZ_NAME,
)
from cdmm.common.models import OverlayInputEntry, PazEntry
from cdmm.services.pamt_index_service import get_game_pamt_index
from cdmm.storage.vanilla_store import VanillaStore
from cdmm.utils.path_utils import lower_game_rel_path

logger = logging.getLogger(__name__)


def build_loose_overlay_entries(
    game_dir: Path,
    vanilla_store: VanillaStore,
    warnings: list[str],
    errors: list[str],
) -> list[OverlayInputEntry]:
    """收集 files/NNNN 和根部 NNNN loose 文件并生成 overlay entry。"""
    mods_dir = game_dir / MODS_DIR_NAME
    if not mods_dir.exists():
        return []

    phase_started = perf_counter()
    loose_files = _iter_loose_files(mods_dir)
    enumerate_seconds = perf_counter() - phase_started
    numbered_count = sum(1 for _mod_dir, pamt_dir, _rel_path, _loose_path in loose_files if pamt_dir is not None)
    root_count = len(loose_files) - numbered_count

    entries: list[OverlayInputEntry] = []
    match_seconds = 0.0
    build_seconds = 0.0
    skipped = 0
    for loose_file in loose_files:
        mod_dir, pamt_dir, rel_path, loose_path = loose_file
        target_path = rel_path.as_posix()
        try:
            match_started = perf_counter()
            source_entry = (
                _find_entry_in_pamt_dir(game_dir, pamt_dir, target_path)
                if pamt_dir is not None
                else _find_entry_globally(game_dir, target_path)
            )
            match_seconds += perf_counter() - match_started
            if pamt_dir is None and source_entry is None:
                warnings.append(f"{mod_dir.name}: {target_path} 未在唯一 vanilla PAMT 中命中，已跳过")
                skipped += 1
                continue
            target_pamt_dir = pamt_dir or derive_pamt_dir(source_entry.paz_file)
            build_started = perf_counter()
            entries.append(
                _build_entry_from_loose_file(
                    mod_dir.name,
                    loose_path,
                    target_pamt_dir,
                    target_path,
                    source_entry,
                    vanilla_store,
                    warnings,
                )
            )
            build_seconds += perf_counter() - build_started
        except Exception as exc:
            prefix = pamt_dir if pamt_dir is not None else "root"
            errors.append(f"{mod_dir.name}: {prefix}/{target_path} 加载失败：{exc}")
    logger.info(
        "loose 细分：枚举 %.2fs，目标匹配 %.2fs，读取/构建 %.2fs，文件 %d 个（编号 %d，根路径 %d，跳过 %d）",
        enumerate_seconds,
        match_seconds,
        build_seconds,
        len(loose_files),
        numbered_count,
        root_count,
        skipped,
    )
    return entries


def collect_loose_pamt_targets(game_dir: Path) -> list[str]:
    """收集 loose 阶段会按 PAMT 查询的目标路径，用于冷启动预筛选。"""
    mods_dir = game_dir / MODS_DIR_NAME
    if not mods_dir.exists():
        return []
    return [rel_path.as_posix() for _mod_dir, _pamt_dir, rel_path, _loose_path in _iter_loose_files(mods_dir)]


def _iter_loose_files(mods_dir: Path) -> list[tuple[Path, str | None, Path, Path]]:
    """按模组目录顺序枚举 files/NNNN 与根部 NNNN 下的实际文件。"""
    result: list[tuple[Path, str | None, Path, Path]] = []
    for mod_dir in sorted((item for item in mods_dir.iterdir() if item.is_dir()), key=_path_sort_key):
        files_dir = mod_dir / LOOSE_FILES_DIR_NAME
        if files_dir.is_dir():
            result.extend(_iter_numbered_loose_dirs(mod_dir, files_dir))
            result.extend(_iter_root_game_path_loose_files(mod_dir, files_dir))
        result.extend(_iter_numbered_loose_dirs(mod_dir, mod_dir))
        result.extend(_iter_root_game_path_loose_files(mod_dir, mod_dir))
    return result


def _iter_numbered_loose_dirs(
    root_mod_dir: Path,
    parent: Path,
) -> list[tuple[Path, str | None, Path, Path]]:
    """枚举 parent/NNNN loose 文件，standalone PAZ/PAMT 目录会跳过。"""
    result: list[tuple[Path, str | None, Path, Path]] = []
    for numbered_dir in sorted(
        (item for item in parent.iterdir() if _is_game_archive_dir(item)),
        key=_path_sort_key,
    ):
        if _looks_like_standalone_archive(numbered_dir):
            continue
        for path in sorted(
            (item for item in numbered_dir.rglob("*") if item.is_file()),
            key=_path_sort_key,
        ):
            result.append((root_mod_dir, numbered_dir.name, path.relative_to(numbered_dir), path))
    return result


def _iter_root_game_path_loose_files(
    root_mod_dir: Path,
    parent: Path,
) -> list[tuple[Path, str | None, Path, Path]]:
    """枚举直接游戏路径 loose 文件，如 gamedata/... 或 files/gamedata/...。"""
    result: list[tuple[Path, str | None, Path, Path]] = []
    for child in sorted((item for item in parent.iterdir() if item.is_dir()), key=_path_sort_key):
        if child.name.lower() not in KNOWN_GAME_TOP_DIRS:
            continue
        for path in sorted((item for item in child.rglob("*") if item.is_file()), key=_path_sort_key):
            result.append((root_mod_dir, None, path.relative_to(parent), path))
    return result


def _build_entry_from_loose_file(
    mod_name: str,
    loose_path: Path,
    pamt_dir: str,
    target_path: str,
    source_entry: PazEntry | None,
    vanilla_store: VanillaStore,
    warnings: list[str],
) -> OverlayInputEntry:
    """基于 loose 文件内容和 vanilla 元数据构造 overlay entry。"""
    content = loose_path.read_bytes()
    if source_entry is None:
        warnings.append(f"{mod_name}: {target_path} 未在 {pamt_dir}/0.pamt 中找到，按原始路径写入")
        return OverlayInputEntry(
            content=content,
            entry_path=target_path,
            pamt_dir=pamt_dir,
            compression_type=0,
        )

    # 确保原始 PAZ/PAMT 已备份，后续 overlay 构建可复用完整目录结构和压缩/加密标记。
    vanilla_entry = vanilla_store.ensure_entry_backup(source_entry)
    return OverlayInputEntry(
        content=content,
        entry_path=vanilla_entry.path,
        pamt_dir=derive_pamt_dir(vanilla_entry.paz_file),
        compression_type=vanilla_entry.compression_type,
        encrypted=vanilla_entry.encrypted,
        crypto_filename=Path(vanilla_entry.path).name,
    )


def _find_entry_in_pamt_dir(game_dir: Path, pamt_dir: str, target_path: str) -> PazEntry | None:
    """只在 files/NNNN 指定的游戏目录中查找目标，避免被旧 overlay 干扰。"""
    normalized = lower_game_rel_path(target_path)
    entry = get_game_pamt_index(game_dir).find_in_dir(pamt_dir, target_path)
    if entry is not None and lower_game_rel_path(entry.path) != normalized:
        logger.info("按 basename 匹配 loose %s/%s -> %s", pamt_dir, target_path, entry.path)
    return entry


def _find_entry_globally(game_dir: Path, target_path: str) -> PazEntry | None:
    """根路径 loose 没有 NNNN，优先完整路径，其次安全 basename 命中。"""
    normalized = lower_game_rel_path(target_path)

    index = get_game_pamt_index(game_dir)
    entry = index.find_best(target_path)
    if entry is not None:
        if lower_game_rel_path(entry.path) != normalized:
            logger.info("按唯一 basename 匹配 root loose %s -> %s", target_path, entry.path)
        return entry
    logger.warning("root loose %s 未命中唯一 PAMT entry，已跳过", target_path)
    return None


def _pick_best_global_match(
    matches: list[PazEntry],
    normalized: str,
    basename: str,
) -> PazEntry | None:
    """从 root loose 候选中选择最像 vanilla 的 entry，无法唯一判断则返回 None。"""
    if not matches:
        return None
    scored = sorted(((_global_match_score(entry, normalized, basename), entry) for entry in matches), key=lambda item: item[0])
    if len(scored) == 1 or scored[0][0] < scored[1][0]:
        return scored[0][1]
    return None


def _global_match_score(entry: PazEntry, normalized: str, basename: str) -> tuple[int, int, int]:
    """root loose 匹配排序：完整路径、gamedata 路径、低编号原版目录优先。"""
    entry_key = lower_game_rel_path(entry.path)
    try:
        pamt_number = int(Path(entry.paz_file).parent.name)
    except ValueError:
        pamt_number = 9999
    exact_score = 0 if entry_key == normalized else 1
    gamedata_score = 0 if entry_key.startswith("gamedata/") else 1
    basename_score = 0 if entry_key.rsplit("/", 1)[-1] == basename else 1
    return exact_score, gamedata_score + basename_score, pamt_number


def _is_game_archive_dir(path: Path) -> bool:
    """判断是否为 0000-9999 形式的游戏归档目录。"""
    return path.is_dir() and len(path.name) == GAME_DIR_NAME_LENGTH and path.name.isdigit()


def _looks_like_standalone_archive(path: Path) -> bool:
    """判断 NNNN 目录是否是 standalone PAZ/PAMT，而不是 loose files。"""
    return (path / OVERLAY_PAZ_NAME).is_file() and (path / OVERLAY_PAMT_NAME).is_file()


def _path_sort_key(path: Path) -> str:
    """统一路径排序，保证测试和真实加载顺序稳定。"""
    return path.as_posix().lower()
