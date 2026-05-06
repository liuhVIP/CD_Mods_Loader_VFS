"""独立加载器业务编排入口。"""

from __future__ import annotations

import logging
import shutil
from time import perf_counter
from pathlib import Path

from cdmm.common.constants import (
    LOGS_DIR_NAME,
    META_DIR_NAME,
    MODS_DIR_NAME,
    OVERLAY_PAMT_NAME,
    OVERLAY_PAZ_NAME,
    PAPGT_FILE_NAME,
    PATHC_FILE_NAME,
    STAGING_DIR_NAME,
    VANILLA_DIR_NAME,
    WORK_DIR_NAME,
)
from cdmm.common.models import LoaderResult
from cdmm.io.transaction import Transaction, recover_interrupted
from cdmm.services.format3_loader import build_format3_overlay_entries, collect_format3_warnings
from cdmm.services.json_loader import build_json_overlay_entries
from cdmm.services.loose_file_service import build_loose_overlay_entries
from cdmm.services.overlay_service import (
    allocate_overlay_dir,
    build_overlay,
    overlay_rel_paths,
)
from cdmm.services.papgt_service import build_papgt
from cdmm.services.pathc_service import build_pathc_for_overlay
from cdmm.services.scanner import MOD_TYPE_FORMAT3, MOD_TYPE_JSON_PATCH, scan_mods
from cdmm.services.standalone_archive_service import (
    collect_standalone_archives,
    standalone_state_items,
)
from cdmm.storage.state_store import clear_state, load_state, save_state
from cdmm.storage.vanilla_store import VanillaStore
from cdmm.utils.hash_utils import fingerprint_mods

logger = logging.getLogger(__name__)


def ensure_work_dirs(game_dir: Path) -> None:
    """创建独立加载器工作目录。"""
    for name in (VANILLA_DIR_NAME, STAGING_DIR_NAME, LOGS_DIR_NAME):
        (game_dir / WORK_DIR_NAME / name).mkdir(parents=True, exist_ok=True)
    (game_dir / MODS_DIR_NAME).mkdir(parents=True, exist_ok=True)


def scan_loader(game_dir: Path) -> LoaderResult:
    """只扫描 mods 目录，不写入游戏文件。"""
    validate_game_dir(game_dir)
    started = perf_counter()
    mods, warnings = scan_mods(game_dir)
    _log_phase("扫描 mods", started)
    format3_mods = [mod for mod in mods if mod.mod_type == MOD_TYPE_FORMAT3]
    warnings.extend(collect_format3_warnings(format3_mods))
    return LoaderResult(overlay_dir=None, loaded_mods=mods, warnings=warnings, errors=[])


def apply_loader(game_dir: Path) -> LoaderResult:
    """全量重建 overlay 并注册到 PAPGT。"""
    total_started = perf_counter()
    validate_game_dir(game_dir)
    ensure_work_dirs(game_dir)
    recovered = recover_interrupted(game_dir)
    warnings: list[str] = []
    errors: list[str] = []
    if recovered:
        warnings.append(f"检测到上次中断提交，已恢复 {recovered} 个文件")

    phase_started = perf_counter()
    mods, scan_warnings = scan_mods(game_dir)
    _log_phase("扫描 mods", phase_started)
    warnings.extend(scan_warnings)
    json_mods = [mod for mod in mods if mod.mod_type == MOD_TYPE_JSON_PATCH]
    format3_mods = [mod for mod in mods if mod.mod_type == MOD_TYPE_FORMAT3]
    warnings.extend(collect_format3_warnings(format3_mods))

    phase_started = perf_counter()
    vanilla_store = VanillaStore(game_dir)
    vanilla_store.ensure_meta_backup()
    _log_phase("准备 vanilla 备份", phase_started)

    phase_started = perf_counter()
    loose_overlay_inputs = build_loose_overlay_entries(
        game_dir,
        vanilla_store,
        warnings,
        errors,
    )
    _log_phase("构建 loose overlay 输入", phase_started)
    phase_started = perf_counter()
    json_overlay_inputs = build_json_overlay_entries(
        game_dir,
        json_mods,
        vanilla_store,
        warnings,
        errors,
        loose_overlay_inputs,
    )
    _log_phase("构建 JSON overlay 输入", phase_started)
    phase_started = perf_counter()
    format3_overlay_inputs = build_format3_overlay_entries(
        game_dir,
        format3_mods,
        vanilla_store,
        warnings,
        errors,
        [*loose_overlay_inputs, *json_overlay_inputs],
    )
    _log_phase("构建 Format 3 overlay 输入", phase_started)
    # loose 先写、JSON 再写、Format 3 最后写；同 entry_path 时 build_overlay 会保留最后写入结果。
    overlay_inputs = [*loose_overlay_inputs, *json_overlay_inputs, *format3_overlay_inputs]
    if errors:
        return LoaderResult(overlay_dir=None, loaded_mods=mods, warnings=warnings, errors=errors)
    phase_started = perf_counter()
    standalone_archives = collect_standalone_archives(game_dir)
    _log_phase("收集 standalone 归档", phase_started)
    if not overlay_inputs and not standalone_archives:
        save_state(
            game_dir,
            overlay_dir=None,
            last_fingerprint=fingerprint_mods(mods),
            loaded_mods=mods,
            standalone_dirs=[],
        )
        warnings.append("没有生成 overlay entry，PAPGT 未改写")
        return LoaderResult(overlay_dir=None, loaded_mods=mods, warnings=warnings, errors=[])

    standalone_pamts = {archive.assigned_dir: archive.pamt_bytes for archive in standalone_archives}
    overlay_dir: str | None = None
    overlay = None
    pathc_bytes: bytes | None = None
    if overlay_inputs:
        reserved_dirs = {archive.assigned_dir for archive in standalone_archives}
        overlay_dir = allocate_overlay_dir(game_dir)
        while overlay_dir in reserved_dirs:
            overlay_dir = f"{int(overlay_dir) + 1:04d}"
        phase_started = perf_counter()
        overlay = build_overlay(overlay_dir, overlay_inputs, game_dir)
        _log_phase("构建 overlay PAZ/PAMT", phase_started)
        phase_started = perf_counter()
        pathc_bytes = build_pathc_for_overlay(
            game_dir,
            vanilla_store,
            overlay_inputs,
            overlay.entries,
            warnings,
        )
        _log_phase("构建 PATHC", phase_started)
    modified_pamts = dict(standalone_pamts)
    if overlay is not None and overlay_dir is not None:
        modified_pamts[overlay_dir] = overlay.pamt_bytes
    phase_started = perf_counter()
    papgt_bytes = build_papgt(game_dir, vanilla_store, modified_pamts)
    _log_phase("构建 PAPGT", phase_started)

    staging = game_dir / WORK_DIR_NAME / STAGING_DIR_NAME
    if staging.exists():
        shutil.rmtree(staging)
    transaction = Transaction(game_dir, staging)
    phase_started = perf_counter()
    for archive in standalone_archives:
        transaction.stage_file(f"{archive.assigned_dir}/{OVERLAY_PAZ_NAME}", archive.paz_bytes)
        transaction.stage_file(f"{archive.assigned_dir}/{OVERLAY_PAMT_NAME}", archive.pamt_bytes)
    if overlay is not None and overlay_dir is not None:
        paz_rel, pamt_rel = overlay_rel_paths(overlay_dir)
        transaction.stage_file(paz_rel, overlay.paz_bytes)
        transaction.stage_file(pamt_rel, overlay.pamt_bytes)
    transaction.stage_file(f"{META_DIR_NAME}/{PAPGT_FILE_NAME}", papgt_bytes)
    if pathc_bytes is not None:
        transaction.stage_file(f"{META_DIR_NAME}/0.pathc", pathc_bytes)
    transaction.commit()
    transaction.cleanup_staging()
    _log_phase("事务写入游戏目录", phase_started)

    save_state(
        game_dir,
        overlay_dir=overlay_dir,
        last_fingerprint=fingerprint_mods(mods),
        loaded_mods=mods,
        standalone_dirs=standalone_state_items(standalone_archives),
    )
    _log_phase("apply 总耗时", total_started)
    return LoaderResult(overlay_dir=overlay_dir, loaded_mods=mods, warnings=warnings, errors=[])


def revert_loader(game_dir: Path) -> LoaderResult:
    """恢复加载器上次创建的 overlay 与 PAPGT。"""
    validate_game_dir(game_dir)
    ensure_work_dirs(game_dir)
    warnings: list[str] = []
    errors: list[str] = []
    recovered = recover_interrupted(game_dir)
    if recovered:
        warnings.append(f"检测到上次中断提交，已恢复 {recovered} 个文件")

    state = load_state(game_dir)
    overlay_dir = state.get("overlay_dir")
    vanilla_store = VanillaStore(game_dir)
    papgt_rel = f"{META_DIR_NAME}/{PAPGT_FILE_NAME}"
    if not vanilla_store.has_file(papgt_rel):
        errors.append("找不到 vanilla PAPGT 备份，无法安全恢复")
        return LoaderResult(overlay_dir=None, loaded_mods=[], warnings=warnings, errors=errors)

    staging = game_dir / WORK_DIR_NAME / STAGING_DIR_NAME
    if staging.exists():
        shutil.rmtree(staging)
    transaction = Transaction(game_dir, staging)
    transaction.stage_file(papgt_rel, vanilla_store.read_file(papgt_rel))
    pathc_rel = f"{META_DIR_NAME}/{PATHC_FILE_NAME}"
    if vanilla_store.has_file(pathc_rel):
        transaction.stage_file(pathc_rel, vanilla_store.read_file(pathc_rel))
    transaction.commit()
    transaction.cleanup_staging()

    if isinstance(overlay_dir, str):
        target = game_dir / overlay_dir
        if target.exists():
            from cdmm.services.overlay_service import remove_previous_overlay

            remove_previous_overlay(game_dir)
    for item in state.get("standalone_dirs", []):
        if not isinstance(item, dict):
            continue
        assigned_dir = item.get("assigned_dir")
        if not isinstance(assigned_dir, str):
            continue
        target = game_dir / assigned_dir
        if _looks_like_staged_archive_dir(target):
            shutil.rmtree(target)
    clear_state(game_dir)
    return LoaderResult(overlay_dir=None, loaded_mods=[], warnings=warnings, errors=[])


def validate_game_dir(game_dir: Path) -> None:
    """校验游戏目录具备 meta/0.papgt。"""
    if not game_dir.exists() or not game_dir.is_dir():
        raise FileNotFoundError(2, "游戏目录不存在", str(game_dir))
    papgt = game_dir / META_DIR_NAME / PAPGT_FILE_NAME
    if not papgt.exists():
        raise FileNotFoundError(2, "未找到 meta/0.papgt，请确认 --game-dir 指向游戏根目录", str(papgt))


def _looks_like_staged_archive_dir(path: Path) -> bool:
    """只允许删除加载器记录且仅包含 0.paz/0.pamt 的 standalone 目录。"""
    if not path.is_dir():
        return False
    names = {item.name for item in path.iterdir()}
    return names.issubset({OVERLAY_PAZ_NAME, OVERLAY_PAMT_NAME})


def _log_phase(name: str, started: float) -> None:
    """输出阶段耗时，方便定位真实加载慢点。"""
    logger.info("耗时：%s %.2fs", name, perf_counter() - started)
