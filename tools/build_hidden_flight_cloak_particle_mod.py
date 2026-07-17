"""生成精简红色飞行披风、保留轻量红色粒子光迹的 ``.cdmod``。

实体相关三张 DXT1 贴图写成全透明块，spline 贴图保留原始明暗并映射为
低亮红色。这样不禁用整个飞行附件，避免连同原生粒子链一起移除。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from dataclasses import asdict, dataclass
from pathlib import Path

from cdmm.services.cdmod_converter import (
    CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE,
    CDMOD_FORMAT_NAME,
    CDMOD_FORMAT_VERSION,
    CDMOD_MANIFEST_PATH,
    CDMOD_REPORT_PATH,
    _write_cdmod_zip,
)
from cdmm.tools.build_colored_flight_cloak_mod import (
    DDS_HEADER_SIZE,
    FILE_REPLACEMENT_PATH,
    FLIGHT_CLOAK_TEXTURE_NAMES,
    TARGET_PAMT_DIR,
    TARGET_TEXTURE_DIR,
    recolor_bc1_dds,
)

# 三张实体/发光层贴图全部透明，确保披风主体不再显示。
HIDDEN_TEXTURE_NAMES = frozenset(FLIGHT_CLOAK_TEXTURE_NAMES[:3])

# spline 是四张实测资源中最适合单独保留的粒子/细线光迹。
PARTICLE_TEXTURE_NAME = FLIGHT_CLOAK_TEXTURE_NAMES[3]

# 低亮暖红粒子，避免重新形成一整片高亮披风轮廓。
SUBTLE_RED_PARTICLE_RGB = (160, 28, 12)

# 默认版本名保持与已实机确认的红色轻量版一致。
DEFAULT_SLIM_VARIANT_NAME = "Slim Red Flight Cloak - Particle Accents"


@dataclass(frozen=True)
class HiddenFlightCloakBuildResult:
    """精简披风粒子版生成摘要。"""

    output_path: Path
    package_sha256: str
    hidden_texture_count: int
    particle_texture_count: int
    payload_bytes: int
    particle_rgb: tuple[int, int, int]
    variant_name: str


def build_hidden_flight_cloak_particle_mod(
    source_dir: Path,
    output_path: Path,
    particle_rgb: tuple[int, int, int] = SUBTLE_RED_PARTICLE_RGB,
    variant_name: str = DEFAULT_SLIM_VARIANT_NAME,
) -> HiddenFlightCloakBuildResult:
    """基于已验证白色资源生成轻量披风、指定颜色粒子版 cdmod。"""
    source_dir = source_dir.resolve()
    output_path = output_path.resolve()
    texture_root = source_dir / "files" / TARGET_PAMT_DIR / TARGET_TEXTURE_DIR

    documents: dict[str, dict[str, object] | bytes] = {}
    file_specs: list[dict[str, object]] = []
    source_hashes: dict[str, str] = {}
    payload_bytes = 0
    for index, texture_name in enumerate(FLIGHT_CLOAK_TEXTURE_NAMES):
        source_path = texture_root / texture_name
        if not source_path.is_file():
            raise ValueError(f"白色参考包缺少飞行披风贴图：{texture_name}")
        source_bytes = source_path.read_bytes()
        if texture_name in HIDDEN_TEXTURE_NAMES:
            transformed = make_fully_transparent_bc1_dds(source_bytes)
        else:
            transformed = recolor_bc1_dds(source_bytes, particle_rgb)
        payload_path = f"assets/{index:05d}/{texture_name}"
        target = f"{TARGET_TEXTURE_DIR}/{texture_name}"
        documents[payload_path] = transformed
        file_specs.append(
            {
                "target": target,
                "pamt_dir": TARGET_PAMT_DIR,
                "payload": payload_path,
                "sha256": hashlib.sha256(transformed).hexdigest(),
                "size": len(transformed),
                "allow_new": False,
                "allow_table_replace": False,
            }
        )
        source_hashes[texture_name] = hashlib.sha256(source_bytes).hexdigest()
        payload_bytes += len(transformed)

    particle_hex = "#" + "".join(f"{channel:02X}" for channel in particle_rgb)
    manifest_document = {
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": f"cdmm-slim-flight-cloak-{particle_hex[1:].lower()}-particle-accents",
        "name": variant_name,
        "version": "1.0",
        "author": "Destriee / Deletriuz reference / cdmm",
        "description": (
            f"Creates a slimmer flight-cloak appearance with subtle {particle_hex} spline "
            "particle accents. This variant does not fully remove the cloak mesh."
        ),
        "dependencies": [],
        "source": {
            "format": "verified-flight-cloak-selective-bc1-transform",
            "particle_color": particle_hex,
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
    replacement_document = {"schema": 1, "files": file_specs}
    report_document = {
        "schema": 1,
        "reference": {
            "animation_mod": "Male Glide Animation 2.8",
            "finding": "PAAC attachment removal hides the whole glider and is not reused",
        },
        "summary": {
            "hidden_textures": sorted(HIDDEN_TEXTURE_NAMES),
            "particle_texture": PARTICLE_TEXTURE_NAME,
            "particle_color": particle_hex,
            "payload_bytes": payload_bytes,
        },
        "preserved": [
            "flight-animation",
            "attachment-lifecycle",
            "dds-header",
            "mip-chain",
            "spline-luminance",
        ],
    }
    documents.update(
        {
            CDMOD_MANIFEST_PATH: manifest_document,
            FILE_REPLACEMENT_PATH: replacement_document,
            CDMOD_REPORT_PATH: report_document,
        }
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_cdmod_zip(output_path, documents)
    return HiddenFlightCloakBuildResult(
        output_path=output_path,
        package_sha256=hashlib.sha256(output_path.read_bytes()).hexdigest(),
        hidden_texture_count=len(HIDDEN_TEXTURE_NAMES),
        particle_texture_count=1,
        payload_bytes=payload_bytes,
        particle_rgb=particle_rgb,
        variant_name=variant_name,
    )


def make_fully_transparent_bc1_dds(dds_bytes: bytes) -> bytes:
    """把所有 DXT1 像素索引写为透明索引 3，保持头部和 mip 长度。"""
    if len(dds_bytes) < DDS_HEADER_SIZE or dds_bytes[:4] != b"DDS ":
        raise ValueError("飞行披风贴图不是有效 DDS")
    if dds_bytes[84:88] != b"DXT1":
        raise ValueError(f"飞行披风贴图不是 DXT1：{dds_bytes[84:88]!r}")
    if (len(dds_bytes) - DDS_HEADER_SIZE) % 8:
        raise ValueError("DXT1 数据长度不是 8 字节块对齐")

    output = bytearray(dds_bytes)
    for offset in range(DDS_HEADER_SIZE, len(output), 8):
        struct.pack_into("<HHI", output, offset, 0, 0, 0xFFFFFFFF)
    return bytes(output)


def result_to_json(result: HiddenFlightCloakBuildResult) -> dict[str, object]:
    """把生成摘要转换为命令行 JSON。"""
    payload = asdict(result)
    payload["output_path"] = str(result.output_path)
    return payload


def main() -> int:
    """解析参数并生成隐藏披风粒子版。"""
    parser = argparse.ArgumentParser(description="生成精简红色披风、保留粒子光迹的 cdmod")
    parser.add_argument("source", type=Path, help="包含 files/0009 的白色披风参考目录")
    parser.add_argument("output", type=Path, help="输出 .cdmod 路径")
    args = parser.parse_args()
    result = build_hidden_flight_cloak_particle_mod(args.source, args.output)
    print(json.dumps(result_to_json(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
