"""把女巫原生头部直接挂到角色创建器使用的 Kliff/Macduff 外观。

本工具复用已验证的 ``Damiane to Areciel`` 外观替换机制，但目标改为 Human
Female standalone 中的男性 Kliff/Macduff APP 外观。女性身体、骨骼、装备和
自定义配置保持不变，只替换 ``<Head>`` 内的原生头部 Prefab，因此理发师仍可
沿原角色创建器链路修改头发和发色；脸型切换不属于本包支持范围。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from cdmm.archive.pamt import parse_pamt
from cdmm.archive.paz_crypto import decrypt
from cdmm.common.models import OverlayInputEntry
from cdmm.services.cdmod_converter import (
    CDMOD_FORMAT_NAME,
    CDMOD_FORMAT_VERSION,
    CDMOD_MANIFEST_PATH,
    CDMOD_REPORT_PATH,
    CDMOD_STANDALONE_COMPONENT_TYPE,
    _write_cdmod_zip,
)
from cdmm.services.cdmod_package import load_cdmod_package
from cdmm.services.overlay_service import build_overlay

# Kliff/Macduff 男性角色身份在 Human Female 包中使用的 APP 外观文件。
KLIFF_APP_ENTRY_NAME = "cd_phm_macduff_00000.app_xml"

# 小型 standalone 覆盖组件的固定文档路径。
STANDALONE_ARCHIVE_INDEX_PATH = "archives/000/archive.json"
STANDALONE_PAMT_PATH = "archives/000/0.pamt"
STANDALONE_PAZ_PATH = "archives/000/0.paz"

# Areciel 原生头部资源 ID。
DEFAULT_WITCH_HEAD_ID = "0139"

# 正式强制挂载测试包的版本与展示名称。
FORCED_HEAD_PACKAGE_VERSION = "1.0-test"
FORCED_HEAD_PACKAGE_NAME = "Forced Areciel Face - Hair Customization"


@dataclass(frozen=True)
class KliffHeadMountPatch:
    """Kliff APP 外观头部替换摘要。"""

    old_head_id: str
    new_head_id: str
    replacement_count: int


@dataclass(frozen=True)
class KliffHeadMountBuildResult:
    """Kliff 直挂女巫头测试包生成结果。"""

    output_path: Path
    package_sha256: str
    source_package_sha256: str
    source_entry_path: str
    patch: KliffHeadMountPatch


def patch_kliff_head_prefab(
    app_xml_bytes: bytes,
    *,
    new_head_id: str,
) -> tuple[bytes, KliffHeadMountPatch]:
    """只替换 Kliff APP XML 的 ``<Head>`` Prefab，并保持文件长度不变。"""
    if not re.fullmatch(r"\d{4}", new_head_id):
        raise ValueError("new_head_id 必须是四位数字")

    text = app_xml_bytes.decode("utf-8-sig")
    head_match = re.search(r"<Head>[\s\S]*?</Head>", text, re.IGNORECASE)
    if head_match is None:
        raise ValueError("Kliff APP XML 缺少 <Head> 节点")
    head_block = head_match.group(0)
    prefab_match = re.search(
        r'Name="cd_phw_00_head_00_(\d{4})"',
        head_block,
        re.IGNORECASE,
    )
    if prefab_match is None:
        raise ValueError("Kliff <Head> 不是标准 PHW 原生头部 Prefab")
    old_head_id = prefab_match.group(1)
    if old_head_id == new_head_id:
        raise ValueError(f"Kliff <Head> 已经指向 {new_head_id}")

    patched_head = (
        head_block[:prefab_match.start(1)]
        + new_head_id
        + head_block[prefab_match.end(1):]
    )
    replacement_count = 1
    patched_text = text[:head_match.start()] + patched_head + text[head_match.end():]
    patched_bytes = patched_text.encode("utf-8-sig")
    if len(patched_bytes) != len(app_xml_bytes):
        raise ValueError("Kliff APP XML 头部替换后长度发生变化")
    return patched_bytes, KliffHeadMountPatch(
        old_head_id=old_head_id,
        new_head_id=new_head_id,
        replacement_count=replacement_count,
    )


def build_kliff_witch_head_mount_probe(
    source_package_path: Path,
    output_path: Path,
    *,
    new_head_id: str = DEFAULT_WITCH_HEAD_ID,
) -> KliffHeadMountBuildResult:
    """基于原 Human Female standalone 生成 Kliff 直挂女巫头覆盖包。"""
    source_package_path = source_package_path.resolve()
    output_path = output_path.resolve()
    source_package_bytes = source_package_path.read_bytes()
    source_package_sha256 = hashlib.sha256(source_package_bytes).hexdigest()
    package = load_cdmod_package(source_package_path)
    if len(package.standalone_archives) != 1:
        raise ValueError(f"预期 1 个 standalone archive，实际 {len(package.standalone_archives)} 个")
    standalone = package.standalone_archives[0]

    with TemporaryDirectory(prefix="kliff-witch-head-mount-") as temp_dir:
        temp_root = Path(temp_dir)
        pamt_path = temp_root / "0.pamt"
        paz_path = temp_root / "0.paz"
        pamt_path.write_bytes(standalone.pamt_bytes)
        paz_path.write_bytes(standalone.paz_bytes)
        matches = [
            entry
            for entry in parse_pamt(pamt_path, paz_dir=temp_root)
            if Path(entry.path).name.casefold() == KLIFF_APP_ENTRY_NAME
        ]
        if len(matches) != 1:
            raise ValueError(f"{KLIFF_APP_ENTRY_NAME} 候选数异常：{len(matches)}")
        entry = matches[0]
        if entry.compression_type != 0:
            raise ValueError("Kliff APP XML 不是当前已验证的未压缩形态")
        raw = standalone.paz_bytes[entry.offset:entry.offset + entry.comp_size]
        plaintext = decrypt(raw, KLIFF_APP_ENTRY_NAME)
        patched_plaintext, patch = patch_kliff_head_prefab(
            plaintext,
            new_head_id=new_head_id,
        )

    overlay = build_overlay(
        str(standalone.name),
        [
            OverlayInputEntry(
                content=patched_plaintext,
                entry_path=entry.path,
                pamt_dir=str(standalone.name),
                compression_type=0,
                encrypted=True,
                crypto_filename=KLIFF_APP_ENTRY_NAME,
                resolved_dir_path=entry.resolved_dir_path,
            )
        ],
        source_package_path.parent,
    )
    paz_bytes = bytes(overlay.paz_bytes)
    archive_document = {
        "schema": 1,
        "name": "kliff-witch-head-mount-overlay",
        "pamt": STANDALONE_PAMT_PATH,
        "paz": STANDALONE_PAZ_PATH,
        "pamt_sha256": hashlib.sha256(overlay.pamt_bytes).hexdigest(),
        "paz_sha256": hashlib.sha256(paz_bytes).hexdigest(),
    }
    manifest_document = {
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": f"kliff-witch-head-mount-{new_head_id}",
        "name": FORCED_HEAD_PACKAGE_NAME,
        "version": FORCED_HEAD_PACKAGE_VERSION,
        "author": "cdmm diagnostic",
        "description": (
            f"Forces native PHW head {new_head_id} on the Human Female Kliff/Macduff "
            "appearance. Hair and hair-color customization remain on the original "
            "Character Creator chain; face switching is intentionally unsupported."
        ),
        "dependencies": [],
        "source": {
            "format": "standalone-kliff-app-head-patch",
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
        "mapping": asdict(patch),
        "target": {
            "source_archive_name": standalone.name,
            "entry_path": entry.path,
            "resolved_dir_path": entry.resolved_dir_path,
        },
        "preserved": {
            "character_identity": "cd_phm_macduff",
            "customization_file": "cd_pc/cd_phw_damian_customization",
            "meshparam_file": "meshparam_example_kliff.xml",
            "hair_node_unchanged": True,
            "hair_color_customization": True,
            "female_body_and_skeleton": True,
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_cdmod_zip(
        output_path,
        {
            CDMOD_MANIFEST_PATH: manifest_document,
            STANDALONE_ARCHIVE_INDEX_PATH: archive_document,
            STANDALONE_PAMT_PATH: overlay.pamt_bytes,
            STANDALONE_PAZ_PATH: paz_bytes,
            CDMOD_REPORT_PATH: report_document,
        },
    )
    return KliffHeadMountBuildResult(
        output_path=output_path,
        package_sha256=hashlib.sha256(output_path.read_bytes()).hexdigest(),
        source_package_sha256=source_package_sha256,
        source_entry_path=entry.path,
        patch=patch,
    )


def result_to_json(result: KliffHeadMountBuildResult) -> dict[str, object]:
    """把生成结果转换为命令行 JSON。"""
    payload = asdict(result)
    payload["output_path"] = str(result.output_path)
    return payload


def main() -> int:
    """解析原 Human Female 包、输出包和女巫头部 ID。"""
    parser = argparse.ArgumentParser(description="生成 Kliff 强制挂载女巫头测试包")
    parser.add_argument("source", type=Path, help="原 Human Female standalone .cdmod")
    parser.add_argument("output", type=Path, help="输出测试 .cdmod")
    parser.add_argument("--head-id", default=DEFAULT_WITCH_HEAD_ID, help="四位女巫头部 ID")
    args = parser.parse_args()
    result = build_kliff_witch_head_mount_probe(
        args.source,
        args.output,
        new_head_id=args.head_id,
    )
    print(json.dumps(result_to_json(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
