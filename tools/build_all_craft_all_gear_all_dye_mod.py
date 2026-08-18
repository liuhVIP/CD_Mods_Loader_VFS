"""All Craft Material + All Gear + All Dye - Full 3.7 -> 1.18 语义重放生成器。

来源模组只携带旧完整 standalone 表（storeinfo/stageinfo），1.18 布局漂移后
整表替换会崩溃。本生成器在 1.18 原版 storeinfo 上重放「商店卖全部材料/
装备/染料」部分：把旧表里被大幅扩充的 13 个商店的 stock 链语义化重建。

机制（见 .codex/skills/crimson-desert-mod-loader/references/
all-craft-all-gear-all-dye-store-replay.md）：
- 1.17 记录 = 1.18 记录在 +84..+87 插入 4 字节 0（123/136B -> 119/132B）。
- 只提取来源商品 key 集合，每条记录一律使用 1.18 vanilla 合法字节：
  同 key 复用本商店 vanilla 记录，新 key 用 vanilla 第一条记录作模板、
  只改 item_key（+43 与 +102 dup）。
- 最终链 = 来源 key（去重、过滤死商品）+ 来源中缺失的 vanilla 商品。
- 商店头部随来源模组，但把「值 == 来源记录数」的 u32 改写为最终记录数。
- storeinfo 必须用单个整表 change + _pabgh_companion，不能用逐 entry change。
- stageinfo 用 3 条 CString entry 级 change，offset 写 vanilla 原始 start
  （不能预加前面变更的累计 delta），不挂显式 companion：pabgh 交给 loader
  自动 companion（合并全部 insert 位移），否则与 Early Barber 的整表
  companion 冲突。详见参考文档「1.18.01 实机失败记录与修复」。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

from cdmm.archive.pamt import parse_pamt, parse_pamt_filtered
from cdmm.archive.paz_crypto import decrypt
from cdmm.services.cdmod_converter import (
    CDMOD_FORMAT_NAME,
    CDMOD_FORMAT_VERSION,
    CDMOD_LEGACY_JSON_COMPONENT_TYPE,
    CDMOD_MANIFEST_PATH,
    CDMOD_REPORT_PATH,
    _write_cdmod_zip,
)
from cdmm.services.json_loader import extract_plaintext
from cdmm.services.pab_table_service import build_entry_bounds, parse_pabgh_index
from cdmm.services.pabgh_rewrite import rewrite_pabgh_offsets

DEFAULT_GAME_VERSION = "1.18.01"
SOURCE_ARCHIVE_DIR = "0036"
STOREINFO_BODY_NAME = "storeinfo.pabgb"
STOREINFO_HEADER_NAME = "storeinfo.pabgh"
ITEMINFO_BODY_NAME = "iteminfo.pabgb"
ITEMINFO_HEADER_NAME = "iteminfo.pabgh"
OUTPUT_NAME_TEMPLATE = "All Craft Material All Gear All Dye-{version}.cdmod"

# 1.18 记录长度与固定尾部（raw_list_a/b 等）。
VANILLA_RECORD_LENGTHS = (119, 132)
SOURCE_RECORD_LENGTHS = (123, 136)
STOREINFO_TAIL_SIZE = 17
# 记录内 item_key 固定位置（1.18 布局）。
ITEM_KEY_OFFSET = 43
ITEM_KEY_DUP_OFFSET = 102
# 记录数明显多于原版才视为被扩充的商店。

MIN_EXPANSION = 10

# stageinfo 商店引用重定向：旧城镇商店 NPC -> Hernand 扩充商店。
# 只重放验证过的 CString 替换，不迁移旧版 UI/CraftTool/天气字段。
STAGEINFO_REDIRECTS: dict[int, tuple[tuple[str, str], ...]] = {
    1006283: (
        ("Shop_Clothes_Varnia", "Shop_Clothes_Hernand"),
        ("Shop_Varnia_0001_Phase00_06", "Shop_Hernand_0001_Phase00_31"),
    ),
    1100210080: (
        ("Shop_Clothes_Calphade", "Shop_Clothes_Hernand"),
        ("Shop_Calphade_0001_Phase00_03", "Shop_Hernand_0001_Phase00_31"),
    ),
    1002825: (
        ("Shop_Butcher_IsvatuFortress", "Shop_Butcher_Hernand"),
        ("Shop_IsvatuFortress", "Shop_Hernand_0001_Phase00_05_sub_1_0"),
    ),
}



class StoreReplayError(ValueError):
    """重放失败：来源或原版结构不符合已验证布局。"""


def _read_and_decrypt_entry(entry: object) -> bytes:
    """读取来源 standalone 的未压缩 ChaCha20 加密 entry。"""
    if entry.compression_type != 0 or entry.comp_size != entry.orig_size:
        raise StoreReplayError(f"暂不支持压缩的来源 entry：{entry.path}")
    with Path(entry.paz_file).open("rb") as handle:
        handle.seek(entry.offset)
        raw = handle.read(entry.comp_size)
    if len(raw) != entry.comp_size:
        raise StoreReplayError(f"来源 PAZ entry 截断：{entry.path}")
    return decrypt(raw, Path(entry.path).name)


def extract_source_storeinfo(source_dir: Path) -> tuple[bytes, bytes]:
    """从来源 0036 读取解密后的 StoreInfo body/header。"""
    archive_dir = source_dir / SOURCE_ARCHIVE_DIR
    entries = parse_pamt(archive_dir / "0.pamt", archive_dir)
    by_name = {Path(entry.path).name.lower(): entry for entry in entries}
    body_entry = by_name.get(STOREINFO_BODY_NAME)
    header_entry = by_name.get(STOREINFO_HEADER_NAME)
    if body_entry is None or header_entry is None:
        raise StoreReplayError("来源 0036 未同时包含 storeinfo.pabgb/.pabgh")
    return _read_and_decrypt_entry(body_entry), _read_and_decrypt_entry(header_entry)


def extract_vanilla_storeinfo(game_dir: Path) -> tuple[bytes, bytes]:
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
        raise StoreReplayError("当前游戏中找不到原版 storeinfo.pabgb/.pabgh")
    body, _ = extract_plaintext(matches[STOREINFO_BODY_NAME])
    header, _ = extract_plaintext(matches[STOREINFO_HEADER_NAME])
    return body, header


def extract_vanilla_iteminfo(game_dir: Path) -> tuple[bytes, bytes]:
    """从当前游戏低编号 PAMT 提取原版 ItemInfo（用于商品 key 过滤）。"""
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
        raise StoreReplayError("当前游戏中找不到原版 iteminfo.pabgb/.pabgh")
    body, _ = extract_plaintext(matches[ITEMINFO_BODY_NAME])
    header, _ = extract_plaintext(matches[ITEMINFO_HEADER_NAME])
    return body, header


def parse_entries(body: bytes, header: bytes) -> dict[int, dict[str, object]]:
    """解析 StoreInfo 条目：key、范围、名称、head、链。"""
    key_size, offsets = parse_pabgh_index(header, "storeinfo")
    if key_size != 2 or not offsets:
        raise StoreReplayError("StoreInfo PABGH 不是预期的 u16 key 布局")
    bounds = build_entry_bounds(body, key_size, offsets)
    if len(bounds) != len(offsets):
        raise StoreReplayError("StoreInfo 存在无法解析名称的 entry")
    result: dict[int, dict[str, object]] = {}
    for key, (start, end, name, name_end) in bounds.items():
        result[key] = {
            "key": key,
            "name": name,
            "start": start,
            "end": end,
            "entry": body[start:end],
        }
    return result


def parse_stock_chain(
    entry: bytes,
    store_key: int,
    record_lengths: tuple[int, int],
    tail_size: int = STOREINFO_TAIL_SIZE,
) -> tuple[int, list[int], int] | None:
    """严格定位一条 stock 链：chain_start、每条记录起点、最后记录结束。

    必须按 count 精确迭代 count 条；中间记录用下一条 key 判定长度
    （119/132 或 123/136），最后一条用「剩余空间 - 尾部」判定。
    """
    found: list[tuple[int, list[int], int]] = []
    for chain_start in range(4, len(entry)):
        if chain_start + 4 > len(entry):
            break
        count = struct.unpack_from("<I", entry, chain_start - 4)[0]
        if not 0 < count < 20000:
            continue
        cursor = chain_start
        starts = [chain_start]
        ok = True
        for index in range(count):
            if index == count - 1:
                remaining = len(entry) - tail_size - cursor
                if remaining not in record_lengths:
                    ok = False
                    break
                last_end = cursor + remaining
                starts.append(last_end)
            else:
                next_starts = [
                    cursor + length
                    for length in record_lengths
                    if cursor + length + 44 <= len(entry)
                    and struct.unpack_from("<H", entry, cursor + length)[0] == store_key
                    and entry[cursor + length + 42] == 1
                    and struct.unpack_from("<I", entry, cursor + length + 43)[0] != 0
                ]
                if len(next_starts) != 1:
                    ok = False
                    break
                cursor = next_starts[0]
                starts.append(cursor)
        if ok:
            found.append((chain_start, starts, last_end))
    if len(found) != 1:
        return None
    return found[0]


def entry_item_keys(
    entries: dict[int, dict[str, object]],
    body: bytes,
    record_lengths: tuple[int, int],
) -> set[int]:
    """收集整表所有商店记录中的 item key（用于商品过滤）。"""
    keys: set[int] = set()
    for key, info in entries.items():
        entry = info["entry"]
        chain = parse_stock_chain(entry, key, record_lengths)
        if chain is None:
            continue
        _start, starts, _last_end = chain
        for record_start in starts[:-1]:
            keys.add(struct.unpack_from("<I", entry, record_start + ITEM_KEY_OFFSET)[0])
    return keys


def _record_bytes_from_starts(entry: bytes, starts: list[int], last_end: int) -> list[bytes]:
    records = []
    for index in range(len(starts) - 1):
        record_start = starts[index]
        record_end = starts[index + 1]
        records.append(entry[record_start:record_end])
    return records


def build_final_keys(
    source_records: list[bytes],
    vanilla_records: list[bytes],
    allowed_keys: set[int],
) -> list[int]:
    """最终商品 key 顺序 = 来源 key（去重、过滤死商品）+ 缺失的 vanilla 商品。"""
    source_keys: list[int] = []
    seen: set[int] = set()
    for record in source_records:
        item_key = struct.unpack_from("<I", record, ITEM_KEY_OFFSET)[0]
        if item_key in seen or item_key == 0:
            continue
        seen.add(item_key)
        source_keys.append(item_key)

    final_keys: list[int] = []
    for item_key in source_keys:
        if item_key not in allowed_keys:
            continue
        final_keys.append(item_key)

    vanilla_keys = [
        struct.unpack_from("<I", record, ITEM_KEY_OFFSET)[0] for record in vanilla_records
    ]
    vanilla_present = set(final_keys)
    for item_key in vanilla_keys:
        if item_key not in vanilla_present:
            final_keys.append(item_key)
            vanilla_present.add(item_key)
    return final_keys


def _patch_template(template: bytes, item_key: int) -> bytes:
    """用 vanilla 模板记录重建一条记录：只改 item_key 两处。"""
    if len(template) < ITEM_KEY_DUP_OFFSET + 4:
        raise StoreReplayError("vanilla 模板记录过短，无法定位 item_key")
    patched = bytearray(template)
    struct.pack_into("<I", patched, ITEM_KEY_OFFSET, item_key)
    struct.pack_into("<I", patched, ITEM_KEY_DUP_OFFSET, item_key)
    return bytes(patched)


def rebuild_store_entry(
    source_info: dict[str, object],
    vanilla_info: dict[str, object],
    source_body: bytes,
    vanilla_body: bytes,
    source_chain: tuple[int, list[int], int],
    vanilla_chain: tuple[int, list[int], int],
    final_keys: list[int],
) -> bytes:
    """重建单个商店 entry：来源头部 + 新计数 + 新链 + vanilla 尾部。"""
    source_entry = source_info["entry"]
    vanilla_entry = vanilla_info["entry"]
    source_start, source_starts, source_last_end = source_chain
    vanilla_start, vanilla_starts, vanilla_last_end = vanilla_chain

    source_head = source_entry[: source_start - 4]
    source_count = struct.unpack_from("<I", source_entry, source_start - 4)[0]
    vanilla_tail = vanilla_entry[vanilla_last_end:]

    vanilla_records = _record_bytes_from_starts(vanilla_entry, vanilla_starts, vanilla_last_end)
    if not vanilla_records:
        raise StoreReplayError("原版商店没有可用模板记录")
    template = vanilla_records[0]
    records_by_key = {
        struct.unpack_from("<I", record, ITEM_KEY_OFFSET)[0]: record
        for record in vanilla_records
    }

    new_chain = bytearray()
    for item_key in final_keys:
        record = records_by_key.get(item_key)
        if record is None:
            record = _patch_template(template, item_key)
        new_chain += record

    final_count = len(final_keys)
    new_head = _fix_head_counts(source_head, source_count, final_count)
    rebuilt = new_head + struct.pack("<I", final_count) + bytes(new_chain) + vanilla_tail
    return rebuilt


def _fix_head_counts(head: bytes, source_count: int, final_count: int) -> bytes:
    """把 head 内「值 == 来源记录数」的 u32 改写为最终记录数。"""
    if final_count == source_count:
        return head
    output = bytearray(head)
    for offset in range(0, len(head) - 3):
        if struct.unpack_from("<I", head, offset)[0] == source_count:
            struct.pack_into("<I", output, offset, final_count)
    return bytes(output)


def rebuild_storeinfo(
    source_body: bytes,
    source_header: bytes,
    vanilla_body: bytes,
    vanilla_header: bytes,
    iteminfo_header: bytes,
) -> dict[str, object]:
    """在 1.18 原版 storeinfo 上重放来源模组的商店扩充，返回结果摘要。"""
    source_entries = parse_entries(source_body, source_header)
    vanilla_entries = parse_entries(vanilla_body, vanilla_header)

    item_key_size, item_offsets = parse_pabgh_index(iteminfo_header, "iteminfo")
    if item_key_size != 4 or not item_offsets:
        raise StoreReplayError("ItemInfo PABGH 不是预期的 u32 key 布局")
    item_keys = set(item_offsets)
    vanilla_store_keys = entry_item_keys(vanilla_entries, vanilla_body, VANILLA_RECORD_LENGTHS)
    allowed_keys = item_keys | vanilla_store_keys

    replacements: dict[int, bytes] = {}
    summaries: list[dict[str, object]] = []
    for key, source_info in sorted(source_entries.items()):
        if key not in vanilla_entries:
            continue
        vanilla_info = vanilla_entries[key]
        source_chain = parse_stock_chain(
            source_info["entry"], key, SOURCE_RECORD_LENGTHS
        )
        vanilla_chain = parse_stock_chain(
            vanilla_info["entry"], key, VANILLA_RECORD_LENGTHS
        )
        if source_chain is None or vanilla_chain is None:
            continue
        _ss, source_starts, _sl = source_chain
        _vs, vanilla_starts, _vl = vanilla_chain
        source_records = _record_bytes_from_starts(
            source_info["entry"], source_starts, source_chain[2]
        )
        vanilla_records = _record_bytes_from_starts(
            vanilla_info["entry"], vanilla_starts, vanilla_chain[2]
        )
        if len(source_records) - len(vanilla_records) < MIN_EXPANSION:
            continue

        final_keys = build_final_keys(source_records, vanilla_records, allowed_keys)
        rebuilt = rebuild_store_entry(
            source_info,
            vanilla_info,
            source_body,
            vanilla_body,
            source_chain,
            vanilla_chain,
            final_keys,
        )
        original = vanilla_info["entry"]
        if rebuilt == original:
            continue
        replacements[vanilla_info["start"]] = rebuilt
        summaries.append(
            {
                "name": source_info["name"],
                "key": key,
                "vanilla_count": len(vanilla_records),
                "source_count": len(source_records),
                "final_count": len(final_keys),
            }
        )
    if not replacements:
        raise StoreReplayError("没有识别到被扩充的商店，无法重放")

    new_body = bytearray(vanilla_body)
    deltas: list[tuple[int, int]] = []
    for start, rebuilt in sorted(replacements.items(), reverse=True):
        old = vanilla_entries[
            next(k for k, v in vanilla_entries.items() if v["start"] == start)
        ]
        old_end = old["end"]
        delta = len(rebuilt) - (old_end - start)
        new_body[start:old_end] = rebuilt
        deltas.append((start, delta))

    new_offsets = {}
    for key, info in vanilla_entries.items():
        new_offsets[key] = info["start"] + sum(
            delta for start, delta in deltas if start < info["start"]
        )
    new_header = rewrite_pabgh_offsets(vanilla_header, "storeinfo", new_offsets)
    if new_header is None:
        raise StoreReplayError("storeinfo.pabgh 偏移重写失败")

    return {
        "body": bytes(new_body),
        "header": new_header,
        "summaries": summaries,
        "vanilla_entries": len(vanilla_entries),
    }



def _cstring(value: str) -> bytes:
    """CString = u32 长度 + UTF-8 bytes。"""
    raw = value.encode("utf-8")
    return struct.pack("<I", len(raw)) + raw


def extract_vanilla_stageinfo(game_dir: Path) -> tuple[bytes, bytes]:
    """从当前游戏低编号 PAMT 提取原版 StageInfo。"""
    matches: dict[str, object] = {}
    for archive_dir in sorted(game_dir.glob("[0-9][0-9][0-9][0-9]")):
        pamt_path = archive_dir / "0.pamt"
        if not pamt_path.is_file():
            continue
        for entry in parse_pamt_filtered(
            pamt_path,
            archive_dir,
            desired_basenames={"stageinfo.pabgb", "stageinfo.pabgh"},
        ):
            matches.setdefault(Path(entry.path).name.lower(), entry)
    if "stageinfo.pabgb" not in matches or "stageinfo.pabgh" not in matches:
        raise StoreReplayError("当前游戏中找不到原版 stageinfo.pabgb/.pabgh")
    body, _ = extract_plaintext(matches["stageinfo.pabgb"])
    header, _ = extract_plaintext(matches["stageinfo.pabgh"])
    return body, header


def build_stageinfo_redirect_document(
    game_dir: Path,
    game_version: str,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    """在 1.18 stageinfo 上重放 3 条商店引用重定向，返回 (patch block, 摘要)。"""
    body, header = extract_vanilla_stageinfo(game_dir)
    key_size, offsets = parse_pabgh_index(header, "stageinfo")
    if key_size != 4 or not offsets:
        raise StoreReplayError("stageinfo PABGH 不是预期的 u32 key 布局")
    bounds = build_entry_bounds(body, key_size, offsets)

    prepared: list[tuple[int, int, str, bytes, bytes]] = []
    for key in STAGEINFO_REDIRECTS:
        if key not in bounds:
            raise StoreReplayError(f"stageinfo 缺少重定向目标 entry key={key}")
        start, end, name, _name_end = bounds[key]
        record = body[start:end]
        pairs = STAGEINFO_REDIRECTS[key]
        new_record = record
        entry_delta = 0
        for old, new in pairs:
            old_cs = _cstring(old)
            new_cs = _cstring(new)
            count = new_record.count(old_cs)
            if count != 1:
                raise StoreReplayError(
                    f"{name}: CString {old!r} 出现 {count} 次，重放拒绝"
                )
            index = new_record.find(old_cs)
            new_record = new_record[:index] + new_cs + new_record[index + len(old_cs):]
            entry_delta += len(new_cs) - len(old_cs)
        if new_record != record:
            prepared.append((start, entry_delta, name, record, new_record))

    changes: list[dict[str, object]] = []
    deltas: list[tuple[int, int]] = []
    summaries: list[dict[str, object]] = []
    # loader 的 apply_byte_patches 对含长度变化的变更按 offset 升序应用：
    # - 非 dynamic entry 变更会自动并入前面变更的累计位移（shift_for）；
    # - dynamic entry 变更使用原始 offset，失配时经 pattern_scan 就近重定位。
    # 因此这里一律写 vanilla 原始 start，不能预先叠加 cumulative delta。
    # 实测：若把前面变更的累计 delta 预加进 offset（如 Varnia 少 2 字节），
    # dynamic entry 的 hint 会偏离真实 entry，pattern_scan 的 context 分支
    # 会基于错误 hint 返回候选，导致 1/3 失配 -> loader 因“长度变化+未匹配”
    # 整表跳过 stageinfo.pabgb，连带理发师等其他 stageinfo 补丁一起失效。
    for start, entry_delta, name, record, new_record in sorted(
        prepared, key=lambda item: item[0]
    ):
        changes.append(
            {
                "type": "replace",
                "offset": start,
                "original": record.hex(),
                "patched": new_record.hex(),
                "label": f"{game_version} StageInfo {name} shop redirect",
                "_dynamic_entry_offset": True,
            }
        )
        deltas.append((start, entry_delta))
        summaries.append(
            {
                "name": name,
                "key": next(k for k, v in bounds.items() if v[0] == start),
                "old_len": len(record),
                "new_len": len(new_record),
                "delta": entry_delta,
            }
        )
    if not changes:
        raise StoreReplayError("stageinfo 没有可重放的商店重定向")
    patch_block: dict[str, object] = {
        "game_file": "gamedata/stageinfo.pabgb",
        "changes": changes,
    }
    return patch_block, summaries


def build_cdmod(
    source_dir: Path,
    game_dir: Path,
    output_path: Path,
    game_version: str = DEFAULT_GAME_VERSION,
) -> dict[str, object]:
    """执行重放并写出 cdmod，返回机器可读摘要。"""
    source_dir = source_dir.resolve()
    game_dir = game_dir.resolve()
    output_path = output_path.resolve()

    source_body, source_header = extract_source_storeinfo(source_dir)
    vanilla_body, vanilla_header = extract_vanilla_storeinfo(game_dir)
    iteminfo_body, iteminfo_header = extract_vanilla_iteminfo(game_dir)
    result = rebuild_storeinfo(
        source_body, source_header, vanilla_body, vanilla_header, iteminfo_header
    )
    stage_patch, stage_summaries = build_stageinfo_redirect_document(
        game_dir, game_version
    )

    new_body = result["body"]
    new_header = result["header"]
    summaries = result["summaries"]
    change = {
        "type": "replace",
        "offset": 0,
        "original": vanilla_body.hex(),
        "patched": new_body.hex(),
        "label": (
            f"{game_version} storeinfo semantic replay "
            f"({len(summaries)} expanded stores)"
        ),
        "_pabgh_companion": {
            "offset": 0,
            "original": vanilla_header.hex(),
            "patched": new_header.hex(),
            "label": "storeinfo companion pabgh",
        },
    }
    document = {
        "modinfo": {
            "name": "All Craft Material All Gear All Dye - 1.18",
            "version": game_version,
            "author": "Codex custom build; derived from namvn's 3.7 mod",
            "description": (
                "Rebuilds the 13 expanded Hernand/other vendor stock lists on the "
                "1.18 storeinfo table, plus 3 shop-reference redirects on stageinfo. "
                "All records use 1.18-valid layouts."
            ),
        },
        "format": 2,
        "patches": [
            {
                "game_file": "gamedata/storeinfo.pabgb",
                "changes": [change],
            },
            stage_patch,
        ],
    }
    manifest = {
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": "all-craft-all-gear-all-dye-118",
        "name": "All Craft Material All Gear All Dye - 1.18",
        "version": game_version,
        "author": "Codex custom build; derived from namvn's 3.7 mod",
        "description": (
            "Store stock expansion replay on the 1.18 storeinfo table. "
            "Single whole-table byte patch with PABGH companion."
        ),
        "dependencies": [],
        "source": {
            "format": "storeinfo-118-semantic-replay",
            "game_version": game_version,
        },
        "components": [
            {
                "type": CDMOD_LEGACY_JSON_COMPONENT_TYPE,
                "path": "patches/legacy.json",
            }
        ],
    }
    documents = {
        "patches/legacy.json": document,
        CDMOD_MANIFEST_PATH: manifest,
        CDMOD_REPORT_PATH: {
            "schema": 1,
            "game_version": game_version,
            "summary": {
                "expanded_store_count": len(summaries),
                "stores": summaries,
                "total_final_records": sum(s["final_count"] for s in summaries),
                "stageinfo_redirects": stage_summaries,
            },
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_cdmod_zip(output_path, documents)
    sha = hashlib.sha256(output_path.read_bytes()).hexdigest()
    return {
        "output_path": str(output_path),
        "sha256": sha,
        "game_version": game_version,
        "expanded_store_count": len(summaries),
        "stores": summaries,
        "stageinfo_redirects": stage_summaries,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", required=True, type=Path, help="3.7 Full 模组目录")
    parser.add_argument("--game-dir", required=True, type=Path, help="Crimson Desert 游戏根目录")
    parser.add_argument("--output", type=Path, help="输出 cdmod 路径；默认写入游戏 mods")
    parser.add_argument("--game-version", default=DEFAULT_GAME_VERSION)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    output = args.output or (
        args.game_dir
        / "mods"
        / OUTPUT_NAME_TEMPLATE.format(version=args.game_version)
    )
    result = build_cdmod(args.source_dir, args.game_dir, output, args.game_version)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
