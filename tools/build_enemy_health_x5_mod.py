"""生成全难度敌人生命倍率模组。

该工具读取当前游戏原版 ``buffinfo.pabgb``，只修改普通敌人和 Boss 的
原生难度 Buff。最终输出传统 byte patch JSON，并封装为 ``.cdmod``，不会
直接写入游戏 PAZ/PAMT。
"""

from __future__ import annotations

import argparse
import json
import struct
from dataclasses import dataclass
from pathlib import Path

from cdmm.services.buffinfo_parser import (
    BuffPayloadCommon,
    BuffinfoEntry,
    parse_entry,
    parse_item_header,
    parse_payload_common,
)
from cdmm.services.cdmod_bulk_converter import convert_mod_source_to_cdmod
from cdmm.services.json_loader import extract_plaintext
from cdmm.services.pab_table_service import build_entry_bounds, parse_pabgh_index
from cdmm.services.pamt_index_service import get_game_pamt_index

# 原版普通敌人难度 Buff。
NORMAL_ENEMY_DIFFICULTY_BUFF_KEY = 1_000_276

# 原版 Boss 难度 Buff。
BOSS_DIFFICULTY_BUFF_KEY = 1_000_277

# BuffData tag 98：VaryStatMaxValueRateBuffData。
MAX_STAT_RATE_VARIANT_TAG = 98

# tag 98 的 f00=0 表示最大生命值；同 tag 的 f00=2 是 KnockOut。
MAX_HP_VARIANT_TYPE = 0

# 难度 Buff 的 leading_lookup：1=简单、2=普通、3=困难。
EASY_DIFFICULTY_LEVEL = 1
NORMAL_DIFFICULTY_LEVEL = 2
HARD_DIFFICULTY_LEVEL = 3

# 比例定点单位为百万分之一；最终倍率 N 需要增加 (N - 1) * 100%。
RATE_SCALE = 1_000_000

# Nexus 发布默认与当前游戏版本对齐。
DEFAULT_RELEASE_VERSION = "1.13.01"

# 游戏内目标表路径。
BUFFINFO_GAME_FILE = "gamedata/buffinfo.pabgb"


@dataclass(frozen=True)
class BuffItemSlice:
    """一条 BuffData item 在单条 BuffInfo 记录中的位置。"""

    start: int
    end: int
    leading_lookup: int
    common: BuffPayloadCommon


def _load_vanilla_table(game_dir: Path, file_name: str) -> bytes:
    """从低编号原版 PAMT 中读取并解压目标表。"""
    entry = get_game_pamt_index(game_dir).find_best(
        file_name,
        suffix=Path(file_name).suffix,
        require_unique_best=False,
    )
    if entry is None:
        raise ValueError(f"无法定位原版 {file_name}")
    return extract_plaintext(entry)[0]


def _collect_buff_items(entry_bytes: bytes, entry: BuffinfoEntry) -> list[BuffItemSlice]:
    """通过每条 item 的 asset_path CString 严格恢复 item 边界。"""
    starts: dict[int, tuple[int, BuffPayloadCommon]] = {}
    region_start = entry.buff_data_list_offset
    region_end = entry.min_level_offset
    for length_offset in range(region_start, region_end - 4):
        text_length = struct.unpack_from("<I", entry_bytes, length_offset)[0]
        if not 1 <= text_length <= 512:
            continue
        text_end = length_offset + 4 + text_length
        if text_end > region_end:
            continue
        try:
            text = entry_bytes[length_offset + 4 : text_end].decode("utf-8")
        except UnicodeDecodeError:
            continue
        item_start = length_offset - 40
        if item_start < region_start:
            continue
        try:
            item_header = parse_item_header(entry_bytes, item_start)
            common = parse_payload_common(entry_bytes, item_header.payload_offset)
        except ValueError:
            continue
        if (
            item_header.absent_flag != 0
            or item_header.prefix_id
            not in {
                EASY_DIFFICULTY_LEVEL,
                NORMAL_DIFFICULTY_LEVEL,
                HARD_DIFFICULTY_LEVEL,
            }
            or common.asset_path != text
        ):
            continue
        starts[item_start] = (item_header.prefix_id, common)

    ordered_starts = sorted(starts)
    if len(ordered_starts) != entry.buff_data_count:
        raise ValueError(
            f"{entry.name} item 边界恢复失败：声明 {entry.buff_data_count}，"
            f"实际 {len(ordered_starts)}"
        )
    items: list[BuffItemSlice] = []
    for index, item_start in enumerate(ordered_starts):
        item_end = (
            ordered_starts[index + 1]
            if index + 1 < len(ordered_starts)
            else region_end
        )
        leading_lookup, common = starts[item_start]
        items.append(
            BuffItemSlice(
                start=item_start,
                end=item_end,
                leading_lookup=leading_lookup,
                common=common,
            )
        )
    return items


def _is_max_hp_item(entry_bytes: bytes, item: BuffItemSlice) -> bool:
    """确认 item 是 tag 98 的最大生命值倍率项。"""
    if item.common.tag != MAX_STAT_RATE_VARIANT_TAG:
        return False
    variant_start = item.common.end_offset
    return variant_start < item.end and entry_bytes[variant_start] == MAX_HP_VARIANT_TYPE


def _set_hp_rate(entry_bytes: bytearray, item: BuffItemSlice, rate_delta: int) -> None:
    """把 tag 98 的 f01 有符号定点倍率写为目标增量。"""
    value_offset = item.common.end_offset + 1
    if value_offset + 8 > item.end:
        raise ValueError("最大生命值倍率字段越过 item 边界")
    struct.pack_into("<q", entry_bytes, value_offset, rate_delta)


def _validate_patched_entry(
    entry_bytes: bytes,
    expected_count: int,
    rate_delta: int,
) -> None:
    """确认生成记录包含三个难度且倍率值完全一致。"""
    parsed = parse_entry(entry_bytes)
    if parsed.buff_data_count != expected_count:
        raise ValueError(
            f"{parsed.name} 生成后的 item 数量错误："
            f"{parsed.buff_data_count} != {expected_count}"
        )
    items = _collect_buff_items(entry_bytes, parsed)
    hp_rates = {
        item.leading_lookup: struct.unpack_from(
            "<q", entry_bytes, item.common.end_offset + 1
        )[0]
        for item in items
        if _is_max_hp_item(entry_bytes, item)
    }
    expected = {
        EASY_DIFFICULTY_LEVEL: rate_delta,
        NORMAL_DIFFICULTY_LEVEL: rate_delta,
        HARD_DIFFICULTY_LEVEL: rate_delta,
    }
    if hp_rates != expected:
        raise ValueError(f"{parsed.name} 三级生命倍率校验失败：{hp_rates}")


def _build_patched_entry(entry_bytes: bytes, rate_delta: int) -> bytes:
    """为简单、普通、困难三个等级写入目标生命值倍率。"""
    parsed = parse_entry(entry_bytes)
    items = _collect_buff_items(entry_bytes, parsed)
    hp_items = {
        item.leading_lookup: item
        for item in items
        if _is_max_hp_item(entry_bytes, item)
        and item.leading_lookup in {EASY_DIFFICULTY_LEVEL, HARD_DIFFICULTY_LEVEL}
    }
    if set(hp_items) != {EASY_DIFFICULTY_LEVEL, HARD_DIFFICULTY_LEVEL}:
        raise ValueError(f"{parsed.name} 未唯一找到简单/困难最大生命值倍率项")

    normal_items = [item for item in items if item.leading_lookup == NORMAL_DIFFICULTY_LEVEL]
    if not normal_items:
        raise ValueError(f"{parsed.name} 没有普通难度插入锚点")

    modified = bytearray(entry_bytes)
    _set_hp_rate(modified, hp_items[EASY_DIFFICULTY_LEVEL], rate_delta)
    _set_hp_rate(modified, hp_items[HARD_DIFFICULTY_LEVEL], rate_delta)

    hard_item = hp_items[HARD_DIFFICULTY_LEVEL]
    cloned_item = bytearray(modified[hard_item.start : hard_item.end])
    struct.pack_into("<I", cloned_item, 0, NORMAL_DIFFICULTY_LEVEL)

    insert_offset = normal_items[0].start
    modified[insert_offset:insert_offset] = cloned_item
    struct.pack_into(
        "<I",
        modified,
        parsed.buff_data_count_offset,
        parsed.buff_data_count + 1,
    )
    result = bytes(modified)
    _validate_patched_entry(result, parsed.buff_data_count + 1, rate_delta)
    return result


def _build_legacy_document(
    game_dir: Path,
    multiplier: int,
    version: str,
) -> dict[str, object]:
    """根据当前原版表生成两条 entry 级 byte patch。"""
    body = _load_vanilla_table(game_dir, "buffinfo.pabgb")
    header = _load_vanilla_table(game_dir, "buffinfo.pabgh")
    key_size, offsets = parse_pabgh_index(header, "buffinfo")
    bounds = build_entry_bounds(body, key_size, offsets)

    rate_delta = (multiplier - 1) * RATE_SCALE
    changes: list[dict[str, object]] = []
    for key in (NORMAL_ENEMY_DIFFICULTY_BUFF_KEY, BOSS_DIFFICULTY_BUFF_KEY):
        if key not in bounds:
            raise ValueError(f"原版 buffinfo 缺少目标 key：{key}")
        entry_start, entry_end, entry_name, _name_end = bounds[key]
        original = body[entry_start:entry_end]
        patched = _build_patched_entry(original, rate_delta)
        changes.append(
            {
                "offset": entry_start,
                "original": original.hex(),
                "patched": patched.hex(),
                "label": f"{entry_name}: 简单/普通/困难敌人生命值 x{multiplier}",
            }
        )

    return {
        "name": f"Enemy Health x{multiplier}",
        "version": version,
        "author": "N++",
        "description": (
            f"普通敌人与 Boss 在所有难度下均获得 {multiplier} 倍基础生命值。"
        ),
        "modinfo": {
            "name": f"Enemy Health x{multiplier}",
            "version": version,
            "author": "N++",
            "description": (
                f"普通敌人与 Boss 在所有难度下均获得 {multiplier} 倍基础生命值。"
            ),
        },
        "patches": [{"game_file": BUFFINFO_GAME_FILE, "changes": changes}],
    }


def build_mod(
    game_dir: Path,
    output_dir: Path,
    mods_dir: Path,
    multiplier: int,
    version: str,
) -> tuple[Path, Path]:
    """生成源 JSON 和最终 cdmod，并返回两个路径。"""
    game_dir = game_dir.resolve()
    output_dir = output_dir.resolve()
    mods_dir = mods_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    mods_dir.mkdir(parents=True, exist_ok=True)

    mod_name = f"Enemy Health x{multiplier}"
    source_path = output_dir / f"{mod_name}.json"
    source_path.write_text(
        json.dumps(
            _build_legacy_document(game_dir, multiplier, version),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    result = convert_mod_source_to_cdmod(game_dir, source_path, mods_dir)
    if result.status != "converted" or not result.output:
        raise ValueError(f"cdmod 转换失败：{result.detail}")
    generated_path = Path(result.output)
    final_path = mods_dir / f"{mod_name}.cdmod"
    if generated_path != final_path:
        generated_path.replace(final_path)
    return source_path, final_path


def _parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="生成敌人五倍生命值 .cdmod")
    parser.add_argument("--game-dir", type=Path, required=True, help="Crimson Desert 游戏目录")
    parser.add_argument(
        "--multiplier",
        type=int,
        choices=(2, 3, 4, 5),
        default=5,
        help="敌人基础生命值倍率",
    )
    parser.add_argument(
        "--version",
        default=DEFAULT_RELEASE_VERSION,
        help="写入 .cdmod manifest 的发布版本",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("prototype_output/enemy-health-x5"),
        help="源 JSON 输出目录",
    )
    parser.add_argument("--mods-dir", type=Path, required=True, help="最终 .cdmod 输出目录")
    return parser.parse_args()


def main() -> int:
    """命令行入口。"""
    args = _parse_args()
    source_path, cdmod_path = build_mod(
        args.game_dir,
        args.output_dir,
        args.mods_dir,
        args.multiplier,
        args.version,
    )
    print(f"源 JSON：{source_path}")
    print(f"最终 cdmod：{cdmod_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
