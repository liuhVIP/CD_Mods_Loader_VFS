"""生成“v1.5 纯女性持续走路 + PHW 路径起步别名”测试包。

v2.1 已确认 6 组女性 PAA/metabin 逐字节覆盖到 PHM 目标，但实机起步仍表现为男性，
说明起步节点不仅依赖资源载荷，还受 PAAC 引用所属的 PHM/PHW 路径族影响。本工具完整
保留 v1.5，只把 6 条 PHM 起步引用等长改到 PHW 目录下的新别名，并让这些别名承载
一一对应的原版 PHW 起步资源。它不修改 transition、sequence 或 condition bytecode。
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
from cdmm.tools.build_female_locomotion_male_combat_graph_mod import (
    TARGET_PAAC,
    _find_entry,
    _patch_basic_lower,
)
from cdmm.tools.build_female_walk_matched_start_mod import (
    METABIN_PAMT_DIR,
    PAA_PAMT_DIR,
    START_MOTION_PAIRS,
    _metabin_target,
    _paa_target,
    _validate_start_pairs,
)
from cdmm.tools.build_female_walk_single_branch_mod import (
    PAAC_PAMT_DIR,
    WALK_BLEND_PAMT_DIR,
    WALK_BLEND_SOURCE,
    WALK_BLEND_TARGET,
    _file_document,
    _validate_walk_blends,
)

# PHM/PHW 动作路径前缀长度相同，可在不改变 PAAC 布局的前提下等长切换。
MALE_REFERENCE_PREFIX = "1_pc/1_phm/"
FEMALE_ALIAS_PREFIX = "1_pc/2_phw/"

# 六条路径各修改目录编号和性别末字母两个字节。
EXPECTED_START_REFERENCE_CHANGED_BYTES = len(START_MOTION_PAIRS) * 2

MOD_NAME = "Female Walk PHW Start Alias - Native Male Combat"
MOD_VERSION = "2.2-test"
OUTPUT_FILE_NAME = f"{MOD_NAME} v{MOD_VERSION}.cdmod"


def _female_alias_reference(male_reference: str) -> str:
    """把 PHM 起步引用映射为同长度的 PHW 目录别名。"""
    if not male_reference.startswith(MALE_REFERENCE_PREFIX):
        raise ValueError(f"起步引用不是预期 PHM 路径：{male_reference}")
    alias = male_reference.replace(MALE_REFERENCE_PREFIX, FEMALE_ALIAS_PREFIX, 1)
    if len(alias) != len(male_reference):
        raise ValueError("PHW 起步别名长度与 PHM 引用不一致")
    return alias


def _patch_start_references(data: bytes) -> bytes:
    """把六条 PHM 起步字符串等长切到 PHW 目录别名。"""
    patched = data
    for pair in START_MOTION_PAIRS:
        male = pair.male_reference.encode("ascii")
        alias = _female_alias_reference(pair.male_reference).encode("ascii")
        if patched.count(male) != 1 or patched.count(alias) != 0:
            raise ValueError(f"起步路径引用数量异常：{pair.male_reference}")
        patched = patched.replace(male, alias, 1)
    if len(patched) != len(data):
        raise ValueError("起步路径别名修改后 PAAC 长度发生变化")
    changed_offsets = [
        index
        for index, (old_byte, new_byte) in enumerate(zip(data, patched))
        if old_byte != new_byte
    ]
    if len(changed_offsets) != EXPECTED_START_REFERENCE_CHANGED_BYTES:
        raise ValueError(
            "起步路径别名字节差异异常："
            f"预期 {EXPECTED_START_REFERENCE_CHANGED_BYTES}，实际 {len(changed_offsets)}"
        )
    return patched


def _new_alias_document(
    *,
    target: str,
    pamt_dir: str,
    payload_path: str,
    content: bytes,
) -> dict[str, object]:
    """构造允许在明确 PAMT 目录新增的 PHW 起步别名声明。"""
    document = _file_document(
        target=target,
        pamt_dir=pamt_dir,
        payload_path=payload_path,
        content=content,
    )
    document["allow_new"] = True
    return document


def build_mod(game_dir: Path, output_path: Path) -> Path:
    """从当前原版资源构建 v1.5 + 六组 PHW 路径起步别名。"""
    basic_lower_entry = _find_entry(game_dir, PAAC_PAMT_DIR, TARGET_PAAC)
    mixed_walk_entry = _find_entry(
        game_dir,
        WALK_BLEND_PAMT_DIR,
        WALK_BLEND_TARGET,
    )
    female_walk_entry = _find_entry(
        game_dir,
        WALK_BLEND_PAMT_DIR,
        WALK_BLEND_SOURCE,
    )
    vanilla_paac, _ = extract_plaintext(basic_lower_entry)
    mixed_walk, _ = extract_plaintext(mixed_walk_entry)
    female_walk, _ = extract_plaintext(female_walk_entry)
    _validate_start_pairs(vanilla_paac)
    _validate_walk_blends(mixed_walk, female_walk)
    patched_paac = _patch_start_references(_patch_basic_lower(vanilla_paac))

    documents: dict[str, dict[str, object] | bytes] = {}
    replacements: list[dict[str, object]] = []
    expected_content: dict[str, bytes] = {
        TARGET_PAAC: patched_paac,
        WALK_BLEND_TARGET: female_walk,
    }
    paac_payload = "assets/000/basic_lower.paac"
    walk_payload = "assets/001/basic_move_walk.motionblending"
    replacements.extend(
        (
            _file_document(
                target=TARGET_PAAC,
                pamt_dir=PAAC_PAMT_DIR,
                payload_path=paac_payload,
                content=patched_paac,
            ),
            _file_document(
                target=WALK_BLEND_TARGET,
                pamt_dir=WALK_BLEND_PAMT_DIR,
                payload_path=walk_payload,
                content=female_walk,
            ),
        )
    )
    documents[paac_payload] = patched_paac
    documents[walk_payload] = female_walk

    for pair_index, pair in enumerate(START_MOTION_PAIRS, start=2):
        alias_reference = _female_alias_reference(pair.male_reference)
        for kind, pamt_dir, target_builder in (
            ("paa", PAA_PAMT_DIR, _paa_target),
            ("metabin", METABIN_PAMT_DIR, _metabin_target),
        ):
            target = target_builder(alias_reference)
            source = target_builder(pair.female_reference)
            source_entry = _find_entry(game_dir, pamt_dir, source)
            content, _ = extract_plaintext(source_entry)
            payload_path = f"assets/{pair_index:03d}/{kind}_{Path(target).name}"
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
            "Keeps the v1.5 pure-PHW continuous walk baseline and retargets "
            "only six normal-walk start references from PHM paths to new "
            "same-length PHW aliases carrying their native PHW PAA/metabin pairs."
        ),
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": "cdmm.female-walk-phw-start-alias-native-male-combat",
        "name": MOD_NAME,
        "source": {
            "format": "v1.5-plus-six-native-phw-start-path-aliases",
            "paac_target": TARGET_PAAC,
            "start_alias_count": len(START_MOTION_PAIRS),
            "walk_blend_source": WALK_BLEND_SOURCE,
            "walk_blend_target": WALK_BLEND_TARGET,
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
    if package.legacy_json_patches or len(files) != len(replacements):
        raise ValueError("生成后的 v2.2 cdmod 组件数量异常")
    if parsed_content != expected_content:
        raise ValueError("生成后的 v2.2 资源载荷与预期不一致")
    if sum(file.allow_new for file in files) != len(START_MOTION_PAIRS) * 2:
        raise ValueError("生成后的 v2.2 PHW 新别名声明数量异常")
    return output_path


def main() -> int:
    """生成游戏 mods 目录下的新测试包。"""
    game_dir = Path(r"G:\SteamLibrary\steamapps\common\Crimson Desert")
    output_path = game_dir / "mods" / OUTPUT_FILE_NAME
    result = build_mod(game_dir, output_path)
    print(f"已生成：{result}")
    print(f"SHA-256：{hashlib.sha256(result.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
