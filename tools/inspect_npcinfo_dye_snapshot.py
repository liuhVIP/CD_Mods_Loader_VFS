"""Inspect final NpcInfo dye-addon bytes against the current vanilla table."""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

from cdmm.archive.pamt import parse_pamt_filtered
from cdmm.services.format3_array_writer import _locate_npc_dye_arrays
from cdmm.services.json_loader import extract_plaintext
from cdmm.services.pab_table_service import build_entry_bounds, parse_pabgh_index

BODY_NAME = "npcinfo.pabgb"
HEADER_NAME = "npcinfo.pabgh"
DYE_NPC_KEYS = set(range(1_000_221, 1_000_231))


def _extract_pair(archive_dir: Path) -> tuple[bytes, bytes]:
    matches = {
        Path(entry.path).name.lower(): entry
        for entry in parse_pamt_filtered(
            archive_dir / "0.pamt",
            archive_dir,
            desired_basenames={BODY_NAME, HEADER_NAME},
        )
    }
    if BODY_NAME not in matches or HEADER_NAME not in matches:
        raise ValueError(f"NpcInfo pair missing from {archive_dir}")
    body, _ = extract_plaintext(matches[BODY_NAME])
    header, _ = extract_plaintext(matches[HEADER_NAME])
    return body, header


def _extract_vanilla_pair(game_dir: Path) -> tuple[bytes, bytes]:
    matches: dict[str, object] = {}
    for archive_dir in sorted(game_dir.glob("[0-9][0-9][0-9][0-9]")):
        pamt_path = archive_dir / "0.pamt"
        if not pamt_path.is_file():
            continue
        for entry in parse_pamt_filtered(
            pamt_path,
            archive_dir,
            desired_basenames={BODY_NAME, HEADER_NAME},
        ):
            matches.setdefault(Path(entry.path).name.lower(), entry)
    if BODY_NAME not in matches or HEADER_NAME not in matches:
        raise ValueError("vanilla NpcInfo pair missing")
    body, _ = extract_plaintext(matches[BODY_NAME])
    header, _ = extract_plaintext(matches[HEADER_NAME])
    return body, header


def _index(body: bytes, header: bytes) -> tuple[int, dict, dict]:
    key_size, offsets = parse_pabgh_index(header, "npcinfo")
    bounds = build_entry_bounds(body, key_size, offsets)
    key_format = "<H" if key_size == 2 else "<I"
    bad_keys = {
        key: struct.unpack_from(key_format, body, offset)[0]
        for key, offset in offsets.items()
        if offset + key_size > len(body)
        or struct.unpack_from(key_format, body, offset)[0] != key
    }
    return key_size, bounds, bad_keys


def inspect(game_dir: Path, snapshot: Path) -> dict[str, object]:
    vanilla_body, vanilla_header = _extract_vanilla_pair(game_dir)
    patched_body, patched_header = _extract_pair(snapshot / "nppgen")
    vanilla_key_size, vanilla_bounds, vanilla_bad = _index(vanilla_body, vanilla_header)
    patched_key_size, patched_bounds, patched_bad = _index(patched_body, patched_header)

    details: list[dict[str, object]] = []
    vanilla_populated: list[dict[str, object]] = []
    for key, (start, end, name, name_end) in vanilla_bounds.items():
        entry = vanilla_body[start:end]
        arrays = _locate_npc_dye_arrays(entry, name_end - start)
        if arrays is None:
            continue
        count_offset, first_end, _texture_count = arrays
        count = struct.unpack_from("<I", entry, count_offset)[0]
        if count == 0:
            continue
        pairs = [
            struct.unpack_from("<II", entry, count_offset + 4 + index * 8)
            for index in range(count)
        ]
        vanilla_populated.append(
            {
                "key": key,
                "name": name,
                "pairs": pairs,
                "all_targets_match_owner": all(target == key for _group, target in pairs),
                "array_end": first_end,
            }
        )
    for key in sorted(DYE_NPC_KEYS):
        vanilla_start, vanilla_end, name, vanilla_name_end = vanilla_bounds[key]
        patched_start, patched_end, patched_name, patched_name_end = patched_bounds[key]
        vanilla_entry = vanilla_body[vanilla_start:vanilla_end]
        patched_entry = patched_body[patched_start:patched_end]
        vanilla_arrays = _locate_npc_dye_arrays(
            vanilla_entry,
            vanilla_name_end - vanilla_start,
        )
        patched_arrays = _locate_npc_dye_arrays(
            patched_entry,
            patched_name_end - patched_start,
        )
        if vanilla_arrays is None or patched_arrays is None:
            raise ValueError(f"NpcInfo {key} array pair not found")
        vanilla_count_offset, vanilla_first_end, vanilla_texture_count = vanilla_arrays
        patched_count_offset, patched_first_end, patched_texture_count = patched_arrays
        vanilla_count = struct.unpack_from("<I", vanilla_entry, vanilla_count_offset)[0]
        patched_count = struct.unpack_from("<I", patched_entry, patched_count_offset)[0]
        suffix_matches = (
            vanilla_entry[vanilla_first_end:]
            == patched_entry[patched_first_end:]
        )
        original_items_match = (
            vanilla_entry[vanilla_count_offset + 4:vanilla_first_end]
            == patched_entry[patched_count_offset + 4:patched_count_offset + 4 + vanilla_count * 8]
        )
        details.append(
            {
                "key": key,
                "name": name,
                "patched_name": patched_name,
                "delta": len(patched_entry) - len(vanilla_entry),
                "count": [vanilla_count, patched_count],
                "count_offset": [vanilla_count_offset, patched_count_offset],
                "texture_count": [vanilla_texture_count, patched_texture_count],
                "original_items_match": original_items_match,
                "suffix_matches": suffix_matches,
            }
        )

    return {
        "vanilla_body_size": len(vanilla_body),
        "patched_body_size": len(patched_body),
        "body_delta": len(patched_body) - len(vanilla_body),
        "vanilla_entries": len(vanilla_bounds),
        "patched_entries": len(patched_bounds),
        "key_sizes": [vanilla_key_size, patched_key_size],
        "vanilla_bad_header_offsets": vanilla_bad,
        "patched_bad_header_offsets": patched_bad,
        "vanilla_populated_dye_npcs": vanilla_populated,
        "details": details,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--game-dir", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(inspect(args.game_dir, args.snapshot), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
