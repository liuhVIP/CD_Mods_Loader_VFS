"""把 Male Glide Animation 转换为生成型 ``.cdmod``。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from cdmm.services.cdmod_male_glide_converter import (
    convert_male_glide_to_cdmod,
    male_glide_result_to_json,
)


def main() -> int:
    """解析命令行并执行转换。"""
    parser = argparse.ArgumentParser(description="转换 Male Glide Animation 为 cdmod")
    parser.add_argument("source", type=Path, help="Male Glide Animation 模组目录")
    parser.add_argument("output", type=Path, help="输出 .cdmod 文件")
    parser.add_argument("--game-dir", type=Path, required=True, help="Crimson Desert 游戏目录")
    args = parser.parse_args()
    result = convert_male_glide_to_cdmod(args.source, args.game_dir, args.output)
    print(json.dumps(male_glide_result_to_json(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
