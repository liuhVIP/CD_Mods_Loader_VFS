"""mods 目录扫描与模组类型识别。"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from cdmm.common.constants import (
    ARCHIVE_SUFFIXES,
    DISABLED_MODS_FILE_NAME,
    DISABLED_MOD_TYPES_FILE_NAME,
    DDS_SUFFIX,
    GAME_DIR_NAME_LENGTH,
    GAME_FILES_DIR_NAME,
    JSON_SUFFIX,
    KNOWN_GAME_TOP_DIRS,
    LOAD_ORDER_FILE_NAME,
    LOOSE_FILES_DIR_NAME,
    META_DIR_NAME,
    MODS_DIR_NAME,
    OVERLAY_PAMT_NAME,
    OVERLAY_PAZ_NAME,
    PAPGT_FILE_NAME,
    PATHC_FILE_NAME,
    WORK_DIR_NAME,
)
from cdmm.common.models import DiscoveredMod
from cdmm.utils.hash_utils import fingerprint_path
from cdmm.utils.json_utils import load_json_object, load_json_optional
from cdmm.utils.path_utils import game_rel_path

logger = logging.getLogger(__name__)

MOD_TYPE_JSON_PATCH = "json_patch"
MOD_TYPE_FORMAT3 = "format3"
MOD_TYPE_CDMOD = "cdmod"
MOD_TYPE_LOOSE_FILES = "loose_files"
MOD_TYPE_STANDALONE_ARCHIVE = "standalone_archive"
MOD_TYPE_META = "meta"
MOD_TYPE_DDS = "dds"

# 空目录跳过提示前缀，CLI 会据此把空目录单独整理展示给用户。
EMPTY_MOD_DIR_WARNING_PREFIX = "空目录（已跳过）："

# 损坏cdmod必须让apply/VFS拒绝本轮构建，不能按普通未知文件静默忽略。
INVALID_CDMOD_WARNING_PREFIX = "无效cdmod（本次构建将拒绝）："

# 目录型模组可能包含多种组件，按固定顺序合并展示。
DIRECTORY_MOD_TYPE_ORDER = (
    MOD_TYPE_LOOSE_FILES,
    MOD_TYPE_DDS,
    MOD_TYPE_STANDALONE_ARCHIVE,
    MOD_TYPE_META,
)

# Crimson Mod Package统一容器扩展名。
CDMOD_SUFFIX = ".cdmod"


def scan_mods(game_dir: Path) -> tuple[list[DiscoveredMod], list[str]]:
    """扫描 game_dir/mods 并返回可处理模组与提示信息。"""
    mods_dir = game_dir / MODS_DIR_NAME
    warnings: list[str] = []
    if not mods_dir.exists():
        mods_dir.mkdir(parents=True, exist_ok=True)
        _sync_and_apply_load_order(game_dir, mods_dir, [], warnings)
        warnings.append(f"mods 目录不存在，已创建：{mods_dir}")
        return [], warnings

    candidates = _collect_loadable_candidates(_collect_candidates(mods_dir, warnings))
    candidates = _filter_disabled_type_candidates(game_dir, candidates, warnings)
    candidates = _filter_disabled_candidates(game_dir, mods_dir, candidates, warnings)
    ordered = _sync_and_apply_load_order(game_dir, mods_dir, candidates, warnings)
    discovered: list[DiscoveredMod] = []
    seen_hashes: set[str] = set()

    for candidate in ordered:
        mod_type = detect_mod_type(candidate)
        if mod_type is None:
            continue
        fingerprint = fingerprint_path(candidate)
        if fingerprint in seen_hashes:
            warnings.append(f"跳过重复模组：{candidate}")
            continue
        seen_hashes.add(fingerprint)
        discovered.append(
            DiscoveredMod(
                name=candidate.name,
                path=candidate,
                mod_type=mod_type,
                fingerprint=fingerprint,
            )
        )
    _write_primary_load_order(
        game_dir,
        [mod.path.relative_to(mods_dir).as_posix() for mod in discovered],
        warnings,
    )
    return discovered, warnings


def _collect_loadable_candidates(candidates: list[Path]) -> list[Path]:
    """过滤 manifest/modinfo 等不会参与加载的候选，只保留真实可加载模组。"""
    return [candidate for candidate in candidates if detect_mod_type(candidate) is not None]


def _filter_disabled_candidates(
    game_dir: Path,
    mods_dir: Path,
    candidates: list[Path],
    warnings: list[str],
) -> list[Path]:
    """按 .cdloader/disabled_mods.json 跳过临时禁用的模组。"""
    disabled_path = game_dir / WORK_DIR_NAME / DISABLED_MODS_FILE_NAME
    if not disabled_path.exists():
        return candidates
    try:
        raw_items = json.loads(disabled_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        warnings.append(f"{disabled_path}: 禁用模组文件无法解析，已忽略")
        return candidates
    if not isinstance(raw_items, list):
        warnings.append(f"{disabled_path}: 禁用模组文件不是 JSON 数组，已忽略")
        return candidates

    disabled_items = [item for item in raw_items if isinstance(item, str)]
    by_key = _candidate_lookup(mods_dir, candidates)
    disabled: set[Path] = set()
    synced_disabled: list[str] = []
    for item in disabled_items:
        matches = by_key.get(game_rel_path(item).lower())
        if not matches:
            continue
        disabled.update(matches)
        synced_disabled.append(item)

    _write_disabled_mods(disabled_path, synced_disabled, warnings)
    if disabled:
        warnings.append(f"已按 disabled_mods.json 跳过 {len(disabled)} 个模组")
    return [candidate for candidate in candidates if candidate not in disabled]


def _filter_disabled_type_candidates(
    game_dir: Path,
    candidates: list[Path],
    warnings: list[str],
) -> list[Path]:
    """按 .cdloader/disabled_mod_types.json 跳过某类模组。"""
    disabled_types_path = game_dir / WORK_DIR_NAME / DISABLED_MOD_TYPES_FILE_NAME
    if not disabled_types_path.exists():
        return candidates
    disabled_types = _read_disabled_types(disabled_types_path, warnings)
    if not disabled_types:
        return candidates

    enabled: list[Path] = []
    skipped = 0
    for candidate in candidates:
        mod_type = detect_mod_type(candidate)
        if mod_type is not None and _type_matches_disabled(mod_type, disabled_types):
            skipped += 1
            continue
        enabled.append(candidate)
    if skipped:
        warnings.append(
            f"已按 disabled_mod_types.json 跳过 {skipped} 个模组，类型：{', '.join(sorted(disabled_types))}"
        )
    return enabled


def _read_disabled_types(path: Path, warnings: list[str]) -> set[str]:
    """读取并规范化禁用类型列表。"""
    try:
        raw_items = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        warnings.append(f"{path}: 禁用类型文件无法解析，已忽略")
        return set()
    if not isinstance(raw_items, list):
        warnings.append(f"{path}: 禁用类型文件不是 JSON 数组，已忽略")
        return set()
    normalized = {
        item.strip().lower()
        for item in raw_items
        if isinstance(item, str) and item.strip()
    }
    _write_disabled_types(path, sorted(normalized), warnings)
    return normalized


def _write_disabled_types(path: Path, disabled_types: list[str], warnings: list[str]) -> None:
    """规范化 disabled_mod_types.json。"""
    try:
        content = json.dumps(disabled_types, ensure_ascii=False, indent=2) + "\n"
        if path.read_text(encoding="utf-8-sig") == content:
            return
        path.write_text(content, encoding="utf-8")
    except OSError as exc:
        warnings.append(f"{path}: 禁用类型文件写入失败：{exc}")


def _type_matches_disabled(mod_type: str, disabled_types: set[str]) -> bool:
    """组合类型按组件匹配，loose_files+dds 可被任一组件禁用。"""
    parts = {part.strip().lower() for part in mod_type.split("+") if part.strip()}
    return bool(parts & disabled_types)


def _candidate_lookup(mods_dir: Path, candidates: list[Path]) -> dict[str, set[Path]]:
    """建立候选模组的多 key 查找表，支持名称、相对路径和顶层目录。"""
    by_key: dict[str, set[Path]] = {}
    for candidate in candidates:
        rel = candidate.relative_to(mods_dir).as_posix()
        keys = {rel.lower(), candidate.name.lower()}
        if rel:
            keys.add(rel.split("/", 1)[0].lower())
        for key in keys:
            by_key.setdefault(key, set()).add(candidate)
    return by_key


def _write_disabled_mods(path: Path, disabled_items: list[str], warnings: list[str]) -> None:
    """清理 disabled_mods.json 中已经不存在的项。"""
    try:
        content = json.dumps(disabled_items, ensure_ascii=False, indent=2) + "\n"
        if path.read_text(encoding="utf-8-sig") == content:
            return
        path.write_text(content, encoding="utf-8")
    except OSError as exc:
        warnings.append(f"{path}: 禁用模组文件写入失败：{exc}")


def detect_mod_type(path: Path) -> str | None:
    """识别传统 JSON patch、Format 3 JSON 或目录型组件。"""
    if path.is_file() and path.suffix.lower() == CDMOD_SUFFIX:
        try:
            from cdmm.services.cdmod_package import validate_cdmod_header

            validate_cdmod_header(path)
        except (OSError, ValueError):
            return None
        return MOD_TYPE_CDMOD
    if path.is_file() and path.suffix.lower() == JSON_SUFFIX:
        data = load_json_optional(path)
        if is_json_patch_data(data):
            return MOD_TYPE_JSON_PATCH
        if is_format3_data(data):
            return MOD_TYPE_FORMAT3
    if path.is_dir():
        component_types = _detect_directory_component_types(path)
        if component_types:
            return _format_directory_mod_type(component_types)
    return None


def is_json_patch_data(data: object) -> bool:
    """判断 JSON 内容是否为传统 byte patch。"""
    if not isinstance(data, dict):
        return False
    patches = data.get("patches")
    if not isinstance(patches, list) or not patches:
        return False
    return any(
        _is_non_empty_json_patch_item(item)
        for item in patches
    )


def _is_non_empty_json_patch_item(item: object) -> bool:
    """判断单个 JSON patch 目标是否包含真实字节改动，过滤空 DDS 映射清单。"""
    if not isinstance(item, dict):
        return False
    if not isinstance(item.get("game_file"), str):
        return False
    changes = item.get("changes")
    if not isinstance(changes, list):
        return False
    return any(_has_patch_bytes(change) for change in changes)


def _has_patch_bytes(change: object) -> bool:
    """判断 change 是否至少声明了 original/patched 中的一段字节内容。"""
    if not isinstance(change, dict):
        return False
    original = change.get("original")
    patched = change.get("patched")
    return (isinstance(original, str) and bool(original)) or (isinstance(patched, str) and bool(patched))


def is_format3_data(data: object) -> bool:
    """判断 JSON 内容是否为 Format 3 语义补丁，兼容单目标和多目标结构。"""
    if not isinstance(data, dict) or data.get("format") != 3:
        return False
    if isinstance(data.get("target"), str) and isinstance(data.get("intents"), list):
        return True
    targets = data.get("targets")
    return isinstance(targets, list) and any(
        isinstance(item, dict)
        and isinstance(item.get("file"), str)
        and isinstance(item.get("intents"), list)
        for item in targets
    )


def load_json_file(path: Path) -> dict:
    """读取 UTF-8/UTF-8 BOM JSON 文件。"""
    return load_json_object(path)


def _collect_candidates(mods_dir: Path, warnings: list[str]) -> list[Path]:
    """收集 JSON 文件和目录内 JSON，其他第二阶段组件只扫描提示。"""
    candidates: list[Path] = []
    for item in sorted(mods_dir.iterdir(), key=lambda p: p.name.lower()):
        suffix = item.suffix.lower()
        if item.is_file() and item.name == LOAD_ORDER_FILE_NAME:
            continue
        if item.is_file() and suffix in ARCHIVE_SUFFIXES:
            warnings.append(f"跳过压缩包，请先手动解压：{item.name}")
            continue
        if item.is_file() and suffix == CDMOD_SUFFIX:
            if detect_mod_type(item) is None:
                warnings.append(f"{INVALID_CDMOD_WARNING_PREFIX}{item.name}")
                continue
            candidates.append(item)
            continue
        if item.is_file() and suffix == JSON_SUFFIX:
            candidates.append(item)
            continue
        if item.is_dir():
            if _is_empty_directory(item):
                warnings.append(f"{EMPTY_MOD_DIR_WARNING_PREFIX}{item.name}")
                continue
            component_types = _scan_deferred_components(item, warnings)
            if component_types:
                candidates.append(item)
            for json_file in sorted(item.rglob(f"*{JSON_SUFFIX}"), key=lambda p: p.as_posix()):
                if _inside_game_archive_tree(json_file.relative_to(item)):
                    continue
                candidates.append(json_file)
            for cdmod_file in sorted(item.rglob(f"*{CDMOD_SUFFIX}"), key=lambda p: p.as_posix()):
                if detect_mod_type(cdmod_file) is None:
                    rel = cdmod_file.relative_to(mods_dir).as_posix()
                    warnings.append(f"{INVALID_CDMOD_WARNING_PREFIX}{rel}")
                    continue
                candidates.append(cdmod_file)
    return candidates


def _is_empty_directory(path: Path) -> bool:
    """判断顶层模组目录是否完全为空，空目录只提示不参与加载。"""
    return not any(path.iterdir())


def _scan_deferred_components(mod_dir: Path, warnings: list[str]) -> set[str]:
    """识别目录型组件并返回组件类型，具体提示写入 warnings。"""
    reported: set[str] = set()
    component_types: set[str] = set()
    for child in sorted(mod_dir.iterdir(), key=lambda p: p.name.lower()):
        lower_name = child.name.lower()
        if child.is_dir() and lower_name in {LOOSE_FILES_DIR_NAME, GAME_FILES_DIR_NAME}:
            component_types.update(_scan_loose_container(mod_dir, child, warnings, reported))
            continue
        if child.is_dir() and _is_game_archive_dir(child.name):
            component_type = _scan_numbered_dir(mod_dir, child, warnings, reported)
            if component_type is not None:
                component_types.add(component_type)
            continue
        if child.is_dir() and lower_name == META_DIR_NAME:
            if _scan_meta_dir(mod_dir, child, warnings, reported):
                component_types.add(MOD_TYPE_META)
            continue
        if child.is_dir() and lower_name in KNOWN_GAME_TOP_DIRS and _has_any_file(child):
            _append_once(
                warnings,
                reported,
                f"发现根路径 loose files 组件（将尝试加载）：{mod_dir.name}/{child.name}",
            )
            component_types.add(MOD_TYPE_LOOSE_FILES)
    if any(path.suffix.lower() == DDS_SUFFIX for path in mod_dir.rglob("*") if path.is_file()):
        _append_once(warnings, reported, f"发现 DDS 文件（将尝试更新 PATHC）：{mod_dir.name}")
        component_types.add(MOD_TYPE_DDS)
    return component_types


def _scan_loose_container(
    mod_dir: Path,
    container: Path,
    warnings: list[str],
    reported: set[str],
) -> None:
    """扫描 files/NNNN 或 game_files/NNNN 形式的 loose game files。"""
    component_types: set[str] = set()
    for child in sorted(container.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir():
            continue
        if _is_game_archive_dir(child.name) and _has_any_file(child):
            _append_once(
                warnings,
                reported,
                f"发现 loose files 组件（将尝试加载）：{mod_dir.name}/{container.name}/{child.name}",
            )
            component_types.add(MOD_TYPE_LOOSE_FILES)
        elif child.name.lower() in KNOWN_GAME_TOP_DIRS and _has_any_file(child):
            _append_once(
                warnings,
                reported,
                f"发现 loose files 组件（将尝试加载）：{mod_dir.name}/{container.name}/{child.name}",
            )
            component_types.add(MOD_TYPE_LOOSE_FILES)
    return component_types


def _scan_numbered_dir(
    mod_dir: Path,
    numbered_dir: Path,
    warnings: list[str],
    reported: set[str],
) -> str | None:
    """区分 standalone 归档目录和根部 NNNN loose files 目录。"""
    has_archive_pair = (numbered_dir / OVERLAY_PAZ_NAME).is_file() and (
        numbered_dir / OVERLAY_PAMT_NAME
    ).is_file()
    if has_archive_pair:
        _append_once(
            warnings,
            reported,
            f"发现 standalone PAZ/PAMT 组件（将尝试加载）：{mod_dir.name}/{numbered_dir.name}",
        )
        return MOD_TYPE_STANDALONE_ARCHIVE
    if not _has_any_file(numbered_dir):
        return None
    _append_once(
        warnings,
        reported,
        f"发现根部编号 loose files 组件（将尝试加载）：{mod_dir.name}/{numbered_dir.name}",
    )
    return MOD_TYPE_LOOSE_FILES


def _scan_meta_dir(mod_dir: Path, meta_dir: Path, warnings: list[str], reported: set[str]) -> bool:
    """扫描模组自带 meta 文件，当前只记录提示。"""
    meta_files: list[str] = []
    if (meta_dir / PAPGT_FILE_NAME).is_file():
        meta_files.append(PAPGT_FILE_NAME)
    if (meta_dir / PATHC_FILE_NAME).is_file():
        meta_files.append(PATHC_FILE_NAME)
    if meta_files:
        joined = ", ".join(meta_files)
        _append_once(warnings, reported, f"发现 meta 组件（将由加载器统一重建）：{mod_dir.name}/meta/{joined}")
        return True
    return False


def _detect_directory_component_types(mod_dir: Path) -> set[str]:
    """静默识别目录型模组组件类型，用于扫描汇总展示。"""
    component_types: set[str] = set()
    for child in mod_dir.iterdir():
        lower_name = child.name.lower()
        if child.is_dir() and lower_name in {LOOSE_FILES_DIR_NAME, GAME_FILES_DIR_NAME}:
            if _has_loose_container_entries(child):
                component_types.add(MOD_TYPE_LOOSE_FILES)
            continue
        if child.is_dir() and _is_game_archive_dir(child.name):
            has_archive_pair = (child / OVERLAY_PAZ_NAME).is_file() and (child / OVERLAY_PAMT_NAME).is_file()
            if has_archive_pair:
                component_types.add(MOD_TYPE_STANDALONE_ARCHIVE)
            elif _has_any_file(child):
                component_types.add(MOD_TYPE_LOOSE_FILES)
            continue
        if child.is_dir() and lower_name == META_DIR_NAME:
            if (child / PAPGT_FILE_NAME).is_file() or (child / PATHC_FILE_NAME).is_file():
                component_types.add(MOD_TYPE_META)
            continue
        if child.is_dir() and lower_name in KNOWN_GAME_TOP_DIRS and _has_any_file(child):
            component_types.add(MOD_TYPE_LOOSE_FILES)
    if any(path.suffix.lower() == DDS_SUFFIX for path in mod_dir.rglob("*") if path.is_file()):
        component_types.add(MOD_TYPE_DDS)
    return component_types


def _has_loose_container_entries(container: Path) -> bool:
    """判断 files/ 或 game_files/ 下是否有可加载 loose 路径。"""
    return any(
        child.is_dir()
        and (_is_game_archive_dir(child.name) or child.name.lower() in KNOWN_GAME_TOP_DIRS)
        and _has_any_file(child)
        for child in container.iterdir()
    )


def _has_any_file(path: Path) -> bool:
    """判断目录树内是否存在真实文件，空壳 loose 目录不参与加载。"""
    return any(item.is_file() for item in path.rglob("*"))


def _format_directory_mod_type(component_types: set[str]) -> str:
    """把目录型组件类型按固定顺序合并为展示用 mod_type。"""
    ordered = [component_type for component_type in DIRECTORY_MOD_TYPE_ORDER if component_type in component_types]
    return "+".join(ordered)


def _is_game_archive_dir(name: str) -> bool:
    """判断是否为 0000-9999 形式的游戏归档目录名。"""
    return len(name) == GAME_DIR_NAME_LENGTH and name.isdigit()


def _append_once(warnings: list[str], reported: set[str], message: str) -> None:
    """同一模组扫描内避免重复输出同类提示。"""
    if message in reported:
        return
    reported.add(message)
    warnings.append(message)


def _sync_and_apply_load_order(
    game_dir: Path,
    mods_dir: Path,
    candidates: list[Path],
    warnings: list[str],
) -> list[Path]:
    """同步 .cdloader/load_order.json，并返回实际加载顺序。"""
    configured_order = _read_existing_load_order(game_dir, mods_dir, warnings)
    synced_order, ordered_candidates = _normalize_load_order(mods_dir, candidates, configured_order)
    _write_primary_load_order(game_dir, synced_order, warnings)
    return ordered_candidates


def _read_existing_load_order(game_dir: Path, mods_dir: Path, warnings: list[str]) -> list[str]:
    """读取现有加载顺序；优先 .cdloader，兼容旧 mods 目录。"""
    load_order_path = _resolve_load_order_path(game_dir, mods_dir)
    if load_order_path is None:
        return []
    try:
        order = json.loads(load_order_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        warnings.append(f"{load_order_path}: 加载顺序文件无法解析，已按当前 mods 自动重建")
        return []
    if not isinstance(order, list):
        warnings.append(f"{load_order_path}: 加载顺序文件不是 JSON 数组，已按当前 mods 自动重建")
        return []
    return [item for item in order if isinstance(item, str)]


def _normalize_load_order(
    mods_dir: Path,
    candidates: list[Path],
    configured_order: list[str],
) -> tuple[list[str], list[Path]]:
    """删除过期排序项、补齐新增模组，并返回排序后的候选。"""
    by_key: dict[str, Path] = {}
    for candidate in candidates:
        rel = candidate.relative_to(mods_dir).as_posix()
        by_key[rel.lower()] = candidate
        by_key[candidate.name.lower()] = candidate

    synced_order: list[str] = []
    ordered: list[Path] = []
    used: set[Path] = set()
    for item in configured_order:
        match = by_key.get(game_rel_path(item).lower())
        if match is not None and match not in used:
            ordered.append(match)
            used.add(match)
            synced_order.append(match.relative_to(mods_dir).as_posix())

    for candidate in candidates:
        if candidate in used:
            continue
        ordered.append(candidate)
        used.add(candidate)
        synced_order.append(candidate.relative_to(mods_dir).as_posix())

    return synced_order, ordered


def _write_primary_load_order(game_dir: Path, synced_order: list[str], warnings: list[str]) -> None:
    """把规范化后的加载顺序写回 .cdloader/load_order.json。"""
    load_order_path = game_dir / WORK_DIR_NAME / LOAD_ORDER_FILE_NAME
    try:
        load_order_path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(synced_order, ensure_ascii=False, indent=2) + "\n"
        if load_order_path.exists() and load_order_path.read_text(encoding="utf-8-sig") == content:
            return
        load_order_path.write_text(content, encoding="utf-8")
    except OSError as exc:
        warnings.append(f"{load_order_path}: 加载顺序文件写入失败：{exc}")


def _resolve_load_order_path(game_dir: Path, mods_dir: Path) -> Path | None:
    """优先使用 .cdloader/load_order.json，兼容旧的 mods/load_order.json。"""
    loader_order_path = game_dir / WORK_DIR_NAME / LOAD_ORDER_FILE_NAME
    if loader_order_path.exists():
        return loader_order_path
    legacy_order_path = mods_dir / LOAD_ORDER_FILE_NAME
    if legacy_order_path.exists():
        return legacy_order_path
    return None


def _inside_game_archive_tree(rel_path: Path) -> bool:
    """避免把解包后的 meta/NNNN 游戏目录里的 JSON 误识别成补丁。"""
    parents = rel_path.parts[:-1]
    return any((part.isdigit() and len(part) == 4) or part.lower() == "meta" for part in parents)
