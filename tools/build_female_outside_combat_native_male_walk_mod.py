"""生成“v2.3 女性非战斗基线 + 原生男性普通走路分支”测试包。

这是第四步姿势硬重置无法安全修复后的妥协版本。完整保留 v2.3 的女性站姿、跑步、
上坡、跳跃等已验证资源和原生男性战斗链，只把普通走路所需的起步、持续、停止与
180 度转向资源恢复为当前游戏原版 PHM 内容。不修改 PAAC、CharacterInfo、metabin、
transition 或任何节点结构。
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
from cdmm.tools.build_female_locomotion_male_combat_graph_mod import _find_entry
from cdmm.tools.build_female_outside_combat_selective_nodes_mod import (
    EXPECTED_V23_FILE_COUNT,
    V23_PACKAGE_NAME,
    V23_PACKAGE_SHA256,
    _load_v23_files,
)
from cdmm.tools.build_female_outside_combat_smooth_walk_mod import _file_document

# 普通男性走路的完整资源分支：两个混合树、六条起步、两条停止、两条 180 度转向。
# v2.3 没有修改 PHM metabin，因此恢复这些原版 PAA 后会重新与男性 metadata 配对。
NATIVE_MALE_WALK_TARGETS = (
    "character/binary/motionblending/phm_locomotion/basic_move_lv1.motionblending",
    "character/binary/motionblending/phm_locomotion/basic_move_walk.motionblending",
    "character/motion/1_pc/1_phm/cd_phm_basic_00_00_nor_move_walk_f_footr_start_02.paa",
    "character/motion/1_pc/1_phm/cd_phm_basic_00_00_nor_move_walk_turn90r_footr_start_02.paa",
    "character/motion/1_pc/1_phm/cd_phm_basic_00_00_nor_move_walk_turn180r_footr_start_02.paa",
    "character/motion/1_pc/1_phm/cd_phm_basic_00_00_nor_move_walk_f_footl_start_02.paa",
    "character/motion/1_pc/1_phm/cd_phm_basic_00_00_nor_move_walk_turn90l_footl_start_02.paa",
    "character/motion/1_pc/1_phm/cd_phm_basic_00_00_nor_move_walk_turn180l_footl_start_02.paa",
    "character/motion/1_pc/1_phm/cd_phm_basic_00_00_nor_move_walk_endr_00.paa",
    "character/motion/1_pc/1_phm/cd_phm_basic_00_00_nor_move_walk_endl_00.paa",
    "character/motion/1_pc/1_phm/cd_phm_basic_00_00_normal_move_walk_turn_180_l_000.paa",
    "character/motion/1_pc/1_phm/cd_phm_basic_00_00_normal_move_walk_turn_180_r_000.paa",
)

MOD_NAME = "Female Outside Combat Native Male Walk - Male Combat"
MOD_VERSION = "2.8-test"
OUTPUT_FILE_NAME = f"{MOD_NAME} v{MOD_VERSION}.cdmod"


def _load_native_male_walk(game_dir: Path) -> dict[str, bytes]:
    """从当前原版 0009 唯一提取十二项男性普通走路资源。"""
    native: dict[str, bytes] = {}
    for target in NATIVE_MALE_WALK_TARGETS:
        entry = _find_entry(game_dir, "0009", target)
        content, _ = extract_plaintext(entry)
        native[target] = content
    if len(native) != len(NATIVE_MALE_WALK_TARGETS):
        raise ValueError("原生男性普通走路资源数量异常")
    return native


def _restore_native_male_walk(
    baseline: tuple[tuple[str, str, bytes], ...],
    native: dict[str, bytes],
) -> tuple[tuple[str, str, bytes], ...]:
    """保持 v2.3 目标顺序，只恢复十二项男性普通走路载荷。"""
    baseline_targets = {target for target, _pamt_dir, _content in baseline}
    expected_targets = set(NATIVE_MALE_WALK_TARGETS)
    if not expected_targets.issubset(baseline_targets):
        missing = sorted(expected_targets - baseline_targets)
        raise ValueError(f"v2.3 缺少男性走路回退目标：{missing}")
    if set(native) != expected_targets:
        raise ValueError("原生男性走路资源集合与声明不一致")

    restored = tuple(
        (target, pamt_dir, native.get(target, content))
        for target, pamt_dir, content in baseline
    )
    changed_targets = [
        target
        for (target, _pamt_dir, old_content), (_target, _new_pamt_dir, new_content)
        in zip(baseline, restored)
        if old_content != new_content
    ]
    if set(changed_targets) != expected_targets or len(changed_targets) != len(expected_targets):
        raise ValueError(f"男性走路实际恢复目标异常：{changed_targets}")
    return restored


def build_mod(game_dir: Path, baseline_path: Path, output_path: Path) -> Path:
    """完整继承 v2.3，只恢复原生男性普通走路资源分支。"""
    baseline = _load_v23_files(baseline_path)
    restored = _restore_native_male_walk(baseline, _load_native_male_walk(game_dir))

    replacements: list[dict[str, object]] = []
    documents: dict[str, dict[str, object] | bytes] = {}
    expected_content: dict[str, bytes] = {}
    for index, (target, pamt_dir, content) in enumerate(restored):
        payload_path = f"assets/{index:03d}/{Path(target).name}"
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
            "Preserves the verified v2.3 female idle/run/non-combat baseline and native "
            "male combat, while restoring only the complete ordinary walking branch to "
            "the current native PHM resources for seamless continuous walking."
        ),
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": "cdmm.female-outside-combat-native-male-walk-male-combat",
        "name": MOD_NAME,
        "source": {
            "baseline_package": V23_PACKAGE_NAME,
            "baseline_sha256": V23_PACKAGE_SHA256,
            "format": "v2.3-plus-current-native-phm-walk-branch",
            "native_walk_target_count": len(NATIVE_MALE_WALK_TARGETS),
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
    if package.legacy_json_patches or len(files) != EXPECTED_V23_FILE_COUNT:
        raise ValueError("生成后的 v2.8 cdmod 组件数量异常")
    if parsed_content != expected_content:
        raise ValueError("生成后的 v2.8 资源载荷与预期不一致")
    return output_path


def main() -> int:
    """生成游戏 mods 目录下的新测试包。"""
    game_dir = Path(r"G:\SteamLibrary\steamapps\common\Crimson Desert")
    baseline_path = game_dir / "mods" / V23_PACKAGE_NAME
    output_path = game_dir / "mods" / OUTPUT_FILE_NAME
    result = build_mod(game_dir, baseline_path, output_path)
    print(f"已生成：{result}")
    print(f"男性普通走路资源：{len(NATIVE_MALE_WALK_TARGETS)}")
    print(f"SHA-256：{hashlib.sha256(result.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
