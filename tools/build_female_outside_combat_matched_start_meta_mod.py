"""生成“v2.3 稳定基线 + 六组女性起步 metabin”测试包。

v2.3 的六条普通起步 PAA 已逐字节等于对应原版 PHW PAA，但仍沿用 PHM metabin。
六组男女 metabin 的大小和字节均不同，因此第四步抖动可能来自女性动画与男性时长、事件
或 root-motion metadata 不匹配。本工具完整保留 v2.3 的 57 个目标，只新增六条男性
起步 metabin 目标，并写入一一对应的原版 PHW metabin；不修改任何 PAA、motionblending
或 PAAC。v2.4 的 sync 图已实机导致动画冻结和人物漂移，不得作为本版基础。
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
from cdmm.tools.build_female_walk_matched_start_mod import (
    METABIN_PAMT_DIR,
    PAA_PAMT_DIR,
    START_MOTION_PAIRS,
    _metabin_target,
    _paa_target,
)
from cdmm.tools.build_female_walk_single_branch_mod import _file_document

# 用户确认除第四步抖动外全部完美的唯一稳定基线。
V23_PACKAGE_NAME = "Female Outside Combat Smooth Walk - Male Combat v2.3-test.cdmod"
V23_PACKAGE_SHA256 = "f8f40df58068ea9a5a643193f2282299c75a14d9e02508a76f25a0f556478a61"
EXPECTED_V23_FILE_COUNT = 57

MOD_NAME = "Female Outside Combat Matched Start Meta - Male Combat"
MOD_VERSION = "2.5-test"
OUTPUT_FILE_NAME = f"{MOD_NAME} v{MOD_VERSION}.cdmod"


def _load_v23_content(baseline_path: Path) -> tuple[tuple[str, str, bytes], ...]:
    """读取并严格验证 v2.3 的有序稳定资源集合。"""
    actual_sha256 = hashlib.sha256(baseline_path.read_bytes()).hexdigest()
    if actual_sha256 != V23_PACKAGE_SHA256:
        raise ValueError(
            "v2.3 基线包 SHA-256 不匹配："
            f"预期 {V23_PACKAGE_SHA256}，实际 {actual_sha256}"
        )
    package = load_cdmod_package(baseline_path)
    files = [file for patch in package.file_patches for file in patch.files]
    if package.legacy_json_patches or len(files) != EXPECTED_V23_FILE_COUNT:
        raise ValueError("v2.3 基线包组件数量或类型异常")
    if any(file.allow_new or file.allow_table_replace for file in files):
        raise ValueError("v2.3 基线包意外包含新增资源或表覆盖")
    contents = tuple((file.target, file.pamt_dir, file.content) for file in files)
    targets = [target for target, _pamt_dir, _content in contents]
    if len(targets) != len(set(targets)):
        raise ValueError("v2.3 基线包包含重复目标")
    return contents


def build_mod(game_dir: Path, baseline_path: Path, output_path: Path) -> Path:
    """完整继承 v2.3，并为六条已替换 PAA 补齐女性 metabin。"""
    baseline = _load_v23_content(baseline_path)
    baseline_content = {target: content for target, _pamt_dir, content in baseline}

    replacements: list[dict[str, object]] = []
    documents: dict[str, dict[str, object] | bytes] = {}
    expected_content: dict[str, bytes] = {}
    for index, (target, pamt_dir, content) in enumerate(baseline):
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

    for pair_index, pair in enumerate(
        START_MOTION_PAIRS,
        start=len(replacements),
    ):
        male_paa_target = _paa_target(pair.male_reference)
        female_paa_source = _paa_target(pair.female_reference)
        female_paa_entry = _find_entry(game_dir, PAA_PAMT_DIR, female_paa_source)
        female_paa, _ = extract_plaintext(female_paa_entry)
        if baseline_content.get(male_paa_target) != female_paa:
            raise ValueError(f"v2.3 起步 PAA 与女性来源不一致：{male_paa_target}")

        male_meta_target = _metabin_target(pair.male_reference)
        female_meta_source = _metabin_target(pair.female_reference)
        male_meta_entry = _find_entry(game_dir, METABIN_PAMT_DIR, male_meta_target)
        female_meta_entry = _find_entry(game_dir, METABIN_PAMT_DIR, female_meta_source)
        male_meta, _ = extract_plaintext(male_meta_entry)
        female_meta, _ = extract_plaintext(female_meta_entry)
        if male_meta == female_meta:
            raise ValueError(f"男女起步 metabin 意外相同：{male_meta_target}")
        payload_path = f"assets/{pair_index:03d}/{Path(male_meta_target).name}"
        replacements.append(
            _file_document(
                target=male_meta_target,
                pamt_dir=METABIN_PAMT_DIR,
                payload_path=payload_path,
                content=female_meta,
            )
        )
        documents[payload_path] = female_meta
        expected_content[male_meta_target] = female_meta

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
            "Preserves every verified v2.3 resource byte and adds only the six "
            "native PHW metadata files matching its already-female normal-walk "
            "start PAA payloads, aligning transition timing and root-motion metadata."
        ),
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": "cdmm.female-outside-combat-matched-start-meta-male-combat",
        "name": MOD_NAME,
        "source": {
            "baseline_package": V23_PACKAGE_NAME,
            "baseline_sha256": V23_PACKAGE_SHA256,
            "format": "v2.3-plus-six-native-phw-start-metabin",
            "start_pair_count": len(START_MOTION_PAIRS),
        },
        "version": MOD_VERSION,
    }
    documents["manifest.json"] = manifest
    documents["files/replacements.json"] = {
        "schema": 1,
        "files": replacements,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_cdmod_zip(output_path, documents)

    package = load_cdmod_package(output_path)
    files = [file for patch in package.file_patches for file in patch.files]
    parsed_content = {file.target: file.content for file in files}
    expected_count = EXPECTED_V23_FILE_COUNT + len(START_MOTION_PAIRS)
    if package.legacy_json_patches or len(files) != expected_count:
        raise ValueError("生成后的 v2.5 cdmod 组件数量异常")
    if parsed_content != expected_content:
        raise ValueError("生成后的 v2.5 资源载荷与预期不一致")
    if any(file.allow_new or file.allow_table_replace for file in files):
        raise ValueError("生成后的 v2.5 意外包含新增资源或表覆盖")
    return output_path


def main() -> int:
    """生成游戏 mods 目录下的新测试包。"""
    game_dir = Path(r"G:\SteamLibrary\steamapps\common\Crimson Desert")
    baseline_path = game_dir / "mods" / V23_PACKAGE_NAME
    output_path = game_dir / "mods" / OUTPUT_FILE_NAME
    result = build_mod(game_dir, baseline_path, output_path)
    print(f"已生成：{result}")
    print(f"SHA-256：{hashlib.sha256(result.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
