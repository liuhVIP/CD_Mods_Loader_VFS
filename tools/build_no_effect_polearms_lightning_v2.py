"""为原版无特殊效果的双手长柄武器生成雷电属性 V2 测试包。

只选择当前原版中没有 docking、没有元素被动、使用普通武器 gimmick 的双手
长枪和巨型双手长枪。Aeserion 的大剑系 docking 标签移植到普通长柄武器后只能
显示雷属性而不能造成命中雷伤；原生 Marni 雷电双手长枪的完整 1001961 docking
配方与长枪命中标签 666382090 已由西德蒙长枪完成实机验证。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 允许从项目根目录或父目录直接运行工具。
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
from cdmm.services.pab_table_service import build_entry_bounds, parse_pabgh_index  # noqa: E402
from cdmm.services.pamt_index_service import get_game_pamt_index  # noqa: E402


# 已实机生效的 Aeserion 雷电包，作为完整长枪配方唯一来源。
DEFAULT_RECIPE_PACKAGE = Path(
    r"T:\python_pro\cdmm\nexusmods\11-lightning-spears-1.13.01-cdmod"
) / "Aeserion Spear Lightning-1.13.01.cdmod"

# 原生 `Marni_MachineKnight_TwoHandSpear` 使用的雷电实体。
LIGHTNING_GIMMICK_KEY = 1_001_961

# 原生双手长枪 docking 统一使用的命中标签；Aeserion 包中的
# 3365725887 属于另一套武器动作，不能继续直接移植到普通长柄武器。
TWO_HAND_SPEAR_DOCKING_TAG_HASH = 666_382_090

# 原生雷电武器用于提供雷属性与命中雷伤的被动技能。
LIGHTNING_PASSIVE_SKILL_KEY = 91_101
LIGHTNING_PASSIVE_SKILL_LEVEL = 3

# 完整复制原生 Marni 雷电双手长枪的 docking 结构，只保留已确认字段。
LIGHTNING_DOCKING_CHILD_DATA = {
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
    "gimmick_info_key": LIGHTNING_GIMMICK_KEY,
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

# 保留 Aeserion 已验证包作为问题来源和配方演进依据，不再复制其 docking。
REFERENCE_RECIPE_NAME = "Aeserion Spear Lightning-1.13.01.cdmod"

# 原生雷电长枪共同使用的稳定充能与生命周期字段。
LIGHTNING_COOLTIME = 1_000
LIGHTNING_MAX_CHARGED_COUNT = 10
LIGHTNING_ITEM_CHARGE_TYPE = 0
LIGHTNING_RESPAWN_SECONDS = 0

# 当前双手长枪类型与名称后缀。战戟使用不同 equip_type_info，必须排除。
TWO_HAND_SPEAR_EQUIP_TYPE = 2_914_941_932
TWO_HAND_SPEAR_NAME_SUFFIXES = ("TwoHandSpear", "TwoHandGiantSpear")

# 当前原版无特殊效果长枪共同使用的普通武器 gimmick。
VANILLA_NO_EFFECT_GIMMICK_KEY = 18_020_015

# 已实机验证的锚点，后续游戏更新后筛选结果至少必须继续包含该记录。
PROVEN_SIDMON_SPEAR = (310_008, "Kephilray_TwoHandSpear")

# 只允许原版没有 docking 的目标，防止覆盖已有特殊效果。
EMPTY_DOCKING_WINDOW_SIZE = 34

# `.cdmod` 固定路径、确定性时间戳和成品信息。
MANIFEST_PATH = "manifest.json"
SEMANTIC_PATCH_PATH = "patches/semantic.json"
REPORT_PATH = "reports/v2-targets.json"
ZIP_TIMESTAMP = (2026, 1, 1, 0, 0, 0)
OUTPUT_FILE_NAME = "NoSpecialEffect_Polearms_Lightning_V2.field.cdmod"
PACKAGE_ID = "crimsongamemods-itembuffs-no-effect-spears-lightning-v2"
PACKAGE_NAME = "No Special Effect Spears - Lightning"
PACKAGE_VERSION = "2.0"


@dataclass(frozen=True)
class TargetRecord:
    """一个经过原版结构校验的 V2 长柄武器目标。"""

    key: int
    string_key: str
    display_name: str


def build_v2_package(
    game_dir: Path,
    recipe_package: Path,
    output_path: Path,
) -> list[TargetRecord]:
    """按原生 Marni 雷电双手长枪配方构建全部无特效长枪。"""
    if _pack_docking_child_optional(LIGHTNING_DOCKING_CHILD_DATA) is None:
        raise ValueError("原生 Marni 雷电长枪 docking 结构无法序列化")
    targets = _discover_target_layouts(game_dir.resolve())
    _validate_reference_recipe(recipe_package)
    operations = _build_direct_lightning_operations(targets)

    patch_document = {
        "schema": 1,
        "targets": [{"file": "iteminfo.pabgb", "operations": operations}],
    }
    manifest_document = {
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
        "description": f"Native Marni lightning spear docking recipe for {len(targets)} vanilla spears without existing special effects.",
        "format": "crimson-mod-package",
        "format_version": 1,
        "id": PACKAGE_ID,
        "name": PACKAGE_NAME,
        "source": {
            "format": "native-marni-lightning-spear-docking-retarget",
            "native_docking_tag_hash": TWO_HAND_SPEAR_DOCKING_TAG_HASH,
            "native_gimmick_info": LIGHTNING_GIMMICK_KEY,
            "native_passive_skill": LIGHTNING_PASSIVE_SKILL_KEY,
            "recipe_package": recipe_package.name,
            "recipe_sha256": _sha256(recipe_package),
        },
        "version": PACKAGE_VERSION,
    }
    report_document = {
        "schema": 1,
        "policy": "只修改原版无 docking、无被动、普通 gimmick 的双手长枪；排除战戟和已有特殊效果武器",
        "targets": [
            {
                "display_name": target.display_name,
                "key": target.key,
                "string_key": target.string_key,
            }
            for target in targets
        ],
    }
    _write_cdmod(
        output_path,
        {
            MANIFEST_PATH: manifest_document,
            SEMANTIC_PATCH_PATH: patch_document,
            REPORT_PATH: report_document,
        },
    )
    _validate_output(output_path, targets)
    return targets


def _build_direct_lightning_operations(targets: list[TargetRecord]) -> list[dict[str, Any]]:
    """为每个明确目标生成原生 Marni 雷电双手长枪字段。"""
    operations: list[dict[str, Any]] = []
    for target in targets:
        selector = {"key": target.key, "string_key": target.string_key}
        for path, value in (
            ("cooltime", LIGHTNING_COOLTIME),
            ("unk_post_cooltime_a", LIGHTNING_COOLTIME),
            ("unk_post_cooltime_b", LIGHTNING_COOLTIME),
            ("docking_child_data", LIGHTNING_DOCKING_CHILD_DATA),
            (
                "equip_passive_skill_list",
                [{"level": LIGHTNING_PASSIVE_SKILL_LEVEL, "skill": LIGHTNING_PASSIVE_SKILL_KEY}],
            ),
            ("gimmick_info", LIGHTNING_GIMMICK_KEY),
            ("item_charge_type", LIGHTNING_ITEM_CHARGE_TYPE),
            ("max_charged_useable_count", LIGHTNING_MAX_CHARGED_COUNT),
            ("unk_post_max_charged_a", LIGHTNING_MAX_CHARGED_COUNT),
            ("unk_post_max_charged_b", LIGHTNING_MAX_CHARGED_COUNT),
            ("respawn_time_seconds", LIGHTNING_RESPAWN_SECONDS),
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


def _discover_target_layouts(game_dir: Path) -> list[TargetRecord]:
    """发现并校验当前原版全部无特殊效果双手长枪。"""
    index = get_game_pamt_index(game_dir)
    body_entry = index.find_best("iteminfo.pabgb", suffix=".pabgb", require_unique_best=False)
    if body_entry is None:
        raise ValueError("无法定位原版 iteminfo.pabgb")
    header_entry = index.find_best(
        body_entry.path.rsplit(".", 1)[0] + ".pabgh",
        suffix=".pabgh",
        require_unique_best=False,
    )
    if header_entry is None:
        raise ValueError("无法定位原版 iteminfo.pabgh")
    body, _ = extract_plaintext(body_entry)
    header, _ = extract_plaintext(header_entry)
    key_size, offsets = parse_pabgh_index(header, "iteminfo")
    bounds = build_entry_bounds(body, key_size, offsets)
    match_records = collect_iteminfo_match_records(body, bounds)
    targets: list[TargetRecord] = []
    for match_record in match_records:
        key = match_record.get("key")
        string_key = match_record.get("string_key")
        if (
            match_record.get("equip_type_info") != TWO_HAND_SPEAR_EQUIP_TYPE
            or not isinstance(key, int)
            or isinstance(key, bool)
            or not isinstance(string_key, str)
            or not string_key.endswith(TWO_HAND_SPEAR_NAME_SUFFIXES)
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
        if passive_offset is None or gimmick_offset is None:
            continue
        if struct.unpack_from("<I", record, passive_offset)[0] != 0:
            continue
        if struct.unpack_from("<I", record, gimmick_offset)[0] != VANILLA_NO_EFFECT_GIMMICK_KEY:
            continue
        layout = _locate_tail_layout(record)
        if layout is None:
            continue
        cooltime_values = [
            struct.unpack_from("<Q", record, layout["cooltime"] + index * 8)[0]
            for index in range(3)
        ]
        max_charged_values = [
            struct.unpack_from("<I", record, layout["max_charged"] + index * 4)[0]
            for index in range(3)
        ]
        if cooltime_values != [0, 0, 0]:
            continue
        if record[layout["item_charge_type"]] != 2 or max_charged_values != [1, 1, 1]:
            continue
        docking_offset = layout["docking_child_data"]
        expected = b"\x00" * EMPTY_DOCKING_WINDOW_SIZE
        if record[docking_offset:docking_offset + EMPTY_DOCKING_WINDOW_SIZE] != expected:
            continue
        targets.append(TargetRecord(key, string_key, string_key))
    targets.sort(key=lambda target: (target.key, target.string_key))
    identities = {(target.key, target.string_key) for target in targets}
    if PROVEN_SIDMON_SPEAR not in identities:
        raise ValueError("无特效长枪筛选结果缺少已实机验证的西德蒙长枪")
    if not targets:
        raise ValueError("当前原版没有可安全写入雷电配方的无特效长枪")
    return targets


def _validate_reference_recipe(recipe_package: Path) -> None:
    """确认用户提供的 Aeserion 参考包仍是已分析的旧 docking 配方。"""
    with zipfile.ZipFile(recipe_package) as archive:
        document = json.loads(archive.read(SEMANTIC_PATCH_PATH).decode("utf-8-sig"))
    operations = document["targets"][0]["operations"]
    if recipe_package.name != REFERENCE_RECIPE_NAME or len(operations) != 14:
        raise ValueError("Aeserion 参考包身份或操作数量已变化")
    if not any(operation.get("path") == "docking_child_data" for operation in operations):
        raise ValueError("Aeserion 参考包不再包含待规避的完整 docking 配方")


def _write_cdmod(output_path: Path, documents: dict[str, dict[str, Any]]) -> None:
    """按固定顺序和时间戳写入 UTF-8 确定性 `.cdmod`。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for archive_path in sorted(documents):
            info = zipfile.ZipInfo(archive_path, ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, _json_bytes(documents[archive_path]), compresslevel=9)


def _validate_output(
    output_path: Path,
    targets: list[TargetRecord],
) -> None:
    """回读 V2 包，确认目标集合和操作数量没有漂移。"""
    with zipfile.ZipFile(output_path) as archive:
        manifest = json.loads(archive.read(MANIFEST_PATH).decode("utf-8-sig"))
        patch = json.loads(archive.read(SEMANTIC_PATCH_PATH).decode("utf-8-sig"))
    operations = patch["targets"][0]["operations"]
    expected_count = len(targets) * 11
    if manifest.get("id") != PACKAGE_ID or len(operations) != expected_count:
        raise ValueError("V2 成品 manifest 或操作数量校验失败")
    docking_values = [
        operation.get("value")
        for operation in operations
        if operation.get("path") == "docking_child_data"
    ]
    if len(docking_values) != len(targets) or any(
        value != LIGHTNING_DOCKING_CHILD_DATA for value in docking_values
    ):
        raise ValueError("V2 成品没有统一使用原生 Marni 雷电长枪 docking")
    gimmick_values = {
        operation.get("value")
        for operation in operations
        if operation.get("path") == "gimmick_info"
    }
    if gimmick_values != {LIGHTNING_GIMMICK_KEY}:
        raise ValueError("V2 成品没有统一使用原生 Marni 雷电长枪 gimmick")
    target_identities = {
        (operation["selector"].get("key"), operation["selector"].get("string_key"))
        for operation in operations
    }
    expected_identities = {(target.key, target.string_key) for target in targets}
    if target_identities != expected_identities:
        raise ValueError("V2 成品包含未声明目标或缺少目标")


def _json_bytes(document: dict[str, Any]) -> bytes:
    """采用稳定排序的 UTF-8 JSON 编码。"""
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"


def _sha256(path: Path) -> str:
    """计算发布或配方文件 SHA-256。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_args() -> argparse.Namespace:
    """读取游戏、配方和输出参数。"""
    parser = argparse.ArgumentParser(description="生成无特殊效果双手长柄武器雷电 V2 测试包")
    parser.add_argument(
        "--game-dir",
        type=Path,
        default=Path(r"G:\SteamLibrary\steamapps\common\Crimson Desert"),
    )
    parser.add_argument("--recipe", type=Path, default=DEFAULT_RECIPE_PACKAGE)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(r"G:\SteamLibrary\steamapps\common\Crimson Desert\mods") / OUTPUT_FILE_NAME,
    )
    return parser.parse_args()


def main() -> int:
    """执行 V2 构建并输出目标与哈希。"""
    args = _parse_args()
    try:
        targets = build_v2_package(args.game_dir, args.recipe, args.output)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        print(f"V2 构建失败：{exc}", file=sys.stderr)
        return 1
    print(
        f"已生成 {args.output.name}: targets={len(targets)}, operations={len(targets) * 11}, "
        f"sha256={_sha256(args.output)}"
    )
    for target in targets:
        print(f"- {target.display_name}: {target.key}/{target.string_key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
