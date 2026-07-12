"""将 Crimson Desert Format 3 JSON 转换为 ``.cdmod`` 原型包。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from cdmm.services.cdmod_converter import convert_format3_to_cdmod, result_to_json


def main() -> int:
    """解析命令行并执行单文件转换。"""
    parser = argparse.ArgumentParser(description="将 Format 3 JSON 转换为 Crimson Mod Package")
    parser.add_argument("source", type=Path, help="Format 3 JSON 文件")
    parser.add_argument("output", type=Path, help="输出 .cdmod 文件")
    args = parser.parse_args()

    result = convert_format3_to_cdmod(args.source, args.output)
    print(json.dumps(result_to_json(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
