"""复读校验cdloader生成的VFS快照。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from cdmm.archive.pamt import parse_pamt


def main() -> int:
    """校验映射源、PAMT边界和普通LZ4尺寸关系。"""
    parser = argparse.ArgumentParser(description="校验Crimson Desert VFS快照")
    parser.add_argument("--game-dir", required=True, type=Path, help="游戏根目录")
    args = parser.parse_args()

    work_dir = args.game_dir / ".cdloader"
    active_dir = work_dir / "vfs_active"
    mapping_path = work_dir / "vfs_mapping_tree.json"
    errors: list[str] = []
    package_stats: list[dict[str, object]] = []

    mapping = json.loads(mapping_path.read_text(encoding="utf-8-sig"))
    mapped_entries = mapping.get("entries", []) if isinstance(mapping, dict) else []
    for mapped in mapped_entries:
        source = mapped.get("source_absolute_path") if isinstance(mapped, dict) else None
        if not isinstance(source, str) or not Path(source).is_file():
            errors.append(f"映射源不存在：{source}")

    for pamt_path in sorted(active_dir.glob("*/0.pamt")):
        package_name = pamt_path.parent.name
        paz_path = pamt_path.parent / "0.paz"
        if not paz_path.is_file():
            errors.append(f"{package_name}: 缺少0.paz")
            continue
        paz_size = paz_path.stat().st_size
        try:
            entries = parse_pamt(pamt_path, paz_dir=pamt_path.parent)
        except (OSError, ValueError) as exc:
            errors.append(f"{package_name}: PAMT解析失败：{exc}")
            continue
        final_paths = _load_cached_final_paths(
            work_dir,
            package_name,
            pamt_path.read_bytes(),
            paz_size,
        )
        duplicate_paths = _find_duplicates(final_paths)
        duplicate_count = len(duplicate_paths)
        bad_boundary_count = 0
        bad_lz4_size_count = 0
        for entry in entries:
            if entry.offset < 0 or entry.comp_size < 0 or entry.offset + entry.comp_size > paz_size:
                bad_boundary_count += 1
            if entry.compression_type == 2 and entry.comp_size > entry.orig_size:
                bad_lz4_size_count += 1
        if duplicate_count:
            errors.append(f"{package_name}: 包内重复最终路径 {duplicate_count} 条")
        if bad_boundary_count:
            errors.append(f"{package_name}: PAZ越界entry {bad_boundary_count} 条")
        if bad_lz4_size_count:
            errors.append(f"{package_name}: 普通LZ4 comp_size>orig_size {bad_lz4_size_count} 条")
        package_stats.append(
            {
                "package": package_name,
                "entry_count": len(entries),
                "paz_size": paz_size,
                "duplicate_count": duplicate_count,
                "duplicate_paths": duplicate_paths,
                "bad_boundary_count": bad_boundary_count,
                "bad_lz4_size_count": bad_lz4_size_count,
            }
        )

    result = {
        "valid": not errors,
        "mapped_file_count": len(mapped_entries),
        "package_count": len(package_stats),
        "packages": package_stats,
        "errors": errors,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 2


def _load_cached_final_paths(
    work_dir: Path,
    package_name: str,
    pamt_bytes: bytes,
    paz_size: int,
) -> list[str]:
    """从与当前PAMT完全一致的分包缓存读取写入时最终路径。"""
    cache_root = work_dir / "vfs_package_cache"
    for cache_dir in cache_root.glob(f"{package_name}-*"):
        cached_pamt = cache_dir / "0.pamt"
        cached_paz = cache_dir / "0.paz"
        entries_path = cache_dir / "entries.json"
        if not cached_pamt.is_file() or not cached_paz.is_file() or not entries_path.is_file():
            continue
        if cached_paz.stat().st_size != paz_size or cached_pamt.read_bytes() != pamt_bytes:
            continue
        document = json.loads(entries_path.read_text(encoding="utf-8-sig"))
        entries = document.get("entries", []) if isinstance(document, dict) else []
        return [
            f"{entry.get('dir_path', '').strip('/')}/{entry.get('filename', '')}".strip("/").lower()
            for entry in entries
            if isinstance(entry, dict)
        ]
    return []


def _find_duplicates(values: list[str]) -> list[str]:
    """返回首次顺序稳定的重复值。"""
    seen: set[str] = set()
    duplicates: list[str] = []
    for value in values:
        if value in seen and value not in duplicates:
            duplicates.append(value)
        seen.add(value)
    return duplicates


if __name__ == "__main__":
    raise SystemExit(main())
