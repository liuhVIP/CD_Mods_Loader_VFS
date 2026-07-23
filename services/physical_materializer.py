"""把 VFS 的统一 npp 分包产物安全物化到游戏实体目录。"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from cdmm.common.constants import (
    META_DIR_NAME,
    OVERLAY_PAMT_NAME,
    OVERLAY_PAZ_NAME,
    PAPGT_FILE_NAME,
    PATHC_FILE_NAME,
    STAGING_DIR_NAME,
    WORK_DIR_NAME,
)
from cdmm.common.models import DiscoveredMod
from cdmm.io.transaction import Transaction
from cdmm.services.vfs_loader import VfsBuildResult, build_vfs_package
from cdmm.storage.state_store import load_state, save_state
from cdmm.storage.vanilla_store import VanillaStore
from cdmm.utils.hash_utils import fingerprint_mods

logger = logging.getLogger(__name__)

# Physical 固定维护的两个实体 meta 文件。
PHYSICAL_META_FILES = (
    f"{META_DIR_NAME}/{PAPGT_FILE_NAME}",
    f"{META_DIR_NAME}/{PATHC_FILE_NAME}",
)


@dataclass(frozen=True)
class PhysicalMaterializationResult:
    """实体分包写入结果，字段与旧 LoaderResult 保持入口兼容。"""

    overlay_dir: str | None
    loaded_mods: list[DiscoveredMod]
    warnings: list[str]
    errors: list[str]
    output_files: list[str]
    output_dirs: list[str]


def apply_physical_packages(
    game_dir: Path,
    progress_callback: Callable[[str], None] | None = None,
) -> PhysicalMaterializationResult:
    """复用 VFS 分包构建结果，并以事务方式写入真实游戏文件。"""
    previous_state = load_state(game_dir)
    build_result = build_vfs_package(
        game_dir,
        progress_callback=progress_callback,
        refresh_vanilla_on_game_change=False,
    )
    if build_result.errors:
        return PhysicalMaterializationResult(
            overlay_dir=build_result.overlay_dir,
            loaded_mods=build_result.loaded_mods,
            warnings=build_result.warnings,
            errors=build_result.errors,
            output_files=[],
            output_dirs=[],
        )

    vanilla_store = VanillaStore(game_dir)
    vanilla_store.ensure_meta_backup()
    mapped_files = {_normalize_relative_path(item) for item in build_result.mapped_files}
    meta_files = {f"{META_DIR_NAME}/{PAPGT_FILE_NAME}"}
    pathc_rel = f"{META_DIR_NAME}/{PATHC_FILE_NAME}"
    if pathc_rel in mapped_files or vanilla_store.has_file(pathc_rel):
        meta_files.add(pathc_rel)
    output_files = sorted(
        {item for item in mapped_files if _is_archive_file(item) or item in PHYSICAL_META_FILES}
        | meta_files
    )
    output_dirs = sorted({_archive_directory(item) for item in output_files if _is_archive_file(item)})

    staging_dir = game_dir / WORK_DIR_NAME / STAGING_DIR_NAME
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    transaction = Transaction(game_dir, staging_dir)
    for rel_path in output_files:
        _notify_progress(
            progress_callback,
            f"准备实体文件：{rel_path}（{_source_size_text(build_result, vanilla_store, rel_path, mapped_files)}）",
        )
        source_path = build_result.vfs_root / rel_path.replace("/", "\\")
        if rel_path in mapped_files:
            transaction.stage_file_from_path(rel_path, source_path)
        elif vanilla_store.has_file(rel_path):
            transaction.stage_file(rel_path, vanilla_store.read_file(rel_path))
        else:
            raise FileNotFoundError(2, "缺少 Physical 必需的 meta 备份", rel_path)
    _notify_progress(progress_callback, "提交 Physical 实体文件到游戏目录")
    transaction.commit()
    transaction.cleanup_staging()

    _notify_progress(progress_callback, "清理不再使用的旧 Physical 输出")
    removed_dirs = _cleanup_stale_physical_dirs(game_dir, previous_state, set(output_dirs))
    warnings = list(build_result.warnings)
    if removed_dirs:
        warnings.append(f"已清理旧 Physical 输出目录：{', '.join(removed_dirs)}")

    _notify_progress(progress_callback, "保存 Physical 缓存与回滚状态")
    save_state(
        game_dir,
        overlay_dir=None,
        last_fingerprint=fingerprint_mods(build_result.loaded_mods),
        loaded_mods=build_result.loaded_mods,
        standalone_dirs=build_result.standalone_dirs,
        physical_output_files=output_files,
        physical_output_dirs=output_dirs,
    )
    summary = ", ".join(output_dirs) if output_dirs else None
    logger.info("Physical 已物化统一分包：%s", summary or "仅恢复 vanilla meta")
    return PhysicalMaterializationResult(
        overlay_dir=summary,
        loaded_mods=build_result.loaded_mods,
        warnings=warnings,
        errors=[],
        output_files=output_files,
        output_dirs=output_dirs,
    )


def _notify_progress(
    progress_callback: Callable[[str], None] | None,
    message: str,
) -> None:
    """向实体启动器报告当前阶段，慢速磁盘期间由后台线程持续报活。"""
    if progress_callback is not None:
        progress_callback(message)


def _source_size_text(
    build_result: VfsBuildResult,
    vanilla_store: VanillaStore,
    rel_path: str,
    mapped_files: set[str],
) -> str:
    """返回即将复制文件的可读大小，不读取大型文件内容。"""
    if rel_path in mapped_files:
        source_path = build_result.vfs_root / rel_path.replace("/", "\\")
    else:
        source_path = vanilla_store.root / rel_path.replace("/", "\\")
    try:
        size = source_path.stat().st_size
    except OSError:
        return "大小未知"
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.2f} MB"
    if size >= 1024:
        return f"{size / 1024:.2f} KB"
    return f"{size} B"


def _normalize_relative_path(value: str) -> str:
    """校验 VFS 映射路径只能落在游戏根目录内部。"""
    normalized = value.replace("\\", "/").strip("/")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"VFS 映射包含非法相对路径：{value}")
    return path.as_posix()


def _is_archive_file(rel_path: str) -> bool:
    """判断映射是否为一个 PAZ/PAMT 分包文件。"""
    path = PurePosixPath(rel_path)
    return len(path.parts) == 2 and path.name in {OVERLAY_PAZ_NAME, OVERLAY_PAMT_NAME}


def _archive_directory(rel_path: str) -> str:
    """提取已校验归档文件的顶层目录名。"""
    return PurePosixPath(rel_path).parts[0]


def _cleanup_stale_physical_dirs(
    game_dir: Path,
    previous_state: dict,
    current_dirs: set[str],
) -> list[str]:
    """清理旧 npp 分包及旧版数字 overlay，保留当前仍使用的 standalone。"""
    candidates: set[str] = set()
    raw_dirs = previous_state.get("physical_output_dirs")
    if isinstance(raw_dirs, list):
        candidates.update(item for item in raw_dirs if isinstance(item, str))
    legacy_overlay = previous_state.get("overlay_dir")
    if isinstance(legacy_overlay, str):
        candidates.add(legacy_overlay)
    raw_standalone = previous_state.get("standalone_dirs")
    if isinstance(raw_standalone, list):
        for item in raw_standalone:
            if isinstance(item, dict) and isinstance(item.get("assigned_dir"), str):
                candidates.add(item["assigned_dir"])

    removed: list[str] = []
    for directory in sorted(candidates - current_dirs):
        target = game_dir / directory
        if _looks_like_physical_archive_dir(target):
            shutil.rmtree(target)
            removed.append(directory)
    return removed


def _looks_like_physical_archive_dir(path: Path) -> bool:
    """只删除状态明确记录且仅含 PAZ/PAMT 的加载器输出目录。"""
    if not path.is_dir():
        return False
    names = {item.name for item in path.iterdir()}
    return bool(names) and names.issubset({OVERLAY_PAZ_NAME, OVERLAY_PAMT_NAME})
