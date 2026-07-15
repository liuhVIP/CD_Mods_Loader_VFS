"""生成“纯女性走路与三步过渡、原生男性战斗链”测试包。

v1.5 已消除连续两步一卡，只剩固定四步一卡。Basic_Lower 的 motionblending
字符串表前两项是男性 threestepleft/threestepright，原版又不存在对应的 PHW
基础三步混合树。本工具完整保留 v1.5，并只让这两个三步过渡目标复用同一份原生
纯 PHW basic_move_lv1，用于验证“三步过渡 + 下一步切回”是否造成四步卡点。
"""

from __future__ import annotations

import hashlib
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
    MALE_SAMPLE_PREFIX,
    PAAC_PAMT_DIR,
    WALK_BLEND_PAMT_DIR,
    WALK_BLEND_SOURCE,
    WALK_BLEND_TARGET,
    _file_document,
    _validate_walk_blends,
)

# Basic_Lower 中仍会进入的男性左右三步过渡目标。
THREE_STEP_TARGETS = (
    "character/binary/motionblending/phm_locomotion/"
    "threestepleft.motionblending",
    "character/binary/motionblending/phm_locomotion/"
    "threestepright.motionblending",
)

# 三步目标必须仍包含男性三步样本，游戏更新后不允许猜测替换。
THREE_STEP_SAMPLE_TOKEN = b"_3step_"

MOD_NAME = "Female Walk Continuous Transition - Native Male Combat"
MOD_VERSION = "1.6-test"
OUTPUT_FILE_NAME = f"{MOD_NAME} v{MOD_VERSION}.cdmod"


def _validate_three_step_blends(three_step_blends: tuple[bytes, ...]) -> None:
    """确认两个目标仍是男性三步过渡混合树。"""
    if len(three_step_blends) != len(THREE_STEP_TARGETS):
        raise ValueError("三步过渡目标数量异常")
    for index, content in enumerate(three_step_blends):
        if MALE_SAMPLE_PREFIX not in content or THREE_STEP_SAMPLE_TOKEN not in content:
            raise ValueError(f"第 {index + 1} 个三步目标缺少预期 PHM 三步样本")


def build_mod(game_dir: Path, output_path: Path) -> Path:
    """从当前原版资源构建 v1.6 单变量测试包。"""
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
    three_step_entries = tuple(
        _find_entry(game_dir, WALK_BLEND_PAMT_DIR, target)
        for target in THREE_STEP_TARGETS
    )

    vanilla_paac, _ = extract_plaintext(basic_lower_entry)
    mixed_walk, _ = extract_plaintext(mixed_walk_entry)
    female_walk, _ = extract_plaintext(female_walk_entry)
    three_step_blends = tuple(
        extract_plaintext(entry)[0] for entry in three_step_entries
    )
    _validate_walk_blends(mixed_walk, female_walk)
    _validate_three_step_blends(three_step_blends)
    patched_paac = _patch_basic_lower(vanilla_paac)

    replacements: list[dict[str, object]] = []
    documents: dict[str, dict[str, object] | bytes] = {}
    payloads = (
        (TARGET_PAAC, PAAC_PAMT_DIR, "assets/000/basic_lower.paac", patched_paac),
        (
            WALK_BLEND_TARGET,
            WALK_BLEND_PAMT_DIR,
            "assets/001/basic_move_walk.motionblending",
            female_walk,
        ),
        (
            THREE_STEP_TARGETS[0],
            WALK_BLEND_PAMT_DIR,
            "assets/002/threestepleft.motionblending",
            female_walk,
        ),
        (
            THREE_STEP_TARGETS[1],
            WALK_BLEND_PAMT_DIR,
            "assets/003/threestepright.motionblending",
            female_walk,
        ),
    )
    for target, pamt_dir, payload_path, content in payloads:
        replacements.append(
            _file_document(
                target=target,
                pamt_dir=pamt_dir,
                payload_path=payload_path,
                content=content,
            )
        )
        documents[payload_path] = content

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
            "Keeps the v1.5 female-only walk and native male combat baseline. "
            "Only the PHM left/right three-step transition targets are aliased "
            "to the same native female-only PHW level-1 walk blend."
        ),
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": "cdmm.female-walk-continuous-transition-native-male-combat",
        "name": MOD_NAME,
        "source": {
            "format": "v1.5-plus-native-walk-three-step-alias",
            "paac_target": TARGET_PAAC,
            "three_step_targets": list(THREE_STEP_TARGETS),
            "walk_blend_source": WALK_BLEND_SOURCE,
            "walk_blend_target": WALK_BLEND_TARGET,
        },
        "version": MOD_VERSION,
    }
    documents["manifest.json"] = manifest
    documents["files/replacements.json"] = {"schema": 1, "files": replacements}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_cdmod_zip(output_path, documents)

    package = load_cdmod_package(output_path)
    files = [file for patch in package.file_patches for file in patch.files]
    parsed_content = {file.target: file.content for file in files}
    expected_targets = {target for target, *_rest in payloads}
    if package.legacy_json_patches or set(parsed_content) != expected_targets:
        raise ValueError("生成后的 v1.6 cdmod 目标集合异常")
    if parsed_content[TARGET_PAAC] != patched_paac:
        raise ValueError("生成后的 v1.6 PAAC 载荷与预期不一致")
    for target in (WALK_BLEND_TARGET, *THREE_STEP_TARGETS):
        if parsed_content[target] != female_walk:
            raise ValueError(f"生成后的 v1.6 纯女性过渡载荷不一致：{target}")
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
