"""比较两个 Enemy Health Multiplier 游戏版本基线。

全游戏清单只比较路径、大小和修改时间；与模组直接相关的原版表、
目标 Buff 记录及容器文件使用采集阶段保存的 SHA-256 和明文字节比较。
该工具只读取基线快照，不生成或修改任何模组。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from cdmm.services.pab_table_service import build_entry_bounds, parse_pabgh_index

# 需要进行明文字节比较的原版表文件。
TARGET_TABLE_NAMES = ("buffinfo.pabgb", "buffinfo.pabgh")


def _load_snapshot(path: Path) -> dict[str, Any]:
    """读取并校验基线快照。"""
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    if snapshot.get("schema_version") != 1:
        raise ValueError(f"不支持的快照版本：{path}")
    return snapshot


def _common_prefix_size(old: bytes, new: bytes) -> int:
    """计算两段字节的共同前缀长度。"""
    limit = min(len(old), len(new))
    for index in range(limit):
        if old[index] != new[index]:
            return index
    return limit


def _common_suffix_size(old: bytes, new: bytes, prefix_size: int) -> int:
    """计算不与共同前缀重叠的共同后缀长度。"""
    limit = min(len(old), len(new)) - prefix_size
    for size in range(limit):
        if old[-size - 1] != new[-size - 1]:
            return size
    return limit


def _compare_bytes(old: bytes, new: bytes) -> dict[str, int | bool]:
    """返回字节长度、差异数量及共同前后缀。"""
    prefix_size = _common_prefix_size(old, new)
    suffix_size = _common_suffix_size(old, new, prefix_size)
    overlap_size = min(len(old), len(new))
    changed_in_overlap = sum(
        old[index] != new[index] for index in range(overlap_size)
    )
    return {
        "identical": old == new,
        "old_size": len(old),
        "new_size": len(new),
        "size_delta": len(new) - len(old),
        "common_prefix_size": prefix_size,
        "common_suffix_size": suffix_size,
        "differing_byte_count": changed_in_overlap + abs(len(new) - len(old)),
    }


def _compare_inventory(
    old_snapshot: dict[str, Any],
    new_snapshot: dict[str, Any],
) -> dict[str, Any]:
    """比较全游戏文件清单的路径、大小与修改时间。"""
    old_files = {
        item["path"]: item for item in old_snapshot["game_inventory"]["files"]
    }
    new_files = {
        item["path"]: item for item in new_snapshot["game_inventory"]["files"]
    }
    added = [new_files[path] for path in sorted(new_files.keys() - old_files.keys())]
    removed = [old_files[path] for path in sorted(old_files.keys() - new_files.keys())]
    size_changed = []
    metadata_only_changed = []
    unchanged_count = 0
    for path in sorted(old_files.keys() & new_files.keys()):
        old_item = old_files[path]
        new_item = new_files[path]
        if old_item["size"] != new_item["size"]:
            size_changed.append(
                {
                    "path": path,
                    "old_size": old_item["size"],
                    "new_size": new_item["size"],
                    "size_delta": new_item["size"] - old_item["size"],
                    "old_mtime_utc": old_item["mtime_utc"],
                    "new_mtime_utc": new_item["mtime_utc"],
                }
            )
        elif old_item["mtime_utc"] != new_item["mtime_utc"]:
            metadata_only_changed.append(
                {
                    "path": path,
                    "size": old_item["size"],
                    "old_mtime_utc": old_item["mtime_utc"],
                    "new_mtime_utc": new_item["mtime_utc"],
                }
            )
        else:
            unchanged_count += 1
    return {
        "old_file_count": len(old_files),
        "new_file_count": len(new_files),
        "old_total_size": old_snapshot["game_inventory"]["total_size"],
        "new_total_size": new_snapshot["game_inventory"]["total_size"],
        "total_size_delta": (
            new_snapshot["game_inventory"]["total_size"]
            - old_snapshot["game_inventory"]["total_size"]
        ),
        "added": added,
        "removed": removed,
        "size_changed": size_changed,
        "metadata_only_changed": metadata_only_changed,
        "unchanged_count": unchanged_count,
    }


def _compare_target_tables(
    old_snapshot_path: Path,
    new_snapshot_path: Path,
    old_snapshot: dict[str, Any],
    new_snapshot: dict[str, Any],
) -> dict[str, Any]:
    """逐字节比较目标表明文及其 PAMT 定位信息。"""
    result: dict[str, Any] = {}
    for table_name in TARGET_TABLE_NAMES:
        old_bytes = (old_snapshot_path.parent / "vanilla" / table_name).read_bytes()
        new_bytes = (new_snapshot_path.parent / "vanilla" / table_name).read_bytes()
        result[table_name] = {
            **_compare_bytes(old_bytes, new_bytes),
            "old_entry": old_snapshot["table_entries"][table_name],
            "new_entry": new_snapshot["table_entries"][table_name],
        }
    return result


def _compare_target_records(
    old_snapshot_path: Path,
    new_snapshot_path: Path,
    old_snapshot: dict[str, Any],
    new_snapshot: dict[str, Any],
) -> dict[str, Any]:
    """逐字节比较普通敌人与 Boss 的目标 Buff 记录。"""
    result: dict[str, Any] = {}
    keys = sorted(
        old_snapshot["target_records"].keys()
        | new_snapshot["target_records"].keys()
    )
    for key in keys:
        old_record = old_snapshot["target_records"].get(key)
        new_record = new_snapshot["target_records"].get(key)
        if old_record is None or new_record is None:
            result[key] = {
                "exists_in_old": old_record is not None,
                "exists_in_new": new_record is not None,
            }
            continue
        old_bytes = (old_snapshot_path.parent / old_record["file"]).read_bytes()
        new_bytes = (new_snapshot_path.parent / new_record["file"]).read_bytes()
        result[key] = {
            **_compare_bytes(old_bytes, new_bytes),
            "old": old_record,
            "new": new_record,
        }
    return result


def _find_changed_buff_records(
    old_snapshot_path: Path,
    new_snapshot_path: Path,
) -> list[dict[str, Any]]:
    """定位 buffinfo 整表变化实际落入的记录。"""
    old_body = (old_snapshot_path.parent / "vanilla" / "buffinfo.pabgb").read_bytes()
    new_body = (new_snapshot_path.parent / "vanilla" / "buffinfo.pabgb").read_bytes()
    old_header = (old_snapshot_path.parent / "vanilla" / "buffinfo.pabgh").read_bytes()
    new_header = (new_snapshot_path.parent / "vanilla" / "buffinfo.pabgh").read_bytes()
    old_key_size, old_offsets = parse_pabgh_index(old_header, "buffinfo")
    new_key_size, new_offsets = parse_pabgh_index(new_header, "buffinfo")
    old_bounds = build_entry_bounds(old_body, old_key_size, old_offsets)
    new_bounds = build_entry_bounds(new_body, new_key_size, new_offsets)
    changed_records = []
    for key in sorted(old_bounds.keys() | new_bounds.keys()):
        old_bound = old_bounds.get(key)
        new_bound = new_bounds.get(key)
        if old_bound is None or new_bound is None:
            changed_records.append(
                {
                    "key": key,
                    "exists_in_old": old_bound is not None,
                    "exists_in_new": new_bound is not None,
                }
            )
            continue
        old_start, old_end, old_name, _old_name_end = old_bound
        new_start, new_end, new_name, _new_name_end = new_bound
        old_record = old_body[old_start:old_end]
        new_record = new_body[new_start:new_end]
        if old_record == new_record:
            continue
        changed_records.append(
            {
                "key": key,
                "old_name": old_name,
                "new_name": new_name,
                "old_offset": old_start,
                "new_offset": new_start,
                **_compare_bytes(old_record, new_record),
                "changed_offsets_in_record": [
                    index
                    for index in range(min(len(old_record), len(new_record)))
                    if old_record[index] != new_record[index]
                ],
                "byte_changes": [
                    {
                        "offset_in_record": index,
                        "old": old_record[index],
                        "new": new_record[index],
                    }
                    for index in range(min(len(old_record), len(new_record)))
                    if old_record[index] != new_record[index]
                ],
            }
        )
    return changed_records


def _compare_related_files(
    old_snapshot: dict[str, Any],
    new_snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    """比较关键 PAZ、PAMT 与 meta 文件的采集哈希。"""
    old_files = {item["path"].casefold(): item for item in old_snapshot["related_files"]}
    new_files = {item["path"].casefold(): item for item in new_snapshot["related_files"]}
    results = []
    for path_key in sorted(old_files.keys() | new_files.keys()):
        old_item = old_files.get(path_key)
        new_item = new_files.get(path_key)
        results.append(
            {
                "path": (new_item or old_item)["path"],
                "exists_in_old": old_item is not None,
                "exists_in_new": new_item is not None,
                "sha256_changed": (
                    old_item is None
                    or new_item is None
                    or old_item["sha256"] != new_item["sha256"]
                ),
                "old": old_item,
                "new": new_item,
            }
        )
    return results


def compare_baselines(
    old_snapshot_path: Path,
    new_snapshot_path: Path,
    output_path: Path,
) -> Path:
    """生成两个游戏版本之间的完整差异报告。"""
    old_snapshot_path = old_snapshot_path.resolve()
    new_snapshot_path = new_snapshot_path.resolve()
    output_path = output_path.resolve()
    old_snapshot = _load_snapshot(old_snapshot_path)
    new_snapshot = _load_snapshot(new_snapshot_path)
    report = {
        "schema_version": 1,
        "purpose": "Enemy Health Multiplier game update comparison",
        "old_version": old_snapshot["version_label"],
        "new_version": new_snapshot["version_label"],
        "steam_build": {
            "old": old_snapshot["steam_manifest"]["fields"].get("buildid"),
            "new": new_snapshot["steam_manifest"]["fields"].get("buildid"),
        },
        "game_executable": {
            "old": old_snapshot["game_executable"],
            "new": new_snapshot["game_executable"],
            "sha256_changed": (
                old_snapshot["game_executable"]["sha256"]
                != new_snapshot["game_executable"]["sha256"]
            ),
        },
        "game_inventory": _compare_inventory(old_snapshot, new_snapshot),
        "target_tables": _compare_target_tables(
            old_snapshot_path,
            new_snapshot_path,
            old_snapshot,
            new_snapshot,
        ),
        "target_records": _compare_target_records(
            old_snapshot_path,
            new_snapshot_path,
            old_snapshot,
            new_snapshot,
        ),
        "changed_buff_records": _find_changed_buff_records(
            old_snapshot_path,
            new_snapshot_path,
        ),
        "related_files": _compare_related_files(old_snapshot, new_snapshot),
        "old_cdmods_against_new_vanilla": new_snapshot["existing_cdmods"],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output_path


def _parse_args() -> argparse.Namespace:
    """解析差异比较参数。"""
    parser = argparse.ArgumentParser(description="比较 Enemy Health 游戏更新基线")
    parser.add_argument("--old", type=Path, required=True, help="旧版 snapshot.json")
    parser.add_argument("--new", type=Path, required=True, help="新版 snapshot.json")
    parser.add_argument("--output", type=Path, required=True, help="差异报告路径")
    return parser.parse_args()


def main() -> int:
    """命令行入口。"""
    args = _parse_args()
    output_path = compare_baselines(args.old, args.new, args.output)
    print(f"差异报告：{output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
