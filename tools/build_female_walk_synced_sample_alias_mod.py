"""生成“原版同步走路树 + 隔离女性方向样本”测试包。

v1.6 已证伪左右 threestep，v1.7 已确认第四步进入持续 walk 槽，v1.8 又证明
PAAC 中重复的 7..13 实际是 inline transition sequence，不能当作 motion lane 修改。
本工具保留 v1.5 的男性战斗底座，但恢复原版 PHW basic_move_walk 的 loop+sync 结构，
只把其中 13 个 PHM 样本引用等长改到隔离的 PHW 别名路径。别名资源内容来自原版
fist_stride 使用的 basic_01_01 女性站定与八方向走路 PAA，并同步携带 PAA metabin。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
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
from cdmm.tools.build_female_walk_single_branch_mod import (
    PAAC_PAMT_DIR,
    WALK_BLEND_PAMT_DIR,
    WALK_BLEND_TARGET,
    _file_document,
)
from cdmm.tools.build_female_walk_sync_probe_mod import (
    LOOP_DECLARATION,
    SYNC_DECLARATION,
)

# 原版 PAA 与动画 metadata 所在 PAMT 目录。
PAA_PAMT_DIR = "0009"
METABIN_PAMT_DIR = "0010"

# 男性样本统一改到新的 PHW 命名空间，保持每条引用长度完全不变。
MALE_REFERENCE_PREFIX = "1_pc/1_phm/cd_phm"
FEMALE_ALIAS_PREFIX = "1_pc/2_phw/cd_phw"

# 原版 fist_stride 已验证使用的女性基础方向样本前缀。
FEMALE_DIRECTION_PREFIX = "1_pc/2_phw/cd_phw_basic_01_01_nor_move_walk"

MOD_NAME = "Female Walk Synced Sample Alias - Native Male Combat"
MOD_VERSION = "1.9-test"
OUTPUT_FILE_NAME = f"{MOD_NAME} v{MOD_VERSION}.cdmod"


@dataclass(frozen=True)
class WalkSampleAlias:
    """一条男性样本、隔离 PHW 目标与原版女性来源的映射。"""

    male_reference: str
    female_alias_reference: str
    female_source_reference: str


def _sample_alias(male_stem: str, female_direction: str) -> WalkSampleAlias:
    """构造同长度 PHM -> PHW 别名和对应女性方向来源。"""
    male_reference = f"{MALE_REFERENCE_PREFIX}_{male_stem}.paa"
    female_alias_reference = male_reference.replace(
        MALE_REFERENCE_PREFIX,
        FEMALE_ALIAS_PREFIX,
        1,
    )
    female_source_reference = (
        f"{FEMALE_DIRECTION_PREFIX}_{female_direction}_ing_00.paa"
    )
    if len(male_reference) != len(female_alias_reference):
        raise ValueError("PHM -> PHW 样本别名长度不一致")
    return WalkSampleAlias(
        male_reference,
        female_alias_reference,
        female_source_reference,
    )


# 13 个混合 walk 男性槽逐一映射到站定或对应女性移动方向。
WALK_SAMPLE_ALIASES = (
    _sample_alias("basic_00_00_dialogue_move_walk_s_ing_00", "s"),
    _sample_alias("basic_00_00_dialogue_move_walk_f_ing_00", "f"),
    _sample_alias("basic_00_00_dialogue_move_walk_fl_ing_01", "fl"),
    _sample_alias("basic_00_00_dialogue_move_walk_fr_ing_01", "fr"),
    _sample_alias("basic_00_00_dialogue_move_walk_bl_ing_01", "bl"),
    _sample_alias("basic_00_00_dialogue_move_walk_br_ing_01", "br"),
    _sample_alias("basic_00_00_nor_move_walk_ing_l_00", "l"),
    _sample_alias("basic_00_00_nor_move_walk_ing_r_00", "r"),
    _sample_alias("basic_00_00_nor_move_walk_ing_fr_00", "fr"),
    _sample_alias("basic_00_00_nor_move_walk_ing_fl_00", "fl"),
    _sample_alias("basic_00_00_nor_move_walk_ing_bl_00", "bl"),
    _sample_alias("basic_00_00_nor_move_walk_ing_br_00", "br"),
    _sample_alias("basic_00_00_nor_move_walk_b_stride_00", "b"),
)


def _paa_target(reference: str) -> str:
    """把 motionblending 引用转换为 PAA 游戏路径。"""
    return f"character/motion/{reference}"


def _metabin_target(reference: str) -> str:
    """把 motionblending 引用转换为 PAA metadata 游戏路径。"""
    return f"actionchart/bin__/animmeta/{reference}_metabin"


def _patch_synced_walk_samples(data: bytes) -> bytes:
    """保留完整同步树，只等长切换 13 个样本引用命名空间。"""
    for declaration in (LOOP_DECLARATION, SYNC_DECLARATION):
        if data.count(declaration) != 1:
            raise ValueError(f"原版 walk 树同步声明异常：{declaration!r}")

    patched = data
    for sample in WALK_SAMPLE_ALIASES:
        old = sample.male_reference.encode("ascii")
        new = sample.female_alias_reference.encode("ascii")
        if patched.count(old) != 1 or patched.count(new) != 0:
            raise ValueError(f"walk 样本引用数量异常：{sample.male_reference}")
        patched = patched.replace(old, new, 1)

    if len(patched) != len(data):
        raise ValueError("同步 walk 样本修改后文件长度发生变化")
    if MALE_REFERENCE_PREFIX.encode("ascii") in patched:
        raise ValueError("同步 walk 树仍残留 PHM 样本引用")
    return patched


def _new_file_document(
    *,
    target: str,
    pamt_dir: str,
    payload_path: str,
    content: bytes,
) -> dict[str, object]:
    """构造保持声明目录的新资源载荷。"""
    document = _file_document(
        target=target,
        pamt_dir=pamt_dir,
        payload_path=payload_path,
        content=content,
    )
    document["allow_new"] = True
    return document


def build_mod(game_dir: Path, output_path: Path) -> Path:
    """从当前原版 PAAC、同步 walk 树和女性方向资源构建 v1.9。"""
    basic_lower_entry = _find_entry(game_dir, PAAC_PAMT_DIR, TARGET_PAAC)
    mixed_walk_entry = _find_entry(
        game_dir,
        WALK_BLEND_PAMT_DIR,
        WALK_BLEND_TARGET,
    )
    vanilla_paac, _ = extract_plaintext(basic_lower_entry)
    mixed_walk, _ = extract_plaintext(mixed_walk_entry)
    patched_paac = _patch_basic_lower(vanilla_paac)
    patched_walk = _patch_synced_walk_samples(mixed_walk)

    documents: dict[str, dict[str, object] | bytes] = {}
    replacements: list[dict[str, object]] = []

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
                content=patched_walk,
            ),
        )
    )
    documents[paac_payload] = patched_paac
    documents[walk_payload] = patched_walk

    source_cache: dict[tuple[str, str], bytes] = {}
    for sample_index, sample in enumerate(WALK_SAMPLE_ALIASES, start=2):
        for kind, pamt_dir, target_builder in (
            ("paa", PAA_PAMT_DIR, _paa_target),
            ("metabin", METABIN_PAMT_DIR, _metabin_target),
        ):
            source_target = target_builder(sample.female_source_reference)
            source_key = (pamt_dir, source_target)
            if source_key not in source_cache:
                source_entry = _find_entry(game_dir, pamt_dir, source_target)
                source_cache[source_key], _ = extract_plaintext(source_entry)
            content = source_cache[source_key]
            alias_target = target_builder(sample.female_alias_reference)
            payload_path = f"assets/{sample_index:03d}/{Path(alias_target).name}"
            replacements.append(
                _new_file_document(
                    target=alias_target,
                    pamt_dir=pamt_dir,
                    payload_path=payload_path,
                    content=content,
                )
            )
            documents[payload_path] = content

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
            "Keeps the native male combat graph and the original PHW walk loop+sync "
            "structure. Its 13 PHM samples are redirected to isolated PHW aliases "
            "backed by native female standing and eight-direction walk assets."
        ),
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": "cdmm.female-walk-synced-sample-alias-native-male-combat",
        "name": MOD_NAME,
        "source": {
            "alias_count": len(WALK_SAMPLE_ALIASES),
            "format": "same-length-motion-reference-plus-isolated-native-resource-aliases",
            "paac_target": TARGET_PAAC,
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
        raise ValueError("生成后的 v1.9 cdmod 组件数量异常")
    if parsed_content.get(TARGET_PAAC) != patched_paac:
        raise ValueError("生成后的 v1.9 PAAC 载荷与预期不一致")
    if parsed_content.get(WALK_BLEND_TARGET) != patched_walk:
        raise ValueError("生成后的 v1.9 同步 walk 载荷与预期不一致")
    if not all(file.allow_new for file in files[2:]):
        raise ValueError("生成后的 v1.9 PHW 别名资源未声明 allow_new")
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
