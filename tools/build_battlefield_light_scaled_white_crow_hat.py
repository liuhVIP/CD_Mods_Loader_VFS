"""生成“战场之光”白鸦巫师帽等比缩小测试包。

本工具继续使用已经实机验证稳定的 0151 目标 Prefab 结构，不复制白鸦完整
0164 Prefab，也不注册条件装备表。白鸦主 PAC 会在全部子网格共同包围盒中心
等比缩小，再写入一个同长度私有 PAC 路径，避免覆盖白鸦原生装备资源。
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from cdmm.archive.pamt import derive_pamt_dir, parse_pamt_filtered
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
from cdmm.services.pamt_index_service import get_game_pamt_index
from cdmm.tools.build_battlefield_light_white_crow_hat_swap import (
    BATTLEFIELD_LIGHT_HAT_PATH,
    BATTLEFIELD_LIGHT_MAIN_PAC,
    EXPECTED_TARGET_SHA256,
    HAT_PAMT_DIR,
    WHITE_CROW_MAIN_PAC,
    build_structural_hat_prefab,
)

# 当前游戏白鸦主帽 PAC 的最终规范路径。
WHITE_CROW_MAIN_PAC_PATH = WHITE_CROW_MAIN_PAC.decode("ascii")

# 当前 1.14 白鸦主帽 PAC 明文指纹，游戏更新后拒绝盲目套用旧网格布局。
EXPECTED_WHITE_CROW_PAC_SHA256 = (
    "4e01b097d1af7a1be8bad464ac86bf558bc66f4cf3eb514e34999c332174577d"
)

# 网格工作台只在生成阶段使用，成品 cdmod 没有任何运行时依赖。
DEFAULT_WORKBENCH_ROOT = Path(r"T:\C++\crimson-desert-mod-workbench")
WORKBENCH_ROOT_ENV = "CDMW_ROOT"

# cdmod 内文件替换组件与载荷使用固定路径，便于回读审计。
FILE_REPLACEMENT_PATH = "files/replacements.json"
PREFAB_PAYLOAD_PATH = "assets/00000/cd_phw_00_hel_00_0151.prefab"
PAC_PAYLOAD_PATH = "assets/00001/scaled_white_crow_hat.pac"


@dataclass(frozen=True)
class ScalePreset:
    """一个互斥缩放测试版本及其私有 PAC 编号。"""

    key: str
    scale: float
    alias_number: str
    output_filename: str

    @property
    def alias_pac_path(self) -> str:
        """返回与原 0164 路径等长的私有 PAC 规范路径。"""
        return WHITE_CROW_MAIN_PAC_PATH.replace("0164.pac", f"{self.alias_number}.pac")


# 三个比例互斥，只允许同时启用其中一个进行实机对比。
SCALE_PRESETS = (
    ScalePreset(
        key="80",
        scale=0.80,
        alias_number="8064",
        output_filename="ZZZ - Battlefield White Crow Hat Scale 80 Percent-0.1-test.cdmod",
    ),
    ScalePreset(
        key="85",
        scale=0.85,
        alias_number="8564",
        output_filename="ZZZ - Battlefield White Crow Hat Scale 85 Percent-0.1-test.cdmod",
    ),
    ScalePreset(
        key="90",
        scale=0.90,
        alias_number="9064",
        output_filename="ZZZ - Battlefield White Crow Hat Scale 90 Percent-0.1-test.cdmod",
    ),
)
SCALE_PRESETS_BY_KEY = {preset.key: preset for preset in SCALE_PRESETS}


@dataclass(frozen=True)
class PacScaleAudit:
    """缩放前后 PAC 网格与字节布局审计结果。"""

    source_sha256: str
    output_sha256: str
    source_size: int
    output_size: int
    scale: float
    center_before: tuple[float, float, float]
    center_after: tuple[float, float, float]
    extent_before: tuple[float, float, float]
    extent_after: tuple[float, float, float]
    submesh_count: int
    vertex_count: int
    face_count: int


@dataclass(frozen=True)
class ScaledHatBuildResult:
    """单个缩小帽测试包的生成结果。"""

    preset_key: str
    output_path: Path
    package_sha256: str
    alias_pac_path: str
    prefab_sha256: str
    pac_audit: PacScaleAudit


def get_scale_preset(preset_key: str) -> ScalePreset:
    """按稳定 key 获取缩放预设。"""
    try:
        return SCALE_PRESETS_BY_KEY[preset_key]
    except KeyError as exc:
        choices = ", ".join(SCALE_PRESETS_BY_KEY)
        raise ValueError(f"未知缩放预设 {preset_key!r}，可选：{choices}") from exc


def _load_mesh_tooling() -> tuple[Callable[..., Any], Callable[..., bytes]]:
    """延迟加载开发用 PAC 解析器，避免给生成后的 cdmod 增加依赖。"""
    configured = os.environ.get(WORKBENCH_ROOT_ENV, "").strip()
    workbench_root = Path(configured) if configured else DEFAULT_WORKBENCH_ROOT
    if not (workbench_root / "cdmw" / "modding" / "mesh_parser.py").is_file():
        raise FileNotFoundError(
            f"缺少 Crimson Desert Mod Workbench：{workbench_root}；"
            f"可通过环境变量 {WORKBENCH_ROOT_ENV} 指定目录"
        )
    root_text = str(workbench_root.resolve())
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    from cdmw.modding.mesh_importer import build_pac
    from cdmw.modding.mesh_parser import parse_pac

    return parse_pac, build_pac


def _mesh_vertices(mesh: Any) -> list[tuple[float, float, float]]:
    """汇总全部非空子网格顶点。"""
    vertices = [
        tuple(vertex)
        for submesh in mesh.submeshes
        for vertex in submesh.vertices
    ]
    if not vertices:
        raise ValueError("白鸦主帽 PAC 没有可缩放顶点")
    return vertices


def _bounds(
    vertices: list[tuple[float, float, float]],
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """计算顶点集合的共同包围盒中心与三轴尺寸。"""
    minimum = tuple(min(vertex[axis] for vertex in vertices) for axis in range(3))
    maximum = tuple(max(vertex[axis] for vertex in vertices) for axis in range(3))
    center = tuple((minimum[axis] + maximum[axis]) * 0.5 for axis in range(3))
    extent = tuple(maximum[axis] - minimum[axis] for axis in range(3))
    return center, extent


def scale_mesh_around_common_center(mesh: Any, scale: float) -> Any:
    """保持全部子网格相对关系，围绕共同包围盒中心等比缩放。"""
    if not 0.5 <= scale < 1.0:
        raise ValueError(f"帽子缩放比例超出安全测试范围：{scale}")
    output = copy.deepcopy(mesh)
    center, _extent = _bounds(_mesh_vertices(output))
    for submesh in output.submeshes:
        submesh.vertices = [
            tuple(
                center[axis] + (vertex[axis] - center[axis]) * scale
                for axis in range(3)
            )
            for vertex in submesh.vertices
        ]
    return output


def _read_target_prefab(game_dir: Path) -> bytes:
    """读取当前原版 0151 Prefab，并固定校验版本指纹。"""
    pamt_path = game_dir / HAT_PAMT_DIR / "0.pamt"
    entries = parse_pamt_filtered(
        pamt_path,
        paz_dir=pamt_path.parent,
        desired_exact={BATTLEFIELD_LIGHT_HAT_PATH},
    )
    if len(entries) != 1:
        raise ValueError("当前游戏中未唯一找到战场之光 0151 Prefab")
    content, _ = extract_plaintext(entries[0])
    source_sha256 = hashlib.sha256(content).hexdigest()
    if source_sha256 != EXPECTED_TARGET_SHA256:
        raise ValueError(f"原版 0151 Prefab SHA256 已变化：{source_sha256}")
    return content


def _read_white_crow_pac(game_dir: Path) -> bytes:
    """从当前原版 0009 精确读取白鸦主帽 PAC。"""
    entry = get_game_pamt_index(game_dir).find_best(WHITE_CROW_MAIN_PAC_PATH)
    if entry is None or derive_pamt_dir(entry.paz_file) != HAT_PAMT_DIR:
        raise ValueError("当前游戏 0009 中未找到白鸦主帽 PAC")
    content, _ = extract_plaintext(entry)
    source_sha256 = hashlib.sha256(content).hexdigest()
    if source_sha256 != EXPECTED_WHITE_CROW_PAC_SHA256:
        raise ValueError(f"原版白鸦主帽 PAC SHA256 已变化：{source_sha256}")
    return content


def _scaled_pac(
    source: bytes,
    scale: float,
) -> tuple[bytes, PacScaleAudit]:
    """解析、缩放、原位重建并回读验证白鸦主帽 PAC。"""
    parse_pac, build_pac = _load_mesh_tooling()
    source_mesh = parse_pac(source, WHITE_CROW_MAIN_PAC_PATH)
    source_vertices = _mesh_vertices(source_mesh)
    center_before, extent_before = _bounds(source_vertices)
    scaled_mesh = scale_mesh_around_common_center(source_mesh, scale)
    output = build_pac(scaled_mesh, source)
    rebuilt_mesh = parse_pac(output, WHITE_CROW_MAIN_PAC_PATH)
    rebuilt_vertices = _mesh_vertices(rebuilt_mesh)
    center_after, extent_after = _bounds(rebuilt_vertices)

    source_topology = [
        (len(submesh.vertices), len(submesh.faces))
        for submesh in source_mesh.submeshes
    ]
    rebuilt_topology = [
        (len(submesh.vertices), len(submesh.faces))
        for submesh in rebuilt_mesh.submeshes
    ]
    if source_topology != rebuilt_topology:
        raise ValueError("缩放后 PAC 子网格拓扑发生变化")
    if len(output) != len(source):
        raise ValueError("缩放后 PAC 文件长度发生变化")
    for axis in range(3):
        if abs(center_after[axis] - center_before[axis]) > 0.00001:
            raise ValueError("缩放后帽子共同中心发生异常漂移")
        actual_ratio = extent_after[axis] / extent_before[axis]
        if abs(actual_ratio - scale) > 0.0001:
            raise ValueError("缩放后帽子三轴尺寸比例与目标不一致")

    audit = PacScaleAudit(
        source_sha256=hashlib.sha256(source).hexdigest(),
        output_sha256=hashlib.sha256(output).hexdigest(),
        source_size=len(source),
        output_size=len(output),
        scale=scale,
        center_before=center_before,
        center_after=center_after,
        extent_before=extent_before,
        extent_after=extent_after,
        submesh_count=len(source_mesh.submeshes),
        vertex_count=sum(len(submesh.vertices) for submesh in source_mesh.submeshes),
        face_count=sum(len(submesh.faces) for submesh in source_mesh.submeshes),
    )
    return output, audit


def build_scaled_hat_mod(
    game_dir: Path,
    output_path: Path,
    preset_key: str = "85",
) -> ScaledHatBuildResult:
    """生成一个私有 PAC 别名加 0151 结构适配 Prefab 的缩放测试包。"""
    game_dir = game_dir.resolve()
    output_path = output_path.resolve()
    preset = get_scale_preset(preset_key)
    target_prefab = _read_target_prefab(game_dir)
    source_pac = _read_white_crow_pac(game_dir)
    scaled_pac, pac_audit = _scaled_pac(source_pac, preset.scale)
    alias_pac = preset.alias_pac_path.encode("ascii")
    if len(alias_pac) != len(BATTLEFIELD_LIGHT_MAIN_PAC):
        raise ValueError("私有 PAC 别名长度与战场之光原路径不一致")
    patched_prefab = build_structural_hat_prefab(target_prefab, alias_pac)
    prefab_sha256 = hashlib.sha256(patched_prefab).hexdigest()

    replacements = {
        "schema": 1,
        "files": [
            {
                "target": BATTLEFIELD_LIGHT_HAT_PATH,
                "pamt_dir": HAT_PAMT_DIR,
                "payload": PREFAB_PAYLOAD_PATH,
                "sha256": prefab_sha256,
                "size": len(patched_prefab),
                "allow_new": False,
                "allow_table_replace": False,
            },
            {
                "target": preset.alias_pac_path,
                "pamt_dir": HAT_PAMT_DIR,
                "payload": PAC_PAYLOAD_PATH,
                "sha256": pac_audit.output_sha256,
                "size": len(scaled_pac),
                "allow_new": True,
                "allow_table_replace": False,
            },
        ],
    }
    manifest = {
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": f"battlefield-light-white-crow-hat-scale-{preset.key}",
        "name": f"Battlefield White Crow Hat Scale {preset.key} Percent",
        "version": "0.1-test",
        "author": "cdmm research",
        "description": (
            "Keeps the verified 0151 single-component prefab contract and mounts "
            f"a private White Crow main-PAC alias uniformly scaled to {preset.key}%."
        ),
        "dependencies": [],
        "source": {
            "format": "white-crow-pac-uniform-scale-private-alias",
            "game_version": "1.14",
            "source_pac": WHITE_CROW_MAIN_PAC_PATH,
            "scale": preset.scale,
        },
        "components": [
            {
                "type": CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE,
                "path": FILE_REPLACEMENT_PATH,
            }
        ],
    }
    report = {
        "schema": 1,
        "preset": asdict(preset),
        "pac_audit": asdict(pac_audit),
        "mapping": {
            "target_prefab": BATTLEFIELD_LIGHT_HAT_PATH,
            "source_pac": WHITE_CROW_MAIN_PAC_PATH,
            "private_alias_pac": preset.alias_pac_path,
        },
        "preserved": [
            "target-prefab-length",
            "target-component-count",
            "target-scene-object-uid",
            "target-bone-socket",
            "pac-file-length",
            "pac-submesh-count",
            "pac-vertex-count",
            "pac-face-count",
            "pac-bone-weights",
            "pac-material-bindings",
        ],
        "compatibility": {
            "uses_conditional_table": False,
            "overwrites_native_white_crow_pac": False,
            "mutually_exclusive_with_other_0151_hat_variants": True,
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_cdmod_zip(
        output_path,
        {
            CDMOD_MANIFEST_PATH: manifest,
            FILE_REPLACEMENT_PATH: replacements,
            PREFAB_PAYLOAD_PATH: patched_prefab,
            PAC_PAYLOAD_PATH: scaled_pac,
            CDMOD_REPORT_PATH: report,
        },
    )
    _verify_package(output_path, patched_prefab, scaled_pac, preset)
    return ScaledHatBuildResult(
        preset_key=preset.key,
        output_path=output_path,
        package_sha256=hashlib.sha256(output_path.read_bytes()).hexdigest(),
        alias_pac_path=preset.alias_pac_path,
        prefab_sha256=prefab_sha256,
        pac_audit=pac_audit,
    )


def _verify_package(
    output_path: Path,
    expected_prefab: bytes,
    expected_pac: bytes,
    preset: ScalePreset,
) -> None:
    """重读成品并确认目标 Prefab 与私有 PAC 别名声明完整。"""
    package = load_cdmod_package(output_path)
    files = [file for patch in package.file_patches for file in patch.files]
    if len(files) != 2:
        raise ValueError("缩放帽测试包替换文件数量异常")
    files_by_target = {file.target: file for file in files}
    prefab_file = files_by_target.get(BATTLEFIELD_LIGHT_HAT_PATH)
    pac_file = files_by_target.get(preset.alias_pac_path)
    if prefab_file is None or prefab_file.content != expected_prefab or prefab_file.allow_new:
        raise ValueError("缩放帽测试包 0151 Prefab 声明异常")
    if pac_file is None or pac_file.content != expected_pac or not pac_file.allow_new:
        raise ValueError("缩放帽测试包私有 PAC 别名声明异常")
    if preset.alias_pac_path.encode("ascii") not in expected_prefab:
        raise ValueError("缩放帽测试包 Prefab 未引用私有 PAC 别名")
    if WHITE_CROW_MAIN_PAC in expected_prefab:
        raise ValueError("缩放帽测试包 Prefab 仍直接引用原生白鸦 PAC")


def result_to_json(result: ScaledHatBuildResult) -> dict[str, object]:
    """把生成结果转换为便于审计的 JSON。"""
    document = asdict(result)
    document["output_path"] = str(result.output_path)
    return document


def build_all_scaled_hat_mods(
    game_dir: Path,
    output_dir: Path,
) -> list[ScaledHatBuildResult]:
    """生成全部互斥缩放比例测试包。"""
    return [
        build_scaled_hat_mod(
            game_dir,
            output_dir / preset.output_filename,
            preset.key,
        )
        for preset in SCALE_PRESETS
    ]


def main() -> int:
    """解析游戏目录、输出路径与缩放预设。"""
    parser = argparse.ArgumentParser(description="生成战场之光白鸦巫师帽缩小测试包")
    parser.add_argument("game_dir", type=Path, help="Crimson Desert 游戏根目录")
    parser.add_argument("output", type=Path, help="单包输出路径或 --all 输出目录")
    parser.add_argument(
        "--preset",
        choices=tuple(SCALE_PRESETS_BY_KEY),
        default="85",
        help="单包缩放比例，默认 85",
    )
    parser.add_argument("--all", action="store_true", help="生成 80/85/90 三个互斥版本")
    args = parser.parse_args()
    if args.all:
        results = build_all_scaled_hat_mods(args.game_dir, args.output)
        print(
            json.dumps(
                [result_to_json(result) for result in results],
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    result = build_scaled_hat_mod(args.game_dir, args.output, args.preset)
    print(json.dumps(result_to_json(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
