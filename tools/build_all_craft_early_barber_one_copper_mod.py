"""把 All Craft Material + All Gear + All Dye - Early Barber 重建为 1 铜币 cdmod。

生成器以当前游戏 StoreInfo 为原版基线，只修改来源模组实际改动过的商店。
商品记录保持原长度，仅把两项 64 位价格值改为 1；ItemInfo 只转换贡献
购买价格结构中的货币 key，来源 PAZ/PAMT 始终只读。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from dataclasses import asdict, dataclass
from pathlib import Path

from cdmm.archive.pamt import parse_pamt, parse_pamt_filtered
from cdmm.archive.paz_crypto import decrypt, encrypt
from cdmm.services.cdmod_converter import (
    CDMOD_FORMAT_NAME,
    CDMOD_FORMAT_VERSION,
    CDMOD_LEGACY_JSON_COMPONENT_TYPE,
    CDMOD_MANIFEST_PATH,
    CDMOD_REPORT_PATH,
    CDMOD_STANDALONE_COMPONENT_TYPE,
    _write_cdmod_zip,
)
from cdmm.services.json_loader import extract_plaintext
from cdmm.services.pab_table_service import parse_pabgh_index
from cdmm.services.pab_table_service import parse_entry_name_end

# 当前生成器适配的游戏数据布局版本。
DEFAULT_GAME_VERSION = "1.17.00"
# 新模组默认文件名，版本号由命令行的 game-version 派生。
OUTPUT_NAME_TEMPLATE = (
    "All Craft Material All Gear All Dye - Early Barber - All Items 1 Copper-{version}.cdmod"
)
# 仅转换信誉商品货币时使用的默认文件名，避免误解为普通商店全商品改价。
CONTRIBUTION_ONLY_OUTPUT_NAME_TEMPLATE = "Contribution Currency To Copper-{version}.cdmod"
# 来源模组中三个 standalone 的职责分别为 StoreInfo、StageInfo 和 MissionInfo。
SOURCE_ARCHIVE_DIRS = ("0036", "0037", "0038")
# StoreInfo 正文与索引的固定目标文件名。
STOREINFO_BODY_NAME = "storeinfo.pabgb"
STOREINFO_HEADER_NAME = "storeinfo.pabgh"
# ItemInfo 货币转换组件使用的当前原版表文件名。
ITEMINFO_BODY_NAME = "iteminfo.pabgb"
ITEMINFO_HEADER_NAME = "iteminfo.pabgh"
# 旧贡献购买补丁中已确认的六种贡献货币/物品 key。
CONTRIBUTION_CURRENCY_KEYS = (
    1_001_754,
    1_001_753,
    1_003_749,
    1_000_658,
    1_001_759,
    1_001_755,
)
# 铜币在 ItemInfo 价格结构中的 key。
COPPER_CURRENCY_KEY = 1
# cdmod 内贡献货币转换组件的固定路径。
CONTRIBUTION_PATCH_PATH = "patches/contribution-currency-to-copper.json"
# 1.17 StockData 固定区域长度及关键字段相对偏移。
STOCK_FIXED_SIZE = 118
STOCK_CONST_OFFSET = 42
STOCK_ITEM_KEY_OFFSET = 43
STOCK_PRICE_PAIR_OFFSET = 2
STOCK_OPTIONAL_OFFSET = STOCK_FIXED_SIZE
# 完整 stock list 后是当前 1.17 StoreInfo 的固定 17 字节尾部字段。
STOREINFO_TAIL_SIZE = 17
# 两个 64 位价格都改为 1，保持记录长度不变。
ONE_COPPER_PRICE_PAIR = struct.pack("<QQ", 1, 1)


@dataclass(frozen=True)
class StorePatchSummary:
    """单个商店的等长改价结果。"""

    name: str
    key: int
    stock_count: int
    unique_item_count: int


@dataclass(frozen=True)
class BuildResult:
    """一次 cdmod 构建的摘要。"""

    output_path: Path
    package_sha256: str
    changed_store_count: int
    stock_record_count: int
    unique_item_count: int
    contribution_currency_change_count: int
    ignored_non_price_reference_count: int


def build_one_copper_cdmod(
    source_dir: Path,
    game_dir: Path,
    output_path: Path,
    game_version: str = DEFAULT_GAME_VERSION,
) -> BuildResult:
    """从 3.7 Early Barber 来源包生成自包含的 1 铜币 cdmod。"""
    source_dir = source_dir.resolve()
    game_dir = game_dir.resolve()
    output_path = output_path.resolve()
    _validate_source(source_dir)

    source_body, source_header, source_body_entry = _extract_source_storeinfo(source_dir)
    vanilla_body, vanilla_header = _extract_vanilla_storeinfo(game_dir)
    vanilla_item_body, vanilla_item_header = _extract_vanilla_iteminfo(game_dir)
    source_entries = _parse_table_entries(source_body, source_header)
    vanilla_entries = _parse_table_entries(vanilla_body, vanilla_header)

    changed_names = [
        name
        for name, source_entry in source_entries.items()
        if name in vanilla_entries
        and source_body[source_entry[1]:source_entry[2]]
        != vanilla_body[vanilla_entries[name][1]:vanilla_entries[name][2]]
    ]
    if not changed_names:
        raise ValueError("来源 StoreInfo 与当前原版没有差异，无法识别模组商品商店")

    patched_body = bytearray(source_body)
    summaries: list[StorePatchSummary] = []
    all_item_keys: set[int] = set()
    for name in changed_names:
        store_key, entry_start, entry_end = source_entries[name]
        entry_bytes = source_body[entry_start:entry_end]
        record_offsets = _locate_stock_record_chain(entry_bytes, store_key)
        item_keys: set[int] = set()
        for relative_offset in record_offsets:
            item_key = struct.unpack_from(
                "<I",
                entry_bytes,
                relative_offset + STOCK_ITEM_KEY_OFFSET,
            )[0]
            item_keys.add(item_key)
            absolute_price_offset = (
                entry_start + relative_offset + STOCK_PRICE_PAIR_OFFSET
            )
            patched_body[
                absolute_price_offset:absolute_price_offset + len(ONE_COPPER_PRICE_PAIR)
            ] = ONE_COPPER_PRICE_PAIR
        all_item_keys.update(item_keys)
        summaries.append(
            StorePatchSummary(name, store_key, len(record_offsets), len(item_keys))
        )

    patched_paz = _replace_encrypted_storeinfo(
        source_dir / "0036" / "0.paz",
        source_body_entry,
        bytes(patched_body),
    )
    documents, components = _build_archive_documents(source_dir, patched_paz)
    (
        contribution_patch,
        currency_change_count,
        currency_record_count,
        ignored_reference_count,
    ) = (
        _build_contribution_currency_patch(vanilla_item_body, vanilla_item_header)
    )
    documents[CONTRIBUTION_PATCH_PATH] = contribution_patch
    components.append(
        {
            "type": CDMOD_LEGACY_JSON_COMPONENT_TYPE,
            "path": CONTRIBUTION_PATCH_PATH,
        }
    )
    source_hashes = {
        archive_dir: {
            "paz_sha256": _sha256(source_dir / archive_dir / "0.paz"),
            "pamt_sha256": _sha256(source_dir / archive_dir / "0.pamt"),
        }
        for archive_dir in SOURCE_ARCHIVE_DIRS
    }
    manifest = {
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": "all-craft-material-all-gear-all-dye-early-barber-one-copper",
        "name": "All Craft Material + All Gear + All Dye - Early Barber - All Items 1 Copper",
        "version": game_version,
        "author": "Codex custom build; derived from namvn's 3.7 mod",
        "description": (
            "Self-contained 1.17 build of All Craft Material + All Gear + All Dye "
            "and Early Barber. Every stock record in the stores changed by the source "
            "mod has both buy/sell price values set to 1, and recognized contribution "
            "purchase-price fields are converted to copper key 1. Non-price item "
            "references are preserved."
        ),
        "dependencies": [],
        "source": {
            "format": "standalone-equal-length-encrypted-storeinfo-patch",
            "game_version": game_version,
            "archives": source_hashes,
        },
        "components": components,
    }
    report = {
        "schema": 1,
        "game_version": game_version,
        "summary": {
            "changed_store_count": len(summaries),
            "stock_record_count": sum(item.stock_count for item in summaries),
            "unique_item_count": len(all_item_keys),
            "contribution_currency_change_count": currency_change_count,
            "contribution_currency_record_count": currency_record_count,
            "ignored_non_price_reference_count": ignored_reference_count,
            "source_storeinfo_size": len(source_body),
            "patched_storeinfo_size": len(patched_body),
        },
        "stores": [asdict(item) for item in summaries],
        "safety": {
            "source_archives_unchanged": True,
            "storeinfo_length_unchanged": len(source_body) == len(patched_body),
            "pamt_unchanged": True,
            "patched_field": "StockData price pair (u64, u64)",
            "contribution_currency_keys": list(CONTRIBUTION_CURRENCY_KEYS),
            "copper_currency_key": COPPER_CURRENCY_KEY,
        },
    }
    documents[CDMOD_MANIFEST_PATH] = manifest
    documents[CDMOD_REPORT_PATH] = report
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_cdmod_zip(output_path, documents)
    return BuildResult(
        output_path=output_path,
        package_sha256=_sha256(output_path),
        changed_store_count=len(summaries),
        stock_record_count=sum(item.stock_count for item in summaries),
        unique_item_count=len(all_item_keys),
        contribution_currency_change_count=currency_change_count,
        ignored_non_price_reference_count=ignored_reference_count,
    )


def build_contribution_currency_cdmod(
    game_dir: Path,
    output_path: Path,
    game_version: str = DEFAULT_GAME_VERSION,
) -> BuildResult:
    """生成仅将信誉商品购买货币转换为铜币的独立 cdmod。"""
    game_dir = game_dir.resolve()
    output_path = output_path.resolve()
    vanilla_item_body, vanilla_item_header = _extract_vanilla_iteminfo(game_dir)
    (
        contribution_patch,
        currency_change_count,
        currency_record_count,
        ignored_reference_count,
    ) = _build_contribution_currency_patch(vanilla_item_body, vanilla_item_header)
    documents: dict[str, dict[str, object] | bytes] = {
        CONTRIBUTION_PATCH_PATH: contribution_patch,
        CDMOD_MANIFEST_PATH: {
            "format": CDMOD_FORMAT_NAME,
            "format_version": CDMOD_FORMAT_VERSION,
            "id": "contribution-currency-to-copper",
            "name": "Contribution Currency To Copper",
            "version": game_version,
            "author": "Codex custom build",
            "description": (
                "Converts verified contribution-item purchase currency fields to "
                "copper key 1 while preserving non-price item references."
            ),
            "dependencies": [],
            "source": {
                "format": "legacy-byte-patch",
                "game_version": game_version,
            },
            "components": [
                {
                    "type": CDMOD_LEGACY_JSON_COMPONENT_TYPE,
                    "path": CONTRIBUTION_PATCH_PATH,
                }
            ],
        },
        CDMOD_REPORT_PATH: {
            "schema": 1,
            "game_version": game_version,
            "summary": {
                "contribution_currency_change_count": currency_change_count,
                "contribution_currency_record_count": currency_record_count,
                "ignored_non_price_reference_count": ignored_reference_count,
            },
            "safety": {
                "standalone_archives_included": False,
                "contribution_currency_keys": list(CONTRIBUTION_CURRENCY_KEYS),
                "copper_currency_key": COPPER_CURRENCY_KEY,
            },
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_cdmod_zip(output_path, documents)
    return BuildResult(
        output_path=output_path,
        package_sha256=_sha256(output_path),
        changed_store_count=0,
        stock_record_count=0,
        unique_item_count=0,
        contribution_currency_change_count=currency_change_count,
        ignored_non_price_reference_count=ignored_reference_count,
    )


def _validate_source(source_dir: Path) -> None:
    """确认三个来源 standalone 均完整存在。"""
    missing = [
        str(source_dir / archive_dir / file_name)
        for archive_dir in SOURCE_ARCHIVE_DIRS
        for file_name in ("0.paz", "0.pamt")
        if not (source_dir / archive_dir / file_name).is_file()
    ]
    if missing:
        raise FileNotFoundError(f"来源模组缺少文件：{', '.join(missing)}")


def _extract_source_storeinfo(source_dir: Path) -> tuple[bytes, bytes, object]:
    """从来源 0036 中读取手动 ChaCha20 解密的 StoreInfo。"""
    archive_dir = source_dir / "0036"
    entries = parse_pamt(archive_dir / "0.pamt", archive_dir)
    by_name = {Path(entry.path).name.lower(): entry for entry in entries}
    body_entry = by_name.get(STOREINFO_BODY_NAME)
    header_entry = by_name.get(STOREINFO_HEADER_NAME)
    if body_entry is None or header_entry is None:
        raise ValueError("来源 0036 未同时包含 storeinfo.pabgb/.pabgh")
    body = _read_and_decrypt_entry(body_entry)
    header = _read_and_decrypt_entry(header_entry)
    return body, header, body_entry


def _read_and_decrypt_entry(entry: object) -> bytes:
    """读取来源 standalone 的未压缩加密 entry。"""
    if entry.compression_type != 0 or entry.comp_size != entry.orig_size:
        raise ValueError(f"暂不支持压缩的来源 entry：{entry.path}")
    with Path(entry.paz_file).open("rb") as handle:
        handle.seek(entry.offset)
        raw = handle.read(entry.comp_size)
    if len(raw) != entry.comp_size:
        raise ValueError(f"来源 PAZ entry 截断：{entry.path}")
    return decrypt(raw, Path(entry.path).name)


def _extract_vanilla_storeinfo(game_dir: Path) -> tuple[bytes, bytes]:
    """从当前游戏低编号 PAMT 提取原版 StoreInfo。"""
    matches: dict[str, object] = {}
    for archive_dir in sorted(game_dir.glob("[0-9][0-9][0-9][0-9]")):
        pamt_path = archive_dir / "0.pamt"
        if not pamt_path.is_file():
            continue
        for entry in parse_pamt_filtered(
            pamt_path,
            archive_dir,
            desired_basenames={STOREINFO_BODY_NAME, STOREINFO_HEADER_NAME},
        ):
            matches.setdefault(Path(entry.path).name.lower(), entry)
    if STOREINFO_BODY_NAME not in matches or STOREINFO_HEADER_NAME not in matches:
        raise ValueError("当前游戏中找不到原版 storeinfo.pabgb/.pabgh")
    body, _ = extract_plaintext(matches[STOREINFO_BODY_NAME])
    header, _ = extract_plaintext(matches[STOREINFO_HEADER_NAME])
    return body, header


def _extract_vanilla_iteminfo(game_dir: Path) -> tuple[bytes, bytes]:
    """从当前游戏低编号 PAMT 提取原版 ItemInfo。"""
    matches: dict[str, object] = {}
    desired = {ITEMINFO_BODY_NAME, ITEMINFO_HEADER_NAME}
    for archive_dir in sorted(game_dir.glob("[0-9][0-9][0-9][0-9]")):
        pamt_path = archive_dir / "0.pamt"
        if not pamt_path.is_file():
            continue
        for entry in parse_pamt_filtered(
            pamt_path,
            archive_dir,
            desired_basenames=desired,
        ):
            matches.setdefault(Path(entry.path).name.lower(), entry)
    if ITEMINFO_BODY_NAME not in matches or ITEMINFO_HEADER_NAME not in matches:
        raise ValueError("当前游戏中找不到原版 iteminfo.pabgb/.pabgh")
    body, _ = extract_plaintext(matches[ITEMINFO_BODY_NAME])
    header, _ = extract_plaintext(matches[ITEMINFO_HEADER_NAME])
    return body, header


def _build_contribution_currency_patch(
    body: bytes,
    header: bytes,
) -> tuple[dict[str, object], int, int, int]:
    """只把 ItemInfo 价格结构中成对出现的贡献 key 重建为铜币补丁。"""
    key_size, offsets = parse_pabgh_index(header, "iteminfo")
    if key_size != 4 or not offsets:
        raise ValueError("ItemInfo PABGH 不是预期的 u32 key 布局")
    ordered = sorted(offsets.items(), key=lambda item: item[1])
    changes: list[dict[str, object]] = []
    changed_records = 0
    ignored_references = 0
    replacement = struct.pack("<I", COPPER_CURRENCY_KEY)
    for index, (_key, entry_start) in enumerate(ordered):
        entry_end = ordered[index + 1][1] if index + 1 < len(ordered) else len(body)
        parsed = parse_entry_name_end(body, entry_start, key_size)
        if parsed is None:
            raise ValueError(f"ItemInfo entry 0x{entry_start:X} 名称无法解析")
        entry_name, name_end = parsed
        record_hits: list[tuple[int, int]] = []
        for contribution_key in CONTRIBUTION_CURRENCY_KEYS:
            original = struct.pack("<I", contribution_key)
            cursor = entry_start
            while True:
                offset = body.find(original, cursor, entry_end)
                if offset < 0:
                    break
                record_hits.append((offset, contribution_key))
                cursor = offset + len(original)
        hit_set = set(record_hits)
        price_hits = [
            (offset, contribution_key)
            for offset, contribution_key in record_hits
            if (
                (offset - 16, contribution_key) in hit_set
                or (offset + 16, contribution_key) in hit_set
            )
        ]
        ignored_references += len(record_hits) - len(price_hits)
        for offset, contribution_key in price_hits:
            original = struct.pack("<I", contribution_key)
            changes.append(
                {
                    "entry": entry_name,
                    "rel_offset": offset - name_end,
                    "offset": offset,
                    "original": original.hex(),
                    "patched": replacement.hex(),
                    "label": (
                        f"{entry_name}.contribution_currency "
                        f"{contribution_key}->{COPPER_CURRENCY_KEY}"
                    ),
                    "_dynamic_entry_offset": True,
                }
            )
        if price_hits:
            changed_records += 1
    if not changes:
        raise ValueError("当前 ItemInfo 中未发现已知贡献货币 key")
    changes.sort(key=lambda item: int(item["offset"]))
    return (
        {
            "modinfo": {
                "name": "Contribution Currency To Copper - 1.17",
                "version": DEFAULT_GAME_VERSION,
                "author": "Codex custom build",
                "description": (
                    "Rebuilds contribution purchase-price fields as copper key 1 "
                    "while preserving non-price item references."
                ),
            },
            "format": 2,
            "patches": [
                {
                    "game_file": "gamedata/iteminfo.pabgb",
                    "changes": changes,
                }
            ],
        },
        len(changes),
        changed_records,
        ignored_references,
    )


def _parse_table_entries(
    body: bytes,
    header: bytes,
) -> dict[str, tuple[int, int, int]]:
    """按 PABGH 权威边界解析 StoreInfo 的名称、key 和范围。"""
    key_size, offsets = parse_pabgh_index(header, "storeinfo")
    if key_size != 2 or not offsets:
        raise ValueError("StoreInfo PABGH 不是预期的 u16 key 布局")
    ordered = sorted(offsets.items(), key=lambda item: item[1])
    result: dict[str, tuple[int, int, int]] = {}
    for index, (key, entry_start) in enumerate(ordered):
        entry_end = ordered[index + 1][1] if index + 1 < len(ordered) else len(body)
        name_length = struct.unpack_from("<I", body, entry_start + key_size)[0]
        name_start = entry_start + key_size + 4
        name_end = name_start + name_length
        if name_end > entry_end:
            raise ValueError(f"StoreInfo entry {key} 名称越过记录边界")
        name = body[name_start:name_end].decode("utf-8")
        if name in result:
            raise ValueError(f"StoreInfo 存在重复名称：{name}")
        result[name] = (key, entry_start, entry_end)
    return result


def _locate_stock_record_chain(entry: bytes, store_key: int) -> list[int]:
    """严格定位 1.17 StockData 连续链，并用链前 count 反向验真。"""
    valid_records: dict[int, int] = {}
    for offset in range(4, len(entry) - 122):
        if struct.unpack_from("<H", entry, offset)[0] != store_key:
            continue
        if entry[offset + STOCK_CONST_OFFSET] != 1:
            continue
        item_key = struct.unpack_from("<I", entry, offset + STOCK_ITEM_KEY_OFFSET)[0]
        if item_key == 0:
            continue
        cursor = offset + STOCK_OPTIONAL_OFFSET
        optional_flag = entry[cursor]
        cursor += 1
        if optional_flag == 1:
            cursor += 13
        elif optional_flag != 0:
            continue
        if cursor + 4 > len(entry) or struct.unpack_from("<I", entry, cursor)[0] != 0:
            continue
        valid_records[offset] = cursor + 4

    chains: list[list[int]] = []
    for start in sorted(valid_records):
        declared_count = struct.unpack_from("<I", entry, start - 4)[0]
        if not 0 < declared_count < 10000:
            continue
        chain: list[int] = []
        cursor = start
        for _ in range(declared_count):
            next_cursor = valid_records.get(cursor)
            if next_cursor is None:
                break
            chain.append(cursor)
            cursor = next_cursor
        if (
            len(chain) == declared_count
            and cursor == len(entry) - STOREINFO_TAIL_SIZE
        ):
            chains.append(chain)
    if len(chains) != 1:
        raise ValueError(
            f"store key={store_key} 的 StockData 链未唯一定位：候选 {len(chains)}"
        )
    return chains[0]


def _replace_encrypted_storeinfo(
    paz_path: Path,
    body_entry: object,
    patched_body: bytes,
) -> bytes:
    """把等长明文重新加密后写入 PAZ 的内存副本。"""
    if len(patched_body) != body_entry.orig_size:
        raise ValueError("StoreInfo 改价后长度变化，拒绝重建来源 PAZ")
    encrypted = encrypt(patched_body, Path(body_entry.path).name)
    if len(encrypted) != body_entry.comp_size:
        raise ValueError("StoreInfo 重加密长度与 PAMT 不一致")
    output = bytearray(paz_path.read_bytes())
    start = body_entry.offset
    end = start + body_entry.comp_size
    if end > len(output):
        raise ValueError("StoreInfo entry 越过来源 PAZ 边界")
    output[start:end] = encrypted
    return bytes(output)


def _build_archive_documents(
    source_dir: Path,
    patched_storeinfo_paz: bytes,
) -> tuple[dict[str, dict[str, object] | bytes], list[dict[str, str]]]:
    """构造三个 standalone 组件及其载荷文档。"""
    documents: dict[str, dict[str, object] | bytes] = {}
    components: list[dict[str, str]] = []
    for index, archive_dir in enumerate(SOURCE_ARCHIVE_DIRS):
        base = f"archives/{index:03d}"
        descriptor_path = f"{base}/archive.json"
        paz_path = f"{base}/0.paz"
        pamt_path = f"{base}/0.pamt"
        paz = (
            patched_storeinfo_paz
            if archive_dir == "0036"
            else (source_dir / archive_dir / "0.paz").read_bytes()
        )
        pamt = (source_dir / archive_dir / "0.pamt").read_bytes()
        documents[paz_path] = paz
        documents[pamt_path] = pamt
        documents[descriptor_path] = {
            "schema": 1,
            "name": archive_dir,
            "paz": paz_path,
            "pamt": pamt_path,
            "paz_sha256": hashlib.sha256(paz).hexdigest(),
            "pamt_sha256": hashlib.sha256(pamt).hexdigest(),
        }
        components.append(
            {"type": CDMOD_STANDALONE_COMPONENT_TYPE, "path": descriptor_path}
        )
    return documents, components


def _sha256(path: Path) -> str:
    """计算文件 SHA-256。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_parser() -> argparse.ArgumentParser:
    """创建命令行参数解析器。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, help="3.7 Early Barber 模组目录")
    parser.add_argument("--game-dir", required=True, type=Path, help="Crimson Desert 游戏根目录")
    parser.add_argument("--output", type=Path, help="输出 cdmod 路径；默认写入游戏 mods")
    parser.add_argument("--game-version", default=DEFAULT_GAME_VERSION, help="写入成品的游戏版本")
    parser.add_argument(
        "--contribution-only",
        action="store_true",
        help="只生成信誉商品铜币购买补丁，不包含商店 standalone archive",
    )
    return parser


def main() -> int:
    """执行生成器并输出机器可读摘要。"""
    parser = _build_parser()
    args = parser.parse_args()
    output = args.output or (
        args.game_dir
        / "mods"
        / (
            CONTRIBUTION_ONLY_OUTPUT_NAME_TEMPLATE
            if args.contribution_only
            else OUTPUT_NAME_TEMPLATE
        ).format(version=args.game_version)
    )
    if args.contribution_only:
        result = build_contribution_currency_cdmod(
            args.game_dir,
            output,
            args.game_version,
        )
    else:
        if args.source_dir is None:
            parser.error("未指定 --contribution-only 时必须提供 --source-dir")
        result = build_one_copper_cdmod(
            args.source_dir,
            args.game_dir,
            output,
            args.game_version,
        )
    payload = asdict(result)
    payload["output_path"] = str(result.output_path)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
