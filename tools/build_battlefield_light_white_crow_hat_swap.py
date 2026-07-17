"""通过条件装配映射生成“战场之光”替换白鸦帽子的独立测试模组。

直接覆盖 ``cd_phw_00_hel_00_0151.prefab`` 已实机确认会在完成 SaveSlot105
后立即崩溃。本工具改为复用 Female Armor Module 已验证的
``conditionalpartprefab_transmog.xml`` 机制，只新增一条 0151 到 0164 的
条件映射。游戏会合并解析同路径 standalone XML，因此输出必须完整替代原
Female Armor standalone，不能与原包并存；原始 Prefab、PAC、HKX、材质和
纹理均保持不变。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from cdmm.archive.pamt import parse_pamt, parse_pamt_filtered
from cdmm.services.cdmod_converter import (
    CDMOD_FORMAT_NAME,
    CDMOD_FORMAT_VERSION,
    CDMOD_MANIFEST_PATH,
    CDMOD_REPORT_PATH,
    CDMOD_STANDALONE_COMPONENT_TYPE,
    _write_cdmod_zip,
)
from cdmm.services.cdmod_package import load_cdmod_package
from cdmm.services.json_loader import extract_plaintext

# 头饰资源所在的原版 PAMT 目录。
HAT_PAMT_DIR = "0009"

# “战场之光”头饰和白鸦白色帽子的真实游戏路径。
BATTLEFIELD_LIGHT_HAT_PATH = "character/cd_phw_00_hel_00_0151.prefab"
WHITE_CROW_HAT_PATH = "character/cd_phw_00_hel_00_0164.prefab"

# 条件装配系统使用不带扩展名的 PartPrefab 身份。
BATTLEFIELD_LIGHT_HAT_IDENTITY = "cd_phw_00_hel_00_0151"
WHITE_CROW_HAT_IDENTITY = "cd_phw_00_hel_00_0164"
FEMALE_BODY_MATCH_GROUP = "ForDamiane"

# 白鸦帽子 Prefab 必须保留的原版主帽和附属配件引用。
WHITE_CROW_REQUIRED_REFERENCES = (
    b"cd_phw_00_hel_00_0164.pac",
    b"cd_phw_00_hel_00_0164_sub01.pac",
)

# Female Armor Module standalone 中的条件装配表。
CONDITIONAL_ENTRY_NAME = "conditionalpartprefab_transmog.xml"

# standalone 组件在 cdmod 内的固定路径。
STANDALONE_ARCHIVE_INDEX_PATH = "archives/000/archive.json"
STANDALONE_PAMT_PATH = "archives/000/0.pamt"
STANDALONE_PAZ_PATH = "archives/000/0.paz"

# 测试包展示信息。
PACKAGE_NAME = "Light of the Battlefield - White Crow Hat Conditional Swap"
PACKAGE_VERSION = "0.3-test"


@dataclass(frozen=True)
class PrefabAudit:
    """原版源/目标 Prefab 的构建前审计结果。"""

    source_path: str
    source_size: int
    source_sha256: str
    target_path: str
    target_size: int
    target_sha256: str
    required_reference_count: int


@dataclass(frozen=True)
class ConditionalMappingAudit:
    """Female Armor Module 条件表的补丁审计结果。"""

    source_package_sha256: str
    source_entry_path: str
    source_entry_sha256: str
    source_entry_size: int
    original_condition_count: int
    patched_entry_sha256: str
    patched_entry_size: int
    patched_condition_count: int


@dataclass(frozen=True)
class HatSwapBuildResult:
    """条件式帽子替换测试包生成结果。"""

    output_path: Path
    package_sha256: str
    archive_pamt_sha256: str
    archive_paz_sha256: str
    prefab_audit: PrefabAudit
    mapping_audit: ConditionalMappingAudit


def audit_original_prefabs(game_dir: Path) -> PrefabAudit:
    """从原版 0009 精确读取源/目标 Prefab，并验证白鸦双部件引用。"""
    pamt_path = game_dir.resolve() / HAT_PAMT_DIR / "0.pamt"
    if not pamt_path.is_file():
        raise FileNotFoundError(f"缺少原版头饰 PAMT：{pamt_path}")

    desired_paths = {BATTLEFIELD_LIGHT_HAT_PATH, WHITE_CROW_HAT_PATH}
    entries = parse_pamt_filtered(
        pamt_path,
        paz_dir=pamt_path.parent,
        desired_exact=desired_paths,
    )
    entries_by_path = {entry.path.casefold(): entry for entry in entries}
    missing = sorted(path for path in desired_paths if path.casefold() not in entries_by_path)
    if missing:
        raise ValueError(f"原版 0009 缺少头饰 Prefab：{missing}")

    source = entries_by_path[WHITE_CROW_HAT_PATH.casefold()]
    target = entries_by_path[BATTLEFIELD_LIGHT_HAT_PATH.casefold()]
    source_content, _source_entry = extract_plaintext(source)
    target_content, _target_entry = extract_plaintext(target)
    missing_references = [
        reference.decode("ascii")
        for reference in WHITE_CROW_REQUIRED_REFERENCES
        if reference not in source_content
    ]
    if missing_references:
        raise ValueError(f"白鸦帽子 Prefab 缺少预期资源引用：{missing_references}")
    if b"cd_phw_00_hel_00_0151.pac" not in target_content:
        raise ValueError("战场之光头饰 Prefab 不再引用 0151 PAC，疑似游戏资源已更新")

    return PrefabAudit(
        source_path=source.path,
        source_size=len(source_content),
        source_sha256=hashlib.sha256(source_content).hexdigest(),
        target_path=target.path,
        target_size=len(target_content),
        target_sha256=hashlib.sha256(target_content).hexdigest(),
        required_reference_count=len(WHITE_CROW_REQUIRED_REFERENCES),
    )


def patch_conditional_part_prefab_xml(xml_bytes: bytes) -> bytes:
    """利用空白预算等长新增 0151 到 0164 条件映射。"""
    text = xml_bytes.decode("utf-8-sig")
    group_marker = f'<PartPrefabGroup Name="{FEMALE_BODY_MATCH_GROUP}">'
    if text.count(group_marker) != 1:
        raise ValueError(f"条件表中的 {FEMALE_BODY_MATCH_GROUP} 分组数量异常")

    source_pattern = re.compile(
        rf'<Condition\s+SourcePartPrefab="{re.escape(BATTLEFIELD_LIGHT_HAT_IDENTITY)}"(?=\s|>)',
        re.IGNORECASE,
    )
    if source_pattern.search(text):
        raise ValueError("条件表已经存在战场之光 0151 头饰映射，拒绝重复添加")

    condition_text = (
        f'<Condition SourcePartPrefab="{BATTLEFIELD_LIGHT_HAT_IDENTITY}">\r\n'
        f'    <If Type="Match" TargetPartPrefab="{WHITE_CROW_HAT_IDENTITY}" '
        f'MatchPartPrefabGroup="{FEMALE_BODY_MATCH_GROUP}"/>\r\n'
        "</Condition>"
    )
    condition_bytes = condition_text.encode("utf-8")
    compacted = re.sub(rb"(?:\r\n){2,}", b"\r\n", xml_bytes)
    body = compacted.rstrip(b"\r\n\t ")
    patched = body + b"\r\n" + condition_bytes + b"\r\n"
    if len(patched) > len(xml_bytes):
        raise ValueError(
            f"条件表空白预算不足：需要 {len(patched) - len(xml_bytes)} 个额外字节"
        )
    padding_size = len(xml_bytes) - len(patched)
    patched += b"\r\n" * (padding_size // 2)
    if padding_size % 2:
        patched += b" "
    if len(patched) != len(xml_bytes):
        raise ValueError("条件表等长重建失败")

    patched_text = patched.decode("utf-8-sig")
    if patched_text.count(condition_text) != 1:
        raise ValueError("新增条件映射数量异常")
    if len(source_pattern.findall(patched_text)) != 1:
        raise ValueError("新增后战场之光 0151 来源条件数量异常")
    return patched


def build_hat_swap_mod(
    game_dir: Path,
    source_package_path: Path,
    output_path: Path,
) -> HatSwapBuildResult:
    """基于 Female Armor Module 条件表生成独立 standalone cdmod。"""
    output_path = output_path.resolve()
    source_package_path = source_package_path.resolve()
    prefab_audit = audit_original_prefabs(game_dir)
    source_package_sha256 = hashlib.sha256(source_package_path.read_bytes()).hexdigest()
    source_package = load_cdmod_package(source_package_path)
    if len(source_package.standalone_archives) != 1:
        raise ValueError(
            f"Female Armor Module 预期 1 个 standalone，实际 "
            f"{len(source_package.standalone_archives)} 个"
        )
    source_archive = source_package.standalone_archives[0]

    with TemporaryDirectory(prefix="white-crow-hat-conditional-") as temp_dir:
        temp_root = Path(temp_dir)
        source_pamt_path = temp_root / "0.pamt"
        source_paz_path = temp_root / "0.paz"
        source_pamt_path.write_bytes(source_archive.pamt_bytes)
        source_paz_path.write_bytes(source_archive.paz_bytes)
        entries = parse_pamt(source_pamt_path, paz_dir=temp_root)
        matches = [
            entry
            for entry in entries
            if Path(entry.path).name.casefold() == CONDITIONAL_ENTRY_NAME
        ]
        if len(matches) != 1:
            raise ValueError(f"条件装配表候选数异常：{len(matches)}")
        entry = matches[0]
        if entry.compression_type != 0 or entry.comp_size != entry.orig_size:
            raise ValueError("条件装配表不是当前已验证的未压缩形态")
        source_content = source_archive.paz_bytes[
            entry.offset:entry.offset + entry.comp_size
        ]
        if not source_content.startswith(b"\xef\xbb\xbf<PartPrefabGroup"):
            raise ValueError("条件装配表不是当前已验证的 UTF-8 BOM 明文 XML")
        patched_content = patch_conditional_part_prefab_xml(source_content)
        original_condition_count = source_content.count(b"<Condition ")
        patched_condition_count = patched_content.count(b"<Condition ")
        if patched_condition_count != original_condition_count + 1:
            raise ValueError("条件装配表补丁后 Condition 数量异常")

        patched_paz = bytearray(source_archive.paz_bytes)
        payload_end = entry.offset + entry.comp_size
        patched_paz[entry.offset:payload_end] = patched_content

    archive_pamt_bytes = source_archive.pamt_bytes
    archive_paz_bytes = bytes(patched_paz)
    if len(archive_paz_bytes) != len(source_archive.paz_bytes):
        raise ValueError("完整 Female Armor PAZ 长度发生变化")
    if archive_paz_bytes[:entry.offset] != source_archive.paz_bytes[:entry.offset]:
        raise ValueError("条件表前方 PAZ 字节意外变化")
    if archive_paz_bytes[payload_end:] != source_archive.paz_bytes[payload_end:]:
        raise ValueError("条件表后方 PAZ 字节意外变化")
    archive_pamt_sha256 = hashlib.sha256(archive_pamt_bytes).hexdigest()
    archive_paz_sha256 = hashlib.sha256(archive_paz_bytes).hexdigest()
    mapping_audit = ConditionalMappingAudit(
        source_package_sha256=source_package_sha256,
        source_entry_path=entry.path,
        source_entry_sha256=hashlib.sha256(source_content).hexdigest(),
        source_entry_size=len(source_content),
        original_condition_count=original_condition_count,
        patched_entry_sha256=hashlib.sha256(patched_content).hexdigest(),
        patched_entry_size=len(patched_content),
        patched_condition_count=patched_condition_count,
    )
    archive_document = {
        "schema": 1,
        "name": source_archive.name,
        "pamt": STANDALONE_PAMT_PATH,
        "paz": STANDALONE_PAZ_PATH,
        "pamt_sha256": archive_pamt_sha256,
        "paz_sha256": archive_paz_sha256,
    }
    manifest_document = {
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": "battlefield-light-white-crow-hat-conditional",
        "name": PACKAGE_NAME,
        "version": PACKAGE_VERSION,
        "author": "cdmm research",
        "description": (
            "Uses Female Armor Module conditional part-prefab mapping to select "
            "White Crow's native 0164 witch hat for the 0151 Light of the "
            "Battlefield female headgear without overriding either prefab. This "
            "package replaces the Female Armor standalone and must not coexist "
            "with the original standalone package."
        ),
        "dependencies": [],
        "source": {
            "format": "conditional-part-prefab-standalone-overlay",
            "source_package_sha256": source_package_sha256,
        },
        "components": [
            {
                "type": CDMOD_STANDALONE_COMPONENT_TYPE,
                "path": STANDALONE_ARCHIVE_INDEX_PATH,
            }
        ],
    }
    report_document = {
        "schema": 1,
        "mapping": {
            "source_part_prefab": BATTLEFIELD_LIGHT_HAT_IDENTITY,
            "target_part_prefab": WHITE_CROW_HAT_IDENTITY,
            "match_part_prefab_group": FEMALE_BODY_MATCH_GROUP,
        },
        "prefab_audit": asdict(prefab_audit),
        "conditional_mapping_audit": asdict(mapping_audit),
        "safety": {
            "prefab_override": False,
            "pac_override": False,
            "dds_override": False,
            "source_female_armor_module_unchanged": True,
            "source_pamt_unchanged": True,
            "source_paz_length_unchanged": True,
            "only_conditional_entry_bytes_changed": True,
            "must_disable_source_standalone": source_package.path.name,
        },
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_cdmod_zip(
        output_path,
        {
            CDMOD_MANIFEST_PATH: manifest_document,
            STANDALONE_ARCHIVE_INDEX_PATH: archive_document,
            STANDALONE_PAMT_PATH: archive_pamt_bytes,
            STANDALONE_PAZ_PATH: archive_paz_bytes,
            CDMOD_REPORT_PATH: report_document,
        },
    )
    _verify_generated_package(
        output_path,
        expected_pamt=archive_pamt_bytes,
        expected_paz=archive_paz_bytes,
        expected_content=patched_content,
    )
    return HatSwapBuildResult(
        output_path=output_path,
        package_sha256=hashlib.sha256(output_path.read_bytes()).hexdigest(),
        archive_pamt_sha256=archive_pamt_sha256,
        archive_paz_sha256=archive_paz_sha256,
        prefab_audit=prefab_audit,
        mapping_audit=mapping_audit,
    )


def _verify_generated_package(
    output_path: Path,
    *,
    expected_pamt: bytes,
    expected_paz: bytes,
    expected_content: bytes,
) -> None:
    """重读生成包，确认完整 archive 仅修改目标条件表槽位。"""
    package = load_cdmod_package(output_path)
    if package.resource_patches or package.file_patches:
        raise ValueError("条件式帽子包不应包含 Prefab resource/file replacement")
    if len(package.standalone_archives) != 1:
        raise ValueError("条件式帽子包 standalone 数量异常")
    archive = package.standalone_archives[0]
    if archive.pamt_bytes != expected_pamt or archive.paz_bytes != expected_paz:
        raise ValueError("条件式帽子包完整 archive 重读字节不一致")
    with TemporaryDirectory(prefix="verify-white-crow-hat-") as temp_dir:
        temp_root = Path(temp_dir)
        pamt_path = temp_root / "0.pamt"
        paz_path = temp_root / "0.paz"
        pamt_path.write_bytes(archive.pamt_bytes)
        paz_path.write_bytes(archive.paz_bytes)
        entries = parse_pamt(pamt_path, paz_dir=temp_root)
        matches = [
            entry
            for entry in entries
            if Path(entry.path).name.casefold() == CONDITIONAL_ENTRY_NAME
        ]
        if len(matches) != 1:
            raise ValueError(f"条件式帽子包条件表数量异常：{len(matches)}")
        entry = matches[0]
        content = archive.paz_bytes[entry.offset:entry.offset + entry.comp_size]
        if content != expected_content:
            raise ValueError("条件式帽子包重读内容与构建内容不一致")
        if entry.resolved_dir_path != "character/descriptors/conditionalpartprefab":
            raise ValueError(f"条件式帽子包最终目录异常：{entry.resolved_dir_path}")


def result_to_json(result: HatSwapBuildResult) -> dict[str, object]:
    """把构建结果转换为便于审计的 JSON。"""
    payload = asdict(result)
    payload["output_path"] = str(result.output_path)
    return payload


def main() -> int:
    """解析游戏目录、Female Armor standalone 包和输出路径。"""
    parser = argparse.ArgumentParser(description="生成单包条件式战场之光白鸦帽测试 cdmod")
    parser.add_argument("game_dir", type=Path, help="Crimson Desert 游戏根目录")
    parser.add_argument("source", type=Path, help="Female Armor Module standalone .cdmod")
    parser.add_argument("output", type=Path, help="输出 .cdmod 路径")
    args = parser.parse_args()
    result = build_hat_swap_mod(args.game_dir, args.source, args.output)
    print(json.dumps(result_to_json(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
