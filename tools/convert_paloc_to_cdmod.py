"""将 PALOC 整表替换转换为安全的 ``.cdmod`` 本地化差异包。"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from cdmm.services.cdmod_localization_converter import (
    convert_paloc_to_cdmod,
    localization_result_to_json,
)
from cdmm.services.json_loader import extract_plaintext
from cdmm.services.pamt_index_service import get_game_pamt_index, register_game_pamt_targets


def main() -> int:
    """解析命令行并执行 PALOC 转换。"""
    parser = argparse.ArgumentParser(description="将 PALOC 整表替换转换为 cdmod")
    parser.add_argument("modified", type=Path, help="模组提供的 PALOC")
    parser.add_argument("output", type=Path, help="输出 .cdmod 文件")
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--game-dir", type=Path, help="游戏根目录，自动读取最新原版")
    source_group.add_argument("--vanilla", type=Path, help="已经解包的原版 PALOC 明文")
    parser.add_argument("--target", help="最终游戏 entry 路径")
    parser.add_argument("--value-contains", help="仅转换模组值包含该文本的差异")
    parser.add_argument("--append-suffix", help="把改动表达为对当前语言原文追加后缀")
    parser.add_argument("--all-languages", action="store_true", help="对所有已安装语言 PALOC 生效")
    parser.add_argument("--name", help="模组显示名称")
    parser.add_argument("--version", default="1.0.0", help="模组版本")
    parser.add_argument("--author", default="unknown", help="作者")
    parser.add_argument("--description", default="", help="说明")
    args = parser.parse_args()
    target = args.target or f"gamedata/{args.modified.name}"
    with tempfile.TemporaryDirectory(prefix="cdmod-paloc-") as temp_dir:
        vanilla_path, vanilla_label = _resolve_vanilla(
            args.vanilla,
            args.game_dir,
            target,
            Path(temp_dir),
        )
        result = convert_paloc_to_cdmod(
            args.modified,
            vanilla_path,
            args.output,
            target=target,
            value_contains=args.value_contains,
            name=args.name,
            version=args.version,
            author=args.author,
            description=args.description,
            vanilla_source_label=vanilla_label,
            append_suffix=args.append_suffix,
            all_languages=args.all_languages,
        )
    print(json.dumps(localization_result_to_json(result), ensure_ascii=False, indent=2))
    return 0


def _resolve_vanilla(
    vanilla_path: Path | None,
    game_dir: Path | None,
    target: str,
    temp_dir: Path,
) -> tuple[Path, str]:
    """读取显式明文，或从游戏最新 PAZ 自动提取 PALOC。"""
    if vanilla_path is not None:
        return vanilla_path, str(vanilla_path.resolve())
    if game_dir is None:
        raise ValueError("必须提供 --game-dir 或 --vanilla")
    game_dir = game_dir.resolve()
    register_game_pamt_targets(game_dir, [target])
    entry = get_game_pamt_index(game_dir).find_best(
        target,
        suffix=".paloc",
        require_unique_best=False,
    )
    if entry is None:
        raise ValueError(f"游戏 PAMT 中未找到 PALOC：{target}")
    content, _detected_entry = extract_plaintext(entry)
    extracted = temp_dir / Path(entry.path).name
    extracted.write_bytes(content)
    return extracted, f"{entry.path} ({Path(entry.paz_file).parent.name}/0.paz)"


if __name__ == "__main__":
    raise SystemExit(main())
