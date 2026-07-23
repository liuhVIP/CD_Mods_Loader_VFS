"""实体加载与 VFS 加载的互斥模式状态管理。"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cdmm.common.constants import WORK_DIR_NAME

# 实体加载模式状态文件；只允许由加载器创建和清理。
PHYSICAL_MODE_STATE_FILE_NAME = "physical_mode_state.json"

# apply 尚未安全完成时的保守锁定状态。
PHYSICAL_MODE_STATUS_PENDING = "pending"

# apply 已完成且游戏实体 meta/overlay 已被修改的状态。
PHYSICAL_MODE_STATUS_ACTIVE = "active"

# 模式状态结构版本。
PHYSICAL_MODE_STATE_SCHEMA = 1


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
) -> dict[str, Any]:
    """实体 apply 成功后把模式锁更新为 active。"""
    state = {
        "schema": PHYSICAL_MODE_STATE_SCHEMA,
        "status": PHYSICAL_MODE_STATUS_ACTIVE,
        "loader_version": loader_version,
        "activated_at": _utc_now_text(),
        "overlay_dir": overlay_dir,
        "mod_fingerprint": mod_fingerprint,
        "message": "游戏已使用实体文件加载模组，禁止与 VFS 混用。",
    }
    _write_state_atomic(physical_mode_state_path(game_dir), state)
    return state


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


def _utc_now_text() -> str:
    """生成带时区的 UTC 时间文本。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
