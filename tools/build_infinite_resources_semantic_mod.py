"""从失效的无限资源旧包或当前版本重建源生成语义 cdmod。

两种输入方式：

- ``--source-json``：``rebuild_infinite_resources_mod.py`` 针对当前游戏表生成的
  完整重建源。它同时提供 ItemInfo 语义意图（冷却/耐久/封锁位）与 BuffInfo/Skill
  字节补丁（无限耐力/精神），是 2.02 起的推荐方式。
- ``--old-cdmod`` + ``--stamina-source``：旧包内嵌的 ``patches/legacy.json``
  提供 ItemInfo 标签，另配一份当前版本的 stamina/spirit 字节补丁源。

ItemInfo 一律走 ``semantic-patch``（按记录身份定位），BuffInfo/Skill 仍按当前
版本重建为 ``legacy-byte-patch``：前者跨版本稳定，后者必须每次随表重建。
"""

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
ITEMINFO_TARGET = "gamedata/iteminfo.pabgb"
STAMINA_SPIRIT_TARGETS = ("gamedata/buffinfo.pabgb", "gamedata/skill.pabgb")
MOD_ID_TEMPLATE = "quickknastyy.infinite-resources-{slug}-semantic"


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-cdmod", type=Path)
    parser.add_argument("--stamina-source", type=Path)
    parser.add_argument(
        "--source-json",
        type=Path,
        help="当前版本 rebuild_infinite_resources_mod.py 生成的完整重建源",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", default="2.02.00")
    return parser.parse_args()


def _read_legacy(path: Path) -> dict:
    with zipfile.ZipFile(path) as archive:
        return json.loads(archive.read("patches/legacy.json").decode("utf-8-sig"))


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _stamina_spirit_patches(document: dict) -> list[dict]:
    """从重建源里取出 BuffInfo/Skill 补丁，并拒绝混入其它表。"""
    kept: list[dict] = []
    for patch in document.get("patches", []):
        game_file = patch.get("game_file")
        if game_file == ITEMINFO_TARGET:
            continue
        if game_file not in STAMINA_SPIRIT_TARGETS:
            raise ValueError(f"重建源含 ItemInfo 之外的意外目标：{game_file}")
        kept.append(patch)
    if not kept:
        raise ValueError("重建源没有 BuffInfo/Skill 字节补丁")
    return kept


def _item_intents(document: dict, version: str) -> list[dict]:
    changes = [
        change
        for patch in document.get("patches", [])
        if patch.get("game_file") == ITEMINFO_TARGET
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


def _resolve_inputs(args: argparse.Namespace) -> tuple[dict, list[dict], str]:
    """按输入方式返回 (ItemInfo 意图来源文档, stamina/spirit 补丁, 来源标识)。"""
    if args.source_json is not None:
        if args.old_cdmod is not None or args.stamina_source is not None:
            raise ValueError("--source-json 不能与 --old-cdmod / --stamina-source 同时使用")
        document = _read_json(args.source_json)
        return document, _stamina_spirit_patches(document), args.source_json.name
    if args.old_cdmod is None or args.stamina_source is None:
        raise ValueError("需要 --source-json，或同时提供 --old-cdmod 与 --stamina-source")
    stamina = _read_json(args.stamina_source)
    return _read_legacy(args.old_cdmod), _stamina_spirit_patches(stamina), args.old_cdmod.name


def _build_manifest(version: str, source_reference: str) -> dict:
    """按目标版本生成清单，版本号只来自 ``--version``。"""
    return {
        "format": "crimson-mod-package",
        "format_version": 1,
        "id": MOD_ID_TEMPLATE.format(slug=version.replace(".", "-")),
        "name": f"Infinite Cooldown Durability Stamina Spirit {version} Semantic",
        "version": version,
        "author": "QuickkNastyy",
        "description": (
            f"{version} semantic rebuild: 0.1 second cooldown, infinite durability, "
            "stamina and spirit."
        ),
        "dependencies": [],
        "source": {"format": "semantic-replay", "legacy_reference": source_reference},
        "components": [
            {"type": "semantic-patch", "path": "patches/semantic.json"},
            {"type": "legacy-byte-patch", "path": "patches/stamina-spirit.json"},
        ],
    }


def main() -> None:
    args = _args()
    document, stamina_patches, source_reference = _resolve_inputs(args)
    intents = _item_intents(document, args.version)
    patch = {
        "schema": 1,
        "targets": [{"file": ITEMINFO_TARGET, "operations": intents}],
    }
    stamina = {
        "format": 2,
        "modinfo": {
            "title": (
                f"15 - Experimental 0.1 Second Cooldown + Infinite Durability + "
                f"Infinite Stamina + Infinite Spirit (Stamina Spirit Only, {args.version})"
            ),
            "author": "QuickkNastyy",
            "version": args.version,
            "description": (
                f"Crimson Desert v{args.version} JSON V2 byte patch pack. Rebuilt from "
                "the current game tables with record and field identity checks."
            ),
            "note": "Rebuilt for the current game version; use only one option at a time.",
        },
        "patches": stamina_patches,
    }
    manifest = _build_manifest(args.version, source_reference)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    _write_cdmod_zip(
        args.output,
        {
            "manifest.json": manifest,
            "patches/semantic.json": patch,
            "patches/stamina-spirit.json": stamina,
        },
    )
    counts = {entry["game_file"]: len(entry["changes"]) for entry in stamina_patches}
    print(
        f"生成完成：iteminfo intents={len(intents)}，"
        f"stamina/spirit={counts}，输出={args.output}"
    )


if __name__ == "__main__":
    main()
