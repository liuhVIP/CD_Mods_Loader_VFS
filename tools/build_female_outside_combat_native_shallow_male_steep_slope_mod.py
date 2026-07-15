"""生成“原生女性浅坡 + 原生男性陡坡”v3.5 测试包。

v3.4 实机已确认平地与 50 度大坡稳定，但上下 25 度长缓坡仍会出现半步收回、轻微瘸脚。
这说明问题集中在女性平地与男性浅坡样本的持续混合。本工具严格继承 v3.4，只把 walk 与
walkfast 的上下 25 度 phase、PAA 和 metabin 恢复为当前原版 PHW；四条 50 度样本继续
使用 v3.4 的原版 PHM 载荷。
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
from cdmm.tools.build_female_outside_combat_dual_flat_existing_slope_mod import (
    OUTPUT_FILE_NAME as V34_PACKAGE_NAME,
)
from cdmm.tools.build_female_outside_combat_flat_phase_native_slope_mod import (
    FLAT_SAMPLE_INDEXES,
    PHW_SAMPLE_PATHS,
    _extract_phw_sample_paths,
    _load_native_phw_walk,
    _phase_slice,
)
from cdmm.tools.build_female_outside_combat_self_tuned_existing_slope_mod import (
    EXPECTED_TOTAL_FILE_COUNT,
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

# v3.4 是当前最佳女性走路基线，必须原样保留并只作为输入读取。
V34_PACKAGE_SHA256 = (
    "fbf6fdc62c2d1f3c52d2a186e7e5a5d239c91bb9d2aaec9d73ab489923922678"
)

# 25 度浅坡恢复 PHW；50 度陡坡继续沿用 v3.4 的 PHM 载荷。
SHALLOW_SLOPE_SAMPLE_INDEXES = (1, 3, 6, 8)
STEEP_SLOPE_SAMPLE_INDEXES = (2, 4, 7, 9)
EXPECTED_SHALLOW_RESOURCE_TARGETS = len(SHALLOW_SLOPE_SAMPLE_INDEXES) * 2
EXPECTED_CHANGED_TARGETS = 1 + EXPECTED_SHALLOW_RESOURCE_TARGETS
EXPECTED_REAL_WALK_DIFF_BYTES = 9

# v3.5 测试包身份常量。
MOD_NAME = "Female Outside Combat Native Shallow Male Steep Slope - Male Combat"
MOD_VERSION = "3.5-test"
OUTPUT_FILE_NAME = f"{MOD_NAME} v{MOD_VERSION}.cdmod"


def _load_v34_files(
    package_path: Path,
) -> tuple[tuple[str, str, bytes], ...]:
    """读取并严格验证 v3.4 的 73 项资源基线。"""
    digest = hashlib.sha256(package_path.read_bytes()).hexdigest()
    if digest != V34_PACKAGE_SHA256:
        raise ValueError(
            "v3.4 基线 SHA-256 不匹配："
            f"预期 {V34_PACKAGE_SHA256}，实际 {digest}"
        )

    package = load_cdmod_package(package_path)
    files = tuple(file for patch in package.file_patches for file in patch.files)
    if package.legacy_json_patches or len(files) != EXPECTED_TOTAL_FILE_COUNT:
        raise ValueError("v3.4 基线组件数量异常")
    identities = [(file.pamt_dir, file.target) for file in files]
    if len(identities) != len(set(identities)):
        raise ValueError("v3.4 基线存在重复资源目标")
    if sum(file.target == PHM_WALK_TARGET for file in files) != 1:
        raise ValueError("v3.4 基线的持续 walk 目标数量异常")
    return tuple((file.target, file.pamt_dir, file.content) for file in files)


def _restore_native_phw_shallow_phase(
    v34_walk: bytes,
    native_phw: bytes,
) -> bytes:
    """只把四条 25 度浅坡 phase 恢复为当前原版 PHW。"""
    if len(v34_walk) != len(native_phw):
        raise ValueError("v3.4/原版 PHW walk 大小不匹配")
    if _extract_phw_sample_paths(v34_walk) != PHW_SAMPLE_PATHS:
        raise ValueError("v3.4 walk 的 PHW 样本映射异常")
    if _extract_phw_sample_paths(native_phw) != PHW_SAMPLE_PATHS:
        raise ValueError("原版 PHW walk 的样本映射异常")

    if all(
        v34_walk[_phase_slice(index)] == native_phw[_phase_slice(index)]
        for index in SHALLOW_SLOPE_SAMPLE_INDEXES
    ):
        raise ValueError("v3.4 浅坡 phase 已全部是 PHW，实验失去单变量意义")

    tuned = bytearray(v34_walk)
    for sample_index in SHALLOW_SLOPE_SAMPLE_INDEXES:
        tuned[_phase_slice(sample_index)] = native_phw[_phase_slice(sample_index)]
    result = bytes(tuned)

    for sample_index in FLAT_SAMPLE_INDEXES + STEEP_SLOPE_SAMPLE_INDEXES:
        if result[_phase_slice(sample_index)] != v34_walk[
            _phase_slice(sample_index)
        ]:
            raise ValueError(f"v3.5 意外改变保留样本 {sample_index}")
    return result


def _shallow_resource_identities() -> tuple[tuple[str, str], ...]:
    """返回四条 25 度 PHW PAA/metabin 的既有目标身份。"""
    identities: list[tuple[str, str]] = []
    for sample_index in SHALLOW_SLOPE_SAMPLE_INDEXES:
        reference = PHW_SAMPLE_PATHS[sample_index]
        identities.extend(
            (
                (PAA_PAMT_DIR, _paa_target(reference)),
                (METABIN_PAMT_DIR, _metabin_target(reference)),
            )
        )
    result = tuple(identities)
    if len(result) != EXPECTED_SHALLOW_RESOURCE_TARGETS:
        raise ValueError("v3.5 浅坡资源目标数量异常")
    if len(result) != len(set(result)):
        raise ValueError("v3.5 浅坡资源目标重复")
    return result


def build_mod(
    game_dir: Path,
    baseline_path: Path,
    output_path: Path,
) -> Path:
    """生成只恢复四条 25 度 PHW 样本的 v3.5 包。"""
    baseline = _load_v34_files(baseline_path)
    baseline_content = {
        (pamt_dir, target): content for target, pamt_dir, content in baseline
    }
    native_phw = _load_native_phw_walk(game_dir)
    v34_walk = next(
        content for target, _pamt_dir, content in baseline if target == PHM_WALK_TARGET
    )
    tuned_walk = _restore_native_phw_shallow_phase(v34_walk, native_phw)

    walk_diff = [
        index
        for index, (old, new) in enumerate(zip(v34_walk, tuned_walk))
        if old != new
    ]
    expected_walk_offsets = {
        offset
        for sample_index in SHALLOW_SLOPE_SAMPLE_INDEXES
        for offset in range(
            _phase_slice(sample_index).start,
            _phase_slice(sample_index).stop,
        )
        if v34_walk[offset] != native_phw[offset]
    }
    if set(walk_diff) != expected_walk_offsets:
        raise ValueError("v3.5 出现浅坡 phase 之外的 walk 字节变化")
    if len(walk_diff) != EXPECTED_REAL_WALK_DIFF_BYTES:
        raise ValueError(
            "v3.5 真实 walk 差异字节数异常："
            f"预期 {EXPECTED_REAL_WALK_DIFF_BYTES}，实际 {len(walk_diff)}"
        )

    overrides: dict[tuple[str, str], bytes] = {
        ("0009", PHM_WALK_TARGET): tuned_walk
    }
    for pamt_dir, target in _shallow_resource_identities():
        entry = _find_entry(game_dir, pamt_dir, target)
        native_content, _compression_type = extract_plaintext(entry)
        if baseline_content[(pamt_dir, target)] == native_content:
            raise ValueError(f"v3.4 浅坡目标意外已经是原版 PHW：{target}")
        overrides[(pamt_dir, target)] = native_content

    if len(overrides) != EXPECTED_CHANGED_TARGETS:
        raise ValueError("v3.5 覆盖目标数量异常")

    replacements: list[dict[str, object]] = []
    documents: dict[str, dict[str, object] | bytes] = {}
    expected_content: dict[tuple[str, str], bytes] = {}
    for index, (target, pamt_dir, content) in enumerate(baseline):
        identity = (pamt_dir, target)
        final_content = overrides.get(identity, content)
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

    changed_identities = {
        identity
        for identity, content in expected_content.items()
        if content != baseline_content[identity]
    }
    if changed_identities != set(overrides):
        raise ValueError("v3.5 相对 v3.4 的实际变化目标异常")

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
            "Preserves the v3.4 female flat phases, restores native PHW PAA, "
            "metabin and phases for all four 25-degree shallow-slope samples, "
            "and keeps native PHM payloads for all four 50-degree steep slopes."
        ),
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": "cdmm.female-outside-combat-native-shallow-male-steep-slope",
        "name": MOD_NAME,
        "source": {
            "baseline_package": V34_PACKAGE_NAME,
            "baseline_sha256": V34_PACKAGE_SHA256,
            "format": "v3.4-native-phw-25-degree-native-phm-50-degree",
            "shallow_sample_indexes": list(SHALLOW_SLOPE_SAMPLE_INDEXES),
            "steep_sample_indexes": list(STEEP_SLOPE_SAMPLE_INDEXES),
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
        raise ValueError("生成后的 v3.5 cdmod 组件数量异常")
    if parsed_content != expected_content:
        raise ValueError("生成后的 v3.5 资源载荷与预期不一致")
    if any(file.allow_new for file in files):
        raise ValueError("生成后的 v3.5 不应包含 allow_new")
    return output_path


def main() -> int:
    """生成游戏 mods 目录下的 v3.5 浅坡/陡坡分流测试包。"""
    game_dir = Path(r"G:\SteamLibrary\steamapps\common\Crimson Desert")
    mods_dir = game_dir / "mods"
    baseline_path = mods_dir / V34_PACKAGE_NAME
    output_path = mods_dir / OUTPUT_FILE_NAME
    result = build_mod(game_dir, baseline_path, output_path)
    print(f"已生成：{result}")
    print("25 度上下坡：当前原版 PHW phase/PAA/metabin")
    print("50 度上下坡：保持 v3.4 的当前原版 PHM phase/PAA/metabin")
    print(f"v3.5 SHA-256：{hashlib.sha256(result.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
