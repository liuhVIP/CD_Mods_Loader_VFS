"""生成退化飞行披风网格、保留原生动画与 FX 的 ``.cdmod``。

本工具不修改飞行披风 prefab。它读取当前游戏两个男性飞行披风 PAC，逐段解压
内部 PAR/LZ4 几何数据，把所有 LOD 的三角形索引改成零面积三角形，再按原压缩
策略重建 PAC。顶点、骨骼、材质、子网格、计数和 PAC_XML 均保持不变。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from dataclasses import asdict, dataclass
from pathlib import Path

from cdmm.archive.paz_crypto import lz4_compress, lz4_decompress
from cdmm.services.cdmod_converter import (
    CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE,
    CDMOD_FORMAT_NAME,
    CDMOD_FORMAT_VERSION,
    CDMOD_MANIFEST_PATH,
    CDMOD_REPORT_PATH,
    _write_cdmod_zip,
)
from cdmm.services.json_loader import extract_plaintext
from cdmm.services.pamt_index_service import get_game_pamt_index

# PAR 文件头固定长度，包含八个压缩段尺寸槽位。
PAR_HEADER_SIZE = 0x50

# 两个飞行披风 PAC 都位于当前游戏 0009 分包。
TARGET_PAMT_DIR = "0009"

# 完整资源替换组件在 cdmod 内的固定路径。
FILE_REPLACEMENT_PATH = "files/replacements.json"


@dataclass(frozen=True)
class FlightCloakPacSpec:
    """当前游戏 PAC 的严格来源指纹与各几何段索引数量。"""

    target: str
    source_sha256: str
    section_index_counts: tuple[tuple[int, int], ...]


# 这些指纹和索引数量来自 Crimson Desert 1.13.01 当前原版资源。
FLIGHT_CLOAK_PAC_SPECS = (
    FlightCloakPacSpec(
        target="character/cd_phm_00_cloak_0054_14_01.pac",
        source_sha256="f49206f148bf84ac9fe5daecbf3af0960b3ebb7fcb8a33cb51bbcee3d6169bc2",
        section_index_counts=((1, 1107), (2, 41229), (3, 70545), (4, 74154)),
    ),
    FlightCloakPacSpec(
        target="character/cd_phm_00_cloak_0054_14_02.pac",
        source_sha256="de54e986f3a5ea715273048552af657d9d291f973d4d5a3a95e3ada141dc6a53",
        section_index_counts=((1, 1257), (2, 45066), (3, 86937), (4, 94164)),
    ),
)


@dataclass(frozen=True)
class ParSection:
    """一个 PAR 内部段及其原压缩策略。"""

    index: int
    compressed: bool
    decompressed_size: int
    content: bytes


@dataclass(frozen=True)
class MeshlessPacResult:
    """单个 PAC 退化结果。"""

    content: bytes
    triangle_count: int
    changed_index_count: int


@dataclass(frozen=True)
class MeshlessFlightCloakBuildResult:
    """完全隐藏披风网格版本生成摘要。"""

    output_path: Path
    package_sha256: str
    pac_target_count: int
    degenerated_triangle_count: int
    changed_index_count: int
    animation_target_count: int
    prefab_target_count: int


def _parse_par_sections(data: bytes) -> tuple[bytes, tuple[ParSection, ...]]:
    """解压 PAR 内部段，并拒绝尺寸或尾部不一致的资源。"""
    if len(data) < PAR_HEADER_SIZE or data[:4] != b"PAR ":
        raise ValueError("飞行披风 PAC 缺少 PAR 文件头")
    header = data[:PAR_HEADER_SIZE]
    cursor = PAR_HEADER_SIZE
    sections: list[ParSection] = []
    for index in range(8):
        compressed_size, decompressed_size = struct.unpack_from("<II", header, 0x10 + index * 8)
        if decompressed_size == 0:
            if compressed_size != 0:
                raise ValueError(f"PAR section {index} 压缩尺寸非零但解压尺寸为零")
            continue
        stored_size = compressed_size or decompressed_size
        stored_end = cursor + stored_size
        if stored_end > len(data):
            raise ValueError(f"PAR section {index} 超出 PAC 文件边界")
        stored = data[cursor:stored_end]
        content = lz4_decompress(stored, decompressed_size) if compressed_size else stored
        if len(content) != decompressed_size:
            raise ValueError(f"PAR section {index} 解压尺寸不匹配")
        sections.append(
            ParSection(
                index=index,
                compressed=compressed_size > 0,
                decompressed_size=decompressed_size,
                content=content,
            )
        )
        cursor = stored_end
    if cursor != len(data):
        raise ValueError("PAR 末尾存在未登记数据")
    return header, tuple(sections)


def _validate_index_split_offsets(
    sections: tuple[ParSection, ...],
    section_index_counts: dict[int, int],
) -> None:
    """验证 Section 0 的虚拟索引偏移与各段末尾索引区一致。"""
    section_by_index = {section.index: section for section in sections}
    section_zero = section_by_index.get(0)
    if section_zero is None or len(section_zero.content) < 5:
        raise ValueError("PAC 缺少 Section 0")
    lod_count = section_zero.content[4]
    if lod_count != len(section_index_counts):
        raise ValueError(f"PAC LOD 数量不匹配：{lod_count}")
    split_table_offset = 5 + lod_count * 4
    if split_table_offset + lod_count * 4 > len(section_zero.content):
        raise ValueError("PAC Section 0 的 LOD 偏移表不完整")

    virtual_offsets: dict[int, int] = {}
    virtual_cursor = PAR_HEADER_SIZE
    for section in sorted(sections, key=lambda item: item.index):
        virtual_offsets[section.index] = virtual_cursor
        virtual_cursor += section.decompressed_size

    for lod_index in range(lod_count):
        section_index = lod_count - lod_index
        section = section_by_index.get(section_index)
        index_count = section_index_counts.get(section_index)
        if section is None or index_count is None or index_count % 3:
            raise ValueError(f"PAC section {section_index} 的索引声明无效")
        index_bytes = index_count * 2
        if index_bytes > len(section.content):
            raise ValueError(f"PAC section {section_index} 索引区超出段边界")
        expected_split = virtual_offsets[section_index] + len(section.content) - index_bytes
        declared_split = struct.unpack_from(
            "<I",
            section_zero.content,
            split_table_offset + lod_index * 4,
        )[0]
        if declared_split != expected_split:
            raise ValueError(
                f"PAC section {section_index} 索引起点不匹配："
                f"{declared_split} != {expected_split}"
            )


def _degenerated_section(content: bytes, index_count: int) -> tuple[bytes, int]:
    """把段末尾的三角形列表改成零面积三角形。"""
    index_bytes = index_count * 2
    index_start = len(content) - index_bytes
    output = bytearray(content)
    changed_index_count = 0
    for triangle_offset in range(index_start, len(output), 6):
        first, second, third = struct.unpack_from("<HHH", output, triangle_offset)
        if second != first:
            changed_index_count += 1
        if third != first:
            changed_index_count += 1
        struct.pack_into("<HHH", output, triangle_offset, first, first, first)
    return bytes(output), changed_index_count


def _rebuild_par(header: bytes, sections: tuple[ParSection, ...]) -> bytes:
    """按原压缩策略重建 PAR，保持每段解压尺寸不变。"""
    rebuilt_header = bytearray(header)
    payloads: list[bytes] = []
    section_by_index = {section.index: section for section in sections}
    for index in range(8):
        section = section_by_index.get(index)
        if section is None:
            struct.pack_into("<II", rebuilt_header, 0x10 + index * 8, 0, 0)
            continue
        payload = lz4_compress(section.content) if section.compressed else section.content
        compressed_size = len(payload) if section.compressed else 0
        struct.pack_into(
            "<II",
            rebuilt_header,
            0x10 + index * 8,
            compressed_size,
            section.decompressed_size,
        )
        payloads.append(payload)
    return bytes(rebuilt_header) + b"".join(payloads)


def degenerate_flight_cloak_pac(data: bytes, spec: FlightCloakPacSpec) -> MeshlessPacResult:
    """验证当前原版指纹并退化指定 PAC 的全部 LOD 网格。"""
    source_sha256 = hashlib.sha256(data).hexdigest()
    if source_sha256 != spec.source_sha256:
        raise ValueError(
            f"当前游戏 PAC 已变化，拒绝使用旧布局：{spec.target} "
            f"{source_sha256} != {spec.source_sha256}"
        )
    header, sections = _parse_par_sections(data)
    index_counts = dict(spec.section_index_counts)
    _validate_index_split_offsets(sections, index_counts)

    triangle_count = 0
    changed_index_count = 0
    patched_sections: list[ParSection] = []
    for section in sections:
        index_count = index_counts.get(section.index)
        if index_count is None:
            patched_sections.append(section)
            continue
        patched, changed = _degenerated_section(section.content, index_count)
        triangle_count += index_count // 3
        changed_index_count += changed
        patched_sections.append(
            ParSection(
                index=section.index,
                compressed=section.compressed,
                decompressed_size=section.decompressed_size,
                content=patched,
            )
        )

    rebuilt = _rebuild_par(header, tuple(patched_sections))
    _verify_degenerated_pac(rebuilt, spec)
    return MeshlessPacResult(
        content=rebuilt,
        triangle_count=triangle_count,
        changed_index_count=changed_index_count,
    )


def _verify_degenerated_pac(data: bytes, spec: FlightCloakPacSpec) -> None:
    """重读成品，确认所有三角形均退化且虚拟偏移仍有效。"""
    _header, sections = _parse_par_sections(data)
    index_counts = dict(spec.section_index_counts)
    _validate_index_split_offsets(sections, index_counts)
    for section in sections:
        index_count = index_counts.get(section.index)
        if index_count is None:
            continue
        index_start = len(section.content) - index_count * 2
        for triangle_offset in range(index_start, len(section.content), 6):
            first, second, third = struct.unpack_from("<HHH", section.content, triangle_offset)
            if first != second or first != third:
                raise ValueError(f"PAC section {section.index} 仍包含可绘制三角形")


def _read_current_pac(game_dir: Path, target: str) -> bytes:
    """从当前 0009 PAMT 精确读取指定 PAC 明文。"""
    entry = get_game_pamt_index(game_dir).find_in_dir(TARGET_PAMT_DIR, target)
    if entry is None:
        raise ValueError(f"当前游戏 0009 中未找到 {target}")
    return extract_plaintext(entry)[0]


def build_meshless_flight_cloak_mod(
    game_dir: Path,
    output_path: Path,
) -> MeshlessFlightCloakBuildResult:
    """从当前游戏资源生成不修改 prefab 的完全隐藏披风候选包。"""
    game_dir = game_dir.resolve()
    output_path = output_path.resolve()
    documents: dict[str, dict[str, object] | bytes] = {}
    file_specs: list[dict[str, object]] = []
    source_hashes: dict[str, str] = {}
    triangle_count = 0
    changed_index_count = 0

    for index, spec in enumerate(FLIGHT_CLOAK_PAC_SPECS):
        source = _read_current_pac(game_dir, spec.target)
        transformed = degenerate_flight_cloak_pac(source, spec)
        payload_path = f"assets/{index:05d}/{Path(spec.target).name}"
        documents[payload_path] = transformed.content
        file_specs.append(
            {
                "target": spec.target,
                "pamt_dir": TARGET_PAMT_DIR,
                "payload": payload_path,
                "sha256": hashlib.sha256(transformed.content).hexdigest(),
                "size": len(transformed.content),
                "allow_new": False,
                "allow_table_replace": False,
            }
        )
        source_hashes[spec.target] = spec.source_sha256
        triangle_count += transformed.triangle_count
        changed_index_count += transformed.changed_index_count

    manifest_document = {
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": "cdmm-meshless-flight-cloak-native-fx",
        "name": "Meshless Flight Cloak - Native FX",
        "version": "1.0",
        "author": "cdmm",
        "description": (
            "Degenerates every triangle in both male flight-cloak PAC meshes while "
            "preserving prefabs, animations, action charts, materials, bones, LODs, and native FX."
        ),
        "dependencies": [],
        "source": {
            "format": "current-game-pac-internal-lz4-index-degeneration",
            "files": source_hashes,
        },
        "components": [
            {
                "type": CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE,
                "path": FILE_REPLACEMENT_PATH,
                "file_count": len(file_specs),
            }
        ],
    }
    report_document = {
        "schema": 1,
        "summary": {
            "pac_target_count": len(file_specs),
            "degenerated_triangle_count": triangle_count,
            "changed_index_count": changed_index_count,
            "prefab_target_count": 0,
            "animation_target_count": 0,
            "actionchart_target_count": 0,
            "pac_xml_target_count": 0,
        },
        "preserved": [
            "prefab-bytes",
            "pac-section0",
            "vertex-and-bone-records",
            "submesh-and-index-counts",
            "pac-xml-materials",
            "flight-animation",
            "actionchart",
            "native-fx",
        ],
        "safety": {
            "source_policy": "exact-current-game-sha256-required",
            "geometry_policy": "triangle-indices-only; decompressed section size unchanged",
            "compression_policy": "preserve original per-section compressed/uncompressed mode",
        },
    }
    documents.update(
        {
            CDMOD_MANIFEST_PATH: manifest_document,
            FILE_REPLACEMENT_PATH: {"schema": 1, "files": file_specs},
            CDMOD_REPORT_PATH: report_document,
        }
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_cdmod_zip(output_path, documents)
    return MeshlessFlightCloakBuildResult(
        output_path=output_path,
        package_sha256=hashlib.sha256(output_path.read_bytes()).hexdigest(),
        pac_target_count=len(file_specs),
        degenerated_triangle_count=triangle_count,
        changed_index_count=changed_index_count,
        animation_target_count=0,
        prefab_target_count=0,
    )


def result_to_json(result: MeshlessFlightCloakBuildResult) -> dict[str, object]:
    """把生成摘要转换为命令行 JSON。"""
    payload = asdict(result)
    payload["output_path"] = str(result.output_path)
    return payload


def main() -> int:
    """解析当前游戏目录与输出路径并生成候选包。"""
    parser = argparse.ArgumentParser(description="生成不修改 prefab 的完全隐藏飞行披风 cdmod")
    parser.add_argument("game_dir", type=Path, help="Crimson Desert 游戏根目录")
    parser.add_argument("output", type=Path, help="输出 .cdmod 路径")
    args = parser.parse_args()
    result = build_meshless_flight_cloak_mod(args.game_dir, args.output)
    print(json.dumps(result_to_json(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
