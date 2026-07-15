"""生成“女性基础移动、男性战斗资源”实验版 cdmod。

本工具从当前游戏原版归档读取 PHW 默认动作分支，再只把其中明确属于
alert、dash 与 justavoid 的资源替换为配对的 PHM 资源。普通站立、走路、
慢跑和奔跑资源保持原版 PHW，不修改 PAAC 图结构。
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from cdmm.archive.pamt import parse_pamt
from cdmm.common.models import PazEntry
from cdmm.services.cdmod_converter import (
    CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE,
    CDMOD_FORMAT_NAME,
    CDMOD_FORMAT_VERSION,
    CDMOD_LEGACY_JSON_COMPONENT_TYPE,
    _write_cdmod_zip,
)
from cdmm.services.cdmod_package import load_cdmod_package
from cdmm.services.json_loader import extract_plaintext

# 当前实测游戏版本的 PHW Basic_Lower 中应存在 34 条基础战斗资源引用。
EXPECTED_COMBAT_REFERENCE_COUNT = 34

# 只选择基础图里的战斗/警戒动作，不触碰普通 locomotion 循环。
COMBAT_REFERENCE_TOKENS = (
    "_alert_",
    "_dash_",
    "_justavoid_",
    "_avoid_",
    "_roll_",
    "_guard_",
    "_def_",
)

# 男女命名不完全对称的动作必须按 PAAC 中相邻的原版配对关系指定。
CUSTOM_MALE_BASENAME_BY_FEMALE = {
    "cd_phw_basic_00_01_alert_nor_std_idle_end_00.paa":
        "cd_phm_basic_00_00_alert_nor_std_to_0000idle_00.paa",
    "cd_phw_basic_00_01_alert_nor_std_idle_stt_00.paa":
        "cd_phm_basic_00_00_nor_std_to_0000alert_00.paa",
    "cd_phw_basic_00_01_alert_base_move_turn90l_00.paa":
        "cd_phm_basic_00_00_alert_base_move_turn90l_00.paa",
    "cd_phw_basic_00_01_alert_base_move_turn180l_00.paa":
        "cd_phm_basic_00_00_alert_base_move_turn180l_00.paa",
    "cd_phw_basic_00_01_alert_base_move_turn90r_00.paa":
        "cd_phm_basic_00_00_alert_base_move_turn90r_00.paa",
    "cd_phw_basic_00_01_alert_base_move_turn180r_00.paa":
        "cd_phm_basic_00_00_alert_base_move_turn180r_00.paa",
    "cd_phw_basic_00_01_alert_nor_move_walkfast_f_end_footr_00.paa":
        "cd_phm_basic_00_01_alert_nor_move_walkfast_f_endr_00.paa",
    "cd_phw_basic_00_01_alert_nor_move_walkfast_f_end_footl_00.paa":
        "cd_phm_basic_00_01_alert_nor_move_walkfast_f_endl_00.paa",
    "cd_phw_basic_00_01_alert_nor_move_run_f_end_footl_00.paa":
        "cd_phm_basic_00_01_alert_nor_move_run_f_endr_00.paa",
    "cd_phw_basic_00_01_alert_nor_move_run_f_end_footr_00.paa":
        "cd_phm_basic_00_01_alert_nor_move_run_f_endl_00.paa",
}

# 警戒移动混合树决定持续战斗移动时的下半身姿势。
ALERT_MOTIONBLENDING_ALIASES = (
    (
        "character/binary/motionblending/phw_locomotion/alert_move_lv2.motionblending",
        "character/binary/motionblending/phm_locomotion/alert_move_lv2.motionblending",
    ),
    (
        "character/binary/motionblending/phw_locomotion/alert_move_lv3.motionblending",
        "character/binary/motionblending/phm_locomotion/alert_move_lv3.motionblending",
    ),
)

# 女性默认动作入口的四个 characterinfo 固定长度补丁。
DEFAULT_ACTION_CHANGES = (
    (464, "Kliff"),
    (4503, "Kliff_Clone"),
    (8414, "Kliff_AI"),
    (43321, "PlayerAll"),
)

MOD_NAME = "Female Locomotion - Native Male Combat Resources"
MOD_VERSION = "1.3-test"
OUTPUT_FILE_NAME = f"{MOD_NAME} v{MOD_VERSION}.cdmod"


def _full_virtual_path(entry: PazEntry) -> str:
    """还原 PAMT folder record 与扁平 entry basename 组成的完整路径。"""
    basename = Path(entry.path.replace("\\", "/")).name
    resolved_dir = entry.resolved_dir_path.replace("\\", "/").strip("/")
    return f"{resolved_dir}/{basename}".strip("/") if resolved_dir else entry.path


def _build_entry_index(game_dir: Path, pamt_dir: str) -> dict[str, PazEntry]:
    """建立指定原版 PAMT 的唯一完整路径索引。"""
    entries = parse_pamt(game_dir / pamt_dir / "0.pamt")
    index: dict[str, PazEntry] = {}
    for entry in entries:
        key = _full_virtual_path(entry).casefold()
        if key in index:
            raise ValueError(f"{pamt_dir} 存在重复完整路径：{key}")
        index[key] = entry
    return index


def _require_entry(index: dict[str, PazEntry], path: str) -> PazEntry:
    """读取唯一原版资源，缺失时停止生成，避免猜测更新后的资源关系。"""
    entry = index.get(path.casefold())
    if entry is None:
        raise ValueError(f"原版归档缺少资源：{path}")
    return entry


def _extract_combat_references(chart_data: bytes) -> tuple[str, ...]:
    """从 Basic_Lower 恢复 PHW 基础战斗 PAA 引用。"""
    references: list[str] = []
    for match in re.finditer(rb"[A-Za-z0-9_./\\-]{8,}", chart_data):
        raw = match.group().decode("ascii", errors="ignore").replace("\\", "/")
        start = raw.casefold().find("1_pc/2_phw/")
        if start < 0:
            continue
        reference = raw[start:]
        lowered = reference.casefold()
        if not lowered.endswith(".paa"):
            continue
        if not any(token in lowered for token in COMBAT_REFERENCE_TOKENS):
            continue
        if reference not in references:
            references.append(reference)
    if len(references) != EXPECTED_COMBAT_REFERENCE_COUNT:
        raise ValueError(
            "PHW 基础战斗引用数量变化："
            f"预期 {EXPECTED_COMBAT_REFERENCE_COUNT}，实际 {len(references)}"
        )
    return tuple(references)


def _male_reference(female_reference: str) -> str:
    """按原版相邻配对关系解析女性战斗动作对应的男性动作。"""
    female_basename = female_reference.rsplit("/", 1)[-1]
    male_basename = CUSTOM_MALE_BASENAME_BY_FEMALE.get(female_basename)
    if male_basename is None:
        male_basename = female_basename.replace("cd_phw_", "cd_phm_", 1)
    return f"1_pc/1_phm/{male_basename}"


def _add_file_replacement(
    documents: dict[str, dict[str, object] | bytes],
    replacements: list[dict[str, object]],
    *,
    target: str,
    source_entry: PazEntry,
    pamt_dir: str,
) -> None:
    """把原版 source 明文登记为 target 的完整资源替换载荷。"""
    content, _ = extract_plaintext(source_entry)
    payload_path = f"assets/{len(replacements):03d}/{Path(target).name}"
    documents[payload_path] = content
    replacements.append(
        {
            "allow_new": False,
            "allow_table_replace": False,
            "pamt_dir": pamt_dir,
            "payload": payload_path,
            "sha256": hashlib.sha256(content).hexdigest(),
            "size": len(content),
            "target": target,
        }
    )


def _legacy_patch_document() -> dict[str, object]:
    """生成选择 PHW 默认动作入口的传统固定长度补丁。"""
    return {
        "author": "Khione, Slinky, CDMM",
        "description": "Selects the PHW default branch; combat resources are replaced separately.",
        "name": MOD_NAME,
        "patches": [
            {
                "changes": [
                    {
                        "label": f"{character} _defaultActionActionIndex -> PHW selector bytes",
                        "offset": offset,
                        "original": "00000000",
                        "patched": "A114B74C",
                    }
                    for offset, character in DEFAULT_ACTION_CHANGES
                ],
                "game_file": "gamedata/characterinfo.pabgb",
            }
        ],
        "version": MOD_VERSION,
    }


def build_mod(game_dir: Path, output_path: Path) -> Path:
    """从原版资源构建自包含 v1.3 测试包。"""
    index_0009 = _build_entry_index(game_dir, "0009")
    index_0010 = _build_entry_index(game_dir, "0010")
    basic_lower = _require_entry(
        index_0010,
        "actionchart/bin__/loweraction/1_pc/1_phm/basic_lower.paac",
    )
    chart_data, _ = extract_plaintext(basic_lower)
    references = _extract_combat_references(chart_data)

    documents: dict[str, dict[str, object] | bytes] = {}
    replacements: list[dict[str, object]] = []
    for female_reference in references:
        male_reference = _male_reference(female_reference)
        female_motion = f"character/motion/{female_reference}"
        male_motion = f"character/motion/{male_reference}"
        _require_entry(index_0009, female_motion)
        _add_file_replacement(
            documents,
            replacements,
            target=female_motion,
            source_entry=_require_entry(index_0009, male_motion),
            pamt_dir="0009",
        )

        female_meta = f"actionchart/bin__/animmeta/{female_reference}_metabin"
        male_meta = f"actionchart/bin__/animmeta/{male_reference}_metabin"
        _require_entry(index_0010, female_meta)
        _add_file_replacement(
            documents,
            replacements,
            target=female_meta,
            source_entry=_require_entry(index_0010, male_meta),
            pamt_dir="0010",
        )

    for female_target, male_source in ALERT_MOTIONBLENDING_ALIASES:
        _require_entry(index_0009, female_target)
        _add_file_replacement(
            documents,
            replacements,
            target=female_target,
            source_entry=_require_entry(index_0009, male_source),
            pamt_dir="0009",
        )

    replacement_document = {"schema": 1, "files": replacements}
    legacy_document = _legacy_patch_document()
    manifest = {
        "author": "Khione, Slinky, CDMM",
        "components": [
            {"path": "patches/legacy.json", "type": CDMOD_LEGACY_JSON_COMPONENT_TYPE},
            {
                "file_count": len(replacements),
                "path": "files/replacements.json",
                "type": CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE,
            },
        ],
        "dependencies": [],
        "description": (
            "Keeps PHW idle/walk/run through the PHW default branch, while aliasing only "
            "Basic_Lower alert, dash and justavoid resources to paired native PHM assets."
        ),
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": "cdmm.female-locomotion-native-male-combat-resources",
        "name": MOD_NAME,
        "source": {
            "combat_reference_count": len(references),
            "file_replacement_count": len(replacements),
            "format": "controlled-phw-combat-resource-alias",
        },
        "version": MOD_VERSION,
    }
    documents["manifest.json"] = manifest
    documents["patches/legacy.json"] = legacy_document
    documents["files/replacements.json"] = replacement_document
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_cdmod_zip(output_path, documents)

    package = load_cdmod_package(output_path)
    file_count = sum(len(patch.files) for patch in package.file_patches)
    if len(package.legacy_json_patches) != 1 or file_count != len(replacements):
        raise ValueError("生成后的 cdmod 严格解析结果与预期不一致")
    return output_path


def main() -> int:
    """生成游戏 mods 目录下的新测试包。"""
    game_dir = Path(r"G:\SteamLibrary\steamapps\common\Crimson Desert")
    output_path = game_dir / "mods" / OUTPUT_FILE_NAME
    result = build_mod(game_dir, output_path)
    digest = hashlib.sha256(result.read_bytes()).hexdigest()
    package = load_cdmod_package(result)
    file_count = sum(len(patch.files) for patch in package.file_patches)
    print(f"已生成：{result}")
    print(f"完整资源替换：{file_count}")
    print(f"SHA-256：{digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
