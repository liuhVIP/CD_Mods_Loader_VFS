"""路径归一化工具。"""

from __future__ import annotations

from pathlib import Path


def game_rel_path(path: str) -> str:
    """把游戏内相对路径统一成 POSIX 风格。"""
    return path.replace("\\", "/")


def lower_game_rel_path(path: str) -> str:
    """把游戏内路径归一化为小写 POSIX 风格。"""
    return game_rel_path(path).lower()


def fs_rel_path(path: str) -> str:
    """把逻辑相对路径转换为当前平台文件系统路径片段。"""
    return path.replace("/", "\\")


def relative_or_abs(path: Path, base: Path) -> str:
    """优先返回 base 相对 POSIX 路径，失败时返回绝对/原始字符串。"""
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return str(path)
