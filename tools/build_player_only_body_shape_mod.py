"""将大型女性体型 loose 模组筛选为仅女性玩家资源的 ``.cdmod``。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from cdmm.services.cdmod_general_loose_converter import convert_general_loose_to_cdmod
from cdmm.services.cdmod_package import load_cdmod_package

# 女性玩家资源根：德米安与性转后克里夫使用 PHW 玩家链路；NHW 女巫资源不应进入成品。
PLAYER_RESOURCE_RELATIVE_ROOT = Path("1_pc") / "2_phw"

# 编号 loose 的固定包装前缀，确保转换器把资源解析到原始 0009 PAMT。
NUMBERED_LOOSE_PREFIX = Path("0009") / "character" / "model"

# 成品只允许出现的规范游戏路径前缀。
PLAYER_TARGET_PREFIX = "character/model/1_pc/2_phw/"

# 成品元数据默认值；原模组没有作者信息，因此不能擅自署名。
DEFAULT_MOD_NAME = "Body Shape Optimization - Damian and Female Kliff Only"
DEFAULT_MOD_VERSION = "1.0-1.15.00"
DEFAULT_MOD_AUTHOR = "unknown"
DEFAULT_MOD_DESCRIPTION = (
    "Player-only repack of Body shape optimization. Includes only "
    "0009/character/model/1_pc/2_phw resources for Damian and female Kliff; "
    "excludes 3_npc/2_nhw witch resources and all 2_mon resources."
)


@dataclass(frozen=True)
class PlayerOnlyBodyBuildResult:
    """玩家专用体型包的构建与校验摘要。"""

    output_path: str
    package_sha256: str
    package_bytes: int
    source_files: int
    source_bytes: int
    replacement_files: int
    allow_new_files: int


def build_player_only_body_shape_mod(
    game_dir: Path,
    source_model_dir: Path,
    output_path: Path,
    *,
    mod_name: str = DEFAULT_MOD_NAME,
    mod_version: str = DEFAULT_MOD_VERSION,
    mod_author: str = DEFAULT_MOD_AUTHOR,
) -> PlayerOnlyBodyBuildResult:
    """筛选 ``1_pc/2_phw``，复用通用 loose 转换器生成并校验成品。"""
    game_dir = game_dir.resolve()
    source_model_dir = source_model_dir.resolve()
    output_path = output_path.resolve()
    _validate_inputs(game_dir, source_model_dir, output_path)

    player_source = source_model_dir / PLAYER_RESOURCE_RELATIVE_ROOT
    source_files = sorted(path for path in player_source.rglob("*") if path.is_file())
    if not source_files:
        raise ValueError(f"没有发现女性玩家 PAC 资源：{player_source}")
    source_bytes = sum(path.stat().st_size for path in source_files)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    # 临时目录固定放在源模组同盘，优先用硬链接避免复制约数百 MB 的 PAC。
    source_mod_root = source_model_dir.parents[2]
    with tempfile.TemporaryDirectory(
        prefix=".player-only-cdmod-",
        dir=source_mod_root,
    ) as temporary_dir:
        staging_root = Path(temporary_dir)
        staging_player_root = staging_root / NUMBERED_LOOSE_PREFIX / PLAYER_RESOURCE_RELATIVE_ROOT
        _link_player_resources(player_source, staging_player_root, source_files)
        _write_modinfo(
            staging_root,
            name=mod_name,
            version=mod_version,
            author=mod_author,
        )
        conversion = convert_general_loose_to_cdmod(game_dir, staging_root, output_path)

    if conversion.file_count != len(source_files):
        raise ValueError(
            f"转换文件数不一致：source={len(source_files)} output={conversion.file_count}"
        )
    package = load_cdmod_package(output_path)
    replacements = tuple(
        replacement
        for file_patch in package.file_patches
        for replacement in file_patch.files
    )
    _validate_replacements(replacements, len(source_files))

    return PlayerOnlyBodyBuildResult(
        output_path=str(output_path),
        package_sha256=_sha256_file(output_path),
        package_bytes=output_path.stat().st_size,
        source_files=len(source_files),
        source_bytes=source_bytes,
        replacement_files=len(replacements),
        allow_new_files=sum(1 for replacement in replacements if replacement.allow_new),
    )


def _validate_inputs(game_dir: Path, source_model_dir: Path, output_path: Path) -> None:
    """校验游戏、源目录和输出边界，避免误封装 NPC 资源或覆盖源文件。"""
    if not (game_dir / "bin64" / "CrimsonDesert.exe").is_file():
        raise ValueError(f"不是有效游戏目录：{game_dir}")
    if not source_model_dir.is_dir():
        raise ValueError(f"源 model 目录不存在：{source_model_dir}")
    normalized_tail = tuple(part.lower() for part in source_model_dir.parts[-3:])
    if normalized_tail != ("0009", "character", "model"):
        raise ValueError("源目录必须以 0009/character/model 结尾")
    if output_path.suffix.lower() != ".cdmod":
        raise ValueError("输出文件必须使用 .cdmod 后缀")
    if output_path.is_relative_to(source_model_dir):
        raise ValueError("输出文件不能写入源 model 目录")


def _link_player_resources(
    player_source: Path,
    staging_player_root: Path,
    source_files: list[Path],
) -> None:
    """保持相对目录建立硬链接；不支持硬链接时才回退为普通复制。"""
    for source_path in source_files:
        target_path = staging_player_root / source_path.relative_to(player_source)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(source_path, target_path)
        except OSError:
            shutil.copy2(source_path, target_path)


def _write_modinfo(staging_root: Path, *, name: str, version: str, author: str) -> None:
    """写入 UTF-8 元数据，使成品名称、版本和作用范围可审计。"""
    document = {
        "title": name,
        "name": name,
        "version": version,
        "author": author,
        "description": DEFAULT_MOD_DESCRIPTION,
    }
    (staging_root / "modinfo.json").write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _validate_replacements(replacements: tuple[object, ...], expected_count: int) -> None:
    """回读完整载荷，阻止任何 NPC、怪物或非 0009 资源混入。"""
    if len(replacements) != expected_count:
        raise ValueError(
            f"回读 replacement 数量不一致：expected={expected_count} actual={len(replacements)}"
        )
    invalid = [
        replacement
        for replacement in replacements
        if replacement.pamt_dir != "0009"
        or not replacement.target.startswith(PLAYER_TARGET_PREFIX)
        or "/3_npc/" in f"/{replacement.target}"
        or "/2_mon/" in f"/{replacement.target}"
    ]
    if invalid:
        preview = ", ".join(
            f"{item.pamt_dir}/{item.target}" for item in invalid[:5]
        )
        raise ValueError(f"成品混入非玩家资源：{preview}")


def _sha256_file(path: Path) -> str:
    """流式计算大型成品 SHA-256，避免再次把整个包读入内存。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def parse_args() -> argparse.Namespace:
    """解析构建参数。"""
    parser = argparse.ArgumentParser(description="生成仅德米安与性转克里夫使用的体型 .cdmod")
    parser.add_argument("--game-dir", type=Path, required=True, help="Crimson Desert 游戏根目录")
    parser.add_argument(
        "--source-model-dir",
        type=Path,
        required=True,
        help="原模组的 0009/character/model 目录",
    )
    parser.add_argument("--output", type=Path, required=True, help="输出 .cdmod 路径")
    parser.add_argument("--name", default=DEFAULT_MOD_NAME, help="manifest 模组名称")
    parser.add_argument("--version", default=DEFAULT_MOD_VERSION, help="manifest 模组版本")
    parser.add_argument("--author", default=DEFAULT_MOD_AUTHOR, help="原作者名称；未知时保持 unknown")
    return parser.parse_args()


def main() -> int:
    """执行构建并输出机器可读摘要。"""
    args = parse_args()
    result = build_player_only_body_shape_mod(
        args.game_dir,
        args.source_model_dir,
        args.output,
        mod_name=args.name,
        mod_version=args.version,
        mod_author=args.author,
    )
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
