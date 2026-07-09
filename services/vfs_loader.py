"""VFS 实验加载服务。

本文件负责把现有模组合成结果写入独立 VFS 目录，并生成 vfsDmoe 可读取的
mapping_tree.json。它只写入 .cdloader 工作区，不直接修改游戏源文件。
"""

from __future__ import annotations

import json
import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass
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
from cdmm.common.models import BuiltOverlayEntry, DiscoveredMod, OverlayInputEntry
from cdmm.services.format3_loader import (
    build_format3_overlay_entries,
    collect_format3_pamt_targets,
    collect_format3_warnings,
)
from cdmm.services.json_loader import build_json_overlay_entries, collect_json_pamt_targets
from cdmm.services.loader import validate_game_dir
from cdmm.services.loose_file_service import build_loose_overlay_entries, collect_loose_pamt_targets
from cdmm.services.overlay_service import build_overlay, overlay_rel_paths
from cdmm.services.pamt_index_service import register_game_pamt_targets, save_game_pamt_target_cache
from cdmm.services.papgt_service import build_papgt
from cdmm.services.pathc_service import build_pathc_for_overlay
from cdmm.services.scanner import MOD_TYPE_FORMAT3, MOD_TYPE_JSON_PATCH, scan_mods
from cdmm.services.standalone_archive_service import collect_standalone_archives
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

# VFS 状态结构版本。分包策略变化时必须提升，避免复用旧 mapping。
VFS_STATE_SCHEMA = 2

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


def build_vfs_package(
    game_dir: Path,
    allow_missing_targets: bool = False,
    progress_callback: Callable[[str], None] | None = None,
) -> VfsBuildResult:
    """构建 VFS 运行目录和 mapping_tree.json，不修改游戏源文件。"""
    _notify_progress(progress_callback, "检查游戏目录")
    validate_game_dir(game_dir)
    _ensure_vfs_work_dirs(game_dir)

    warnings: list[str] = []
    errors: list[str] = []
    _notify_progress(progress_callback, "扫描 mods 并同步加载顺序")
    mods, scan_warnings = scan_mods(game_dir)
    warnings.extend(scan_warnings)
    current_fingerprint = fingerprint_mods(mods)
    cached_result = _try_reuse_vfs_package(
        game_dir,
        mods,
        warnings,
        current_fingerprint,
        allow_missing_targets,
    )
    if cached_result is not None:
        _notify_progress(progress_callback, "VFS 缓存命中，复用已构建包")
        return cached_result

    _notify_progress(progress_callback, "统计 JSON / Format 3 / loose 目标")
    json_mods = [mod for mod in mods if mod.mod_type == MOD_TYPE_JSON_PATCH]
    format3_mods = [mod for mod in mods if mod.mod_type == MOD_TYPE_FORMAT3]
    warnings.extend(collect_format3_warnings(format3_mods))

    pamt_targets = [
        *collect_loose_pamt_targets(game_dir, mods),
        *collect_json_pamt_targets(json_mods),
        *collect_format3_pamt_targets(format3_mods),
    ]
    register_game_pamt_targets(game_dir, pamt_targets)

    _notify_progress(progress_callback, "准备原版 meta 与 PAMT 索引")
    vanilla_store = VanillaStore(game_dir)
    vanilla_store.ensure_meta_backup()

    _notify_progress(progress_callback, "构建 loose 文件覆盖输入")
    loose_overlay_inputs = build_loose_overlay_entries(
        game_dir,
        vanilla_store,
        warnings,
        errors,
        mods,
    )
    _notify_progress(progress_callback, "构建 JSON 补丁覆盖输入")
    json_overlay_inputs = build_json_overlay_entries(
        game_dir,
        json_mods,
        vanilla_store,
        warnings,
        errors,
        loose_overlay_inputs,
    )
    _notify_progress(progress_callback, "构建 Format 3 语义覆盖输入")
    format3_overlay_inputs = build_format3_overlay_entries(
        game_dir,
        format3_mods,
        vanilla_store,
        warnings,
        errors,
        [*loose_overlay_inputs, *json_overlay_inputs],
    )
    overlay_packages = _build_dmm_like_overlay_packages(
        loose_overlay_inputs,
        json_overlay_inputs,
        format3_overlay_inputs,
    )
    if allow_missing_targets:
        errors = _downgrade_missing_target_errors(errors, warnings)
    if errors:
        return _empty_result(game_dir, mods, warnings, errors)

    _notify_progress(progress_callback, "收集 standalone PAZ/PAMT")
    previous_state = load_state(game_dir)
    previous_standalone_items = previous_state.get("standalone_dirs")
    if not isinstance(previous_standalone_items, list):
        previous_standalone_items = []
    standalone_archives = collect_standalone_archives(
        game_dir,
        previous_items=previous_standalone_items,
        ordered_mods=mods,
    )

    if not overlay_packages and not standalone_archives:
        warnings.append("没有生成 VFS overlay entry，mapping_tree.json 未写入")
        return _empty_result(game_dir, mods, warnings, errors)

    vfs_root = _reset_vfs_active_dir(game_dir)
    mapped_files: list[str] = []
    modified_pamts: dict[str, bytes] = {}
    package_built_entries: list[BuiltOverlayEntry] = []
    package_input_entries: list[OverlayInputEntry] = []
    package_names: list[str] = []
    pathc_bytes: bytes | None = None

    for package in overlay_packages:
        _notify_progress(progress_callback, f"写入 VFS overlay 包：{package.name}")
        overlay = build_overlay(package.name, package.entries, game_dir)
        paz_rel, pamt_rel = overlay_rel_paths(package.name)
        _write_vfs_file(vfs_root, paz_rel, overlay.paz_bytes)
        _write_vfs_file(vfs_root, pamt_rel, overlay.pamt_bytes)
        mapped_files.extend([paz_rel, pamt_rel])
        modified_pamts[package.name] = overlay.pamt_bytes
        package_built_entries.extend(overlay.entries)
        package_input_entries.extend(package.entries)
        package_names.append(package.name)

    standalone_pamts = {archive.assigned_dir: archive.pamt_bytes for archive in standalone_archives}
    modified_pamts.update(standalone_pamts)
    for archive in standalone_archives:
        _notify_progress(progress_callback, f"写入 standalone 包：{archive.assigned_dir}")
        _write_vfs_file(vfs_root, f"{archive.assigned_dir}/{OVERLAY_PAZ_NAME}", archive.paz_bytes)
        _write_vfs_file(vfs_root, f"{archive.assigned_dir}/{OVERLAY_PAMT_NAME}", archive.pamt_bytes)
        mapped_files.extend(
            [
                f"{archive.assigned_dir}/{OVERLAY_PAZ_NAME}",
                f"{archive.assigned_dir}/{OVERLAY_PAMT_NAME}",
            ]
        )

    if package_input_entries:
        _notify_progress(progress_callback, "构建 PATHC 纹理映射")
        pathc_bytes = build_pathc_for_overlay(
            game_dir,
            vanilla_store,
            package_input_entries,
            package_built_entries,
            warnings,
        )

    _notify_progress(progress_callback, "构建 PAPGT 并规范化 VFS flags")
    papgt_order = [*NPP_LIKE_PACKAGE_ORDER, *(archive.assigned_dir for archive in standalone_archives)]
    papgt_bytes = build_papgt(
        game_dir,
        vanilla_store,
        modified_pamts,
        prepend_order=papgt_order,
        normalize_existing_flags=True,
    )

    _write_vfs_file(vfs_root, f"{META_DIR_NAME}/{PAPGT_FILE_NAME}", papgt_bytes)
    mapped_files.append(f"{META_DIR_NAME}/{PAPGT_FILE_NAME}")
    if pathc_bytes is not None:
        _write_vfs_file(vfs_root, f"{META_DIR_NAME}/{PATHC_FILE_NAME}", pathc_bytes)
        mapped_files.append(f"{META_DIR_NAME}/{PATHC_FILE_NAME}")

    mapping_path = game_dir / WORK_DIR_NAME / VFS_MAPPING_FILE_NAME
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
    )
    save_game_pamt_target_cache(game_dir)
    _notify_progress(progress_callback, "VFS 构建完成")
    logger.info("VFS package built: %s", mapping_path)
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
    if state.get("last_fingerprint") != current_fingerprint:
        return None
    if bool(state.get("allow_missing_targets")) != bool(allow_missing_targets):
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

    grouped[NPP_JSON_PACKAGE].extend(json_overlay_inputs)
    for entry in format3_overlay_inputs:
        package_name = _format3_package_name(entry) or NPP_JSON_PACKAGE
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


def _write_vfs_file(vfs_root: Path, relative_path: str, content: bytes) -> None:
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
        "loaded_mods": [mod.name for mod in mods],
        "warnings": warnings,
        "mapped_files": mapped_files,
    }
    state_path = game_dir / WORK_DIR_NAME / VFS_STATE_FILE_NAME
    state_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

