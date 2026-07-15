"""生成“v2.3 完整稳定基线 + 同图 phase sync”测试包。

v2.3 已实机确认除固定第四步抖动外全部正常，而且 ``basic_move_lv1`` 与
``basic_move_walk`` 载荷逐字节相同。这证明剩余抖动不是动画资源差异，而是两个状态交接
时相位/root-motion 被重新初始化。本工具完整继承 v2.3，仅借用原版三样本 loop+sync
motionblending 框架，并把其三条动作引用改到独立 CDMM 别名；别名载荷仍是 v2.3 当前
三条 basic_00_00 女性走路 PAA。lv1 与 walk 两个目标使用完全相同的 sync 图。
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
from cdmm.tools.build_female_outside_combat_smooth_walk_mod import (
    PHM_LV1_TARGET,
    PHM_WALK_TARGET,
)
from cdmm.tools.build_female_walk_matched_start_mod import (
    METABIN_PAMT_DIR,
    PAA_PAMT_DIR,
    _metabin_target,
    _paa_target,
)
from cdmm.tools.build_female_walk_single_branch_mod import _file_document
from cdmm.tools.build_female_walk_sync_probe_mod import (
    LOOP_DECLARATION,
    SYNC_DECLARATION,
)

# 用户确认除第四步抖动外全部完美的稳定基线包。
V23_PACKAGE_NAME = "Female Outside Combat Smooth Walk - Male Combat v2.3-test.cdmod"
V23_PACKAGE_SHA256 = "f8f40df58068ea9a5a643193f2282299c75a14d9e02508a76f25a0f556478a61"
EXPECTED_V23_FILE_COUNT = 57

# 与 basic_move_lv1 结构最接近的原版三样本 loop+sync 框架。
SYNC_TEMPLATE_TARGET = (
    "character/binary/motionblending/phw_locomotion/"
    "fist_move_lv2.motionblending"
)
SYNC_TEMPLATE_REFERENCES = (
    "1_pc/2_phw/cd_phw_basic_00_01_nor_move_walkfast_f_ing_00.paa",
    "1_pc/2_phw/cd_phw_basic_00_01_nor_move_walkfast_f_180turn_l_00.paa",
    "1_pc/2_phw/cd_phw_basic_00_01_nor_move_walkfast_f_180turn_r_00.paa",
)

# v2.3 当前纯女性 lv1 的三条动作，顺序与 sync 框架的前进/左转/右转一一对应。
BASIC_WALK_REFERENCES = (
    "1_pc/2_phw/cd_phw_basic_00_00_nor_move_walk_f_ing_00.paa",
    "1_pc/2_phw/cd_phw_basic_00_00_nor_move_walk_f_180turn_l_00.paa",
    "1_pc/2_phw/cd_phw_basic_00_00_nor_move_walk_f_180turn_r_00.paa",
)

# 用同长度唯一前缀隔离 alias，禁止覆盖原版拳姿 PAA。
SYNC_ALIAS_OLD_TOKEN = "cd_phw_basic_00_01_"
SYNC_ALIAS_NEW_TOKEN = "cd_phw_cdmmx_00_00_"

MOD_NAME = "Female Outside Combat Phase Sync - Male Combat"
MOD_VERSION = "2.4-test"
OUTPUT_FILE_NAME = f"{MOD_NAME} v{MOD_VERSION}.cdmod"


def _sync_alias_reference(reference: str) -> str:
    """把原版 sync 框架动作映射到同长度 CDMM 独立别名。"""
    if reference.count(SYNC_ALIAS_OLD_TOKEN) != 1:
        raise ValueError(f"sync 模板引用前缀异常：{reference}")
    alias = reference.replace(SYNC_ALIAS_OLD_TOKEN, SYNC_ALIAS_NEW_TOKEN, 1)
    if len(alias) != len(reference):
        raise ValueError("sync 动作别名长度不一致")
    return alias


SYNC_ALIAS_REFERENCES = tuple(
    _sync_alias_reference(reference) for reference in SYNC_TEMPLATE_REFERENCES
)


def _load_v23_content(baseline_path: Path) -> tuple[tuple[str, str, bytes], ...]:
    """读取并严格验证 v2.3 的有序稳定基线。"""
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
    contents = tuple((file.target, file.pamt_dir, file.content) for file in files)
    targets = [target for target, _pamt_dir, _content in contents]
    if len(targets) != len(set(targets)):
        raise ValueError("v2.3 基线包包含重复目标")
    if targets.count(PHM_WALK_TARGET) != 1 or targets.count(PHM_LV1_TARGET) != 1:
        raise ValueError("v2.3 缺少唯一的 PHM walk/lv1 目标")
    if any(file.allow_new or file.allow_table_replace for file in files):
        raise ValueError("v2.3 基线包意外包含新增资源或表覆盖")
    return contents


def _patch_sync_template(template: bytes) -> bytes:
    """把三条拳姿 sync 引用等长改到隔离 CDMM 别名。"""
    if template.count(LOOP_DECLARATION) != 1 or template.count(SYNC_DECLARATION) != 1:
        raise ValueError("sync 模板缺少唯一 loop/sync 声明")
    patched = template
    for source, alias in zip(SYNC_TEMPLATE_REFERENCES, SYNC_ALIAS_REFERENCES):
        source_bytes = source.encode("ascii")
        alias_bytes = alias.encode("ascii")
        if patched.count(source_bytes) != 1 or patched.count(alias_bytes) != 0:
            raise ValueError(f"sync 模板动作引用数量异常：{source}")
        patched = patched.replace(source_bytes, alias_bytes, 1)
    if len(patched) != len(template):
        raise ValueError("sync 模板别名修改后长度发生变化")
    if any(reference.encode("ascii") in patched for reference in SYNC_TEMPLATE_REFERENCES):
        raise ValueError("sync 模板仍残留原版拳姿动作引用")
    return patched


def _new_alias_document(
    *,
    target: str,
    pamt_dir: str,
    payload_path: str,
    content: bytes,
) -> dict[str, object]:
    """构造允许在明确 PAMT 目录新增的 sync 动作别名。"""
    document = _file_document(
        target=target,
        pamt_dir=pamt_dir,
        payload_path=payload_path,
        content=content,
    )
    document["allow_new"] = True
    return document


def build_mod(game_dir: Path, baseline_path: Path, output_path: Path) -> Path:
    """完整继承 v2.3，仅让 lv1/walk 共用隔离女性 sync 图。"""
    baseline = _load_v23_content(baseline_path)
    baseline_content = {target: content for target, _pamt_dir, content in baseline}
    if baseline_content[PHM_WALK_TARGET] != baseline_content[PHM_LV1_TARGET]:
        raise ValueError("v2.3 基线的 walk/lv1 载荷不再相同")

    template_entry = _find_entry(game_dir, PAA_PAMT_DIR, SYNC_TEMPLATE_TARGET)
    template, _ = extract_plaintext(template_entry)
    sync_walk = _patch_sync_template(template)

    replacements: list[dict[str, object]] = []
    documents: dict[str, dict[str, object] | bytes] = {}
    expected_content: dict[str, bytes] = {}
    for index, (target, pamt_dir, original_content) in enumerate(baseline):
        content = sync_walk if target in (PHM_LV1_TARGET, PHM_WALK_TARGET) else original_content
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

    for alias_index, (alias_reference, source_reference) in enumerate(
        zip(SYNC_ALIAS_REFERENCES, BASIC_WALK_REFERENCES),
        start=len(replacements),
    ):
        for kind, pamt_dir, target_builder in (
            ("paa", PAA_PAMT_DIR, _paa_target),
            ("metabin", METABIN_PAMT_DIR, _metabin_target),
        ):
            target = target_builder(alias_reference)
            source = target_builder(source_reference)
            source_entry = _find_entry(game_dir, pamt_dir, source)
            content, _ = extract_plaintext(source_entry)
            payload_path = f"assets/{alias_index:03d}/{kind}_{Path(target).name}"
            replacements.append(
                _new_alias_document(
                    target=target,
                    pamt_dir=pamt_dir,
                    payload_path=payload_path,
                    content=content,
                )
            )
            documents[payload_path] = content
            expected_content[target] = content

    changed_targets = [
        target
        for target, content in baseline_content.items()
        if expected_content[target] != content
    ]
    if changed_targets != [PHM_LV1_TARGET, PHM_WALK_TARGET]:
        raise ValueError(f"v2.4 相对 v2.3 的现有目标差异异常：{changed_targets}")

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
            "Preserves the complete verified v2.3 baseline. Only PHM level-1 "
            "and walk states receive one identical native three-sample loop+sync "
            "graph whose isolated aliases carry the existing basic female walk clips."
        ),
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": "cdmm.female-outside-combat-phase-sync-male-combat",
        "name": MOD_NAME,
        "source": {
            "baseline_package": V23_PACKAGE_NAME,
            "baseline_sha256": V23_PACKAGE_SHA256,
            "format": "v2.3-plus-isolated-three-sample-native-sync-graph",
            "sync_template": SYNC_TEMPLATE_TARGET,
            "sync_targets": [PHM_LV1_TARGET, PHM_WALK_TARGET],
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
    expected_count = EXPECTED_V23_FILE_COUNT + len(SYNC_ALIAS_REFERENCES) * 2
    if package.legacy_json_patches or len(files) != expected_count:
        raise ValueError("生成后的 v2.4 cdmod 组件数量异常")
    if parsed_content != expected_content:
        raise ValueError("生成后的 v2.4 资源载荷与预期不一致")
    if parsed_content[PHM_WALK_TARGET] != parsed_content[PHM_LV1_TARGET]:
        raise ValueError("生成后的 v2.4 walk/lv1 sync 载荷未保持一致")
    if sum(file.allow_new for file in files) != len(SYNC_ALIAS_REFERENCES) * 2:
        raise ValueError("生成后的 v2.4 sync 新别名声明数量异常")
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
