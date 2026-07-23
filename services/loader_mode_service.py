"""实体加载与 VFS 加载的互斥模式状态管理。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from cdmm.common.constants import (
    META_DIR_NAME,
    OVERLAY_PAMT_NAME,
    OVERLAY_PAZ_NAME,
    PAPGT_FILE_NAME,
    PATHC_FILE_NAME,
    WORK_DIR_NAME,
)
from cdmm.common.models import DiscoveredMod
from cdmm.storage.state_store import load_state
from cdmm.storage.vanilla_store import VanillaStore
from cdmm.utils.hash_utils import fingerprint_mods

# 实体加载模式状态文件；只允许由加载器创建和清理。
PHYSICAL_MODE_STATE_FILE_NAME = "physical_mode_state.json"

# apply 尚未安全完成时的保守锁定状态。
PHYSICAL_MODE_STATUS_PENDING = "pending"

# apply 已完成且游戏实体 meta/overlay 已被修改的状态。
PHYSICAL_MODE_STATUS_ACTIVE = "active"

# 模式状态结构版本。
PHYSICAL_MODE_STATE_SCHEMA = 1

# 物理模式热缓存结构版本；旧状态必须冷重建一次后才能复用。
PHYSICAL_MODE_CACHE_SCHEMA = 3

# 游戏主程序相对路径，用修改时间识别 Steam 游戏更新。
GAME_EXECUTABLE_RELATIVE_PATH = Path("bin64") / "CrimsonDesert.exe"

# 物理模式状态中的游戏版本与实体输出快照字段。
GAME_EXECUTABLE_MTIME_STATE_KEY = "game_executable_mtime_ns"
PHYSICAL_OUTPUT_SNAPSHOT_STATE_KEY = "output_snapshot"


@dataclass(frozen=True)
class PhysicalModeCacheCheck:
    """物理模式缓存校验结果。"""

    cache_hit: bool
    reason: str
    mod_fingerprint: str
    overlay_dir: str | None = None


class PhysicalModeConflictError(RuntimeError):
    """当前游戏目录已进入实体加载模式。"""


def physical_mode_state_path(game_dir: Path) -> Path:
    """返回实体加载模式状态文件路径。"""
    return game_dir / WORK_DIR_NAME / PHYSICAL_MODE_STATE_FILE_NAME


def begin_physical_mode(game_dir: Path, loader_version: str) -> dict[str, Any]:
    """在写入任何游戏文件前建立 pending 锁。"""
    state = {
        "schema": PHYSICAL_MODE_STATE_SCHEMA,
        "status": PHYSICAL_MODE_STATUS_PENDING,
        "loader_version": loader_version,
        "started_at": _utc_now_text(),
        "message": "实体加载正在执行或上次执行未安全完成，禁止使用 VFS。",
    }
    _write_state_atomic(physical_mode_state_path(game_dir), state)
    return state


def activate_physical_mode(
    game_dir: Path,
    loader_version: str,
    *,
    overlay_dir: str | None,
    mod_fingerprint: str,
    active_language: str | None = None,
) -> dict[str, Any]:
    """实体 apply 成功后把模式锁更新为 active。"""
    loader_state = load_state(game_dir)
    state = {
        "schema": PHYSICAL_MODE_STATE_SCHEMA,
        "cache_schema": PHYSICAL_MODE_CACHE_SCHEMA,
        "status": PHYSICAL_MODE_STATUS_ACTIVE,
        "loader_version": loader_version,
        "activated_at": _utc_now_text(),
        "overlay_dir": overlay_dir,
        "mod_fingerprint": mod_fingerprint,
        "active_language": active_language,
        GAME_EXECUTABLE_MTIME_STATE_KEY: _game_executable_mtime(game_dir),
        PHYSICAL_OUTPUT_SNAPSHOT_STATE_KEY: _build_output_snapshot(game_dir, loader_state),
        "message": "游戏已使用实体文件加载模组，禁止与 VFS 混用。",
    }
    _write_state_atomic(physical_mode_state_path(game_dir), state)
    return state


def check_physical_mode_cache(
    game_dir: Path,
    loader_version: str,
    mods: list[DiscoveredMod],
    active_language: str | None = None,
) -> PhysicalModeCacheCheck:
    """模组和实体产物均未变化时允许直接复用上次 apply。"""
    current_fingerprint = fingerprint_mods(mods)
    mode_state = _read_state(physical_mode_state_path(game_dir))
    if mode_state is None:
        return _cache_miss("缺少或无法读取物理模式状态", current_fingerprint)
    if mode_state.get("schema") != PHYSICAL_MODE_STATE_SCHEMA:
        return _cache_miss("物理模式状态版本不匹配", current_fingerprint)
    if mode_state.get("cache_schema") != PHYSICAL_MODE_CACHE_SCHEMA:
        return _cache_miss("旧物理模式状态尚未建立热缓存", current_fingerprint)
    if mode_state.get("status") != PHYSICAL_MODE_STATUS_ACTIVE:
        return _cache_miss("物理模式尚未处于 active 状态", current_fingerprint)
    if mode_state.get("loader_version") != loader_version:
        return _cache_miss("加载器版本已变化", current_fingerprint)
    if mode_state.get("mod_fingerprint") != current_fingerprint:
        return _cache_miss("模组内容或加载顺序已变化", current_fingerprint)
    if mode_state.get("active_language") != active_language:
        return _cache_miss("当前游戏语言已变化", current_fingerprint)
    saved_game_mtime = _normalize_mtime(mode_state.get(GAME_EXECUTABLE_MTIME_STATE_KEY))
    current_game_mtime = _game_executable_mtime(game_dir)
    if saved_game_mtime is None or current_game_mtime != saved_game_mtime:
        return _cache_miss("检测到游戏版本变化", current_fingerprint)

    loader_state = load_state(game_dir)
    if loader_state.get("last_fingerprint") != current_fingerprint:
        return _cache_miss("实体加载状态指纹不一致", current_fingerprint)

    saved_snapshot = mode_state.get(PHYSICAL_OUTPUT_SNAPSHOT_STATE_KEY)
    current_snapshot = _build_output_snapshot(game_dir, loader_state)
    if not isinstance(saved_snapshot, dict) or current_snapshot != saved_snapshot:
        return _cache_miss("实体输出文件已被修改", current_fingerprint)

    return PhysicalModeCacheCheck(
        cache_hit=True,
        reason="模组指纹与实体产物完整性校验通过",
        mod_fingerprint=current_fingerprint,
        overlay_dir=(
            mode_state.get("overlay_dir")
            if isinstance(mode_state.get("overlay_dir"), str)
            else None
        ),
    )


def refresh_physical_meta_backup_after_game_update(game_dir: Path) -> list[str]:
    """游戏更新替换实体 meta 后，只刷新确实发生变化的 vanilla 备份。"""
    mode_state = _read_state(physical_mode_state_path(game_dir))
    if mode_state is None or mode_state.get("status") != PHYSICAL_MODE_STATUS_ACTIVE:
        return []
    saved_game_mtime = _normalize_mtime(mode_state.get(GAME_EXECUTABLE_MTIME_STATE_KEY))
    current_game_mtime = _game_executable_mtime(game_dir)
    if saved_game_mtime is None or current_game_mtime == saved_game_mtime:
        return []
    saved_snapshot = mode_state.get(PHYSICAL_OUTPUT_SNAPSHOT_STATE_KEY)
    if not isinstance(saved_snapshot, dict):
        return []

    changed_meta: list[str] = []
    for rel_path in _meta_output_relative_paths():
        saved_file = saved_snapshot.get(rel_path)
        current_file = _file_snapshot(game_dir / Path(rel_path), include_hash=True)
        if isinstance(saved_file, dict) and current_file is not None and current_file != saved_file:
            changed_meta.append(rel_path)
    if not changed_meta:
        return []
    VanillaStore(game_dir).refresh_meta_backup(changed_meta)
    return changed_meta


def assert_vfs_mode_allowed(game_dir: Path) -> None:
    """实体模式标记存在时保守阻止所有 VFS 构建和启动。"""
    path = physical_mode_state_path(game_dir)
    if not path.exists():
        return
    state = _read_state(path)
    status = state.get("status") if state else "unknown"
    status_text = {
        PHYSICAL_MODE_STATUS_PENDING: "实体加载未完成或曾中途失败",
        PHYSICAL_MODE_STATUS_ACTIVE: "实体加载已生效",
    }.get(str(status), "实体模式状态文件异常")
    raise PhysicalModeConflictError(
        f"禁止启动 VFS：{status_text}。游戏实体 meta/overlay 可能已被修改，"
        "两种加载方式不能混用。请先使用实体加载器的 --revert 参数成功恢复，"
        "再启动 VFS；不要仅手工删除 physical_mode_state.json。"
    )


def clear_physical_mode_after_revert(game_dir: Path) -> None:
    """仅供完整 revert 成功后清除实体模式锁。"""
    try:
        physical_mode_state_path(game_dir).unlink()
    except FileNotFoundError:
        return


def _read_state(path: Path) -> dict[str, Any] | None:
    """容错读取模式状态；损坏文件仍由调用方按存在即阻止处理。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _write_state_atomic(path: Path, state: dict[str, Any]) -> None:
    """原子替换状态文件，避免断电留下可被误判为干净的半截 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary_path, path)


def _cache_miss(reason: str, mod_fingerprint: str) -> PhysicalModeCacheCheck:
    """生成统一的物理模式缓存未命中结果。"""
    return PhysicalModeCacheCheck(
        cache_hit=False,
        reason=reason,
        mod_fingerprint=mod_fingerprint,
    )


def _optional_numbered_dir(value: object) -> str | None:
    """读取安全的四位数字输出目录；None 表示本次没有对应输出。"""
    if value is None:
        return None
    if isinstance(value, str) and len(value) == 4 and value.isdigit():
        return value
    return None


def _archive_output_complete(path: Path) -> bool:
    """确认实体 PAZ/PAMT 输出仍完整存在。"""
    return (path / OVERLAY_PAZ_NAME).is_file() and (path / OVERLAY_PAMT_NAME).is_file()


def _build_output_snapshot(
    game_dir: Path,
    loader_state: dict[str, Any],
) -> dict[str, dict[str, int | str]] | None:
    """记录实体输出大小与修改时间，低成本识别 Steam 校验或外部覆盖。"""
    relative_paths = _physical_output_relative_paths(loader_state)
    if relative_paths is None:
        return None
    snapshot: dict[str, dict[str, int | str]] = {}
    meta_paths = set(_meta_output_relative_paths())
    for rel_path in relative_paths:
        file_state = _file_snapshot(
            game_dir / Path(rel_path),
            include_hash=rel_path in meta_paths,
        )
        if file_state is None:
            return None
        snapshot[rel_path] = file_state
    return snapshot


def _physical_output_relative_paths(loader_state: dict[str, Any]) -> list[str] | None:
    """从实体 state 安全提取全部应存在的输出文件。"""
    physical_files = loader_state.get("physical_output_files")
    if isinstance(physical_files, list) and physical_files:
        normalized: list[str] = []
        for item in physical_files:
            if not isinstance(item, str):
                return None
            path = Path(item)
            if path.is_absolute() or ".." in path.parts:
                return None
            normalized.append(item.replace("\\", "/"))
        return sorted(set(normalized))

    # 兼容 v9.2 早期单数字 overlay 状态，冷重建后自动迁移为 npp 分包字段。
    paths = list(_meta_output_relative_paths())
    raw_overlay_dir = loader_state.get("overlay_dir")
    overlay_dir = _optional_numbered_dir(raw_overlay_dir)
    if raw_overlay_dir is not None and overlay_dir is None:
        return None
    if overlay_dir is not None:
        paths.extend(_archive_relative_paths(overlay_dir))

    standalone_items = loader_state.get("standalone_dirs")
    if not isinstance(standalone_items, list):
        return None
    for item in standalone_items:
        if not isinstance(item, dict):
            return None
        assigned_dir = _optional_numbered_dir(item.get("assigned_dir"))
        if assigned_dir is None:
            return None
        paths.extend(_archive_relative_paths(assigned_dir))
    return sorted(set(paths))


def _meta_output_relative_paths() -> tuple[str, str]:
    """返回物理模式会维护的两个 meta 相对路径。"""
    return (
        f"{META_DIR_NAME}/{PAPGT_FILE_NAME}",
        f"{META_DIR_NAME}/{PATHC_FILE_NAME}",
    )


def _archive_relative_paths(directory: str) -> tuple[str, str]:
    """返回一个实体归档目录的 PAZ/PAMT 相对路径。"""
    return (
        f"{directory}/{OVERLAY_PAZ_NAME}",
        f"{directory}/{OVERLAY_PAMT_NAME}",
    )


def _file_snapshot(path: Path, *, include_hash: bool = False) -> dict[str, int | str] | None:
    """读取输出文件快照；meta 额外哈希，归档只读取元数据。"""
    try:
        stat = path.stat()
    except OSError:
        return None
    if not path.is_file():
        return None
    snapshot: dict[str, int | str] = {
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }
    if include_hash:
        try:
            snapshot["sha256"] = sha256(path.read_bytes()).hexdigest()
        except OSError:
            return None
    return snapshot


def _game_executable_mtime(game_dir: Path) -> int | None:
    """读取游戏主程序修改时间，失败时强制缓存失效。"""
    try:
        return (game_dir / GAME_EXECUTABLE_RELATIVE_PATH).stat().st_mtime_ns
    except OSError:
        return None


def _normalize_mtime(value: object) -> int | None:
    """只接受 JSON 中有效的整数修改时间。"""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _utc_now_text() -> str:
    """生成带时区的 UTC 时间文本。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
