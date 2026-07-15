"""生成“v0.2 完整女性非战斗链 + v1.5 纯女性持续走路”测试包。

v2.1/v2.2 证明单独替换六条起步 PAA 或修改其 PHM/PHW 引用路径都不能恢复女性起步。
重新拆解 v0.2 后确认，它通过 16 个 PHM locomotion 混合树、40 个移动 PAA 和 1 个
待机 PAA 共同形成女性站姿与起步。为避免再次丢失这条已生效链，本工具完整嵌入 v0.2
的 57 个目标，只把其中 mixed ``phm_locomotion/basic_move_walk`` 的载荷替换为同包内
纯女性 ``basic_move_lv1``。交接前后的 walk 图内容因此一致，不修改 PAAC 状态机。
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
from cdmm.tools.build_female_walk_single_branch_mod import (
    _file_document,
    _validate_walk_blends,
)

# 已实机确认具备女性站姿和起步的自包含 v0.2 基线包。
V02_PACKAGE_NAME = "Female Outside Combat - Male Combat v0.2-test.cdmod"
V02_PACKAGE_SHA256 = "98837989fa60124767192e66cac01bbf96c8cb20d21437f94a2175aa6b1f285b"
EXPECTED_V02_FILE_COUNT = 57

# v0.2 的 mixed 持续 walk 与纯女性 lv1 目标都位于 PHM 路径，保持原生男性 PAAC 路由。
PHM_WALK_TARGET = (
    "character/binary/motionblending/phm_locomotion/"
    "basic_move_walk.motionblending"
)
PHM_LV1_TARGET = (
    "character/binary/motionblending/phm_locomotion/"
    "basic_move_lv1.motionblending"
)

MOD_NAME = "Female Outside Combat Smooth Walk - Male Combat"
MOD_VERSION = "2.3-test"
OUTPUT_FILE_NAME = f"{MOD_NAME} v{MOD_VERSION}.cdmod"


def _load_v02_content(baseline_path: Path) -> tuple[tuple[str, str, bytes], ...]:
    """读取并严格验证 v0.2 的有序完整资源基线。"""
    actual_sha256 = hashlib.sha256(baseline_path.read_bytes()).hexdigest()
    if actual_sha256 != V02_PACKAGE_SHA256:
        raise ValueError(
            "v0.2 基线包 SHA-256 不匹配："
            f"预期 {V02_PACKAGE_SHA256}，实际 {actual_sha256}"
        )
    package = load_cdmod_package(baseline_path)
    files = [file for patch in package.file_patches for file in patch.files]
    if package.legacy_json_patches or len(files) != EXPECTED_V02_FILE_COUNT:
        raise ValueError("v0.2 基线包组件数量或类型异常")
    if any(file.allow_new or file.allow_table_replace for file in files):
        raise ValueError("v0.2 基线包意外包含新增资源或表覆盖")
    contents = tuple((file.target, file.pamt_dir, file.content) for file in files)
    targets = [target for target, _pamt_dir, _content in contents]
    if len(targets) != len(set(targets)):
        raise ValueError("v0.2 基线包包含重复目标")
    if targets.count(PHM_WALK_TARGET) != 1 or targets.count(PHM_LV1_TARGET) != 1:
        raise ValueError("v0.2 缺少唯一的 PHM walk/lv1 目标")
    return contents


def build_mod(baseline_path: Path, output_path: Path) -> Path:
    """完整保留 v0.2，只让持续 walk 复用同包纯女性 lv1。"""
    baseline = _load_v02_content(baseline_path)
    baseline_content = {target: content for target, _pamt_dir, content in baseline}
    mixed_walk = baseline_content[PHM_WALK_TARGET]
    female_walk = baseline_content[PHM_LV1_TARGET]
    _validate_walk_blends(mixed_walk, female_walk)

    replacements: list[dict[str, object]] = []
    documents: dict[str, dict[str, object] | bytes] = {}
    expected_content: dict[str, bytes] = {}
    for index, (target, pamt_dir, original_content) in enumerate(baseline):
        content = female_walk if target == PHM_WALK_TARGET else original_content
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

    changed_targets = [
        target
        for target, content in expected_content.items()
        if baseline_content[target] != content
    ]
    if changed_targets != [PHM_WALK_TARGET]:
        raise ValueError(f"v2.3 相对 v0.2 的目标差异异常：{changed_targets}")

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
            "Embeds the complete v0.2 female idle/start/locomotion baseline and "
            "changes only its mixed PHM basic_move_walk payload to the same "
            "female-only level-1 walk already used before the fourth-step handoff."
        ),
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": "cdmm.female-outside-combat-smooth-walk-male-combat",
        "name": MOD_NAME,
        "source": {
            "baseline_package": V02_PACKAGE_NAME,
            "baseline_sha256": V02_PACKAGE_SHA256,
            "format": "v0.2-full-baseline-plus-v1.5-single-walk-branch",
            "walk_source": PHM_LV1_TARGET,
            "walk_target": PHM_WALK_TARGET,
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
    if package.legacy_json_patches or len(files) != EXPECTED_V02_FILE_COUNT:
        raise ValueError("生成后的 v2.3 cdmod 组件数量异常")
    if parsed_content != expected_content:
        raise ValueError("生成后的 v2.3 资源载荷与预期不一致")
    if parsed_content[PHM_WALK_TARGET] != parsed_content[PHM_LV1_TARGET]:
        raise ValueError("生成后的 v2.3 walk/lv1 载荷未保持一致")
    return output_path


def main() -> int:
    """生成游戏 mods 目录下的新测试包。"""
    game_dir = Path(r"G:\SteamLibrary\steamapps\common\Crimson Desert")
    baseline_path = game_dir / "mods" / V02_PACKAGE_NAME
    output_path = game_dir / "mods" / OUTPUT_FILE_NAME
    result = build_mod(baseline_path, output_path)
    print(f"已生成：{result}")
    print(f"SHA-256：{hashlib.sha256(result.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
