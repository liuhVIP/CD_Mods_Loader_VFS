"""基于已验证白色飞行披风 DDS 生成指定颜色的 ``.cdmod``。

换色过程直接处理 DXT1/BC1 压缩块，保持完整 mip 链、透明索引与羽毛明暗，
避免普通图片编辑器重新导出时丢失游戏需要的 DDS 结构。
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

# 标准 DDS 文件头长度；当前四张参考贴图均为无 DX10 扩展头的 DXT1。
DDS_HEADER_SIZE = 128

# 飞行披风资源固定属于 0009 角色贴图目录。
TARGET_PAMT_DIR = "0009"
TARGET_TEXTURE_DIR = "character/texture"

# 白色参考包中已经实机验证生效的四张男性飞行披风贴图。
FLIGHT_CLOAK_TEXTURE_NAMES = (
    "cd_phm_00_cloak_flight_00_0001.dds",
    "cd_phm_00_cloak_flight_hel_00_0001.dds",
    "cd_phm_00_cloak_flight_hel_00_0001_emi.dds",
    "cd_phm_00_cloak_flight_spline_00_0001.dds",
)

# 完整资源组件在 cdmod 内的固定文档路径。
FILE_REPLACEMENT_PATH = "files/replacements.json"

# 鲜红预设保留少量绿色和蓝色，避免高光区域在游戏色调映射后过度断层。
VIVID_RED_RGB = (255, 24, 18)

# 常用颜色预设同时用于成品内部名称，任意其他 RGB 仍可生成自定义包。
COLOR_VARIANT_NAMES = {
    VIVID_RED_RGB: "Vivid Red Flight Cloak",
    (255, 210, 26): "Gold Yellow Flight Cloak",
    (38, 118, 255): "Sapphire Blue Flight Cloak",
    (32, 208, 96): "Emerald Green Flight Cloak",
    (168, 85, 247): "Royal Purple Flight Cloak",
    (255, 61, 170): "Hot Pink Flight Cloak",
    (33, 223, 255): "Ice Cyan Flight Cloak",
    (255, 122, 26): "Sunset Orange Flight Cloak",
}


@dataclass(frozen=True)
class ColoredFlightCloakBuildResult:
    """一次彩色飞行披风生成结果。"""

    output_path: Path
    package_sha256: str
    color_rgb: tuple[int, int, int]
    file_count: int
    payload_bytes: int


def build_colored_flight_cloak_mod(
    source_dir: Path,
    output_path: Path,
    color_rgb: tuple[int, int, int],
    variant_name: str | None = None,
) -> ColoredFlightCloakBuildResult:
    """从白色参考包生成保留亮度和透明度的彩色披风 cdmod。"""
    source_dir = source_dir.resolve()
    output_path = output_path.resolve()
    _validate_rgb(color_rgb)
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
        colored_bytes = recolor_bc1_dds(source_bytes, color_rgb)
        payload_path = f"assets/{index:05d}/{texture_name}"
        target = f"{TARGET_TEXTURE_DIR}/{texture_name}"
        digest = hashlib.sha256(colored_bytes).hexdigest()
        documents[payload_path] = colored_bytes
        file_specs.append(
            {
                "target": target,
                "pamt_dir": TARGET_PAMT_DIR,
                "payload": payload_path,
                "sha256": digest,
                "size": len(colored_bytes),
                "allow_new": False,
                "allow_table_replace": False,
            }
        )
        source_hashes[texture_name] = hashlib.sha256(source_bytes).hexdigest()
        payload_bytes += len(colored_bytes)

    color_hex = "#" + "".join(f"{channel:02X}" for channel in color_rgb)
    package_name = variant_name or COLOR_VARIANT_NAMES.get(
        color_rgb,
        f"Custom {color_hex} Flight Cloak",
    )
    replacement_document = {"schema": 1, "files": file_specs}
    manifest_document = {
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": f"destriee-flight-cloak-{color_hex[1:].lower()}",
        "name": package_name,
        "version": "1.0",
        "author": "Destriee / cdmm recolor",
        "description": (
            f"Recolors Destriee's verified white flight cloak textures to {color_hex} "
            "while preserving BC1 transparency, mipmaps, and shading."
        ),
        "dependencies": [],
        "source": {
            "format": "verified-white-flight-cloak-bc1-recolor",
            "color": color_hex,
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
        "source": {
            "name": source_dir.name,
            "author": "Destriee",
            "base_color": "white",
        },
        "summary": {
            "target_color": color_hex,
            "file_count": len(file_specs),
            "payload_bytes": payload_bytes,
        },
        "preserved": [
            "dds-header",
            "mip-chain",
            "bc1-block-mode",
            "transparent-pixel-index",
            "source-luminance",
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
    return ColoredFlightCloakBuildResult(
        output_path=output_path,
        package_sha256=hashlib.sha256(output_path.read_bytes()).hexdigest(),
        color_rgb=color_rgb,
        file_count=len(file_specs),
        payload_bytes=payload_bytes,
    )


def recolor_bc1_dds(dds_bytes: bytes, color_rgb: tuple[int, int, int]) -> bytes:
    """保持 DXT1 块结构，把每个端点的原亮度映射到目标色。"""
    _validate_rgb(color_rgb)
    if len(dds_bytes) < DDS_HEADER_SIZE or dds_bytes[:4] != b"DDS ":
        raise ValueError("飞行披风贴图不是有效 DDS")
    if dds_bytes[84:88] != b"DXT1":
        raise ValueError(f"飞行披风贴图不是 DXT1：{dds_bytes[84:88]!r}")
    payload = dds_bytes[DDS_HEADER_SIZE:]
    if len(payload) % 8:
        raise ValueError("DXT1 数据长度不是 8 字节块对齐")

    output = bytearray(dds_bytes)
    for offset in range(DDS_HEADER_SIZE, len(output), 8):
        color0, color1, indices = struct.unpack_from("<HHI", output, offset)
        mapped0 = _recolor_565(color0, color_rgb)
        mapped1 = _recolor_565(color1, color_rgb)
        uses_opaque_palette = color0 > color1

        if uses_opaque_palette and mapped0 == mapped1:
            mapped0, mapped1 = _separate_opaque_endpoints(mapped0, mapped1)
        if uses_opaque_palette and mapped0 < mapped1:
            mapped0, mapped1 = mapped1, mapped0
            indices = _remap_indices(indices, (1, 0, 3, 2))
        elif not uses_opaque_palette and mapped0 > mapped1:
            mapped0, mapped1 = mapped1, mapped0
            indices = _remap_indices(indices, (1, 0, 2, 3))

        struct.pack_into("<HHI", output, offset, mapped0, mapped1, indices)
    return bytes(output)


def result_to_json(result: ColoredFlightCloakBuildResult) -> dict[str, object]:
    """把生成结果转换为命令行 JSON。"""
    payload = asdict(result)
    payload["output_path"] = str(result.output_path)
    return payload


def _recolor_565(value: int, color_rgb: tuple[int, int, int]) -> int:
    """解码 RGB565，按相对亮度缩放目标色后重新量化。"""
    red = ((value >> 11) & 0x1F) * 255 / 31
    green = ((value >> 5) & 0x3F) * 255 / 63
    blue = (value & 0x1F) * 255 / 31
    luminance = (0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255
    mapped = tuple(round(channel * luminance) for channel in color_rgb)
    return _encode_565(mapped)


def _encode_565(color_rgb: tuple[int, int, int]) -> int:
    """把 8 位 RGB 量化为 RGB565。"""
    red, green, blue = color_rgb
    return (
        (round(red * 31 / 255) << 11)
        | (round(green * 63 / 255) << 5)
        | round(blue * 31 / 255)
    )


def _separate_opaque_endpoints(color0: int, color1: int) -> tuple[int, int]:
    """端点量化重合时只调整红色位，继续保持 DXT1 四色模式。"""
    red0 = (color0 >> 11) & 0x1F
    if red0 < 0x1F:
        return color0 + 0x0800, color1
    red1 = (color1 >> 11) & 0x1F
    if red1 > 0:
        return color0, color1 - 0x0800
    return 1, 0


def _remap_indices(indices: int, mapping: tuple[int, int, int, int]) -> int:
    """交换 DXT1 端点时同步重排 16 个像素的调色板索引。"""
    remapped = 0
    for pixel_index in range(16):
        source_index = (indices >> (pixel_index * 2)) & 0x03
        remapped |= mapping[source_index] << (pixel_index * 2)
    return remapped


def _parse_hex_color(value: str) -> tuple[int, int, int]:
    """解析 ``RRGGBB`` 或 ``#RRGGBB`` 命令行颜色。"""
    normalized = value.strip().lstrip("#")
    if len(normalized) != 6:
        raise argparse.ArgumentTypeError("颜色必须为 RRGGBB")
    try:
        color = tuple(int(normalized[index:index + 2], 16) for index in (0, 2, 4))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("颜色必须为十六进制 RRGGBB") from exc
    return color  # type: ignore[return-value]


def _validate_rgb(color_rgb: tuple[int, int, int]) -> None:
    """校验三个 RGB 通道均为 0-255 整数。"""
    if len(color_rgb) != 3 or any(
        not isinstance(channel, int) or isinstance(channel, bool) or not 0 <= channel <= 255
        for channel in color_rgb
    ):
        raise ValueError("RGB 颜色必须包含三个 0-255 整数")


def main() -> int:
    """解析参数并生成彩色飞行披风模组。"""
    parser = argparse.ArgumentParser(description="基于白色参考 DDS 生成彩色飞行披风 cdmod")
    parser.add_argument("source", type=Path, help="包含 files/0009 的白色披风参考目录")
    parser.add_argument("output", type=Path, help="输出 .cdmod 路径")
    parser.add_argument("--color", type=_parse_hex_color, default=VIVID_RED_RGB)
    parser.add_argument("--name", help="写入 cdmod manifest 的颜色版本名称")
    args = parser.parse_args()
    result = build_colored_flight_cloak_mod(
        args.source,
        args.output,
        args.color,
        args.name,
    )
    print(json.dumps(result_to_json(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
