"""JSON 文件读写工具。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_json_object(path: Path, *, encoding: str = "utf-8-sig") -> dict[str, Any]:
    """读取 JSON 对象，要求顶层必须是 dict。"""
    with path.open("r", encoding=encoding) as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"JSON 顶层必须是对象：{path}")
    return data


def load_json_optional(path: Path, *, encoding: str = "utf-8-sig") -> Any | None:
    """容错读取 JSON，失败时返回 None。"""
    try:
        return json.loads(path.read_text(encoding=encoding))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def write_json_object(path: Path, data: dict[str, Any]) -> None:
    """按 UTF-8 写入 JSON 对象。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
