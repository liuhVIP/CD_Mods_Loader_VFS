"""生成“v1.5 纯女性持续走路 + 配对女性起步”测试包。

原生 PHW 正常是因为共享 Basic_Lower 在 lookup_84=phw 时会选择完整 PHW 节点链；
v1.5 保持 lookup_84=0，只替换持续 walk 内容，因此第四步状态交接仍会重置。
真正取消重置需要安全解析 PAAC transition/phase 节点。本工具先实现用户要求的视觉无缝
方案：完整保留 v1.5，再把男性分支使用的 6 个普通起步 PAA 与配对 metabin 替换为
Basic_Lower 字符串表中紧邻的一一对应原版 PHW 起步资源。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from cdmm.services.cdmod_converter import (
    CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE,
    CDMOD_FORMAT_NAME,
    CDMOD_FORMAT_VERSION,
    _write_cdmod_zip,
)
from cdmm.services.cdmod_package import load_cdmod_package
from cdmm.services.json_loader import extract_plaintext
from cdmm.tools.build_female_locomotion_male_combat_graph_mod import (
    TARGET_PAAC,
    _find_entry,
    _patch_basic_lower,
)
from cdmm.tools.build_female_walk_single_branch_mod import (
    PAAC_PAMT_DIR,
    WALK_BLEND_PAMT_DIR,
    WALK_BLEND_SOURCE,
    WALK_BLEND_TARGET,
    _file_document,
    _validate_walk_blends,
)

# 原版 PAA 与配对动画 metadata 所在目录。
PAA_PAMT_DIR = "0009"
METABIN_PAMT_DIR = "0010"

# 动作路径在 PAA 与 metabin PAMT 中的固定前缀。
PAA_PATH_PREFIX = "character/motion/"
METABIN_PATH_PREFIX = "actionchart/bin__/animmeta/"

MOD_NAME = "Female Walk Matched Start - Native Male Combat"
MOD_VERSION = "2.1-test"
OUTPUT_FILE_NAME = f"{MOD_NAME} v{MOD_VERSION}.cdmod"


@dataclass(frozen=True)
class StartMotionPair:
    """一条男性起步目标与原版女性配对来源。"""

    male_reference: str
    female_reference: str


# Basic_Lower 字符串表中的顺序是 6 条 PHM 起步后紧邻 6 条 PHW 起步，逐项对应。
START_MOTION_PAIRS = (
    StartMotionPair(
        "1_pc/1_phm/cd_phm_basic_00_00_nor_move_walk_f_footr_start_02.paa",
        "1_pc/2_phw/cd_phw_basic_00_00_nor_move_walk_stt_r_00.paa",
    ),
    StartMotionPair(
        "1_pc/1_phm/cd_phm_basic_00_00_nor_move_walk_turn90r_footr_start_02.paa",
        "1_pc/2_phw/cd_phw_basic_00_00_nor_move_walk_turn90r_stt_00.paa",
    ),
    StartMotionPair(
        "1_pc/1_phm/cd_phm_basic_00_00_nor_move_walk_turn180r_footr_start_02.paa",
        "1_pc/2_phw/cd_phw_basic_00_00_nor_move_walk_turn180r_stt_00.paa",
    ),
    StartMotionPair(
        "1_pc/1_phm/cd_phm_basic_00_00_nor_move_walk_f_footl_start_02.paa",
        "1_pc/2_phw/cd_phw_basic_00_00_nor_move_walk_stt_l_00.paa",
    ),
    StartMotionPair(
        "1_pc/1_phm/cd_phm_basic_00_00_nor_move_walk_turn90l_footl_start_02.paa",
        "1_pc/2_phw/cd_phw_basic_00_00_nor_move_walk_turn90l_stt_00.paa",
    ),
    StartMotionPair(
        "1_pc/1_phm/cd_phm_basic_00_00_nor_move_walk_turn180l_footl_start_02.paa",
        "1_pc/2_phw/cd_phw_basic_00_00_nor_move_walk_turn180l_stt_00.paa",
    ),
)


def _paa_target(reference: str) -> str:
    """把 PAAC 动作引用转换为 PAA 游戏路径。"""
    return f"{PAA_PATH_PREFIX}{reference}"


def _metabin_target(reference: str) -> str:
    """把 PAAC 动作引用转换为 metabin 游戏路径。"""
    return f"{METABIN_PATH_PREFIX}{reference}_metabin"


def _validate_start_pairs(chart_data: bytes) -> None:
    """确认六组男女起步引用唯一存在并保持原版相邻分组顺序。"""
    male_offsets: list[int] = []
    female_offsets: list[int] = []
    for pair in START_MOTION_PAIRS:
        male = pair.male_reference.encode("ascii")
        female = pair.female_reference.encode("ascii")
        if chart_data.count(male) != 1 or chart_data.count(female) != 1:
            raise ValueError(f"起步动作配对数量异常：{pair}")
        male_offsets.append(chart_data.index(male))
        female_offsets.append(chart_data.index(female))
    if male_offsets != sorted(male_offsets) or female_offsets != sorted(female_offsets):
        raise ValueError("起步动作配对顺序发生变化")
    if max(male_offsets) >= min(female_offsets):
        raise ValueError("PHM/PHW 起步动作不再是相邻分组")


def build_mod(game_dir: Path, output_path: Path) -> Path:
    """从当前原版资源构建 v1.5 + 六组女性起步的 v2.1。"""
    basic_lower_entry = _find_entry(game_dir, PAAC_PAMT_DIR, TARGET_PAAC)
    mixed_walk_entry = _find_entry(
        game_dir,
        WALK_BLEND_PAMT_DIR,
        WALK_BLEND_TARGET,
    )
    female_walk_entry = _find_entry(
        game_dir,
        WALK_BLEND_PAMT_DIR,
        WALK_BLEND_SOURCE,
    )
    vanilla_paac, _ = extract_plaintext(basic_lower_entry)
    mixed_walk, _ = extract_plaintext(mixed_walk_entry)
    female_walk, _ = extract_plaintext(female_walk_entry)
    _validate_start_pairs(vanilla_paac)
    _validate_walk_blends(mixed_walk, female_walk)
    patched_paac = _patch_basic_lower(vanilla_paac)

    documents: dict[str, dict[str, object] | bytes] = {}
    replacements: list[dict[str, object]] = []
    expected_content: dict[str, bytes] = {
        TARGET_PAAC: patched_paac,
        WALK_BLEND_TARGET: female_walk,
    }
    paac_payload = "assets/000/basic_lower.paac"
    walk_payload = "assets/001/basic_move_walk.motionblending"
    replacements.extend(
        (
            _file_document(
                target=TARGET_PAAC,
                pamt_dir=PAAC_PAMT_DIR,
                payload_path=paac_payload,
                content=patched_paac,
            ),
            _file_document(
                target=WALK_BLEND_TARGET,
                pamt_dir=WALK_BLEND_PAMT_DIR,
                payload_path=walk_payload,
                content=female_walk,
            ),
        )
    )
    documents[paac_payload] = patched_paac
    documents[walk_payload] = female_walk

    for pair_index, pair in enumerate(START_MOTION_PAIRS, start=2):
        for kind, pamt_dir, target_builder in (
            ("paa", PAA_PAMT_DIR, _paa_target),
            ("metabin", METABIN_PAMT_DIR, _metabin_target),
        ):
            target = target_builder(pair.male_reference)
            source = target_builder(pair.female_reference)
            target_entry = _find_entry(game_dir, pamt_dir, target)
            source_entry = _find_entry(game_dir, pamt_dir, source)
            extract_plaintext(target_entry)
            content, _ = extract_plaintext(source_entry)
            payload_path = f"assets/{pair_index:03d}/{kind}_{Path(target).name}"
            replacements.append(
                _file_document(
                    target=target,
                    pamt_dir=pamt_dir,
                    payload_path=payload_path,
                    content=content,
                )
            )
            documents[payload_path] = content
            expected_content[target] = content

    manifest = {
        "author": "Khione, Slinky, CDMM",
        "components": [
            {
                "file_count": len(replacements),
                "path": "files/replacements.json",
                "type": CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE,
            }
        ],
        "dependencies": [],
        "description": (
            "Keeps the v1.5 pure-PHW continuous walk baseline and replaces only "
            "the six male normal-walk start clips plus matching metadata with "
            "their adjacent native PHW pairs, making the fourth-step handoff visual."
        ),
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": "cdmm.female-walk-matched-start-native-male-combat",
        "name": MOD_NAME,
        "source": {
            "format": "v1.5-plus-six-native-phw-start-pairs",
            "paac_target": TARGET_PAAC,
            "start_pair_count": len(START_MOTION_PAIRS),
            "walk_blend_source": WALK_BLEND_SOURCE,
            "walk_blend_target": WALK_BLEND_TARGET,
        },
        "version": MOD_VERSION,
    }
    documents["manifest.json"] = manifest
    documents["files/replacements.json"] = {
        "schema": 1,
        "files": replacements,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_cdmod_zip(output_path, documents)

    package = load_cdmod_package(output_path)
    files = [file for patch in package.file_patches for file in patch.files]
    parsed_content = {file.target: file.content for file in files}
    if package.legacy_json_patches or len(files) != len(replacements):
        raise ValueError("生成后的 v2.1 cdmod 组件数量异常")
    if parsed_content != expected_content:
        raise ValueError("生成后的 v2.1 资源载荷与预期不一致")
    return output_path


def main() -> int:
    """生成游戏 mods 目录下的新测试包。"""
    game_dir = Path(r"G:\SteamLibrary\steamapps\common\Crimson Desert")
    output_path = game_dir / "mods" / OUTPUT_FILE_NAME
    result = build_mod(game_dir, output_path)
    print(f"已生成：{result}")
    print(f"SHA-256：{hashlib.sha256(result.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
