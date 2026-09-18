"""为 CrimsonDesertLiveTransmog_display_names.tsv 生成游戏原版名称显示表。

Live Transmog 的 TSV 第一列是 iteminfo 的内部 string_key（如 Aant_PlateArmor_Helm），
第二列是显示名。本脚本按 string_key 从当前游戏版本的原版 iteminfo 与简体中文
``gamedata/item.paloc`` 中提取官方中文名，只替换 TSV 第二列；无官方名称的条目
（QA/开发者物品等）必须使用中文回退名，禁止把英文显示名直接带入成品。

只允许替换已存在的 key，禁止新增或删除行；行顺序、性别列、换行格式全部保留。
生成前锁定游戏 EXE 与表哈希，游戏更新后哈希不匹配会直接拒绝，避免静默错位。

2.01.00 起归档结构变化：数据表物理名由 ``*.pabgb/*.pabgh`` 变为
``gamedata/binarystaticinfo__/bin/*.staticinfobody|*.staticinfoheader``，
本地化文本按语言目录拆成 39 个 ``gamedata/*.paloc``（简中在
``gamedata/stringtable/binary__/zho-cn``）。两种命名统一由加载器的 PAMT 逻辑
查询解析，禁止再按旧物理路径拼文件名。

用法：
  python tools/livetransmog_localizer/generate_display_names.py \
    --game-dir <游戏根目录> --tsv-in <输入> --tsv-out <输出> [--fallback-tsv <旧中文表>]
"""

from __future__ import annotations

import argparse
import hashlib
import struct
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT.parent))

from cdmm.services.json_loader import extract_plaintext  # noqa: E402
from cdmm.services.pab_table_service import parse_pabgh_index  # noqa: E402
from cdmm.services.paloc import parse_paloc  # noqa: E402
from cdmm.services.pamt_index_service import get_game_pamt_index  # noqa: E402

# 当前受支持游戏版本（Crimson Desert 2.02.00）的锁定哈希；任一漂移都必须重新分析。
EXPECTED_GAME_EXE_SHA256 = (
    "BCBF623AD5690147DC462AEAED5B4F97BD73296BA0D6AB54663586E7088B1C0E"
)
EXPECTED_ASSET_SHA256 = {
    "iteminfo.pabgb": "E646E4A0281930AEC1D6D750ACAAFE07740F60E0DF5242041FC5BF57AE7ABADE",
    "iteminfo.pabgh": "59F16D991F77876BB216C16AFFB50C3BBCBFEA9190602C9FDA1557A3E07123FE",
    "gamedata/item.paloc": "A59BE7815A1A3FF45777D9D5699BDD5BC8FBD59A700F6EA6A3BE23B00D715C00",
}

# 物品名/描述的 PALOC category（``LocalizableString.category``）。
ITEM_NAME_CATEGORY = 7

EXPECTED_ITEM_ROWS = 6813

# 简体中文语言目录（PAMT ``resolved_dir_path`` 末段）。
PALOC_LANGUAGE = "zho-cn"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def _validate_game_executable(game_dir: Path) -> None:
    executable = game_dir / "bin64" / "CrimsonDesert.exe"
    if not executable.is_file():
        raise FileNotFoundError(f"缺少游戏主程序：{executable}")
    digest = hashlib.sha256()
    with executable.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest().upper()
    if actual != EXPECTED_GAME_EXE_SHA256:
        raise ValueError(f"游戏主程序哈希不匹配：{actual}")


def _find_language_paloc(entries, language: str):
    """在 PAMT 全量 entry 里按语言目录定位 ``gamedata/item.paloc``。"""
    matches = [
        entry
        for entry in entries
        if entry.path.lower() == "gamedata/item.paloc"
        and (entry.resolved_dir_path or "").lower().rstrip("/").endswith(f"/{language}")
    ]
    if len(matches) != 1:
        raise ValueError(
            f"未能在 PAMT 中唯一定位 gamedata/item.paloc（{language}）：命中 {len(matches)} 个"
        )
    return matches[0]


def _extract_required_assets(game_dir: Path) -> dict[str, bytes]:
    """按加载器的逻辑目标解析原版表，返回 {目标名: 明文}。"""
    index = get_game_pamt_index(game_dir)
    # 先做一次全量枚举：之后 find_best 会把各编号目录按目标 basename 过滤，届时
    # 无法再列出同名的多语言 paloc。
    all_entries = [
        entry
        for dir_name, _mtime, _size in index.signature
        for entry in index.entries_in_dir(dir_name)
    ]
    body_entry = index.find_best("iteminfo.pabgb")
    header_entry = index.find_best("iteminfo.pabgh")
    if body_entry is None or header_entry is None:
        raise ValueError("未能在 PAMT 中定位 iteminfo 表体/表头")
    paloc_entry = _find_language_paloc(all_entries, PALOC_LANGUAGE)
    return {
        "iteminfo.pabgb": extract_plaintext(body_entry)[0],
        "iteminfo.pabgh": extract_plaintext(header_entry)[0],
        "gamedata/item.paloc": extract_plaintext(paloc_entry)[0],
    }


def _validate_asset_hashes(assets: dict[str, bytes]) -> None:
    for name, expected in EXPECTED_ASSET_SHA256.items():
        actual = _sha256(assets[name])
        if actual != expected:
            raise ValueError(f"原版资源哈希不匹配：{name} / {actual}")


def _read_u32(data: bytes | memoryview, cursor: int, label: str) -> int:
    if cursor + 4 > len(data):
        raise ValueError(f"{label} 越界")
    return struct.unpack_from("<I", data, cursor)[0]


def _build_item_name_map(body: bytes, header: bytes, localization: dict) -> dict[str, str]:
    """返回 iteminfo string_key -> 官方简中名称；无官方文本的 key 不收录。"""
    key_size, offsets = parse_pabgh_index(header, "iteminfo")
    if key_size not in (2, 4) or not offsets:
        raise ValueError("无法解析 iteminfo 表头索引")
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
        # PABGB entry: [entry_id:u16|u32][string_key_len:u32][string_key][u8][u64]
        #              [LocalizableString: u8 category + u64 index + CString default]
        cursor = key_size
        name_length = _read_u32(record, cursor, f"ItemInfo row={row} string_key 长度")
        cursor += 4
        string_key = bytes(record[cursor : cursor + name_length]).decode("utf-8")
        cursor += name_length + 1 + 8
        category = record[cursor]
        localization_index = struct.unpack_from("<Q", record, cursor + 1)[0]
        if category != ITEM_NAME_CATEGORY:
            raise ValueError(f"ItemInfo row={row} ({string_key}) 本地化分类异常：{category}")
        localized = localization.get(str(localization_index))
        if localized is not None and localized.value:
            result[string_key] = localized.value
    return result


def _has_chinese(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def _load_fallback_names(path: Path) -> dict[str, str]:
    """读取历史中文 TSV，仅用于无官方名称条目的中文回退名。"""
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) >= 2 and _has_chinese(parts[1]):
            result[parts[0]] = parts[1]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 Live Transmog 游戏原版名称 TSV")
    parser.add_argument("--game-dir", required=True, type=Path, help="当前游戏根目录")
    parser.add_argument("--tsv-in", required=True, type=Path, help="输入 TSV（Live Transmog 显示名）")
    parser.add_argument("--tsv-out", required=True, type=Path, help="输出 TSV")
    parser.add_argument(
        "--fallback-tsv",
        type=Path,
        default=None,
        help="历史中文 TSV：为无官方名称的条目提供中文回退名",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="可选：把名称变化明细写入该文件",
    )
    args = parser.parse_args()

    game_dir = args.game_dir.resolve()
    tsv_in = args.tsv_in.resolve()
    tsv_out = args.tsv_out.resolve()

    _validate_game_executable(game_dir)
    assets = _extract_required_assets(game_dir)
    _validate_asset_hashes(assets)
    localization = parse_paloc(assets["gamedata/item.paloc"]).by_key()
    official_names = _build_item_name_map(
        assets["iteminfo.pabgb"],
        assets["iteminfo.pabgh"],
        localization,
    )
    print(f"原版名称表：{len(official_names)} 个 string_key 有官方简中名称。")

    fallbacks: dict[str, str] = {}
    if args.fallback_tsv is not None:
        fallbacks = _load_fallback_names(args.fallback_tsv.resolve())
        print(f"中文回退表：{len(fallbacks)} 条可用回退名。")

    with open(tsv_in, encoding="utf-8", newline="") as handle:
        raw_lines = handle.read().splitlines(keepends=True)

    replaced = 0
    kept = 0
    untranslated: list[str] = []
    changes: list[tuple[str, str, str]] = []
    output_lines: list[str] = []
    for line in raw_lines:
        stripped = line.rstrip("\r\n")
        if not stripped:
            output_lines.append(line)
            continue
        parts = stripped.split("\t")
        key = parts[0]
        previous = parts[1] if len(parts) > 1 else ""
        official = official_names.get(key)
        if official is not None:
            if official != previous:
                changes.append((key, previous, official))
            parts[1] = official
            replaced += 1
        else:
            fallback = fallbacks.get(key)
            if fallback is not None:
                if fallback != previous:
                    changes.append((key, previous, fallback))
                parts[1] = fallback
            kept += 1
            # QA/开发者条目常没有 PALOC 官方名。允许保留已经人工补好的中文回退名，
            # 但禁止静默保留英文，避免发布半汉化 TSV。
            if len(parts) < 2 or not _has_chinese(parts[1]):
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

    if args.report is not None:
        report = args.report.resolve()
        report.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"{key}\t{old}\t{new}" for key, old, new in changes]
        report.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        print(f"名称变化明细（{len(changes)} 条）：{report}")

    print(f"完成：共 {replaced + kept} 行，替换为原版名称 {replaced}，保留中文回退名 {kept}。")
    print(f"输出：{tsv_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
