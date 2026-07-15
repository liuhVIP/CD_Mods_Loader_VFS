"""生成“作者平地 phase + 当前原版 PHW 坡度 phase”v3.0 测试包。

v2.9 实机确认作者调相 walk 能让平地稳定，但上坡出现持续“两步一抖”。二进制核验已把
10 个连续 MotionPhaseInfo 记录与 10 条 PHW 样本一一对应：第 0、5 条分别是平地 walk
与 walkfast，其余 8 条是上下坡样本。本工具保留作者两条平地 phase，只把八条坡度 phase
恢复为当前原版 PHW 值，再作为唯一 ``basic_move_walk`` 载荷写入 v2.3 的 57 项基线。
"""

from __future__ import annotations

import hashlib
import re
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
    AUTHOR_WALK_SHA256,
    AUTHOR_WALK_SIZE,
    _load_author_walk,
    _replace_author_phase_walk,
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

# 当前原版 PHW mixed walk 是八条坡度 phase 的唯一来源，游戏更新后必须重新核验哈希。
NATIVE_PHW_WALK_TARGET = (
    "character/binary/motionblending/phw_locomotion/basic_move_walk.motionblending"
)
NATIVE_PHW_WALK_SHA256 = (
    "6cdb7c86f9859d55696531e1f5e1229ea544ab575f1e11f6209e63877fde8b2b"
)

# 10 个 41 字节 phase 记录按下列 PHW PAA 引用顺序排列。
PHW_SAMPLE_PATHS = (
    "1_pc/2_phw/cd_phw_basic_00_00_nor_move_walk_f_ing_00.paa",
    "1_pc/2_phw/cd_phw_basic_00_00_nor_base_move_walk_fd_25_ing_00.paa",
    "1_pc/2_phw/cd_phw_basic_00_00_nor_base_move_walk_fd_50_ing_00.paa",
    "1_pc/2_phw/cd_phw_basic_00_00_nor_base_move_walk_fu_25_ing_00.paa",
    "1_pc/2_phw/cd_phw_basic_00_00_nor_base_move_walk_fu_50_ing_00.paa",
    "1_pc/2_phw/cd_phw_basic_00_00_nor_move_walkfast_f_ing_00.paa",
    "1_pc/2_phw/cd_phw_basic_00_00_nor_base_move_walkfast_fd_25_ing_00.paa",
    "1_pc/2_phw/cd_phw_basic_00_00_nor_base_move_walkfast_fd_50_ing_00.paa",
    "1_pc/2_phw/cd_phw_basic_00_00_nor_base_move_walkfast_fu_25_ing_00.paa",
    "1_pc/2_phw/cd_phw_basic_00_00_nor_base_move_walkfast_fu_50_ing_00.paa",
)
PAA_REFERENCE_PATTERN = re.compile(rb"1_pc/[12]_ph[wm]/[A-Za-z0-9_./-]+?\.paa")

# 记录布局由作者与当前原版逐字节对照确认；只替换每条记录中的四个 u16 phase 值。
PHASE_RECORD_BASE_OFFSET = 0x0EEB
PHASE_RECORD_STRIDE = 41
PHASE_VALUES_OFFSET = 24
PHASE_VALUES_SIZE = 8
FLAT_SAMPLE_INDEXES = (0, 5)
SLOPE_SAMPLE_INDEXES = (1, 2, 3, 4, 6, 7, 8, 9)
EXPECTED_AUTHOR_TO_HYBRID_DIFF_BYTES = 29
EXPECTED_NATIVE_TO_HYBRID_DIFF_BYTES = 19

MOD_NAME = "Female Outside Combat Flat Phase Native Slope - Male Combat"
MOD_VERSION = "3.0-test"
OUTPUT_FILE_NAME = f"{MOD_NAME} v{MOD_VERSION}.cdmod"


def _extract_phw_sample_paths(content: bytes) -> tuple[str, ...]:
    """按序提取 mixed walk 中最后十条 PHW PAA 引用。"""
    references = tuple(
        match.group().decode("ascii")
        for match in PAA_REFERENCE_PATTERN.finditer(content)
    )
    phw_references = tuple(
        reference for reference in references if reference.startswith("1_pc/2_phw/")
    )
    if phw_references != PHW_SAMPLE_PATHS:
        raise ValueError("walk 的 PHW 样本顺序与已审计 phase 映射不一致")
    return phw_references


def _load_native_phw_walk(game_dir: Path) -> bytes:
    """从当前原版 0009 唯一提取并验证 PHW mixed walk。"""
    entry = _find_entry(game_dir, "0009", NATIVE_PHW_WALK_TARGET)
    content, _ = extract_plaintext(entry)
    digest = hashlib.sha256(content).hexdigest()
    if digest != NATIVE_PHW_WALK_SHA256:
        raise ValueError(
            "当前原版 PHW walk SHA-256 不匹配："
            f"预期 {NATIVE_PHW_WALK_SHA256}，实际 {digest}"
        )
    if len(content) != AUTHOR_WALK_SIZE:
        raise ValueError("当前原版 PHW walk 大小与作者 walk 不一致")
    _extract_phw_sample_paths(content)
    return content


def _phase_slice(sample_index: int) -> slice:
    """返回指定 PHW 样本的八字节 phase 区间。"""
    start = (
        PHASE_RECORD_BASE_OFFSET
        + PHASE_RECORD_STRIDE * sample_index
        + PHASE_VALUES_OFFSET
    )
    return slice(start, start + PHASE_VALUES_SIZE)


def _merge_flat_author_native_slope(author: bytes, native: bytes) -> bytes:
    """保留作者平地 phase，只恢复八条原版坡度 phase。"""
    if len(author) != AUTHOR_WALK_SIZE or len(native) != AUTHOR_WALK_SIZE:
        raise ValueError("作者/原版 walk 大小不匹配，拒绝按固定 phase 布局合并")

    hybrid = bytearray(author)
    for sample_index in SLOPE_SAMPLE_INDEXES:
        phase_slice = _phase_slice(sample_index)
        if author[phase_slice] == native[phase_slice]:
            raise ValueError(f"坡度样本 {sample_index} 的作者/原版 phase 意外相同")
        hybrid[phase_slice] = native[phase_slice]

    result = bytes(hybrid)
    for sample_index in FLAT_SAMPLE_INDEXES:
        phase_slice = _phase_slice(sample_index)
        if result[phase_slice] != author[phase_slice]:
            raise ValueError(f"平地样本 {sample_index} 未保留作者 phase")
    for sample_index in SLOPE_SAMPLE_INDEXES:
        phase_slice = _phase_slice(sample_index)
        if result[phase_slice] != native[phase_slice]:
            raise ValueError(f"坡度样本 {sample_index} 未恢复原版 PHW phase")
    return result


def _validate_real_hybrid(author: bytes, native: bytes, hybrid: bytes) -> None:
    """验证真实样本只发生预期的八条坡度 phase 变化。"""
    author_diff = [
        index for index, (old, new) in enumerate(zip(author, hybrid)) if old != new
    ]
    native_diff = [
        index for index, (old, new) in enumerate(zip(native, hybrid)) if old != new
    ]
    expected_author_offsets = {
        index
        for sample_index in SLOPE_SAMPLE_INDEXES
        for index in range(_phase_slice(sample_index).start, _phase_slice(sample_index).stop)
        if author[index] != native[index]
    }
    if set(author_diff) != expected_author_offsets:
        raise ValueError("组合 walk 出现坡度 phase 之外的作者字节变化")
    if len(author_diff) != EXPECTED_AUTHOR_TO_HYBRID_DIFF_BYTES:
        raise ValueError(
            "组合 walk 相对作者的差异字节数异常："
            f"预期 {EXPECTED_AUTHOR_TO_HYBRID_DIFF_BYTES}，实际 {len(author_diff)}"
        )
    if len(native_diff) != EXPECTED_NATIVE_TO_HYBRID_DIFF_BYTES:
        raise ValueError(
            "组合 walk 相对原版 PHW 的差异字节数异常："
            f"预期 {EXPECTED_NATIVE_TO_HYBRID_DIFF_BYTES}，实际 {len(native_diff)}"
        )


def build_mod(
    game_dir: Path,
    baseline_path: Path,
    author_walk_path: Path,
    output_path: Path,
) -> Path:
    """完整继承 v2.3，并写入平地作者/坡度原版组合 walk。"""
    baseline = _load_v23_files(baseline_path)
    author = _load_author_walk(author_walk_path)
    _extract_phw_sample_paths(author)
    native = _load_native_phw_walk(game_dir)
    hybrid = _merge_flat_author_native_slope(author, native)
    _validate_real_hybrid(author, native, hybrid)
    replaced = _replace_author_phase_walk(baseline, hybrid)

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

    hybrid_sha256 = hashlib.sha256(hybrid).hexdigest()
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
            "Preserves all 57 verified v2.3 targets. The only changed payload "
            "keeps Andyground's flat walk/walkfast phase values while restoring "
            "all eight slope phase records from the current native PHW walk."
        ),
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": "cdmm.female-outside-combat-flat-phase-native-slope-male-combat",
        "name": MOD_NAME,
        "source": {
            "author_walk_sha256": AUTHOR_WALK_SHA256,
            "baseline_package": V23_PACKAGE_NAME,
            "baseline_sha256": V23_PACKAGE_SHA256,
            "flat_sample_indexes": list(FLAT_SAMPLE_INDEXES),
            "format": "v2.3-plus-author-flat-phase-native-phw-slope-phase",
            "hybrid_walk_sha256": hybrid_sha256,
            "native_phw_walk_sha256": NATIVE_PHW_WALK_SHA256,
            "slope_sample_indexes": list(SLOPE_SAMPLE_INDEXES),
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
        raise ValueError("生成后的 v3.0 cdmod 组件数量异常")
    if parsed_content != expected_content:
        raise ValueError("生成后的 v3.0 资源载荷与预期不一致")
    if parsed_content[PHM_WALK_TARGET] != hybrid:
        raise ValueError("生成后的 v3.0 未保留组合 walk 原始字节")
    return output_path


def main() -> int:
    """生成游戏 mods 目录下的 v3.0 坡度隔离测试包。"""
    game_dir = Path(r"G:\SteamLibrary\steamapps\common\Crimson Desert")
    mods_dir = game_dir / "mods"
    baseline_path = mods_dir / V23_PACKAGE_NAME
    author_walk_path = (
        mods_dir / AUTHOR_MOD_DIRECTORY_NAME / AUTHOR_WALK_RELATIVE_PATH
    )
    output_path = mods_dir / OUTPUT_FILE_NAME
    result = build_mod(game_dir, baseline_path, author_walk_path, output_path)
    print(f"已生成：{result}")
    print("作者平地 phase 样本：0, 5")
    print("原版坡度 phase 样本：1, 2, 3, 4, 6, 7, 8, 9")
    print(f"v3.0 SHA-256：{hashlib.sha256(result.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
