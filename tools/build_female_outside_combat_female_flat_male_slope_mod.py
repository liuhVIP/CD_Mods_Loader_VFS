"""生成“平地女性、坡面男性”v3.1 测试包。

v3.0 已实机确认平地正常，但上坡仍一步一抖。本工具保留作者 mixed walk 的两条平地
PHW 样本及其 phase，把八条上下坡 PHW 引用等长切到独立 PHM ``_base`` 别名，并为每条
别名新增当前原版男性 PAA 与配对 metabin 载荷；八条坡度 phase 同时使用当前原版 PHM
数值。v2.3 的其他 56 项、起步、PAAC、CharacterInfo 与战斗资源全部不变。
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
    AUTHOR_WALK_SHA256,
    AUTHOR_WALK_SIZE,
    _load_author_walk,
    _replace_author_phase_walk,
)
from cdmm.tools.build_female_outside_combat_flat_phase_native_slope_mod import (
    FLAT_SAMPLE_INDEXES,
    PAA_REFERENCE_PATTERN,
    PHASE_RECORD_STRIDE,
    PHASE_VALUES_OFFSET,
    PHASE_VALUES_SIZE,
    PHW_SAMPLE_PATHS,
    SLOPE_SAMPLE_INDEXES,
    _extract_phw_sample_paths,
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
from cdmm.services.pamt_index_service import get_game_pamt_index

# 当前原版 PHM mixed walk 提供男性坡度样本顺序与配套 phase。
NATIVE_PHM_WALK_TARGET = (
    "character/binary/motionblending/phm_locomotion/basic_move_walk.motionblending"
)
NATIVE_PHM_WALK_SHA256 = (
    "55d1603fb72a30b68ff39ee5e7ba5dbf4aa186698e92f589ed741b8bf7ac79f2"
)
NATIVE_PHM_WALK_SIZE = 16_306
NATIVE_PHM_PHASE_RECORD_BASE_OFFSET = 0x0EC9

# 原版 PHM mixed walk 最后十条样本，与 PHW/phase 索引逐项对应。
PHM_SAMPLE_PATHS = (
    "1_pc/1_phm/cd_phm_basic_00_00_nor_move_walk_ing_00.paa",
    "1_pc/1_phm/cd_phm_basic_00_00_nor_move_walk_fd_25_ing_00.paa",
    "1_pc/1_phm/cd_phm_basic_00_00_nor_move_walk_fd_50_ing_00.paa",
    "1_pc/1_phm/cd_phm_basic_00_00_nor_move_walk_fu_25_ing_00.paa",
    "1_pc/1_phm/cd_phm_basic_00_00_nor_move_walk_fu_50_ing_00.paa",
    "1_pc/1_phm/cd_phm_basic_00_00_nor_move_walkfast_f_ing_00.paa",
    "1_pc/1_phm/cd_phm_basic_00_00_nor_move_walkfast_fd_25_ing_00.paa",
    "1_pc/1_phm/cd_phm_basic_00_00_nor_move_walkfast_fd_50_ing_00.paa",
    "1_pc/1_phm/cd_phm_basic_00_00_nor_move_walkfast_fu_25_ing_00.paa",
    "1_pc/1_phm/cd_phm_basic_00_00_nor_move_walkfast_fu_50_ing_00.paa",
)

EXPECTED_EXISTING_TARGET_COUNT = EXPECTED_V23_FILE_COUNT
EXPECTED_ALIAS_PAIR_COUNT = len(SLOPE_SAMPLE_INDEXES)
EXPECTED_NEW_ALIAS_COUNT = EXPECTED_ALIAS_PAIR_COUNT * 2
EXPECTED_TOTAL_FILE_COUNT = EXPECTED_EXISTING_TARGET_COUNT + EXPECTED_NEW_ALIAS_COUNT

MOD_NAME = "Female Outside Combat Female Flat Male Slope - Male Combat"
MOD_VERSION = "3.1-test"
OUTPUT_FILE_NAME = f"{MOD_NAME} v{MOD_VERSION}.cdmod"


def _extract_phm_sample_paths(content: bytes) -> tuple[str, ...]:
    """按序提取原版 PHM mixed walk 的最后十条男性 PAA 引用。"""
    references = tuple(
        match.group().decode("ascii")
        for match in PAA_REFERENCE_PATTERN.finditer(content)
    )
    if tuple(references[-len(PHM_SAMPLE_PATHS):]) != PHM_SAMPLE_PATHS:
        raise ValueError("原版 PHM walk 样本顺序与已审计 phase 映射不一致")
    return PHM_SAMPLE_PATHS


def _male_slope_alias_reference(phw_reference: str) -> str:
    """把 PHW 坡度引用等长转换为独立 PHM ``_base`` 别名。"""
    if phw_reference.count("1_pc/2_phw/") != 1:
        raise ValueError(f"PHW 坡度引用目录异常：{phw_reference}")
    if phw_reference.count("cd_phw_") != 1 or "_base_move_" not in phw_reference:
        raise ValueError(f"PHW 坡度引用名称异常：{phw_reference}")
    alias = phw_reference.replace("1_pc/2_phw/", "1_pc/1_phm/", 1).replace(
        "cd_phw_",
        "cd_phm_",
        1,
    )
    if len(alias) != len(phw_reference):
        raise ValueError("PHM 坡度别名与 PHW 来源长度不一致")
    return alias


SLOPE_ALIAS_REFERENCES = tuple(
    _male_slope_alias_reference(PHW_SAMPLE_PATHS[index])
    for index in SLOPE_SAMPLE_INDEXES
)
SLOPE_NATIVE_REFERENCES = tuple(
    PHM_SAMPLE_PATHS[index] for index in SLOPE_SAMPLE_INDEXES
)


def _native_phm_phase_slice(sample_index: int) -> slice:
    """返回原版 PHM 文件中指定样本的八字节 phase 区间。"""
    start = (
        NATIVE_PHM_PHASE_RECORD_BASE_OFFSET
        + PHASE_RECORD_STRIDE * sample_index
        + PHASE_VALUES_OFFSET
    )
    return slice(start, start + PHASE_VALUES_SIZE)


def _load_native_phm_walk(game_dir: Path) -> bytes:
    """从当前原版 0009 唯一提取并验证 PHM mixed walk。"""
    entry = _find_entry(game_dir, PAA_PAMT_DIR, NATIVE_PHM_WALK_TARGET)
    content, _ = extract_plaintext(entry)
    digest = hashlib.sha256(content).hexdigest()
    if digest != NATIVE_PHM_WALK_SHA256:
        raise ValueError(
            "当前原版 PHM walk SHA-256 不匹配："
            f"预期 {NATIVE_PHM_WALK_SHA256}，实际 {digest}"
        )
    if len(content) != NATIVE_PHM_WALK_SIZE:
        raise ValueError("当前原版 PHM walk 大小不匹配")
    _extract_phm_sample_paths(content)
    return content


def _merge_female_flat_male_slope(
    author: bytes,
    native_phm: bytes,
    slope_alias_references: tuple[str, ...] = SLOPE_ALIAS_REFERENCES,
) -> bytes:
    """保留作者平地女性数据，只替换八条男性坡度引用与 PHM phase。"""
    if len(author) != AUTHOR_WALK_SIZE or len(native_phm) != NATIVE_PHM_WALK_SIZE:
        raise ValueError("作者/原版 PHM walk 大小不匹配，拒绝固定布局合并")
    _extract_phw_sample_paths(author)
    _extract_phm_sample_paths(native_phm)

    result = author
    for sample_index, alias_reference in zip(
        SLOPE_SAMPLE_INDEXES,
        slope_alias_references,
    ):
        phw_reference = PHW_SAMPLE_PATHS[sample_index]
        source = phw_reference.encode("ascii")
        alias = alias_reference.encode("ascii")
        if result.count(source) != 1 or result.count(alias) != 0:
            raise ValueError(f"作者 walk 坡度引用数量异常：{phw_reference}")
        result = result.replace(source, alias, 1)
    if len(result) != len(author):
        raise ValueError("坡度引用别名修改后 walk 长度发生变化")

    hybrid = bytearray(result)
    for sample_index in SLOPE_SAMPLE_INDEXES:
        target_slice = _phase_slice(sample_index)
        source_slice = _native_phm_phase_slice(sample_index)
        hybrid[target_slice] = native_phm[source_slice]
    merged = bytes(hybrid)

    for sample_index in FLAT_SAMPLE_INDEXES:
        phase_slice = _phase_slice(sample_index)
        if merged[phase_slice] != author[phase_slice]:
            raise ValueError(f"平地样本 {sample_index} 未保留作者 phase")
        if PHW_SAMPLE_PATHS[sample_index].encode("ascii") not in merged:
            raise ValueError(f"平地样本 {sample_index} 未保留 PHW 引用")
    for sample_index, alias_reference in zip(
        SLOPE_SAMPLE_INDEXES,
        slope_alias_references,
    ):
        if alias_reference.encode("ascii") not in merged:
            raise ValueError(f"坡度样本 {sample_index} 未切到 PHM 别名")
        if merged[_phase_slice(sample_index)] != native_phm[
            _native_phm_phase_slice(sample_index)
        ]:
            raise ValueError(f"坡度样本 {sample_index} 未使用原版 PHM phase")
    return merged


def _new_alias_document(
    *,
    target: str,
    pamt_dir: str,
    payload_path: str,
    content: bytes,
) -> dict[str, object]:
    """构造允许在明确 PAMT 目录新增的男性坡度动作别名。"""
    document = _file_document(
        target=target,
        pamt_dir=pamt_dir,
        payload_path=payload_path,
        content=content,
    )
    document["allow_new"] = True
    return document


def build_mod(
    game_dir: Path,
    baseline_path: Path,
    author_walk_path: Path,
    output_path: Path,
    *,
    slope_alias_references: tuple[str, ...] = SLOPE_ALIAS_REFERENCES,
    mod_name: str = MOD_NAME,
    mod_version: str = MOD_VERSION,
    mod_id: str = "cdmm.female-outside-combat-female-flat-male-slope-male-combat",
    source_format: str = "v2.3-plus-author-female-flat-native-phm-slope-aliases",
    require_new_alias_targets: bool = False,
) -> Path:
    """完整继承 v2.3，并加入唯一 walk 变化与十六个男性坡度别名。"""
    baseline = _load_v23_files(baseline_path)
    author = _load_author_walk(author_walk_path)
    native_phm = _load_native_phm_walk(game_dir)
    if len(slope_alias_references) != EXPECTED_ALIAS_PAIR_COUNT:
        raise ValueError("男性坡度别名引用数量异常")
    hybrid = _merge_female_flat_male_slope(
        author,
        native_phm,
        slope_alias_references,
    )
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

    asset_index = len(replacements)
    alias_targets: list[str] = []
    for alias_reference, native_reference in zip(
        slope_alias_references,
        SLOPE_NATIVE_REFERENCES,
    ):
        for kind, pamt_dir, target_builder in (
            ("paa", PAA_PAMT_DIR, _paa_target),
            ("metabin", METABIN_PAMT_DIR, _metabin_target),
        ):
            alias_target = target_builder(alias_reference)
            native_target = target_builder(native_reference)
            if (
                require_new_alias_targets
                and get_game_pamt_index(game_dir).find_in_dir(pamt_dir, alias_target)
                is not None
            ):
                raise ValueError(f"男性坡度别名目标已被原版 basename 占用：{alias_target}")
            source_entry = _find_entry(game_dir, pamt_dir, native_target)
            content, _ = extract_plaintext(source_entry)
            payload_path = f"assets/{asset_index:03d}/{kind}_{Path(alias_target).name}"
            replacements.append(
                _new_alias_document(
                    target=alias_target,
                    pamt_dir=pamt_dir,
                    payload_path=payload_path,
                    content=content,
                )
            )
            documents[payload_path] = content
            expected_content[alias_target] = content
            alias_targets.append(alias_target)
            asset_index += 1

    if len(alias_targets) != EXPECTED_NEW_ALIAS_COUNT:
        raise ValueError("男性坡度别名数量异常")
    if len(alias_targets) != len(set(alias_targets)):
        raise ValueError("男性坡度别名目标重复")

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
            "Preserves the verified v2.3 baseline and Andyground's stable flat "
            "female walk. Eight slope samples use equal-length PHM aliases backed "
            "by current native male PAA/metabin bytes and native PHM phase values."
        ),
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": mod_id,
        "name": mod_name,
        "source": {
            "alias_pair_count": EXPECTED_ALIAS_PAIR_COUNT,
            "author_walk_sha256": AUTHOR_WALK_SHA256,
            "baseline_package": V23_PACKAGE_NAME,
            "baseline_sha256": V23_PACKAGE_SHA256,
            "format": source_format,
            "hybrid_walk_sha256": hybrid_sha256,
            "native_phm_walk_sha256": NATIVE_PHM_WALK_SHA256,
            "slope_sample_indexes": list(SLOPE_SAMPLE_INDEXES),
            "walk_target": PHM_WALK_TARGET,
        },
        "version": mod_version,
    }
    documents["manifest.json"] = manifest
    documents["files/replacements.json"] = {"schema": 1, "files": replacements}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_cdmod_zip(output_path, documents)

    package = load_cdmod_package(output_path)
    files = [file for patch in package.file_patches for file in patch.files]
    parsed_content = {file.target: file.content for file in files}
    if package.legacy_json_patches or len(files) != EXPECTED_TOTAL_FILE_COUNT:
        raise ValueError(f"生成后的 {mod_version} cdmod 组件数量异常")
    if parsed_content != expected_content:
        raise ValueError(f"生成后的 {mod_version} 资源载荷与预期不一致")
    if parsed_content[PHM_WALK_TARGET] != hybrid:
        raise ValueError(f"生成后的 {mod_version} 未保留平地女/坡面男组合 walk")
    allow_new_targets = {file.target for file in files if file.allow_new}
    if allow_new_targets != set(alias_targets):
        raise ValueError(f"生成后的 {mod_version} allow_new 目标异常")
    return output_path


def main() -> int:
    """生成游戏 mods 目录下的 v3.1 平地女/坡面男测试包。"""
    game_dir = Path(r"G:\SteamLibrary\steamapps\common\Crimson Desert")
    mods_dir = game_dir / "mods"
    baseline_path = mods_dir / V23_PACKAGE_NAME
    author_walk_path = (
        mods_dir / AUTHOR_MOD_DIRECTORY_NAME / AUTHOR_WALK_RELATIVE_PATH
    )
    output_path = mods_dir / OUTPUT_FILE_NAME
    result = build_mod(game_dir, baseline_path, author_walk_path, output_path)
    print(f"已生成：{result}")
    print("平地样本：作者 PHW 0, 5")
    print("坡度样本：原生 PHM 1, 2, 3, 4, 6, 7, 8, 9")
    print(f"新增男性坡度 PAA/metabin：{EXPECTED_NEW_ALIAS_COUNT}")
    print(f"v3.1 SHA-256：{hashlib.sha256(result.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
