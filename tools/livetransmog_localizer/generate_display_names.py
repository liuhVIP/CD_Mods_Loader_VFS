"""为 CrimsonDesertLiveTransmog_display_names.tsv 生成游戏原版名称显示表。

Live Transmog 的 TSV 第一列是 iteminfo 的内部 string_key（如 Aant_PlateArmor_Helm），
第二列是显示名。本脚本按 string_key 从当前游戏版本的原版 iteminfo 与简体中文 PALOC
中提取官方中文名，替换 TSV 第二列；无官方名称的条目（QA/开发者物品等）必须
使用已有的中文回退名，禁止把英文显示名直接带入成品。

只允许替换已存在的 key，禁止新增或删除行；行顺序、性别列、换行格式全部保留。
生成前锁定游戏 EXE 与表哈希，游戏更新后哈希不匹配会直接拒绝，避免静默错位。

用法：
  python tools/livetransmog_localizer/generate_display_names.py --game-dir <游戏根目录> --tsv-in <输入> --tsv-out <输出>
"""

from __future__ import annotations

import argparse
import hashlib
import struct
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT.parent))

from cdmm.archive.pamt import parse_pamt_filtered  # noqa: E402
from cdmm.services.json_loader import extract_plaintext  # noqa: E402
from cdmm.services.pab_table_service import parse_pabgh_index  # noqa: E402
from cdmm.services.paloc import parse_paloc  # noqa: E402

# 当前受支持游戏版本（Crimson Desert 1.18.01）的锁定哈希；任一漂移都必须重新分析。
EXPECTED_GAME_EXE_SHA256 = (
    "B596A498701DFCDC49C486D890C42755DABC8C174314C7F26F7329394452446D"
)
EXPECTED_ASSET_SHA256 = {
    "iteminfo.pabgb": "51F87FB41046C1D8DE9F84DE6F11E51BA2A837205F121FA5825552C2E6948746",
    "iteminfo.pabgh": "2621A26D3432C02DE4692361EBA6F437B7B16D2233A6131EAD280265FC52D627",
    "localizationstring_zho-cn.paloc": (
        "B8F209C4AF224E4BCF103961BA72EDB8E8722DCAA8B9D04A9CB234874EFC04DF"
    ),
}

EXPECTED_ITEM_ROWS = 6810


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _validate_game_executable(game_dir: Path) -> None:
    executable = game_dir / "bin64" / "CrimsonDesert.exe"
    if not executable.is_file():
        raise FileNotFoundError(f"缺少游戏主程序：{executable}")
    actual = _sha256(executable)
    if actual != EXPECTED_GAME_EXE_SHA256:
        raise ValueError(f"游戏主程序哈希不匹配：{actual}")


def _extract_from_pamt(pamt_path: Path, basenames: set[str]) -> dict[str, bytes]:
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


def _extract_required_assets(game_dir: Path) -> dict[str, bytes]:
    data_assets = _extract_from_pamt(
        game_dir / "0008" / "0.pamt",
        {"iteminfo.pabgb", "iteminfo.pabgh"},
    )
    localization_assets = _extract_from_pamt(
        game_dir / "0032" / "0.pamt",
        {"localizationstring_zho-cn.paloc"},
    )
    return {**data_assets, **localization_assets}


def _validate_asset_hashes(assets: dict[str, bytes]) -> None:
    for name, expected in EXPECTED_ASSET_SHA256.items():
        actual = hashlib.sha256(assets[name]).hexdigest().upper()
        if actual != expected:
            raise ValueError(f"原版资源哈希不匹配：{name} / {actual}")


def _read_u32(data: memoryview, cursor: int, label: str) -> int:
    if cursor + 4 > len(data):
        raise ValueError(f"{label} 越界")
    return struct.unpack_from("<I", data, cursor)[0]


def _build_item_name_map(body: bytes, header: bytes, localization: dict) -> dict[str, str]:
    """返回 iteminfo string_key -> 官方简中名称；无官方文本的 key 不收录。"""
    key_size, offsets = parse_pabgh_index(header, "iteminfo")
    if key_size not in (2, 4) or not offsets:
        raise ValueError("无法解析 iteminfo.pabgh")
    ordered = sorted(offsets.values())
    bounds = [
        (offset, ordered[index + 1] if index + 1 < len(ordered) else len(body))
        for index, offset in enumerate(ordered)
    ]
    if len(bounds) != EXPECTED_ITEM_ROWS:
        raise ValueError(f"iteminfo 行数不匹配：{len(bounds)} != {EXPECTED_ITEM_ROWS}")
    result: dict[str, str] = {}
    for row, (start, end) in enumerate(bounds):
        record = memoryview(body)[start:end]
        cursor = key_size
        name_length = _read_u32(record, cursor, f"ItemInfo row={row} string_key 长度")
        cursor += 4
        string_key = bytes(record[cursor : cursor + name_length]).decode("utf-8")
        cursor += name_length + 1 + 8
        category = record[cursor]
        localization_index = struct.unpack_from("<Q", record, cursor + 1)[0]
        if category != 7:
            raise ValueError(f"ItemInfo row={row} 本地化分类异常：{category}")
        localized = localization.get(str(localization_index))
        if localized is not None and localized.value:
            result[string_key] = localized.value
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 Live Transmog 游戏原版名称 TSV")
    parser.add_argument("--game-dir", required=True, type=Path, help="当前游戏根目录")
    parser.add_argument("--tsv-in", required=True, type=Path, help="输入 TSV（Live Transmog 显示名）")
    parser.add_argument("--tsv-out", required=True, type=Path, help="输出 TSV")
    args = parser.parse_args()

    game_dir = args.game_dir.resolve()
    tsv_in = args.tsv_in.resolve()
    tsv_out = args.tsv_out.resolve()

    _validate_game_executable(game_dir)
    assets = _extract_required_assets(game_dir)
    _validate_asset_hashes(assets)
    localization = parse_paloc(assets["localizationstring_zho-cn.paloc"]).by_key()
    official_names = _build_item_name_map(
        assets["iteminfo.pabgb"],
        assets["iteminfo.pabgh"],
        localization,
    )
    print(f"原版名称表：{len(official_names)} 个 string_key 有官方简中名称。")

    with open(tsv_in, encoding="utf-8", newline="") as handle:
        raw_lines = handle.read().splitlines(keepends=True)

    replaced = 0
    kept = 0
    missing = 0
    untranslated: list[str] = []
    output_lines: list[str] = []
    for line in raw_lines:
        stripped = line.rstrip("\r\n")
        if not stripped:
            output_lines.append(line)
            continue
        parts = stripped.split("\t")
        key = parts[0]
        official = official_names.get(key)
        if official is not None:
            parts[1] = official
            replaced += 1
        else:
            kept += 1
            missing += 1
            # QA/开发者条目常没有 PALOC 官方名。允许保留已经人工补好的中文回退名，
            # 但禁止静默保留英文，避免发布半汉化 TSV。
            if len(parts) < 2 or not any("\u4e00" <= char <= "\u9fff" for char in parts[1]):
                untranslated.append(key)
        output_lines.append("\t".join(parts) + line[len(stripped):])

    if untranslated:
        preview = ", ".join(untranslated[:20])
        suffix = " ..." if len(untranslated) > 20 else ""
        raise ValueError(
            f"{len(untranslated)} 个无官方名称条目仍为英文，请先补中文回退名：{preview}{suffix}"
        )

    tsv_out.parent.mkdir(parents=True, exist_ok=True)
    with open(tsv_out, "w", encoding="utf-8", newline="") as handle:
        handle.write("".join(output_lines))

    total = replaced + kept
    print(f"完成：共 {total} 行，替换为原版名称 {replaced}，保留原显示名 {kept}。")
    print(f"输出：{tsv_out}")
    if missing:
        print(f"提示：{missing} 个 key 无官方简中名称（多为 QA/开发者物品），已保留中文回退名。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
