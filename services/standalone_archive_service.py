"""standalone PAZ/PAMT 模组分配与暂存服务。"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

from cdmm.archive.pamt import parse_pamt, parse_pamt_filtered
from cdmm.common.constants import (
    GAME_DIR_NAME_LENGTH,
    KNOWN_GAME_TOP_DIRS,
    LOOSE_FILES_DIR_NAME,
    MODS_DIR_NAME,
    OVERLAY_PAMT_NAME,
    OVERLAY_PAZ_NAME,
    OVERLAY_START_DIR,
)
from cdmm.common.models import DiscoveredMod, OverlayInputEntry, PazEntry
from cdmm.services.json_loader import decompress_entry
from cdmm.services.cdmod_package import load_cdmod_package
from cdmm.services.mod_risk_service import (
    VERIFIED_STANDALONE_CONFLICT_WARNING_PREFIX,
    build_verified_standalone_path_conflict_warning,
)
from cdmm.services.scanner import MOD_TYPE_CDMOD


# standalone 冲突提示前缀，用于构建日志、状态缓存与控制台醒目输出。
STANDALONE_CONFLICT_WARNING_PREFIX = VERIFIED_STANDALONE_CONFLICT_WARNING_PREFIX

# 已实机确认不能由两个 standalone 同时注册的条件装配表文件名。
CONDITIONAL_PART_PREFAB_TABLE_NAME = "conditionalpartprefab_transmog.xml"

# 全局身体裁剪描述表不能依赖重编号 standalone 的同路径覆盖优先级。
PART_SHRINK_DESCRIPTOR_PATH = "character/descriptors/partshrinkdesc.xml"


@dataclass(frozen=True)
class StandaloneArchive:
    """待写入游戏根目录的 standalone PAZ/PAMT 模组。"""

    mod_name: str
    source_dir: Path
    assigned_dir: str
    paz_bytes: bytes
    pamt_bytes: bytes


def collect_standalone_archives(
    game_dir: Path,
    reserved_dirs: set[str] | None = None,
    previous_items: list[dict[str, str]] | None = None,
    ordered_mods: list[DiscoveredMod] | None = None,
    warnings: list[str] | None = None,
) -> list[StandaloneArchive]:
    """收集 standalone PAZ/PAMT，并优先复用上次分配目录。"""
    mods_dir = game_dir / MODS_DIR_NAME
    if not mods_dir.exists():
        return []

    used_dirs = _collect_used_dirs(game_dir)
    if reserved_dirs:
        used_dirs.update(int(item) for item in reserved_dirs if _is_dir_name(item))
    previous_by_source = _previous_assigned_dirs(game_dir, previous_items or [])

    result: list[StandaloneArchive] = []
    for mod in ordered_mods or []:
        if mod.path.is_dir():
            for source_dir in _iter_standalone_dirs_for_mod(mod.path):
                _append_directory_archive(
                    game_dir,
                    source_dir,
                    previous_by_source,
                    used_dirs,
                    result,
                    warnings,
                )
        elif mod.mod_type == MOD_TYPE_CDMOD:
            _append_cdmod_archives(
                game_dir,
                mod,
                previous_by_source,
                used_dirs,
                result,
            )

    # ordered_mods 为空仅用于兼容直接调用；正常构建始终采用 scan_mods 的最终顺序。
    if ordered_mods is None:
        for source_dir in _iter_standalone_dirs(mods_dir):
            _append_directory_archive(
                game_dir,
                source_dir,
                previous_by_source,
                used_dirs,
                result,
                warnings,
            )

    if warnings is not None:
        _append_standalone_conflict_warnings(result, warnings)
    return result


def registered_papgt_dirs(papgt_bytes: bytes) -> set[str]:
    """读取 PAPGT 已登记的四位数字目录，供 standalone 安全编号避让。"""
    # 延迟导入复用 PAPGT 的唯一解析规则，避免复制二进制布局。
    from cdmm.services.papgt_service import directory_names

    return {name for name in directory_names(papgt_bytes) if _is_dir_name(name)}


def promote_partshrink_descriptor_archives(
    archives: list[StandaloneArchive],
    warnings: list[str] | None = None,
) -> tuple[list[StandaloneArchive], list[OverlayInputEntry]]:
    """把单文件 PartShrink 承载包提升为普通最终覆盖 entry。

    游戏对全局 ``partshrinkdesc.xml`` 的解析不稳定地遵循重编号 standalone
    优先级。仅当一个 archive 恰好只承载该文件时才提升并移除原 archive，避免
    改变普通服装、多文件 standalone 或其他全局描述表的行为。
    """
    remaining: list[StandaloneArchive] = []
    promoted: list[OverlayInputEntry] = []
    for archive in archives:
        entry = _read_single_partshrink_entry(archive)
        if entry is None:
            remaining.append(archive)
            continue
        try:
            content, resolved_entry = _read_archive_entry_content(archive, entry)
        except Exception as exc:
            remaining.append(archive)
            if warnings is not None:
                warnings.append(
                    f"{archive.mod_name}/{archive.source_dir.name}: "
                    f"PartShrink 描述表提升失败，保留 standalone：{exc}"
                )
            continue
        promoted.append(
            OverlayInputEntry(
                content=content,
                entry_path=PART_SHRINK_DESCRIPTOR_PATH,
                pamt_dir=archive.assigned_dir,
                compression_type=resolved_entry.compression_type,
                encrypted=resolved_entry.encrypted,
                crypto_filename=Path(PART_SHRINK_DESCRIPTOR_PATH).name,
                preserve_entry_dir=True,
                resolved_dir_path="character/descriptors",
            )
        )
        if warnings is not None:
            warnings.append(
                f"{archive.mod_name}/{archive.source_dir.name}: "
                "PartShrink 描述表已提升到 nppsa，避免重编号 standalone 同路径覆盖失效"
            )
    return remaining, promoted


def _read_single_partshrink_entry(archive: StandaloneArchive) -> PazEntry | None:
    """仅识别恰好包含一个 PartShrink 描述表的 standalone。"""
    with TemporaryDirectory(prefix="cdmm-partshrink-") as temp_dir:
        archive_dir = Path(temp_dir)
        (archive_dir / OVERLAY_PAMT_NAME).write_bytes(archive.pamt_bytes)
        (archive_dir / OVERLAY_PAZ_NAME).write_bytes(archive.paz_bytes)
        try:
            entries = parse_pamt(archive_dir / OVERLAY_PAMT_NAME, paz_dir=archive_dir)
        except (OSError, ValueError):
            return None
        if len(entries) != 1 or _entry_final_path(entries[0]) != PART_SHRINK_DESCRIPTOR_PATH:
            return None
        return entries[0]


def _read_archive_entry_content(
    archive: StandaloneArchive,
    entry: PazEntry,
) -> tuple[bytes, PazEntry]:
    """从内存中的 standalone PAZ 读取并解密解压一个 entry。"""
    raw = archive.paz_bytes[entry.offset : entry.offset + entry.comp_size]
    return decompress_entry(raw, entry)


def _entry_final_path(entry: PazEntry) -> str:
    """按 PAMT folder record 还原规范最终路径。"""
    filename = entry.path.replace("\\", "/").rsplit("/", 1)[-1]
    parent = (entry.resolved_dir_path or "").replace("\\", "/").strip("/")
    return f"{parent}/{filename}".strip("/").casefold()


def _append_directory_archive(
    game_dir: Path,
    source_dir: Path,
    previous_by_source: dict[str, str],
    used_dirs: set[int],
    result: list[StandaloneArchive],
    warnings: list[str] | None,
) -> None:
    """按最终加载顺序添加一个目录型 standalone archive。"""
    if _standalone_archive_is_duplicated_by_loose(source_dir):
        if warnings is not None:
            warnings.append(
                f"{source_dir.parent.name}/{source_dir.name}: standalone 内容已被同模组 loose 文件完整覆盖，已跳过重复 PAZ/PAMT"
            )
        return
    assigned_dir = _assign_archive_dir(game_dir, source_dir, previous_by_source, used_dirs)
    result.append(
        StandaloneArchive(
            mod_name=source_dir.parent.name,
            source_dir=source_dir,
            assigned_dir=assigned_dir,
            paz_bytes=(source_dir / OVERLAY_PAZ_NAME).read_bytes(),
            pamt_bytes=(source_dir / OVERLAY_PAMT_NAME).read_bytes(),
        )
    )


def _append_cdmod_archives(
    game_dir: Path,
    mod: DiscoveredMod,
    previous_by_source: dict[str, str],
    used_dirs: set[int],
    result: list[StandaloneArchive],
) -> None:
    """按最终加载顺序添加一个 cdmod 中的 standalone archive。"""
    package = load_cdmod_package(mod.path)
    for index, archive in enumerate(package.standalone_archives):
        source_dir = mod.path / f"standalone-{index}-{archive.name}"
        assigned_dir = _assign_archive_dir(game_dir, source_dir, previous_by_source, used_dirs)
        result.append(
            StandaloneArchive(
                mod_name=package.name,
                source_dir=source_dir,
                assigned_dir=assigned_dir,
                paz_bytes=archive.paz_bytes,
                pamt_bytes=archive.pamt_bytes,
            )
        )


def _assign_archive_dir(
    game_dir: Path,
    source_dir: Path,
    previous_by_source: dict[str, str],
    used_dirs: set[int],
) -> str:
    """复用或分配 standalone 目录，并立即占用该编号。"""
    assigned_dir = previous_by_source.get(_source_key(game_dir, source_dir))
    if assigned_dir is None or not _can_reuse_assigned_dir(game_dir, assigned_dir, used_dirs):
        assigned_dir = _next_free_dir(used_dirs)
    used_dirs.add(int(assigned_dir))
    return assigned_dir


def _append_standalone_conflict_warnings(
    archives: list[StandaloneArchive],
    warnings: list[str],
) -> None:
    """提示同一 PAMT 对应不同 PAZ；该风险只告警，不阻止用户尝试启动。"""
    verified_conflict_pamt_digests = _append_verified_final_path_conflicts(
        archives,
        warnings,
    )
    by_pamt: dict[str, list[StandaloneArchive]] = {}
    for archive in archives:
        by_pamt.setdefault(sha256(archive.pamt_bytes).hexdigest(), []).append(archive)

    for pamt_digest, group in by_pamt.items():
        if len(group) < 2:
            continue
        if pamt_digest in verified_conflict_pamt_digests:
            continue
        # 只对 PAMT 已重复的小组比较 PAZ，避免冷构建额外哈希所有大型归档。
        paz_signatures = {
            (len(archive.paz_bytes), sha256(archive.paz_bytes).digest())
            for archive in group
        }
        if len(paz_signatures) < 2:
            continue
        sources = "; ".join(
            f"{archive.mod_name}/{archive.source_dir.name} -> {archive.assigned_dir}"
            for archive in group
        )
        warnings.append(
            f"{STANDALONE_CONFLICT_WARNING_PREFIX}\n"
            f"PAMT 索引 SHA-256: {pamt_digest[:12]}\n"
            f"冲突 archive（加载顺序）: {sources}\n"
            f"同一 PAMT 索引对应 {len(paz_signatures)} 份不同 PAZ；加载器将继续启动，实际结果由游戏决定。"
        )


def _append_verified_final_path_conflicts(
    archives: list[StandaloneArchive],
    warnings: list[str],
) -> set[str]:
    """解析小型 PAMT，识别会让游戏重复解析的精确最终路径。"""
    archives_by_final_path: dict[str, list[StandaloneArchive]] = {}
    with TemporaryDirectory(prefix="cdmm-standalone-risk-") as temp_dir:
        temp_root = Path(temp_dir)
        for index, archive in enumerate(archives):
            archive_dir = temp_root / f"{index:04d}"
            archive_dir.mkdir()
            pamt_path = archive_dir / OVERLAY_PAMT_NAME
            pamt_path.write_bytes(archive.pamt_bytes)
            try:
                entries = parse_pamt_filtered(
                    pamt_path,
                    paz_dir=archive_dir,
                    desired_basenames={CONDITIONAL_PART_PREFAB_TABLE_NAME},
                )
            except (OSError, ValueError):
                continue
            for entry in entries:
                filename = entry.path.replace("\\", "/").rsplit("/", 1)[-1]
                final_path = (
                    f"{entry.resolved_dir_path}/{filename}"
                    if entry.resolved_dir_path
                    else entry.path
                ).replace("\\", "/").strip("/").casefold()
                archives_by_final_path.setdefault(final_path, []).append(archive)

    matched_pamt_digests: set[str] = set()
    for final_path, group in archives_by_final_path.items():
        if len(group) < 2:
            continue
        sources = [
            f"{archive.mod_name}/{archive.source_dir.name} -> {archive.assigned_dir}"
            for archive in group
        ]
        warning = build_verified_standalone_path_conflict_warning(final_path, sources)
        if warning is None:
            continue
        warnings.append(warning)
        matched_pamt_digests.update(
            sha256(archive.pamt_bytes).hexdigest()
            for archive in group
        )
    return matched_pamt_digests


def cleanup_stale_standalone_dirs(
    game_dir: Path,
    previous_items: list[dict[str, str]] | None,
    current_archives: list[StandaloneArchive],
) -> list[str]:
    """清理上次记录但本次不再使用的 standalone 输出目录。"""
    current_dirs = {archive.assigned_dir for archive in current_archives}
    removed: list[str] = []
    for item in previous_items or []:
        if not isinstance(item, dict):
            continue
        assigned_dir = item.get("assigned_dir")
        if not isinstance(assigned_dir, str) or assigned_dir in current_dirs:
            continue
        target = game_dir / assigned_dir
        if _looks_like_standalone_output(target):
            for child in target.iterdir():
                child.unlink()
            target.rmdir()
            removed.append(assigned_dir)
    return removed


def standalone_state_items(archives: list[StandaloneArchive]) -> list[dict[str, str]]:
    """把 standalone 分配结果写成 state.json 中的可恢复结构。"""
    return [
        {
            "mod_name": archive.mod_name,
            "source_dir": archive.source_dir.as_posix(),
            "assigned_dir": archive.assigned_dir,
        }
        for archive in archives
    ]


def _iter_standalone_dirs(
    mods_dir: Path,
    ordered_mods: list[DiscoveredMod] | None = None,
) -> list[Path]:
    """按稳定顺序枚举 standalone archive 目录。"""
    result: list[Path] = []
    for mod_dir in _iter_ordered_mod_dirs(mods_dir, ordered_mods):
        result.extend(_iter_standalone_dirs_for_mod(mod_dir))
    return result


def _iter_standalone_dirs_for_mod(mod_dir: Path) -> list[Path]:
    """枚举单个模组目录内的 standalone archive。"""
    return [
        child
        for child in sorted(
            (item for item in mod_dir.iterdir() if item.is_dir() and _is_dir_name(item.name)),
            key=_path_sort_key,
        )
        if (child / OVERLAY_PAZ_NAME).is_file() and (child / OVERLAY_PAMT_NAME).is_file()
    ]


def _standalone_archive_is_duplicated_by_loose(source_dir: Path) -> bool:
    """判断 standalone 中所有 entry 是否已有同模组 loose 原文件副本。"""
    entries = _parse_standalone_entries(source_dir)
    if not entries:
        return False
    loose_by_name = _collect_same_mod_loose_files(source_dir.parent)
    for entry in entries:
        candidates = loose_by_name.get(_entry_basename(entry))
        if len(candidates or []) != 1:
            return False
        try:
            archive_content = _read_standalone_entry_content(entry)
            loose_content = candidates[0].read_bytes()
        except OSError:
            return False
        except Exception:
            return False
        if archive_content != loose_content:
            return False
    return True


def _parse_standalone_entries(source_dir: Path) -> list[PazEntry]:
    """安全解析 standalone PAMT，解析失败时保持原行为继续加载。"""
    try:
        return parse_pamt(source_dir / OVERLAY_PAMT_NAME, paz_dir=source_dir)
    except Exception:
        return []


def _read_standalone_entry_content(entry: PazEntry) -> bytes:
    """读取 standalone entry 的解压后内容，用于和 loose 原文件去重。"""
    with Path(entry.paz_file).open("rb") as handle:
        handle.seek(entry.offset)
        raw = handle.read(entry.comp_size)
    content, _entry = decompress_entry(raw, entry)
    return content


def _collect_same_mod_loose_files(mod_dir: Path) -> dict[str, list[Path]]:
    """收集同一模组目录内 root game-path loose 文件，按 basename 建索引。"""
    result: dict[str, list[Path]] = {}
    for parent in _loose_search_roots(mod_dir):
        for child in sorted((item for item in parent.iterdir() if item.is_dir()), key=_path_sort_key):
            if child.name.lower() not in KNOWN_GAME_TOP_DIRS:
                continue
            for loose_file in sorted((item for item in child.rglob("*") if item.is_file()), key=_path_sort_key):
                result.setdefault(loose_file.name.lower(), []).append(loose_file)
    return result


def _loose_search_roots(mod_dir: Path) -> list[Path]:
    """返回需要检查 loose game-path 的目录根。"""
    roots = [mod_dir]
    files_dir = mod_dir / LOOSE_FILES_DIR_NAME
    if files_dir.is_dir():
        roots.append(files_dir)
    return roots


def _entry_basename(entry: PazEntry) -> str:
    """提取 PAMT entry 的文件名并统一大小写。"""
    normalized = entry.path.replace("\\", "/")
    return normalized.rsplit("/", 1)[-1].lower()


def _iter_ordered_mod_dirs(
    mods_dir: Path,
    ordered_mods: list[DiscoveredMod] | None,
) -> list[Path]:
    """按 scan_mods 解析出的加载顺序枚举目录型模组，其余目录按名称补齐。"""
    all_dirs = sorted((item for item in mods_dir.iterdir() if item.is_dir()), key=_path_sort_key)
    if ordered_mods is None:
        return all_dirs

    by_resolved = {path.resolve(): path for path in all_dirs}
    ordered: list[Path] = []
    used: set[Path] = set()
    for mod in ordered_mods:
        if not mod.path.is_dir():
            continue
        mod_dir = by_resolved.get(mod.path.resolve())
        if mod_dir is None or mod_dir in used:
            continue
        ordered.append(mod_dir)
        used.add(mod_dir)
    return ordered


def _collect_used_dirs(game_dir: Path) -> set[int]:
    """收集游戏根目录已有的四位数字目录编号。"""
    return {int(item.name) for item in game_dir.iterdir() if item.is_dir() and _is_dir_name(item.name)}


def _next_free_dir(used_dirs: set[int]) -> str:
    """从 overlay 起始目录开始分配空闲目录。"""
    candidate = OVERLAY_START_DIR
    while candidate in used_dirs:
        candidate += 1
    return f"{candidate:0{GAME_DIR_NAME_LENGTH}d}"


def _is_dir_name(value: str) -> bool:
    """判断是否为四位数字游戏目录名。"""
    return len(value) == GAME_DIR_NAME_LENGTH and value.isdigit()


def _previous_assigned_dirs(game_dir: Path, previous_items: list[dict[str, str]]) -> dict[str, str]:
    """读取 state 中的 standalone 源目录 -> 分配目录映射。"""
    result: dict[str, str] = {}
    for item in previous_items:
        if not isinstance(item, dict):
            continue
        source_dir = item.get("source_dir")
        assigned_dir = item.get("assigned_dir")
        if not isinstance(source_dir, str) or not isinstance(assigned_dir, str):
            continue
        if not _is_dir_name(assigned_dir):
            continue
        result[_source_key(game_dir, Path(source_dir))] = assigned_dir
    return result


def _can_reuse_assigned_dir(game_dir: Path, assigned_dir: str, used_dirs: set[int]) -> bool:
    """判断 state 中的目录号是否可以安全复用。"""
    if not _is_dir_name(assigned_dir):
        return False
    assigned_number = int(assigned_dir)
    target = game_dir / assigned_dir
    if target.exists() and not _looks_like_standalone_output(target):
        return False
    return assigned_number not in used_dirs or _looks_like_standalone_output(target)


def _looks_like_standalone_output(path: Path) -> bool:
    """确认目录只包含 standalone 输出文件，才允许复用或清理。"""
    if not path.is_dir():
        return False
    names = {item.name for item in path.iterdir()}
    return names.issubset({OVERLAY_PAZ_NAME, OVERLAY_PAMT_NAME})


def _source_key(game_dir: Path, source_dir: Path) -> str:
    """规范化 standalone 源目录路径，用于跨运行复用目录号。"""
    if not source_dir.is_absolute():
        source_dir = game_dir / source_dir
    try:
        return source_dir.resolve().relative_to(game_dir.resolve()).as_posix().lower()
    except ValueError:
        return source_dir.resolve().as_posix().lower()


def _path_sort_key(path: Path) -> str:
    """统一路径排序，保证分配结果稳定。"""
    return path.as_posix().lower()
