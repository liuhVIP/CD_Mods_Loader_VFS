"""在完整 Human Female 资源内安全重打包女巫脸位映射。

工具既支持旧版完整 ``.cdmod`` 的精确尺寸重打包，也支持游戏更新后作者发布的
``0009/0012`` loose 目录。目录模式以全部新版资源为基底重建 ``0.paz/0.pamt``，
同步修改 Damian 与 Kliff 两份 meshparam，并保留新增资源。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

import lz4.block

from cdmm.archive.pamt import parse_pamt
from cdmm.archive.paz_crypto import encrypt, lz4_compress
from cdmm.common.constants import HASH_SEED, PAZ_ALIGNMENT
from cdmm.common.hashlittle import hashlittle
from cdmm.common.models import BuiltOverlayEntry
from cdmm.services.cdmod_converter import (
    CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE,
    CDMOD_FORMAT_NAME,
    CDMOD_FORMAT_VERSION,
    CDMOD_MANIFEST_PATH,
    CDMOD_REPORT_PATH,
    CDMOD_STANDALONE_COMPONENT_TYPE,
    _write_cdmod_zip,
)
from cdmm.services.cdmod_package import load_cdmod_package
from cdmm.services.json_loader import extract_plaintext
from cdmm.services.overlay_service import _build_multi_pamt
from cdmm.tools.build_character_creator_native_head_slot_probe import (
    MESH_PARAM_ENTRY_NAMES,
    HeadSlotPatch,
    patch_head_meshset_xml,
)

# 完整 standalone 组件在 cdmod 内的固定路径。
STANDALONE_ARCHIVE_INDEX_PATH = "archives/000/archive.json"
STANDALONE_PAMT_PATH = "archives/000/0.pamt"
STANDALONE_PAZ_PATH = "archives/000/0.paz"

# K-Makeup DDS 组件在组合包中的固定路径。
MAKEUP_REPLACEMENT_INDEX_PATH = "files/makeup-replacements.json"
MAKEUP_DIFFUSE_SOURCE_PATH = (
    "character/texture/cd_phw_00_head_base_youth_0027.dds"
)
MAKEUP_NORMAL_SOURCE_PATH = (
    "character/texture/cd_phw_00_head_base_youth_0027_n.dds"
)
MAKEUP_PAMT_DIR = "0009"

# 保留原 0027 妆容，并覆盖五位女巫 PAC XML 实际读取的唯一肤色/法线路径。
MAKEUP_TEXTURE_TARGETS = (
    MAKEUP_DIFFUSE_SOURCE_PATH,
    MAKEUP_NORMAL_SOURCE_PATH,
    "character/texture/cd_phw_00_head_00_0139.dds",
    "character/texture/cd_phw_00_head_00_0139_n.dds",
    "character/texture/cd_phw_00_head_00_0141.dds",
    "character/texture/cd_phw_00_head_00_0141_n.dds",
    "character/texture/cd_phw_00_head_00_0143_n.dds",
    "character/texture/cd_phw_00_head_base_youth_0019.dds",
    "character/texture/cd_phw_00_head_base_youth_0019_n.dds",
    "character/texture/cd_phw_00_head_00_0046.dds",
    "character/texture/cd_phw_00_head_00_0046_n.dds",
)

# 五位女巫头部在 Human Female 脸型参数表中的目标位置。
DEFAULT_SLOT_MAPPINGS = (
    (2, "0139"),
    (3, "0143"),
    (4, "0141"),
    (5, "0019"),
    (6, "0046"),
)

# 五位女巫原生发型在 Human Female Hair 参数表中的已确认位置。
DEFAULT_WITCH_HAIR_SLOT_MAPPINGS = (
    (2, 51, "Areciel", "cd_phw_00_hair_00_0504"),
    (3, 18, "Bari", "cd_phw_00_hair_00_0007_01"),
    (4, 14, "Elowen", "cd_phw_00_hair_00_0006_04"),
    (5, 52, "Lyselia", "cd_phw_00_hair_00_0505"),
    (7, 48, "White Crow", "cd_phw_00_hair_00_0018"),
)

# Human Female 发型参数段固定使用 ParamDesc Index=2。
HAIR_PARAM_INDEX = 2

# 原有 XML 注释体使用的纯 ASCII 字符，排除 XML 特殊字符和双横线来源。
SAFE_XML_COMMENT_ALPHABET = bytes(
    value
    for value in range(0x21, 0x7F)
    if value not in b"-<>&"
)

# 不同确定性填充种子上限，避免无法精确匹配时无限搜索。
MAX_SAFE_XML_FILL_SEEDS = 256

# loose Human Female 完整包重建时使用的 standalone 目录名。
LOOSE_STANDALONE_ARCHIVE_NAME = "0036"

# 游戏按文件名派生密钥加密这些文本资源；APP_XML 保持原始未加密载荷。
ENCRYPTED_TEXT_SUFFIXES = (".xml", ".html", ".css", ".js")


@dataclass(frozen=True)
class FullMeshparamPatchSummary:
    """完整包内一份 meshparam 的脸位修改摘要。"""

    entry_name: str
    patches: tuple[HeadSlotPatch, ...]
    hair_patches: tuple["HairSlotSwapPatch", ...]
    original_comp_size: int
    rebuilt_comp_size: int
    original_plaintext_size: int
    adjusted_comment_byte_count: int


@dataclass(frozen=True)
class HairSlotSwapPatch:
    """一个女巫发型与目标角色创建器发型位的成对交换摘要。"""

    target_index: int
    source_index: int
    witch_name: str
    witch_hair: str
    displaced_hair: str


@dataclass(frozen=True)
class FullCharacterCreatorRepackResult:
    """完整 Human Female 重打包结果。"""

    output_path: Path
    package_sha256: str
    source_package_sha256: str
    archive_pamt_sha256: str
    archive_paz_sha256: str
    mappings: tuple[tuple[int, str], ...]
    hair_mappings: tuple[tuple[int, int, str, str], ...]
    entries: tuple[FullMeshparamPatchSummary, ...]
    makeup_source_package_sha256: str | None
    makeup_targets: tuple[str, ...]


def build_full_character_creator_witch_slots_repack(
    source_package_path: Path,
    output_path: Path,
    *,
    mappings: tuple[tuple[int, str], ...] = DEFAULT_SLOT_MAPPINGS,
    hair_mappings: tuple[tuple[int, int, str, str], ...] = (
        DEFAULT_WITCH_HAIR_SLOT_MAPPINGS
    ),
    makeup_package_path: Path | None = None,
) -> FullCharacterCreatorRepackResult:
    """从完整 Human Female .cdmod 或 loose 目录生成女巫组合包。"""
    source_package_path = source_package_path.resolve()
    output_path = output_path.resolve()
    normalized_mappings = _validate_mappings(mappings)
    normalized_hair_mappings = _validate_hair_mappings(hair_mappings)
    source_is_directory = source_package_path.is_dir()
    if source_is_directory:
        source_package_sha256 = _hash_loose_source_directory(source_package_path)
        (
            archive_name,
            patched_pamt_bytes,
            patched_paz_bytes,
            entry_summaries,
            source_entry_count,
        ) = _build_loose_source_archive(
            source_package_path,
            normalized_mappings,
            normalized_hair_mappings,
        )
    else:
        source_package_sha256 = hashlib.sha256(source_package_path.read_bytes()).hexdigest()
        package = load_cdmod_package(source_package_path)
        if len(package.standalone_archives) != 1:
            raise ValueError(
                "预期 1 个 standalone archive，"
                f"实际 {len(package.standalone_archives)} 个"
            )
        standalone = package.standalone_archives[0]
        archive_name = standalone.name
        source_entry_count = 0
        entry_summaries = []
        with TemporaryDirectory(prefix="full-human-female-repack-") as temp_dir:
            temp_root = Path(temp_dir)
            pamt_path = temp_root / "0.pamt"
            paz_path = temp_root / "0.paz"
            pamt_path.write_bytes(standalone.pamt_bytes)
            paz_path.write_bytes(standalone.paz_bytes)
            entries = parse_pamt(pamt_path, paz_dir=temp_root)
            source_entry_count = len(entries)
            entries_by_name = {
                Path(entry.path).name.casefold(): entry
                for entry in entries
                if Path(entry.path).name.casefold() in MESH_PARAM_ENTRY_NAMES
            }
            missing = sorted(set(MESH_PARAM_ENTRY_NAMES) - entries_by_name.keys())
            if missing:
                raise ValueError(f"Human Female 缺少玩家脸位表：{missing}")

            patched_paz = bytearray(standalone.paz_bytes)
            for entry_name in MESH_PARAM_ENTRY_NAMES:
                entry = entries_by_name[entry_name]
                plaintext, _detected = extract_plaintext(entry)
                patched_plaintext, patches, hair_patches = _patch_meshparam_plaintext(
                    plaintext,
                    normalized_mappings,
                    normalized_hair_mappings,
                )
                rebuilt_raw, adjusted_comment_byte_count = _build_exact_entry_payload(
                    patched_plaintext,
                    entry_name=entry_name,
                    compression_type=entry.compression_type,
                    encrypted=entry.encrypted,
                    target_comp_size=entry.comp_size,
                    target_orig_size=entry.orig_size,
                )
                payload_end = entry.offset + entry.comp_size
                patched_paz[entry.offset:payload_end] = rebuilt_raw
                entry_summaries.append(
                    FullMeshparamPatchSummary(
                        entry_name=entry_name,
                        patches=patches,
                        hair_patches=hair_patches,
                        original_comp_size=entry.comp_size,
                        rebuilt_comp_size=len(rebuilt_raw),
                        original_plaintext_size=entry.orig_size,
                        adjusted_comment_byte_count=adjusted_comment_byte_count,
                    )
                )

            patched_pamt_bytes = standalone.pamt_bytes
            patched_paz_bytes = bytes(patched_paz)
            paz_path.write_bytes(patched_paz_bytes)
            _verify_repacked_entries(
                pamt_path,
                temp_root,
                normalized_mappings,
                normalized_hair_mappings,
            )

        if len(patched_paz_bytes) != len(standalone.paz_bytes):
            raise ValueError("完整 Human Female PAZ 重打包后长度发生变化")
    archive_pamt_sha256 = hashlib.sha256(patched_pamt_bytes).hexdigest()
    archive_paz_sha256 = hashlib.sha256(patched_paz_bytes).hexdigest()
    makeup_source_package_sha256: str | None = None
    makeup_targets: tuple[str, ...] = ()
    makeup_documents: dict[str, object] = {}
    makeup_component: dict[str, object] | None = None
    if makeup_package_path is not None:
        (
            makeup_source_package_sha256,
            makeup_targets,
            makeup_component,
            makeup_documents,
        ) = _build_makeup_replacement_documents(makeup_package_path)
    archive_document = {
        "schema": 1,
        "name": archive_name,
        "pamt": STANDALONE_PAMT_PATH,
        "paz": STANDALONE_PAZ_PATH,
        "pamt_sha256": archive_pamt_sha256,
        "paz_sha256": archive_paz_sha256,
    }
    mapping_label = "-".join(f"{slot}-{head}" for slot, head in normalized_mappings)
    manifest_components = [
        {
            "type": CDMOD_STANDALONE_COMPONENT_TYPE,
            "path": STANDALONE_ARCHIVE_INDEX_PATH,
        }
    ]
    if makeup_component is not None:
        manifest_components.append(makeup_component)
    manifest_document = {
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": (
            f"full-human-female-witch-faces-hair-{mapping_label}-k-makeup"
            if makeup_targets
            else f"full-human-female-witch-faces-hair-{mapping_label}"
        ),
        "name": (
            f"Full Human Female Witch Faces and Hairstyles {mapping_label} with K-Makeup"
            if makeup_targets
            else f"Full Human Female Witch Faces and Hairstyles {mapping_label}"
        ),
        "version": "1.16.04" if source_is_directory else (
            "1.6-test" if makeup_targets else "1.5-test"
        ),
        "author": (
            "cdmm diagnostic; K-Makeup textures by maru12259"
            if makeup_targets
            else "cdmm diagnostic"
        ),
        "description": (
            "Rebuilds the complete Human Female standalone with Damian and Kliff "
            "meshparam face slots mapped to native witch head identities and swaps "
        "the matching native witch hairstyles into slots 2, 3, 4, 5, and 7."
        ),
        "dependencies": [],
        "source": {
            "format": (
                "loose-directory-full-standalone-rebuild"
                if source_is_directory
                else "full-standalone-exact-size-existing-comment-repack"
            ),
            "source_package_sha256": source_package_sha256,
            "makeup_package_sha256": makeup_source_package_sha256,
        },
        "components": manifest_components,
    }
    report_document = {
        "schema": 1,
        "mappings": [
            {"xml_index": slot_index, "head_id": head_id}
            for slot_index, head_id in normalized_mappings
        ],
        "hair_mappings": [
            {
                "target_index": target_index,
                "source_index": source_index,
                "witch_name": witch_name,
                "witch_hair": witch_hair,
            }
            for target_index, source_index, witch_name, witch_hair in normalized_hair_mappings
        ],
        "entries": [asdict(entry) for entry in entry_summaries],
        "makeup": {
            "source_package_sha256": makeup_source_package_sha256,
            "targets": list(makeup_targets),
            "target_count": len(makeup_targets),
        },
        "safety": {
            "complete_source_archive_preserved": not source_is_directory,
            "complete_loose_source_rebuilt": source_is_directory,
            "source_entry_count": source_entry_count,
            "pamt_unchanged": not source_is_directory,
            "paz_length_unchanged": not source_is_directory,
            "utf8_xml_validated": True,
            "patched_entry_count": len(entry_summaries),
            "hair_list_count_preserved": True,
            "hair_slots_swapped_not_duplicated": True,
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    package_documents = {
        CDMOD_MANIFEST_PATH: manifest_document,
        STANDALONE_ARCHIVE_INDEX_PATH: archive_document,
        STANDALONE_PAMT_PATH: patched_pamt_bytes,
        STANDALONE_PAZ_PATH: patched_paz_bytes,
        CDMOD_REPORT_PATH: report_document,
    }
    package_documents.update(makeup_documents)
    _write_cdmod_zip(output_path, package_documents)
    return FullCharacterCreatorRepackResult(
        output_path=output_path,
        package_sha256=hashlib.sha256(output_path.read_bytes()).hexdigest(),
        source_package_sha256=source_package_sha256,
        archive_pamt_sha256=archive_pamt_sha256,
        archive_paz_sha256=archive_paz_sha256,
        mappings=normalized_mappings,
        hair_mappings=normalized_hair_mappings,
        entries=tuple(entry_summaries),
        makeup_source_package_sha256=makeup_source_package_sha256,
        makeup_targets=makeup_targets,
    )


def _hash_loose_source_directory(source_root: Path) -> str:
    """按相对路径和文件内容计算稳定的 loose 目录指纹。"""
    digest = hashlib.sha256()
    files = sorted(path for path in source_root.rglob("*") if path.is_file())
    if not files:
        raise ValueError(f"Human Female loose 目录为空：{source_root}")
    for path in files:
        relative_path = path.relative_to(source_root).as_posix()
        content = path.read_bytes()
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(struct.pack("<Q", len(content)))
        digest.update(content)
    return digest.hexdigest()


def _collect_loose_source_entries(source_root: Path) -> dict[str, bytes]:
    """收集数字目录下资源，并转换为最终游戏路径。"""
    entries: dict[str, bytes] = {}
    ignored_files: list[str] = []
    for path in sorted(item for item in source_root.rglob("*") if item.is_file()):
        parts = path.relative_to(source_root).parts
        if len(parts) < 2 or not re.fullmatch(r"\d{4}", parts[0]):
            ignored_files.append(path.relative_to(source_root).as_posix())
            continue
        entry_path = "/".join(parts[1:])
        key = entry_path.casefold()
        if key in entries:
            raise ValueError(f"loose 目录存在重复最终路径：{entry_path}")
        entries[key] = path.read_bytes()
    if ignored_files:
        raise ValueError(f"loose 目录包含无法归档的文件：{ignored_files[:5]}")
    if not entries:
        raise ValueError("loose 目录未找到 NNNN/游戏路径 资源")
    return entries


def _patch_meshparam_plaintext(
    plaintext: bytes,
    mappings: tuple[tuple[int, str], ...],
    hair_mappings: tuple[tuple[int, int, str, str], ...],
    *,
    require_equal_length: bool = True,
) -> tuple[bytes, tuple[HeadSlotPatch, ...], tuple[HairSlotSwapPatch, ...]]:
    """在一份 meshparam 内同步应用脸位和发型映射。"""
    patched_plaintext = plaintext
    patches: list[HeadSlotPatch] = []
    for slot_index, head_id in mappings:
        patched_plaintext, patch = patch_head_meshset_xml(
            patched_plaintext,
            xml_index=slot_index,
            new_head_id=head_id,
            require_equal_length=require_equal_length,
        )
        patches.append(patch)
    patched_plaintext, hair_patches = patch_hair_meshset_slots_xml(
        patched_plaintext,
        mappings=hair_mappings,
    )
    _validate_utf8_xml(patched_plaintext)
    return patched_plaintext, tuple(patches), hair_patches


def _build_loose_source_archive(
    source_root: Path,
    mappings: tuple[tuple[int, str], ...],
    hair_mappings: tuple[tuple[int, int, str, str], ...],
) -> tuple[str, bytes, bytes, list[FullMeshparamPatchSummary], int]:
    """把新版完整 loose 目录确定性重建为单 PAZ standalone。"""
    source_entries = _collect_loose_source_entries(source_root)
    meshparam_paths = {
        Path(entry_path).name.casefold(): entry_path
        for entry_path in source_entries
        if Path(entry_path).name.casefold() in MESH_PARAM_ENTRY_NAMES
    }
    missing = sorted(set(MESH_PARAM_ENTRY_NAMES) - meshparam_paths.keys())
    if missing:
        raise ValueError(f"Human Female loose 目录缺少玩家脸位表：{missing}")

    summaries: list[FullMeshparamPatchSummary] = []
    for entry_name in MESH_PARAM_ENTRY_NAMES:
        entry_path = meshparam_paths[entry_name]
        original = source_entries[entry_path]
        patched, patches, hair_patches = _patch_meshparam_plaintext(
            original,
            mappings,
            hair_mappings,
        )
        source_entries[entry_path] = patched
        summaries.append(
            FullMeshparamPatchSummary(
                entry_name=entry_name,
                patches=patches,
                hair_patches=hair_patches,
                original_comp_size=len(original),
                rebuilt_comp_size=0,
                original_plaintext_size=len(original),
                adjusted_comment_byte_count=0,
            )
        )

    paz_buffer = bytearray()
    built_entries: list[BuiltOverlayEntry] = []
    rebuilt_sizes: dict[str, int] = {}
    for entry_path in sorted(source_entries):
        content = source_entries[entry_path]
        filename = entry_path.rsplit("/", 1)[-1]
        dir_path = entry_path.rsplit("/", 1)[0] if "/" in entry_path else ""
        payload, flags = _pack_loose_standalone_payload(content, filename)
        paz_offset = len(paz_buffer)
        paz_buffer.extend(payload)
        padding = (PAZ_ALIGNMENT - len(paz_buffer) % PAZ_ALIGNMENT) % PAZ_ALIGNMENT
        if padding:
            paz_buffer.extend(b"\x00" * padding)
        built_entries.append(
            BuiltOverlayEntry(
                entry_path=entry_path,
                dir_path=dir_path,
                filename=filename,
                paz_offset=paz_offset,
                comp_size=len(payload),
                decomp_size=len(content),
                flags=flags,
                content=content,
            )
        )
        rebuilt_sizes[filename.casefold()] = len(payload)

    paz_bytes = bytes(paz_buffer)
    pamt_buffer = bytearray(_build_multi_pamt(built_entries, len(paz_bytes)))
    struct.pack_into("<I", pamt_buffer, 16, hashlittle(paz_bytes, HASH_SEED))
    struct.pack_into(
        "<I",
        pamt_buffer,
        0,
        hashlittle(bytes(pamt_buffer[12:]), HASH_SEED),
    )
    pamt_bytes = bytes(pamt_buffer)
    summaries = [
        FullMeshparamPatchSummary(
            entry_name=summary.entry_name,
            patches=summary.patches,
            hair_patches=summary.hair_patches,
            original_comp_size=summary.original_comp_size,
            rebuilt_comp_size=rebuilt_sizes[summary.entry_name],
            original_plaintext_size=summary.original_plaintext_size,
            adjusted_comment_byte_count=summary.adjusted_comment_byte_count,
        )
        for summary in summaries
    ]

    with TemporaryDirectory(prefix="full-human-female-loose-verify-") as temp_dir:
        temp_root = Path(temp_dir)
        pamt_path = temp_root / "0.pamt"
        (temp_root / "0.paz").write_bytes(paz_bytes)
        pamt_path.write_bytes(pamt_bytes)
        _verify_repacked_entries(pamt_path, temp_root, mappings, hair_mappings)
        reparsed = parse_pamt(pamt_path, paz_dir=temp_root)
        reparsed_paths = {
            f"{entry.resolved_dir_path}/{Path(entry.path).name}".strip("/").casefold()
            for entry in reparsed
        }
        if reparsed_paths != set(source_entries):
            missing_paths = sorted(set(source_entries) - reparsed_paths)
            extra_paths = sorted(reparsed_paths - set(source_entries))
            raise ValueError(
                "loose standalone 回读路径不一致："
                f"missing={missing_paths[:5]}, extra={extra_paths[:5]}"
            )

    return (
        LOOSE_STANDALONE_ARCHIVE_NAME,
        pamt_bytes,
        paz_bytes,
        summaries,
        len(source_entries),
    )


def _pack_loose_standalone_payload(content: bytes, filename: str) -> tuple[bytes, int]:
    """按 Human Female 原包规则打包一个 loose 资源。"""
    lower_name = filename.casefold()
    encrypted = lower_name.endswith(ENCRYPTED_TEXT_SUFFIXES)
    flags = 0
    payload = content
    if encrypted:
        compressed = lz4_compress(content)
        if len(compressed) < len(content):
            payload = compressed
            flags = 2
        payload = encrypt(payload, filename)
        flags = (flags & 0x0F) | 0x30
    return payload, flags


def _build_makeup_replacement_documents(
    makeup_package_path: Path,
) -> tuple[str, tuple[str, ...], dict[str, object], dict[str, object]]:
    """读取 K-Makeup 两张 DDS，并扩展为五女巫实际材质目标。"""
    makeup_package_path = makeup_package_path.resolve()
    package_sha256 = hashlib.sha256(makeup_package_path.read_bytes()).hexdigest()
    package = load_cdmod_package(makeup_package_path)
    replacements = [
        replacement
        for file_patch in package.file_patches
        for replacement in file_patch.files
    ]
    replacements_by_target = {
        replacement.target.casefold(): replacement
        for replacement in replacements
    }
    required_sources = {
        MAKEUP_DIFFUSE_SOURCE_PATH.casefold(),
        MAKEUP_NORMAL_SOURCE_PATH.casefold(),
    }
    missing = sorted(required_sources - replacements_by_target.keys())
    if missing:
        raise ValueError(f"K-Makeup 缺少预期 DDS：{missing}")
    diffuse = replacements_by_target[MAKEUP_DIFFUSE_SOURCE_PATH.casefold()]
    normal = replacements_by_target[MAKEUP_NORMAL_SOURCE_PATH.casefold()]
    payload_documents = {
        "assets/makeup/k_makeup_diffuse.dds": diffuse.content,
        "assets/makeup/k_makeup_normal.dds": normal.content,
    }
    file_items: list[dict[str, object]] = []
    for target in MAKEUP_TEXTURE_TARGETS:
        source = normal if target.casefold().endswith("_n.dds") else diffuse
        payload_path = (
            "assets/makeup/k_makeup_normal.dds"
            if source is normal
            else "assets/makeup/k_makeup_diffuse.dds"
        )
        file_items.append(
            {
                "target": target,
                "pamt_dir": MAKEUP_PAMT_DIR,
                "payload": payload_path,
                "sha256": hashlib.sha256(source.content).hexdigest(),
                "size": len(source.content),
            }
        )
    replacement_document = {
        "schema": 1,
        "files": file_items,
    }
    documents: dict[str, object] = {
        MAKEUP_REPLACEMENT_INDEX_PATH: replacement_document,
        **payload_documents,
    }
    component = {
        "type": CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE,
        "path": MAKEUP_REPLACEMENT_INDEX_PATH,
        "file_count": len(file_items),
    }
    return package_sha256, MAKEUP_TEXTURE_TARGETS, component, documents


def _build_exact_entry_payload(
    plaintext: bytes,
    *,
    entry_name: str,
    compression_type: int,
    encrypted: bool,
    target_comp_size: int,
    target_orig_size: int,
) -> tuple[bytes, int]:
    """生成尺寸与原 PAMT 完全一致的合法 XML entry 载荷。"""
    if len(plaintext) != target_orig_size:
        raise ValueError(
            f"{entry_name} 明文长度变化：{len(plaintext)} != {target_orig_size}"
        )
    adjusted_plaintext = plaintext
    inserted_comment_count = 0
    if compression_type == 2:
        adjusted_plaintext, inserted_comment_count = (
            _match_xml_compressed_size_safely(
                plaintext,
                target_comp_size=target_comp_size,
            )
        )
        payload = lz4.block.compress(adjusted_plaintext, store_size=False)
    elif compression_type == 0:
        payload = plaintext
    else:
        raise ValueError(f"{entry_name} 暂不支持压缩类型 {compression_type}")
    if len(payload) != target_comp_size:
        raise ValueError(
            f"{entry_name} 载荷长度不匹配：{len(payload)} != {target_comp_size}"
        )
    if encrypted:
        payload = encrypt(payload, entry_name)
    return payload, inserted_comment_count


def _match_xml_compressed_size_safely(
    plaintext: bytes,
    *,
    target_comp_size: int,
) -> tuple[bytes, int]:
    """只扰动原有注释的 ASCII 文本，安全匹配目标压缩长度。"""
    base_size = len(lz4.block.compress(plaintext, store_size=False))
    if base_size == target_comp_size:
        return plaintext, 0

    safe_positions = _find_existing_comment_ascii_positions(plaintext)
    if not safe_positions:
        raise ValueError("XML 原有注释中没有可安全调整的 ASCII 文本")
    baseline = bytearray(plaintext)
    if base_size > target_comp_size:
        for position in safe_positions:
            baseline[position] = ord("A")
        baseline_bytes = bytes(baseline)
        baseline_size = len(lz4.block.compress(baseline_bytes, store_size=False))
        if baseline_size > target_comp_size:
            raise ValueError(
                f"修改后 LZ4 长度 {base_size} 超过原槽位 {target_comp_size}，"
                f"注释规范化后仍为 {baseline_size}"
            )
        if baseline_size == target_comp_size:
            _validate_utf8_xml(baseline_bytes)
            return baseline_bytes, _count_adjusted_comment_bytes(
                plaintext,
                baseline_bytes,
                safe_positions,
            )
    for seed in range(MAX_SAFE_XML_FILL_SEEDS):
        safe_fill = _build_deterministic_safe_fill(len(safe_positions), seed=seed)
        candidate = bytearray(baseline)
        for position, value in zip(safe_positions, safe_fill, strict=True):
            candidate[position] = value
            candidate_bytes = bytes(candidate)
            if len(lz4.block.compress(candidate_bytes, store_size=False)) != target_comp_size:
                continue
            _validate_utf8_xml(candidate_bytes)
            if candidate_bytes.count(b"<!--") != plaintext.count(b"<!--"):
                raise ValueError("安全压缩调整意外改变了 XML 注释节点数量")
            return candidate_bytes, _count_adjusted_comment_bytes(
                plaintext,
                candidate_bytes,
                safe_positions,
            )
    raise ValueError(
        f"无法在保持 UTF-8/XML 合法的前提下匹配 LZ4 长度 {target_comp_size}"
    )


def _count_adjusted_comment_bytes(
    original: bytes,
    adjusted: bytes,
    safe_positions: list[int],
) -> int:
    """统计最终实际变化的注释 ASCII 字节数。"""
    return sum(original[position] != adjusted[position] for position in safe_positions)


def _find_existing_comment_ascii_positions(plaintext: bytes) -> list[int]:
    """收集原有 XML 注释体内完整的可打印 ASCII 字节位置。"""
    positions: list[int] = []
    search_offset = 0
    while True:
        comment_start = plaintext.find(b"<!--", search_offset)
        if comment_start < 0:
            break
        body_start = comment_start + 4
        comment_end = plaintext.find(b"-->", body_start)
        if comment_end < 0:
            raise ValueError("XML 存在未闭合注释")
        positions.extend(
            position
            for position in range(body_start, comment_end)
            if 0x20 <= plaintext[position] <= 0x7E
        )
        search_offset = comment_end + 3
    return positions


def _build_deterministic_safe_fill(length: int, *, seed: int) -> bytes:
    """用 SHA-256 生成稳定的 XML 注释安全 ASCII 填充。"""
    result = bytearray()
    counter = 0
    while len(result) < length:
        digest = hashlib.sha256(f"{seed}:{counter}".encode("ascii")).digest()
        result.extend(
            SAFE_XML_COMMENT_ALPHABET[value % len(SAFE_XML_COMMENT_ALPHABET)]
            for value in digest
        )
        counter += 1
    return bytes(result[:length])


def _validate_utf8_xml(plaintext: bytes) -> None:
    """严格验证调整后的明文仍是 UTF-8 且 XML 可解析。"""
    text = plaintext.decode("utf-8-sig")
    ET.fromstring(text.rstrip("\x00"))


def patch_hair_meshset_slots_xml(
    xml_bytes: bytes,
    *,
    mappings: tuple[tuple[int, int, str, str], ...],
) -> tuple[bytes, tuple[HairSlotSwapPatch, ...]]:
    """成对交换女巫发型与目标发型位，完整保留原发型列表。"""
    normalized_mappings = _validate_hair_mappings(mappings)
    text = xml_bytes.decode("utf-8-sig")
    hair_match = re.search(
        rf'<ParamDesc\s+Index="{HAIR_PARAM_INDEX}"(?=\s|>)[\s\S]*?</ParamDesc>',
        text,
        re.IGNORECASE,
    )
    if hair_match is None:
        raise ValueError("未找到 Human Female 发型 ParamDesc")
    hair_section = hair_match.group(0)
    block_matches = list(
        re.finditer(
            r'<MeshSet\s+Index="(\d+)"(?=\s|>)[\s\S]*?</MeshSet>',
            hair_section,
            re.IGNORECASE,
        )
    )
    blocks_by_index: dict[int, re.Match[str]] = {}
    for block_match in block_matches:
        block_index = int(block_match.group(1))
        if block_index in blocks_by_index:
            raise ValueError(f"发型 XML Index={block_index} 出现重复 MeshSet")
        blocks_by_index[block_index] = block_match

    replacements: list[tuple[int, int, str]] = []
    patches: list[HairSlotSwapPatch] = []
    for target_index, source_index, witch_name, witch_hair in normalized_mappings:
        target_match = blocks_by_index.get(target_index)
        source_match = blocks_by_index.get(source_index)
        if target_match is None or source_match is None:
            raise ValueError(
                f"发型映射缺少 MeshSet：target={target_index}, source={source_index}"
            )
        target_block = target_match.group(0)
        source_block = source_match.group(0)
        displaced_hair = _extract_hair_mesh_name(target_block, target_index)
        source_hair = _extract_hair_mesh_name(source_block, source_index)
        if source_hair.casefold() != witch_hair.casefold():
            raise ValueError(
                f"{witch_name} 发型源 Index={source_index} 异常："
                f"{source_hair} != {witch_hair}"
            )

        replacements.append(
            (
                target_match.start(),
                target_match.end(),
                _replace_meshset_index(source_block, target_index),
            )
        )
        replacements.append(
            (
                source_match.start(),
                source_match.end(),
                _replace_meshset_index(target_block, source_index),
            )
        )
        patches.append(
            HairSlotSwapPatch(
                target_index=target_index,
                source_index=source_index,
                witch_name=witch_name,
                witch_hair=witch_hair,
                displaced_hair=displaced_hair,
            )
        )

    patched_hair_section = hair_section
    for start, end, replacement in sorted(replacements, reverse=True):
        patched_hair_section = (
            patched_hair_section[:start]
            + replacement
            + patched_hair_section[end:]
        )
    if len(patched_hair_section.encode("utf-8")) != len(hair_section.encode("utf-8")):
        raise ValueError("发型成对交换后参数段长度发生变化")
    if patched_hair_section.count("<MeshSet") != hair_section.count("<MeshSet"):
        raise ValueError("发型成对交换后 MeshSet 数量发生变化")

    patched_text = (
        text[:hair_match.start()]
        + patched_hair_section
        + text[hair_match.end():]
    )
    patched_bytes = patched_text.encode("utf-8-sig")
    if len(patched_bytes) != len(xml_bytes):
        raise ValueError("发型成对交换后 meshparam XML 总长度发生变化")
    return patched_bytes, tuple(patches)


def _extract_hair_mesh_name(block: str, slot_index: int) -> str:
    """从一个 Hair MeshSet 提取唯一 MeshFileName。"""
    matches = re.findall(r'MeshFileName="([^"]+)"', block, re.IGNORECASE)
    if len(matches) != 1:
        raise ValueError(f"发型 XML Index={slot_index} MeshFileName 数量异常：{len(matches)}")
    return matches[0]


def _replace_meshset_index(block: str, new_index: int) -> str:
    """只替换 MeshSet 起始标签的 Index，不改动发型资源内容。"""
    patched, count = re.subn(
        r'(<MeshSet\s+Index=")\d+("(?=\s|>))',
        lambda match: f"{match.group(1)}{new_index}{match.group(2)}",
        block,
        count=1,
        flags=re.IGNORECASE,
    )
    if count != 1:
        raise ValueError("发型 MeshSet 起始 Index 替换失败")
    return patched


def _verify_repacked_entries(
    pamt_path: Path,
    paz_dir: Path,
    mappings: tuple[tuple[int, str], ...],
    hair_mappings: tuple[tuple[int, int, str, str], ...],
) -> None:
    """重读完整 PAZ，确认 Damian/Kliff 的目标脸位与发型位。"""
    entries = parse_pamt(pamt_path, paz_dir=paz_dir)
    entries_by_name = {Path(entry.path).name.casefold(): entry for entry in entries}
    for entry_name in MESH_PARAM_ENTRY_NAMES:
        plaintext, _detected = extract_plaintext(entries_by_name[entry_name])
        text = plaintext.decode("utf-8-sig", errors="ignore")
        head_start = text.find('<ParamDesc Index="1" Default = "0">')
        head_end = text.find("<ParamDesc", head_start + 1)
        if head_start < 0 or head_end < 0:
            raise ValueError(f"{entry_name} 重读后缺少头部参数段")
        head_section = text[head_start:head_end]
        for slot_index, head_id in mappings:
            block_match = re.search(
                rf'<MeshSet\s+Index="{slot_index}"(?=\s|>)[\s\S]*?</MeshSet>',
                head_section,
                re.IGNORECASE,
            )
            if block_match is None or block_match.group(0).count(head_id) < 2:
                raise ValueError(f"{entry_name} 槽位 {slot_index} 重读验证失败")
        hair_match = re.search(
            rf'<ParamDesc\s+Index="{HAIR_PARAM_INDEX}"(?=\s|>)[\s\S]*?</ParamDesc>',
            text,
            re.IGNORECASE,
        )
        if hair_match is None:
            raise ValueError(f"{entry_name} 重读后缺少发型参数段")
        hair_section = hair_match.group(0)
        for target_index, _source_index, witch_name, witch_hair in hair_mappings:
            block_match = re.search(
                rf'<MeshSet\s+Index="{target_index}"(?=\s|>)[\s\S]*?</MeshSet>',
                hair_section,
                re.IGNORECASE,
            )
            if block_match is None:
                raise ValueError(f"{entry_name} 发型位 {target_index} 重读验证失败")
            actual_hair = _extract_hair_mesh_name(block_match.group(0), target_index)
            if actual_hair.casefold() != witch_hair.casefold():
                raise ValueError(
                    f"{entry_name} {witch_name} 发型位 {target_index} 重读异常："
                    f"{actual_hair} != {witch_hair}"
                )


def _validate_mappings(
    mappings: tuple[tuple[int, str], ...],
) -> tuple[tuple[int, str], ...]:
    """校验并规范化脸位到四位女巫头部 ID 的映射。"""
    if not mappings:
        raise ValueError("至少提供一个脸位映射")
    slots = [slot for slot, _head_id in mappings]
    if len(slots) != len(set(slots)):
        raise ValueError("脸位映射不能重复")
    for slot_index, head_id in mappings:
        if slot_index < 0:
            raise ValueError("脸位 Index 不能为负数")
        if not re.fullmatch(r"\d{4}", head_id):
            raise ValueError("女巫头部 ID 必须是四位数字")
    return tuple(sorted(mappings))


def _validate_hair_mappings(
    mappings: tuple[tuple[int, int, str, str], ...],
) -> tuple[tuple[int, int, str, str], ...]:
    """校验目标发型位、原生来源位和资源名不会互相覆盖。"""
    if not mappings:
        raise ValueError("至少提供一个女巫发型映射")
    target_indexes = [target for target, _source, _name, _hair in mappings]
    source_indexes = [source for _target, source, _name, _hair in mappings]
    if len(target_indexes) != len(set(target_indexes)):
        raise ValueError("女巫目标发型位不能重复")
    if len(source_indexes) != len(set(source_indexes)):
        raise ValueError("女巫来源发型位不能重复")
    overlap = sorted(set(target_indexes) & set(source_indexes))
    if overlap:
        raise ValueError(f"目标发型位与来源发型位不能重叠：{overlap}")
    for target_index, source_index, witch_name, witch_hair in mappings:
        if target_index < 0 or source_index < 0:
            raise ValueError("发型 Index 不能为负数")
        if not witch_name.strip():
            raise ValueError("女巫名称不能为空")
        if not re.fullmatch(r"cd_phw_00_hair_00_\d{4}(?:_\d{2})?", witch_hair):
            raise ValueError(f"女巫发型资源名格式异常：{witch_hair}")
    return tuple(sorted(mappings))


def _parse_mappings(value: str) -> tuple[tuple[int, str], ...]:
    """解析 ``Index:头部ID`` 逗号分隔参数。"""
    try:
        items = []
        for raw_item in value.split(","):
            slot_text, head_id = raw_item.strip().split(":", 1)
            items.append((int(slot_text), head_id.strip()))
        return _validate_mappings(tuple(items))
    except (ValueError, TypeError) as exc:
        raise argparse.ArgumentTypeError("--mappings 格式应为 2:0139,3:0143") from exc


def result_to_json(result: FullCharacterCreatorRepackResult) -> dict[str, object]:
    """把生成结果转换为命令行 JSON。"""
    payload = asdict(result)
    payload["output_path"] = str(result.output_path)
    return payload


def main() -> int:
    """解析原包、输出包和脸位映射。"""
    parser = argparse.ArgumentParser(description="重打包完整 Human Female 女巫脸位")
    parser.add_argument(
        "source",
        type=Path,
        help="作者原 Human Female .cdmod 或新版 NNNN loose 目录",
    )
    parser.add_argument("output", type=Path, help="输出完整替代 .cdmod")
    parser.add_argument(
        "--mappings",
        type=_parse_mappings,
        default=DEFAULT_SLOT_MAPPINGS,
        help="逗号分隔的 Index:头部ID，默认五女巫完整映射",
    )
    parser.add_argument(
        "--makeup-package",
        type=Path,
        help="可选 K-Makeup .cdmod；提供后把 0027 妆容嵌入五女巫实际材质路径",
    )
    args = parser.parse_args()
    result = build_full_character_creator_witch_slots_repack(
        args.source,
        args.output,
        mappings=args.mappings,
        makeup_package_path=args.makeup_package,
    )
    print(json.dumps(result_to_json(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
