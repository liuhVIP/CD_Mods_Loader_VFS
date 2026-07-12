"""为一组 ``.cdmod`` 生成确定性合并计划。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from cdmm.services.cdmod_build_plan import (
    CDMOD_PLAN_VALID,
    build_plan_to_json,
    compile_cdmod_build_plan,
    write_cdmod_build_plan,
)


def main() -> int:
    """解析命令行并生成计划文件。"""
    parser = argparse.ArgumentParser(description="生成 Crimson Mod Package 确定性构建计划")
    parser.add_argument("packages", nargs="+", type=Path, help="按加载顺序排列的 .cdmod 文件")
    parser.add_argument("--game-dir", required=True, type=Path, help="游戏根目录")
    parser.add_argument("--output", required=True, type=Path, help="计划JSON输出路径")
    args = parser.parse_args()

    plan = compile_cdmod_build_plan(args.packages, game_dir=args.game_dir)
    write_cdmod_build_plan(plan, args.output)
    print(json.dumps(build_plan_to_json(plan)["summary"], ensure_ascii=False, indent=2))
    print(f"status={plan.status} plan_hash={plan.plan_hash}")
    return 0 if plan.status == CDMOD_PLAN_VALID else 2


if __name__ == "__main__":
    raise SystemExit(main())
