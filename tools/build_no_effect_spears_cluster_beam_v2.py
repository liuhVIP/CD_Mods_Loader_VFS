"""为当前原版全部无特殊效果长枪生成集束光线 V2 `.cdmod`。

目标集合复用已实机验证的无特效长枪结构筛选；效果字段严格来自原版
`Kuku_YamatoCannon_TwoHandSpear`，不修改战戟或已有特殊效果武器。
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import zipfile
from pathlib import Path
from typing import Any


PROJECT_PARENT = Path(__file__).resolve().parents[2]
if str(PROJECT_PARENT) not in sys.path:
    sys.path.insert(0, str(PROJECT_PARENT))

from cdmm.services.format3_iteminfo_record_writer import (  # noqa: E402
    _locate_existing_docking_child_data,
    _locate_schema_field,
    _locate_tail_layout,
    _pack_docking_child_optional,
)
from cdmm.services.iteminfo_native_parser import (  # noqa: E402
    _Reader,
    _read_DockingChildData,
)
from cdmm.services.json_loader import extract_plaintext  # noqa: E402
from cdmm.services.pab_table_service import (  # noqa: E402
    build_entry_bounds,
    parse_pabgh_index,
)
from cdmm.services.pamt_index_service import get_game_pamt_index  # noqa: E402
from cdmm.tools.build_no_effect_polearms_lightning_v2 import (  # noqa: E402
    TargetRecord,
    _discover_target_layouts,
    _sha256,
    _write_cdmod,
)


# 原版嘟嘟鸟激光炮长枪记录，是强化版集束光线配方的唯一来源。
NATIVE_RECIPE_KEY = 1_002_179
NATIVE_RECIPE_STRING_KEY = "Kuku_YamatoCannon_TwoHandSpear"

# 原版集束光线实体与双手长枪命中标签。
CLUSTER_BEAM_GIMMICK_KEY = 1_001_885
TWO_HAND_SPEAR_DOCKING_TAG_HASH = 666_382_090

# 嘟嘟鸟强化版激光炮长枪的冷却、充能和生命周期字段。
CLUSTER_BEAM_COOLTIME = 2_000
CLUSTER_BEAM_MAX_CHARGED_COUNT = 10
CLUSTER_BEAM_ITEM_CHARGE_TYPE = 0
CLUSTER_BEAM_RESPAWN_SECONDS = 0
# 游戏 1.12 新增的 ItemInfo 效果开关，缺少时只有挂接特效而没有词条和特殊伤害。
CLUSTER_BEAM_ITEM_EFFECT_INFO = 1

# 完整复制原版集束光线长枪的 docking 结构。
CLUSTER_BEAM_DOCKING_CHILD_DATA = {
    "attach_child_socket_name": "",
    "attach_parent_socket_name": "Gimmick_Weapon_00_Socket",
    "character_key": 0,
    "detected_by_npc": 0,
    "disable_collision_with_other_gimmick": 1,
    "docking_equip_slot_no": 65_535,
    "docking_slot_key": "",
    "docking_tag_name_hash": [TWO_HAND_SPEAR_DOCKING_TAG_HASH, 0, 0, 0],
    "docking_type": 0,
    "enable_collision": 0,
    "gimmick_info_key": CLUSTER_BEAM_GIMMICK_KEY,
    "hit_part": 0,
    "inherit_summoner": 0,
    "is_bag_docking": 0,
    "is_body_part": 0,
    "is_item_equip_docking_gimmick": 1,
    "is_npc_only": 0,
    "is_player_only": 0,
    "is_summoner_team": 0,
    "is_sync_break_parent": 0,
    "item_key": 0,
    "send_damage_to_parent": 0,
    "spawn_distance_level": 4_294_967_295,
    "summon_tag_name_hash": [0, 0, 0, 0],
    "unk_docking_108": 0,
}

# 容器路径、正式标识和成品信息。
MANIFEST_PATH = "manifest.json"
SEMANTIC_PATCH_PATH = "patches/semantic.json"
REPORT_PATH = "reports/cluster-beam-targets.json"
OUTPUT_FILE_NAME = "NoSpecialEffect_Spears_ClusterBeam_V2.field.cdmod"
PACKAGE_ID = "crimsongamemods-itembuffs-no-effect-spears-cluster-beam-v2"
PACKAGE_NAME = "All Non-Elemental Spears - Cluster Beam"
PACKAGE_VERSION = "2.1"
EXPECTED_TARGET_COUNT = 23


def build_cluster_beam_package(game_dir: Path, output_path: Path) -> list[TargetRecord]:
    """生成只包含无特殊效果长枪的集束光线语义包。"""
    if _pack_docking_child_optional(CLUSTER_BEAM_DOCKING_CHILD_DATA) is None:
        raise ValueError("原版集束光线长枪 docking 结构无法序列化")
    _validate_native_recipe(game_dir.resolve())
    targets = _discover_target_layouts(game_dir.resolve())
    if len(targets) != EXPECTED_TARGET_COUNT:
        raise ValueError(f"无特效长枪数量变化：期望 {EXPECTED_TARGET_COUNT}，实际 {len(targets)}")
    operations = _build_cluster_beam_operations(targets)
    manifest = {
        "author": "CrimsonGameMods ItemBuffs",
        "components": [
            {
                "operation_count": len(operations),
                "path": SEMANTIC_PATCH_PATH,
                "target_count": 1,
                "type": "semantic-patch",
            }
        ],
        "dependencies": [],
        "description": (
            "Native Kuku laser cannon spear cluster beam recipe for "
            "23 vanilla spears without existing special effects."
        ),
        "format": "crimson-mod-package",
        "format_version": 1,
        "id": PACKAGE_ID,
        "name": PACKAGE_NAME,
        "source": {
            "format": "native-kuku-laser-cannon-spear-docking-retarget",
            "native_docking_tag_hash": TWO_HAND_SPEAR_DOCKING_TAG_HASH,
            "native_gimmick_info": CLUSTER_BEAM_GIMMICK_KEY,
            "native_recipe_key": NATIVE_RECIPE_KEY,
            "native_recipe_string_key": NATIVE_RECIPE_STRING_KEY,
        },
        "version": PACKAGE_VERSION,
    }
    patch = {
        "schema": 1,
        "targets": [{"file": "iteminfo.pabgb", "operations": operations}],
    }
    report = {
        "schema": 1,
        "policy": (
            "只修改原版无 docking、无被动、普通 gimmick 的双手长枪；"
            "排除战戟和已有特殊效果武器"
        ),
        "recipe": {
            "key": NATIVE_RECIPE_KEY,
            "string_key": NATIVE_RECIPE_STRING_KEY,
        },
        "targets": [
            {"key": target.key, "string_key": target.string_key}
            for target in targets
        ],
    }
    _write_cdmod(
        output_path,
        {
            MANIFEST_PATH: manifest,
            SEMANTIC_PATCH_PATH: patch,
            REPORT_PATH: report,
        },
    )
    _validate_output(output_path, targets)
    return targets


def _build_cluster_beam_operations(targets: list[TargetRecord]) -> list[dict[str, Any]]:
    """为每条长枪记录生成完整集束光线字段。"""
    operations: list[dict[str, Any]] = []
    for target in targets:
        selector = {"key": target.key, "string_key": target.string_key}
        for path, value in (
            ("cooltime", CLUSTER_BEAM_COOLTIME),
            ("unk_post_cooltime_a", CLUSTER_BEAM_COOLTIME),
            ("unk_post_cooltime_b", CLUSTER_BEAM_COOLTIME),
            ("docking_child_data", CLUSTER_BEAM_DOCKING_CHILD_DATA),
            ("equip_passive_skill_list", []),
            ("gimmick_info", CLUSTER_BEAM_GIMMICK_KEY),
            ("item_effect_info", CLUSTER_BEAM_ITEM_EFFECT_INFO),
            ("item_charge_type", CLUSTER_BEAM_ITEM_CHARGE_TYPE),
            ("max_charged_useable_count", CLUSTER_BEAM_MAX_CHARGED_COUNT),
            ("unk_post_max_charged_a", CLUSTER_BEAM_MAX_CHARGED_COUNT),
            ("unk_post_max_charged_b", CLUSTER_BEAM_MAX_CHARGED_COUNT),
            ("respawn_time_seconds", CLUSTER_BEAM_RESPAWN_SECONDS),
        ):
            operations.append(
                {
                    "conversion": "conservative",
                    "op": "set",
                    "path": path,
                    "selector": selector,
                    "value": value,
                }
            )
    return operations


def _validate_native_recipe(game_dir: Path) -> None:
    """从当前原版 ItemInfo 回读并校验嘟嘟鸟激光炮长枪配方。"""
    index = get_game_pamt_index(game_dir)
    body_entry = index.find_best("iteminfo.pabgb", suffix=".pabgb", require_unique_best=False)
    header_entry = index.find_best("iteminfo.pabgh", suffix=".pabgh", require_unique_best=False)
    if body_entry is None or header_entry is None:
        raise ValueError("无法定位原版 iteminfo.pabgb/pabgh")
    body, _ = extract_plaintext(body_entry)
    header, _ = extract_plaintext(header_entry)
    key_size, offsets = parse_pabgh_index(header, "iteminfo")
    bounds = build_entry_bounds(body, key_size, offsets).get(NATIVE_RECIPE_KEY)
    if bounds is None or bounds[2] != NATIVE_RECIPE_STRING_KEY:
        raise ValueError("原版嘟嘟鸟激光炮长枪记录不存在或身份已变化")
    record = body[bounds[0]:bounds[1]]
    docking_offset = _locate_existing_docking_child_data(record)
    gimmick_offset = _locate_schema_field(record, "gimmick_info")
    passive_offset = _locate_schema_field(record, "equip_passive_skill_list")
    tail = _locate_tail_layout(record)
    if docking_offset is None or gimmick_offset is None or passive_offset is None or tail is None:
        raise ValueError("原版集束光线长枪字段定位失败")
    docking = _read_DockingChildData(
        _Reader(record, docking_offset + 1, rec_end=len(record))
    )
    expected_docking = {
        key: value
        for key, value in CLUSTER_BEAM_DOCKING_CHILD_DATA.items()
        if key not in {"inherit_summoner", "summon_tag_name_hash", "unk_docking_108"}
    }
    cooltime = list(struct.unpack_from("<QQQ", record, tail["cooltime"]))
    max_charged = list(struct.unpack_from("<III", record, tail["max_charged"]))
    passive_count = struct.unpack_from("<I", record, passive_offset)[0]
    gimmick = struct.unpack_from("<I", record, gimmick_offset)[0]
    if docking != expected_docking:
        raise ValueError("原版集束光线长枪 docking 配方已变化")
    if passive_count != 0 or gimmick != CLUSTER_BEAM_GIMMICK_KEY:
        raise ValueError("原版集束光线长枪被动或 gimmick 已变化")
    if cooltime != [CLUSTER_BEAM_COOLTIME] * 3:
        raise ValueError("原版集束光线长枪冷却字段已变化")
    if record[tail["item_charge_type"]] != CLUSTER_BEAM_ITEM_CHARGE_TYPE:
        raise ValueError("原版集束光线长枪充能类型已变化")
    if max_charged != [CLUSTER_BEAM_MAX_CHARGED_COUNT] * 3:
        raise ValueError("原版集束光线长枪最大充能字段已变化")
    respawn = struct.unpack_from("<I", record, tail["respawn_time_seconds"])[0]
    if respawn != CLUSTER_BEAM_RESPAWN_SECONDS:
        raise ValueError("原版集束光线长枪生命周期字段已变化")
    item_effect = struct.unpack_from("<I", record, tail["item_effect_info"])[0]
    if item_effect != CLUSTER_BEAM_ITEM_EFFECT_INFO:
        raise ValueError("原版集束光线长枪 ItemEffectInfo 已变化")


def _validate_output(output_path: Path, targets: list[TargetRecord]) -> None:
    """回读成品，确认目标和集束光线字段没有漂移。"""
    with zipfile.ZipFile(output_path) as archive:
        manifest = json.loads(archive.read(MANIFEST_PATH).decode("utf-8-sig"))
        patch = json.loads(archive.read(SEMANTIC_PATCH_PATH).decode("utf-8-sig"))
    operations = patch["targets"][0]["operations"]
    if manifest.get("id") != PACKAGE_ID or manifest.get("version") != PACKAGE_VERSION:
        raise ValueError("集束光线成品 manifest 标识或版本错误")
    if len(operations) != len(targets) * 12:
        raise ValueError("集束光线成品操作数量错误")
    identities = {
        (operation["selector"].get("key"), operation["selector"].get("string_key"))
        for operation in operations
    }
    expected = {(target.key, target.string_key) for target in targets}
    if identities != expected or any("Alebard" in name for _, name in identities):
        raise ValueError("集束光线成品目标集合错误或包含战戟")
    gimmicks = {
        operation.get("value")
        for operation in operations
        if operation.get("path") == "gimmick_info"
    }
    if gimmicks != {CLUSTER_BEAM_GIMMICK_KEY}:
        raise ValueError("集束光线成品 gimmick 配方错误")


def _parse_args() -> argparse.Namespace:
    """读取游戏目录和输出路径。"""
    parser = argparse.ArgumentParser(description="生成 23 把无特殊效果长枪集束光线 V2")
    parser.add_argument(
        "--game-dir",
        type=Path,
        default=Path(r"G:\SteamLibrary\steamapps\common\Crimson Desert"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(r"G:\SteamLibrary\steamapps\common\Crimson Desert\mods")
        / OUTPUT_FILE_NAME,
    )
    return parser.parse_args()


def main() -> int:
    """生成集束光线包并输出目标数量与哈希。"""
    args = _parse_args()
    try:
        targets = build_cluster_beam_package(args.game_dir, args.output)
    except (OSError, ValueError, KeyError, struct.error, zipfile.BadZipFile) as exc:
        print(f"集束光线 V2 构建失败：{exc}", file=sys.stderr)
        return 1
    print(
        f"已生成 {args.output.name}: targets={len(targets)}, "
        f"operations={len(targets) * 12}, sha256={_sha256(args.output)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
