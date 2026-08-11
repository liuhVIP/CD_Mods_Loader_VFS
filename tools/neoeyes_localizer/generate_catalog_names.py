"""为 NeoEyes 召唤目录生成游戏原版简体中文名称映射。

脚本只读取当前受支持游戏版本的 ``CharacterInfo`` 与简中 PALOC，并且只收录
目标 NeoEyes 二进制真实内嵌的角色 ID。生成结果仅用于最终 GDI+ 显示层，不会
改写 NeoEyes 的搜索、召唤或内部目录数据。
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT.parent))

from cdmm.archive.pamt import parse_pamt_filtered  # noqa: E402
from cdmm.services.characterinfo_full_parser import parse_all_entries  # noqa: E402
from cdmm.services.json_loader import extract_plaintext  # noqa: E402
from cdmm.services.paloc import parse_paloc  # noqa: E402

# NeoEyes 1.2.7 修复版对应的 Crimson Desert 1.17.00 游戏主程序哈希。
EXPECTED_GAME_EXE_SHA256 = (
    "A1DFC0329E177240A978EE4CC3D331E5DDD1903D1055787816199C559E16857C"
)

# 目录名称生成依赖的当前原版明文表哈希；漂移时必须重新分析。
EXPECTED_ASSET_SHA256 = {
    "characterinfo.pabgb": "20413EE9E180AF6F1785EA65F859C347362DC2AC74D766BABD1C7516C6329E19",
    "characterinfo.pabgh": "BCB5B4EDCFB08F859BA8B59D3201FCF16207E18A828935D81C751AC31785479F",
    "localizationstring_zho-cn.paloc": (
        "24A46A10DB7FF0DF4174E333B2CCDAC8E98E1549CC86F45C5FF335A1CF570BF3"
    ),
}

# 目标 NeoEyes 1.2.7 样本和生成结果的严格数量基线。
EXPECTED_NEOEYES_SHA256 = (
    "619FCFA0F54128227DCA152E6E36C2606C6A944DD1CBDB1567E8188CE9C17D80"
)
EXPECTED_CHARACTER_ROWS = 7_233
EXPECTED_OFFICIAL_NAMES = 7_094
EXPECTED_PALOC_ROWS = 183_624


def main() -> int:
    """校验当前游戏表和 NeoEyes 样本，再生成官方中文名称头文件。"""
    parser = argparse.ArgumentParser(description="生成 NeoEyes 原版中文角色名称映射")
    parser.add_argument("--game-dir", required=True, type=Path, help="当前游戏根目录")
    parser.add_argument("--neoeyes-sample", required=True, type=Path, help="目标 NeoEyes ASI")
    parser.add_argument("--output", required=True, type=Path, help="生成的 C++ 头文件")
    args = parser.parse_args()

    game_dir = args.game_dir.resolve()
    sample_path = args.neoeyes_sample.resolve()
    _validate_file_hash(sample_path, EXPECTED_NEOEYES_SHA256, "NeoEyes 样本")
    _validate_file_hash(
        game_dir / "bin64" / "CrimsonDesert.exe",
        EXPECTED_GAME_EXE_SHA256,
        "游戏主程序",
    )

    assets = _extract_required_assets(game_dir)
    for name, expected_hash in EXPECTED_ASSET_SHA256.items():
        actual_hash = hashlib.sha256(assets[name]).hexdigest().upper()
        if actual_hash != expected_hash:
            raise ValueError(f"原版资源哈希不匹配：{name} / {actual_hash}")

    character_rows = parse_all_entries(
        assets["characterinfo.pabgb"],
        assets["characterinfo.pabgh"],
    )
    localization = parse_paloc(assets["localizationstring_zho-cn.paloc"])
    if len(character_rows) != EXPECTED_CHARACTER_ROWS:
        raise ValueError(f"CharacterInfo 行数异常：{len(character_rows)}")
    if len(localization.records) != EXPECTED_PALOC_ROWS:
        raise ValueError(f"简中 PALOC 行数异常：{len(localization.records)}")

    sample_bytes = sample_path.read_bytes()
    localized_by_key = localization.by_key()
    official_names: dict[str, str] = {}
    for row in character_rows:
        source = str(row["name"])
        localized = localized_by_key.get(str(row["_characterName_hash"]))
        if localized is None or not localized.value.strip():
            continue
        if source.encode("utf-8") + b"\0" not in sample_bytes:
            continue
        translation = localized.value.strip()
        previous = official_names.setdefault(source, translation)
        if previous != translation:
            raise ValueError(f"同一角色 ID 对应多个官方名称：{source}")

    if len(official_names) != EXPECTED_OFFICIAL_NAMES:
        raise ValueError(f"NeoEyes 官方名称映射数量异常：{len(official_names)}")

    _write_generated_header(args.output.resolve(), official_names)
    print(
        "NeoEyes 官方目录名称生成完成："
        f"{len(official_names)}/{len(character_rows)}，"
        f"PALOC {len(localization.records)} 行"
    )
    return 0


def _validate_file_hash(path: Path, expected_hash: str, label: str) -> None:
    """校验输入文件存在且版本哈希完全匹配。"""
    if not path.is_file():
        raise FileNotFoundError(f"缺少{label}：{path}")
    actual_hash = hashlib.sha256(path.read_bytes()).hexdigest().upper()
    if actual_hash != expected_hash:
        raise ValueError(f"{label}哈希不匹配：{actual_hash}")


def _extract_required_assets(game_dir: Path) -> dict[str, bytes]:
    """从已确认的原版分包定点提取 CharacterInfo 与简中 PALOC。"""
    character_assets = _extract_from_pamt(
        game_dir / "0008" / "0.pamt",
        {"characterinfo.pabgb", "characterinfo.pabgh"},
    )
    localization_assets = _extract_from_pamt(
        game_dir / "0032" / "0.pamt",
        {"localizationstring_zho-cn.paloc"},
    )
    return {**character_assets, **localization_assets}


def _extract_from_pamt(pamt_path: Path, basenames: set[str]) -> dict[str, bytes]:
    """仅解析指定 basename，避免扫描无关游戏资源。"""
    if not pamt_path.is_file():
        raise FileNotFoundError(f"缺少原版 PAMT：{pamt_path}")
    entries = parse_pamt_filtered(pamt_path, desired_basenames=basenames)
    by_name = {Path(entry.path).name.casefold(): entry for entry in entries}
    missing = sorted(name for name in basenames if name.casefold() not in by_name)
    if missing:
        raise ValueError(f"{pamt_path} 缺少目标：{', '.join(missing)}")
    return {
        name: extract_plaintext(by_name[name.casefold()])[0]
        for name in sorted(basenames)
    }


def _write_generated_header(output_path: Path, official_names: dict[str, str]) -> None:
    """按英文 ID 排序生成可二分查找的 UTF-8 C++ 表。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "// 此文件由 generate_catalog_names.py 生成，请勿手工修改。",
        "#pragma once",
        "#include <cstddef>",
        "namespace neoeyes_cn::generated {",
        "struct CatalogName { const char* source; const char* translation; };",
        "inline constexpr CatalogName kCatalogNames[] = {",
    ]
    for source, translation in sorted(official_names.items()):
        lines.append(
            f"    {{ {_cpp_utf8_literal(source)}, {_cpp_utf8_literal(translation)} }},"
        )
    lines.extend(
        [
            "};",
            "inline constexpr std::size_t kCatalogNameCount = "
            "sizeof(kCatalogNames) / sizeof(kCatalogNames[0]);",
            "}",
        ]
    )
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _cpp_utf8_literal(value: str) -> str:
    """把 UTF-8 文本转换为不受源文件字面量边界影响的 C++ 字节串。"""
    return '"' + "".join(f"\\x{byte:02X}" for byte in value.encode("utf-8")) + '"'


if __name__ == "__main__":
    raise SystemExit(main())
