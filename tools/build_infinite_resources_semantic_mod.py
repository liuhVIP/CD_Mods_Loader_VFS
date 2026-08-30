"""从失效的无限资源旧包提取功能意图，生成 2.00.01 语义 cdmod。"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR.parent) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR.parent))

from cdmm.services.cdmod_converter import _write_cdmod_zip  # noqa: E402

ITEM_LABEL = re.compile(r"ItemInfo (.+) \((\d+)\)$")


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-cdmod", type=Path, required=True)
    parser.add_argument("--stamina-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", default="2.00.01")
    return parser.parse_args()


def _read_legacy(path: Path) -> dict:
    with zipfile.ZipFile(path) as archive:
        return json.loads(archive.read("patches/legacy.json").decode("utf-8-sig"))


def _item_intents(document: dict, version: str) -> list[dict]:
    changes = [
        change
        for patch in document.get("patches", [])
        if patch.get("game_file") == "gamedata/iteminfo.pabgb"
        for change in patch.get("changes", [])
    ]
    grouped: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for change in changes:
        match = ITEM_LABEL.search(str(change.get("label", "")))
        if match is None:
            raise ValueError(f"ItemInfo change 缺少稳定身份：{change!r}")
        grouped[(match.group(1), int(match.group(2)))].append(change)

    intents: list[dict] = []
    for (entry, key), items in grouped.items():
        cooldown = [item for item in items if item.get("patched") in {"6400", "640000"}]
        durability = [item for item in items if item.get("patched") == "ffff"]
        blocked = [item for item in items if item.get("original") == "01" and item.get("patched") == "00"]
        unknown = [item for item in items if item not in cooldown + durability + blocked]
        if unknown:
            raise ValueError(f"无法分类 ItemInfo 变化：{entry}/{key}: {unknown!r}")
        if cooldown:
            if len(cooldown) != 3:
                raise ValueError(f"冷却字段不是三联：{entry}/{key}: {len(cooldown)}")
            for field in ("cooltime", "unk_post_cooltime_a", "unk_post_cooltime_b"):
                intents.append({"selector": {"string_key": entry, "key": key}, "path": field, "op": "set", "value": 100})
        if durability:
            if len(durability) != 1:
                raise ValueError(f"耐久字段重复：{entry}/{key}: {len(durability)}")
            intents.append({"selector": {"string_key": entry, "key": key}, "path": "max_endurance", "op": "set", "value": 65535})
        if blocked:
            if len(blocked) != 1:
                raise ValueError(f"is_blocked 字段重复：{entry}/{key}: {len(blocked)}")
            intents.append({"selector": {"string_key": entry, "key": key}, "path": "is_blocked", "op": "set", "value": 0})

    return intents


def main() -> None:
    args = _args()
    old = _read_legacy(args.old_cdmod)
    intents = _item_intents(old, args.version)
    stamina = json.loads(args.stamina_source.read_text(encoding="utf-8-sig"))
    patch = {
        "schema": 1,
        "targets": [{"file": "gamedata/iteminfo.pabgb", "operations": intents}],
    }
    manifest = {
        "format": "crimson-mod-package",
        "format_version": 1,
        "id": "quickknastyy.infinite-resources-2-00-01-semantic",
        "name": "Infinite Cooldown Durability Stamina Spirit 2.00.01 Semantic",
        "version": args.version,
        "author": "QuickkNastyy",
        "description": "2.00.01 semantic rebuild: 0.1 second cooldown, infinite durability, stamina and spirit.",
        "dependencies": [],
        "source": {"format": "semantic-replay", "legacy_reference": args.old_cdmod.name},
        "components": [
            {"type": "semantic-patch", "path": "patches/semantic.json"},
            {"type": "legacy-byte-patch", "path": "patches/stamina-spirit.json"},
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    _write_cdmod_zip(
        args.output,
        {
            "manifest.json": manifest,
            "patches/semantic.json": patch,
            "patches/stamina-spirit.json": stamina,
        },
    )
    print(f"生成完成：iteminfo intents={len(intents)}，输出={args.output}")


if __name__ == "__main__":
    main()
