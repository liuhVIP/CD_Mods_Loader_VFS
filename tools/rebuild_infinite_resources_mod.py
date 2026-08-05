"""按当前游戏表重建无限耐久、冷却、体力和精神传统 JSON 模组。"""

from __future__ import annotations

import argparse
import bisect
import difflib
import json
import struct
import sys
from collections import defaultdict
from pathlib import Path

# 源码工具从项目外层启动时，显式加入 cdmm 所在父目录。
PROJECT_DIR = Path(__file__).resolve().parents[1]
PROJECT_PARENT = PROJECT_DIR.parent
if str(PROJECT_PARENT) not in sys.path:
    sys.path.insert(0, str(PROJECT_PARENT))

from cdmm.services.cdmod_bulk_converter import convert_mod_source_to_cdmod  # noqa: E402
from cdmm.services.json_loader import extract_plaintext  # noqa: E402
from cdmm.services.pab_table_service import (  # noqa: E402
    build_entry_bounds,
    parse_pabgh_index,
)
from cdmm.services.pamt_index_service import get_game_pamt_index  # noqa: E402

# 当前游戏中技能 ResourceStat 使用的体力、精神 stat hash。
STAMINA_STAT_HASH = 0x000F425A
SPIRIT_STAT_HASH = 0x000F425B
RESOURCE_STAT_TYPE = 3
RESOURCE_STRUCT_SIZE = 22
RESOURCE_VALUE_OFFSET = 6

# ResourceStat 后两个 lookup 是当前引擎中体力/精神消耗链的身份校验。
STAMINA_RESOURCE_LOOKUPS = (1_000_064, 1_000_037)
SPIRIT_RESOURCE_LOOKUPS = (1_000_063, 1_000_046)

# BuffInfo 测试梯度记录的固定结构：体力每级 -2000，精神每级 -200，共 50 级。
BUFF_LEVEL_COUNT = 50
STAMINA_BUFF_STEP = 2000
SPIRIT_BUFF_STEP = 200
BUFF_VALUE_OFFSET_AFTER_HASH = 4


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-dir", type=Path, required=True)
    parser.add_argument("--mod-json", type=Path, required=True)
    parser.add_argument("--old-iteminfo", type=Path, required=True)
    parser.add_argument("--game-version", default="1.16.04")
    parser.add_argument("--source-json", type=Path, required=True)
    parser.add_argument("--output-cdmod", type=Path, required=True)
    return parser.parse_args()


def _extract_tables(game_dir: Path, names: tuple[str, ...]) -> dict[str, bytes]:
    index = get_game_pamt_index(game_dir)
    result: dict[str, bytes] = {}
    for name in names:
        entry = index.find_best(f"gamedata/{name}")
        if entry is None:
            raise RuntimeError(f"当前游戏中未找到 {name}")
        result[name] = extract_plaintext(entry)[0]
    return result


def _changes_by_file(document: dict) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {}
    for patch in document.get("patches", []):
        result[patch["game_file"]] = list(patch.get("changes", []))
    return result


def _build_old_item_anchors(
    old_body: bytes,
    current_body: bytes,
    current_header: bytes,
) -> tuple[list[tuple[int, int, str, int, int]], list[int]]:
    key_size, offsets = parse_pabgh_index(current_header, "iteminfo")
    bounds = build_entry_bounds(current_body, key_size, offsets)
    anchors: list[tuple[int, int, str, int, int]] = []
    for key, (start, end, name, name_end) in bounds.items():
        header = current_body[start:name_end]
        old_start = old_body.find(header)
        if old_start < 0:
            continue
        if old_body.find(header, old_start + 1) >= 0:
            raise RuntimeError(f"旧 ItemInfo 中记录头不唯一：{name} / {key}")
        anchors.append((old_start, key, name, start, end))
    anchors.sort()
    return anchors, [item[0] for item in anchors]


def _map_item_changes(
    old_body: bytes,
    current_body: bytes,
    current_header: bytes,
    changes: list[dict],
    version: str,
) -> list[dict]:
    anchors, starts = _build_old_item_anchors(old_body, current_body, current_header)
    grouped: dict[int, list[tuple[dict, int]]] = defaultdict(list)
    anchor_by_key: dict[int, tuple[int, int, str, int, int]] = {}

    for change in changes:
        old_offset = int(change["offset"])
        anchor_index = bisect.bisect_right(starts, old_offset) - 1
        if anchor_index < 0:
            raise RuntimeError(f"ItemInfo 旧偏移无法归属记录：{old_offset}")
        anchor = anchors[anchor_index]
        old_end = anchors[anchor_index + 1][0] if anchor_index + 1 < len(anchors) else len(old_body)
        if old_offset >= old_end:
            raise RuntimeError(f"ItemInfo 旧偏移落在记录空洞：{old_offset}")
        grouped[anchor[1]].append((change, old_offset - anchor[0]))
        anchor_by_key[anchor[1]] = anchor

    rebuilt: list[dict] = []
    for key, items in grouped.items():
        anchor = anchor_by_key[key]
        anchor_index = bisect.bisect_left(starts, anchor[0])
        old_end = anchors[anchor_index + 1][0] if anchor_index + 1 < len(anchors) else len(old_body)
        old_record = old_body[anchor[0]:old_end]
        current_record = current_body[anchor[3]:anchor[4]]
        blocks = difflib.SequenceMatcher(
            None,
            old_record,
            current_record,
            autojunk=False,
        ).get_matching_blocks()

        for change, relative_offset in items:
            original = bytes.fromhex(change["original"])
            current_relative: int | None = None
            for block in blocks:
                if block.a <= relative_offset and relative_offset + len(original) <= block.a + block.size:
                    current_relative = block.b + relative_offset - block.a
                    break
            if current_relative is None:
                raise RuntimeError(
                    f"ItemInfo 字段无法映射：{anchor[2]} / {key} / +0x{relative_offset:X}"
                )
            current_offset = anchor[3] + current_relative
            actual = current_body[current_offset:current_offset + len(original)]
            if actual != original:
                raise RuntimeError(
                    f"ItemInfo 当前原始字节不匹配：{anchor[2]} / {key} / "
                    f"{actual.hex()} != {original.hex()}"
                )
            rebuilt.append(
                {
                    "type": "replace",
                    "offset": current_offset,
                    "original": original.hex(),
                    "patched": change["patched"].lower(),
                    "label": f"{version} ItemInfo {anchor[2]} ({key})",
                }
            )

    rebuilt.sort(key=lambda item: item["offset"])
    return rebuilt


def _build_buff_changes(body: bytes, version: str) -> list[dict]:
    changes: list[dict] = []
    specs = (
        ("Stamina", STAMINA_STAT_HASH, STAMINA_BUFF_STEP),
        ("Spirit", SPIRIT_STAT_HASH, SPIRIT_BUFF_STEP),
    )
    for label, stat_hash, step in specs:
        hash_bytes = struct.pack("<I", stat_hash)
        for level in range(1, BUFF_LEVEL_COUNT + 1):
            value = -(step * level)
            level_value = level + 1
            prefix = b"\x00\x03\x00\x00\x00\x00" + hash_bytes + struct.pack("<q", value)
            if level < BUFF_LEVEL_COUNT:
                pattern = prefix + struct.pack("<I", level_value) + b"\x00\x0e\x00\x00"
            else:
                # 第 50 级是独立的 Regen 汇总记录，value 后为 u32 0 + u8 level。
                pattern = prefix + struct.pack("<IB", 0, level)
            match = body.find(pattern)
            if match < 0 or body.find(pattern, match + 1) >= 0:
                raise RuntimeError(f"BuffInfo {label} level={level} 当前结构未唯一命中")
            offset = match + 6 + BUFF_VALUE_OFFSET_AFTER_HASH
            original = body[offset:offset + 8]
            changes.append(
                {
                    "type": "replace",
                    "offset": offset,
                    "original": original.hex(),
                    "patched": (b"\xff" * 8).hex(),
                    "label": f"{version} BuffInfo Infinite {label} level {level}",
                }
            )
    return sorted(changes, key=lambda item: item["offset"])


def _build_skill_changes(body: bytes, version: str) -> list[dict]:
    changes: list[dict] = []
    specs = (
        ("Stamina", STAMINA_STAT_HASH, STAMINA_RESOURCE_LOOKUPS),
        ("Spirit", SPIRIT_STAT_HASH, SPIRIT_RESOURCE_LOOKUPS),
    )
    for label, stat_hash, expected_lookups in specs:
        prefix = bytes((RESOURCE_STAT_TYPE,)) + struct.pack("<I", stat_hash)
        search_from = 0
        while True:
            start = body.find(prefix, search_from)
            if start < 0:
                break
            search_from = start + 1
            if start + RESOURCE_STRUCT_SIZE > len(body):
                continue
            value_offset = start + RESOURCE_VALUE_OFFSET
            value = struct.unpack_from("<q", body, value_offset)[0]
            lookups = struct.unpack_from("<II", body, start + 14)
            if lookups != expected_lookups:
                continue
            if value >= -1:
                continue
            original = body[value_offset:value_offset + 8]
            changes.append(
                {
                    "type": "replace",
                    "offset": value_offset,
                    "original": original.hex(),
                    "patched": (b"\xff" * 8).hex(),
                    "label": f"{version} Skill Infinite {label}",
                }
            )
    return sorted(changes, key=lambda item: item["offset"])


def _write_rebuilt_mod(args: argparse.Namespace) -> None:
    document = json.loads(args.mod_json.read_text(encoding="utf-8"))
    old_changes = _changes_by_file(document)
    tables = _extract_tables(
        args.game_dir,
        (
            "iteminfo.pabgb",
            "iteminfo.pabgh",
            "buffinfo.pabgb",
            "skill.pabgb",
        ),
    )

    item_changes = _map_item_changes(
        args.old_iteminfo.read_bytes(),
        tables["iteminfo.pabgb"],
        tables["iteminfo.pabgh"],
        old_changes["gamedata/iteminfo.pabgb"],
        args.game_version,
    )
    buff_changes = _build_buff_changes(tables["buffinfo.pabgb"], args.game_version)
    skill_changes = _build_skill_changes(tables["skill.pabgb"], args.game_version)

    document["modinfo"]["version"] = args.game_version
    document["modinfo"]["description"] = (
        f"Crimson Desert v{args.game_version} JSON V2 byte patch pack. "
        "Rebuilt from the current game tables with record and field identity checks."
    )
    document["modinfo"]["note"] = (
        "Rebuilt for the current game version; use only one option at a time."
    )
    document["patches"] = [
        {"game_file": "gamedata/iteminfo.pabgb", "changes": item_changes},
        {"game_file": "gamedata/buffinfo.pabgb", "changes": buff_changes},
        {"game_file": "gamedata/skill.pabgb", "changes": skill_changes},
    ]
    args.source_json.parent.mkdir(parents=True, exist_ok=True)
    args.source_json.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    args.output_cdmod.parent.mkdir(parents=True, exist_ok=True)
    conversion = convert_mod_source_to_cdmod(
        args.game_dir,
        args.source_json,
        args.output_cdmod.parent,
    )
    if conversion.status != "converted" or conversion.output is None:
        raise RuntimeError(f"cdmod 转换失败：{conversion.detail}")
    generated_cdmod = Path(conversion.output)
    if generated_cdmod.resolve() != args.output_cdmod.resolve():
        generated_cdmod.replace(args.output_cdmod)
    print(
        f"重建完成：iteminfo={len(item_changes)}, "
        f"buffinfo={len(buff_changes)}, skill={len(skill_changes)}, "
        f"source={args.source_json}, cdmod={args.output_cdmod}"
    )


def main() -> None:
    _write_rebuilt_mod(_parse_args())


if __name__ == "__main__":
    main()
