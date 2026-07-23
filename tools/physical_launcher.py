"""Crimson Desert 实体文件加载与游戏启动入口。"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path
from time import perf_counter

from cdmm.common.constants import (
    GAME_BIN_DIR_NAME,
    GAME_EXECUTABLE_NAME,
    LOGS_DIR_NAME,
    WORK_DIR_NAME,
)
from cdmm.services.loader import revert_loader
from cdmm.services.loader_mode_service import (
    activate_physical_mode,
    begin_physical_mode,
    check_physical_mode_cache,
    refresh_physical_meta_backup_after_game_update,
)
from cdmm.services.cdmod_localization_loader import detect_active_paloc_language
from cdmm.services.scanner import INVALID_CDMOD_WARNING_PREFIX, scan_mods
from cdmm.services.physical_materializer import apply_physical_packages
from cdmm.tools.vfs_launcher import (
    APP_VERSION,
    CREATE_NEW_PROCESS_GROUP_FLAG,
    DETACHED_PROCESS_FLAG,
    VfsBuildProgressPrinter,
    cleanup_owned_asi_runtime_files,
    ensure_no_running_target,
    executable_dir,
    resolve_steam_app_id,
)
from cdmm.utils.console_alert import (
    is_high_risk_mod_warning,
    is_standalone_conflict,
    print_high_risk_mod_warning,
    print_standalone_conflict,
)
from cdmm.utils.hash_utils import fingerprint_mods

# 实体加载器独立日志文件名。
PHYSICAL_LAUNCH_LOG_FILE_NAME = "physical_exe_launch.log"

# 用户可见实体加载器标题和文件名，版本统一来自 version.txt。
PHYSICAL_APP_TITLE = f"红色沙漠实体加载模组启动器 {APP_VERSION}"
PHYSICAL_APP_EXE_NAME = f"cdloader-Physical-{APP_VERSION}.exe"

# 直接启动非 Steam 游戏时与控制台解耦。
DETACHED_GAME_CREATION_FLAGS = DETACHED_PROCESS_FLAG | CREATE_NEW_PROCESS_GROUP_FLAG


def main(argv: list[str] | None = None) -> int:
    """实体写入模组后，以完全不加载 VFS runtime 的方式启动游戏。"""
    configure_console_encoding()
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    game_dir = resolve_physical_game_dir(args.game_dir)
    if game_dir is None:
        pause_before_exit()
        return 1
    configure_logging(game_dir / WORK_DIR_NAME / LOGS_DIR_NAME / PHYSICAL_LAUNCH_LOG_FILE_NAME)
    print_header()
    print(f"游戏目录：{game_dir}")
    started = perf_counter()
    try:
        ensure_game_ready(game_dir)
        ensure_no_running_target(game_dir, False)
        if args.revert:
            refreshed_meta = refresh_physical_meta_backup_after_game_update(game_dir)
            if refreshed_meta:
                refreshed_text = ", ".join(refreshed_meta)
                logging.info("revert 前已刷新游戏更新后的 vanilla meta：%s", refreshed_text)
                print(f"检测到游戏更新，恢复前已刷新原版 meta 备份：{refreshed_text}")
            result = revert_loader(game_dir)
            print_loader_messages(result.warnings, result.errors)
            if result.errors:
                pause_before_exit()
                return 2
            print("实体加载修改已安全恢复，VFS 模式锁已解除。")
            return 0

        cached_mods, cached_scan_warnings = scan_mods(game_dir)
        active_language = detect_active_paloc_language(game_dir)
        invalid_cdmods = [
            warning
            for warning in cached_scan_warnings
            if warning.startswith(INVALID_CDMOD_WARNING_PREFIX)
        ]
        cache_check = check_physical_mode_cache(
            game_dir,
            APP_VERSION,
            cached_mods,
            active_language,
        )
        if cache_check.cache_hit and not invalid_cdmods:
            print_loader_messages(cached_scan_warnings, [])
            logging.info("实体加载缓存命中：%s", cache_check.reason)
            cleanup_owned_asi_runtime_files(game_dir / GAME_BIN_DIR_NAME)
            launch_game_without_vfs(game_dir, args.steam_app_id)
            print("实体加载缓存命中：模组与实体文件未变化，已直接启动游戏。")
            print("注意：恢复成功前请勿再运行 VFS 启动器。")
            return 0
        cache_miss_reason = (
            "检测到无效 cdmod，禁止复用缓存"
            if invalid_cdmods
            else cache_check.reason
        )
        logging.info("实体加载缓存未命中：%s", cache_miss_reason)
        print(f"实体加载缓存未命中：{cache_miss_reason}，将重新构建。")

        refreshed_meta = refresh_physical_meta_backup_after_game_update(game_dir)
        if refreshed_meta:
            refreshed_text = ", ".join(refreshed_meta)
            logging.info("游戏更新后已刷新 vanilla meta 备份：%s", refreshed_text)
            print(f"检测到游戏更新，已刷新原版 meta 备份：{refreshed_text}")

        begin_physical_mode(game_dir, APP_VERSION)
        print("已锁定为实体加载模式；正在复用 VFS 的 npp 分类分包并写入实体文件。")
        progress = VfsBuildProgressPrinter(operation_name="Physical 实体加载")
        progress.start()
        try:
            result = apply_physical_packages(game_dir, progress_callback=progress.update)
        finally:
            progress.finish()
        print_loader_messages(result.warnings, result.errors)
        if result.errors:
            print("实体加载失败：pending 模式锁已保留，VFS 不会冒险启动。", file=sys.stderr)
            pause_before_exit()
            return 2
        activate_physical_mode(
            game_dir,
            APP_VERSION,
            overlay_dir=result.overlay_dir,
            mod_fingerprint=fingerprint_mods(result.loaded_mods),
            active_language=active_language,
        )
        cleanup_owned_asi_runtime_files(game_dir / GAME_BIN_DIR_NAME)
        launch_game_without_vfs(game_dir, args.steam_app_id)
        if result.overlay_dir:
            print(f"实体加载完成：已写入分包 {result.overlay_dir}，游戏已启动。")
        else:
            print("实体加载完成：本次没有生成 overlay，游戏已启动。")
        print("注意：恢复成功前请勿再运行 VFS 启动器。")
        return 0
    except Exception as exc:
        logging.exception("实体加载启动器执行失败")
        print(f"失败：{exc}", file=sys.stderr)
        print("若已开始实体加载，模式锁会保留以阻止 VFS 混用。", file=sys.stderr)
        pause_before_exit()
        return 1
    finally:
        logging.info("实体加载启动器耗时：%.2fs", perf_counter() - started)


def build_parser() -> argparse.ArgumentParser:
    """创建实体加载器参数。"""
    parser = argparse.ArgumentParser(description=PHYSICAL_APP_TITLE)
    parser.add_argument("--game-dir", "-GameDir", type=Path, default=None, help="红色沙漠游戏根目录")
    parser.add_argument("--steam-app-id", "-SteamAppId", default="", help="可选 Steam AppID")
    parser.add_argument("--revert", "-Revert", action="store_true", help="安全恢复实体加载修改并解除 VFS 锁")
    return parser


def resolve_physical_game_dir(game_dir_arg: Path | None) -> Path | None:
    """解析实体加载器所在游戏根目录，兼容 core 位于 cdloader 子目录。"""
    if game_dir_arg is not None:
        return game_dir_arg.resolve()
    core_dir = executable_dir()
    for candidate in (core_dir, core_dir.parent):
        if (candidate / GAME_BIN_DIR_NAME / GAME_EXECUTABLE_NAME).exists():
            return candidate
    print(f"未识别到游戏根目录，请把 {PHYSICAL_APP_EXE_NAME} 放到游戏根目录后运行。")
    return None


def ensure_game_ready(game_dir: Path) -> None:
    """确认游戏主程序存在。"""
    target_exe = game_dir / GAME_BIN_DIR_NAME / GAME_EXECUTABLE_NAME
    if not target_exe.exists():
        raise FileNotFoundError(f"未找到红色沙漠主程序：{target_exe}")


def launch_game_without_vfs(game_dir: Path, explicit_steam_app_id: str = "") -> list[str]:
    """不创建映射、不注入 runtime，只走 Steam URI 或游戏原生 EXE。"""
    target_exe = game_dir / GAME_BIN_DIR_NAME / GAME_EXECUTABLE_NAME
    steam_app_id = explicit_steam_app_id.strip() or resolve_steam_app_id(target_exe)
    if steam_app_id:
        uri = f"steam://run/{steam_app_id}"
        logging.info("实体模式通过 Steam 启动：%s", uri)
        os.startfile(uri)
        return [uri]
    command = [str(target_exe)]
    logging.info("实体模式直接启动：%s", subprocess.list2cmdline(command))
    subprocess.Popen(
        command,
        cwd=target_exe.parent,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=DETACHED_GAME_CREATION_FLAGS,
    )
    return command


def print_loader_messages(warnings: list[str], errors: list[str]) -> None:
    """复用现有高风险红字和 standalone 冲突显示。"""
    for warning in warnings:
        logging.warning(warning)
        if is_high_risk_mod_warning(warning):
            print_high_risk_mod_warning(warning)
        elif is_standalone_conflict(warning):
            print_standalone_conflict(warning)
    for error in errors:
        logging.error(error)
        print(f"ERROR: {error}", file=sys.stderr)


def configure_console_encoding() -> None:
    """将控制台输出切换为 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def configure_logging(log_path: Path) -> None:
    """配置实体加载器独立覆盖日志。"""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
        handlers=[logging.FileHandler(log_path, mode="w", encoding="utf-8")],
        force=True,
    )


def print_header() -> None:
    """输出实体加载器醒目标识。"""
    print("")
    print("=" * 72)
    print(PHYSICAL_APP_TITLE.center(60))
    print("此模式会实际修改游戏 overlay/meta，不能与 VFS 混用。".center(48))
    print("=" * 72)


def pause_before_exit() -> None:
    """错误时保留窗口供用户读取。"""
    if sys.stdin is not None and sys.stdin.isatty():
        try:
            input("按回车键退出...")
        except (EOFError, KeyboardInterrupt):
            return
