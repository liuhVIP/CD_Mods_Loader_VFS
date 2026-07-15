"""生成“原生同步女性移动树”四步循环诊断包。

v1.5 使用的纯 PHW basic_move_lv1 缺少 _isSyncMotionBlending，实机固定在第四步
出现疑似循环重置。v1.6 又排除了左右 threestep。本工具回到 v1.5 的两目标结构，
只把 walk 载荷改为原版纯 PHW、同时具备 loop/sync 声明的 basic_move_lv3。
该包只用于确认相位同步根因，走路时可能暂时呈现慢速跑姿。
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

# 原版纯 PHW 且带同步循环声明的最近似普通移动树。
SYNC_BLEND_SOURCE = (
    "character/binary/motionblending/phw_locomotion/"
    "basic_move_lv3.motionblending"
)

# 相位同步诊断必须同时满足 loop/sync 声明，缺一不可。
LOOP_DECLARATION = b"_isLoopMotionBlending"
SYNC_DECLARATION = b"_isSyncMotionBlending"

MOD_NAME = "Female Walk Sync Probe - Native Male Combat"
MOD_VERSION = "1.7-test"
OUTPUT_FILE_NAME = f"{MOD_NAME} v{MOD_VERSION}.cdmod"


def _validate_sync_probe_source(mixed_walk: bytes, sync_female: bytes) -> None:
    """确认目标仍为混合 walk，诊断来源仍为纯 PHW 同步循环树。"""
    if MALE_SAMPLE_PREFIX not in mixed_walk or FEMALE_SAMPLE_PREFIX not in mixed_walk:
        raise ValueError("原版 basic_move_walk 不再同时包含 PHM/PHW 样本")
    if MALE_SAMPLE_PREFIX in sync_female or FEMALE_SAMPLE_PREFIX not in sync_female:
        raise ValueError("同步诊断来源不再是纯 PHW 样本树")
    for declaration in (LOOP_DECLARATION, SYNC_DECLARATION):
        if declaration not in sync_female:
            raise ValueError(f"同步诊断来源缺少声明：{declaration!r}")


def build_mod(game_dir: Path, output_path: Path) -> Path:
    """从当前原版 PAAC 与同步 PHW 移动树构建 v1.7。"""
    basic_lower_entry = _find_entry(game_dir, PAAC_PAMT_DIR, TARGET_PAAC)
    mixed_walk_entry = _find_entry(
        game_dir,
        WALK_BLEND_PAMT_DIR,
        WALK_BLEND_TARGET,
    )
    sync_female_entry = _find_entry(
        game_dir,
        WALK_BLEND_PAMT_DIR,
        SYNC_BLEND_SOURCE,
    )
    vanilla_paac, _ = extract_plaintext(basic_lower_entry)
    mixed_walk, _ = extract_plaintext(mixed_walk_entry)
    sync_female, _ = extract_plaintext(sync_female_entry)
    _validate_sync_probe_source(mixed_walk, sync_female)
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
            content=sync_female,
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
            "Diagnostic build preserving the native male combat baseline while "
            "placing a native female-only loop+sync PHW movement tree in the walk slot. "
            "The temporary gait may look like a slow run."
        ),
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": "cdmm.female-walk-sync-probe-native-male-combat",
        "name": MOD_NAME,
        "source": {
            "format": "v1.5-sync-phase-diagnostic",
            "paac_target": TARGET_PAAC,
            "sync_blend_source": SYNC_BLEND_SOURCE,
            "walk_blend_target": WALK_BLEND_TARGET,
        },
        "version": MOD_VERSION,
    }
    documents: dict[str, dict[str, object] | bytes] = {
        "manifest.json": manifest,
        "files/replacements.json": {"schema": 1, "files": replacements},
        paac_payload: patched_paac,
        walk_payload: sync_female,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_cdmod_zip(output_path, documents)

    package = load_cdmod_package(output_path)
    files = [file for patch in package.file_patches for file in patch.files]
    parsed_content = {file.target: file.content for file in files}
    if package.legacy_json_patches or len(files) != 2:
        raise ValueError("生成后的 v1.7 cdmod 组件数量异常")
    if parsed_content.get(TARGET_PAAC) != patched_paac:
        raise ValueError("生成后的 v1.7 PAAC 载荷与预期不一致")
    if parsed_content.get(WALK_BLEND_TARGET) != sync_female:
        raise ValueError("生成后的 v1.7 同步移动树载荷与预期不一致")
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
