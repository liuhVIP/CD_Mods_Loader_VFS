"""生成“平地相位对齐浅坡 + 男性陡坡”v3.6 测试包。

v3.5 已把四条 25 度 PAA/metabin 恢复原版 PHW，但实机仍与 v3.4 几乎无差异。最终
active 载荷逐字节正确，说明剩余问题来自浅坡持续混合时的步态相位回算。本工具严格继承
v3.5，只把四条 25 度 phase 对齐到对应速度的作者平地 phase；所有动画资源保持不变。
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
from cdmm.tools.build_female_outside_combat_flat_phase_native_slope_mod import (
    PHW_SAMPLE_PATHS,
    _extract_phw_sample_paths,
    _phase_slice,
)
from cdmm.tools.build_female_outside_combat_native_shallow_male_steep_slope_mod import (
    EXPECTED_TOTAL_FILE_COUNT,
    OUTPUT_FILE_NAME as V35_PACKAGE_NAME,
    SHALLOW_SLOPE_SAMPLE_INDEXES,
    STEEP_SLOPE_SAMPLE_INDEXES,
)
from cdmm.tools.build_female_outside_combat_smooth_walk_mod import (
    PHM_WALK_TARGET,
    _file_document,
)

# v3.5 是只恢复浅坡 PHW 资源的直接基线，必须锁定哈希。
V35_PACKAGE_SHA256 = (
    "66a8c0a4d3bdf627e43b7d61832b5f25e9b47654af336af0b2dedf76293bb73f"
)

# 四条浅坡分别对齐同速度的平地 walk 或 walkfast 样本。
SHALLOW_TO_FLAT_SAMPLE = {
    1: 0,
    3: 0,
    6: 5,
    8: 5,
}
FLAT_SAMPLE_INDEXES = (0, 5)
EXPECTED_REAL_WALK_DIFF_BYTES = 16

# v3.6 测试包身份常量。
MOD_NAME = "Female Outside Combat Flat Aligned Shallow Male Steep Slope - Male Combat"
MOD_VERSION = "3.6-test"
OUTPUT_FILE_NAME = f"{MOD_NAME} v{MOD_VERSION}.cdmod"


def _load_v35_files(
    package_path: Path,
) -> tuple[tuple[str, str, bytes], ...]:
    """读取并严格验证 v3.5 的 73 项资源基线。"""
    digest = hashlib.sha256(package_path.read_bytes()).hexdigest()
    if digest != V35_PACKAGE_SHA256:
        raise ValueError(
            "v3.5 基线 SHA-256 不匹配："
            f"预期 {V35_PACKAGE_SHA256}，实际 {digest}"
        )

    package = load_cdmod_package(package_path)
    files = tuple(file for patch in package.file_patches for file in patch.files)
    if package.legacy_json_patches or len(files) != EXPECTED_TOTAL_FILE_COUNT:
        raise ValueError("v3.5 基线组件数量异常")
    identities = [(file.pamt_dir, file.target) for file in files]
    if len(identities) != len(set(identities)):
        raise ValueError("v3.5 基线存在重复资源目标")
    if sum(file.target == PHM_WALK_TARGET for file in files) != 1:
        raise ValueError("v3.5 基线的持续 walk 目标数量异常")
    return tuple((file.target, file.pamt_dir, file.content) for file in files)


def _align_shallow_phase_to_flat(v35_walk: bytes) -> bytes:
    """把四条 25 度 phase 对齐到对应速度的平地 phase。"""
    if _extract_phw_sample_paths(v35_walk) != PHW_SAMPLE_PATHS:
        raise ValueError("v3.5 walk 的 PHW 样本映射异常")
    if set(SHALLOW_TO_FLAT_SAMPLE) != set(SHALLOW_SLOPE_SAMPLE_INDEXES):
        raise ValueError("v3.6 浅坡/平地 phase 映射不完整")

    if all(
        v35_walk[_phase_slice(shallow)] == v35_walk[_phase_slice(flat)]
        for shallow, flat in SHALLOW_TO_FLAT_SAMPLE.items()
    ):
        raise ValueError("v3.5 浅坡 phase 已全部与平地对齐，实验失去单变量意义")

    tuned = bytearray(v35_walk)
    for shallow_index, flat_index in SHALLOW_TO_FLAT_SAMPLE.items():
        tuned[_phase_slice(shallow_index)] = v35_walk[_phase_slice(flat_index)]
    result = bytes(tuned)

    for sample_index in FLAT_SAMPLE_INDEXES + STEEP_SLOPE_SAMPLE_INDEXES:
        if result[_phase_slice(sample_index)] != v35_walk[
            _phase_slice(sample_index)
        ]:
            raise ValueError(f"v3.6 意外改变保留样本 {sample_index}")
    return result


def build_mod(
    baseline_path: Path,
    output_path: Path,
) -> Path:
    """生成相对 v3.5 只改变浅坡 phase 的 v3.6 包。"""
    baseline = _load_v35_files(baseline_path)
    v35_walk = next(
        content for target, _pamt_dir, content in baseline if target == PHM_WALK_TARGET
    )
    tuned_walk = _align_shallow_phase_to_flat(v35_walk)

    walk_diff = [
        index
        for index, (old, new) in enumerate(zip(v35_walk, tuned_walk))
        if old != new
    ]
    expected_offsets = {
        offset
        for shallow_index, flat_index in SHALLOW_TO_FLAT_SAMPLE.items()
        for offset in range(
            _phase_slice(shallow_index).start,
            _phase_slice(shallow_index).stop,
        )
        if v35_walk[offset] != v35_walk[
            _phase_slice(flat_index).start
            + offset
            - _phase_slice(shallow_index).start
        ]
    }
    if set(walk_diff) != expected_offsets:
        raise ValueError("v3.6 出现浅坡 phase 之外的 walk 字节变化")
    if len(walk_diff) != EXPECTED_REAL_WALK_DIFF_BYTES:
        raise ValueError(
            "v3.6 真实 walk 差异字节数异常："
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
        raise ValueError("v3.6 相对 v3.5 的变化目标异常")

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
            "Preserves every v3.5 resource and changes only four 25-degree "
            "phase records. Walk slopes align to the female flat-walk phase; "
            "walkfast slopes align to the female flat-walkfast phase."
        ),
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": "cdmm.female-outside-combat-flat-aligned-shallow-male-steep-slope",
        "name": MOD_NAME,
        "source": {
            "baseline_package": V35_PACKAGE_NAME,
            "baseline_sha256": V35_PACKAGE_SHA256,
            "format": "v3.5-shallow-phase-aligned-to-flat-speed-pair",
            "shallow_to_flat_sample": {
                str(key): value for key, value in SHALLOW_TO_FLAT_SAMPLE.items()
            },
            "tuned_walk_sha256": hashlib.sha256(tuned_walk).hexdigest(),
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
        raise ValueError("生成后的 v3.6 cdmod 组件数量异常")
    if parsed_content != expected_content:
        raise ValueError("生成后的 v3.6 资源载荷与预期不一致")
    if any(file.allow_new for file in files):
        raise ValueError("生成后的 v3.6 不应包含 allow_new")
    return output_path


def main() -> int:
    """生成游戏 mods 目录下的 v3.6 浅坡 phase 对齐测试包。"""
    game_dir = Path(r"G:\SteamLibrary\steamapps\common\Crimson Desert")
    mods_dir = game_dir / "mods"
    baseline_path = mods_dir / V35_PACKAGE_NAME
    output_path = mods_dir / OUTPUT_FILE_NAME
    result = build_mod(baseline_path, output_path)
    print(f"已生成：{result}")
    print("25 度 walk phase -> 作者平地 walk phase")
    print("25 度 walkfast phase -> 作者平地 walkfast phase")
    print("PAA/metabin：全部保持 v3.5")
    print(f"v3.6 SHA-256：{hashlib.sha256(result.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
