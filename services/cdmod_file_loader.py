"""把 ``.cdmod`` 完整资源载荷转换为 overlay entry。"""

from __future__ import annotations

import bisect
import hashlib
from pathlib import Path

from cdmm.archive.pathc_handler import get_path_hash, read_pathc
from cdmm.archive.pamt import derive_pamt_dir
from cdmm.common.constants import (
    META_DIR_NAME,
    PATHC_FILE_NAME,
    VANILLA_DIR_NAME,
    WORK_DIR_NAME,
)
from cdmm.common.models import OverlayInputEntry, PazEntry
from cdmm.services.cdmod_package import (
    CdmodFileReplacement,
    CdmodPackage,
    CdmodProfiledFilePatch,
)
from cdmm.services.json_loader import extract_plaintext
from cdmm.services.pamt_index_service import get_game_pamt_index
from cdmm.storage.vanilla_store import VanillaStore
from cdmm.utils.path_utils import lower_game_rel_path

# 表和 meta 必须走专用语义/重建组件，禁止退化成完整文件覆盖。
_FORBIDDEN_SUFFIXES = (".pabgb", ".pabgh", ".pamt", ".paz", ".papgt", ".pathc")

# PATHC hash 表按文件状态缓存，避免大型 DDS 模组为每个文件重复解析 meta。
_PATHC_HASH_CACHE: dict[tuple[str, int, int], tuple[int, ...]] = {}


def collect_file_replacement_pamt_targets(packages: list[CdmodPackage]) -> list[str]:
    """收集完整资源替换需要查询的目标。"""
    targets = [
        file.target
        for package in packages
        for patch in package.file_patches
        for file in patch.files
    ]
    targets.extend(
        target
        for package in packages
        for patch in package.profiled_file_patches
        for target in (
            patch.probe_target,
            *(file.target for file in patch.files),
        )
    )
    return list(dict.fromkeys(targets))


def build_file_replacement_overlay_entries(
    game_dir: Path,
    packages: list[CdmodPackage],
    vanilla_store: VanillaStore,
    warnings: list[str],
    errors: list[str],
    base_entries: list[OverlayInputEntry] | None = None,
) -> list[OverlayInputEntry]:
    """按包顺序应用完整资源替换，最终目标元数据来自当前游戏。"""
    base_entries = base_entries or []
    outputs: dict[tuple[str, str], OverlayInputEntry] = {}
    owners: dict[tuple[str, str], str] = {}
    for package in packages:
        for patch in package.file_patches:
            for file in patch.files:
                _apply_replacement(
                    game_dir,
                    package.name,
                    file,
                    vanilla_store,
                    warnings,
                    errors,
                    base_entries,
                    outputs,
                    owners,
                )
        for patch in package.profiled_file_patches:
            selected_profile = _resolve_profile(
                game_dir,
                package.name,
                patch,
                base_entries,
                outputs,
                warnings,
                errors,
            )
            if selected_profile is None:
                continue
            for file in patch.files:
                selected = next(
                    (
                        variant
                        for variant in file.variants
                        if variant.profile_id == selected_profile
                    ),
                    file.fallback,
                )
                replacement = CdmodFileReplacement(
                    target=file.target,
                    pamt_dir=file.pamt_dir,
                    payload_path=selected.payload_path,
                    sha256=selected.sha256,
                    content=selected.content,
                    index=file.index,
                )
                _apply_replacement(
                    game_dir,
                    package.name,
                    replacement,
                    vanilla_store,
                    warnings,
                    errors,
                    base_entries,
                    outputs,
                    owners,
                )
    if outputs:
        warnings.append(f"cdmod 完整资源：生成 {len(outputs)} 个替换 entry")
    return list(outputs.values())


def _resolve_profile(
    game_dir: Path,
    package_name: str,
    patch: CdmodProfiledFilePatch,
    base_entries: list[OverlayInputEntry],
    outputs: dict[tuple[str, str], OverlayInputEntry],
    warnings: list[str],
    errors: list[str],
) -> str | None:
    """读取最终探针资源一次，并选择已声明配置或回退配置。"""
    probe = _resolve_target(
        game_dir,
        patch.probe_pamt_dir,
        patch.probe_target,
        [*base_entries, *outputs.values()],
    )
    if probe is None:
        errors.append(
            f"{package_name}: 体型探针不存在："
            f"{patch.probe_pamt_dir}/{patch.probe_target}"
        )
        return None
    content = (
        probe.content
        if isinstance(probe, OverlayInputEntry)
        else extract_plaintext(probe)[0]
    )
    probe_sha256 = hashlib.sha256(content).hexdigest()
    matched = next(
        (
            profile.profile_id
            for profile in patch.profiles
            if profile.probe_sha256 == probe_sha256
        ),
        None,
    )
    if matched is not None:
        warnings.append(
            f"{package_name}: 体型探针命中 {matched} "
            f"({patch.probe_target}, sha256={probe_sha256})"
        )
        return matched
    fallback = patch.files[0].fallback.profile_id
    warnings.append(
        f"{package_name}: 未识别体型探针 sha256={probe_sha256}，已回退 {fallback}"
    )
    return fallback


def _apply_replacement(
    game_dir: Path,
    package_name: str,
    file: CdmodFileReplacement,
    vanilla_store: VanillaStore,
    warnings: list[str],
    errors: list[str],
    base_entries: list[OverlayInputEntry],
    outputs: dict[tuple[str, str], OverlayInputEntry],
    owners: dict[tuple[str, str], str],
) -> None:
    """把普通或已选择的条件载荷写入统一完整资源输出。"""
    normalized = lower_game_rel_path(file.target)
    identity = (file.pamt_dir, normalized)
    if normalized.endswith(_FORBIDDEN_SUFFIXES) and not file.allow_table_replace:
        errors.append(
            f"{package_name}: file-replacement 禁止覆盖表/归档/meta：{file.target}"
        )
        return
    target = _resolve_target(
        game_dir,
        file.pamt_dir,
        file.target,
        [*base_entries, *outputs.values()],
    )
    if target is None:
        if file.allow_new:
            outputs[identity] = OverlayInputEntry(
                content=file.content,
                entry_path=file.target,
                pamt_dir=file.pamt_dir,
                compression_type=0,
                preserve_entry_dir=True,
            )
            owners[identity] = package_name
            return
        errors.append(f"{package_name}: {file.pamt_dir} 中未找到资源目标 {file.target}")
        return
    previous = owners.get(identity)
    if previous is not None:
        warnings.append(
            f"cdmod 完整资源冲突：{file.pamt_dir}/{file.target} "
            f"按加载顺序由 {package_name} 覆盖 {previous}"
        )
    outputs[identity] = _replacement_entry(
        game_dir,
        file.target,
        target,
        file.content,
        vanilla_store,
    )
    owners[identity] = package_name


def _resolve_target(
    game_dir: Path,
    pamt_dir: str,
    target: str,
    base_entries: list[OverlayInputEntry],
) -> OverlayInputEntry | PazEntry | None:
    """同目录同路径 base 优先，否则从当前 vanilla PAMT 查询。"""
    normalized = lower_game_rel_path(target)
    matches = [
        entry for entry in base_entries if _matches_entry(entry, pamt_dir, normalized)
    ]
    if matches:
        return matches[-1]
    return get_game_pamt_index(game_dir).find_in_dir(pamt_dir, target)


def _matches_entry(
    entry: OverlayInputEntry,
    pamt_dir: str,
    normalized_target: str,
) -> bool:
    """同时识别扁平 PAMT path 和带 resolved_dir_path 的最终路径。"""
    if entry.pamt_dir != pamt_dir:
        return False
    entry_path = lower_game_rel_path(entry.entry_path)
    if entry_path == normalized_target:
        return True
    if not entry.resolved_dir_path:
        return False
    basename = entry_path.rsplit("/", 1)[-1]
    final_path = lower_game_rel_path(f"{entry.resolved_dir_path}/{basename}")
    return final_path == normalized_target


def _replacement_entry(
    game_dir: Path,
    declared_target: str,
    target: OverlayInputEntry | PazEntry,
    content: bytes,
    vanilla_store: VanillaStore,
) -> OverlayInputEntry:
    """保留目标 PAMT 元数据并替换资源明文。"""
    if isinstance(target, OverlayInputEntry):
        return OverlayInputEntry(
            content,
            target.entry_path,
            target.pamt_dir,
            target.compression_type,
            target.encrypted,
            target.crypto_filename,
            target.preserve_entry_dir,
            target.resolved_dir_path,
        )
    entry = vanilla_store.ensure_entry_backup(target)
    entry_path = entry.path
    if declared_target.lower().endswith(".dds") and _pathc_contains(
        game_dir, declared_target
    ):
        entry_path = lower_game_rel_path(declared_target)
    return OverlayInputEntry(
        content=content,
        entry_path=entry_path,
        pamt_dir=derive_pamt_dir(entry.paz_file),
        compression_type=entry.compression_type,
        encrypted=entry.encrypted,
        crypto_filename=Path(entry.path).name,
        resolved_dir_path=entry.resolved_dir_path,
    )


def _pathc_contains(game_dir: Path, target: str) -> bool:
    """确认声明的 DDS 最终路径已由当前原版 PATHC 注册。"""
    path = game_dir / WORK_DIR_NAME / VANILLA_DIR_NAME / META_DIR_NAME / PATHC_FILE_NAME
    if not path.is_file():
        path = game_dir / META_DIR_NAME / PATHC_FILE_NAME
    if not path.is_file():
        return False
    stat = path.stat()
    cache_key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    hashes = _PATHC_HASH_CACHE.get(cache_key)
    try:
        if hashes is None:
            hashes = tuple(read_pathc(path).key_hashes)
            _PATHC_HASH_CACHE[cache_key] = hashes
    except (OSError, ValueError):
        return False
    target_hash = get_path_hash(target)
    index = bisect.bisect_left(hashes, target_hash)
    return index < len(hashes) and hashes[index] == target_hash
