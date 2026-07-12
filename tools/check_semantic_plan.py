"""只读检查当前游戏目录全部Format 3/cdmod的统一构建计划。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from cdmm.services.cdmod_build_plan import build_plan_to_json, compile_cdmod_package_plan
from cdmm.services.cdmod_semantic_loader import _normalize_semantic_packages
from cdmm.services.scanner import MOD_TYPE_CDMOD, MOD_TYPE_FORMAT3, scan_mods


def main() -> int:
    """扫描真实加载顺序并输出计划摘要。"""
    parser = argparse.ArgumentParser(description="检查当前语义模组统一构建计划")
    parser.add_argument("--game-dir", required=True, type=Path, help="游戏根目录")
    args = parser.parse_args()

    mods, scan_warnings = scan_mods(args.game_dir)
    semantic_mods = [mod for mod in mods if mod.mod_type in {MOD_TYPE_FORMAT3, MOD_TYPE_CDMOD}]
    errors: list[str] = []
    packages = _normalize_semantic_packages(semantic_mods, errors)
    if errors:
        print(json.dumps({"status": "REJECTED", "errors": errors}, ensure_ascii=False, indent=2))
        return 2
    plan = compile_cdmod_package_plan(tuple(packages), game_dir=args.game_dir)
    document = build_plan_to_json(plan)
    result = {
        "status": plan.status,
        "semantic_mod_count": len(semantic_mods),
        "scan_warning_count": len(scan_warnings),
        "summary": document["summary"],
        "plan_hash": plan.plan_hash,
        "rejection_reasons": list(plan.rejection_reasons),
        "resolutions": list(plan.resolutions),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if plan.status == "VALID" else 2


if __name__ == "__main__":
    raise SystemExit(main())
