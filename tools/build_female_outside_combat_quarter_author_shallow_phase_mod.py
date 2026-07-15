"""生成“浅坡循环末端提前 4”v3.7 微调包。

旧 v3.7 从 v3.6 向作者坡面模板移动 25%，实机抖动反而加重，证明该方向不适合当前
Kliff 链。循环末端提前 1 后抖动明显减轻，提前 2 后仍能明显看出。本工具继续保留 v3.7
包名并从 v3.6 重新生成，按实机反馈跳过 -3，把四条浅坡 phase 的循环末端标记各减 4。
"""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path

from cdmm.services.cdmod_converter import (
    CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE,
    CDMOD_FORMAT_NAME,
    CDMOD_FORMAT_VERSION,
    _write_cdmod_zip,
)
from cdmm.services.cdmod_package import load_cdmod_package
from cdmm.tools.build_female_outside_combat_flat_aligned_shallow_male_steep_slope_mod import (
    FLAT_SAMPLE_INDEXES,
    OUTPUT_FILE_NAME as V36_PACKAGE_NAME,
    SHALLOW_TO_FLAT_SAMPLE,
)
from cdmm.tools.build_female_outside_combat_flat_phase_native_slope_mod import (
    PHW_SAMPLE_PATHS,
    _extract_phw_sample_paths,
    _phase_slice,
)
from cdmm.tools.build_female_outside_combat_native_shallow_male_steep_slope_mod import (
    EXPECTED_TOTAL_FILE_COUNT,
    STEEP_SLOPE_SAMPLE_INDEXES,
)
from cdmm.tools.build_female_outside_combat_smooth_walk_mod import (
    PHM_WALK_TARGET,
    _file_document,
)

# v3.6 是浅坡完全对齐平地后的直接基线，必须锁定哈希。
V36_PACKAGE_SHA256 = (
    "999bcbf007bab6962583a818e4019a92197a3afe622f545d50ba8e114a1dc415"
)

# v3.6 的两组已审计平地模板，以及本轮提前 4 的循环末端标记。
V36_FLAT_WALK_PHASE = (6, 36, 52, 78)
V36_FLAT_WALKFAST_PHASE = (8, 32, 52, 82)
LOOP_END_ADJUSTMENT = -4
EXPECTED_TUNED_WALK_PHASE = (6, 36, 52, 74)
EXPECTED_TUNED_WALKFAST_PHASE = (8, 32, 52, 78)
EXPECTED_REAL_WALK_DIFF_BYTES = 4

# v3.7 测试包身份常量。
MOD_NAME = "Female Outside Combat Quarter Author Shallow Phase - Male Combat"
MOD_VERSION = "3.7-test"
OUTPUT_FILE_NAME = f"{MOD_NAME} v{MOD_VERSION}.cdmod"


def _load_v36_files(
    package_path: Path,
) -> tuple[tuple[str, str, bytes], ...]:
    """读取并严格验证 v3.6 的 73 项资源基线。"""
    digest = hashlib.sha256(package_path.read_bytes()).hexdigest()
    if digest != V36_PACKAGE_SHA256:
        raise ValueError(
            "v3.6 基线 SHA-256 不匹配："
            f"预期 {V36_PACKAGE_SHA256}，实际 {digest}"
        )

    package = load_cdmod_package(package_path)
    files = tuple(file for patch in package.file_patches for file in patch.files)
    if package.legacy_json_patches or len(files) != EXPECTED_TOTAL_FILE_COUNT:
        raise ValueError("v3.6 基线组件数量异常")
    identities = [(file.pamt_dir, file.target) for file in files]
    if len(identities) != len(set(identities)):
        raise ValueError("v3.6 基线存在重复资源目标")
    if sum(file.target == PHM_WALK_TARGET for file in files) != 1:
        raise ValueError("v3.6 基线的持续 walk 目标数量异常")
    return tuple((file.target, file.pamt_dir, file.content) for file in files)


def _adjust_loop_end(
    phase: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    """保持前三个 phase 不变，只微调循环末端标记。"""
    adjusted_end = phase[-1] + LOOP_END_ADJUSTMENT
    if adjusted_end <= phase[-2]:
        raise ValueError("循环末端微调后必须大于第三个 phase")
    return (*phase[:-1], adjusted_end)


def _tune_shallow_phase(v36_walk: bytes) -> bytes:
    """将四条浅坡的循环末端标记从 v3.6 基线提前 4。"""
    if _extract_phw_sample_paths(v36_walk) != PHW_SAMPLE_PATHS:
        raise ValueError("v3.6 walk 的 PHW 样本映射异常")

    flat_walk = struct.unpack("<4H", v36_walk[_phase_slice(0)])
    flat_walkfast = struct.unpack("<4H", v36_walk[_phase_slice(5)])
    if flat_walk != V36_FLAT_WALK_PHASE:
        raise ValueError(f"v3.6 平地 walk phase 异常：{flat_walk}")
    if flat_walkfast != V36_FLAT_WALKFAST_PHASE:
        raise ValueError(f"v3.6 平地 walkfast phase 异常：{flat_walkfast}")

    tuned_walk_phase = _adjust_loop_end(flat_walk)
    tuned_walkfast_phase = _adjust_loop_end(flat_walkfast)
    if tuned_walk_phase != EXPECTED_TUNED_WALK_PHASE:
        raise ValueError("v3.7 慢走浅坡模板计算异常")
    if tuned_walkfast_phase != EXPECTED_TUNED_WALKFAST_PHASE:
        raise ValueError("v3.7 快走浅坡模板计算异常")

    tuned = bytearray(v36_walk)
    for shallow_index, flat_index in SHALLOW_TO_FLAT_SAMPLE.items():
        phase = tuned_walk_phase if flat_index == 0 else tuned_walkfast_phase
        tuned[_phase_slice(shallow_index)] = struct.pack("<4H", *phase)
    result = bytes(tuned)

    for sample_index in FLAT_SAMPLE_INDEXES + STEEP_SLOPE_SAMPLE_INDEXES:
        if result[_phase_slice(sample_index)] != v36_walk[
            _phase_slice(sample_index)
        ]:
            raise ValueError(f"v3.7 意外改变保留样本 {sample_index}")
    return result


def build_mod(
    baseline_path: Path,
    output_path: Path,
) -> Path:
    """生成相对 v3.6 只微调四条浅坡 phase 的 v3.7 包。"""
    baseline = _load_v36_files(baseline_path)
    v36_walk = next(
        content for target, _pamt_dir, content in baseline if target == PHM_WALK_TARGET
    )
    tuned_walk = _tune_shallow_phase(v36_walk)

    walk_diff = [
        index
        for index, (old, new) in enumerate(zip(v36_walk, tuned_walk))
        if old != new
    ]
    expected_offsets = {
        offset
        for shallow_index in SHALLOW_TO_FLAT_SAMPLE
        for offset in range(
            _phase_slice(shallow_index).start,
            _phase_slice(shallow_index).stop,
        )
        if v36_walk[offset] != tuned_walk[offset]
    }
    if set(walk_diff) != expected_offsets:
        raise ValueError("v3.7 出现浅坡 phase 之外的 walk 字节变化")
    if len(walk_diff) != EXPECTED_REAL_WALK_DIFF_BYTES:
        raise ValueError(
            "v3.7 真实 walk 差异字节数异常："
            f"预期 {EXPECTED_REAL_WALK_DIFF_BYTES}，实际 {len(walk_diff)}"
        )

    replacements: list[dict[str, object]] = []
    documents: dict[str, dict[str, object] | bytes] = {}
    expected_content: dict[tuple[str, str], bytes] = {}
    changed_identities: list[tuple[str, str]] = []
    for index, (target, pamt_dir, content) in enumerate(baseline):
        final_content = tuned_walk if target == PHM_WALK_TARGET else content
        identity = (pamt_dir, target)
        if final_content != content:
            changed_identities.append(identity)
        payload_path = f"assets/{index:03d}/{Path(target).name}"
        replacements.append(
            _file_document(
                target=target,
                pamt_dir=pamt_dir,
                payload_path=payload_path,
                content=final_content,
            )
        )
        documents[payload_path] = final_content
        expected_content[identity] = final_content

    if changed_identities != [("0009", PHM_WALK_TARGET)]:
        raise ValueError("v3.7 相对 v3.6 的变化目标异常")

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
            "Preserves all v3.6 resources. Only the loop-end marker in each "
            "of four shallow-slope phase records is reduced by four."
        ),
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": "cdmm.female-outside-combat-quarter-author-shallow-phase",
        "name": MOD_NAME,
        "source": {
            "baseline_package": V36_PACKAGE_NAME,
            "baseline_sha256": V36_PACKAGE_SHA256,
            "format": "v3.6-shallow-loop-end-minus-four",
            "loop_end_adjustment": LOOP_END_ADJUSTMENT,
            "tuned_walk_phase": list(EXPECTED_TUNED_WALK_PHASE),
            "tuned_walk_sha256": hashlib.sha256(tuned_walk).hexdigest(),
            "tuned_walkfast_phase": list(EXPECTED_TUNED_WALKFAST_PHASE),
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
    parsed_content = {
        (file.pamt_dir, file.target): file.content for file in files
    }
    if package.legacy_json_patches or len(files) != EXPECTED_TOTAL_FILE_COUNT:
        raise ValueError("生成后的 v3.7 cdmod 组件数量异常")
    if parsed_content != expected_content:
        raise ValueError("生成后的 v3.7 资源载荷与预期不一致")
    if any(file.allow_new for file in files):
        raise ValueError("生成后的 v3.7 不应包含 allow_new")
    return output_path


def main() -> int:
    """生成游戏 mods 目录下的 v3.7 浅坡 phase 微调包。"""
    game_dir = Path(r"G:\SteamLibrary\steamapps\common\Crimson Desert")
    mods_dir = game_dir / "mods"
    baseline_path = mods_dir / V36_PACKAGE_NAME
    output_path = mods_dir / OUTPUT_FILE_NAME
    result = build_mod(baseline_path, output_path)
    print(f"已生成：{result}")
    print(f"浅坡 walk phase：{EXPECTED_TUNED_WALK_PHASE}")
    print(f"浅坡 walkfast phase：{EXPECTED_TUNED_WALKFAST_PHASE}")
    print("PAA/metabin：全部保持 v3.6")
    print(f"v3.7 SHA-256：{hashlib.sha256(result.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
