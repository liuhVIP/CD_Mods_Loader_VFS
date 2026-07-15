"""生成“纯女性八方向同步走路 + 原生男性战斗链”测试包。

三阶段实机结果已经把两层卡步拆开：原版混合 basic_move_walk 会连续两步一卡，
v1.5 的纯女性 basic_move_lv1 消除了两步层，但因缺少 sync 而固定四步一卡；
v1.9 恢复原版混合树并替换样本后，两步层立即恢复，证明问题属于混合树结构。
本工具保持 v1.5 的男性战斗底座，只把 walk 槽内容换成游戏原版唯一符合
“PHW 普通 basic、纯女性八方向 walk、loop+sync”的 fist_stride 树。
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
    FEMALE_SAMPLE_PREFIX,
    MALE_SAMPLE_PREFIX,
    PAAC_PAMT_DIR,
    WALK_BLEND_PAMT_DIR,
    WALK_BLEND_TARGET,
    _file_document,
)
from cdmm.tools.build_female_walk_sync_probe_mod import (
    LOOP_DECLARATION,
    SYNC_DECLARATION,
)

# 原版唯一使用 basic_01_01 女性普通八方向 walk 的 loop+sync 树。
SYNCED_EIGHT_DIRECTION_SOURCE = (
    "character/binary/motionblending/phw_locomotion/"
    "fist_stride.motionblending"
)

# 八方向样本必须全部存在，避免游戏更新后静默生成残缺树。
EXPECTED_DIRECTIONS = ("f", "b", "l", "r", "fl", "fr", "bl", "br")
EXPECTED_SAMPLE_TEMPLATE = (
    b"1_pc/2_phw/cd_phw_basic_01_01_nor_move_walk_%s_ing_00.paa"
)

# 本版明确排除 v1.7 使用的跑步样本和其他速度层。
FORBIDDEN_SAMPLE_TOKENS = (b"_move_run_", b"_move_walkfast_")

MOD_NAME = "Female Walk Synced Eight Direction - Native Male Combat"
MOD_VERSION = "2.0-test"
OUTPUT_FILE_NAME = f"{MOD_NAME} v{MOD_VERSION}.cdmod"


def _expected_samples() -> tuple[bytes, ...]:
    """返回原版女性八方向普通走路样本引用。"""
    return tuple(
        EXPECTED_SAMPLE_TEMPLATE % direction.encode("ascii")
        for direction in EXPECTED_DIRECTIONS
    )


def _validate_synced_walk_source(mixed_walk: bytes, source: bytes) -> None:
    """确认目标仍为混合树，来源仍为纯女性八方向同步走路树。"""
    if MALE_SAMPLE_PREFIX not in mixed_walk or FEMALE_SAMPLE_PREFIX not in mixed_walk:
        raise ValueError("原版 basic_move_walk 不再是预期 PHM/PHW 混合树")
    if MALE_SAMPLE_PREFIX in source or FEMALE_SAMPLE_PREFIX not in source:
        raise ValueError("八方向同步来源不再是纯 PHW 树")
    for declaration in (LOOP_DECLARATION, SYNC_DECLARATION):
        if source.count(declaration) != 1:
            raise ValueError(f"八方向同步来源声明异常：{declaration!r}")
    for sample in _expected_samples():
        if source.count(sample) != 1:
            raise ValueError(f"八方向同步来源缺少样本：{sample!r}")
    for token in FORBIDDEN_SAMPLE_TOKENS:
        if token in source:
            raise ValueError(f"八方向同步来源混入其他速度样本：{token!r}")


def build_mod(game_dir: Path, output_path: Path) -> Path:
    """从当前原版 PAAC 和纯女性八方向同步树构建 v2.0。"""
    basic_lower_entry = _find_entry(game_dir, PAAC_PAMT_DIR, TARGET_PAAC)
    mixed_walk_entry = _find_entry(
        game_dir,
        WALK_BLEND_PAMT_DIR,
        WALK_BLEND_TARGET,
    )
    source_entry = _find_entry(
        game_dir,
        WALK_BLEND_PAMT_DIR,
        SYNCED_EIGHT_DIRECTION_SOURCE,
    )
    vanilla_paac, _ = extract_plaintext(basic_lower_entry)
    mixed_walk, _ = extract_plaintext(mixed_walk_entry)
    synced_walk, _ = extract_plaintext(source_entry)
    _validate_synced_walk_source(mixed_walk, synced_walk)
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
            content=synced_walk,
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
            "Keeps the native male combat graph while placing the game's only "
            "native pure-PHW eight-direction loop+sync walk tree in the normal "
            "walk slot. It contains no run or walkfast samples."
        ),
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": "cdmm.female-walk-synced-eight-direction-native-male-combat",
        "name": MOD_NAME,
        "source": {
            "direction_count": len(EXPECTED_DIRECTIONS),
            "format": "v1.5-native-synced-eight-direction-walk",
            "paac_target": TARGET_PAAC,
            "walk_blend_source": SYNCED_EIGHT_DIRECTION_SOURCE,
            "walk_blend_target": WALK_BLEND_TARGET,
        },
        "version": MOD_VERSION,
    }
    documents: dict[str, dict[str, object] | bytes] = {
        "manifest.json": manifest,
        "files/replacements.json": {"schema": 1, "files": replacements},
        paac_payload: patched_paac,
        walk_payload: synced_walk,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_cdmod_zip(output_path, documents)

    package = load_cdmod_package(output_path)
    files = [file for patch in package.file_patches for file in patch.files]
    parsed_content = {file.target: file.content for file in files}
    if package.legacy_json_patches or len(files) != 2:
        raise ValueError("生成后的 v2.0 cdmod 组件数量异常")
    if parsed_content.get(TARGET_PAAC) != patched_paac:
        raise ValueError("生成后的 v2.0 PAAC 载荷与预期不一致")
    if parsed_content.get(WALK_BLEND_TARGET) != synced_walk:
        raise ValueError("生成后的 v2.0 八方向同步 walk 载荷与预期不一致")
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
