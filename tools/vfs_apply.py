"""VFS 实验构建命令。

该入口只负责生成 .cdloader/vfs_active 与 mapping_tree.json，供 PowerShell
脚本再调用 vfsDmoe 启动游戏。
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from cdmm.services.vfs_loader import build_vfs_package


def main() -> int:
    """执行 VFS 包构建并输出简要结果。"""
    parser = argparse.ArgumentParser(description="构建红色沙漠 VFS 实验加载包")
    parser.add_argument("--game-dir", required=True, help="红色沙漠游戏根目录")
    parser.add_argument(
        "--allow-missing-targets",
        action="store_true",
        help="实验模式：跳过当前 PAMT 中不存在的 JSON 目标",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    result = build_vfs_package(
        Path(args.game_dir),
        allow_missing_targets=args.allow_missing_targets,
    )
    for warning in result.warnings:
        print(f"WARNING: {warning}")
    for error in result.errors:
        print(f"ERROR: {error}")
    if result.errors:
        return 2

    print(f"VFS 构建完成：映射文件 {result.mapping_path}")
    print(f"VFS 输出目录：{result.vfs_root}")
    print(f"已映射文件数：{len(result.mapped_files)}")
    if result.overlay_dir:
        print(f"虚拟 overlay 目录：{result.overlay_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
