"""早期理发师修复生成器：把营地 Eric 理发师重定向到 Hernand 杂货店。

来源：All Craft Material + All Gear + All Dye - Early Barber 3.7 模组。
原始模组把 stageinfo 的 BaseCamp_Func_Eric_barbershop (key=1000179) 商店引用
从 `Shop_barbershop_BaseCamp` / `Shop_BaseCamp_Lv4`（营地 4 级解锁）改为
`Shop_Butcher_Hernand` / `Shop_Hernand_0001_Phase00_05_sub_1_0`（Hernand
杂货店），实现早期理发师。旧完整表在 1.18 上不兼容，这里改为在 1.18 原版
stageinfo 上只做两条 CString 替换，记录长度变化由 PABGH companion 修正。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

from cdmm.archive.pamt import parse_pamt
from cdmm.services.cdmod_converter import (
    CDMOD_FORMAT_NAME,
    CDMOD_FORMAT_VERSION,
    CDMOD_LEGACY_JSON_COMPONENT_TYPE,
    CDMOD_MANIFEST_PATH,
    CDMOD_REPORT_PATH,
    _write_cdmod_zip,
)
from cdmm.services.json_loader import extract_plaintext
from cdmm.services.pab_table_service import parse_entry_name_end, parse_pabgh_index
from cdmm.services.pabgh_rewrite import rewrite_pabgh_offsets

DEFAULT_GAME_VERSION = "1.18.00"
BARBER_KEY = 1000179

# 原版引用（len + 字符串）
OLD_SHOP_1 = b"Shop_barbershop_BaseCamp"
OLD_SHOP_2 = b"Shop_BaseCamp_Lv4"
# 模组意图（len + 字符串）
NEW_SHOP_1 = b"Shop_Butcher_Hernand"
NEW_SHOP_2 = b"Shop_Hernand_0001_Phase00_05_sub_1_0"

# 原版营地理发师坐标（1.18 stageinfo key=1000179，interaction 后 hash 区）
OLD_COORDS = (-10522.2470703125, 610.489013671875, -4355.73876953125, -0.10684884339570999)
# 模组意图：Hernand General 杂货店门口坐标（从来源 3.7 模组表提取）
NEW_COORDS = (-10646.41015625, 613.6599731445312, -3861.010009765625, -2.0)


def _extract_stageinfo(game_dir: Path) -> tuple[bytes, bytes]:
    """从当前游戏低编号 PAMT 提取原版 stageinfo。"""
    matches: dict[str, object] = {}
    for archive_dir in sorted(game_dir.glob("[0-9][0-9][0-9][0-9]")):
        pamt_path = archive_dir / "0.pamt"
        if not pamt_path.is_file():
            continue
        for entry in parse_pamt(pamt_path, archive_dir):
            name = Path(entry.path).name.lower()
            if name in ("stageinfo.pabgb", "stageinfo.pabgh"):
                matches.setdefault(name, entry)
    if "stageinfo.pabgb" not in matches or "stageinfo.pabgh" not in matches:
        raise ValueError("当前游戏中找不到原版 stageinfo.pabgb/.pabgh")
    body, _ = extract_plaintext(matches["stageinfo.pabgb"])
    header, _ = extract_plaintext(matches["stageinfo.pabgh"])
    return body, header


def _build_barber_patch(body: bytes, header: bytes, version: str) -> dict:
    """在 1.18 原版 stageinfo 上重放理发师商店重定向，返回 legacy-byte-patch 文档。"""
    key_size, offsets = parse_pabgh_index(header, "stageinfo")
    if key_size != 4 or not offsets:
        raise ValueError("stageinfo PABGH 不是预期的 u32 key 布局")
    ordered = sorted(offsets.items(), key=lambda item: item[1])

    barber_start = barber_end = None
    for index, (key, start) in enumerate(ordered):
        if key == BARBER_KEY:
            barber_start = start
            barber_end = ordered[index + 1][1] if index + 1 < len(ordered) else len(body)
            break
    if barber_start is None:
        raise ValueError(f"stageinfo 缺少理发师记录 key={BARBER_KEY}")

    parsed = parse_entry_name_end(body, barber_start, key_size)
    if parsed is None:
        raise ValueError("理发师记录名称无法解析")
    entry_name, name_end = parsed
    record = body[barber_start:barber_end]
    print(f"理发师记录 {entry_name!r} [{barber_start:#x},{barber_end:#x}) len={barber_end - barber_start}")

    # 定位 interaction 串后的坐标区（4 个 float: x/y/z/朝向）
    inter_idx = record.find(b"funcnpc/funcnpc_interaction")
    if inter_idx < 0:
        raise ValueError("理发师记录缺少 funcnpc_interaction 引用")
    coord_off = inter_idx + len(b"funcnpc/funcnpc_interaction")
    if coord_off + 16 > len(record):
        raise ValueError("理发师记录坐标区越界")
    old_coords = struct.unpack_from("<ffff", record, coord_off)
    for actual, expected in zip(old_coords, OLD_COORDS):
        if abs(actual - expected) > 0.01:
            raise ValueError(
                f"理发师坐标与预期不符：{old_coords} != {OLD_COORDS} "
                f"(coord_off=0x{coord_off:x})"
            )
    print(
        f"坐标区 @record+0x{coord_off:x}: {old_coords} -> {NEW_COORDS}"
    )

    # 定位两个 CString（相对记录起点）
    old1_abs = body.find(OLD_SHOP_1, barber_start, barber_end)
    old2_abs = body.find(OLD_SHOP_2, barber_start, barber_end)
    if old1_abs < 0 or old2_abs < 0:
        raise ValueError(
            f"理发师记录缺少预期商店引用 "
            f"(shop1@{old1_abs:#x} shop2@{old2_abs:#x})"
        )
    # CString: u32 len + bytes，定位 len 前缀
    len1_abs = old1_abs - 4
    len2_abs = old2_abs - 4
    for label, len_abs in (("field1", len1_abs), ("field2", len2_abs)):
        ln = struct.unpack_from("<I", body, len_abs)[0]
        if not 0 < ln < 300:
            raise ValueError(f"{label} 长度前缀异常：{ln}")
    len1 = struct.unpack_from("<I", body, len1_abs)[0]
    len2 = struct.unpack_from("<I", body, len2_abs)[0]
    if len1 != len(OLD_SHOP_1) or len2 != len(OLD_SHOP_2):
        raise ValueError(
            f"商店引用长度前缀与预期不符：{len1}!={len(OLD_SHOP_1)} {len2}!={len(OLD_SHOP_2)}"
        )

    field1_start = len1_abs  # 含 len 前缀
    field1_end = len1_abs + 4 + len1
    field2_start = len2_abs
    field2_end = len2_abs + 4 + len2
    print(
        f"字段1 @record+{field1_start - barber_start:#x} ({len1}B: {OLD_SHOP_1.decode()})\n"
        f"字段2 @record+{field2_start - barber_start:#x} ({len2}B: {OLD_SHOP_2.decode()})"
    )

    # 构造替换后的完整记录（坐标 + 两个商店引用）
    new_field1 = struct.pack("<I", len(NEW_SHOP_1)) + NEW_SHOP_1
    new_field2 = struct.pack("<I", len(NEW_SHOP_2)) + NEW_SHOP_2
    new_record = bytearray()
    new_record += record[:coord_off]
    new_record += struct.pack("<ffff", *NEW_COORDS)
    new_record += record[coord_off + 16:field1_start - barber_start]
    new_record += new_field1
    new_record += record[field1_end - barber_start:field2_start - barber_start]
    new_record += new_field2
    new_record += record[field2_end - barber_start:]
    print(
        f"记录长度 {len(record)} -> {len(new_record)} "
        f"(delta {len(new_record) - len(record):+d})"
    )

    # 构造补丁：整记录替换（覆盖原记录范围），companion 由 loader 处理 PABGH
    original = record
    patched = bytes(new_record)
    change = {
        "type": "replace",
        "offset": barber_start,
        "original": original.hex(),
        "patched": patched.hex(),
        "label": f"{version} StageInfo {entry_name} ({BARBER_KEY}) early barber shop redirect",
        "_dynamic_entry_offset": True,
    }

    # PABGH companion：记录长度变化，其后所有 entry offset 前移 delta
    new_offsets = {}
    delta = len(new_record) - len(record)
    for key, start in ordered:
        if key == BARBER_KEY:
            new_offsets[key] = start
        elif start > barber_start:
            new_offsets[key] = start + delta
        else:
            new_offsets[key] = start
    companion_header = rewrite_pabgh_offsets(header, "stageinfo", new_offsets)
    if companion_header is None:
        raise ValueError("stageinfo PABGH 重写失败")
    change["_pabgh_companion"] = {
        "offset": 0,
        "original": header.hex(),
        "patched": companion_header.hex(),
        "label": "stageinfo companion pabgh",
    }

    return {
        "modinfo": {
            "name": "Early Barber (Hernand) - 1.18",
            "version": version,
            "author": "Codex custom build; derived from namvn's 3.7 mod",
            "description": (
                "Redirects the BaseCamp Eric barbershop to the Hernand butcher/general "
                "store so the barber is available early, rebuilt on the 1.18 stageinfo table."
            ),
        },
        "format": 2,
        "patches": [
            {
                "game_file": "gamedata/stageinfo.pabgb",
                "changes": [change],
            }
        ],
    }


def _build_cdmod(
    game_dir: Path,
    output_path: Path,
    version: str,
) -> tuple[Path, str, dict, dict]:
    body, header = _extract_stageinfo(game_dir)
    document = _build_barber_patch(body, header, version)
    manifest = {
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": "early-barber-hernand",
        "name": "Early Barber (Hernand)",
        "version": version,
        "author": "Codex custom build; derived from namvn's 3.7 mod",
        "description": (
            "Early barber: redirects BaseCamp Eric barbershop store references to "
            "the Hernand butcher store on the 1.18 stageinfo table."
        ),
        "dependencies": [],
        "source": {"format": "stageinfo-cstring-shop-redirect", "game_version": version},
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
            "game_version": version,
            "summary": {
                "patched_record": BARBER_KEY,
                "old_shops": [OLD_SHOP_1.decode(), OLD_SHOP_2.decode()],
                "new_shops": [NEW_SHOP_1.decode(), NEW_SHOP_2.decode()],
                "patch_count": 1,
            },
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_cdmod_zip(output_path, documents)
    sha = hashlib.sha256(output_path.read_bytes()).hexdigest()
    return output_path, sha, manifest, document


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-dir", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--game-version", default=DEFAULT_GAME_VERSION)
    args = parser.parse_args()

    output = args.output or (
        args.game_dir / "mods" / f"Early Barber (Hernand)-{args.game_version}.cdmod"
    )
    path, sha, manifest, document = _build_cdmod(args.game_dir, output, args.game_version)
    changes = document["patches"][0]["changes"]
    print(
        json.dumps(
            {
                "output_path": str(path),
                "sha256": sha,
                "game_version": args.game_version,
                "patch_count": len(changes),
                "record_len": len(bytes.fromhex(changes[0]["original"])),
                "new_record_len": len(bytes.fromhex(changes[0]["patched"])),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
