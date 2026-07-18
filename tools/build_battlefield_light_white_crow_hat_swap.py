"""生成“战场之光”女性头饰替换为女巫帽子的结构适配型变体。

已实机证伪的两条路线分别是：完整复制 ``0164.prefab`` 覆盖 ``0151`` 会在
存档加载后崩溃；注册第二份同路径条件表会在数据加载 ``2/12`` 阶段把同一表
重复解析。当前方案以原版目标 Prefab 为基底，仅等长替换内部唯一主 PAC 路径，
保留组件布局、UID、骨骼 socket 和文件长度。所有变体覆盖同一目标，只能单选。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from cdmm.archive.pamt import parse_pamt_filtered
from cdmm.services.cdmod_converter import (
    CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE,
    CDMOD_FORMAT_NAME,
    CDMOD_FORMAT_VERSION,
    CDMOD_MANIFEST_PATH,
    CDMOD_REPORT_PATH,
    _write_cdmod_zip,
)
from cdmm.services.cdmod_package import load_cdmod_package
from cdmm.services.json_loader import extract_plaintext

# 头饰 Prefab 所在的原版 PAMT 目录。
HAT_PAMT_DIR = "0009"

# “战场之光”女性头饰是全部互斥变体共同覆盖的目标。
BATTLEFIELD_LIGHT_HAT_PATH = "character/cd_phw_00_hel_00_0151.prefab"
BATTLEFIELD_LIGHT_MAIN_PAC = (
    b"character/model/1_pc/2_phw/armor/13_hel/cd_phw_00_hel_00_0151.pac"
)

# 当前 1.13.01 原版目标字节的安全锚点，游戏更新后拒绝盲目生成。
EXPECTED_TARGET_SHA256 = "bc4479f5de7905660d273b4af46223d2a364fe6290faac36fe5ed9c895277f14"

# file-replacement 组件在 cdmod 内的固定路径。
FILE_REPLACEMENT_PATH = "files/replacements.json"
PREFAB_PAYLOAD_PATH = "assets/00000/cd_phw_00_hel_00_0151.prefab"


@dataclass(frozen=True)
class WitchHatVariant:
    """一位女巫的原生帽子资源与测试包元数据。"""

    key: str
    display_name: str
    source_prefab_path: str
    source_main_pac: bytes
    expected_source_sha256: str
    expected_source_model_reference_count: int
    appearance_entry_path: str
    package_id: str
    package_name: str
    package_version: str
    output_filename: str


# Head 与 Hair 组合已从原版 app_xml 唯一反查到以下帽子 Prefab。
HAT_VARIANTS = (
    WitchHatVariant(
        key="areciel",
        display_name="Areciel",
        source_prefab_path="character/cd_phw_00_hel_0177.prefab",
        source_main_pac=(
            b"character/model/1_pc/2_phw/armor/13_hel/cd_phw_00_hel_00_0177.pac"
        ),
        expected_source_sha256=(
            "200d390c68fd04a1ba90e7e17082893fdfead8ac8cadb06dc08316c75da33527"
        ),
        expected_source_model_reference_count=2,
        appearance_entry_path="character/cd_nhw_south_20165.app_xml",
        package_id="battlefield-light-areciel-hat-structural",
        package_name="Light of the Battlefield - Areciel Main Hat Structural Swap",
        package_version="0.1-test",
        output_filename=(
            "ZZZ - Light of the Battlefield Areciel Hat Structural-0.1-test.cdmod"
        ),
    ),
    WitchHatVariant(
        key="bari",
        display_name="Bari",
        source_prefab_path="character/cd_phw_00_hel_00_0187.prefab",
        source_main_pac=(
            b"character/model/1_pc/2_phw/armor/13_hel/cd_phw_00_hel_00_0187.pac"
        ),
        expected_source_sha256=(
            "38f18dfa83d9ce1654a9d1d0f45b3e1b123d7342c5626a6463bcb9c0b9ac71e6"
        ),
        expected_source_model_reference_count=1,
        appearance_entry_path="character/cd_nhw_south_20167.app_xml",
        package_id="battlefield-light-bari-hat-structural",
        package_name="Light of the Battlefield - Bari Hat Structural Swap",
        package_version="0.1-test",
        output_filename="ZZZ - Light of the Battlefield Bari Hat Structural-0.1-test.cdmod",
    ),
    WitchHatVariant(
        key="elowen",
        display_name="Elowen",
        source_prefab_path="character/cd_phw_00_hel_00_0186.prefab",
        source_main_pac=(
            b"character/model/1_pc/2_phw/armor/13_hel/cd_phw_00_hel_00_0186.pac"
        ),
        expected_source_sha256=(
            "c05212247c3c7ec4b90028fe8f2925f7ed8897d957ff1c8fbb2f7e298aef573e"
        ),
        expected_source_model_reference_count=2,
        appearance_entry_path="character/cd_nhw_south_20168.app_xml",
        package_id="battlefield-light-elowen-hat-structural",
        package_name="Light of the Battlefield - Elowen Main Hat Structural Swap",
        package_version="0.1-test",
        output_filename=(
            "ZZZ - Light of the Battlefield Elowen Hat Structural-0.1-test.cdmod"
        ),
    ),
    WitchHatVariant(
        key="lyselia",
        display_name="Lyselia",
        source_prefab_path="character/cd_phw_00_hel_0185.prefab",
        source_main_pac=(
            b"character/model/1_pc/2_phw/armor/13_hel/cd_phw_00_hel_00_0185.pac"
        ),
        expected_source_sha256=(
            "d637822a39d97451f7de42c2bc059e8241a35c380e3e64e288f39e15cf1653d7"
        ),
        expected_source_model_reference_count=1,
        appearance_entry_path="character/cd_nhw_south_20166.app_xml",
        package_id="battlefield-light-lyselia-hat-structural",
        package_name="Light of the Battlefield - Lyselia Hat Structural Swap",
        package_version="0.1-test",
        output_filename=(
            "ZZZ - Light of the Battlefield Lyselia Hat Structural-0.1-test.cdmod"
        ),
    ),
    WitchHatVariant(
        key="white-crow",
        display_name="White Crow",
        source_prefab_path="character/cd_phw_00_hel_00_0164.prefab",
        source_main_pac=(
            b"character/model/1_pc/2_phw/armor/13_hel/cd_phw_00_hel_00_0164.pac"
        ),
        expected_source_sha256=(
            "8f51daa6f36a246076b3bf3b36fef7c4c200dea14d9b2cb44fa57a2414252dc6"
        ),
        expected_source_model_reference_count=2,
        appearance_entry_path="character/cd_m0001_00_phw_north_44001.app_xml",
        package_id="battlefield-light-white-crow-hat-structural",
        package_name="Light of the Battlefield - White Crow Main Hat Structural Swap",
        package_version="0.5-test",
        output_filename=(
            "ZZZ - Light of the Battlefield White Crow Main Hat Structural-0.5-test.cdmod"
        ),
    ),
)
HAT_VARIANTS_BY_KEY = {variant.key: variant for variant in HAT_VARIANTS}

# 保留旧白鸦生成器的公开常量，避免现有脚本和专项测试因多变体扩展失效。
WHITE_CROW_VARIANT = HAT_VARIANTS_BY_KEY["white-crow"]
WHITE_CROW_HAT_PATH = WHITE_CROW_VARIANT.source_prefab_path
WHITE_CROW_MAIN_PAC = WHITE_CROW_VARIANT.source_main_pac
WHITE_CROW_SUB_PAC = (
    b"character/model/1_pc/2_phw/armor/13_hel/cd_phw_00_hel_00_0164_sub01.pac"
)
EXPECTED_SOURCE_SHA256 = WHITE_CROW_VARIANT.expected_source_sha256
PACKAGE_ID = WHITE_CROW_VARIANT.package_id
PACKAGE_NAME = WHITE_CROW_VARIANT.package_name
PACKAGE_VERSION = WHITE_CROW_VARIANT.package_version


@dataclass(frozen=True)
class PrefabAudit:
    """原版 Prefab 与等长结构补丁的审计结果。"""

    variant_key: str
    target_path: str
    target_size: int
    target_sha256: str
    source_path: str
    source_size: int
    source_sha256: str
    patched_size: int
    patched_sha256: str
    changed_byte_count: int
    target_model_reference_count: int
    source_model_reference_count: int


@dataclass(frozen=True)
class HatSwapBuildResult:
    """结构适配型帽子替换测试包生成结果。"""

    variant_key: str
    output_path: Path
    package_sha256: str
    prefab_audit: PrefabAudit


def get_hat_variant(variant_key: str) -> WitchHatVariant:
    """按稳定 key 获取女巫帽子变体。"""
    try:
        return HAT_VARIANTS_BY_KEY[variant_key]
    except KeyError as exc:
        choices = ", ".join(HAT_VARIANTS_BY_KEY)
        raise ValueError(f"未知女巫帽子变体 {variant_key!r}，可选：{choices}") from exc


def build_structural_hat_prefab(
    target_content: bytes,
    source_main_pac: bytes | None = None,
) -> bytes:
    """只在原目标结构内等长替换唯一主 PAC 路径。"""
    source_main_pac = source_main_pac or get_hat_variant("white-crow").source_main_pac
    if len(BATTLEFIELD_LIGHT_MAIN_PAC) != len(source_main_pac):
        raise ValueError("主帽 PAC 路径长度不一致，不能执行结构内等长替换")
    if target_content.count(BATTLEFIELD_LIGHT_MAIN_PAC) != 1:
        raise ValueError("原版 0151 Prefab 中主 PAC 路径数量异常")
    if source_main_pac in target_content:
        raise ValueError("目标 Prefab 已经引用所选女巫的主帽 PAC")
    patched = target_content.replace(BATTLEFIELD_LIGHT_MAIN_PAC, source_main_pac, 1)
    if len(patched) != len(target_content):
        raise ValueError("结构补丁意外改变 Prefab 长度")
    if patched.count(source_main_pac) != 1:
        raise ValueError("补丁后的女巫主帽 PAC 引用数量异常")
    if BATTLEFIELD_LIGHT_MAIN_PAC in patched:
        raise ValueError("补丁后仍残留战场之光 0151 主 PAC 引用")
    return patched


def _load_original_prefabs(
    game_dir: Path,
    variant: WitchHatVariant,
) -> tuple[bytes, bytes]:
    """读取目标和来源明文，并用固定 SHA 防止跨版本误改。"""
    pamt_path = game_dir.resolve() / HAT_PAMT_DIR / "0.pamt"
    if not pamt_path.is_file():
        raise FileNotFoundError(f"缺少原版头饰 PAMT：{pamt_path}")
    desired_paths = {BATTLEFIELD_LIGHT_HAT_PATH, variant.source_prefab_path}
    entries = parse_pamt_filtered(
        pamt_path,
        paz_dir=pamt_path.parent,
        desired_exact=desired_paths,
    )
    entries_by_path = {entry.path.casefold(): entry for entry in entries}
    missing = sorted(path for path in desired_paths if path.casefold() not in entries_by_path)
    if missing:
        raise ValueError(f"原版 0009 缺少头饰 Prefab：{missing}")
    target, _target_entry = extract_plaintext(
        entries_by_path[BATTLEFIELD_LIGHT_HAT_PATH.casefold()]
    )
    source, _source_entry = extract_plaintext(
        entries_by_path[variant.source_prefab_path.casefold()]
    )
    target_sha256 = hashlib.sha256(target).hexdigest()
    source_sha256 = hashlib.sha256(source).hexdigest()
    if target_sha256 != EXPECTED_TARGET_SHA256:
        raise ValueError(f"原版 0151 Prefab SHA256 已变化：{target_sha256}")
    if source_sha256 != variant.expected_source_sha256:
        raise ValueError(
            f"原版 {variant.display_name} 帽子 Prefab SHA256 已变化：{source_sha256}"
        )
    source_model_references = re.findall(rb"character/model/[^\x00]+?\.pac", source)
    if source.count(variant.source_main_pac) != 1:
        raise ValueError(f"{variant.display_name} 来源 Prefab 主 PAC 引用数量异常")
    if len(source_model_references) != variant.expected_source_model_reference_count:
        raise ValueError(
            f"{variant.display_name} 来源模型引用数量已变化："
            f"{len(source_model_references)}"
        )
    return target, source


def build_hat_swap_mod(
    game_dir: Path,
    output_path: Path,
    variant_key: str = "white-crow",
) -> HatSwapBuildResult:
    """生成零依赖、无条件表、只覆盖 0151 Prefab 的结构补丁包。"""
    game_dir = game_dir.resolve()
    output_path = output_path.resolve()
    variant = get_hat_variant(variant_key)
    target, source = _load_original_prefabs(game_dir, variant)
    patched = build_structural_hat_prefab(target, variant.source_main_pac)
    changed_byte_count = sum(old != new for old, new in zip(target, patched, strict=True))
    target_model_references = re.findall(rb"character/model/[^\x00]+?\.pac", target)
    source_model_references = re.findall(rb"character/model/[^\x00]+?\.pac", source)
    if len(target_model_references) != 1:
        raise ValueError(
            f"目标头饰模型引用数量与已验证结构不符：{len(target_model_references)}"
        )
    audit = PrefabAudit(
        variant_key=variant.key,
        target_path=BATTLEFIELD_LIGHT_HAT_PATH,
        target_size=len(target),
        target_sha256=hashlib.sha256(target).hexdigest(),
        source_path=variant.source_prefab_path,
        source_size=len(source),
        source_sha256=hashlib.sha256(source).hexdigest(),
        patched_size=len(patched),
        patched_sha256=hashlib.sha256(patched).hexdigest(),
        changed_byte_count=changed_byte_count,
        target_model_reference_count=len(target_model_references),
        source_model_reference_count=len(source_model_references),
    )
    replacement_document = {
        "schema": 1,
        "files": [
            {
                "target": BATTLEFIELD_LIGHT_HAT_PATH,
                "pamt_dir": HAT_PAMT_DIR,
                "payload": PREFAB_PAYLOAD_PATH,
                "sha256": audit.patched_sha256,
                "size": len(patched),
                "allow_new": False,
                "allow_table_replace": False,
            }
        ],
    }
    manifest_document = {
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": variant.package_id,
        "name": variant.package_name,
        "version": variant.package_version,
        "author": "cdmm research",
        "description": (
            "Keeps the native 0151 prefab structure and replaces only its "
            f"same-length main PAC path with {variant.display_name}. No conditional "
            "table, no second component, and no runtime mod dependency."
        ),
        "dependencies": [],
        "source": {
            "format": "target-prefab-same-length-pac-path-replacement",
            "game_version": "1.13.01",
        },
        "components": [
            {
                "type": CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE,
                "path": FILE_REPLACEMENT_PATH,
            }
        ],
    }
    omitted_component_count = max(0, len(source_model_references) - 1)
    report_document = {
        "schema": 1,
        "variant": {
            "key": variant.key,
            "display_name": variant.display_name,
            "source_prefab_path": variant.source_prefab_path,
            "source_main_pac": variant.source_main_pac.decode("ascii"),
            "expected_source_sha256": variant.expected_source_sha256,
            "expected_source_model_reference_count": (
                variant.expected_source_model_reference_count
            ),
            "appearance_entry_path": variant.appearance_entry_path,
        },
        "prefab_audit": asdict(audit),
        "mapping": {
            "target_prefab": BATTLEFIELD_LIGHT_HAT_PATH,
            "old_main_pac": BATTLEFIELD_LIGHT_MAIN_PAC.decode("ascii"),
            "new_main_pac": variant.source_main_pac.decode("ascii"),
        },
        "appearance_evidence": {
            "entry_path": variant.appearance_entry_path,
            "source_prefab": variant.source_prefab_path,
        },
        "compatibility": {
            "runtime_dependency": None,
            "uses_conditional_table": False,
            "mutually_exclusive_with_other_hat_variants": True,
            "can_coexist_with_female_armor_module": True,
            "equip_everything_role": "allows male Kliff to equip the female item",
        },
        "preserved": [
            "target-prefab-length",
            "target-component-count",
            "target-scene-object-uid",
            "target-bone-socket",
            "target-component-layout",
        ],
        "known_limit": (
            f"Only the source main PAC is mounted; {omitted_component_count} extra source "
            "component(s) are intentionally omitted by this single-component safety build."
        ),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_cdmod_zip(
        output_path,
        {
            CDMOD_MANIFEST_PATH: manifest_document,
            FILE_REPLACEMENT_PATH: replacement_document,
            PREFAB_PAYLOAD_PATH: patched,
            CDMOD_REPORT_PATH: report_document,
        },
    )
    _verify_generated_package(output_path, patched, variant)
    return HatSwapBuildResult(
        variant_key=variant.key,
        output_path=output_path,
        package_sha256=hashlib.sha256(output_path.read_bytes()).hexdigest(),
        prefab_audit=audit,
    )


def _verify_generated_package(
    output_path: Path,
    expected_payload: bytes,
    variant: WitchHatVariant,
) -> None:
    """重读包并确认只有唯一的 0151 完整资源替换。"""
    package = load_cdmod_package(output_path)
    if package.dependencies:
        raise ValueError("结构帽子包不允许声明运行时依赖")
    if package.standalone_archives or package.resource_patches:
        raise ValueError("结构帽子包不能包含 standalone 或 resource-transform")
    files = [file for patch in package.file_patches for file in patch.files]
    if len(files) != 1:
        raise ValueError(f"结构帽子包替换文件数量异常：{len(files)}")
    file = files[0]
    if file.target != BATTLEFIELD_LIGHT_HAT_PATH or file.pamt_dir != HAT_PAMT_DIR:
        raise ValueError("结构帽子包最终目标异常")
    if file.content != expected_payload:
        raise ValueError("结构帽子包重读载荷不一致")
    original_target = expected_payload.replace(
        variant.source_main_pac,
        BATTLEFIELD_LIGHT_MAIN_PAC,
        1,
    )
    if build_structural_hat_prefab(original_target, variant.source_main_pac) != expected_payload:
        raise ValueError("结构帽子包不能通过等长映射往返验证")


def result_to_json(result: HatSwapBuildResult) -> dict[str, object]:
    """把生成结果转换为便于审计的 JSON。"""
    payload = asdict(result)
    payload["output_path"] = str(result.output_path)
    return payload


def build_all_hat_swap_mods(game_dir: Path, output_dir: Path) -> list[HatSwapBuildResult]:
    """在指定目录生成全部互斥帽子变体。"""
    return [
        build_hat_swap_mod(game_dir, output_dir / variant.output_filename, variant.key)
        for variant in HAT_VARIANTS
    ]


def main() -> int:
    """解析游戏目录、输出路径与女巫变体。"""
    parser = argparse.ArgumentParser(description="生成战场之光女巫帽结构变体 cdmod")
    parser.add_argument("game_dir", type=Path, help="Crimson Desert 游戏根目录")
    parser.add_argument("output", type=Path, help="单包输出路径或 --all 输出目录")
    parser.add_argument(
        "--variant",
        choices=tuple(HAT_VARIANTS_BY_KEY),
        default="white-crow",
        help="单包女巫变体，默认 white-crow",
    )
    parser.add_argument("--all", action="store_true", help="生成全部互斥变体")
    args = parser.parse_args()
    if args.all:
        results = build_all_hat_swap_mods(args.game_dir, args.output)
        print(
            json.dumps(
                [result_to_json(result) for result in results],
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    result = build_hat_swap_mod(args.game_dir, args.output, args.variant)
    print(json.dumps(result_to_json(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
