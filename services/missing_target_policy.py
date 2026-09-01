"""统一处理游戏更新后已经不存在的模组资源目标。"""

from __future__ import annotations

# 普通、VFS 与 Physical 用户入口共享同一默认策略。严格诊断入口可显式传 False。
DEFAULT_ALLOW_MISSING_TARGETS = True

_MISSING_TARGET_MARKERS = (
    "未在任何 PAMT 中找到目标文件",
    "中未找到资源目标",
)


def is_missing_game_target_error(error: str) -> bool:
    """判断错误是否仅表示模组声明的目标已不在当前游戏 PAMT。"""
    return any(marker in error for marker in _MISSING_TARGET_MARKERS)


def apply_missing_target_policy(
    errors: list[str],
    warnings: list[str],
    *,
    allow_missing_targets: bool = DEFAULT_ALLOW_MISSING_TARGETS,
) -> list[str]:
    """按统一策略过滤缺失目标；返回仍需阻止加载的真正错误。"""
    if not allow_missing_targets:
        return errors
    remaining: list[str] = []
    for error in errors:
        if not is_missing_game_target_error(error):
            remaining.append(error)
            continue
        warnings.append(f"已跳过当前游戏版本不存在的目标：{error}")
    return remaining
