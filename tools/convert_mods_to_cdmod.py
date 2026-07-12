"""批量转换整个 Crimson Desert mods 目录。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from cdmm.services.cdmod_bulk_converter import (
    DEFAULT_BULK_WORKERS,
    bulk_result_to_json,
    convert_mods_directory_to_cdmod,
)


def main() -> int:
    """解析命令行并执行批量转换。"""
    parser = argparse.ArgumentParser(description="批量转换 Crimson Desert mods 为 cdmod")
    parser.add_argument("mods_dir", type=Path, help="游戏 mods 目录")
    parser.add_argument("output_dir", type=Path, help="转换输出目录")
    parser.add_argument("--workers", type=int, default=DEFAULT_BULK_WORKERS, help="并发转换数")
    args = parser.parse_args()
    result = convert_mods_directory_to_cdmod(
        args.mods_dir,
        args.output_dir,
        workers=args.workers,
    )
    print(json.dumps(bulk_result_to_json(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
