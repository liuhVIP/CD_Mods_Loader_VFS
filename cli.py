"""独立加载器控制台入口。

支持双击 exe 后进入交互菜单，也支持其他 Python 客户端通过命令行参数调用。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from time import perf_counter
from typing import Any

from tqdm import tqdm

from cdmm.common.constants import (
    APPLY_PROGRESS_PHASES,
    COLD_LOAD_LOG_FILE_NAME,
    GAME_BIN_DIR_NAME,
    GAME_EXECUTABLE_NAME,
    HOT_LOAD_LOG_FILE_NAME,
    LOGS_DIR_NAME,
    WORK_DIR_NAME,
)
from cdmm.common.models import LoaderResult
from cdmm.services.loader import apply_loader, revert_loader, scan_loader
from cdmm.services.scanner import (
    MOD_TYPE_DDS,
    MOD_TYPE_FORMAT3,
    MOD_TYPE_JSON_PATCH,
    MOD_TYPE_LOOSE_FILES,
    MOD_TYPE_META,
    MOD_TYPE_STANDALONE_ARCHIVE,
)
from cdmm.storage.state_store import load_state

# 控制台标题，集中定义避免菜单和命令行描述不一致。
APP_TITLE = "红色沙漠独立轻量模组加载器(b站up改名开发)—版本v1.0"

# 默认配置文件名，命令行没有传 --game-dir 时读取。
DEFAULT_CONFIG_REL_PATH = Path("config") / "game_config.json"

# 命令行可执行动作，apply 是带 tqdm 的默认真实加载模式。
COMMAND_APPLY = "apply"
COMMAND_SCAN = "scan"
COMMAND_REVERT = "revert"

# 交互菜单项，双击 exe 且没有命令行参数时使用。
MENU_ITEMS = {
    "1": COMMAND_APPLY,
    "2": COMMAND_SCAN,
    "3": "exit",
}

# 扫描结果分组展示顺序，保持用户最关心的 JSON、松散文件、独立文件靠前。
SCAN_GROUP_ORDER = (
    "json",
    "loose",
    "standalone_meta",
    "standalone",
    "format3",
    "other",
)

# 扫描结果分组中文名称。
SCAN_GROUP_TITLES = {
    "json": "JSON 补丁",
    "loose": "松散文件",
    "standalone_meta": "独立文件 + meta",
    "standalone": "独立文件",
    "format3": "Format 3 语义补丁",
    "other": "其他",
}

# 组件类型中文名称。
MOD_TYPE_LABELS = {
    MOD_TYPE_JSON_PATCH: "JSON 补丁",
    MOD_TYPE_FORMAT3: "Format 3 语义补丁",
    MOD_TYPE_LOOSE_FILES: "松散文件",
    MOD_TYPE_DDS: "DDS 纹理",
    MOD_TYPE_STANDALONE_ARCHIVE: "独立文件",
    MOD_TYPE_META: "meta",
}


def main(argv: list[str] | None = None) -> int:
    """执行 cdloader 控制台入口。"""
    configure_console_encoding()
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if not raw_argv:
        if not ensure_packaged_exe_game_root():
            return 1
        return run_interactive_menu()

    parser = build_parser()
    args = parser.parse_args(raw_argv)
    return run_command(args.command, args.game_dir)


def build_parser() -> argparse.ArgumentParser:
    """创建命令行参数解析器。"""
    parser = argparse.ArgumentParser(prog="cdloader", description=APP_TITLE)
    parser.add_argument(
        "command",
        choices=(COMMAND_APPLY, COMMAND_SCAN, COMMAND_REVERT),
        help="apply=加载模组，scan=只扫描，revert=恢复上次写入",
    )
    parser.add_argument("--game-dir", type=Path, default=None, help="游戏根目录；打包 exe 无参数时必须放在游戏根目录")
    return parser


def run_interactive_menu() -> int:
    """显示双击运行时的控制台菜单。"""
    exit_code = 0
    while True:
        print("")
        print(APP_TITLE)
        print("请选择要执行的操作：")
        print("1. 开始加载模组")
        print("2. 只扫描 mods，不写入游戏文件")
        print("3. 退出")
        choice = input("请输入编号，直接回车默认执行 1：").strip() or "1"
        command = MENU_ITEMS.get(choice)
        if command == "exit":
            print("已退出。")
            return exit_code
        if command is None:
            print("无效选择，请重新输入。")
            continue
        exit_code = run_command(command, None, pause_on_exit=False)
        input("按 Enter 返回菜单")


def ensure_packaged_exe_game_root() -> bool:
    """打包 exe 无参数启动时，先确认程序是否位于游戏根目录。"""
    if not is_frozen_app():
        return True
    local_game_dir = executable_dir()
    if looks_like_game_dir(local_game_dir):
        return True
    print("")
    print(APP_TITLE)
    print("未识别到游戏根目录，请把本程序放到红色沙漠游戏根目录后再运行。")
    print("游戏根目录需要包含：bin64\\CrimsonDesert.exe")
    print("示例：G:\\SteamLibrary\\steamapps\\common\\Crimson Desert")
    pause_before_exit()
    return False


def run_command(
    command: str,
    game_dir_arg: Path | None,
    *,
    pause_on_exit: bool = False,
) -> int:
    """执行单个加载器命令。"""
    game_dir = resolve_game_dir(game_dir_arg)
    if game_dir is None:
        return finish_console(1, pause_on_exit)

    log_path = command_log_path(game_dir, command)
    configure_logging(log_path)
    print(f"游戏目录：{game_dir}")
    print(f"mods 目录：{game_dir / 'mods'}")
    started = perf_counter()
    result: LoaderResult | None = None
    try:
        if command == COMMAND_SCAN:
            result = scan_loader(game_dir)
        elif command == COMMAND_REVERT:
            result = revert_loader(game_dir)
        else:
            result = run_apply_command(game_dir)
    except Exception as exc:
        logging.exception("执行失败")
        print(f"失败：{exc}", file=sys.stderr)
        return finish_console(1, pause_on_exit)

    elapsed_seconds = perf_counter() - started
    write_result_log(command, result, elapsed_seconds, log_path)
    exit_code = print_result(command, result, elapsed_seconds)
    return finish_console(exit_code, pause_on_exit)


def configure_logging(log_path: Path) -> None:
    """配置文件日志，控制台不输出详细过程。"""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
        handlers=[logging.FileHandler(log_path, mode="w", encoding="utf-8")],
        force=True,
    )


def configure_console_encoding() -> None:
    """尽量使用 UTF-8 控制台输出，减少中文日志乱码。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def resolve_game_dir(game_dir_arg: Path | None) -> Path | None:
    """解析游戏目录，打包后只允许命令行参数或 exe 所在目录。"""
    if game_dir_arg is not None:
        return game_dir_arg.resolve()
    local_game_dir = executable_dir()
    if looks_like_game_dir(local_game_dir):
        print(f"已使用程序所在游戏根目录：{local_game_dir}")
        return local_game_dir
    if is_frozen_app():
        print("未识别到游戏根目录，请把本程序放到红色沙漠游戏根目录后再运行。")
        print("游戏根目录需要包含：bin64\\CrimsonDesert.exe")
        print("示例：G:\\SteamLibrary\\steamapps\\common\\Crimson Desert")
        return None
    config_game_dir = read_configured_game_dir(default_config_path())
    if config_game_dir is not None:
        return config_game_dir.resolve()
    entered = input("请输入 Crimson Desert 游戏根目录：").strip().strip('"')
    if not entered:
        print("未输入游戏目录，已取消。")
        return None
    return Path(entered).resolve()


def read_configured_game_dir(config_path: Path) -> Path | None:
    """从配置文件读取默认游戏目录。"""
    if not config_path.exists():
        return None
    try:
        config: Any = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"配置文件读取失败，将改为手动输入：{exc}")
        return None
    game_dir = config.get("game_dir") if isinstance(config, dict) else None
    if not isinstance(game_dir, str) or not game_dir.strip():
        return None
    print(f"已从配置读取游戏根目录：{game_dir}")
    return Path(game_dir.strip().strip('"'))


def looks_like_game_dir(path: Path) -> bool:
    """判断目录是否像 Crimson Desert 游戏根目录。"""
    return (path / GAME_BIN_DIR_NAME / GAME_EXECUTABLE_NAME).exists()


def executable_dir() -> Path:
    """获取当前程序所在目录，兼容源码运行和 PyInstaller 单体 exe。"""
    if is_frozen_app():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def is_frozen_app() -> bool:
    """判断当前是否为 PyInstaller 打包后的 exe。"""
    return bool(getattr(sys, "frozen", False))


def default_config_path() -> Path:
    """获取开发阶段默认配置路径，打包后不再读取配置。"""
    return Path(__file__).resolve().parent / DEFAULT_CONFIG_REL_PATH


def run_apply_command(game_dir: Path) -> LoaderResult:
    """执行真实加载并显示 tqdm 进度条。"""
    with tqdm(
        total=len(APPLY_PROGRESS_PHASES),
        desc="真实加载 mods",
        unit="阶段",
        dynamic_ncols=True,
        leave=True,
    ) as progress_bar:
        completed_phases: set[str] = set()

        def update_progress(phase_name: str) -> None:
            if phase_name in completed_phases:
                return
            completed_phases.add(phase_name)
            progress_bar.set_postfix_str(phase_name)
            progress_bar.update(1)

        result = apply_loader(game_dir, progress_callback=update_progress)
        for phase_name in APPLY_PROGRESS_PHASES:
            update_progress(phase_name)
        return result


def print_result(command: str, result: LoaderResult, elapsed_seconds: float) -> int:
    """统一输出精简控制台结果。"""
    for error in result.errors:
        print(f"错误：{error}", file=sys.stderr)
    if result.errors:
        return 2

    if command == COMMAND_SCAN:
        print(f"扫描完成：发现 {len(result.loaded_mods)} 个可识别模组")
        print_scan_groups(result.loaded_mods)
    elif command == COMMAND_REVERT:
        print("恢复完成")
    else:
        if result.overlay_dir:
            print(f"加载完成：overlay 已写入 {result.overlay_dir}")
        else:
            print("加载完成：未生成 overlay")
        print(f"完成时间：{elapsed_seconds:.2f}s")
    return 0


def print_scan_groups(mods: Iterable[Any]) -> None:
    """按类型分组输出扫描结果。"""
    grouped: dict[str, list[Any]] = defaultdict(list)
    for mod in mods:
        grouped[scan_group_key(mod.mod_type)].append(mod)

    for group_key in SCAN_GROUP_ORDER:
        group_mods = grouped.get(group_key)
        if not group_mods:
            continue
        print("")
        print(f"{SCAN_GROUP_TITLES[group_key]}（{len(group_mods)} 个）")
        for mod in group_mods:
            print(f"- {mod.name} [{mod_type_label(mod.mod_type)}]")


def scan_group_key(mod_type: str) -> str:
    """返回扫描结果展示分组。"""
    parts = set(mod_type.split("+"))
    if mod_type == MOD_TYPE_JSON_PATCH:
        return "json"
    if mod_type == MOD_TYPE_FORMAT3:
        return "format3"
    if MOD_TYPE_LOOSE_FILES in parts:
        return "loose"
    if MOD_TYPE_STANDALONE_ARCHIVE in parts and MOD_TYPE_META in parts:
        return "standalone_meta"
    if MOD_TYPE_STANDALONE_ARCHIVE in parts:
        return "standalone"
    return "other"


def mod_type_label(mod_type: str) -> str:
    """把内部 mod_type 转成中文展示。"""
    labels = [MOD_TYPE_LABELS.get(part, part) for part in mod_type.split("+")]
    return " + ".join(labels)


def write_result_log(command: str, result: LoaderResult, elapsed_seconds: float, log_path: Path) -> None:
    """把本次运行结果、警告和错误写入 .cdloader/logs。"""
    logging.info("命令：%s", command)
    logging.info("耗时：%.2fs", elapsed_seconds)
    logging.info("识别模组数量：%s", len(result.loaded_mods))
    if result.overlay_dir:
        logging.info("overlay 已写入：%s", result.overlay_dir)
    else:
        logging.info("未生成 overlay")
    for warning in result.warnings:
        logging.warning(warning)
    for error in result.errors:
        logging.error(error)
    logging.info("日志文件：%s", log_path)


def command_log_path(game_dir: Path, command: str) -> Path:
    """返回本次运行日志路径，真实加载只保留冷/热两个覆盖文件。"""
    logs_dir = game_dir / WORK_DIR_NAME / LOGS_DIR_NAME
    if command == COMMAND_APPLY:
        state = load_state(game_dir)
        file_name = HOT_LOAD_LOG_FILE_NAME if state.get("last_fingerprint") else COLD_LOAD_LOG_FILE_NAME
    else:
        file_name = f"{command}.log"
    return logs_dir / file_name


def finish_console(exit_code: int, pause_on_exit: bool) -> int:
    """双击交互模式结束时暂停窗口，命令行模式直接返回退出码。"""
    if pause_on_exit:
        pause_before_exit()
    return exit_code


def pause_before_exit() -> None:
    """控制台双击场景退出前暂停，避免窗口一闪而过。"""
    try:
        input("按 Enter 退出")
    except EOFError:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
