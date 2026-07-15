"""生成“自主调相平地慢走 + 现有坡度槽男性载荷”v3.3 测试包。

v3.2 证明新增深层动画别名在最终 PAMT 中仍会错误带入 ``ui`` 目录。本工具完全放弃新增
路径：以当前原版 PHW basic_move_walk 为主树，只把平地慢走索引 0 的 phase 写成作者已
实机验证稳定的数值；平地 walkfast 索引 5 保持当前原版 PHW，以隔离跑步交接卡顿；八条
坡度 phase 使用当前原版 PHM，并把游戏现有八组 PHW 坡度 PAA/metabin 目标替换为配对的
原版 PHM 载荷。该方案没有 allow_new，也不依赖别名目录解析。
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
from cdmm.tools.build_female_outside_combat_author_phase_walk_mod import (
    AUTHOR_MOD_DIRECTORY_NAME,
    AUTHOR_WALK_RELATIVE_PATH,
    AUTHOR_WALK_SIZE,
    _load_author_walk,
    _replace_author_phase_walk,
)
from cdmm.tools.build_female_outside_combat_female_flat_male_slope_mod import (
    NATIVE_PHM_WALK_SHA256,
    NATIVE_PHM_WALK_SIZE,
    PHM_SAMPLE_PATHS,
    _load_native_phm_walk,
    _native_phm_phase_slice,
)
from cdmm.tools.build_female_outside_combat_flat_phase_native_slope_mod import (
    NATIVE_PHW_WALK_SHA256,
    PHASE_VALUES_SIZE,
    PHW_SAMPLE_PATHS,
    SLOPE_SAMPLE_INDEXES,
    _extract_phw_sample_paths,
    _load_native_phw_walk,
    _phase_slice,
)
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
from cdmm.tools.build_female_walk_matched_start_mod import (
    METABIN_PAMT_DIR,
    PAA_PAMT_DIR,
    _metabin_target,
    _paa_target,
)

# 只保留作者已经证明能稳定平地慢走的索引 0；索引 5 walkfast 恢复当前原版。
FEMALE_FLAT_WALK_SAMPLE_INDEX = 0
NATIVE_FLAT_WALKFAST_SAMPLE_INDEX = 5
EXPECTED_EXISTING_SLOPE_REPLACEMENTS = len(SLOPE_SAMPLE_INDEXES) * 2
EXPECTED_TOTAL_FILE_COUNT = EXPECTED_V23_FILE_COUNT + EXPECTED_EXISTING_SLOPE_REPLACEMENTS

MOD_NAME = "Female Outside Combat Self Tuned Flat Existing Male Slope - Male Combat"
MOD_VERSION = "3.3-test"
OUTPUT_FILE_NAME = f"{MOD_NAME} v{MOD_VERSION}.cdmod"


def _build_self_tuned_walk(
    author: bytes,
    native_phw: bytes,
    native_phm: bytes,
) -> bytes:
    """当前原版 PHW 主树中只保留作者慢走 phase，并写入 PHM 坡度 phase。"""
    if len(author) != AUTHOR_WALK_SIZE or len(native_phw) != AUTHOR_WALK_SIZE:
        raise ValueError("作者/原版 PHW walk 大小不匹配")
    if len(native_phm) != NATIVE_PHM_WALK_SIZE:
        raise ValueError("原版 PHM walk 大小不匹配")
    _extract_phw_sample_paths(author)
    _extract_phw_sample_paths(native_phw)

    tuned = bytearray(native_phw)
    flat_walk_slice = _phase_slice(FEMALE_FLAT_WALK_SAMPLE_INDEX)
    if len(author[flat_walk_slice]) != PHASE_VALUES_SIZE:
        raise ValueError("作者平地慢走 phase 区间大小异常")
    tuned[flat_walk_slice] = author[flat_walk_slice]
    for sample_index in SLOPE_SAMPLE_INDEXES:
        tuned[_phase_slice(sample_index)] = native_phm[
            _native_phm_phase_slice(sample_index)
        ]
    result = bytes(tuned)

    if result[flat_walk_slice] != author[flat_walk_slice]:
        raise ValueError("自主调相 walk 未保留作者平地慢走 phase")
    walkfast_slice = _phase_slice(NATIVE_FLAT_WALKFAST_SAMPLE_INDEX)
    if result[walkfast_slice] != native_phw[walkfast_slice]:
        raise ValueError("自主调相 walk 未恢复当前原版平地 walkfast phase")
    for sample_index in SLOPE_SAMPLE_INDEXES:
        if result[_phase_slice(sample_index)] != native_phm[
            _native_phm_phase_slice(sample_index)
        ]:
            raise ValueError(f"坡度样本 {sample_index} 未使用原版 PHM phase")
    return result


def build_mod(
    game_dir: Path,
    baseline_path: Path,
    author_walk_path: Path,
    output_path: Path,
) -> Path:
    """生成无新增路径的 v3.3 平地女/坡面男测试包。"""
    baseline = _load_v23_files(baseline_path)
    author = _load_author_walk(author_walk_path)
    native_phw = _load_native_phw_walk(game_dir)
    native_phm = _load_native_phm_walk(game_dir)
    tuned_walk = _build_self_tuned_walk(author, native_phw, native_phm)
    replaced = _replace_author_phase_walk(baseline, tuned_walk)

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

    asset_index = len(replacements)
    slope_targets: list[str] = []
    for sample_index in SLOPE_SAMPLE_INDEXES:
        phw_reference = PHW_SAMPLE_PATHS[sample_index]
        phm_reference = PHM_SAMPLE_PATHS[sample_index]
        for kind, pamt_dir, target_builder in (
            ("paa", PAA_PAMT_DIR, _paa_target),
            ("metabin", METABIN_PAMT_DIR, _metabin_target),
        ):
            target = target_builder(phw_reference)
            source = target_builder(phm_reference)
            target_entry = _find_entry(game_dir, pamt_dir, target)
            source_entry = _find_entry(game_dir, pamt_dir, source)
            target_content, _ = extract_plaintext(target_entry)
            source_content, _ = extract_plaintext(source_entry)
            if target_content == source_content:
                raise ValueError(f"PHW/PHM 坡度载荷意外相同：{target}")
            payload_path = f"assets/{asset_index:03d}/{kind}_{Path(target).name}"
            replacements.append(
                _file_document(
                    target=target,
                    pamt_dir=pamt_dir,
                    payload_path=payload_path,
                    content=source_content,
                )
            )
            documents[payload_path] = source_content
            expected_content[target] = source_content
            slope_targets.append(target)
            asset_index += 1

    if len(slope_targets) != EXPECTED_EXISTING_SLOPE_REPLACEMENTS:
        raise ValueError("现有 PHW 坡度替换目标数量异常")
    if len(slope_targets) != len(set(slope_targets)):
        raise ValueError("现有 PHW 坡度替换目标重复")

    tuned_walk_sha256 = hashlib.sha256(tuned_walk).hexdigest()
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
            "Uses the current native PHW walk tree, keeps only Andyground's stable "
            "flat slow-walk phase, restores native PHW flat walkfast for run "
            "transitions, and places native PHM bytes in the eight existing PHW "
            "slope PAA/metabin slots. No new animation paths are created."
        ),
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": "cdmm.female-outside-combat-self-tuned-flat-existing-male-slope",
        "name": MOD_NAME,
        "source": {
            "baseline_package": V23_PACKAGE_NAME,
            "baseline_sha256": V23_PACKAGE_SHA256,
            "flat_walk_sample_index": FEMALE_FLAT_WALK_SAMPLE_INDEX,
            "flat_walkfast_sample_index": NATIVE_FLAT_WALKFAST_SAMPLE_INDEX,
            "format": "current-phw-tree-author-flat0-native-walkfast-phm-slope-slots",
            "native_phm_walk_sha256": NATIVE_PHM_WALK_SHA256,
            "native_phw_walk_sha256": NATIVE_PHW_WALK_SHA256,
            "slope_sample_indexes": list(SLOPE_SAMPLE_INDEXES),
            "tuned_walk_sha256": tuned_walk_sha256,
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
    if package.legacy_json_patches or len(files) != EXPECTED_TOTAL_FILE_COUNT:
        raise ValueError("生成后的 v3.3 cdmod 组件数量异常")
    if parsed_content != expected_content:
        raise ValueError("生成后的 v3.3 资源载荷与预期不一致")
    if any(file.allow_new for file in files):
        raise ValueError("生成后的 v3.3 不应包含 allow_new")
    if parsed_content[PHM_WALK_TARGET] != tuned_walk:
        raise ValueError("生成后的 v3.3 未保留自主调相 walk")
    return output_path


def main() -> int:
    """生成游戏 mods 目录下的 v3.3 自主调相测试包。"""
    game_dir = Path(r"G:\SteamLibrary\steamapps\common\Crimson Desert")
    mods_dir = game_dir / "mods"
    baseline_path = mods_dir / V23_PACKAGE_NAME
    author_walk_path = (
        mods_dir / AUTHOR_MOD_DIRECTORY_NAME / AUTHOR_WALK_RELATIVE_PATH
    )
    output_path = mods_dir / OUTPUT_FILE_NAME
    result = build_mod(game_dir, baseline_path, author_walk_path, output_path)
    print(f"已生成：{result}")
    print("平地慢走：作者 phase；平地 walkfast：当前原版 PHW")
    print("坡度：现有 PHW 目标承载原版 PHM PAA/metabin/phase")
    print("新增路径：0")
    print(f"v3.3 SHA-256：{hashlib.sha256(result.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
