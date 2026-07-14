"""生成当前原版全部无特殊效果双手剑的雷电版与火焰版 `.cdmod`。

雷电配方来自已验证的 Righteous Verdict 单武器包；火焰配方使用已验证的
火焰实体与被动，并替换为原生双手剑 docking 标签。两个成品互相独立。
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import zipfile
from dataclasses import dataclass
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
from cdmm.services.format3_loader import collect_iteminfo_match_records  # noqa: E402
from cdmm.services.json_loader import extract_plaintext  # noqa: E402
from cdmm.services.pab_table_service import (  # noqa: E402
    build_entry_bounds,
    parse_pabgh_index,
)
from cdmm.services.pamt_index_service import get_game_pamt_index  # noqa: E402
from cdmm.tools.build_no_effect_polearms_lightning_v2 import (  # noqa: E402
    TargetRecord,
    _sha256,
    _write_cdmod,
)


# 当前原版双手剑类型与无特殊效果记录的共同字段。
TWO_HAND_SWORD_EQUIP_TYPE = 1_086_980_073
TWO_HAND_SWORD_NAME_SUFFIX = "TwoHandSword"
VANILLA_NO_EFFECT_GIMMICK_KEY = 1_008_474
EMPTY_DOCKING_WINDOW_SIZE = 34
EXPECTED_TARGET_COUNT = 20
PROVEN_TARGET = (240_030, "Caliburn_TwoHandSword")

# 原生双手剑命中标签。长枪的 666382090 不能用于双手剑。
TWO_HAND_SWORD_DOCKING_TAG_HASH = 3_365_725_887

# 已验证的雷电与火焰实体、被动技能。
LIGHTNING_GIMMICK_KEY = 1_001_961
LIGHTNING_PASSIVE_SKILL_KEY = 91_101
FLAME_GIMMICK_KEY = 1_001_492
FLAME_PASSIVE_SKILL_KEY = 91_105
ELEMENT_PASSIVE_SKILL_LEVEL = 3

# 两个已验证单武器配方包，仅用于构建前真实性校验。
DEFAULT_LIGHTNING_RECIPE_PACKAGE = Path(
    r"G:\SteamLibrary\steamapps\common\Crimson Desert\mods\RighteousVerdict_Lightning.field.cdmod"
)
DEFAULT_FLAME_RECIPE_PACKAGE = Path(
    r"G:\SteamLibrary\steamapps\common\Crimson Desert\mods\AeserionSpear_Flame.field.cdmod.123"
)

# `.cdmod` 内部路径和输出文件名。
MANIFEST_PATH = "manifest.json"
SEMANTIC_PATCH_PATH = "patches/semantic.json"
REPORT_PATH = "reports/element-targets.json"
LIGHTNING_OUTPUT_FILE_NAME = "NoSpecialEffect_TwoHandSwords_Lightning_V2.field.cdmod"
FLAME_OUTPUT_FILE_NAME = "NoSpecialEffect_TwoHandSwords_Flame_V2.field.cdmod"
PACKAGE_VERSION = "2.0"


@dataclass(frozen=True)
class ElementRecipe:
    """一套可安全移植到无特效双手剑的元素配方。"""

    slug: str
    display_name: str
    package_id: str
    output_file_name: str
    gimmick_key: int
    passive_skill_key: int
    cooltime: int
    max_charged_count: int
    reference_key: int
    reference_string_key: str


LIGHTNING_RECIPE = ElementRecipe(
    slug="lightning",
    display_name="Lightning",
    package_id="crimsongamemods-itembuffs-no-effect-two-hand-swords-lightning-v2",
    output_file_name=LIGHTNING_OUTPUT_FILE_NAME,
    gimmick_key=LIGHTNING_GIMMICK_KEY,
    passive_skill_key=LIGHTNING_PASSIVE_SKILL_KEY,
    cooltime=1_000,
    max_charged_count=3,
    reference_key=240_030,
    reference_string_key="Caliburn_TwoHandSword",
)

FLAME_RECIPE = ElementRecipe(
    slug="flame",
    display_name="Flame",
    package_id="crimsongamemods-itembuffs-no-effect-two-hand-swords-flame-v2",
    output_file_name=FLAME_OUTPUT_FILE_NAME,
    gimmick_key=FLAME_GIMMICK_KEY,
    passive_skill_key=FLAME_PASSIVE_SKILL_KEY,
    cooltime=1_000,
    max_charged_count=10,
    reference_key=15_700,
    reference_string_key="Legendary_Dragon_TwoHandGiantSpear",
)


def build_element_packages(
    game_dir: Path,
    output_dir: Path,
    lightning_reference: Path,
    flame_reference: Path,
) -> dict[str, tuple[Path, list[TargetRecord]]]:
    """校验配方后同时生成雷电和火焰两个双手剑包。"""
    _validate_reference_recipe(lightning_reference, LIGHTNING_RECIPE, expect_sword_tag=True)
    _validate_reference_recipe(flame_reference, FLAME_RECIPE, expect_sword_tag=False)
    targets = _discover_target_layouts(game_dir.resolve())
    if len(targets) != EXPECTED_TARGET_COUNT:
        raise ValueError(f"无特效双手剑数量变化：期望 {EXPECTED_TARGET_COUNT}，实际 {len(targets)}")
    results: dict[str, tuple[Path, list[TargetRecord]]] = {}
    for recipe, reference in (
        (LIGHTNING_RECIPE, lightning_reference),
        (FLAME_RECIPE, flame_reference),
    ):
        output_path = output_dir / recipe.output_file_name
        _build_package(output_path, recipe, reference, targets)
        results[recipe.slug] = (output_path, targets)
    return results


def _discover_target_layouts(game_dir: Path) -> list[TargetRecord]:
    """发现并严格筛选当前原版全部无特殊效果双手剑。"""
    index = get_game_pamt_index(game_dir)
    body_entry = index.find_best("iteminfo.pabgb", suffix=".pabgb", require_unique_best=False)
    header_entry = index.find_best("iteminfo.pabgh", suffix=".pabgh", require_unique_best=False)
    if body_entry is None or header_entry is None:
        raise ValueError("无法定位原版 iteminfo.pabgb/pabgh")
    body, _ = extract_plaintext(body_entry)
    header, _ = extract_plaintext(header_entry)
    key_size, offsets = parse_pabgh_index(header, "iteminfo")
    bounds = build_entry_bounds(body, key_size, offsets)
    targets: list[TargetRecord] = []
    for match_record in collect_iteminfo_match_records(body, bounds):
        key = match_record.get("key")
        string_key = match_record.get("string_key")
        if (
            match_record.get("equip_type_info") != TWO_HAND_SWORD_EQUIP_TYPE
            or not isinstance(key, int)
            or isinstance(key, bool)
            or not isinstance(string_key, str)
            or not string_key.endswith(TWO_HAND_SWORD_NAME_SUFFIX)
        ):
            continue
        record_bounds = bounds.get(key)
        if record_bounds is None or record_bounds[2] != string_key:
            continue
        record = body[record_bounds[0]:record_bounds[1]]
        if _locate_existing_docking_child_data(record) is not None:
            continue
        passive_offset = _locate_schema_field(record, "equip_passive_skill_list")
        gimmick_offset = _locate_schema_field(record, "gimmick_info")
        tail = _locate_tail_layout(record)
        if passive_offset is None or gimmick_offset is None or tail is None:
            continue
        if struct.unpack_from("<I", record, passive_offset)[0] != 0:
            continue
        if struct.unpack_from("<I", record, gimmick_offset)[0] != VANILLA_NO_EFFECT_GIMMICK_KEY:
            continue
        cooltime = list(struct.unpack_from("<QQQ", record, tail["cooltime"]))
        max_charged = list(struct.unpack_from("<III", record, tail["max_charged"]))
        if cooltime != [0, 0, 0]:
            continue
        if record[tail["item_charge_type"]] != 2 or max_charged != [1, 1, 1]:
            continue
        docking_offset = tail["docking_child_data"]
        if record[docking_offset:docking_offset + EMPTY_DOCKING_WINDOW_SIZE] != (
            b"\x00" * EMPTY_DOCKING_WINDOW_SIZE
        ):
            continue
        targets.append(TargetRecord(key, string_key, string_key))
    targets.sort(key=lambda target: (target.key, target.string_key))
    identities = {(target.key, target.string_key) for target in targets}
    if PROVEN_TARGET not in identities:
        raise ValueError("无特效双手剑筛选结果缺少 Righteous Verdict 已验证目标")
    return targets


def _build_package(
    output_path: Path,
    recipe: ElementRecipe,
    reference_path: Path,
    targets: list[TargetRecord],
) -> None:
    """按同一目标集合生成一个确定性的元素双手剑包。"""
    docking = _build_docking_child_data(recipe.gimmick_key)
    if _pack_docking_child_optional(docking) is None:
        raise ValueError(f"{recipe.display_name} 双手剑 docking 结构无法序列化")
    operations = _build_operations(targets, recipe, docking)
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
            f"{recipe.display_name} attribute, weapon visual, and hit damage recipe for "
            f"{len(targets)} vanilla two-handed swords without existing special effects."
        ),
        "format": "crimson-mod-package",
        "format_version": 1,
        "id": recipe.package_id,
        "name": f"All Non-Elemental Two-Handed Swords - {recipe.display_name}",
        "source": {
            "format": "verified-element-recipe-with-native-two-hand-sword-docking",
            "native_docking_tag_hash": TWO_HAND_SWORD_DOCKING_TAG_HASH,
            "native_gimmick_info": recipe.gimmick_key,
            "native_passive_skill": recipe.passive_skill_key,
            "reference_package": reference_path.name,
            "reference_sha256": _sha256(reference_path),
        },
        "version": PACKAGE_VERSION,
    }
    patch = {
        "schema": 1,
        "targets": [{"file": "iteminfo.pabgb", "operations": operations}],
    }
    report = {
        "schema": 1,
        "policy": "只修改原版无 docking、无被动、普通 gimmick 的双手剑；排除已有特殊效果武器",
        "recipe": recipe.slug,
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
    _validate_output(output_path, recipe, targets, docking)


def _build_operations(
    targets: list[TargetRecord],
    recipe: ElementRecipe,
    docking: dict[str, Any],
) -> list[dict[str, Any]]:
    """为一个元素配方生成全部双手剑的 11 个完整字段操作。"""
    operations: list[dict[str, Any]] = []
    for target in targets:
        selector = {"key": target.key, "string_key": target.string_key}
        for path, value in (
            ("cooltime", recipe.cooltime),
            ("unk_post_cooltime_a", recipe.cooltime),
            ("unk_post_cooltime_b", recipe.cooltime),
            ("docking_child_data", docking),
            (
                "equip_passive_skill_list",
                [{"level": ELEMENT_PASSIVE_SKILL_LEVEL, "skill": recipe.passive_skill_key}],
            ),
            ("gimmick_info", recipe.gimmick_key),
            ("item_charge_type", 0),
            ("max_charged_useable_count", recipe.max_charged_count),
            ("unk_post_max_charged_a", recipe.max_charged_count),
            ("unk_post_max_charged_b", recipe.max_charged_count),
            ("respawn_time_seconds", 0),
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


def _build_docking_child_data(gimmick_key: int) -> dict[str, Any]:
    """创建使用原生双手剑命中标签的完整 docking 结构。"""
    return {
        "attach_child_socket_name": "",
        "attach_parent_socket_name": "Gimmick_Weapon_00_Socket",
        "character_key": 0,
        "detected_by_npc": 0,
        "disable_collision_with_other_gimmick": 1,
        "docking_equip_slot_no": 65_535,
        "docking_slot_key": "",
        "docking_tag_name_hash": [TWO_HAND_SWORD_DOCKING_TAG_HASH, 0, 0, 0],
        "docking_type": 0,
        "enable_collision": 0,
        "gimmick_info_key": gimmick_key,
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


def _validate_reference_recipe(
    package_path: Path,
    recipe: ElementRecipe,
    *,
    expect_sword_tag: bool,
) -> None:
    """确认参考包仍包含预期目标与完整元素配方。"""
    with zipfile.ZipFile(package_path) as archive:
        document = json.loads(archive.read(SEMANTIC_PATCH_PATH).decode("utf-8-sig"))
    target_operations = [
        operation
        for operation in document["targets"][0]["operations"]
        if operation.get("selector", {}).get("key") == recipe.reference_key
        and operation.get("selector", {}).get("string_key") == recipe.reference_string_key
    ]
    if len(target_operations) != 11:
        raise ValueError(f"{recipe.display_name} 参考包目标操作数量已变化")
    by_path = {operation["path"]: operation.get("value") for operation in target_operations}
    docking = by_path.get("docking_child_data")
    if not isinstance(docking, dict):
        raise ValueError(f"{recipe.display_name} 参考包缺少完整 docking")
    expected_tag = TWO_HAND_SWORD_DOCKING_TAG_HASH if expect_sword_tag else 666_382_090
    if docking.get("docking_tag_name_hash") != [expected_tag, 0, 0, 0]:
        raise ValueError(f"{recipe.display_name} 参考包 docking 标签已变化")
    if docking.get("gimmick_info_key") != recipe.gimmick_key:
        raise ValueError(f"{recipe.display_name} 参考包 docking gimmick 已变化")
    if by_path.get("gimmick_info") != recipe.gimmick_key:
        raise ValueError(f"{recipe.display_name} 参考包 gimmick 已变化")
    expected_passive = [
        {"level": ELEMENT_PASSIVE_SKILL_LEVEL, "skill": recipe.passive_skill_key}
    ]
    if by_path.get("equip_passive_skill_list") != expected_passive:
        raise ValueError(f"{recipe.display_name} 参考包元素被动已变化")
    if by_path.get("cooltime") != recipe.cooltime:
        raise ValueError(f"{recipe.display_name} 参考包冷却字段已变化")
    if by_path.get("max_charged_useable_count") != recipe.max_charged_count:
        raise ValueError(f"{recipe.display_name} 参考包充能字段已变化")


def _validate_output(
    output_path: Path,
    recipe: ElementRecipe,
    targets: list[TargetRecord],
    docking: dict[str, Any],
) -> None:
    """回读成品并校验身份、目标集合与元素配方。"""
    with zipfile.ZipFile(output_path) as archive:
        manifest = json.loads(archive.read(MANIFEST_PATH).decode("utf-8-sig"))
        patch = json.loads(archive.read(SEMANTIC_PATCH_PATH).decode("utf-8-sig"))
    operations = patch["targets"][0]["operations"]
    if manifest.get("id") != recipe.package_id or manifest.get("version") != PACKAGE_VERSION:
        raise ValueError(f"{recipe.display_name} 成品 manifest 标识或版本错误")
    if len(operations) != len(targets) * 11:
        raise ValueError(f"{recipe.display_name} 成品操作数量错误")
    identities = {
        (operation["selector"].get("key"), operation["selector"].get("string_key"))
        for operation in operations
    }
    expected = {(target.key, target.string_key) for target in targets}
    if identities != expected:
        raise ValueError(f"{recipe.display_name} 成品目标集合错误")
    docking_values = [
        operation.get("value")
        for operation in operations
        if operation.get("path") == "docking_child_data"
    ]
    if len(docking_values) != len(targets) or any(value != docking for value in docking_values):
        raise ValueError(f"{recipe.display_name} 成品双手剑 docking 配方错误")


def _parse_args() -> argparse.Namespace:
    """读取游戏目录、参考包与输出目录。"""
    parser = argparse.ArgumentParser(description="生成无特殊效果双手剑雷电与火焰 V2")
    parser.add_argument(
        "--game-dir",
        type=Path,
        default=Path(r"G:\SteamLibrary\steamapps\common\Crimson Desert"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(r"G:\SteamLibrary\steamapps\common\Crimson Desert\mods"),
    )
    parser.add_argument(
        "--lightning-reference",
        type=Path,
        default=DEFAULT_LIGHTNING_RECIPE_PACKAGE,
    )
    parser.add_argument(
        "--flame-reference",
        type=Path,
        default=DEFAULT_FLAME_RECIPE_PACKAGE,
    )
    return parser.parse_args()


def main() -> int:
    """生成两个元素双手剑包并输出目标数量与哈希。"""
    args = _parse_args()
    try:
        results = build_element_packages(
            args.game_dir,
            args.output_dir,
            args.lightning_reference,
            args.flame_reference,
        )
    except (OSError, ValueError, KeyError, struct.error, zipfile.BadZipFile) as exc:
        print(f"双手剑元素 V2 构建失败：{exc}", file=sys.stderr)
        return 1
    for slug in ("lightning", "flame"):
        output_path, targets = results[slug]
        print(
            f"已生成 {output_path.name}: targets={len(targets)}, "
            f"operations={len(targets) * 11}, sha256={_sha256(output_path)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
