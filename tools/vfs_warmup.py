"""源码版 VFS 启动前的 Steam 冷启动预热入口。

本模块只负责命令行参数适配，预热状态、Steam 安装识别和游戏进程等待统一
复用 ``tools.vfs_launcher``，避免源码版与打包版形成两套行为。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from cdmm.common.constants import GAME_BIN_DIR_NAME, GAME_EXECUTABLE_NAME
from cdmm.tools.vfs_launcher import (
    ensure_steam_warmup_for_current_boot,
    resolve_steam_app_id,
)


def build_parser() -> argparse.ArgumentParser:
    """创建源码版预热命令行解析器。"""
    parser = argparse.ArgumentParser(description="检查并完成 Crimson Desert Steam 冷启动预热")
    parser.add_argument("--game-dir", required=True, type=Path, help="Crimson Desert 游戏根目录")
    parser.add_argument("--steam-app-id", default="", help="显式 Steam AppID；默认从 manifest 识别")
    return parser


def run_warmup(game_dir: Path, explicit_steam_app_id: str = "") -> bool:
    """在 Steam 安装上完成本次开机预热；非 Steam 安装返回 False。"""
    game_dir = game_dir.resolve()
    target_exe = game_dir / GAME_BIN_DIR_NAME / GAME_EXECUTABLE_NAME
    steam_app_id = explicit_steam_app_id.strip() or resolve_steam_app_id(target_exe)
    if not steam_app_id:
        print("未识别到匹配当前游戏目录的 Steam manifest，跳过 Steam 冷启动预热。")
        return False
    ensure_steam_warmup_for_current_boot(game_dir, steam_app_id)
    return True


def main() -> int:
    """执行命令行预热检查。"""
    args = build_parser().parse_args()
    run_warmup(args.game_dir, args.steam_app_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
