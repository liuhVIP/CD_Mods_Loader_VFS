"""生成完全隐藏飞行披风、保留原生动画与粒子 FX 的 ``.cdmod``。

成品只在两个男性飞行披风原始 prefab 内关闭四个 ``SkinnedMeshComponent``，
不替换 prefab、不删除组件，也不修改 PAA 动画、PAAC 动作图、PAC 或 DDS。
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from cdmm.services.cdmod_converter import (
    CDMOD_FORMAT_NAME,
    CDMOD_FORMAT_VERSION,
    CDMOD_MANIFEST_PATH,
    CDMOD_REPORT_PATH,
    CDMOD_RESOURCE_TRANSFORM_COMPONENT_TYPE,
    _write_cdmod_zip,
)

# 资源变换组件在 cdmod 内的固定路径。
RESOURCE_PATCH_PATH = "patches/resources.json"

# 两个目标内 SkinnedMeshComponent 记录的精确前缀及当前游戏预期命中数。
# 记录前两个字节是组件类型索引，第六个字节是首字段 _isEnable 的布尔值。
CLOAK_PREFAB_PATCHES = (
    (
        "character/cd_phm_00_cloak_flight_0001.prefab",
        ((bytes.fromhex("03 00 28 08 00 01"), bytes.fromhex("03 00 28 08 00 00"), 2),),
    ),
    (
        "character/cd_phm_00_cloak_flight_0001_index01.prefab",
        (
            (bytes.fromhex("03 00 68 10 00 01"), bytes.fromhex("03 00 68 10 00 00"), 1),
            (bytes.fromhex("03 00 68 00 00 01"), bytes.fromhex("03 00 68 00 00 00"), 1),
        ),
    ),
)

# 两个飞行披风 prefab 分别覆盖普通与 index01 选择路径。
CLOAK_PREFAB_TARGETS = tuple(target for target, _replacements in CLOAK_PREFAB_PATCHES)

# 当前游戏两个 prefab 合计包含四个需要关闭的实体网格组件。
EXPECTED_DISABLED_COMPONENT_COUNT = 4

# 动作图内独立原生 FX 不在本模组目标范围内，运行时继续保留。
PRESERVED_NATIVE_FX = (
    "baseseq/gamesystemfx/effect/cdfx_action_cloakwing_start_001a",
    "baseseq/gamesystemfx/effect/cdfx_post_cloack_start_001a",
)


@dataclass(frozen=True)
class FullyHiddenFlightCloakBuildResult:
    """完全隐藏披风版本生成摘要。"""

    output_path: Path
    package_sha256: str
    prefab_target_count: int
    disabled_component_count: int
    animation_target_count: int
    actionchart_target_count: int


def build_fully_hidden_flight_cloak_mod(
    output_path: Path,
) -> FullyHiddenFlightCloakBuildResult:
    """生成只关闭原始 prefab 网格组件的等长资源变换包。"""
    output_path = output_path.resolve()
    operations = [
        {
            "op": "replace-bytes",
            "target": target,
            "target_pamt_dir": "0009",
            "replacements": [
                {"old_hex": old.hex(), "new_hex": new.hex()}
                for old, new, _expected_count in replacements
            ],
        }
        for target, replacements in CLOAK_PREFAB_PATCHES
    ]
    patch_document = {"schema": 1, "operations": operations}
    manifest_document = {
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": "cdmm-fully-hidden-flight-cloak-disable-skinned-mesh",
        "name": "Fully Hidden Flight Cloak - Native Particles",
        "version": "1.3",
        "author": "cdmm",
        "description": (
            "Disables the four original male flight-cloak SkinnedMeshComponents in place "
            "while preserving prefab structure, animations, action charts, PAC paths, and native FX."
        ),
        "dependencies": [],
        "source": {
            "format": "current-game-prefab-equal-length-is-enable-patch",
            "expected_disabled_components": EXPECTED_DISABLED_COMPONENT_COUNT,
        },
        "components": [
            {
                "type": CDMOD_RESOURCE_TRANSFORM_COMPONENT_TYPE,
                "path": RESOURCE_PATCH_PATH,
                "operation_count": len(operations),
            }
        ],
    }
    report_document = {
        "schema": 1,
        "summary": {
            "prefab_target_count": len(CLOAK_PREFAB_TARGETS),
            "disabled_component_count": EXPECTED_DISABLED_COMPONENT_COUNT,
            "animation_target_count": 0,
            "actionchart_target_count": 0,
            "dds_target_count": 0,
        },
        "behavior": {
            "cloak_prefab": "original structure preserved; four SkinnedMeshComponent _isEnable values set false",
            "native_fx": list(PRESERVED_NATIVE_FX),
            "animation_policy": "unchanged",
            "actionchart_policy": "unchanged",
        },
        "safety": {
            "source_policy": "patch current target bytes from current 0009 PAMT",
            "layout_policy": "equal-length six-byte component signatures only",
            "target_policy": "reject when all expected old/new signatures are absent",
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_cdmod_zip(
        output_path,
        {
            CDMOD_MANIFEST_PATH: manifest_document,
            RESOURCE_PATCH_PATH: patch_document,
            CDMOD_REPORT_PATH: report_document,
        },
    )
    return FullyHiddenFlightCloakBuildResult(
        output_path=output_path,
        package_sha256=hashlib.sha256(output_path.read_bytes()).hexdigest(),
        prefab_target_count=len(CLOAK_PREFAB_TARGETS),
        disabled_component_count=EXPECTED_DISABLED_COMPONENT_COUNT,
        animation_target_count=0,
        actionchart_target_count=0,
    )


def result_to_json(result: FullyHiddenFlightCloakBuildResult) -> dict[str, object]:
    """把生成摘要转换为命令行 JSON。"""
    payload = asdict(result)
    payload["output_path"] = str(result.output_path)
    return payload


def main() -> int:
    """解析输出路径并生成完全隐藏披风版本。"""
    parser = argparse.ArgumentParser(description="生成空 prefab 完全隐藏披风 cdmod")
    parser.add_argument("output", type=Path, help="输出 .cdmod 路径")
    args = parser.parse_args()
    result = build_fully_hidden_flight_cloak_mod(args.output)
    print(json.dumps(result_to_json(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
