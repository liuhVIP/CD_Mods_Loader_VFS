"""把标准 ``files/NNNN`` loose 模组转换为 ``.cdmod``。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from cdmm.services.cdmod_loose_converter import (
    convert_numbered_loose_to_cdmod,
    loose_result_to_json,
)


def main() -> int:
    """解析命令行并执行通用 numbered loose 转换。"""
    parser = argparse.ArgumentParser(description="转换 files/NNNN loose 模组为 cdmod")
    parser.add_argument("source", type=Path, help="模组目录")
    parser.add_argument("output", type=Path, help="输出 .cdmod 文件")
    args = parser.parse_args()
    result = convert_numbered_loose_to_cdmod(args.source, args.output)
    print(json.dumps(loose_result_to_json(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
