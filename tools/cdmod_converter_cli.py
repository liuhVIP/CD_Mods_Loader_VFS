"""Crimson Desert 模组作者独立 `.cdmod` 控制台转换器。

该入口只负责交互、参数解析与报告展示，格式识别和转换始终复用 cdmm
核心服务，确保同一版本 Tag 下的转换器与加载器保持一致。
"""

from __future__ import annotations

import argparse
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


def main(argv: list[str] | None = None) -> int:
    """运行参数模式或双击交互模式。"""
    _configure_utf8_console()
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.input is not None:
            return _run_arguments(args)
        return _run_interactive(args)
    except (OSError, ValueError) as exc:
        print(f"\n转换失败：{exc}", file=sys.stderr)
        if args.pause:
            _pause()
        return 1


def _build_parser() -> argparse.ArgumentParser:
    """创建适合 CMD 自动化调用的参数解析器。"""
    parser = argparse.ArgumentParser(description="Crimson Desert .cdmod 模组转换器")
    parser.add_argument("--game-dir", type=Path, help="Crimson Desert 游戏根目录")
    parser.add_argument("--input", type=Path, help="单个旧模组，或批量模式下的 mods 目录")
    parser.add_argument("--output-dir", type=Path, help="输出目录")
    parser.add_argument("--batch", action="store_true", help="批量转换输入目录下的所有模组")
    parser.add_argument("--workers", type=int, default=2, help="批量转换并发数，默认 2")
    parser.add_argument("--pause", action="store_true", help="结束前等待按键")
    return parser


def _run_arguments(args: argparse.Namespace) -> int:
    """执行非交互参数模式。"""
    game_dir = _require_game_dir(args.game_dir)
    source = args.input.resolve()
    output_dir = (args.output_dir or _default_output_dir(source, args.batch)).resolve()
    if args.batch:
        result = convert_mods_directory_to_cdmod(
            source,
            output_dir,
            workers=args.workers,
        )
        summary = bulk_result_to_json(result)["summary"]
        print(f"批量转换完成：{output_dir}")
        print(f"转换报告：{result.report_path}")
        print(f"结果汇总：{summary}")
    else:
        item = convert_mod_source_to_cdmod(game_dir, source, output_dir)
        _print_single_result(item)
    if args.pause:
        _pause()
    return 0


def _run_interactive(args: argparse.Namespace) -> int:
    """显示双击可用的中文控制台菜单。"""
    print("=" * 62)
    print(" Crimson Desert .cdmod 模组转换器")
    print(" 转换器与同版本 cdloader 配套使用")
    print("=" * 62)
    print("1. 转换单个模组")
    print("2. 批量转换 mods 目录")
    print("3. 退出")
    choice = input("\n请选择 [1-3]：").strip()
    if choice == "3":
        return 0
    if choice not in {"1", "2"}:
        raise ValueError("请输入 1、2 或 3")

    game_dir = _require_game_dir(_read_path("游戏根目录"))
    batch = choice == "2"
    prompt = "mods 目录" if batch else "旧模组文件或文件夹"
    source = _read_path(prompt).resolve()
    default_output = _default_output_dir(source, batch)
    output_text = input(f"输出目录（直接回车使用 {default_output}）：").strip()
    output_dir = _clean_path_text(output_text).resolve() if output_text else default_output

    if batch:
        result = convert_mods_directory_to_cdmod(source, output_dir, workers=args.workers)
        summary = bulk_result_to_json(result)["summary"]
        print(f"\n批量转换完成：{output_dir}")
        print(f"转换报告：{result.report_path}")
        print(f"结果汇总：{summary}")
    else:
        _print_single_result(convert_mod_source_to_cdmod(game_dir, source, output_dir))
    _pause()
    return 0


def _require_game_dir(value: Path | None) -> Path:
    """验证游戏根目录并返回规范路径。"""
    if value is None:
        raise ValueError("必须提供游戏根目录")
    game_dir = value.resolve()
    if not (game_dir / GAME_EXE_RELATIVE_PATH).is_file():
        raise ValueError(f"所选目录不是游戏根目录，未找到 {GAME_EXE_RELATIVE_PATH}")
    return game_dir


def _default_output_dir(source: Path, batch: bool) -> Path:
    """生成不会改写原模组的默认输出目录。"""
    return source.parent / DEFAULT_OUTPUT_DIR_NAME if batch else source.parent / DEFAULT_OUTPUT_DIR_NAME


def _read_path(label: str) -> Path:
    """读取支持拖入窗口和引号包裹的 Windows 路径。"""
    text = input(f"请输入或拖入{label}：").strip()
    if not text:
        raise ValueError(f"{label}不能为空")
    return _clean_path_text(text)


def _clean_path_text(value: str) -> Path:
    """清理由 CMD 拖放产生的外围引号。"""
    return Path(value.strip().strip('"'))


def _print_single_result(item: BulkConversionItem) -> None:
    """输出单模组转换结果。"""
    if item.status not in {"converted", "partial"}:
        raise ValueError(f"{item.status}：{item.detail or item.source_type}")
    print("\n转换完成")
    print(f"类型：{item.source_type}")
    print(f"输出：{item.output}")
    if item.detail:
        print(f"说明：{item.detail}")


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
        input("\n按回车键退出...")
    except EOFError:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
