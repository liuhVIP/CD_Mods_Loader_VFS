"""只读比较当前全部旧Format 3在新旧语义入口中的最终字节。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from cdmm.services.cdmod_semantic_loader import build_semantic_overlay_entries
from cdmm.services.format3_loader import build_format3_overlay_entries
from cdmm.services.scanner import MOD_TYPE_FORMAT3, scan_mods
from cdmm.storage.vanilla_store import VanillaStore


def main() -> int:
    """扫描当前Format 3并比较两个入口生成的entry SHA256。"""
    parser = argparse.ArgumentParser(description="验证统一语义入口与旧Format 3入口等价")
    parser.add_argument("--game-dir", required=True, type=Path, help="游戏根目录")
    parser.add_argument("--output", type=Path, default=None, help="可选的JSON报告输出路径")
    args = parser.parse_args()

    mods, _scan_warnings = scan_mods(args.game_dir)
    format3_mods = [mod for mod in mods if mod.mod_type == MOD_TYPE_FORMAT3]
    vanilla_store = VanillaStore(args.game_dir)
    old_warnings: list[str] = []
    old_errors: list[str] = []
    new_warnings: list[str] = []
    new_errors: list[str] = []
    old_entries = build_format3_overlay_entries(
        args.game_dir,
        format3_mods,
        vanilla_store,
        old_warnings,
        old_errors,
    )
    new_entries = build_semantic_overlay_entries(
        args.game_dir,
        format3_mods,
        vanilla_store,
        new_warnings,
        new_errors,
    )
    old_hashes = _hash_entries(old_entries)
    new_hashes = _hash_entries(new_entries)
    result = {
        "equivalent": not old_errors and not new_errors and old_hashes == new_hashes,
        "format3_mod_count": len(format3_mods),
        "old_entries": old_hashes,
        "new_entries": new_hashes,
        "old_warning_count": len(old_warnings),
        "new_warning_count": len(new_warnings),
        "old_warnings": old_warnings,
        "new_warnings": new_warnings,
        "old_errors": old_errors,
        "new_errors": new_errors,
    }
    output_text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output_text, encoding="utf-8")
    print(output_text, end="")
    return 0 if result["equivalent"] else 2


def _hash_entries(entries) -> dict[str, str]:
    """按最终entry路径生成明文字节哈希。"""
    return {
        entry.entry_path.lower(): hashlib.sha256(entry.content).hexdigest()
        for entry in entries
    }


if __name__ == "__main__":
    raise SystemExit(main())
