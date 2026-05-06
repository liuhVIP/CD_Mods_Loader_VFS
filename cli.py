"""独立加载器命令行入口。"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from cdmm.services.loader import apply_loader, revert_loader, scan_loader


def main(argv: list[str] | None = None) -> int:
    """执行 cdloader 命令行。"""
    parser = argparse.ArgumentParser(prog="cdloader", description="Crimson Desert 独立模组加载器")
    parser.add_argument("command", choices=("apply", "scan", "revert"), help="要执行的操作")
    parser.add_argument("--game-dir", type=Path, default=None, help="游戏根目录，默认当前目录")
    parser.add_argument("--verbose", action="store_true", help="输出调试日志")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    game_dir = (args.game_dir or Path.cwd()).resolve()
    try:
        if args.command == "scan":
            result = scan_loader(game_dir)
        elif args.command == "apply":
            result = apply_loader(game_dir)
        else:
            result = revert_loader(game_dir)
    except Exception as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 1

    for warning in result.warnings:
        print(f"警告：{warning}")
    for error in result.errors:
        print(f"错误：{error}", file=sys.stderr)
    if result.errors:
        return 2

    if args.command == "scan":
        print(f"扫描完成：发现 {len(result.loaded_mods)} 个可识别模组")
        for mod in result.loaded_mods:
            print(f"- {mod.name} [{mod.mod_type}]")
    elif args.command == "apply":
        if result.overlay_dir:
            print(f"加载完成：overlay 已写入 {result.overlay_dir}")
        else:
            print("加载完成：未生成 overlay")
    else:
        print("恢复完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
