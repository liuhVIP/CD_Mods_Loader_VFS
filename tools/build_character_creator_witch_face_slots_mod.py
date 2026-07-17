"""把五个女巫的原生头部资源映射到角色创建器第 2 至第 6 号脸。

本工具从游戏原版 ``0009`` 读取女巫头部资源，按目标脸位的 PAMT 路径重新
打包为 standalone ``.cdmod``。加密 ``pac_xml`` / ``prefabdata_xml`` 仅在
生成工具内部显式处理，不要求加载器扩大通用加密后缀识别范围。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterable

from cdmm.archive.pamt import parse_pamt, parse_pamt_filtered
from cdmm.archive.paz_crypto import decrypt, lz4_decompress
from cdmm.common.models import OverlayInputEntry, PazEntry
from cdmm.services.cdmod_converter import (
    CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE,
    CDMOD_FORMAT_NAME,
    CDMOD_FORMAT_VERSION,
    CDMOD_MANIFEST_PATH,
    CDMOD_REPORT_PATH,
    CDMOD_RESOURCE_TRANSFORM_COMPONENT_TYPE,
    CDMOD_STANDALONE_COMPONENT_TYPE,
    _write_cdmod_zip,
)
from cdmm.services.cdmod_package import load_cdmod_package
from cdmm.services.json_loader import extract_plaintext
from cdmm.services.overlay_service import build_overlay

# 五个女巫头部到角色创建器脸位的固定研究映射。
WITCH_FACE_SLOTS = (
    (2, "0002", "Areciel", "0139", "Damiane to Areciel"),
    (3, "0003", "Bari", "0143", "Damiane to Bari"),
    (4, "0004", "Elowen", "0141", "Damiane to Elowen"),
    (5, "0005", "Lyselia", "0019", "Damiane To Lyselia"),
    (6, "0006", "White Crow", "0046", "Damiane To White Crow"),
)

# 五个目标脸位的基础色/法线纹理映射，来源由各女巫 PAC XML 实际引用确定。
WITCH_FACE_TEXTURE_PATHS = {
    2: (
        "character/texture/cd_phw_00_head_00_0139.dds",
        "character/texture/cd_phw_00_head_00_0139_n.dds",
        "character/texture/cd_phw_00_head_npc_0001.dds",
        "character/texture/cd_phw_00_head_npc_0001_n.dds",
    ),
    3: (
        "character/texture/cd_phw_00_head_00_0141.dds",
        "character/texture/cd_phw_00_head_00_0143_n.dds",
        "character/texture/cd_phw_00_head_npc_0002.dds",
        "character/texture/cd_phw_00_head_npc_0002_n.dds",
    ),
    4: (
        "character/texture/cd_phw_00_head_00_0141.dds",
        "character/texture/cd_phw_00_head_00_0141_n.dds",
        "character/texture/cd_phw_00_head_npc_0003.dds",
        "character/texture/cd_phw_00_head_npc_0003_n.dds",
    ),
    5: (
        "character/texture/cd_phw_00_head_base_youth_0019.dds",
        "character/texture/cd_phw_00_head_base_youth_0019_n.dds",
        "character/texture/cd_phw_00_head_0001.dds",
        "character/texture/cd_phw_00_head_0001_n.dds",
    ),
    6: (
        "character/texture/cd_phw_00_head_00_0046.dds",
        "character/texture/cd_phw_00_head_00_0046_n.dds",
        "character/texture/cd_phw_00_head_base_youth_0006.dds",
        "character/texture/cd_phw_00_head_base_youth_0006_n.dds",
    ),
}

# 每个头部必须完整覆盖的资源后缀。
HEAD_RESOURCE_SUFFIXES = (
    "hkx",
    "prefab",
    "pabc",
    "pac",
    "pac_xml",
    "prefabdata_xml",
)

# 女巫与目标脸位资源都位于原版 0009 包。
HEAD_PAMT_DIR = "0009"

# standalone 组件在 cdmod 内的固定路径。
STANDALONE_ARCHIVE_INDEX_PATH = "archives/000/archive.json"
STANDALONE_PAMT_PATH = "archives/000/0.pamt"
STANDALONE_PAZ_PATH = "archives/000/0.paz"

# DDS file-replacement 组件固定路径。
TEXTURE_REPLACEMENT_INDEX_PATH = "files/replacements.json"

# 六件头部资源 copy-entry 组件固定路径。
HEAD_RESOURCE_TRANSFORM_INDEX_PATH = "patches/head-remap.json"

# 下划线 XML 在 PAZ 中仍使用按文件名派生密钥的 ChaCha20。
ENCRYPTED_HEAD_XML_SUFFIX = "_xml"


@dataclass(frozen=True)
class WitchFaceSlot:
    """一个女巫头部到角色创建器脸位的映射。"""

    slot_number: int
    target_head_id: str
    witch_name: str
    source_head_id: str
    source_mod_name: str


@dataclass(frozen=True)
class WitchFaceEntrySummary:
    """一个已打包头部资源的审计摘要。"""

    witch_name: str
    slot_number: int
    source_path: str
    target_path: str
    plaintext_size: int
    plaintext_sha256: str
    encrypted: bool
    compression_type: int
    rebased_identity_count: int


@dataclass(frozen=True)
class WitchFaceSlotsBuildResult:
    """女巫脸位模组生成结果。"""

    output_path: Path
    package_sha256: str
    archive_paz_sha256: str
    archive_pamt_sha256: str
    slots: tuple[WitchFaceSlot, ...]
    entries: tuple[WitchFaceEntrySummary, ...]


@dataclass(frozen=True)
class WitchFaceTextureSummary:
    """一个女巫 DDS 到目标脸位 DDS 的映射摘要。"""

    witch_name: str
    slot_number: int
    source_path: str
    target_path: str
    payload_size: int
    payload_sha256: str


@dataclass(frozen=True)
class WitchFaceTextureBuildResult:
    """纯 DDS 女巫脸位探针生成结果。"""

    output_path: Path
    package_sha256: str
    slots: tuple[WitchFaceSlot, ...]
    textures: tuple[WitchFaceTextureSummary, ...]


@dataclass(frozen=True)
class WitchFaceHybridBuildResult:
    """脸型资源与 DDS 同时进入 nppsa 的混合探针结果。"""

    output_path: Path
    package_sha256: str
    slots: tuple[WitchFaceSlot, ...]
    head_operation_count: int
    textures: tuple[WitchFaceTextureSummary, ...]


def select_witch_face_slots(slot_numbers: Iterable[int]) -> tuple[WitchFaceSlot, ...]:
    """按脸位号选择映射，并拒绝重复或越界值。"""
    requested = tuple(slot_numbers)
    if not requested:
        raise ValueError("至少选择一个角色创建器脸位")
    if len(set(requested)) != len(requested):
        raise ValueError("角色创建器脸位不能重复")
    mapping_by_slot = {
        item[0]: WitchFaceSlot(*item)
        for item in WITCH_FACE_SLOTS
    }
    unknown = sorted(set(requested) - mapping_by_slot.keys())
    if unknown:
        raise ValueError(f"仅支持角色创建器第 2 至第 6 号脸：{unknown}")
    return tuple(mapping_by_slot[slot] for slot in sorted(requested))


def extract_head_entry_plaintext(entry: PazEntry) -> bytes:
    """读取头部 entry 明文，并在工具内部显式处理下划线 XML。"""
    if not Path(entry.path).name.casefold().endswith(ENCRYPTED_HEAD_XML_SUFFIX):
        content, _detected = extract_plaintext(entry)
        return content

    raw = _read_entry_raw(entry)
    decrypted = decrypt(raw, Path(entry.path).name)
    if entry.compression_type == 0:
        return decrypted
    if entry.compression_type == 2:
        return lz4_decompress(decrypted, entry.orig_size)
    raise ValueError(
        f"暂不支持的加密头部 XML 压缩类型：{entry.path} type={entry.compression_type}"
    )


def build_witch_face_slots_mod(
    game_dir: Path,
    output_path: Path,
    *,
    slot_numbers: Iterable[int] = (2,),
) -> WitchFaceSlotsBuildResult:
    """从当前游戏原版资源生成指定脸位的女巫头部 standalone cdmod。"""
    game_dir = game_dir.resolve()
    output_path = output_path.resolve()
    slots = select_witch_face_slots(slot_numbers)
    pamt_path = game_dir / HEAD_PAMT_DIR / "0.pamt"
    if not pamt_path.exists():
        raise FileNotFoundError(f"缺少原版头部 PAMT：{pamt_path}")

    source_ids = {slot.source_head_id for slot in slots}
    target_ids = {slot.target_head_id for slot in slots}
    entries_by_name = _load_required_head_entries(
        pamt_path,
        source_ids | target_ids,
    )

    overlay_inputs: list[OverlayInputEntry] = []
    entry_summaries: list[WitchFaceEntrySummary] = []
    expected_plaintext: dict[str, bytes] = {}
    for slot in slots:
        for suffix in HEAD_RESOURCE_SUFFIXES:
            source_name = _head_resource_name(slot.source_head_id, suffix)
            target_name = _head_resource_name(slot.target_head_id, suffix)
            source = entries_by_name[source_name.casefold()]
            target = entries_by_name[target_name.casefold()]
            source_content = extract_head_entry_plaintext(source)
            content, rebased_identity_count = rebase_head_resource_identity(
                source_content,
                suffix=suffix,
                source_head_id=slot.source_head_id,
                target_head_id=slot.target_head_id,
            )
            target_encrypted = target_name.casefold().endswith(ENCRYPTED_HEAD_XML_SUFFIX)
            overlay_inputs.append(
                OverlayInputEntry(
                    content=content,
                    entry_path=target.path,
                    pamt_dir=HEAD_PAMT_DIR,
                    compression_type=target.compression_type,
                    encrypted=target_encrypted,
                    crypto_filename=target_name,
                    resolved_dir_path=target.resolved_dir_path,
                )
            )
            expected_plaintext[target_name.casefold()] = content
            entry_summaries.append(
                WitchFaceEntrySummary(
                    witch_name=slot.witch_name,
                    slot_number=slot.slot_number,
                    source_path=source.path,
                    target_path=target.path,
                    plaintext_size=len(content),
                    plaintext_sha256=hashlib.sha256(content).hexdigest(),
                    encrypted=target_encrypted,
                    compression_type=target.compression_type,
                    rebased_identity_count=rebased_identity_count,
                )
            )

    overlay = build_overlay(HEAD_PAMT_DIR, overlay_inputs, game_dir)
    paz_bytes = bytes(overlay.paz_bytes)
    _verify_built_archive(
        overlay.pamt_bytes,
        paz_bytes,
        expected_plaintext,
    )

    archive_pamt_sha256 = hashlib.sha256(overlay.pamt_bytes).hexdigest()
    archive_paz_sha256 = hashlib.sha256(paz_bytes).hexdigest()
    archive_document = {
        "schema": 1,
        "name": "witch-face-slots-0009",
        "pamt": STANDALONE_PAMT_PATH,
        "paz": STANDALONE_PAZ_PATH,
        "pamt_sha256": archive_pamt_sha256,
        "paz_sha256": archive_paz_sha256,
    }
    manifest_document = {
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": _build_package_id(slots),
        "name": _build_package_name(slots),
        "version": "1.3-test",
        "author": "cdmm research",
        "description": (
            "Rebases native witch head resource identities onto Human Female slots "
            f"{', '.join(str(slot.slot_number) for slot in slots)}."
        ),
        "dependencies": ["n20260707125900"],
        "source": {
            "format": "vanilla-head-entry-remap",
            "pamt_dir": HEAD_PAMT_DIR,
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
        "mappings": [asdict(slot) for slot in slots],
        "entries": [asdict(entry) for entry in entry_summaries],
        "safety": {
            "loader_logic_changed": False,
            "character_creator_xml_changed": False,
            "source_material_references_preserved": True,
            "target_slot_paths_replaced": True,
            "runtime_identity_rebased": True,
            "standalone_archive": True,
        },
        "acceptance": {
            "first_apply": "pending-user-test",
            "main_menu_reload": "pending-user-test",
            "game_restart": "pending-user-test",
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
    loaded = load_cdmod_package(output_path)
    if len(loaded.standalone_archives) != 1:
        raise ValueError("生成后的 cdmod standalone 数量异常")
    return WitchFaceSlotsBuildResult(
        output_path=output_path,
        package_sha256=hashlib.sha256(output_path.read_bytes()).hexdigest(),
        archive_paz_sha256=archive_paz_sha256,
        archive_pamt_sha256=archive_pamt_sha256,
        slots=slots,
        entries=tuple(entry_summaries),
    )


def rebase_head_resource_identity(
    content: bytes,
    *,
    suffix: str,
    source_head_id: str,
    target_head_id: str,
) -> tuple[bytes, int]:
    """把女巫内部资源身份改成目标脸位，同时保留其原生纹理路径。"""
    if len(source_head_id) != len(target_head_id):
        raise ValueError("头部身份重基要求源 ID 与目标 ID 等长")
    source_bytes = source_head_id.encode("ascii")
    target_bytes = target_head_id.encode("ascii")
    if suffix != "pac_xml":
        replacement_count = content.count(source_bytes)
        return content.replace(source_bytes, target_bytes), replacement_count

    encoding = "utf-8-sig" if content.startswith(b"\xef\xbb\xbf") else "utf-8"
    text = content.decode(encoding)
    pattern = re.compile(
        rf'(_subMeshName="[^"]*?){re.escape(source_head_id)}([^"]*")',
        re.IGNORECASE,
    )
    patched_text, replacement_count = pattern.subn(
        rf"\g<1>{target_head_id}\g<2>",
        text,
    )
    patched_content = patched_text.encode(encoding)
    if len(patched_content) != len(content):
        raise ValueError("PAC XML 身份重基后长度发生变化")
    return patched_content, replacement_count


def build_witch_face_texture_mod(
    game_dir: Path,
    output_path: Path,
    *,
    slot_numbers: Iterable[int] = (2,),
) -> WitchFaceTextureBuildResult:
    """按 K-Makeup 形态生成指定脸位的纯 DDS file-replacement cdmod。"""
    game_dir = game_dir.resolve()
    output_path = output_path.resolve()
    slots = select_witch_face_slots(slot_numbers)
    pamt_path = game_dir / HEAD_PAMT_DIR / "0.pamt"
    if not pamt_path.exists():
        raise FileNotFoundError(f"缺少原版头部 PAMT：{pamt_path}")

    texture_summaries, file_items, documents = _collect_texture_payloads(
        pamt_path,
        slots,
    )

    replacement_document = {
        "schema": 1,
        "files": file_items,
    }
    manifest_document = {
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": _build_texture_package_id(slots),
        "name": _build_texture_package_name(slots),
        "version": "0.2-test",
        "author": "cdmm research",
        "description": (
            "Copies native witch diffuse and normal textures onto Human Female "
            f"character creator slots {', '.join(str(slot.slot_number) for slot in slots)}."
        ),
        "dependencies": ["n20260707125900"],
        "source": {
            "format": "vanilla-dds-remap",
            "pamt_dir": HEAD_PAMT_DIR,
        },
        "components": [
            {
                "type": CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE,
                "path": TEXTURE_REPLACEMENT_INDEX_PATH,
                "file_count": len(file_items),
            }
        ],
    }
    report_document = {
        "schema": 1,
        "mappings": [asdict(slot) for slot in slots],
        "textures": [asdict(texture) for texture in texture_summaries],
        "safety": {
            "loader_logic_changed": False,
            "head_mesh_changed": False,
            "dds_pathc": "rebuilt-by-loader",
            "slot_5_uses_shared_head_0001_textures": any(
                slot.slot_number == 5 for slot in slots
            ),
        },
        "limitation": "DDS changes surface appearance only; it does not replace facial geometry.",
    }
    documents.update(
        {
            CDMOD_MANIFEST_PATH: manifest_document,
            TEXTURE_REPLACEMENT_INDEX_PATH: replacement_document,
            CDMOD_REPORT_PATH: report_document,
        }
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_cdmod_zip(output_path, documents)
    loaded = load_cdmod_package(output_path)
    loaded_file_count = sum(len(patch.files) for patch in loaded.file_patches)
    if loaded_file_count != len(file_items):
        raise ValueError("生成后的 DDS file-replacement 数量异常")
    return WitchFaceTextureBuildResult(
        output_path=output_path,
        package_sha256=hashlib.sha256(output_path.read_bytes()).hexdigest(),
        slots=slots,
        textures=tuple(texture_summaries),
    )


def build_witch_face_hybrid_mod(
    game_dir: Path,
    output_path: Path,
    *,
    slot_numbers: Iterable[int] = (2,),
) -> WitchFaceHybridBuildResult:
    """生成头部六件资源 copy-entry 与两张 DDS 同时合成的 nppsa 探针。"""
    game_dir = game_dir.resolve()
    output_path = output_path.resolve()
    slots = select_witch_face_slots(slot_numbers)
    pamt_path = game_dir / HEAD_PAMT_DIR / "0.pamt"
    if not pamt_path.exists():
        raise FileNotFoundError(f"缺少原版头部 PAMT：{pamt_path}")

    head_ids = {
        head_id
        for slot in slots
        for head_id in (slot.source_head_id, slot.target_head_id)
    }
    _load_required_head_entries(pamt_path, head_ids)
    operations = [
        {
            "op": "copy-entry",
            "source": f"character/{_head_resource_name(slot.source_head_id, suffix)}",
            "source_pamt_dir": HEAD_PAMT_DIR,
            "target": f"character/{_head_resource_name(slot.target_head_id, suffix)}",
            "target_pamt_dir": HEAD_PAMT_DIR,
        }
        for slot in slots
        for suffix in HEAD_RESOURCE_SUFFIXES
    ]
    texture_summaries, file_items, documents = _collect_texture_payloads(
        pamt_path,
        slots,
    )
    resource_document = {
        "schema": 1,
        "operations": operations,
    }
    replacement_document = {
        "schema": 1,
        "files": file_items,
    }
    manifest_document = {
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": _build_hybrid_package_id(slots),
        "name": _build_hybrid_package_name(slots),
        "version": "0.3-test",
        "author": "cdmm research",
        "description": (
            "Copies native witch head geometry resources and DDS textures onto Human Female "
            f"character creator slots {', '.join(str(slot.slot_number) for slot in slots)}."
        ),
        "dependencies": ["n20260707125900"],
        "source": {
            "format": "dynamic-head-resource-and-dds-remap",
            "pamt_dir": HEAD_PAMT_DIR,
        },
        "components": [
            {
                "type": CDMOD_RESOURCE_TRANSFORM_COMPONENT_TYPE,
                "path": HEAD_RESOURCE_TRANSFORM_INDEX_PATH,
                "operation_count": len(operations),
            },
            {
                "type": CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE,
                "path": TEXTURE_REPLACEMENT_INDEX_PATH,
                "file_count": len(file_items),
            },
        ],
    }
    report_document = {
        "schema": 1,
        "mappings": [asdict(slot) for slot in slots],
        "head_operation_count": len(operations),
        "textures": [asdict(texture) for texture in texture_summaries],
        "safety": {
            "character_creator_xml_changed": False,
            "black_index_1_placeholder_changed": False,
            "nppsa_composed_overlay": True,
            "dds_pathc": "rebuilt-by-loader",
            "slot_5_uses_shared_head_0001_textures": any(
                slot.slot_number == 5 for slot in slots
            ),
        },
    }
    documents.update(
        {
            CDMOD_MANIFEST_PATH: manifest_document,
            HEAD_RESOURCE_TRANSFORM_INDEX_PATH: resource_document,
            TEXTURE_REPLACEMENT_INDEX_PATH: replacement_document,
            CDMOD_REPORT_PATH: report_document,
        }
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_cdmod_zip(output_path, documents)
    loaded = load_cdmod_package(output_path)
    loaded_operation_count = sum(
        len(patch.operations) for patch in loaded.resource_patches
    )
    loaded_file_count = sum(len(patch.files) for patch in loaded.file_patches)
    if loaded_operation_count != len(operations):
        raise ValueError("生成后的头部 resource-transform 数量异常")
    if loaded_file_count != len(file_items):
        raise ValueError("生成后的 DDS file-replacement 数量异常")
    return WitchFaceHybridBuildResult(
        output_path=output_path,
        package_sha256=hashlib.sha256(output_path.read_bytes()).hexdigest(),
        slots=slots,
        head_operation_count=len(operations),
        textures=tuple(texture_summaries),
    )


def result_to_json(
    result: WitchFaceSlotsBuildResult
    | WitchFaceTextureBuildResult
    | WitchFaceHybridBuildResult,
) -> dict[str, object]:
    """把生成结果转换为命令行 JSON。"""
    payload = asdict(result)
    payload["output_path"] = str(result.output_path)
    return payload


def _load_required_head_entries(
    pamt_path: Path,
    head_ids: set[str],
) -> dict[str, PazEntry]:
    """按 basename 精确读取构建所需的原版头部资源。"""
    required_names = {
        _head_resource_name(head_id, suffix).casefold()
        for head_id in head_ids
        for suffix in HEAD_RESOURCE_SUFFIXES
    }
    entries = parse_pamt_filtered(
        pamt_path,
        paz_dir=pamt_path.parent,
        desired_basenames=required_names,
    )
    entries_by_name: dict[str, PazEntry] = {}
    duplicates: set[str] = set()
    for entry in entries:
        name = Path(entry.path).name.casefold()
        if name in entries_by_name:
            duplicates.add(name)
        entries_by_name[name] = entry
    if duplicates:
        raise ValueError(f"原版 0009 存在重复头部 basename：{sorted(duplicates)}")
    missing = sorted(required_names - entries_by_name.keys())
    if missing:
        raise ValueError(f"原版 0009 缺少头部资源：{missing}")
    return entries_by_name


def _load_required_texture_entries(
    pamt_path: Path,
    required_paths: set[str],
) -> dict[str, PazEntry]:
    """读取纯 DDS 探针需要的唯一原版纹理 entry。"""
    required_names = {Path(path).name.casefold() for path in required_paths}
    entries = parse_pamt_filtered(
        pamt_path,
        paz_dir=pamt_path.parent,
        desired_basenames=required_names,
    )
    entries_by_name: dict[str, PazEntry] = {}
    duplicates: set[str] = set()
    for entry in entries:
        name = Path(entry.path).name.casefold()
        if name in entries_by_name:
            duplicates.add(name)
        entries_by_name[name] = entry
    if duplicates:
        raise ValueError(f"原版 0009 存在重复 DDS basename：{sorted(duplicates)}")
    missing = sorted(required_names - entries_by_name.keys())
    if missing:
        raise ValueError(f"原版 0009 缺少女巫 DDS：{missing}")
    return entries_by_name


def _collect_texture_payloads(
    pamt_path: Path,
    slots: tuple[WitchFaceSlot, ...],
) -> tuple[
    list[WitchFaceTextureSummary],
    list[dict[str, object]],
    dict[str, object],
]:
    """收集女巫基础色/法线 DDS，并生成 file-replacement 文档载荷。"""
    required_paths = {
        path
        for slot in slots
        for path in WITCH_FACE_TEXTURE_PATHS[slot.slot_number]
    }
    entries_by_name = _load_required_texture_entries(pamt_path, required_paths)
    texture_summaries: list[WitchFaceTextureSummary] = []
    file_items: list[dict[str, object]] = []
    documents: dict[str, object] = {}
    for slot in slots:
        source_diffuse, source_normal, target_diffuse, target_normal = (
            WITCH_FACE_TEXTURE_PATHS[slot.slot_number]
        )
        for source_path, target_path in (
            (source_diffuse, target_diffuse),
            (source_normal, target_normal),
        ):
            source = entries_by_name[Path(source_path).name.casefold()]
            target = entries_by_name[Path(target_path).name.casefold()]
            content, _detected = extract_plaintext(source)
            payload_path = f"assets/{HEAD_PAMT_DIR}/character/texture/{Path(target.path).name}"
            payload_sha256 = hashlib.sha256(content).hexdigest()
            documents[payload_path] = content
            file_items.append(
                {
                    "target": target_path,
                    "pamt_dir": HEAD_PAMT_DIR,
                    "payload": payload_path,
                    "sha256": payload_sha256,
                    "size": len(content),
                }
            )
            texture_summaries.append(
                WitchFaceTextureSummary(
                    witch_name=slot.witch_name,
                    slot_number=slot.slot_number,
                    source_path=source_path,
                    target_path=target_path,
                    payload_size=len(content),
                    payload_sha256=payload_sha256,
                )
            )
    return texture_summaries, file_items, documents


def _verify_built_archive(
    pamt_bytes: bytes,
    paz_bytes: bytes,
    expected_plaintext: dict[str, bytes],
) -> None:
    """重读生成后的 PAZ/PAMT，验证目标名加密与内容完全一致。"""
    with TemporaryDirectory(prefix="witch-face-slots-verify-") as temp_dir:
        archive_dir = Path(temp_dir)
        pamt_path = archive_dir / "0.pamt"
        paz_path = archive_dir / "0.paz"
        pamt_path.write_bytes(pamt_bytes)
        paz_path.write_bytes(paz_bytes)
        entries = parse_pamt(pamt_path, paz_dir=archive_dir)
        entries_by_name = {Path(entry.path).name.casefold(): entry for entry in entries}
        if entries_by_name.keys() != expected_plaintext.keys():
            raise ValueError("生成后的 standalone 目标 entry 集合不一致")
        for name, expected in expected_plaintext.items():
            actual = extract_head_entry_plaintext(entries_by_name[name])
            if actual != expected:
                raise ValueError(f"生成后的 standalone 内容验证失败：{name}")


def _read_entry_raw(entry: PazEntry) -> bytes:
    """按 PAMT offset 读取单个 PAZ entry 原始载荷。"""
    with Path(entry.paz_file).open("rb") as handle:
        handle.seek(entry.offset)
        raw = handle.read(entry.comp_size)
    if len(raw) != entry.comp_size:
        raise ValueError(f"PAZ entry 读取不完整：{entry.path}")
    return raw


def _head_resource_name(head_id: str, suffix: str) -> str:
    """生成一个标准女性头部资源文件名。"""
    return f"cd_phw_00_head_00_{head_id}.{suffix}"


def _build_package_id(slots: tuple[WitchFaceSlot, ...]) -> str:
    """生成按脸位区分的稳定包 ID。"""
    slot_part = "-".join(str(slot.slot_number) for slot in slots)
    return f"character-creator-witch-face-slots-{slot_part}"


def _build_package_name(slots: tuple[WitchFaceSlot, ...]) -> str:
    """生成便于测试识别的包名。"""
    mappings = ", ".join(
        f"Slot {slot.slot_number} {slot.witch_name}"
        for slot in slots
    )
    return f"Character Creator Witch Faces - {mappings}"


def _build_texture_package_id(slots: tuple[WitchFaceSlot, ...]) -> str:
    """生成纯 DDS 探针的稳定包 ID。"""
    slot_part = "-".join(str(slot.slot_number) for slot in slots)
    return f"character-creator-witch-face-textures-{slot_part}"


def _build_texture_package_name(slots: tuple[WitchFaceSlot, ...]) -> str:
    """生成纯 DDS 探针显示名称。"""
    mappings = ", ".join(
        f"Slot {slot.slot_number} {slot.witch_name}"
        for slot in slots
    )
    return f"Character Creator Witch Textures - {mappings}"


def _build_hybrid_package_id(slots: tuple[WitchFaceSlot, ...]) -> str:
    """生成脸型与 DDS 混合探针的稳定包 ID。"""
    slot_part = "-".join(str(slot.slot_number) for slot in slots)
    return f"character-creator-witch-face-hybrid-{slot_part}"


def _build_hybrid_package_name(slots: tuple[WitchFaceSlot, ...]) -> str:
    """生成脸型与 DDS 混合探针显示名称。"""
    mappings = ", ".join(
        f"Slot {slot.slot_number} {slot.witch_name}"
        for slot in slots
    )
    return f"Character Creator Witch Face Hybrid - {mappings}"


def _parse_slot_numbers(value: str) -> tuple[int, ...]:
    """解析逗号分隔脸位参数。"""
    try:
        return tuple(int(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--slots 必须是逗号分隔的整数") from exc


def main() -> int:
    """解析游戏目录、输出包、构建模式和待构建脸位。"""
    parser = argparse.ArgumentParser(description="生成角色创建器脸替换五女巫测试 cdmod")
    parser.add_argument("game_dir", type=Path, help="Crimson Desert 游戏根目录")
    parser.add_argument("output", type=Path, help="输出 .cdmod 路径")
    parser.add_argument(
        "--slots",
        type=_parse_slot_numbers,
        default=(2,),
        help="逗号分隔脸位，支持 2,3,4,5,6；默认仅生成第 2 号脸",
    )
    parser.add_argument(
        "--mode",
        choices=("hybrid", "texture", "standalone"),
        default="standalone",
        help="standalone=完整头部资源；hybrid/texture=单项 DDS 诊断模式",
    )
    args = parser.parse_args()
    if args.mode == "hybrid":
        result = build_witch_face_hybrid_mod(
            args.game_dir,
            args.output,
            slot_numbers=args.slots,
        )
    elif args.mode == "texture":
        result = build_witch_face_texture_mod(
            args.game_dir,
            args.output,
            slot_numbers=args.slots,
        )
    else:
        result = build_witch_face_slots_mod(
            args.game_dir,
            args.output,
            slot_numbers=args.slots,
        )
    print(json.dumps(result_to_json(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
