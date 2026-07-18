"""采集 Enemy Health Multiplier 跨游戏版本修复所需的原版基线。

该工具只读取游戏文件，不执行 apply，也不会修改 PAZ、PAMT 或 meta。
输出包含目标表明文、目标 Buff 记录、相关容器哈希和游戏文件清单，
用于游戏更新后定位原版资源变化并重新生成传统 byte patch。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cdmm.services.json_loader import extract_plaintext
from cdmm.services.pab_table_service import build_entry_bounds, parse_pabgh_index
from cdmm.services.pamt_index_service import get_game_pamt_index
from cdmm.tools.build_enemy_health_x5_mod import (
    BOSS_DIFFICULTY_BUFF_KEY,
    BUFFINFO_GAME_FILE,
    NORMAL_ENEMY_DIFFICULTY_BUFF_KEY,
    _collect_buff_items,
    _is_max_hp_item,
)
from cdmm.services.buffinfo_parser import parse_entry

# 当前模组依赖的原版表文件。
TARGET_TABLE_NAMES = ("buffinfo.pabgb", "buffinfo.pabgh")

# 当前模组修改的普通敌人与 Boss 难度 Buff key。
TARGET_BUFF_KEYS = (
    NORMAL_ENEMY_DIFFICULTY_BUFF_KEY,
    BOSS_DIFFICULTY_BUFF_KEY,
)

# 不属于 Steam 原版游戏内容的本地目录，文件清单采集时统一排除。
EXCLUDED_GAME_DIRECTORIES = {".cdloader", "mods"}

# 流式计算大文件哈希时使用的块大小。
HASH_CHUNK_SIZE = 8 * 1024 * 1024

# Steam appmanifest 中需要保留的版本定位字段。
STEAM_MANIFEST_FIELDS = {
    "appid",
    "buildid",
    "installdir",
    "LastUpdated",
    "StateFlags",
}


def _sha256_file(path: Path) -> str:
    """流式计算文件 SHA-256，避免把 PAZ 整体读入内存。"""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(HASH_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(content: bytes) -> str:
    """计算内存字节的 SHA-256。"""
    return hashlib.sha256(content).hexdigest()


def _utc_mtime(path: Path) -> str:
    """返回稳定、可比较的 UTC 修改时间。"""
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()


def _file_metadata(path: Path, *, with_hash: bool) -> dict[str, Any]:
    """采集文件大小、修改时间以及可选哈希。"""
    metadata: dict[str, Any] = {
        "path": str(path),
        "size": path.stat().st_size,
        "mtime_utc": _utc_mtime(path),
    }
    if with_hash:
        metadata["sha256"] = _sha256_file(path)
    return metadata


def _find_steam_manifest(game_dir: Path) -> Path | None:
    """根据标准 steamapps/common 结构定位当前游戏的 appmanifest。"""
    common_dir = game_dir.parent
    if common_dir.name.casefold() != "common":
        return None
    steamapps_dir = common_dir.parent
    for candidate in sorted(steamapps_dir.glob("appmanifest_*.acf")):
        text = candidate.read_text(encoding="utf-8", errors="replace")
        match = re.search(r'"installdir"\s+"([^"]+)"', text, re.IGNORECASE)
        if match and match.group(1).casefold() == game_dir.name.casefold():
            return candidate
    return None


def _read_steam_manifest(path: Path | None) -> dict[str, Any] | None:
    """读取 appmanifest 的关键字段和原文件哈希。"""
    if path is None:
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    fields = {
        key: value
        for key, value in re.findall(r'"([^"]+)"\s+"([^"]*)"', text)
        if key in STEAM_MANIFEST_FIELDS
    }
    return {
        **_file_metadata(path, with_hash=True),
        "fields": fields,
    }


def _scan_game_inventory(game_dir: Path) -> list[dict[str, Any]]:
    """采集游戏目录文件路径、大小与时间，不对全游戏执行昂贵哈希。"""
    inventory: list[dict[str, Any]] = []
    for path in game_dir.rglob("*"):
        if not path.is_file():
            continue
        relative_path = path.relative_to(game_dir)
        if relative_path.parts[0].casefold() in EXCLUDED_GAME_DIRECTORIES:
            continue
        inventory.append(
            {
                "path": relative_path.as_posix(),
                "size": path.stat().st_size,
                "mtime_utc": _utc_mtime(path),
            }
        )
    return sorted(inventory, key=lambda item: str(item["path"]).casefold())


def _summarize_target_record(record: bytes) -> dict[str, Any]:
    """解析目标 Buff 记录中的三级最大生命倍率原始值。"""
    parsed = parse_entry(record)
    max_hp_items = []
    for item in _collect_buff_items(record, parsed):
        if not _is_max_hp_item(record, item):
            continue
        max_hp_items.append(
            {
                "difficulty_level": item.leading_lookup,
                "rate_delta": struct.unpack_from(
                    "<q", record, item.common.end_offset + 1
                )[0],
            }
        )
    return {
        "name": parsed.name,
        "record_size": len(record),
        "buff_data_count": parsed.buff_data_count,
        "max_hp_items": max_hp_items,
        "sha256": _sha256_bytes(record),
    }


def _inspect_existing_cdmods(
    release_dir: Path | None,
    body: bytes,
) -> list[dict[str, Any]]:
    """验证现有倍率包的 original 字节是否仍与当前原版表一致。"""
    if release_dir is None or not release_dir.is_dir():
        return []
    results: list[dict[str, Any]] = []
    for cdmod_path in sorted(release_dir.glob("*.cdmod")):
        with zipfile.ZipFile(cdmod_path) as archive:
            document = json.loads(archive.read("patches/legacy.json"))
        changes = document["patches"][0]["changes"]
        checks = []
        for change in changes:
            offset = int(change["offset"])
            original = bytes.fromhex(change["original"])
            checks.append(
                {
                    "label": change.get("label"),
                    "offset": offset,
                    "original_size": len(original),
                    "matches_current_vanilla": (
                        body[offset : offset + len(original)] == original
                    ),
                    "original_sha256": _sha256_bytes(original),
                }
            )
        results.append(
            {
                "name": cdmod_path.name,
                "sha256": _sha256_file(cdmod_path),
                "all_original_bytes_match": all(
                    item["matches_current_vanilla"] for item in checks
                ),
                "changes": checks,
            }
        )
    return results


def capture_baseline(
    game_dir: Path,
    output_dir: Path,
    version_label: str,
    release_dir: Path | None,
) -> Path:
    """采集并写出指定游戏版本的 Enemy Health 原版基线。"""
    game_dir = game_dir.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    vanilla_dir = output_dir / "vanilla"
    records_dir = output_dir / "records"
    vanilla_dir.mkdir(parents=True, exist_ok=True)
    records_dir.mkdir(parents=True, exist_ok=True)

    pamt_index = get_game_pamt_index(game_dir)
    table_bytes: dict[str, bytes] = {}
    table_entries: dict[str, dict[str, Any]] = {}
    related_files: dict[Path, dict[str, Any]] = {}
    for table_name in TARGET_TABLE_NAMES:
        entry = pamt_index.find_best(
            table_name,
            suffix=Path(table_name).suffix,
            require_unique_best=False,
        )
        if entry is None:
            raise ValueError(f"无法定位原版 {table_name}")
        plaintext = extract_plaintext(entry)[0]
        table_bytes[table_name] = plaintext
        (vanilla_dir / table_name).write_bytes(plaintext)
        table_entries[table_name] = {
            "game_path": entry.path,
            "resolved_dir_path": entry.resolved_dir_path,
            "paz_file": entry.paz_file,
            "offset": entry.offset,
            "compressed_size": entry.comp_size,
            "original_size": entry.orig_size,
            "flags": entry.flags,
            "plaintext_sha256": _sha256_bytes(plaintext),
        }
        paz_path = Path(entry.paz_file)
        related_files[paz_path] = _file_metadata(paz_path, with_hash=True)
        pamt_path = paz_path.with_name("0.pamt")
        related_files[pamt_path] = _file_metadata(pamt_path, with_hash=True)

    body = table_bytes["buffinfo.pabgb"]
    header = table_bytes["buffinfo.pabgh"]
    key_size, offsets = parse_pabgh_index(header, "buffinfo")
    bounds = build_entry_bounds(body, key_size, offsets)
    target_records: dict[str, dict[str, Any]] = {}
    for key in TARGET_BUFF_KEYS:
        if key not in bounds:
            raise ValueError(f"原版 buffinfo 缺少目标 key：{key}")
        start, end, name, _name_end = bounds[key]
        record = body[start:end]
        record_path = records_dir / f"{key}-{name}.bin"
        record_path.write_bytes(record)
        target_records[str(key)] = {
            "offset": start,
            "end": end,
            "file": record_path.relative_to(output_dir).as_posix(),
            **_summarize_target_record(record),
        }

    for meta_name in ("0.papgt", "0.pathc"):
        meta_path = game_dir / "meta" / meta_name
        if meta_path.is_file():
            related_files[meta_path] = _file_metadata(meta_path, with_hash=True)

    executable_path = game_dir / "bin64" / "CrimsonDesert.exe"
    inventory = _scan_game_inventory(game_dir)
    snapshot = {
        "schema_version": 1,
        "purpose": "Enemy Health Multiplier game update baseline",
        "version_label": version_label,
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "game_dir": str(game_dir),
        "target_game_file": BUFFINFO_GAME_FILE,
        "steam_manifest": _read_steam_manifest(_find_steam_manifest(game_dir)),
        "game_executable": _file_metadata(executable_path, with_hash=True),
        "table_entries": table_entries,
        "pabgh": {
            "key_size": key_size,
            "entry_count": len(offsets),
        },
        "target_records": target_records,
        "related_files": [
            metadata
            for _path, metadata in sorted(
                related_files.items(), key=lambda item: str(item[0]).casefold()
            )
        ],
        "existing_cdmods": _inspect_existing_cdmods(release_dir, body),
        "game_inventory": {
            "file_count": len(inventory),
            "total_size": sum(int(item["size"]) for item in inventory),
            "files": inventory,
        },
    }
    snapshot_path = output_dir / "snapshot.json"
    snapshot_path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return snapshot_path


def _parse_args() -> argparse.Namespace:
    """解析基线采集参数。"""
    parser = argparse.ArgumentParser(description="采集 Enemy Health 游戏更新基线")
    parser.add_argument("--game-dir", type=Path, required=True, help="游戏根目录")
    parser.add_argument("--output-dir", type=Path, required=True, help="基线输出目录")
    parser.add_argument("--version-label", required=True, help="当前游戏版本标签")
    parser.add_argument(
        "--release-dir",
        type=Path,
        help="可选：现有 Enemy Health .cdmod 发布目录",
    )
    return parser.parse_args()


def main() -> int:
    """命令行入口。"""
    args = _parse_args()
    snapshot_path = capture_baseline(
        args.game_dir,
        args.output_dir,
        args.version_label,
        args.release_dir,
    )
    print(f"基线快照：{snapshot_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
