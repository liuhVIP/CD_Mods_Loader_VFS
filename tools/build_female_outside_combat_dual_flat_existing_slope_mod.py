"""生成“双平地调相 + 现有坡度槽男性载荷”v3.4 测试包。

v3.3 把平地 walkfast phase 恢复为当前原版 PHW 后，实机重新出现与旧版相同的四步
姿势重置。本工具严格以 v3.3 为基线，只把平地 walkfast 索引 5 的 phase 恢复为作者值；
平地慢走索引 0、八条 PHM 坡度 phase、现有 PHW 坡度目标中的 PHM PAA/metabin 载荷，
以及 v2.3 继承的其余资源全部保持不变。
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
from cdmm.tools.build_female_outside_combat_author_phase_walk_mod import (
    AUTHOR_MOD_DIRECTORY_NAME,
    AUTHOR_WALK_RELATIVE_PATH,
    AUTHOR_WALK_SHA256,
    AUTHOR_WALK_SIZE,
    _load_author_walk,
)
from cdmm.tools.build_female_outside_combat_flat_phase_native_slope_mod import (
    PHASE_VALUES_SIZE,
    PHW_SAMPLE_PATHS,
    SLOPE_SAMPLE_INDEXES,
    _extract_phw_sample_paths,
    _phase_slice,
)
from cdmm.tools.build_female_outside_combat_self_tuned_existing_slope_mod import (
    EXPECTED_TOTAL_FILE_COUNT,
    FEMALE_FLAT_WALK_SAMPLE_INDEX,
    OUTPUT_FILE_NAME as V33_PACKAGE_NAME,
)
from cdmm.tools.build_female_outside_combat_smooth_walk_mod import (
    PHM_WALK_TARGET,
    _file_document,
)

# v3.3 是本轮唯一输入基线，哈希用于防止误拿其他测试版本继续叠加。
V33_PACKAGE_SHA256 = (
    "7001ea52ee831b91673c51d03f4c0c50843b26356347de884192297a8b7799e6"
)

# 平地 walkfast 是本版唯一允许变化的 phase 记录。
FEMALE_FLAT_WALKFAST_SAMPLE_INDEX = 5
EXPECTED_REAL_WALK_DIFF_BYTES = 4

# v3.4 测试包身份常量。
MOD_NAME = "Female Outside Combat Dual Flat Phase Existing Male Slope - Male Combat"
MOD_VERSION = "3.4-test"
OUTPUT_FILE_NAME = f"{MOD_NAME} v{MOD_VERSION}.cdmod"


def _load_v33_files(
    package_path: Path,
) -> tuple[tuple[str, str, bytes], ...]:
    """读取并严格验证 v3.3 的 73 项资源基线。"""
    digest = hashlib.sha256(package_path.read_bytes()).hexdigest()
    if digest != V33_PACKAGE_SHA256:
        raise ValueError(
            "v3.3 基线 SHA-256 不匹配："
            f"预期 {V33_PACKAGE_SHA256}，实际 {digest}"
        )

    package = load_cdmod_package(package_path)
    files = tuple(file for patch in package.file_patches for file in patch.files)
    if package.legacy_json_patches or len(files) != EXPECTED_TOTAL_FILE_COUNT:
        raise ValueError("v3.3 基线组件数量异常")

    identities = [(file.pamt_dir, file.target) for file in files]
    if len(identities) != len(set(identities)):
        raise ValueError("v3.3 基线存在重复资源目标")
    if sum(file.target == PHM_WALK_TARGET for file in files) != 1:
        raise ValueError("v3.3 基线的持续 walk 目标数量异常")
    return tuple((file.target, file.pamt_dir, file.content) for file in files)


def _restore_author_walkfast_phase(v33_walk: bytes, author: bytes) -> bytes:
    """只把 v3.3 的平地 walkfast phase 恢复为作者值。"""
    if len(v33_walk) != AUTHOR_WALK_SIZE or len(author) != AUTHOR_WALK_SIZE:
        raise ValueError("v3.3/作者 walk 大小不匹配")
    v33_references = _extract_phw_sample_paths(v33_walk)
    author_references = _extract_phw_sample_paths(author)
    if v33_references != PHW_SAMPLE_PATHS or author_references != PHW_SAMPLE_PATHS:
        raise ValueError("v3.3/作者 walk 的 PHW 样本映射不一致")

    walkfast_slice = _phase_slice(FEMALE_FLAT_WALKFAST_SAMPLE_INDEX)
    if len(author[walkfast_slice]) != PHASE_VALUES_SIZE:
        raise ValueError("作者平地 walkfast phase 区间大小异常")
    if v33_walk[walkfast_slice] == author[walkfast_slice]:
        raise ValueError("v3.3 的平地 walkfast 已是作者值，实验失去单变量意义")

    tuned = bytearray(v33_walk)
    tuned[walkfast_slice] = author[walkfast_slice]
    result = bytes(tuned)

    if result[_phase_slice(FEMALE_FLAT_WALK_SAMPLE_INDEX)] != v33_walk[
        _phase_slice(FEMALE_FLAT_WALK_SAMPLE_INDEX)
    ]:
        raise ValueError("v3.4 意外改变平地慢走 phase")
    for sample_index in SLOPE_SAMPLE_INDEXES:
        if result[_phase_slice(sample_index)] != v33_walk[
            _phase_slice(sample_index)
        ]:
            raise ValueError(f"v3.4 意外改变坡度样本 {sample_index}")
    return result


def build_mod(
    baseline_path: Path,
    author_walk_path: Path,
    output_path: Path,
) -> Path:
    """生成相对 v3.3 只恢复作者 walkfast phase 的 v3.4 包。"""
    baseline = _load_v33_files(baseline_path)
    author = _load_author_walk(author_walk_path)
    v33_walk = next(
        content for target, _pamt_dir, content in baseline if target == PHM_WALK_TARGET
    )
    tuned_walk = _restore_author_walkfast_phase(v33_walk, author)
    walk_diff = [
        index
        for index, (old, new) in enumerate(zip(v33_walk, tuned_walk))
        if old != new
    ]
    expected_offsets = {
        index
        for index in range(
            _phase_slice(FEMALE_FLAT_WALKFAST_SAMPLE_INDEX).start,
            _phase_slice(FEMALE_FLAT_WALKFAST_SAMPLE_INDEX).stop,
        )
        if v33_walk[index] != author[index]
    }
    if set(walk_diff) != expected_offsets:
        raise ValueError("v3.4 出现平地 walkfast phase 之外的字节变化")
    if len(walk_diff) != EXPECTED_REAL_WALK_DIFF_BYTES:
        raise ValueError(
            "v3.4 真实 walk 差异字节数异常："
            f"预期 {EXPECTED_REAL_WALK_DIFF_BYTES}，实际 {len(walk_diff)}"
        )

    replacements: list[dict[str, object]] = []
    documents: dict[str, dict[str, object] | bytes] = {}
    expected_content: dict[tuple[str, str], bytes] = {}
    for index, (target, pamt_dir, content) in enumerate(baseline):
        final_content = tuned_walk if target == PHM_WALK_TARGET else content
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
        expected_content[(pamt_dir, target)] = final_content

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
            "Preserves all 73 v3.3 targets and changes only the flat walkfast "
            "phase record back to Andyground's paired value. Flat walk remains "
            "female; existing PHW slope slots still carry native PHM payloads."
        ),
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": "cdmm.female-outside-combat-dual-flat-existing-male-slope",
        "name": MOD_NAME,
        "source": {
            "author_walk_sha256": AUTHOR_WALK_SHA256,
            "baseline_package": V33_PACKAGE_NAME,
            "baseline_sha256": V33_PACKAGE_SHA256,
            "changed_sample_index": FEMALE_FLAT_WALKFAST_SAMPLE_INDEX,
            "format": "v3.3-plus-author-flat-walkfast-phase",
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
        raise ValueError("生成后的 v3.4 cdmod 组件数量异常")
    if parsed_content != expected_content:
        raise ValueError("生成后的 v3.4 资源载荷与预期不一致")
    if any(file.allow_new for file in files):
        raise ValueError("生成后的 v3.4 不应包含 allow_new")
    return output_path


def main() -> int:
    """生成游戏 mods 目录下的 v3.4 双平地 phase 测试包。"""
    game_dir = Path(r"G:\SteamLibrary\steamapps\common\Crimson Desert")
    mods_dir = game_dir / "mods"
    baseline_path = mods_dir / V33_PACKAGE_NAME
    author_walk_path = (
        mods_dir / AUTHOR_MOD_DIRECTORY_NAME / AUTHOR_WALK_RELATIVE_PATH
    )
    output_path = mods_dir / OUTPUT_FILE_NAME
    result = build_mod(baseline_path, author_walk_path, output_path)
    print(f"已生成：{result}")
    print("平地 walk/walkfast：作者配对 phase")
    print("坡度：保持 v3.3 的现有 PHW 目标承载 PHM PAA/metabin/phase")
    print(f"v3.4 SHA-256：{hashlib.sha256(result.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
