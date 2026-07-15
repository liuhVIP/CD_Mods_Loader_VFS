"""生成“纯女性走路混合树、原生男性战斗链”单变量测试包。

v1.4 已证明仅重定向 Basic_Lower 的普通移动路径仍会出现男性起步和周期卡步。
进一步核验发现原版 PHW basic_move_walk 本身同时携带 PHM 与 PHW 动作样本；
本工具保留 v1.4 的五处等长 PAAC 修改，只把该混合 walk 文件替换为原生纯 PHW
basic_move_lv1，避免修改未知 PAAC condition 或战斗资源。
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

# v1.4 已审计的男性 Basic_Lower 路径修改仍作为本版状态机底座。
PAAC_PAMT_DIR = "0010"

# 原版 basic_move_walk 同时包含 PHM/PHW 样本，是本版唯一新增替换目标。
WALK_BLEND_TARGET = (
    "character/binary/motionblending/phw_locomotion/"
    "basic_move_walk.motionblending"
)

# 原版 basic_move_lv1 只引用 PHW 走路和左右 180 度转身动作。
WALK_BLEND_SOURCE = (
    "character/binary/motionblending/phw_locomotion/"
    "basic_move_lv1.motionblending"
)
WALK_BLEND_PAMT_DIR = "0009"

# 混合树与纯女性树必须满足的来源断言，游戏更新后不允许静默生成。
MALE_SAMPLE_PREFIX = b"1_pc/1_phm/"
FEMALE_SAMPLE_PREFIX = b"1_pc/2_phw/"
FEMALE_WALK_SAMPLE = b"cd_phw_basic_00_00_nor_move_walk_f_ing_00.paa"

MOD_NAME = "Female Walk Single Branch - Native Male Combat"
MOD_VERSION = "1.5-test"
OUTPUT_FILE_NAME = f"{MOD_NAME} v{MOD_VERSION}.cdmod"


def _validate_walk_blends(mixed_walk: bytes, female_walk: bytes) -> None:
    """确认目标仍为混合树，替代来源仍为纯女性走路树。"""
    if MALE_SAMPLE_PREFIX not in mixed_walk or FEMALE_SAMPLE_PREFIX not in mixed_walk:
        raise ValueError("原版 basic_move_walk 不再同时包含 PHM/PHW 样本")
    if MALE_SAMPLE_PREFIX in female_walk:
        raise ValueError("basic_move_lv1 意外包含 PHM 样本")
    if FEMALE_SAMPLE_PREFIX not in female_walk or FEMALE_WALK_SAMPLE not in female_walk:
        raise ValueError("basic_move_lv1 缺少预期 PHW 走路样本")


def _file_document(
    *,
    target: str,
    pamt_dir: str,
    payload_path: str,
    content: bytes,
) -> dict[str, object]:
    """构造一个经过哈希约束的完整资源替换声明。"""
    return {
        "allow_new": False,
        "allow_table_replace": False,
        "pamt_dir": pamt_dir,
        "payload": payload_path,
        "sha256": hashlib.sha256(content).hexdigest(),
        "size": len(content),
        "target": target,
    }


def build_mod(game_dir: Path, output_path: Path) -> Path:
    """从当前原版 PAAC 与 motionblending 构建 v1.5 测试包。"""
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
    _validate_walk_blends(mixed_walk, female_walk)
    patched_paac = _patch_basic_lower(vanilla_paac)

    paac_payload = "assets/000/basic_lower.paac"
    walk_payload = "assets/001/basic_move_walk.motionblending"
    replacements = [
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
    ]
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
            "Keeps the v1.4 male state machine and native male combat chain, "
            "but replaces the mixed PHM/PHW walk blend with the native "
            "female-only PHW level-1 walk blend."
        ),
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": "cdmm.female-walk-single-branch-native-male-combat",
        "name": MOD_NAME,
        "source": {
            "format": "same-length-paac-plus-native-walk-blend-alias",
            "paac_target": TARGET_PAAC,
            "walk_blend_source": WALK_BLEND_SOURCE,
            "walk_blend_target": WALK_BLEND_TARGET,
        },
        "version": MOD_VERSION,
    }
    documents: dict[str, dict[str, object] | bytes] = {
        "manifest.json": manifest,
        "files/replacements.json": {"schema": 1, "files": replacements},
        paac_payload: patched_paac,
        walk_payload: female_walk,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_cdmod_zip(output_path, documents)

    package = load_cdmod_package(output_path)
    files = [file for patch in package.file_patches for file in patch.files]
    parsed_content = {file.target: file.content for file in files}
    if package.legacy_json_patches or len(files) != 2:
        raise ValueError("生成后的 v1.5 cdmod 组件数量异常")
    if parsed_content.get(TARGET_PAAC) != patched_paac:
        raise ValueError("生成后的 v1.5 PAAC 载荷与预期不一致")
    if parsed_content.get(WALK_BLEND_TARGET) != female_walk:
        raise ValueError("生成后的 v1.5 走路混合树载荷与预期不一致")
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
