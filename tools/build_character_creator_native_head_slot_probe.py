"""把角色创建器指定头部槽位改为直接引用原生头部身份。

本工具只处理包含 Human Female standalone archive 的 ``.cdmod``。它同时在
Damian 与 Kliff 的 meshparam 头部参数段内等长替换一个或多个 MeshSet，再生成
仅含这两份 XML 的小型 standalone 覆盖包，用于验证玩家实际身份读取的脸位表。
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
from cdmm.services.json_loader import extract_plaintext
from cdmm.services.overlay_service import build_overlay

# standalone 组件在探针包中的固定路径。
STANDALONE_ARCHIVE_INDEX_PATH = "archives/000/archive.json"
STANDALONE_PAMT_PATH = "archives/000/0.pamt"
STANDALONE_PAZ_PATH = "archives/000/0.paz"

# Human Female 角色创建器的两份玩家身份脸位表与头部参数段标记。
MESH_PARAM_ENTRY_NAMES = (
    "meshparam_example_damian.xml",
    "meshparam_example_kliff.xml",
)
# 兼容已有单文件测试与外部调用的 Damian 文件名常量。
MESH_PARAM_ENTRY_NAME = "meshparam_example_damian.xml"
HEAD_PARAM_MARKER = '<ParamDesc Index="1" Default = "0">'

# 首轮探针保留原生 0027 身份，只把界面第一张脸的 XML Index=0 指向它。
DEFAULT_XML_INDEX = 0
DEFAULT_SOURCE_HEAD_ID = "0027"

# 五个女巫原生头部到角色创建器第 2 至第 6 号脸的实机研究映射。
DEFAULT_WITCH_SLOT_MAPPINGS = (
    (2, "0139"),
    (3, "0143"),
    (4, "0141"),
    (5, "0019"),
    (6, "0046"),
)


@dataclass(frozen=True)
class HeadSlotPatch:
    """一个角色创建器头部槽位的等长映射摘要。"""

    xml_index: int
    old_head_id: str
    new_head_id: str
    replacement_count: int


@dataclass(frozen=True)
class NativeHeadSlotProbeBuildResult:
    """原生头部身份探针包生成摘要。"""

    output_path: Path
    package_sha256: str
    source_package_sha256: str
    archive_name: str
    meshparam_entry_path: str
    patch: HeadSlotPatch
    patches: tuple[HeadSlotPatch, ...]
    slot_mappings: tuple[tuple[int, str], ...]


def patch_head_meshset_xml(
    xml_bytes: bytes,
    *,
    xml_index: int,
    new_head_id: str,
    require_equal_length: bool = True,
) -> tuple[bytes, HeadSlotPatch]:
    """只在头部参数段内替换一个 MeshSet 的原生资源身份。"""
    if xml_index < 0:
        raise ValueError("xml_index 不能为负数")
    if not re.fullmatch(r"\d{4}", new_head_id):
        raise ValueError("new_head_id 必须是四位数字")

    text = xml_bytes.decode("utf-8-sig")
    head_start = text.find(HEAD_PARAM_MARKER)
    if head_start < 0:
        raise ValueError("未找到 Human Female 头部 ParamDesc")
    head_end = text.find("<ParamDesc", head_start + len(HEAD_PARAM_MARKER))
    if head_end < 0:
        raise ValueError("Human Female 头部 ParamDesc 缺少结束边界")

    head_section = text[head_start:head_end]
    block_pattern = re.compile(
        rf'<MeshSet\s+Index="{xml_index}"(?=\s|>)[\s\S]*?</MeshSet>',
        re.IGNORECASE,
    )
    blocks = list(block_pattern.finditer(head_section))
    if len(blocks) != 1:
        raise ValueError(f"头部 XML Index={xml_index} 候选数异常：{len(blocks)}")

    block_match = blocks[0]
    block = block_match.group(0)
    mesh_match = re.search(
        r'MeshFileName="cd_phw_00_head_00_(\d{4})([^\"]*)"',
        block,
        re.IGNORECASE,
    )
    skeleton_match = re.search(
        r'SkeletonVariation="[^"]*cd_phw_00_head_00_(\d{4})\.pabc"',
        block,
        re.IGNORECASE,
    )
    if mesh_match is None or skeleton_match is None:
        raise ValueError(f"头部 XML Index={xml_index} 不是完整头部 MeshSet")
    old_head_id = mesh_match.group(1)
    if skeleton_match.group(1) != old_head_id:
        raise ValueError(f"头部 XML Index={xml_index} 的 mesh/skeleton ID 不一致")
    if old_head_id == new_head_id:
        raise ValueError(f"头部 XML Index={xml_index} 已经指向 {new_head_id}")

    replacement_count = block.count(old_head_id)
    if replacement_count < 2:
        raise ValueError(f"头部 XML Index={xml_index} 的 ID 引用数不足：{replacement_count}")
    mesh_suffix = mesh_match.group(2)
    if mesh_suffix and require_equal_length:
        raise ValueError(
            f"头部 XML Index={xml_index} 使用特殊网格后缀 {mesh_suffix}，不能等长替换"
        )
    if mesh_suffix:
        patched_block = re.sub(
            r'(SkeletonVariation="[^\"]*cd_phw_00_head_00_)\d{4}(\.pabc")',
            rf"\g<1>{new_head_id}\g<2>",
            block,
            count=1,
            flags=re.IGNORECASE,
        )
        patched_block = re.sub(
            r'MeshFileName="cd_phw_00_head_00_\d{4}[^\"]*"',
            f'MeshFileName="cd_phw_00_head_00_{new_head_id}"',
            patched_block,
            count=1,
            flags=re.IGNORECASE,
        )
        patched_block = re.sub(
            r'(IconPath="[^\"]*khione_cd_phw_00_head_00_)\d{4}[^\"]*(\.dds")',
            rf"\g<1>{new_head_id}\g<2>",
            patched_block,
            count=1,
            flags=re.IGNORECASE,
        )
    else:
        patched_block = block.replace(old_head_id, new_head_id)
    if require_equal_length and len(patched_block.encode("utf-8")) != len(block.encode("utf-8")):
        raise ValueError("头部 MeshSet 替换后长度发生变化")

    absolute_start = head_start + block_match.start()
    absolute_end = head_start + block_match.end()
    patched_text = text[:absolute_start] + patched_block + text[absolute_end:]
    patched_bytes = patched_text.encode("utf-8-sig")
    if require_equal_length and len(patched_bytes) != len(xml_bytes):
        raise ValueError("meshparam XML 替换后总长度发生变化")
    return patched_bytes, HeadSlotPatch(
        xml_index=xml_index,
        old_head_id=old_head_id,
        new_head_id=new_head_id,
        replacement_count=replacement_count,
    )


def build_native_head_slot_probe(
    source_package_path: Path,
    output_path: Path,
    *,
    xml_index: int = DEFAULT_XML_INDEX,
    new_head_id: str = DEFAULT_SOURCE_HEAD_ID,
    mappings: tuple[tuple[int, str], ...] | None = None,
) -> NativeHeadSlotProbeBuildResult:
    """基于现有 Human Female standalone 包生成原生头部身份补丁。"""
    source_package_path = source_package_path.resolve()
    output_path = output_path.resolve()
    normalized_mappings = _validate_mappings(
        mappings if mappings is not None else ((xml_index, new_head_id),)
    )
    source_package_bytes = source_package_path.read_bytes()
    source_package_sha256 = hashlib.sha256(source_package_bytes).hexdigest()
    package = load_cdmod_package(source_package_path)
    if len(package.standalone_archives) != 1:
        raise ValueError(f"预期 1 个 standalone archive，实际 {len(package.standalone_archives)} 个")
    standalone = package.standalone_archives[0]

    overlay_inputs: list[OverlayInputEntry] = []
    patches: list[HeadSlotPatch] = []
    targets: list[dict[str, object]] = []
    with TemporaryDirectory(prefix="character-creator-head-slot-") as temp_dir:
        temp_root = Path(temp_dir)
        pamt_path = temp_root / "0.pamt"
        paz_path = temp_root / "0.paz"
        pamt_path.write_bytes(standalone.pamt_bytes)
        paz_path.write_bytes(standalone.paz_bytes)
        matches = [
            entry
            for entry in parse_pamt(pamt_path, paz_dir=temp_root)
            if Path(entry.path).name.casefold() in MESH_PARAM_ENTRY_NAMES
        ]
        matches_by_name = {Path(entry.path).name.casefold(): entry for entry in matches}
        missing_names = sorted(set(MESH_PARAM_ENTRY_NAMES) - matches_by_name.keys())
        if missing_names:
            raise ValueError(f"Human Female 缺少玩家脸位表：{missing_names}")
        for entry_name in MESH_PARAM_ENTRY_NAMES:
            entry = matches_by_name[entry_name]
            if entry.compression_type not in {0, 2} or not entry.encrypted:
                raise ValueError(f"{entry_name} 不是当前已验证的加密 XML 形态")
            plaintext, _detected = extract_plaintext(entry)
            patched_plaintext = plaintext
            entry_patches: list[HeadSlotPatch] = []
            for target_xml_index, target_head_id in normalized_mappings:
                patched_plaintext, patch = patch_head_meshset_xml(
                    patched_plaintext,
                    xml_index=target_xml_index,
                    new_head_id=target_head_id,
                    require_equal_length=False,
                )
                entry_patches.append(patch)
            overlay_inputs.append(
                OverlayInputEntry(
                    content=patched_plaintext,
                    entry_path=entry.path,
                    pamt_dir=str(standalone.name),
                    compression_type=entry.compression_type,
                    encrypted=True,
                    crypto_filename=Path(entry.path).name,
                    resolved_dir_path=entry.resolved_dir_path,
                )
            )
            patches.extend(entry_patches)
            targets.append(
                {
                    "source_archive_name": standalone.name,
                    "entry_path": entry.path,
                    "source_entry_offset": entry.offset,
                    "source_entry_size": entry.comp_size,
                    "source_entry_plaintext_size": entry.orig_size,
                }
            )

    overlay = build_overlay(str(standalone.name), overlay_inputs, source_package_path.parent)
    patched_paz_bytes = bytes(overlay.paz_bytes)
    archive_document = {
        "schema": 1,
        "name": "barber-head-slot-overlay",
        "pamt": STANDALONE_PAMT_PATH,
        "paz": STANDALONE_PAZ_PATH,
        "pamt_sha256": hashlib.sha256(overlay.pamt_bytes).hexdigest(),
        "paz_sha256": hashlib.sha256(patched_paz_bytes).hexdigest(),
    }
    is_dependency_patch = len(normalized_mappings) > 1
    mapping_label = "-".join(
        f"{target_xml_index}-{target_head_id}"
        for target_xml_index, target_head_id in normalized_mappings
    )
    dependency_ids = list(package.dependencies)
    if is_dependency_patch and package.mod_id not in dependency_ids:
        dependency_ids.append(package.mod_id)
    manifest_document = {
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": (
            f"{package.mod_id}-native-head-slots-patch-{mapping_label}"
            if is_dependency_patch
            else f"{package.mod_id}-native-head-slot-probe-{xml_index}-{new_head_id}"
        ),
        "name": (
            f"{package.name} - Five Witch Head Slots Patch"
            if is_dependency_patch
            else f"{package.name} - Native Head Slot Probe"
        ),
        "version": "1.0-test" if is_dependency_patch else "1.0",
        "author": "cdmm diagnostic",
        "description": (
            "Maps Human Female head XML slots directly to native witch head identities "
            "without copying or redistributing the source Character Creator archive."
            if is_dependency_patch
            else (
                f"Maps Human Female head XML Index {xml_index} directly to native head "
                f"{new_head_id} without copying or renaming head resources."
            )
        ),
        "dependencies": dependency_ids,
        "source": {
            "format": "standalone-equal-length-encrypted-xml-patch",
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
        "slot_mappings": [
            {"xml_index": target_xml_index, "head_id": target_head_id}
            for target_xml_index, target_head_id in normalized_mappings
        ],
        "mappings": [asdict(patch) for patch in patches],
        "targets": targets,
        "safety": {
            "source_archive_unchanged": True,
            "overlay_entry_count": len(overlay_inputs),
            "entry_length_unchanged": all(
                len(entry.content) == target["source_entry_plaintext_size"]
                for entry, target in zip(overlay_inputs, targets, strict=True)
            ),
            "unrelated_entries_copied": False,
            "head_resources_copied": False,
            "native_head_identity_preserved": True,
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_cdmod_zip(
        output_path,
        {
            CDMOD_MANIFEST_PATH: manifest_document,
            STANDALONE_ARCHIVE_INDEX_PATH: archive_document,
            STANDALONE_PAMT_PATH: overlay.pamt_bytes,
            STANDALONE_PAZ_PATH: patched_paz_bytes,
            CDMOD_REPORT_PATH: report_document,
        },
    )
    return NativeHeadSlotProbeBuildResult(
        output_path=output_path,
        package_sha256=hashlib.sha256(output_path.read_bytes()).hexdigest(),
        source_package_sha256=source_package_sha256,
        archive_name=str(archive_document["name"]),
        meshparam_entry_path=str(targets[0]["entry_path"]),
        patch=patches[0],
        patches=tuple(patches),
        slot_mappings=normalized_mappings,
    )


def _validate_mappings(
    mappings: tuple[tuple[int, str], ...],
) -> tuple[tuple[int, str], ...]:
    """校验并稳定排序多个脸位到原生头部 ID 的映射。"""
    if not mappings:
        raise ValueError("至少需要一个头部槽位映射")
    normalized: list[tuple[int, str]] = []
    seen_indexes: set[int] = set()
    for xml_index, head_id in mappings:
        if xml_index < 0:
            raise ValueError("xml_index 不能为负数")
        if xml_index in seen_indexes:
            raise ValueError(f"头部 XML Index={xml_index} 重复")
        if not re.fullmatch(r"\d{4}", head_id):
            raise ValueError("new_head_id 必须是四位数字")
        seen_indexes.add(xml_index)
        normalized.append((xml_index, head_id))
    return tuple(sorted(normalized))


def _parse_mappings(value: str) -> tuple[tuple[int, str], ...]:
    """解析命令行逗号分隔的 ``Index:头部ID`` 映射。"""
    try:
        mappings = tuple(
            (int(index_text), head_id.strip())
            for item in value.split(",")
            for index_text, head_id in [item.split(":", maxsplit=1)]
        )
        return _validate_mappings(mappings)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(
            "--mappings 格式应为 2:0139,3:0143,4:0141,5:0019,6:0046"
        ) from exc


def result_to_json(result: NativeHeadSlotProbeBuildResult) -> dict[str, object]:
    """把探针生成摘要转换为命令行 JSON。"""
    payload = asdict(result)
    payload["output_path"] = str(result.output_path)
    return payload


def main() -> int:
    """解析输入包、输出包和头部槽位参数。"""
    parser = argparse.ArgumentParser(description="生成角色创建器原生头部身份槽位探针")
    parser.add_argument("source", type=Path, help="原 Human Female standalone .cdmod")
    parser.add_argument("output", type=Path, help="输出探针 .cdmod")
    parser.add_argument("--xml-index", type=int, default=DEFAULT_XML_INDEX, help="头部 XML Index")
    parser.add_argument(
        "--head-id",
        default=DEFAULT_SOURCE_HEAD_ID,
        help="四位原生头部资源 ID",
    )
    parser.add_argument(
        "--mappings",
        type=_parse_mappings,
        help="逗号分隔的多个 Index:头部ID；指定后忽略 --xml-index/--head-id",
    )
    args = parser.parse_args()
    result = build_native_head_slot_probe(
        args.source,
        args.output,
        xml_index=args.xml_index,
        new_head_id=args.head_id,
        mappings=args.mappings,
    )
    print(json.dumps(result_to_json(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
