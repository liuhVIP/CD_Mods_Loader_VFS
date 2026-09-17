"""Build the minimal witch-store A/B diagnostic Format 3 mod."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from copy import deepcopy
from pathlib import Path

from cdmm.archive.pamt import parse_pamt_filtered
from cdmm.services.json_loader import extract_plaintext
from cdmm.services.pab_table_service import build_entry_bounds, parse_pabgh_index
from cdmm.services.storeinfo_native_parser import (
    parse_storeinfo_entry,
    stock_record_from_json,
)
from cdmm.tools.build_all_craft_early_barber_one_copper_mod import (
    _extract_vanilla_storeinfo,
)

WITCH_STORE_KEY = 902
WITCH_STORE_NAME = "Store_Her_Witch"
STOREINFO_TARGET = "storeinfo.pabgb"
STOREINFO_HEADER = "storeinfo.pabgh"


def build_diagnostic_mod(
    game_dir: Path,
    source_path: Path,
    output_path: Path,
    witch_mode: str,
) -> list[int]:
    with source_path.open("r", encoding="utf-8-sig") as handle:
        source = json.load(handle)
    if source.get("format") != 3 or not isinstance(source.get("targets"), list):
        raise ValueError("source is not a multi-target Format 3 mod")

    store_target = next(
        (target for target in source["targets"] if target.get("file") == STOREINFO_TARGET),
        None,
    )
    if store_target is None or not isinstance(store_target.get("intents"), list):
        raise ValueError("source has no StoreInfo target")

    vanilla_body, vanilla_header = _extract_vanilla_storeinfo(game_dir)
    key_size, offsets = parse_pabgh_index(vanilla_header, "storeinfo")
    bounds = build_entry_bounds(vanilla_body, key_size, offsets)
    if WITCH_STORE_KEY not in bounds:
        raise ValueError("current vanilla Store_Her_Witch was not parsed")
    start, end, _name, _name_end = bounds[WITCH_STORE_KEY]
    witch_entry = parse_storeinfo_entry(vanilla_body[start:end], key_size)
    current_items = {record.value.payload.body for record in witch_entry.stock_data_list}
    store_templates = witch_entry.stock_data_list

    selected: list[dict] = []
    selected_keys: list[int] = []
    for intent in store_target["intents"]:
        if intent.get("key") != WITCH_STORE_KEY or intent.get("entry") != WITCH_STORE_NAME:
            continue
        if intent.get("field") != "stock_data_list" or intent.get("op") != "array_append":
            continue
        value = intent.get("value", intent.get("new"))
        requested = stock_record_from_json(value)
        if requested.value.payload.body not in current_items:
            continue
        selected.append(deepcopy(intent))
        selected_keys.append(requested.value.payload.body)

    if len(selected) != 13:
        raise ValueError(f"expected 13 current-item witch records, got {len(selected)}")

    if witch_mode == "current-items":
        final_count = len(store_templates) + len(selected)
        replacement_witch_intents = selected + [
            {
                "entry": WITCH_STORE_NAME,
                "key": WITCH_STORE_KEY,
                "op": "set",
                "field": "buyable_stock_count",
                "new": final_count,
            }
        ]
    elif witch_mode == "vanilla":
        replacement_witch_intents = []
    else:
        raise ValueError(f"unsupported witch mode: {witch_mode}")

    replacement_intents: list[dict] = []
    inserted_witch = False
    for intent in store_target["intents"]:
        is_witch = (
            intent.get("key") == WITCH_STORE_KEY
            and intent.get("entry") == WITCH_STORE_NAME
        )
        if not is_witch:
            replacement_intents.append(deepcopy(intent))
            continue
        if not inserted_witch:
            replacement_intents.extend(replacement_witch_intents)
            inserted_witch = True
    if not inserted_witch:
        raise ValueError("source has no Store_Her_Witch intents")

    modinfo = deepcopy(source.get("modinfo", {}))
    modinfo["title"] = f"Expanded Vendor Witch A-B - {witch_mode}"
    modinfo["description"] = f"Diagnostic witch StoreInfo mode: {witch_mode}."
    output = deepcopy(source)
    output["modinfo"] = modinfo
    output_store_target = next(
        target for target in output["targets"] if target.get("file") == STOREINFO_TARGET
    )
    output_store_target["intents"] = replacement_intents
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(output, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return selected_keys


def inspect_snapshot(snapshot: Path) -> dict[str, object]:
    package_dir = snapshot / "nppgen"
    matches = {
        Path(entry.path).name.lower(): entry
        for entry in parse_pamt_filtered(
            package_dir / "0.pamt",
            package_dir,
            desired_basenames={STOREINFO_TARGET, STOREINFO_HEADER},
        )
    }
    if STOREINFO_TARGET not in matches or STOREINFO_HEADER not in matches:
        raise ValueError("snapshot nppgen has no complete StoreInfo pair")
    body, _ = extract_plaintext(matches[STOREINFO_TARGET])
    header, _ = extract_plaintext(matches[STOREINFO_HEADER])
    key_size, offsets = parse_pabgh_index(header, "storeinfo")
    bounds = build_entry_bounds(body, key_size, offsets)
    start, end, name, _name_end = bounds[WITCH_STORE_KEY]
    entry = parse_storeinfo_entry(body[start:end], key_size)
    records = entry.stock_data_list
    buyable_count = entry.buyable_stock_count
    if name != WITCH_STORE_NAME:
        raise ValueError("witch StoreInfo entry boundary is invalid")

    restore_stores: dict[int, set[int]] = defaultdict(set)
    for store_key, (store_start, store_end, _n, _ne) in bounds.items():
        store_entry = parse_storeinfo_entry(body[store_start:store_end], key_size)
        for record in store_entry.stock_data_list:
            if record.is_restore_item:
                restore_stores[record.value.payload.body].add(store_key)
    duplicate_restore = {
        item_key: sorted(store_keys)
        for item_key, store_keys in restore_stores.items()
        if len(store_keys) > 1
    }
    return {
        "entries": len(bounds),
        "witch_count": len(records),
        "witch_buyable_count": buyable_count,
        "witch_unique_items": len({record.value.payload.body for record in records}),
        "witch_item_keys": [record.value.payload.body for record in records],
        "witch_discriminators": sorted({record.value.disc for record in records}),
        "duplicate_restore_items": len(duplicate_restore),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--game-dir", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--witch-mode",
        choices=("current-items", "vanilla"),
        default="current-items",
    )
    parser.add_argument("--validate-snapshot", type=Path)
    args = parser.parse_args()
    selected_keys = build_diagnostic_mod(
        args.game_dir,
        args.source,
        args.output,
        args.witch_mode,
    )
    print(f"Witch diagnostic item keys ({len(selected_keys)}): {selected_keys}")
    print(f"Output: {args.output}")
    if args.validate_snapshot is not None:
        print(json.dumps(inspect_snapshot(args.validate_snapshot), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
