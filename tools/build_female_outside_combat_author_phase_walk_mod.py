"""生成“v2.3 稳定基线 + 作者调相持续走路”测试包。

作者 ``Walk Like Female Fly Like a Male`` 的普通 walk 文件保留男女混合样本，
但相对当前原版 PHW walk 调整了集中 phase 数据。为了验证这组 phase 数据能否消除
v2.3 的第四步姿势硬重置，本工具完整继承 v2.3 的 57 个资源目标，只替换
``phm_locomotion/basic_move_walk.motionblending``，不修改起步、PAA、metabin、PAAC、
CharacterInfo、战斗资源或任何节点结构。
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
from cdmm.tools.build_female_outside_combat_selective_nodes_mod import (
    EXPECTED_V23_FILE_COUNT,
    V23_PACKAGE_NAME,
    V23_PACKAGE_SHA256,
    _load_v23_files,
)
from cdmm.tools.build_female_outside_combat_smooth_walk_mod import (
    PHM_WALK_TARGET,
    _file_document,
)

# 作者 loose 模组中经过当前目录与中间备份双重核验的普通走路核心文件。
AUTHOR_MOD_DIRECTORY_NAME = "Walk Like Female Fly Like a Male"
AUTHOR_WALK_RELATIVE_PATH = Path(
    "character/binary/motionblending/phm_locomotion/basic_move_walk.motionblending"
)
AUTHOR_WALK_SHA256 = "a14f3768bda161ba0e47a67184c80b84221fb4d8b6ef63540ff54d9960f65d9c"
AUTHOR_WALK_SIZE = 16_340

# 作者 walk 的已审计结构标记；用于防止选错同名文件或误用纯女性 lv1。
LOOP_DECLARATION = b"_isLoopMotionBlending"
SYNC_DECLARATION = b"_isSyncMotionBlending"
# 只统计 PAA 引用，避免把 PHM skeleton 路径误算成第十四条男性动作样本。
MALE_SAMPLE_PREFIX = b"1_pc/1_phm/cd_phm_"
FEMALE_SAMPLE_PREFIX = b"1_pc/2_phw/cd_phw_"
EXPECTED_MALE_SAMPLE_COUNT = 13
EXPECTED_FEMALE_SAMPLE_COUNT = 10

MOD_NAME = "Female Outside Combat Author Phase Walk - Male Combat"
MOD_VERSION = "2.9-test"
OUTPUT_FILE_NAME = f"{MOD_NAME} v{MOD_VERSION}.cdmod"


def _load_author_walk(author_walk_path: Path) -> bytes:
    """读取并严格验证作者调相 walk 样本。"""
    content = author_walk_path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if digest != AUTHOR_WALK_SHA256:
        raise ValueError(
            "作者 walk SHA-256 不匹配："
            f"预期 {AUTHOR_WALK_SHA256}，实际 {digest}"
        )
    if len(content) != AUTHOR_WALK_SIZE:
        raise ValueError(
            f"作者 walk 大小不匹配：预期 {AUTHOR_WALK_SIZE}，实际 {len(content)}"
        )
    if LOOP_DECLARATION not in content or SYNC_DECLARATION not in content:
        raise ValueError("作者 walk 缺少已审计的 loop/sync 声明")
    if content.count(MALE_SAMPLE_PREFIX) != EXPECTED_MALE_SAMPLE_COUNT:
        raise ValueError("作者 walk 的 PHM 样本数量不匹配")
    if content.count(FEMALE_SAMPLE_PREFIX) != EXPECTED_FEMALE_SAMPLE_COUNT:
        raise ValueError("作者 walk 的 PHW 样本数量不匹配")
    return content


def _replace_author_phase_walk(
    baseline: tuple[tuple[str, str, bytes], ...],
    author_walk: bytes,
) -> tuple[tuple[str, str, bytes], ...]:
    """保持 v2.3 顺序，只替换唯一持续 walk 目标。"""
    matching_indexes = [
        index
        for index, (target, _pamt_dir, _content) in enumerate(baseline)
        if target == PHM_WALK_TARGET
    ]
    if len(matching_indexes) != 1:
        raise ValueError(
            f"v2.3 的作者 walk 目标数量异常：预期 1，实际 {len(matching_indexes)}"
        )

    target_index = matching_indexes[0]
    if baseline[target_index][2] == author_walk:
        raise ValueError("作者 walk 与 v2.3 持续 walk 相同，实验失去单变量意义")

    replaced = tuple(
        (target, pamt_dir, author_walk if target == PHM_WALK_TARGET else content)
        for target, pamt_dir, content in baseline
    )
    changed_targets = [
        old_target
        for (old_target, _old_pamt_dir, old_content),
        (_new_target, _new_pamt_dir, new_content) in zip(baseline, replaced)
        if old_content != new_content
    ]
    if changed_targets != [PHM_WALK_TARGET]:
        raise ValueError(f"v2.9 相对 v2.3 的目标差异异常：{changed_targets}")
    return replaced


def build_mod(
    baseline_path: Path,
    author_walk_path: Path,
    output_path: Path,
) -> Path:
    """完整继承 v2.3，并写入唯一作者调相 walk 载荷。"""
    baseline = _load_v23_files(baseline_path)
    author_walk = _load_author_walk(author_walk_path)
    replaced = _replace_author_phase_walk(baseline, author_walk)

    replacements: list[dict[str, object]] = []
    documents: dict[str, dict[str, object] | bytes] = {}
    expected_content: dict[str, bytes] = {}
    for index, (target, pamt_dir, content) in enumerate(replaced):
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
        "author": "Andyground, Khione, Slinky, CDMM",
        "components": [
            {
                "file_count": len(replacements),
                "path": "files/replacements.json",
                "type": CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE,
            }
        ],
        "dependencies": [],
        "description": (
            "Preserves all 57 verified v2.3 targets and changes only the PHM "
            "basic_move_walk payload to Andyground's phase-adjusted mixed walk "
            "tree, testing fourth-step continuity without touching PAAC or combat."
        ),
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": "cdmm.female-outside-combat-author-phase-walk-male-combat",
        "name": MOD_NAME,
        "source": {
            "author_mod": AUTHOR_MOD_DIRECTORY_NAME,
            "author_walk_sha256": AUTHOR_WALK_SHA256,
            "baseline_package": V23_PACKAGE_NAME,
            "baseline_sha256": V23_PACKAGE_SHA256,
            "format": "v2.3-plus-author-phase-adjusted-mixed-walk",
            "walk_target": PHM_WALK_TARGET,
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
        raise ValueError("生成后的 v2.9 cdmod 组件数量异常")
    if parsed_content != expected_content:
        raise ValueError("生成后的 v2.9 资源载荷与预期不一致")
    if parsed_content[PHM_WALK_TARGET] != author_walk:
        raise ValueError("生成后的 v2.9 未保留作者 walk 原始字节")
    return output_path


def main() -> int:
    """生成游戏 mods 目录下的 v2.9 单变量测试包。"""
    game_dir = Path(r"G:\SteamLibrary\steamapps\common\Crimson Desert")
    mods_dir = game_dir / "mods"
    baseline_path = mods_dir / V23_PACKAGE_NAME
    author_walk_path = (
        mods_dir / AUTHOR_MOD_DIRECTORY_NAME / AUTHOR_WALK_RELATIVE_PATH
    )
    output_path = mods_dir / OUTPUT_FILE_NAME
    result = build_mod(baseline_path, author_walk_path, output_path)
    print(f"已生成：{result}")
    print("相对 v2.3 变更目标：1")
    print(f"作者 walk SHA-256：{AUTHOR_WALK_SHA256}")
    print(f"v2.9 SHA-256：{hashlib.sha256(result.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
