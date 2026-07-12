"""比较旧Format 3和新cdmod的最终overlay字节。"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from cdmm.services.cdmod_equivalence import verify_format3_cdmod_equivalence


def main() -> int:
    """执行真实游戏只读等价性验证。"""
    parser = argparse.ArgumentParser(description="验证Format 3与cdmod最终entry字节等价")
    parser.add_argument("original", type=Path, help="原Format 3 JSON")
    parser.add_argument("cdmod", type=Path, help="转换后的cdmod")
    parser.add_argument("--game-dir", required=True, type=Path, help="游戏根目录")
    args = parser.parse_args()

    result = verify_format3_cdmod_equivalence(args.original, args.cdmod, args.game_dir)
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return 0 if result.equivalent else 2


if __name__ == "__main__":
    raise SystemExit(main())
