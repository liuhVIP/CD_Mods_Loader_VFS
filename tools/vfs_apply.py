"""VFS 实验构建命令。

该入口只负责生成 .cdloader/vfs_active 与 mapping_tree.json，供 PowerShell
脚本再调用 vfsDmoe 启动游戏。
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from cdmm.services.vfs_loader import build_vfs_package_for_launch
from cdmm.services.missing_target_policy import DEFAULT_ALLOW_MISSING_TARGETS
from cdmm.utils.console_alert import (
    is_high_risk_mod_warning,
    is_json_version_mismatch_warning,
    is_standalone_conflict,
    print_high_risk_mod_warning,
    print_json_version_mismatch_warning,
    print_standalone_conflict,
)


def print_vfs_warning(warning: str) -> None:
    """按风险类型输出 VFS 构建告警，高风险资源复用 CMD 亮红色块。"""
    if is_high_risk_mod_warning(warning):
        print_high_risk_mod_warning(warning)
        return
    if is_json_version_mismatch_warning(warning):
        print_json_version_mismatch_warning(warning)
        return
    if is_standalone_conflict(warning):
        print_standalone_conflict(warning)
        return
    print(f"WARNING: {warning}")


def main() -> int:
    """执行 VFS 包构建并输出简要结果。"""
    parser = argparse.ArgumentParser(description="构建红色沙漠 VFS 实验加载包")
    parser.add_argument("--game-dir", required=True, help="红色沙漠游戏根目录")
    parser.add_argument(
        "--allow-missing-targets",
        action="store_true",
        help="兼容旧参数；现在所有用户入口默认都会跳过当前游戏已删除的目标",
    )
    parser.add_argument(
        "--strict-targets",
        action="store_true",
        help="严格处理缺失目标并阻止构建",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    result = build_vfs_package_for_launch(
        Path(args.game_dir),
        allow_missing_targets=(
            DEFAULT_ALLOW_MISSING_TARGETS
            if not args.strict_targets
            else False
        ),
    )
    for warning in result.warnings:
        print_vfs_warning(warning)
    for error in result.errors:
        print(f"ERROR: {error}")
    if result.errors:
        return 2

    if result.cache_hit:
        print(f"VFS 缓存命中：直接使用现有映射文件 {result.mapping_path}")
    else:
        print(f"VFS 构建完成：映射文件 {result.mapping_path}")
    print(f"VFS 输出目录：{result.vfs_root}")
    print(f"已映射文件数：{len(result.mapped_files)}")
    if result.overlay_dir:
        print(f"虚拟 overlay 目录：{result.overlay_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
