"""为当前原版全部无特殊效果长枪生成火焰效果 V2 `.cdmod`。

目标集合复用已经实机验证的无特效长枪结构筛选；火焰字段严格来自原始
Aeserion 火焰包和原生 Fire Staff Spear，不修改战戟或已有特殊效果武器。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path
from typing import Any


PROJECT_PARENT = Path(__file__).resolve().parents[2]
if str(PROJECT_PARENT) not in sys.path:
    sys.path.insert(0, str(PROJECT_PARENT))

from cdmm.services.format3_iteminfo_record_writer import (  # noqa: E402
    _pack_docking_child_optional,
)
from cdmm.tools.build_no_effect_polearms_lightning_v2 import (  # noqa: E402
    TargetRecord,
    _discover_target_layouts,
)


# 原生双手长枪火焰实体、火焰被动与命中标签。
FLAME_GIMMICK_KEY = 1_001_492
FLAME_PASSIVE_SKILL_KEY = 91_105
FLAME_PASSIVE_SKILL_LEVEL = 3
TWO_HAND_SPEAR_DOCKING_TAG_HASH = 666_382_090

# 已验证长枪配方的稳定冷却、充能和生命周期字段。
FLAME_COOLTIME = 1_000
FLAME_MAX_CHARGED_COUNT = 10
FLAME_ITEM_CHARGE_TYPE = 0
FLAME_RESPAWN_SECONDS = 0

# 原生 Fire Staff Spear 的完整 docking 结构。
FLAME_DOCKING_CHILD_DATA = {
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
    "gimmick_info_key": FLAME_GIMMICK_KEY,
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

# 容器路径、正式标识和确定性 ZIP 时间戳。
MANIFEST_PATH = "manifest.json"
SEMANTIC_PATCH_PATH = "patches/semantic.json"
REPORT_PATH = "reports/flame-targets.json"
ZIP_TIMESTAMP = (2026, 1, 1, 0, 0, 0)
OUTPUT_FILE_NAME = "NoSpecialEffect_Spears_Flame_V2.field.cdmod"
PACKAGE_ID = "crimsongamemods-itembuffs-no-effect-spears-flame-v2"
PACKAGE_NAME = "All Non-Elemental Spears - Flame Effects"
PACKAGE_VERSION = "2.0"
EXPECTED_TARGET_COUNT = 23

# 原始火焰包只作为配方真实性校验，不会被写入成品。
DEFAULT_RECIPE_PACKAGE = Path(
    r"G:\SteamLibrary\steamapps\common\Crimson Desert\mods\AeserionSpear_Flame.field.cdmod.123"
)


def build_flame_package(
    game_dir: Path,
    recipe_package: Path,
    output_path: Path,
) -> list[TargetRecord]:
    """生成只包含 23 把无特殊效果长枪的火焰语义包。"""
    if _pack_docking_child_optional(FLAME_DOCKING_CHILD_DATA) is None:
        raise ValueError("原生火焰长枪 docking 结构无法序列化")
    _validate_flame_recipe(recipe_package)
    targets = _discover_target_layouts(game_dir.resolve())
    if len(targets) != EXPECTED_TARGET_COUNT:
        raise ValueError(f"无特效长枪数量变化：期望 {EXPECTED_TARGET_COUNT}，实际 {len(targets)}")
    operations = _build_flame_operations(targets)
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
            "Verified native spear flame visuals, flame weapon attribute, and flame hit damage "
            "recipe for 23 vanilla spears without existing special effects."
        ),
        "format": "crimson-mod-package",
        "format_version": 1,
        "id": PACKAGE_ID,
        "name": PACKAGE_NAME,
        "source": {
            "format": "native-fire-staff-spear-docking-retarget",
            "native_docking_tag_hash": TWO_HAND_SPEAR_DOCKING_TAG_HASH,
            "native_gimmick_info": FLAME_GIMMICK_KEY,
            "native_passive_skill": FLAME_PASSIVE_SKILL_KEY,
            "recipe_package": recipe_package.name,
            "recipe_sha256": _sha256(recipe_package),
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


def _build_flame_operations(targets: list[TargetRecord]) -> list[dict[str, Any]]:
    """为每条长枪记录生成完整火焰效果字段。"""
    operations: list[dict[str, Any]] = []
    for target in targets:
        selector = {"key": target.key, "string_key": target.string_key}
        for path, value in (
            ("cooltime", FLAME_COOLTIME),
            ("unk_post_cooltime_a", FLAME_COOLTIME),
            ("unk_post_cooltime_b", FLAME_COOLTIME),
            ("docking_child_data", FLAME_DOCKING_CHILD_DATA),
            (
                "equip_passive_skill_list",
                [{"level": FLAME_PASSIVE_SKILL_LEVEL, "skill": FLAME_PASSIVE_SKILL_KEY}],
            ),
            ("gimmick_info", FLAME_GIMMICK_KEY),
            ("item_charge_type", FLAME_ITEM_CHARGE_TYPE),
            ("max_charged_useable_count", FLAME_MAX_CHARGED_COUNT),
            ("unk_post_max_charged_a", FLAME_MAX_CHARGED_COUNT),
            ("unk_post_max_charged_b", FLAME_MAX_CHARGED_COUNT),
            ("respawn_time_seconds", FLAME_RESPAWN_SECONDS),
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


def _validate_flame_recipe(recipe_package: Path) -> None:
    """确认参考包仍包含原生火焰长枪的完整字段值。"""
    with zipfile.ZipFile(recipe_package) as archive:
        document = json.loads(archive.read(SEMANTIC_PATCH_PATH).decode("utf-8-sig"))
    operations = document["targets"][0]["operations"]
    target_operations = [
        operation
        for operation in operations
        if operation.get("selector", {}).get("key") == 15_700
    ]
    by_path = {operation.get("path"): operation.get("value") for operation in target_operations}
    if len(target_operations) != 11:
        raise ValueError("Aeserion 火焰参考包目标操作数量已变化")
    if by_path.get("gimmick_info") != FLAME_GIMMICK_KEY:
        raise ValueError("Aeserion 火焰参考包 gimmick 已变化")
    if by_path.get("equip_passive_skill_list") != [
        {"level": FLAME_PASSIVE_SKILL_LEVEL, "skill": FLAME_PASSIVE_SKILL_KEY}
    ]:
        raise ValueError("Aeserion 火焰参考包被动技能已变化")
    if by_path.get("docking_child_data") != FLAME_DOCKING_CHILD_DATA:
        raise ValueError("Aeserion 火焰参考包 docking 已变化")


def _write_cdmod(output_path: Path, documents: dict[str, dict[str, Any]]) -> None:
    """按固定顺序和时间戳写入 UTF-8 确定性 `.cdmod`。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for archive_path in sorted(documents):
            info = zipfile.ZipInfo(archive_path, ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, _json_bytes(documents[archive_path]), compresslevel=9)


def _validate_output(output_path: Path, targets: list[TargetRecord]) -> None:
    """回读成品，确认身份、目标和火焰字段没有漂移。"""
    with zipfile.ZipFile(output_path) as archive:
        manifest = json.loads(archive.read(MANIFEST_PATH).decode("utf-8-sig"))
        patch = json.loads(archive.read(SEMANTIC_PATCH_PATH).decode("utf-8-sig"))
    operations = patch["targets"][0]["operations"]
    if manifest.get("id") != PACKAGE_ID or manifest.get("version") != PACKAGE_VERSION:
        raise ValueError("火焰成品 manifest 标识或版本错误")
    if len(operations) != len(targets) * 11:
        raise ValueError("火焰成品操作数量错误")
    identities = {
        (operation["selector"].get("key"), operation["selector"].get("string_key"))
        for operation in operations
    }
    expected = {(target.key, target.string_key) for target in targets}
    if identities != expected or any("Alebard" in name for _, name in identities):
        raise ValueError("火焰成品目标集合错误或包含战戟")


def _json_bytes(document: dict[str, Any]) -> bytes:
    """输出稳定排序的 UTF-8 JSON。"""
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"


def _sha256(path: Path) -> str:
    """计算输入配方或成品文件 SHA-256。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_args() -> argparse.Namespace:
    """读取游戏目录、参考配方和输出路径。"""
    parser = argparse.ArgumentParser(description="生成 23 把无特殊效果长枪火焰 V2")
    parser.add_argument(
        "--game-dir",
        type=Path,
        default=Path(r"G:\SteamLibrary\steamapps\common\Crimson Desert"),
    )
    parser.add_argument("--recipe", type=Path, default=DEFAULT_RECIPE_PACKAGE)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(r"G:\SteamLibrary\steamapps\common\Crimson Desert\mods")
        / OUTPUT_FILE_NAME,
    )
    return parser.parse_args()


def main() -> int:
    """生成火焰包并输出目标数量与哈希。"""
    args = _parse_args()
    try:
        targets = build_flame_package(args.game_dir, args.recipe, args.output)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        print(f"火焰 V2 构建失败：{exc}", file=sys.stderr)
        return 1
    print(
        f"已生成 {args.output.name}: targets={len(targets)}, operations={len(targets) * 11}, "
        f"sha256={_sha256(args.output)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
