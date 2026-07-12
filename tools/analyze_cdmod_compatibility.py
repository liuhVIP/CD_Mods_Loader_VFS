"""分析多个 ``.cdmod`` 包之间的字段级兼容性。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from cdmm.services.cdmod_compatibility import (
    analyze_cdmod_compatibility,
    compatibility_report_to_json,
    write_compatibility_report,
)


def main() -> int:
    """读取命令行参数并输出兼容性报告。"""
    parser = argparse.ArgumentParser(description="分析 Crimson Mod Package 兼容性")
    parser.add_argument("packages", nargs="+", type=Path, help="按加载顺序排列的 .cdmod 文件")
    parser.add_argument("--output", type=Path, default=None, help="可选的 JSON 报告输出路径")
    parser.add_argument("--game-dir", type=Path, default=None, help="可选的游戏根目录，用于展开动态 match")
    args = parser.parse_args()

    report = analyze_cdmod_compatibility(args.packages, game_dir=args.game_dir)
    document = compatibility_report_to_json(report)
    if args.output is not None:
        write_compatibility_report(report, args.output)
    print(json.dumps(document, ensure_ascii=False, indent=2))
    has_unresolved_risk = bool(report.unresolved_dynamic_selectors)
    has_resolution_error = bool(report.resolution_errors)
    return 2 if report.conflict_count or report.missing_dependencies or has_unresolved_risk or has_resolution_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
