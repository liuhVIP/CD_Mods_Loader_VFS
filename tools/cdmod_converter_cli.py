"""Crimson Desert 模组作者独立 `.cdmod` 双语控制台转换器。

该入口只负责语言、交互、参数解析与报告展示，格式识别和转换始终复用
cdmm 核心服务，确保同一版本 Tag 下的转换器与加载器保持一致。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from cdmm.services.cdmod_bulk_converter import (
    BulkConversionItem,
    bulk_result_to_json,
    convert_mod_source_to_cdmod,
    convert_mods_directory_to_cdmod,
)

# 游戏根目录验证文件，避免作者误选 mods 或 bin64 目录。
GAME_EXE_RELATIVE_PATH = Path("bin64/CrimsonDesert.exe")
# 独立转换器默认输出目录名。
DEFAULT_OUTPUT_DIR_NAME = "cdmod-converted"
# 便携语言配置文件名，只保存用户选择的界面语言。
LANGUAGE_CONFIG_NAME = "cdmod-converter.config.json"
# 自动化测试和特殊部署可覆盖语言配置位置。
LANGUAGE_CONFIG_ENV = "CDMOD_CONVERTER_CONFIG"
SUPPORTED_LANGUAGES = ("zh-CN", "en-US")

_LANGUAGE = "zh-CN"
_TEXT = {
    "zh-CN": {
        "description": "Crimson Desert .cdmod 模组转换器",
        "game_dir_help": "Crimson Desert 游戏根目录",
        "input_help": "单个旧模组，或批量模式下的 mods 目录",
        "output_help": "输出目录",
        "batch_help": "批量转换输入目录下的所有模组",
        "workers_help": "批量转换并发数，默认 2",
        "pause_help": "结束前等待按键",
        "language_help": "界面语言：zh-CN 或 en-US",
        "failed": "转换失败：{error}",
        "batch_done": "批量转换完成：{output}",
        "report": "转换报告：{report}",
        "summary": "结果汇总：{summary}",
        "title": " Crimson Desert .cdmod 模组转换器",
        "matching": " 转换器与同版本 cdloader 配套使用",
        "single_menu": "1. 转换单个模组",
        "batch_menu": "2. 批量转换 mods 目录",
        "exit_menu": "3. 退出",
        "choice": "\n请选择 [1-3]：",
        "choice_error": "请输入 1、2 或 3",
        "game_dir": "游戏根目录",
        "mods_dir": "mods 目录",
        "mod_source": "旧模组文件或文件夹",
        "output_prompt": "输出目录（直接回车使用 {output}）：",
        "path_prompt": "请输入或拖入{label}：",
        "empty_path": "{label}不能为空",
        "missing_game_dir": "必须提供游戏根目录",
        "wrong_game_dir": "所选目录不是游戏根目录，未找到 {path}",
        "single_done": "\n转换完成",
        "type": "类型：{value}",
        "output": "输出：{value}",
        "detail": "说明：{value}",
        "pause": "\n按回车键退出...",
    },
    "en-US": {
        "description": "Crimson Desert .cdmod Converter",
        "game_dir_help": "Crimson Desert game directory",
        "input_help": "A legacy mod, or a mods directory in batch mode",
        "output_help": "Output directory",
        "batch_help": "Convert every supported mod in the input directory",
        "workers_help": "Batch worker count, default: 2",
        "pause_help": "Wait for Enter before exiting",
        "language_help": "Interface language: zh-CN or en-US",
        "failed": "Conversion failed: {error}",
        "batch_done": "Batch conversion completed: {output}",
        "report": "Conversion report: {report}",
        "summary": "Summary: {summary}",
        "title": " Crimson Desert .cdmod Converter",
        "matching": " Use the converter and cdloader from the same release",
        "single_menu": "1. Convert one mod",
        "batch_menu": "2. Convert a mods directory",
        "exit_menu": "3. Exit",
        "choice": "\nSelect [1-3]: ",
        "choice_error": "Enter 1, 2, or 3",
        "game_dir": "game directory",
        "mods_dir": "mods directory",
        "mod_source": "legacy mod file or folder",
        "output_prompt": "Output directory (press Enter for {output}): ",
        "path_prompt": "Enter or drag the {label} here: ",
        "empty_path": "The {label} cannot be empty",
        "missing_game_dir": "The game directory is required",
        "wrong_game_dir": "The selected directory is not the game root; missing {path}",
        "single_done": "\nConversion completed",
        "type": "Type: {value}",
        "output": "Output: {value}",
        "detail": "Details: {value}",
        "pause": "\nPress Enter to exit...",
    },
}


def main(argv: list[str] | None = None) -> int:
    """运行参数模式或双击交互模式。"""
    _configure_utf8_console()
    raw_args = list(sys.argv[1:] if argv is None else argv)
    explicit_language = _read_language_argument(raw_args)
    interactive = not _has_input_argument(raw_args)
    language = explicit_language or _load_language()
    if language is None:
        language = _choose_language() if interactive else "en-US"
    _set_language(language)
    if explicit_language is not None or _load_language() is None:
        _save_language(language)

    parser = _build_parser()
    args = parser.parse_args(raw_args)
    try:
        if args.input is not None:
            return _run_arguments(args)
        return _run_interactive(args)
    except (OSError, ValueError) as exc:
        print("\n" + _t("failed", error=exc), file=sys.stderr)
        if args.pause:
            _pause()
        return 1


def _build_parser() -> argparse.ArgumentParser:
    """按当前语言创建适合 CMD 自动化调用的参数解析器。"""
    parser = argparse.ArgumentParser(description=_t("description"))
    parser.add_argument("--game-dir", type=Path, help=_t("game_dir_help"))
    parser.add_argument("--input", type=Path, help=_t("input_help"))
    parser.add_argument("--output-dir", type=Path, help=_t("output_help"))
    parser.add_argument("--batch", action="store_true", help=_t("batch_help"))
    parser.add_argument("--workers", type=int, default=2, help=_t("workers_help"))
    parser.add_argument("--pause", action="store_true", help=_t("pause_help"))
    parser.add_argument("--language", choices=SUPPORTED_LANGUAGES, help=_t("language_help"))
    return parser


def _run_arguments(args: argparse.Namespace) -> int:
    """执行非交互参数模式。"""
    game_dir = _require_game_dir(args.game_dir)
    source = args.input.resolve()
    output_dir = (args.output_dir or _default_output_dir(source)).resolve()
    if args.batch:
        result = convert_mods_directory_to_cdmod(source, output_dir, workers=args.workers)
        _print_batch_result(result, output_dir)
    else:
        _print_single_result(convert_mod_source_to_cdmod(game_dir, source, output_dir))
    if args.pause:
        _pause()
    return 0


def _run_interactive(args: argparse.Namespace) -> int:
    """显示双击可用的当前语言控制台菜单。"""
    print("=" * 62)
    print(_t("title"))
    print(_t("matching"))
    print("=" * 62)
    print(_t("single_menu"))
    print(_t("batch_menu"))
    print(_t("exit_menu"))
    choice = input(_t("choice")).strip()
    if choice == "3":
        return 0
    if choice not in {"1", "2"}:
        raise ValueError(_t("choice_error"))

    game_dir = _require_game_dir(_read_path(_t("game_dir")))
    batch = choice == "2"
    source = _read_path(_t("mods_dir") if batch else _t("mod_source")).resolve()
    default_output = _default_output_dir(source)
    output_text = input(_t("output_prompt", output=default_output)).strip()
    output_dir = _clean_path_text(output_text).resolve() if output_text else default_output

    if batch:
        result = convert_mods_directory_to_cdmod(source, output_dir, workers=args.workers)
        _print_batch_result(result, output_dir)
    else:
        _print_single_result(convert_mod_source_to_cdmod(game_dir, source, output_dir))
    _pause()
    return 0


def _print_batch_result(result, output_dir: Path) -> None:
    """输出当前语言的批量结果摘要。"""
    summary = bulk_result_to_json(result)["summary"]
    print("\n" + _t("batch_done", output=output_dir))
    print(_t("report", report=result.report_path))
    print(_t("summary", summary=summary))


def _require_game_dir(value: Path | None) -> Path:
    """验证游戏根目录并返回规范路径。"""
    if value is None:
        raise ValueError(_t("missing_game_dir"))
    game_dir = value.resolve()
    if not (game_dir / GAME_EXE_RELATIVE_PATH).is_file():
        raise ValueError(_t("wrong_game_dir", path=GAME_EXE_RELATIVE_PATH))
    return game_dir


def _default_output_dir(source: Path) -> Path:
    """生成不会改写原模组的默认输出目录。"""
    return source.parent / DEFAULT_OUTPUT_DIR_NAME


def _read_path(label: str) -> Path:
    """读取支持拖入窗口和引号包裹的 Windows 路径。"""
    text = input(_t("path_prompt", label=label)).strip()
    if not text:
        raise ValueError(_t("empty_path", label=label))
    return _clean_path_text(text)


def _clean_path_text(value: str) -> Path:
    """清理由 CMD 拖放产生的外围引号。"""
    return Path(value.strip().strip('"'))


def _print_single_result(item: BulkConversionItem) -> None:
    """输出当前语言的单模组转换结果。"""
    if item.status not in {"converted", "partial"}:
        raise ValueError(f"{item.status}: {item.detail or item.source_type}")
    print(_t("single_done"))
    print(_t("type", value=item.source_type))
    print(_t("output", value=item.output))
    if item.detail:
        print(_t("detail", value=item.detail))


def _choose_language() -> str:
    """首次交互启动时以双语提示选择界面语言。"""
    print("Select language / 选择语言")
    print("1. 中文")
    print("2. English")
    while True:
        choice = input("[1-2]: ").strip()
        if choice == "1":
            return "zh-CN"
        if choice == "2":
            return "en-US"
        print("Please enter 1 or 2 / 请输入 1 或 2")


def _read_language_argument(args: list[str]) -> str | None:
    """在完整 argparse 构建前读取显式语言参数。"""
    for index, value in enumerate(args):
        if value == "--language" and index + 1 < len(args):
            language = args[index + 1]
            return language if language in SUPPORTED_LANGUAGES else None
        if value.startswith("--language="):
            language = value.partition("=")[2]
            return language if language in SUPPORTED_LANGUAGES else None
    return None


def _has_input_argument(args: list[str]) -> bool:
    """判断本次是否为不应阻塞的参数模式。"""
    return "--input" in args or any(value.startswith("--input=") for value in args)


def _language_config_path() -> Path:
    """优先返回 EXE 同目录的便携配置路径。"""
    override = os.environ.get(LANGUAGE_CONFIG_ENV)
    if override:
        return Path(override).resolve()
    executable = Path(sys.argv[0]).resolve()
    return executable.parent / LANGUAGE_CONFIG_NAME


def _load_language() -> str | None:
    """读取已保存语言；损坏配置按首次启动处理。"""
    path = _language_config_path()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    language = document.get("language") if isinstance(document, dict) else None
    return language if language in SUPPORTED_LANGUAGES else None


def _save_language(language: str) -> None:
    """保存便携语言配置，目录不可写时回退到 LocalAppData。"""
    document = json.dumps({"language": language}, ensure_ascii=False, indent=2) + "\n"
    primary = _language_config_path()
    candidates = [primary]
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data and not os.environ.get(LANGUAGE_CONFIG_ENV):
        candidates.append(Path(local_app_data) / "cdmod-converter" / LANGUAGE_CONFIG_NAME)
    for path in candidates:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(document, encoding="utf-8")
            return
        except OSError:
            continue


def _set_language(language: str) -> None:
    """设置当前进程语言。"""
    global _LANGUAGE
    _LANGUAGE = language if language in SUPPORTED_LANGUAGES else "en-US"


def _t(key: str, **values: object) -> str:
    """读取并格式化当前语言文本。"""
    return _TEXT[_LANGUAGE][key].format(**values)


def _configure_utf8_console() -> None:
    """统一 Windows 控制台和重定向输出编码。"""
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except (AttributeError, OSError):
            pass
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def _pause() -> None:
    """让双击启动的窗口保留最终结果。"""
    try:
        input(_t("pause"))
    except EOFError:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
