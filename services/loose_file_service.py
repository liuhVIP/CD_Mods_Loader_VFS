"""files/NNNN loose game files 转 overlay entry 服务。"""

from __future__ import annotations

import logging
from pathlib import Path

from cdmm.archive.pamt import build_pamt_index, derive_pamt_dir, parse_pamt
from cdmm.common.constants import (
    GAME_DIR_NAME_LENGTH,
    KNOWN_GAME_TOP_DIRS,
    LOOSE_FILES_DIR_NAME,
    MODS_DIR_NAME,
    OVERLAY_PAMT_NAME,
    OVERLAY_PAZ_NAME,
)
from cdmm.common.models import OverlayInputEntry, PazEntry
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

    entries: list[OverlayInputEntry] = []
    for loose_file in _iter_loose_files(mods_dir):
        mod_dir, pamt_dir, rel_path, loose_path = loose_file
        target_path = rel_path.as_posix()
        try:
            source_entry = (
                _find_entry_in_pamt_dir(game_dir, pamt_dir, target_path)
                if pamt_dir is not None
                else _find_entry_globally(game_dir, target_path)
            )
            if pamt_dir is None and source_entry is None:
                warnings.append(f"{mod_dir.name}: {target_path} 未在唯一 vanilla PAMT 中命中，已跳过")
                continue
            target_pamt_dir = pamt_dir or derive_pamt_dir(source_entry.paz_file)
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
        except Exception as exc:
            prefix = pamt_dir if pamt_dir is not None else "root"
            errors.append(f"{mod_dir.name}: {prefix}/{target_path} 加载失败：{exc}")
    return entries


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
    pamt_path = game_dir / pamt_dir / OVERLAY_PAMT_NAME
    if not pamt_path.exists():
        return None
    try:
        entries = parse_pamt(pamt_path, paz_dir=pamt_path.parent)
    except Exception as exc:
        logger.warning("跳过无法解析的 loose 目标 PAMT：%s (%s)", pamt_path, exc)
        return None

    normalized = lower_game_rel_path(target_path)
    basename = normalized.rsplit("/", 1)[-1]
    basename_match: PazEntry | None = None
    for entry in entries:
        entry_key = lower_game_rel_path(entry.path)
        if entry_key == normalized:
            return entry
        if entry_key.rsplit("/", 1)[-1] == basename:
            basename_match = entry
    if basename_match is not None:
        logger.info("按 basename 匹配 loose %s/%s -> %s", pamt_dir, target_path, basename_match.path)
    return basename_match


def _find_entry_globally(game_dir: Path, target_path: str) -> PazEntry | None:
    """根路径 loose 没有 NNNN，优先完整路径，其次安全 basename 命中。"""
    normalized = lower_game_rel_path(target_path)
    basename = normalized.rsplit("/", 1)[-1]

    exact_matches: list[PazEntry] = []
    basename_matches: list[PazEntry] = []
    for directory in sorted((item for item in game_dir.iterdir() if _is_game_archive_dir(item)), key=_path_sort_key):
        pamt_path = directory / OVERLAY_PAMT_NAME
        if not pamt_path.exists():
            continue
        try:
            entries = parse_pamt(pamt_path, paz_dir=pamt_path.parent)
        except Exception as exc:
            logger.warning("跳过无法解析的 root loose 目标 PAMT：%s (%s)", pamt_path, exc)
            continue
        for entry in entries:
            entry_key = lower_game_rel_path(entry.path)
            if entry_key == normalized:
                exact_matches.append(entry)
            elif entry_key.rsplit("/", 1)[-1] == basename:
                basename_matches.append(entry)

    exact = _pick_best_global_match(exact_matches, normalized, basename)
    if exact is not None:
        return exact
    entry = _pick_best_global_match(basename_matches, normalized, basename)
    if entry is not None:
        logger.info("按唯一 basename 匹配 root loose %s -> %s", target_path, entry.path)
        return entry
    if basename_matches:
        logger.warning("root loose %s basename 命中多个 PAMT entry，已跳过", target_path)
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
