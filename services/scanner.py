"""mods 目录扫描与模组类型识别。"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from cdmm.common.constants import (
    ARCHIVE_SUFFIXES,
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
)
from cdmm.common.models import DiscoveredMod
from cdmm.utils.hash_utils import fingerprint_path
from cdmm.utils.json_utils import load_json_object, load_json_optional
from cdmm.utils.path_utils import game_rel_path

logger = logging.getLogger(__name__)

MOD_TYPE_JSON_PATCH = "json_patch"
MOD_TYPE_FORMAT3 = "format3"
MOD_TYPE_LOOSE_FILES = "loose_files"
MOD_TYPE_STANDALONE_ARCHIVE = "standalone_archive"
MOD_TYPE_META = "meta"
MOD_TYPE_DDS = "dds"

# 目录型模组可能包含多种组件，按固定顺序合并展示。
DIRECTORY_MOD_TYPE_ORDER = (
    MOD_TYPE_LOOSE_FILES,
    MOD_TYPE_DDS,
    MOD_TYPE_STANDALONE_ARCHIVE,
    MOD_TYPE_META,
)


def scan_mods(game_dir: Path) -> tuple[list[DiscoveredMod], list[str]]:
    """扫描 game_dir/mods 并返回可处理模组与提示信息。"""
    mods_dir = game_dir / MODS_DIR_NAME
    warnings: list[str] = []
    if not mods_dir.exists():
        mods_dir.mkdir(parents=True, exist_ok=True)
        warnings.append(f"mods 目录不存在，已创建：{mods_dir}")
        return [], warnings

    candidates = _collect_candidates(mods_dir, warnings)
    ordered = _apply_load_order(mods_dir, candidates)
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
    return discovered, warnings


def detect_mod_type(path: Path) -> str | None:
    """识别传统 JSON patch、Format 3 JSON 或目录型组件。"""
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
        isinstance(item, dict)
        and isinstance(item.get("game_file"), str)
        and isinstance(item.get("changes"), list)
        for item in patches
    )


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
        if item.is_file() and suffix == JSON_SUFFIX:
            candidates.append(item)
            continue
        if item.is_dir():
            component_types = _scan_deferred_components(item, warnings)
            if component_types:
                candidates.append(item)
            for json_file in sorted(item.rglob(f"*{JSON_SUFFIX}"), key=lambda p: p.as_posix()):
                if _inside_game_archive_tree(json_file.relative_to(item)):
                    continue
                candidates.append(json_file)
    return candidates


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
            component_types.add(_scan_numbered_dir(mod_dir, child, warnings, reported))
            continue
        if child.is_dir() and lower_name == META_DIR_NAME:
            if _scan_meta_dir(mod_dir, child, warnings, reported):
                component_types.add(MOD_TYPE_META)
            continue
        if child.is_dir() and lower_name in KNOWN_GAME_TOP_DIRS:
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
        if _is_game_archive_dir(child.name):
            _append_once(
                warnings,
                reported,
                f"发现 loose files 组件（将尝试加载）：{mod_dir.name}/{container.name}/{child.name}",
            )
            component_types.add(MOD_TYPE_LOOSE_FILES)
        elif child.name.lower() in KNOWN_GAME_TOP_DIRS:
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
) -> None:
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
            component_types.add(MOD_TYPE_STANDALONE_ARCHIVE if has_archive_pair else MOD_TYPE_LOOSE_FILES)
            continue
        if child.is_dir() and lower_name == META_DIR_NAME:
            if (child / PAPGT_FILE_NAME).is_file() or (child / PATHC_FILE_NAME).is_file():
                component_types.add(MOD_TYPE_META)
            continue
        if child.is_dir() and lower_name in KNOWN_GAME_TOP_DIRS:
            component_types.add(MOD_TYPE_LOOSE_FILES)
    if any(path.suffix.lower() == DDS_SUFFIX for path in mod_dir.rglob("*") if path.is_file()):
        component_types.add(MOD_TYPE_DDS)
    return component_types


def _has_loose_container_entries(container: Path) -> bool:
    """判断 files/ 或 game_files/ 下是否有可加载 loose 路径。"""
    return any(
        child.is_dir() and (_is_game_archive_dir(child.name) or child.name.lower() in KNOWN_GAME_TOP_DIRS)
        for child in container.iterdir()
    )


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


def _apply_load_order(mods_dir: Path, candidates: list[Path]) -> list[Path]:
    """按 mods/load_order.json 调整加载顺序，其余按路径升序追加。"""
    load_order_path = mods_dir / LOAD_ORDER_FILE_NAME
    if not load_order_path.exists():
        return candidates
    try:
        order = json.loads(load_order_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        logger.warning("load_order.json 无法解析，改用文件名升序")
        return candidates
    if not isinstance(order, list):
        return candidates

    by_key: dict[str, Path] = {}
    for candidate in candidates:
        rel = candidate.relative_to(mods_dir).as_posix()
        by_key[rel.lower()] = candidate
        by_key[candidate.name.lower()] = candidate

    ordered: list[Path] = []
    used: set[Path] = set()
    for item in order:
        if not isinstance(item, str):
            continue
        match = by_key.get(game_rel_path(item).lower())
        if match is not None and match not in used:
            ordered.append(match)
            used.add(match)
    ordered.extend(candidate for candidate in candidates if candidate not in used)
    return ordered


def _inside_game_archive_tree(rel_path: Path) -> bool:
    """避免把解包后的 meta/NNNN 游戏目录里的 JSON 误识别成补丁。"""
    parents = rel_path.parts[:-1]
    return any((part.isdigit() and len(part) == 4) or part.lower() == "meta" for part in parents)
