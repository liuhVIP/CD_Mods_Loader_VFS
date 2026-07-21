"""生成“大地荣誉皮制披风”替换为 0141 舞者披风的独立 ``.cdmod``。

原 ``Demenissian Clothing`` loose 模组直接提供了一份长度不同的目标 Prefab。
本工具以当前 1.14 原版 0163_t Prefab 为基底，只把内部唯一主 PAC 路径等长
替换为原生 0141 披风，保留目标组件、UID、骨骼插槽和完整字节布局。
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

# 披风 Prefab、模型、材质和物理资源都由原版 0009 PAMT 索引。
PAMT_DIR = "0009"

# “大地荣誉皮制披风”的女性目标 Prefab 与原始主 PAC。
TARGET_PREFAB_PATH = "character/cd_phw_00_cloak_00_0163_t.prefab"
TARGET_MAIN_PAC = (
    b"character/model/1_pc/2_phw/armor/19_cloak/cd_phw_00_cloak_00_0163.pac"
)

# Demenissian Clothing 实际借用的 0141 舞者披风原生资源链。
SOURCE_PREFAB_PATH = "character/cd_phw_00_cloak_00_0141.prefab"
SOURCE_MAIN_PAC_PATH = "character/cd_phw_00_cloak_00_0141.pac"
SOURCE_PROPERTY_PATH = "character/cd_phw_00_cloak_00_0141.pac_xml"
SOURCE_PHYSICS_PATH = "character/cd_phw_00_cloak_00_0141.hkx"
SOURCE_MAIN_PAC = (
    b"character/model/1_pc/2_phw/armor/19_cloak/cd_phw_00_cloak_00_0141.pac"
)

# 2026-07-21 从 Crimson Desert 1.14 原版读取的输入安全锚点。
EXPECTED_RESOURCE_SHA256 = {
    TARGET_PREFAB_PATH: (
        "c8fc5aac1c953ee8ea518f9ac3f90b07c611516fd9ac4dc17485e66fdd77de52"
    ),
    SOURCE_PREFAB_PATH: (
        "71ca9101ceee26756739b39c5c9ee8c145412b646c578e5dd7210d05a9999b89"
    ),
    SOURCE_MAIN_PAC_PATH: (
        "31bdc2a0d431a7bbe862399f0cc3f9e4f174f9c3f7c4b6519451746222554b0d"
    ),
    SOURCE_PROPERTY_PATH: (
        "18f2417a7c711d7a8bdddf108e28e1680f1ee2398d289f7f0522f577aedd8778"
    ),
    SOURCE_PHYSICS_PATH: (
        "2e1a6383c2f25b8bfef0c2c213ce9133bef805e6ad569dc9715cfe0201c5d8e1"
    ),
}

# cdmod 内组件和载荷使用固定路径，保证重复构建结果可审计。
FILE_REPLACEMENT_PATH = "files/replacements.json"
PREFAB_PAYLOAD_PATH = "assets/00000/cd_phw_00_cloak_00_0163_t.prefab"

PACKAGE_ID = "earths-honor-leather-cloak-dancer-0141"
PACKAGE_NAME = "Earth's Honor Leather Cloak - Dancer Cloak 0141"
PACKAGE_VERSION = "1.0"
OUTPUT_FILENAME = "ZZZ - Earths Honor Leather Cloak to Dancer Cloak-1.0.cdmod"


@dataclass(frozen=True)
class PrefabAudit:
    """记录目标 Prefab 的原版与结构补丁审计结果。"""

    target_path: str
    vanilla_size: int
    vanilla_sha256: str
    patched_size: int
    patched_sha256: str
    changed_byte_count: int
    model_reference_count: int


@dataclass(frozen=True)
class NativeResourceAudit:
    """记录一个无需打包、由游戏原生提供的 0141 资源。"""

    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class BuildResult:
    """独立披风包生成结果。"""

    output_path: Path
    package_sha256: str
    prefab: PrefabAudit
    native_resources: tuple[NativeResourceAudit, ...]


def build_structural_cloak_prefab(target_content: bytes) -> bytes:
    """在目标结构内等长替换唯一主 PAC 路径。"""
    if len(TARGET_MAIN_PAC) != len(SOURCE_MAIN_PAC):
        raise ValueError("目标与 0141 披风 PAC 路径长度不一致")
    if target_content.count(TARGET_MAIN_PAC) != 1:
        raise ValueError("原版 0163_t Prefab 中主 PAC 路径数量异常")
    if SOURCE_MAIN_PAC in target_content:
        raise ValueError("目标 Prefab 已经引用 0141 舞者披风")

    patched = target_content.replace(TARGET_MAIN_PAC, SOURCE_MAIN_PAC, 1)
    if len(patched) != len(target_content):
        raise ValueError("披风结构补丁意外改变 Prefab 长度")
    if patched.count(SOURCE_MAIN_PAC) != 1 or TARGET_MAIN_PAC in patched:
        raise ValueError("披风结构补丁后的主 PAC 引用异常")
    return patched


def build_dancer_cloak_mod(game_dir: Path, output_path: Path) -> BuildResult:
    """从当前原版资源生成只覆盖 0163_t Prefab 的独立包。"""
    game_dir = game_dir.resolve()
    output_path = output_path.resolve()
    pamt_path = game_dir / PAMT_DIR / "0.pamt"
    if not pamt_path.is_file():
        raise FileNotFoundError(f"缺少当前游戏 PAMT：{pamt_path}")

    requested_paths = set(EXPECTED_RESOURCE_SHA256)
    entries = parse_pamt_filtered(
        pamt_path,
        paz_dir=pamt_path.parent,
        desired_exact=requested_paths,
    )
    entries_by_path = {entry.path.casefold(): entry for entry in entries}
    missing = sorted(
        path for path in requested_paths if path.casefold() not in entries_by_path
    )
    if missing:
        raise ValueError(f"当前游戏缺少披风资源：{missing}")

    resources: dict[str, bytes] = {}
    native_audits: list[NativeResourceAudit] = []
    for path, expected_sha256 in EXPECTED_RESOURCE_SHA256.items():
        content, _compression_type = extract_plaintext(entries_by_path[path.casefold()])
        actual_sha256 = hashlib.sha256(content).hexdigest()
        if actual_sha256 != expected_sha256:
            raise ValueError(
                f"当前游戏披风资源已变化，拒绝盲目生成：{path} "
                f"expected={expected_sha256} actual={actual_sha256}"
            )
        resources[path] = content
        if path != TARGET_PREFAB_PATH:
            native_audits.append(
                NativeResourceAudit(
                    path=path,
                    size=len(content),
                    sha256=actual_sha256,
                )
            )

    source_prefab = resources[SOURCE_PREFAB_PATH]
    source_model_references = re.findall(
        rb"character/model/[^\x00]+?\.pac", source_prefab
    )
    if source_prefab.count(SOURCE_MAIN_PAC) != 1:
        raise ValueError("原版 0141 Prefab 中主 PAC 引用数量异常")
    if len(source_model_references) != 1:
        raise ValueError("原版 0141 Prefab 不是已验证的单模型结构")

    target = resources[TARGET_PREFAB_PATH]
    target_model_references = re.findall(rb"character/model/[^\x00]+?\.pac", target)
    if len(target_model_references) != 1:
        raise ValueError("原版 0163_t Prefab 不是已验证的单模型结构")
    patched = build_structural_cloak_prefab(target)
    patched_sha256 = hashlib.sha256(patched).hexdigest()
    audit = PrefabAudit(
        target_path=TARGET_PREFAB_PATH,
        vanilla_size=len(target),
        vanilla_sha256=hashlib.sha256(target).hexdigest(),
        patched_size=len(patched),
        patched_sha256=patched_sha256,
        changed_byte_count=sum(
            before != after for before, after in zip(target, patched, strict=True)
        ),
        model_reference_count=len(target_model_references),
    )

    replacements = {
        "schema": 1,
        "files": [
            {
                "target": TARGET_PREFAB_PATH,
                "pamt_dir": PAMT_DIR,
                "payload": PREFAB_PAYLOAD_PATH,
                "sha256": patched_sha256,
                "size": len(patched),
                "allow_new": False,
                "allow_table_replace": False,
            }
        ],
    }
    manifest = {
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": PACKAGE_ID,
        "name": PACKAGE_NAME,
        "version": PACKAGE_VERSION,
        "author": "Eyu94; structural extraction by cdmm",
        "description": (
            "Replaces only the Earth's Honor leather cloak 0163_t prefab with "
            "the native female Dancer cloak 0141 main PAC while preserving the "
            "complete target prefab structure."
        ),
        "dependencies": [],
        "source": {
            "format": "target-prefab-same-length-pac-path-replacement",
            "game_version": "1.14",
            "original_mod": "Demenissian Clothing by Eyu94",
        },
        "components": [
            {
                "type": CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE,
                "path": FILE_REPLACEMENT_PATH,
                "file_count": 1,
            }
        ],
    }
    report = {
        "schema": 1,
        "mapping": {
            "target_item": "Earth's Honor Leather Cloak",
            "target_prefab": TARGET_PREFAB_PATH,
            "old_main_pac": TARGET_MAIN_PAC.decode("ascii"),
            "new_main_pac": SOURCE_MAIN_PAC.decode("ascii"),
            "source_identity": "Dancer cloak 0141",
        },
        "prefab_audit": asdict(audit),
        "native_resources": [asdict(item) for item in native_audits],
        "safety": {
            "modifies_vanilla_archives": False,
            "uses_standalone_archive": False,
            "preserves_target_prefab_size": True,
            "preserves_target_component_layout": True,
            "uses_same_length_pac_path_replacement": True,
            "bundles_unrelated_demenissian_clothing_replacements": False,
        },
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_cdmod_zip(
        output_path,
        {
            CDMOD_MANIFEST_PATH: manifest,
            FILE_REPLACEMENT_PATH: replacements,
            PREFAB_PAYLOAD_PATH: patched,
            CDMOD_REPORT_PATH: report,
        },
    )
    _verify_package(output_path, patched)
    return BuildResult(
        output_path=output_path,
        package_sha256=hashlib.sha256(output_path.read_bytes()).hexdigest(),
        prefab=audit,
        native_resources=tuple(native_audits),
    )


def _verify_package(output_path: Path, expected_payload: bytes) -> None:
    """用正式加载器回读，确认成品只有一个目标 Prefab 替换。"""
    package = load_cdmod_package(output_path)
    if package.dependencies or package.standalone_archives or package.resource_patches:
        raise ValueError("独立披风包只能包含无依赖的 file-replacement")
    files = [item for patch in package.file_patches for item in patch.files]
    if len(files) != 1:
        raise ValueError(f"独立披风包替换文件数量异常：{len(files)}")
    item = files[0]
    if item.target != TARGET_PREFAB_PATH or item.pamt_dir != PAMT_DIR:
        raise ValueError("独立披风包最终目标异常")
    if item.content != expected_payload:
        raise ValueError("独立披风包载荷回读不一致")
    if len(item.content) != 1800:
        raise ValueError("独立披风包未保持当前原版 Prefab 长度")
    if item.content.count(SOURCE_MAIN_PAC) != 1 or TARGET_MAIN_PAC in item.content:
        raise ValueError("独立披风包主 PAC 路由回读异常")


def _parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="生成大地荣誉舞者披风独立 .cdmod")
    parser.add_argument(
        "--game-dir", type=Path, required=True, help="Crimson Desert 根目录"
    )
    parser.add_argument("--output", type=Path, required=True, help="输出 .cdmod 路径")
    return parser.parse_args()


def main() -> int:
    """生成包并输出 UTF-8 JSON 审计摘要。"""
    args = _parse_args()
    result = build_dancer_cloak_mod(args.game_dir, args.output)
    payload = asdict(result)
    payload["output_path"] = str(result.output_path)
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
