"""独立加载器 state.json 读写。"""

from __future__ import annotations

from pathlib import Path

from cdmm.common.constants import STATE_FILE_NAME, STATE_SCHEMA, WORK_DIR_NAME
from cdmm.common.models import DiscoveredMod
from cdmm.utils.json_utils import load_json_optional, write_json_object
from cdmm.utils.path_utils import relative_or_abs


def state_path(game_dir: Path) -> Path:
    """返回 state.json 路径。"""
    return game_dir / WORK_DIR_NAME / STATE_FILE_NAME


def load_state(game_dir: Path) -> dict:
    """读取状态文件，不存在或损坏时返回空 schema。"""
    path = state_path(game_dir)
    if not path.exists():
        return {"schema": STATE_SCHEMA}
    data = load_json_optional(path, encoding="utf-8")
    if not isinstance(data, dict):
        return {"schema": STATE_SCHEMA}
    return data


def save_state(
    game_dir: Path,
    *,
    overlay_dir: str | None,
    last_fingerprint: str,
    loaded_mods: list[DiscoveredMod],
    standalone_dirs: list[dict[str, str]] | None = None,
    physical_output_files: list[str] | None = None,
    physical_output_dirs: list[str] | None = None,
) -> None:
    """写入最小状态，便于下次清理和诊断。"""
    path = state_path(game_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "schema": STATE_SCHEMA,
        "last_fingerprint": last_fingerprint,
        "overlay_dir": overlay_dir,
        "standalone_dirs": standalone_dirs or [],
        "physical_output_files": physical_output_files or [],
        "physical_output_dirs": physical_output_dirs or [],
        "loaded_mods": [
            {
                "name": mod.name,
                "path": relative_or_abs(mod.path, game_dir),
                "type": mod.mod_type,
                "fingerprint": mod.fingerprint,
            }
            for mod in loaded_mods
        ],
    }
    write_json_object(path, data)


def clear_state(game_dir: Path) -> None:
    """清空 overlay/fingerprint 状态但保留 schema。"""
    path = state_path(game_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_object(path, {"schema": STATE_SCHEMA})
