"""批量生成九种轻量飞行披风粒子配色 ``.cdmod``。

所有版本复用已验证的轻量披风处理链：三张实体/发光 DDS 保持透明，只对
spline DDS 的原始明暗进行低亮配色，保留 MipMap、BC1 透明索引和附件生命周期。
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from cdmm.tools.build_hidden_flight_cloak_particle_mod import (
    SUBTLE_RED_PARTICLE_RGB,
    build_hidden_flight_cloak_particle_mod,
)

# 九色轻量版使用低亮粒子色，避免 spline 重新形成整片高亮披风轮廓。
SLIM_FLIGHT_CLOAK_VARIANTS = (
    ("White", (210, 210, 210)),
    ("Sunset Orange", (160, 77, 16)),
    ("Hot Pink", (160, 38, 107)),
    ("Ice Cyan", (26, 140, 160)),
    ("Royal Purple", (107, 54, 157)),
    ("Emerald Green", (25, 160, 74)),
    ("Gold Yellow", (160, 132, 16)),
    ("Sapphire Blue", (24, 74, 160)),
    ("Vivid Red", SUBTLE_RED_PARTICLE_RGB),
)


@dataclass(frozen=True)
class SlimFlightCloakCollectionResult:
    """九色轻量披风批量生成摘要。"""

    output_dir: Path
    package_count: int
    output_paths: tuple[Path, ...]


def build_slim_colored_flight_cloak_collection(
    source_dir: Path,
    output_dir: Path,
    *,
    disabled_backup: bool = False,
) -> SlimFlightCloakCollectionResult:
    """生成九个互斥轻量披风颜色包。"""
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = ".cdmod.white-backup" if disabled_backup else ".cdmod"
    output_paths: list[Path] = []
    for color_name, particle_rgb in SLIM_FLIGHT_CLOAK_VARIANTS:
        variant_name = f"Slim {color_name} Flight Cloak - Particle Accents"
        output_path = output_dir / f"{variant_name}-1.0{suffix}"
        build_hidden_flight_cloak_particle_mod(
            source_dir,
            output_path,
            particle_rgb=particle_rgb,
            variant_name=variant_name,
        )
        output_paths.append(output_path)
    return SlimFlightCloakCollectionResult(
        output_dir=output_dir,
        package_count=len(output_paths),
        output_paths=tuple(output_paths),
    )


def result_to_json(result: SlimFlightCloakCollectionResult) -> dict[str, object]:
    """把批量生成摘要转换成命令行 JSON。"""
    payload = asdict(result)
    payload["output_dir"] = str(result.output_dir)
    payload["output_paths"] = [str(path) for path in result.output_paths]
    return payload


def main() -> int:
    """解析参数并批量生成九色轻量披风。"""
    parser = argparse.ArgumentParser(description="生成九色轻量飞行披风粒子合集")
    parser.add_argument("source", type=Path, help="包含 files/0009 的白色披风参考目录")
    parser.add_argument("output_dir", type=Path, help="九个 .cdmod 的输出目录")
    parser.add_argument(
        "--disabled-backup",
        action="store_true",
        help="输出为 .cdmod.white-backup，避免游戏扫描时直接启用",
    )
    args = parser.parse_args()
    result = build_slim_colored_flight_cloak_collection(
        args.source,
        args.output_dir,
        disabled_backup=args.disabled_backup,
    )
    print(json.dumps(result_to_json(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
