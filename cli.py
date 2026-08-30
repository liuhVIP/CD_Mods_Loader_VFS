"""独立加载器控制台入口。

支持双击 exe 后进入交互菜单，也支持其他 Python 客户端通过命令行参数调用。
"""

from __future__ import annotations

import argparse
import ctypes
import json
import logging
import os
import sys
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from time import perf_counter
from typing import Any

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
    EMPTY_MOD_DIR_WARNING_PREFIX,
    MOD_TYPE_CDMOD,
    MOD_TYPE_DDS,
    MOD_TYPE_FORMAT3,
    MOD_TYPE_JSON_PATCH,
    MOD_TYPE_LOOSE_FILES,
    MOD_TYPE_META,
    MOD_TYPE_STANDALONE_ARCHIVE,
)
from cdmm.storage.state_store import load_state
from cdmm.utils.console_alert import (
    is_high_risk_mod_warning,
    is_json_version_mismatch_warning,
    is_standalone_conflict,
    print_high_risk_mod_warning,
    print_json_version_mismatch_warning,
    print_standalone_conflict,
)

# 中文语言标识，写入用户界面配置文件。
LANGUAGE_ZH = "zh-CN"

# 英文语言标识，写入用户界面配置文件。
LANGUAGE_EN = "en-US"

# 默认用户界面语言，读取配置失败或命令行静默运行时使用。
DEFAULT_LANGUAGE = LANGUAGE_ZH

# 默认配置文件名，命令行没有传 --game-dir 时读取。
DEFAULT_CONFIG_REL_PATH = Path("config") / "game_config.json"

# 用户界面配置文件名，保存控制台显示语言，位于程序所在目录。
UI_CONFIG_FILE_NAME = "cdloader_config.json"

# 版本文件名，标题和命令行说明统一从这里读取版本号。
VERSION_FILE_NAME = "version.txt"

# 版本文件缺失或读取失败时使用的兜底版本号。
DEFAULT_APP_VERSION = "v1.0"

# 用户界面配置 schema 版本，后续结构变化时用于兼容迁移。
UI_CONFIG_SCHEMA = 1

# pyc 发布包启动脚本写入的启动目录环境变量，用于恢复“程序所在目录”语义。
LAUNCH_DIR_ENV_NAME = "CDLOADER_LAUNCH_DIR"

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

# 只扫描模式下最多直接展示的空目录数量，避免异常目录过多刷屏。
MAX_VISIBLE_EMPTY_MOD_DIRS = 50

# 应用版本缓存，避免菜单循环中重复读取 version.txt。
_APP_VERSION_CACHE: str | None = None

# 控制台双语文案，所有用户可见文本尽量集中在这里，避免散落硬编码。
UI_TEXTS = {
    LANGUAGE_ZH: {
        "app_title": "红色沙漠独立轻量模组加载器 {app_version}",
        "app_subtitle": "B站 UP「改名开发」制作 | 支持游戏 1.13 以下版本",
        "arg_description": "红色沙漠独立轻量模组加载器 {app_version}（支持中文 / English）",
        "arg_command_help": "apply=加载模组，scan=只扫描，revert=恢复上次写入",
        "arg_game_dir_help": "游戏根目录；打包 exe 无参数时必须放在游戏根目录",
        "feature_lines": (
            "体积小、轻量化：专注独立加载，不依赖完整 GUI 管理器。",
            "多线程 + 目标索引缓存：减少重复解析，提高模组扫描与构建速度。",
            "分钟级加载体验：大型模组组合也尽量压缩到可等待的加载时间。",
            "支持中文 / English：首次打开选择语言，之后会自动记住。",
        ),
        "language_prompt_title": "请选择界面语言 / Please select language:",
        "language_prompt_zh": "1. 简体中文",
        "language_prompt_en": "2. English",
        "language_prompt_input": "请输入编号，直接回车默认中文",
        "language_invalid": "无效选择，请输入 1 或 2。",
        "select_action": "请选择要执行的操作：",
        "menu_apply": "1. 开始加载模组",
        "menu_scan": "2. 只扫描 mods，不写入游戏文件",
        "menu_exit": "3. 退出",
        "choice_prompt": "请输入编号，直接回车默认执行 1：",
        "exit_done": "已退出。",
        "invalid_choice": "无效选择，请重新输入。",
        "not_game_root": "未识别到游戏根目录，请把本程序放到红色沙漠游戏根目录后再运行。",
        "game_root_need": "游戏根目录需要包含：bin64\\CrimsonDesert.exe",
        "game_root_example": "示例：G:\\SteamLibrary\\steamapps\\common\\Crimson Desert",
        "using_local_game_dir": "已使用程序所在游戏根目录：{game_dir}",
        "configured_game_dir": "已从配置读取游戏根目录：{game_dir}",
        "config_read_failed": "配置文件读取失败，将改为手动输入：{error}",
        "enter_game_dir": "请输入 Crimson Desert 游戏根目录：",
        "empty_game_dir": "未输入游戏目录，已取消。",
        "game_dir": "游戏目录：{game_dir}",
        "mods_dir": "mods 目录：{mods_dir}",
        "failed": "失败：{error}",
        "error": "错误：{error}",
        "scan_done": "扫描完成：发现 {count} 个可识别模组",
        "empty_mod_dirs_title": "空目录（不参与加载）",
        "empty_mod_dirs_more": "还有 {count} 个空目录，详见日志。",
        "revert_done": "恢复完成",
        "load_done_overlay": "加载完成：overlay 已写入 {overlay_dir}",
        "load_done_no_overlay": "加载完成：未生成 overlay",
        "elapsed": "完成时间：{seconds:.2f}s",
        "pause_menu": "按 Enter 返回菜单",
        "pause_exit": "按 Enter 退出",
        "apply_desc": "真实加载 mods",
        "progress_unit": "阶段",
        "scan_group_titles": {
            "json": "JSON 补丁",
            "loose": "松散文件",
            "standalone_meta": "独立文件 + meta",
            "standalone": "独立文件",
            "format3": "Format 3 语义补丁",
            "other": "其他",
        },
        "mod_type_labels": {
            MOD_TYPE_JSON_PATCH: "JSON 补丁",
            MOD_TYPE_FORMAT3: "Format 3 语义补丁",
            MOD_TYPE_CDMOD: "CDMOD语义模组",
            MOD_TYPE_LOOSE_FILES: "松散文件",
            MOD_TYPE_DDS: "DDS 纹理",
            MOD_TYPE_STANDALONE_ARCHIVE: "独立文件",
            MOD_TYPE_META: "meta",
        },
        "progress_phase_labels": {},
    },
    LANGUAGE_EN: {
        "app_title": "Crimson Desert Lightweight Mod Loader {app_version}",
        "app_subtitle": "Made by Bilibili creator GaiMingDev | Supports game version 1.04.02+",
        "arg_description": "Crimson Desert Lightweight Mod Loader {app_version} (Chinese / English)",
        "arg_command_help": "apply=load mods, scan=scan only, revert=restore last write",
        "arg_game_dir_help": "Game root directory; packaged exe without arguments must be placed in the game root",
        "feature_lines": (
            "Small and lightweight: focused standalone loading without the full GUI manager.",
            "Multithreading + target index cache: less repeated parsing and faster mod builds.",
            "Minute-level loading: large mod sets are kept within a practical wait time where possible.",
            "Chinese / English support: choose once on first launch, then it is remembered.",
        ),
        "language_prompt_title": "请选择界面语言 / Please select language:",
        "language_prompt_zh": "1. 简体中文",
        "language_prompt_en": "2. English",
        "language_prompt_input": "Enter a number, press Enter for Chinese",
        "language_invalid": "Invalid choice. Please enter 1 or 2.",
        "select_action": "Choose an action:",
        "menu_apply": "1. Load mods",
        "menu_scan": "2. Scan mods only, do not write game files",
        "menu_exit": "3. Exit",
        "choice_prompt": "Enter a number, press Enter to run 1 by default: ",
        "exit_done": "Exited.",
        "invalid_choice": "Invalid choice, please try again.",
        "not_game_root": "Game root was not detected. Please place this program in the Crimson Desert game root and run it again.",
        "game_root_need": "The game root must contain: bin64\\CrimsonDesert.exe",
        "game_root_example": "Example: G:\\SteamLibrary\\steamapps\\common\\Crimson Desert",
        "using_local_game_dir": "Using the game root where the program is located: {game_dir}",
        "configured_game_dir": "Loaded game root from config: {game_dir}",
        "config_read_failed": "Failed to read config, switching to manual input: {error}",
        "enter_game_dir": "Enter the Crimson Desert game root: ",
        "empty_game_dir": "No game directory entered, cancelled.",
        "game_dir": "Game directory: {game_dir}",
        "mods_dir": "mods directory: {mods_dir}",
        "failed": "Failed: {error}",
        "error": "Error: {error}",
        "scan_done": "Scan complete: found {count} recognizable mods",
        "empty_mod_dirs_title": "Empty directories (not loaded)",
        "empty_mod_dirs_more": "{count} more empty directories; see the log.",
        "revert_done": "Restore complete",
        "load_done_overlay": "Load complete: overlay written to {overlay_dir}",
        "load_done_no_overlay": "Load complete: no overlay generated",
        "elapsed": "Elapsed: {seconds:.2f}s",
        "pause_menu": "Press Enter to return to the menu",
        "pause_exit": "Press Enter to exit",
        "apply_desc": "Loading mods",
        "progress_unit": "phase",
        "scan_group_titles": {
            "json": "JSON patches",
            "loose": "Loose files",
            "standalone_meta": "Standalone archives + meta",
            "standalone": "Standalone archives",
            "format3": "Format 3 semantic patches",
            "other": "Other",
        },
        "mod_type_labels": {
            MOD_TYPE_JSON_PATCH: "JSON patch",
            MOD_TYPE_FORMAT3: "Format 3 semantic patch",
            MOD_TYPE_CDMOD: "CDMOD semantic mod",
            MOD_TYPE_LOOSE_FILES: "Loose files",
            MOD_TYPE_DDS: "DDS texture",
            MOD_TYPE_STANDALONE_ARCHIVE: "Standalone archive",
            MOD_TYPE_META: "meta",
        },
        "progress_phase_labels": {
            "初始化加载环境": "Initialize",
            "扫描 mods": "Scan mods",
            "准备 meta 读取": "Prepare meta",
            "构建 loose overlay 输入": "Build loose overlay input",
            "构建 JSON overlay 输入": "Build JSON overlay input",
            "构建 Format 3 overlay 输入": "Build Format 3 overlay input",
            "收集 standalone 归档": "Collect standalone archives",
            "构建 overlay PAZ/PAMT": "Build overlay PAZ/PAMT",
            "构建 PATHC": "Build PATHC",
            "构建 PAPGT": "Build PAPGT",
            "事务写入游戏目录": "Commit transaction",
            "保存加载状态": "Save state",
        },
    },
}


def main(argv: list[str] | None = None) -> int:
    """执行 cdloader 控制台入口。"""
    configure_console_encoding()
    set_console_title(text("app_title"))
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    language = read_ui_language(ui_config_path()) or DEFAULT_LANGUAGE
    if not raw_argv:
        language = ensure_ui_language()
        set_console_title(text("app_title", language))
        if not ensure_packaged_exe_game_root(language):
            return 1
        return run_interactive_menu(language)

    parser = build_parser(language)
    args = parser.parse_args(raw_argv)
    return run_command(args.command, args.game_dir, language)


def build_parser(language: str = DEFAULT_LANGUAGE) -> argparse.ArgumentParser:
    """创建命令行参数解析器。"""
    parser = argparse.ArgumentParser(prog="cdloader", description=text("arg_description", language))
    parser.add_argument(
        "command",
        choices=(COMMAND_APPLY, COMMAND_SCAN, COMMAND_REVERT),
        help=text("arg_command_help", language),
    )
    parser.add_argument("--game-dir", type=Path, default=None, help=text("arg_game_dir_help", language))
    return parser


def run_interactive_menu(language: str) -> int:
    """显示双击运行时的控制台菜单。"""
    exit_code = 0
    while True:
        print_app_header(language)
        print(text("select_action", language))
        print(text("menu_apply", language))
        print(text("menu_scan", language))
        print(text("menu_exit", language))
        choice = input(text("choice_prompt", language)).strip() or "1"
        command = MENU_ITEMS.get(choice)
        if command == "exit":
            print(text("exit_done", language))
            return exit_code
        if command is None:
            print(text("invalid_choice", language))
            continue
        exit_code = run_command(command, None, language, pause_on_exit=False)
        input(text("pause_menu", language))


def ensure_packaged_exe_game_root(language: str) -> bool:
    """打包 exe 无参数启动时，先确认程序是否位于游戏根目录。"""
    if not is_frozen_app():
        return True
    local_game_dir = executable_dir()
    if looks_like_game_dir(local_game_dir):
        return True
    print_app_header(language)
    print(text("not_game_root", language))
    print(text("game_root_need", language))
    print(text("game_root_example", language))
    pause_before_exit(language)
    return False


def run_command(
    command: str,
    game_dir_arg: Path | None,
    language: str = DEFAULT_LANGUAGE,
    *,
    pause_on_exit: bool = False,
) -> int:
    """执行单个加载器命令。"""
    game_dir = resolve_game_dir(game_dir_arg, language)
    if game_dir is None:
        return finish_console(1, pause_on_exit, language)

    log_path = command_log_path(game_dir, command)
    configure_logging(log_path)
    print(text("game_dir", language, game_dir=game_dir))
    print(text("mods_dir", language, mods_dir=game_dir / "mods"))
    started = perf_counter()
    result: LoaderResult | None = None
    try:
        if command == COMMAND_SCAN:
            result = scan_loader(game_dir)
        elif command == COMMAND_REVERT:
            result = revert_loader(game_dir)
        else:
            result = run_apply_command(game_dir, language)
    except Exception as exc:
        logging.exception("执行失败")
        print(text("failed", language, error=exc), file=sys.stderr)
        return finish_console(1, pause_on_exit, language)

    elapsed_seconds = perf_counter() - started
    write_result_log(command, result, elapsed_seconds, log_path)
    exit_code = print_result(command, result, elapsed_seconds, language)
    return finish_console(exit_code, pause_on_exit, language)


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


def resolve_game_dir(game_dir_arg: Path | None, language: str) -> Path | None:
    """解析游戏目录，打包后只允许命令行参数或 exe 所在目录。"""
    if game_dir_arg is not None:
        return game_dir_arg.resolve()
    local_game_dir = executable_dir()
    if looks_like_game_dir(local_game_dir):
        print(text("using_local_game_dir", language, game_dir=local_game_dir))
        return local_game_dir
    if is_frozen_app():
        print(text("not_game_root", language))
        print(text("game_root_need", language))
        print(text("game_root_example", language))
        return None
    config_game_dir = read_configured_game_dir(default_config_path(), language)
    if config_game_dir is not None:
        return config_game_dir.resolve()
    entered = input(text("enter_game_dir", language)).strip().strip('"')
    if not entered:
        print(text("empty_game_dir", language))
        return None
    game_dir = Path(entered).resolve()
    if looks_like_game_dir(game_dir):
        save_configured_game_dir(default_config_path(), game_dir)
    return game_dir


def read_configured_game_dir(config_path: Path, language: str) -> Path | None:
    """从配置文件读取默认游戏目录。"""
    if not config_path.exists():
        return None
    try:
        config: Any = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(text("config_read_failed", language, error=exc))
        return None
    game_dir = config.get("game_dir") if isinstance(config, dict) else None
    if not isinstance(game_dir, str) or not game_dir.strip():
        return None
    print(text("configured_game_dir", language, game_dir=game_dir))
    return Path(game_dir.strip().strip('"'))


def save_configured_game_dir(config_path: Path, game_dir: Path) -> None:
    """保存用户手动输入的默认游戏目录。"""
    config = {
        "game_dir": str(game_dir),
    }
    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps(config, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        logging.debug("默认游戏目录配置写入失败：%s", config_path, exc_info=True)


def looks_like_game_dir(path: Path) -> bool:
    """判断目录是否像 Crimson Desert 游戏根目录。"""
    return (path / GAME_BIN_DIR_NAME / GAME_EXECUTABLE_NAME).exists()


def executable_dir() -> Path:
    """获取当前程序所在目录，兼容源码运行和 PyInstaller 单体 exe。"""
    if is_frozen_app():
        return Path(sys.executable).resolve().parent
    launch_dir = launch_script_dir()
    if launch_dir is not None:
        return launch_dir
    return Path(__file__).resolve().parent


def launch_script_dir() -> Path | None:
    """读取 pyc 发布脚本传入的启动目录。"""
    value = os.environ.get(LAUNCH_DIR_ENV_NAME, "").strip().strip('"')
    if not value:
        return None
    return Path(value).resolve()


def is_frozen_app() -> bool:
    """判断当前是否为 PyInstaller 打包后的 exe。"""
    return bool(getattr(sys, "frozen", False))


def default_config_path() -> Path:
    """获取开发阶段默认配置路径，打包后不再读取配置。"""
    launch_dir = launch_script_dir()
    if launch_dir is not None:
        return launch_dir / "cdmm" / DEFAULT_CONFIG_REL_PATH
    return Path(__file__).resolve().parent / DEFAULT_CONFIG_REL_PATH


def run_apply_command(game_dir: Path, language: str = DEFAULT_LANGUAGE) -> LoaderResult:
    """执行真实加载并显示轻量控制台进度。"""
    progress_bar = ConsoleProgress(
        total=len(APPLY_PROGRESS_PHASES),
        desc=text("apply_desc", language),
        unit=text("progress_unit", language),
    )
    completed_phases: set[str] = set()

    def update_progress(phase_name: str) -> None:
        if phase_name in completed_phases:
            return
        completed_phases.add(phase_name)
        progress_bar.update(progress_phase_label(phase_name, language))

    try:
        result = apply_loader(game_dir, progress_callback=update_progress)
        for phase_name in APPLY_PROGRESS_PHASES:
            update_progress(phase_name)
        return result
    finally:
        progress_bar.finish()


def print_result(command: str, result: LoaderResult, elapsed_seconds: float, language: str) -> int:
    """统一输出精简控制台结果。"""
    for warning in result.warnings:
        if is_high_risk_mod_warning(warning):
            print_high_risk_mod_warning(warning)
            continue
        if is_json_version_mismatch_warning(warning):
            print_json_version_mismatch_warning(warning)
            continue
        if is_standalone_conflict(warning):
            print_standalone_conflict(warning)
    for error in result.errors:
        print(text("error", language, error=error), file=sys.stderr)
    if result.errors:
        return 2

    if command == COMMAND_SCAN:
        print(text("scan_done", language, count=len(result.loaded_mods)))
        print_scan_groups(result.loaded_mods, language)
        print_empty_mod_dirs(result.warnings, language)
    elif command == COMMAND_REVERT:
        print(text("revert_done", language))
    else:
        if result.overlay_dir:
            print(text("load_done_overlay", language, overlay_dir=result.overlay_dir))
        else:
            print(text("load_done_no_overlay", language))
        print(text("elapsed", language, seconds=elapsed_seconds))
    return 0


def print_scan_groups(mods: Iterable[Any], language: str) -> None:
    """按类型分组输出扫描结果。"""
    grouped: dict[str, list[Any]] = defaultdict(list)
    for mod in mods:
        grouped[scan_group_key(mod.mod_type)].append(mod)

    for group_key in SCAN_GROUP_ORDER:
        group_mods = grouped.get(group_key)
        if not group_mods:
            continue
        print("")
        print(f"{scan_group_title(group_key, language)} ({len(group_mods)})")
        for mod in group_mods:
            print(f"- {mod.name} [{mod_type_label(mod.mod_type, language)}]")


def print_empty_mod_dirs(warnings: Iterable[str], language: str) -> None:
    """只扫描模式下，把空目录单独整理给用户看。"""
    empty_names = [
        warning.removeprefix(EMPTY_MOD_DIR_WARNING_PREFIX)
        for warning in warnings
        if warning.startswith(EMPTY_MOD_DIR_WARNING_PREFIX)
    ]
    if not empty_names:
        return
    visible_names = empty_names[:MAX_VISIBLE_EMPTY_MOD_DIRS]
    print("")
    print(f"{text('empty_mod_dirs_title', language)} ({len(empty_names)})")
    for name in visible_names:
        print(f"- {name}")
    hidden_count = len(empty_names) - len(visible_names)
    if hidden_count > 0:
        print(text("empty_mod_dirs_more", language, count=hidden_count))


def scan_group_key(mod_type: str) -> str:
    """返回扫描结果展示分组。"""
    parts = set(mod_type.split("+"))
    if mod_type == MOD_TYPE_JSON_PATCH:
        return "json"
    if mod_type in {MOD_TYPE_FORMAT3, MOD_TYPE_CDMOD}:
        return "format3"
    if MOD_TYPE_LOOSE_FILES in parts:
        return "loose"
    if MOD_TYPE_STANDALONE_ARCHIVE in parts and MOD_TYPE_META in parts:
        return "standalone_meta"
    if MOD_TYPE_STANDALONE_ARCHIVE in parts:
        return "standalone"
    return "other"


def mod_type_label(mod_type: str, language: str) -> str:
    """把内部 mod_type 转成当前语言展示。"""
    labels = [typed_text("mod_type_labels", part, language, part) for part in mod_type.split("+")]
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


def ensure_ui_language() -> str:
    """读取或初始化用户界面语言配置。"""
    config_path = ui_config_path()
    configured_language = read_ui_language(config_path)
    if configured_language:
        return configured_language
    language = prompt_ui_language()
    save_ui_language(config_path, language)
    return language


def read_ui_language(config_path: Path) -> str | None:
    """从程序目录下读取界面语言配置。"""
    if not config_path.exists():
        return None
    try:
        config: Any = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    language = config.get("language") if isinstance(config, dict) else None
    return normalize_language(language)


def save_ui_language(config_path: Path, language: str) -> None:
    """保存界面语言配置，失败时不影响加载器主体功能。"""
    config = {
        "schema": UI_CONFIG_SCHEMA,
        "language": language,
    }
    try:
        config_path.write_text(
            json.dumps(config, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        logging.debug("界面语言配置写入失败：%s", config_path, exc_info=True)


def prompt_ui_language() -> str:
    """首次打开时让用户选择中文或英文。"""
    print("")
    print(text("language_prompt_title", DEFAULT_LANGUAGE))
    print(text("language_prompt_zh", DEFAULT_LANGUAGE))
    print(text("language_prompt_en", DEFAULT_LANGUAGE))
    while True:
        choice = input(text("language_prompt_input", DEFAULT_LANGUAGE)).strip()
        if choice in {"", "1"}:
            return LANGUAGE_ZH
        if choice == "2":
            return LANGUAGE_EN
        print(text("language_invalid", DEFAULT_LANGUAGE))


def normalize_language(value: Any) -> str | None:
    """把配置中的语言值规整成受支持的语言标识。"""
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower().replace("_", "-")
    if normalized in {"zh", "zh-cn", "cn", "chinese", "简体中文", "中文"}:
        return LANGUAGE_ZH
    if normalized in {"en", "en-us", "english"}:
        return LANGUAGE_EN
    return None


def ui_config_path() -> Path:
    """获取界面配置文件路径，打包后位于 exe 所在目录，源码运行时位于项目目录。"""
    return executable_dir() / UI_CONFIG_FILE_NAME


def version_file_path() -> Path:
    """获取版本文件路径，打包后优先读取程序所在目录的 version.txt。"""
    return executable_dir() / VERSION_FILE_NAME


def bundled_resource_path(file_name: str) -> Path:
    """获取 PyInstaller/Nuitka 单文件运行时解包资源路径。"""
    bundle_dir = getattr(sys, "_MEIPASS", "")
    if bundle_dir:
        return Path(bundle_dir) / file_name
    return Path(__file__).resolve().parent / file_name


def app_version() -> str:
    """读取应用版本号，供标题和命令行描述统一使用。"""
    global _APP_VERSION_CACHE
    if _APP_VERSION_CACHE is not None:
        return _APP_VERSION_CACHE
    version = ""
    for version_path in (version_file_path(), bundled_resource_path(VERSION_FILE_NAME)):
        try:
            version = version_path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if version:
            break
    _APP_VERSION_CACHE = version or DEFAULT_APP_VERSION
    return _APP_VERSION_CACHE


def print_app_header(language: str) -> None:
    """输出更醒目的控制台标题和加载器优势说明。"""
    title = text("app_title", language)
    subtitle = text("app_subtitle", language)
    feature_lines = tuple(typed_text("feature_lines", "", language, ()))
    width = max(74, len(title), len(subtitle), *(len(line) for line in feature_lines)) + 6
    print("")
    print("=" * width)
    print(center_console_text(title, width))
    print(center_console_text(subtitle, width))
    print("-" * width)
    for line in feature_lines:
        print(f"  {line}")
    print("=" * width)


def center_console_text(value: str, width: int) -> str:
    """按控制台字符宽度居中显示，中文宽度按 2 个英文字符估算。"""
    display_length = sum(2 if ord(char) > 127 else 1 for char in value)
    left_padding = max(0, (width - display_length) // 2)
    return f"{' ' * left_padding}{value}"


def set_console_title(title: str) -> None:
    """设置 Windows 控制台窗口标题，失败时静默忽略。"""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.kernel32.SetConsoleTitleW(title)
    except Exception:
        return


def text(key: str, language: str = DEFAULT_LANGUAGE, **kwargs: Any) -> str:
    """读取当前语言文案并进行格式化。"""
    value = UI_TEXTS.get(language, UI_TEXTS[DEFAULT_LANGUAGE]).get(key)
    if value is None:
        value = UI_TEXTS[DEFAULT_LANGUAGE][key]
    if not isinstance(value, str):
        return str(value)
    format_kwargs = {"app_version": app_version(), **kwargs}
    return value.format(**format_kwargs)


def typed_text(key: str, nested_key: str, language: str, default: Any) -> Any:
    """读取当前语言的嵌套文案配置。"""
    bucket = UI_TEXTS.get(language, UI_TEXTS[DEFAULT_LANGUAGE]).get(key)
    if isinstance(bucket, dict):
        return bucket.get(nested_key, default)
    return bucket if bucket is not None else default


def scan_group_title(group_key: str, language: str) -> str:
    """返回扫描结果分组标题。"""
    return typed_text("scan_group_titles", group_key, language, group_key)


def progress_phase_label(phase_name: str, language: str) -> str:
    """返回进度阶段的当前语言名称。"""
    return typed_text("progress_phase_labels", phase_name, language, phase_name)


class ConsoleProgress:
    """单文件打包友好的轻量控制台进度条。"""

    def __init__(self, total: int, desc: str, unit: str) -> None:
        """初始化进度条状态。"""
        self.total = max(total, 1)
        self.desc = desc
        self.unit = unit
        self.current = 0
        self.render("")

    def update(self, phase_name: str) -> None:
        """推进一个阶段并刷新同一行显示。"""
        self.current = min(self.current + 1, self.total)
        self.render(phase_name)

    def finish(self) -> None:
        """结束进度条输出，避免后续结果覆盖当前行。"""
        print("")

    def render(self, phase_name: str) -> None:
        """渲染固定宽度文本进度条。"""
        bar_width = 28
        filled = int(bar_width * self.current / self.total)
        bar = "#" * filled + "-" * (bar_width - filled)
        percent = int(100 * self.current / self.total)
        suffix = f" | {phase_name}" if phase_name else ""
        message = f"\r{self.desc}: [{bar}] {self.current}/{self.total} {self.unit} {percent:3d}%{suffix}"
        print(message[:120], end="", flush=True)


def command_log_path(game_dir: Path, command: str) -> Path:
    """返回本次运行日志路径，真实加载只保留冷/热两个覆盖文件。"""
    logs_dir = game_dir / WORK_DIR_NAME / LOGS_DIR_NAME
    if command == COMMAND_APPLY:
        state = load_state(game_dir)
        file_name = HOT_LOAD_LOG_FILE_NAME if state.get("last_fingerprint") else COLD_LOAD_LOG_FILE_NAME
    else:
        file_name = f"{command}.log"
    return logs_dir / file_name


def finish_console(exit_code: int, pause_on_exit: bool, language: str = DEFAULT_LANGUAGE) -> int:
    """双击交互模式结束时暂停窗口，命令行模式直接返回退出码。"""
    if pause_on_exit:
        pause_before_exit(language)
    return exit_code


def pause_before_exit(language: str = DEFAULT_LANGUAGE) -> None:
    """控制台双击场景退出前暂停，避免窗口一闪而过。"""
    try:
        input(text("pause_exit", language))
    except EOFError:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
