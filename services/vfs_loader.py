"""VFS 实验加载服务。

本文件负责把现有模组合成结果写入独立 VFS 目录，并生成 vfsDmoe 可读取的
mapping_tree.json。它只写入 .cdloader 工作区，不直接修改游戏源文件。
"""

from __future__ import annotations

import json
import logging
import shutil
from hashlib import sha256
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from time import perf_counter, sleep

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
from cdmm.common.models import BuiltOverlayEntry, DiscoveredMod, OverlayBuildResult, OverlayInputEntry
from cdmm.services.cdmod_semantic_loader import (
    build_cdmod_file_base_entries,
    build_semantic_overlay_entries,
    collect_semantic_pamt_targets,
    collect_semantic_warnings,
)
from cdmm.services.cdmod_localization_loader import detect_active_paloc_language
from cdmm.services.json_loader import build_json_overlay_entries, collect_json_pamt_targets
from cdmm.services.loader import validate_game_dir
from cdmm.services.loose_file_service import build_loose_overlay_entries, collect_loose_pamt_targets
from cdmm.services.overlay_service import build_overlay, overlay_rel_paths
from cdmm.services.pamt_index_service import register_game_pamt_targets, save_game_pamt_target_cache
from cdmm.services.papgt_service import build_papgt
from cdmm.services.pathc_service import build_pathc_for_overlay
from cdmm.services.scanner import (
    INVALID_CDMOD_WARNING_PREFIX,
    MOD_TYPE_CDMOD,
    MOD_TYPE_FORMAT3,
    MOD_TYPE_JSON_PATCH,
    scan_mods,
)
from cdmm.services.standalone_archive_service import (
    STANDALONE_CONFLICT_WARNING_PREFIX,
    collect_standalone_archives,
)
from cdmm.storage.state_store import load_state
from cdmm.storage.vanilla_store import VanillaStore
from cdmm.utils.hash_utils import fingerprint_mods

logger = logging.getLogger(__name__)

# VFS 输出目录名，所有虚拟产物都集中到这里，便于一键清理和重复试跑。
VFS_ACTIVE_DIR_NAME = "vfs_active"

# vfsDmoe 精确映射清单文件名。
VFS_MAPPING_FILE_NAME = "vfs_mapping_tree.json"

# VFS 构建摘要文件名，用于排障时确认本次映射了哪些文件。
VFS_STATE_FILE_NAME = "vfs_state.json"

# VFS 分包缓存目录。模组集合变化时，未变化的分包可直接复用，避免重新
# 打包大 PAZ 并计算纯 Python hashlittle。
VFS_PACKAGE_CACHE_DIR_NAME = "vfs_package_cache"

# VFS 状态结构版本。分包策略变化时必须提升，避免复用旧 mapping。
VFS_STATE_SCHEMA = 5

# 分包构建算法版本，必须参与缓存key。当前版本要求按最终PAMT路径去重，
# 防止复用旧版可能含重复entry的PAZ/PAMT缓存。
VFS_PACKAGE_BUILD_SCHEMA = 2

# 冷构建返回后只读取文件元数据确认稳定，不重复读取或哈希大型 PAZ。
VFS_STABILITY_CHECK_INTERVAL_SECONDS = 0.1
VFS_REQUIRED_STABLE_CHECKS = 2
VFS_STABILITY_MAX_CHECKS = 50

# DMM 已验证的高风险表分包名。这里用 ASCII 常量，避免后续脚本编码踩坑。
NPP_V3_STATUSINFO_PACKAGE = "nppv3_statusinfo"
NPP_V3_EQUIPSLOTINFO_PACKAGE = "nppv3_equipslotinfo"
NPP_V3_STRINGINFO_PACKAGE = "nppv3_stringinfo"
NPP_V3_ITEMINFO_PACKAGE = "nppv3_iteminfo"
NPP_VOICE_PACKAGE = "nppvoice"
NPP_JSON_PACKAGE = "nppgen"
NPP_LOOSE_PACKAGE = "nppsa"

# DMM-like VFS 的 PAPGT 前置顺序，越靠前优先级越高。
NPP_LIKE_PACKAGE_ORDER = [
    NPP_V3_STATUSINFO_PACKAGE,
    NPP_V3_STRINGINFO_PACKAGE,
    NPP_V3_EQUIPSLOTINFO_PACKAGE,
    NPP_V3_ITEMINFO_PACKAGE,
    NPP_VOICE_PACKAGE,
    NPP_JSON_PACKAGE,
    NPP_LOOSE_PACKAGE,
]

# Format 3 已实现 writer 的高风险表按 DMM 现场拆成独立包。
NPP_V3_PACKAGE_BY_TABLE = {
    "statusinfo": NPP_V3_STATUSINFO_PACKAGE,
    "equipslotinfo": NPP_V3_EQUIPSLOTINFO_PACKAGE,
    "stringinfo": NPP_V3_STRINGINFO_PACKAGE,
    "iteminfo": NPP_V3_ITEMINFO_PACKAGE,
}


@dataclass(frozen=True)
class VfsOverlayPackage:
    """一个 DMM-like VFS 输出包。"""

    name: str
    entries: list[OverlayInputEntry]


@dataclass(frozen=True)
class _OverlayPackageMaterial:
    """分包构建结果及其可复用缓存位置。"""

    overlay: OverlayBuildResult
    cache_dir: Path
    cache_hit: bool


@dataclass(frozen=True)
class VfsBuildResult:
    """VFS 构建结果摘要。"""

    game_dir: Path
    vfs_root: Path
    mapping_path: Path
    overlay_dir: str | None
    loaded_mods: list[DiscoveredMod]
    warnings: list[str]
    errors: list[str]
    mapped_files: list[str]
    cache_hit: bool = False


def build_vfs_package_for_launch(
    game_dir: Path,
    allow_missing_targets: bool = False,
    progress_callback: Callable[[str], None] | None = None,
) -> VfsBuildResult:
    """两阶段准备 VFS：完成构建后确认文件稳定，并复核整包缓存完整性。"""
    initial_result = build_vfs_package(
        game_dir,
        allow_missing_targets=allow_missing_targets,
        progress_callback=progress_callback,
    )
    if initial_result.errors or initial_result.cache_hit:
        return initial_result

    _notify_progress(progress_callback, "冷构建完成，等待 VFS 文件稳定")
    try:
        _wait_for_vfs_outputs_stable(initial_result)
    except (OSError, ValueError) as exc:
        return replace(
            initial_result,
            errors=[*initial_result.errors, f"VFS 冷构建稳定性检查失败：{exc}"],
        )

    _notify_progress(progress_callback, "第二阶段：复核 VFS 缓存完整性")
    verified_result = build_vfs_package(
        game_dir,
        allow_missing_targets=allow_missing_targets,
        progress_callback=None,
    )
    if verified_result.errors:
        return replace(
            initial_result,
            errors=[*initial_result.errors, *verified_result.errors],
        )
    if not verified_result.cache_hit:
        return replace(
            initial_result,
            errors=[*initial_result.errors, "VFS 冷构建复核未命中缓存，已阻止启动游戏"],
        )

    _notify_progress(progress_callback, "第二阶段完成，VFS 文件已稳定")
    return replace(
        verified_result,
        warnings=initial_result.warnings,
        cache_hit=False,
    )


def build_vfs_package(
    game_dir: Path,
    allow_missing_targets: bool = False,
    progress_callback: Callable[[str], None] | None = None,
) -> VfsBuildResult:
    """构建 VFS 运行目录和 mapping_tree.json，不修改游戏源文件。"""
    total_started = perf_counter()
    stage_started = perf_counter()
    _notify_progress(progress_callback, "检查游戏目录")
    validate_game_dir(game_dir)
    _ensure_vfs_work_dirs(game_dir)
    _log_vfs_stage("检查游戏目录", stage_started)

    warnings: list[str] = []
    errors: list[str] = []
    stage_started = perf_counter()
    _notify_progress(progress_callback, "扫描 mods 并同步加载顺序")
    mods, scan_warnings = scan_mods(game_dir)
    warnings.extend(scan_warnings)
    invalid_cdmods = [warning for warning in scan_warnings if warning.startswith(INVALID_CDMOD_WARNING_PREFIX)]
    if invalid_cdmods:
        errors.extend(invalid_cdmods)
        _log_vfs_stage("VFS 构建总耗时", total_started)
        return _empty_result(game_dir, mods, warnings, errors)
    current_fingerprint = fingerprint_mods(mods)
    active_language = detect_active_paloc_language(game_dir)
    _log_vfs_stage("扫描 mods / 指纹", stage_started)

    stage_started = perf_counter()
    cached_result = _try_reuse_vfs_package(
        game_dir,
        mods,
        warnings,
        current_fingerprint,
        allow_missing_targets,
        active_language,
    )
    if cached_result is not None:
        _log_vfs_stage("整包缓存检查", stage_started)
        _log_vfs_stage("VFS 构建总耗时", total_started)
        _notify_progress(progress_callback, "VFS 缓存命中，复用已构建包")
        return cached_result
    _log_vfs_stage("整包缓存检查", stage_started)

    stage_started = perf_counter()
    _notify_progress(progress_callback, "统计 JSON / Format 3 / loose 目标")
    json_mods = [mod for mod in mods if mod.mod_type in {MOD_TYPE_JSON_PATCH, MOD_TYPE_CDMOD}]
    semantic_mods = [mod for mod in mods if mod.mod_type in {MOD_TYPE_FORMAT3, MOD_TYPE_CDMOD}]
    warnings.extend(collect_semantic_warnings(semantic_mods))

    pamt_targets = [
        *collect_loose_pamt_targets(game_dir, mods, include_numbered=False),
        *collect_json_pamt_targets(json_mods),
        *collect_semantic_pamt_targets(semantic_mods),
    ]
    register_game_pamt_targets(game_dir, pamt_targets)
    _log_vfs_stage("统计并注册 PAMT 目标", stage_started)

    stage_started = perf_counter()
    _notify_progress(progress_callback, "准备原版 meta 与 PAMT 索引")
    vanilla_store = VanillaStore(game_dir)
    vanilla_store.ensure_meta_backup()
    _log_vfs_stage("准备原版 meta / PAMT", stage_started)

    stage_started = perf_counter()
    _notify_progress(progress_callback, "构建 loose 文件覆盖输入")
    loose_overlay_inputs = build_loose_overlay_entries(
        game_dir,
        vanilla_store,
        warnings,
        errors,
        mods,
    )
    cdmod_file_base_inputs = build_cdmod_file_base_entries(
        game_dir,
        semantic_mods,
        vanilla_store,
        warnings,
        errors,
        loose_overlay_inputs,
    )
    loose_overlay_inputs = [*loose_overlay_inputs, *cdmod_file_base_inputs]
    _log_vfs_stage("构建 loose 覆盖输入", stage_started)

    stage_started = perf_counter()
    _notify_progress(progress_callback, "构建 JSON 补丁覆盖输入")
    json_overlay_inputs = build_json_overlay_entries(
        game_dir,
        json_mods,
        vanilla_store,
        warnings,
        errors,
        loose_overlay_inputs,
    )
    _log_vfs_stage("构建 JSON 覆盖输入", stage_started)

    stage_started = perf_counter()
    _notify_progress(progress_callback, "构建 Format 3 语义覆盖输入")
    format3_overlay_inputs = build_semantic_overlay_entries(
        game_dir,
        semantic_mods,
        vanilla_store,
        warnings,
        errors,
        [*loose_overlay_inputs, *json_overlay_inputs],
    )
    _log_vfs_stage("构建 Format 3 覆盖输入", stage_started)

    stage_started = perf_counter()
    overlay_packages = _build_dmm_like_overlay_packages(
        loose_overlay_inputs,
        json_overlay_inputs,
        format3_overlay_inputs,
    )
    _log_vfs_stage("拆分 DMM-like VFS 包", stage_started)
    if allow_missing_targets:
        errors = _downgrade_missing_target_errors(errors, warnings)
    if errors:
        _log_vfs_stage("VFS 构建总耗时", total_started)
        return _empty_result(game_dir, mods, warnings, errors)

    stage_started = perf_counter()
    _notify_progress(progress_callback, "收集 standalone PAZ/PAMT")
    previous_state = load_state(game_dir)
    previous_standalone_items = previous_state.get("standalone_dirs")
    if not isinstance(previous_standalone_items, list):
        previous_standalone_items = []
    standalone_archives = collect_standalone_archives(
        game_dir,
        previous_items=previous_standalone_items,
        ordered_mods=mods,
        warnings=warnings,
    )
    _log_vfs_stage("收集 standalone 包", stage_started)

    if not overlay_packages and not standalone_archives:
        warnings.append("没有生成 VFS overlay entry，mapping_tree.json 未写入")
        _log_vfs_stage("VFS 构建总耗时", total_started)
        return _empty_result(game_dir, mods, warnings, errors)

    stage_started = perf_counter()
    vfs_root = _reset_vfs_active_dir(game_dir)
    mapped_files: list[str] = []
    modified_pamts: dict[str, bytes] = {}
    package_built_entries: list[BuiltOverlayEntry] = []
    package_input_entries: list[OverlayInputEntry] = []
    package_names: list[str] = []
    pathc_bytes: bytes | None = None
    _log_vfs_stage("重置 VFS 输出目录", stage_started)

    for package in overlay_packages:
        stage_started = perf_counter()
        _notify_progress(progress_callback, f"写入 VFS overlay 包：{package.name}")
        material = _build_or_reuse_overlay_package(game_dir, package)
        overlay = material.overlay
        paz_rel, pamt_rel = overlay_rel_paths(package.name)
        paz_output = vfs_root / paz_rel.replace("/", "\\")
        pamt_output = vfs_root / pamt_rel.replace("/", "\\")
        if material.cache_hit:
            _materialize_cached_file(material.cache_dir / OVERLAY_PAZ_NAME, paz_output)
            _materialize_cached_file(material.cache_dir / OVERLAY_PAMT_NAME, pamt_output)
        else:
            _write_vfs_file(vfs_root, paz_rel, overlay.paz_bytes)
            _write_vfs_file(vfs_root, pamt_rel, overlay.pamt_bytes)
            _write_overlay_package_cache_from_outputs(
                material.cache_dir,
                overlay,
                paz_output,
                pamt_output,
            )
        mapped_files.extend([paz_rel, pamt_rel])
        modified_pamts[package.name] = overlay.pamt_bytes
        package_built_entries.extend(overlay.entries)
        package_input_entries.extend(package.entries)
        package_names.append(package.name)
        _log_vfs_stage(f"写入 VFS overlay 包 {package.name}", stage_started)

    standalone_pamts = {archive.assigned_dir: archive.pamt_bytes for archive in standalone_archives}
    modified_pamts.update(standalone_pamts)
    for archive in standalone_archives:
        stage_started = perf_counter()
        _notify_progress(progress_callback, f"写入 standalone 包：{archive.assigned_dir}")
        _write_vfs_file(vfs_root, f"{archive.assigned_dir}/{OVERLAY_PAZ_NAME}", archive.paz_bytes)
        _write_vfs_file(vfs_root, f"{archive.assigned_dir}/{OVERLAY_PAMT_NAME}", archive.pamt_bytes)
        mapped_files.extend(
            [
                f"{archive.assigned_dir}/{OVERLAY_PAZ_NAME}",
                f"{archive.assigned_dir}/{OVERLAY_PAMT_NAME}",
            ]
        )
        _log_vfs_stage(f"写入 standalone 包 {archive.assigned_dir}", stage_started)

    if package_input_entries:
        stage_started = perf_counter()
        _notify_progress(progress_callback, "构建 PATHC 纹理映射")
        pathc_bytes = build_pathc_for_overlay(
            game_dir,
            vanilla_store,
            package_input_entries,
            package_built_entries,
            warnings,
        )
        _log_vfs_stage("构建 PATHC 纹理映射", stage_started)

    stage_started = perf_counter()
    _notify_progress(progress_callback, "构建 PAPGT 并规范化 VFS flags")
    papgt_order = [*NPP_LIKE_PACKAGE_ORDER, *(archive.assigned_dir for archive in standalone_archives)]
    papgt_bytes = build_papgt(
        game_dir,
        vanilla_store,
        modified_pamts,
        prepend_order=papgt_order,
        normalize_existing_flags=True,
    )
    _log_vfs_stage("构建 PAPGT", stage_started)

    stage_started = perf_counter()
    _write_vfs_file(vfs_root, f"{META_DIR_NAME}/{PAPGT_FILE_NAME}", papgt_bytes)
    mapped_files.append(f"{META_DIR_NAME}/{PAPGT_FILE_NAME}")
    if pathc_bytes is not None:
        _write_vfs_file(vfs_root, f"{META_DIR_NAME}/{PATHC_FILE_NAME}", pathc_bytes)
        mapped_files.append(f"{META_DIR_NAME}/{PATHC_FILE_NAME}")
    _log_vfs_stage("写入 meta 文件", stage_started)

    mapping_path = game_dir / WORK_DIR_NAME / VFS_MAPPING_FILE_NAME
    stage_started = perf_counter()
    _notify_progress(progress_callback, "写入 VFS 映射清单")
    _write_mapping_manifest(game_dir, vfs_root, mapping_path, mapped_files)
    overlay_dir_summary = ", ".join(package_names) if package_names else None
    _write_vfs_state(
        game_dir,
        vfs_root,
        mapping_path,
        overlay_dir_summary,
        package_names,
        mods,
        warnings,
        mapped_files,
        allow_missing_targets,
        active_language,
    )
    save_game_pamt_target_cache(game_dir)
    _log_vfs_stage("写入 VFS 状态和目标缓存", stage_started)
    _notify_progress(progress_callback, "VFS 构建完成")
    logger.info("VFS package built: %s", mapping_path)
    _log_vfs_stage("VFS 构建总耗时", total_started)
    return VfsBuildResult(
        game_dir=game_dir,
        vfs_root=vfs_root,
        mapping_path=mapping_path,
        overlay_dir=overlay_dir_summary,
        loaded_mods=mods,
        warnings=warnings,
        errors=errors,
        mapped_files=mapped_files,
    )


def _notify_progress(progress_callback: Callable[[str], None] | None, message: str) -> None:
    """向控制台入口报告 VFS 构建阶段，旧调用方可不传回调。"""
    if progress_callback is not None:
        progress_callback(message)


def _log_vfs_stage(name: str, started: float) -> None:
    """记录 VFS 构建阶段耗时，便于区分 hash、JSON、Format 3 等瓶颈。"""
    logger.info("VFS 耗时：%s %.2fs", name, perf_counter() - started)


def _ensure_vfs_work_dirs(game_dir: Path) -> None:
    """创建 VFS 构建需要的工作目录。"""
    for name in (VANILLA_DIR_NAME, STAGING_DIR_NAME, LOGS_DIR_NAME):
        (game_dir / WORK_DIR_NAME / name).mkdir(parents=True, exist_ok=True)
    (game_dir / MODS_DIR_NAME).mkdir(parents=True, exist_ok=True)


def _empty_result(
    game_dir: Path,
    mods: list[DiscoveredMod],
    warnings: list[str],
    errors: list[str],
) -> VfsBuildResult:
    """返回未生成映射时的统一结果。"""
    return VfsBuildResult(
        game_dir=game_dir,
        vfs_root=game_dir / WORK_DIR_NAME / VFS_ACTIVE_DIR_NAME,
        mapping_path=game_dir / WORK_DIR_NAME / VFS_MAPPING_FILE_NAME,
        overlay_dir=None,
        loaded_mods=mods,
        warnings=warnings,
        errors=errors,
        mapped_files=[],
        cache_hit=False,
    )


def _try_reuse_vfs_package(
    game_dir: Path,
    mods: list[DiscoveredMod],
    warnings: list[str],
    current_fingerprint: str,
    allow_missing_targets: bool,
    active_language: str | None,
) -> VfsBuildResult | None:
    """模组未变化且 VFS 产物完整时，直接复用上次构建结果。"""
    state_path = game_dir / WORK_DIR_NAME / VFS_STATE_FILE_NAME
    if not state_path.exists():
        return None
    try:
        state = json.loads(state_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(state, dict) or state.get("schema") != VFS_STATE_SCHEMA:
        return None
    # 旧状态没有持久化 standalone 冲突；只强制重建一次，确保后续每次热启动均可提示。
    if state.get("standalone_conflicts_checked") is not True:
        return None
    if state.get("last_fingerprint") != current_fingerprint:
        return None
    if bool(state.get("allow_missing_targets")) != bool(allow_missing_targets):
        return None
    if state.get("active_language") != active_language:
        return None

    vfs_root = Path(str(state.get("vfs_root") or game_dir / WORK_DIR_NAME / VFS_ACTIVE_DIR_NAME))
    mapping_path = Path(str(state.get("mapping_path") or game_dir / WORK_DIR_NAME / VFS_MAPPING_FILE_NAME))
    mapped_files = state.get("mapped_files")
    if not vfs_root.is_dir() or not mapping_path.is_file() or not isinstance(mapped_files, list):
        return None
    normalized_files = [item for item in mapped_files if isinstance(item, str)]
    if len(normalized_files) != len(mapped_files) or not normalized_files:
        return None
    for relative_path in normalized_files:
        if not (vfs_root / relative_path.replace("/", "\\")).is_file():
            return None

    overlay_packages = state.get("overlay_packages")
    if not isinstance(overlay_packages, list):
        overlay_packages = []
    overlay_dir = state.get("overlay_dir")
    saved_warnings = state.get("warnings")
    if isinstance(saved_warnings, list):
        existing = set(warnings)
        for warning in saved_warnings:
            if (
                isinstance(warning, str)
                and warning.startswith(STANDALONE_CONFLICT_WARNING_PREFIX)
                and warning not in existing
            ):
                warnings.append(warning)
                existing.add(warning)
    logger.info("VFS cache hit: %s", mapping_path)
    return VfsBuildResult(
        game_dir=game_dir,
        vfs_root=vfs_root,
        mapping_path=mapping_path,
        overlay_dir=overlay_dir if isinstance(overlay_dir, str) else None,
        loaded_mods=mods,
        warnings=warnings,
        errors=[],
        mapped_files=normalized_files,
        cache_hit=True,
    )


def _wait_for_vfs_outputs_stable(result: VfsBuildResult) -> None:
    """等待所有映射产物连续两次保持大小和修改时间不变。"""
    previous = _vfs_output_snapshot(result)
    stable_checks = 0
    for _ in range(VFS_STABILITY_MAX_CHECKS):
        sleep(VFS_STABILITY_CHECK_INTERVAL_SECONDS)
        current = _vfs_output_snapshot(result)
        if current == previous:
            stable_checks += 1
            if stable_checks >= VFS_REQUIRED_STABLE_CHECKS:
                return
        else:
            stable_checks = 0
            previous = current
    raise ValueError("VFS 文件在稳定等待窗口内持续变化")


def _vfs_output_snapshot(result: VfsBuildResult) -> tuple[tuple[str, int, int], ...]:
    """生成轻量文件快照；只执行 stat，不读取大型构建内容。"""
    paths = [
        result.mapping_path,
        result.game_dir / WORK_DIR_NAME / VFS_STATE_FILE_NAME,
        *(result.vfs_root / relative_path.replace("/", "\\") for relative_path in result.mapped_files),
    ]
    snapshot: list[tuple[str, int, int]] = []
    for path in paths:
        stat = path.stat()
        snapshot.append((str(path), stat.st_size, stat.st_mtime_ns))
    return tuple(snapshot)


def _downgrade_missing_target_errors(errors: list[str], warnings: list[str]) -> list[str]:
    """VFS 试跑时允许跳过当前游戏版本不存在的 JSON 目标。"""
    remaining: list[str] = []
    for error in errors:
        if "未在任何 PAMT 中找到目标文件" not in error:
            remaining.append(error)
            continue
        warnings.append(f"VFS 实验模式已跳过缺失目标：{error}")
    return remaining


def _build_dmm_like_overlay_packages(
    loose_overlay_inputs: list[OverlayInputEntry],
    json_overlay_inputs: list[OverlayInputEntry],
    format3_overlay_inputs: list[OverlayInputEntry],
) -> list[VfsOverlayPackage]:
    """把合成结果拆成接近 DMM 的多个 VFS 包，降低大包与覆盖顺序风险。"""
    grouped: dict[str, list[OverlayInputEntry]] = {name: [] for name in NPP_LIKE_PACKAGE_ORDER}

    # JSON 输出已经基于 loose base 合成，不能再让同目标 loose base 进 nppsa 覆盖。
    composed_targets = {
        _entry_key(entry)
        for entry in [*json_overlay_inputs, *format3_overlay_inputs]
    }
    for entry in loose_overlay_inputs:
        if _entry_key(entry) in composed_targets:
            continue
        package_name = NPP_VOICE_PACKAGE if _is_voice_entry(entry) else NPP_LOOSE_PACKAGE
        grouped[package_name].append(entry)

    # Format 3 输出已经叠加了 loose/JSON base。若 nppgen 再保留同目标
    # JSON 旧版本，运行期可能把 pabgb/pabgh 拆成两个包交叉读取。
    format3_targets = {_entry_key(entry) for entry in format3_overlay_inputs}
    grouped[NPP_JSON_PACKAGE].extend(
        entry for entry in json_overlay_inputs if _entry_key(entry) not in format3_targets
    )
    for entry in format3_overlay_inputs:
        package_name = _format3_package_name(entry) or (
            NPP_JSON_PACKAGE if entry.entry_path.lower().endswith((".pabgb", ".pabgh")) else NPP_LOOSE_PACKAGE
        )
        grouped[package_name].append(entry)

    return [
        VfsOverlayPackage(name=name, entries=entries)
        for name in NPP_LIKE_PACKAGE_ORDER
        if (entries := grouped.get(name))
    ]


def _entry_key(entry: OverlayInputEntry) -> str:
    """返回 overlay entry 的稳定合成 key。"""
    return entry.entry_path.replace("\\", "/").lower()


def _is_voice_entry(entry: OverlayInputEntry) -> bool:
    """判断 loose 资源是否应拆入语音包。"""
    normalized = entry.entry_path.replace("\\", "/").lower()
    return normalized.endswith(".wem")


def _format3_package_name(entry: OverlayInputEntry) -> str | None:
    """按 Format 3 目标表名选择 DMM v3 分包。"""
    basename = Path(entry.entry_path.replace("\\", "/")).name.lower()
    stem = basename.rsplit(".", 1)[0]
    return NPP_V3_PACKAGE_BY_TABLE.get(stem)


def _build_or_reuse_overlay_package(game_dir: Path, package: VfsOverlayPackage) -> _OverlayPackageMaterial:
    """构建或复用单个 VFS overlay 分包。"""
    cache_key = _overlay_package_cache_key(package)
    cache_dir = _overlay_package_cache_dir(game_dir, package.name, cache_key)
    cached = _read_overlay_package_cache(package.name, cache_dir)
    if cached is not None:
        logger.info("VFS package cache hit: %s", package.name)
        return _OverlayPackageMaterial(cached, cache_dir, True)

    overlay = build_overlay(package.name, package.entries, game_dir)
    return _OverlayPackageMaterial(overlay, cache_dir, False)


def _overlay_package_cache_key(package: VfsOverlayPackage) -> str:
    """按分包输入内容生成稳定缓存 key。"""
    digest = sha256()
    digest.update(f"schema:{VFS_PACKAGE_BUILD_SCHEMA}".encode("ascii"))
    digest.update(b"\0")
    digest.update(package.name.encode("utf-8"))
    digest.update(b"\0")
    seen: dict[str, OverlayInputEntry] = {}
    for entry in package.entries:
        seen[entry.entry_path.lower()] = entry
    for key, entry in seen.items():
        digest.update(key.encode("utf-8"))
        digest.update(b"\0")
        digest.update(entry.entry_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(entry.pamt_dir.encode("ascii", errors="ignore"))
        digest.update(b"\0")
        digest.update(str(entry.compression_type).encode("ascii"))
        digest.update(b"\0")
        digest.update(b"1" if entry.encrypted else b"0")
        digest.update(b"\0")
        digest.update((entry.crypto_filename or "").encode("utf-8"))
        digest.update(b"\0")
        digest.update((entry.resolved_dir_path or "").encode("utf-8"))
        digest.update(b"\0")
        digest.update(len(entry.content).to_bytes(8, "little"))
        digest.update(entry.content)
        digest.update(b"\0")
    return digest.hexdigest()


def _overlay_package_cache_dir(game_dir: Path, package_name: str, cache_key: str) -> Path:
    """返回 VFS 分包缓存目录。"""
    safe_name = package_name.replace("/", "_").replace("\\", "_")
    return game_dir / WORK_DIR_NAME / VFS_PACKAGE_CACHE_DIR_NAME / f"{safe_name}-{cache_key[:24]}"


def _read_overlay_package_cache(package_name: str, cache_dir: Path) -> OverlayBuildResult | None:
    """读取分包缓存，结构异常时返回 None 并走正常构建。"""
    paz_path = cache_dir / OVERLAY_PAZ_NAME
    pamt_path = cache_dir / OVERLAY_PAMT_NAME
    meta_path = cache_dir / "entries.json"
    if not paz_path.is_file() or not pamt_path.is_file() or not meta_path.is_file():
        return None
    try:
        raw_meta = json.loads(meta_path.read_text(encoding="utf-8"))
        entries = [_built_entry_from_json(item) for item in raw_meta.get("entries", [])]
        if any(entry is None for entry in entries):
            return None
        return OverlayBuildResult(
            overlay_dir=package_name,
            # 命中时由同盘硬链接直接物化到 vfs_active，无需把大型 PAZ 读入内存。
            paz_bytes=b"",
            pamt_bytes=pamt_path.read_bytes(),
            entries=[entry for entry in entries if entry is not None],
        )
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _write_overlay_package_cache(cache_dir: Path, overlay: OverlayBuildResult) -> None:
    """写入分包缓存，失败不影响本次 VFS 构建。"""
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / OVERLAY_PAZ_NAME).write_bytes(overlay.paz_bytes)
        (cache_dir / OVERLAY_PAMT_NAME).write_bytes(overlay.pamt_bytes)
        payload = {
            "schema": 1,
            "overlay_dir": overlay.overlay_dir,
            "entries": [_built_entry_to_json(entry) for entry in overlay.entries],
        }
        (cache_dir / "entries.json").write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("VFS package cache write failed: %s", exc)


def _write_overlay_package_cache_from_outputs(
    cache_dir: Path,
    overlay: OverlayBuildResult,
    paz_output: Path,
    pamt_output: Path,
) -> None:
    """由已落盘 VFS 输出建立分包缓存，避免首次构建重复写大型 PAZ。"""
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        _replace_with_hardlink_or_copy(paz_output, cache_dir / OVERLAY_PAZ_NAME)
        _replace_with_hardlink_or_copy(pamt_output, cache_dir / OVERLAY_PAMT_NAME)
        payload = {
            "schema": 1,
            "overlay_dir": overlay.overlay_dir,
            "entries": [_built_entry_to_json(entry) for entry in overlay.entries],
        }
        (cache_dir / "entries.json").write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("VFS package cache link failed: %s", exc)


def _materialize_cached_file(source: Path, target: Path) -> None:
    """把同盘缓存零拷贝物化到 VFS 目录，失败时回退普通复制。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    _replace_with_hardlink_or_copy(source, target)


def _replace_with_hardlink_or_copy(source: Path, target: Path) -> None:
    """优先创建NTFS硬链接，并兼容不支持硬链接的文件系统。"""
    if target.exists():
        target.unlink()
    try:
        target.hardlink_to(source)
    except OSError:
        shutil.copy2(source, target)


def _built_entry_to_json(entry: BuiltOverlayEntry) -> dict[str, object]:
    """序列化 BuiltOverlayEntry 缓存元数据。"""
    return {
        "entry_path": entry.entry_path,
        "dir_path": entry.dir_path,
        "filename": entry.filename,
        "paz_offset": entry.paz_offset,
        "comp_size": entry.comp_size,
        "decomp_size": entry.decomp_size,
        "flags": entry.flags,
        "dds_m_values": list(entry.dds_m_values) if entry.dds_m_values is not None else None,
        "dds_last4": entry.dds_last4,
    }


def _built_entry_from_json(raw: object) -> BuiltOverlayEntry | None:
    """反序列化 BuiltOverlayEntry 缓存元数据。"""
    if not isinstance(raw, dict):
        return None
    try:
        dds_m_values = raw.get("dds_m_values")
        return BuiltOverlayEntry(
            entry_path=str(raw["entry_path"]),
            dir_path=str(raw["dir_path"]),
            filename=str(raw["filename"]),
            paz_offset=int(raw["paz_offset"]),
            comp_size=int(raw["comp_size"]),
            decomp_size=int(raw["decomp_size"]),
            flags=int(raw["flags"]),
            dds_m_values=tuple(dds_m_values) if isinstance(dds_m_values, list) else None,  # type: ignore[arg-type]
            dds_last4=int(raw.get("dds_last4", 0)),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _reset_vfs_active_dir(game_dir: Path) -> Path:
    """重建本次 VFS 输出目录，只清理 .cdloader 内部实验目录。"""
    vfs_root = game_dir / WORK_DIR_NAME / VFS_ACTIVE_DIR_NAME
    expected_parent = (game_dir / WORK_DIR_NAME).resolve()
    if vfs_root.exists() and vfs_root.resolve().parent != expected_parent:
        raise ValueError(f"VFS 输出目录异常，拒绝清理：{vfs_root}")
    if vfs_root.exists():
        shutil.rmtree(vfs_root)
    vfs_root.mkdir(parents=True, exist_ok=True)
    return vfs_root


def _write_vfs_file(vfs_root: Path, relative_path: str, content: bytes | bytearray) -> None:
    """把单个虚拟文件写入 VFS 输出目录。"""
    target = vfs_root / relative_path.replace("/", "\\")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


def _write_mapping_manifest(
    game_dir: Path,
    vfs_root: Path,
    mapping_path: Path,
    mapped_files: list[str],
) -> None:
    """生成 vfsDmoe mapping_tree.json。"""
    entries = []
    for index, relative_path in enumerate(mapped_files):
        normalized_rel = relative_path.replace("\\", "/")
        source_path = vfs_root / normalized_rel.replace("/", "\\")
        entries.append(
            {
                "enabled": True,
                "is_active": True,
                "load_order_index": index,
                "source_absolute_path": str(source_path),
                "virtual_relative_path": normalized_rel,
                "runtime_virtual_relative_path": normalized_rel,
            }
        )

    payload = {
        "schema": 1,
        "generator": "cdmm-vfs-experiment",
        "game_root": str(game_dir),
        "entries": entries,
    }
    mapping_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _write_vfs_state(
    game_dir: Path,
    vfs_root: Path,
    mapping_path: Path,
    overlay_dir: str | None,
    overlay_packages: list[str],
    mods: list[DiscoveredMod],
    warnings: list[str],
    mapped_files: list[str],
    allow_missing_targets: bool,
    active_language: str | None,
) -> None:
    """写入 VFS 构建摘要，便于后续排障。"""
    payload = {
        "schema": VFS_STATE_SCHEMA,
        "game_dir": str(game_dir),
        "vfs_root": str(vfs_root),
        "mapping_path": str(mapping_path),
        "overlay_dir": overlay_dir,
        "overlay_packages": overlay_packages,
        "last_fingerprint": fingerprint_mods(mods),
        "allow_missing_targets": bool(allow_missing_targets),
        "active_language": active_language,
        "standalone_conflicts_checked": True,
        "loaded_mods": [mod.name for mod in mods],
        "warnings": warnings,
        "mapped_files": mapped_files,
    }
    state_path = game_dir / WORK_DIR_NAME / VFS_STATE_FILE_NAME
    state_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

