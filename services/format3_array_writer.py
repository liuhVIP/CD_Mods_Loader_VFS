"""Format 3 append writers for DyeColorGroupInfo and NpcInfo arrays."""

from __future__ import annotations

import struct
from collections import defaultdict

from cdmm.services.format3_parser import Format3Intent
from cdmm.services.format3_runtime import (
    Format3DispatchResult,
    Format3RuntimeContext,
    Format3SkippedIntent,
)


def build_dyecolorgroupinfo_result(
    context: Format3RuntimeContext,
    intents: list[Format3Intent],
) -> Format3DispatchResult:
    return _build_array_result(context, intents, kind="color")


def build_npcinfo_result(
    context: Format3RuntimeContext,
    intents: list[Format3Intent],
) -> Format3DispatchResult:
    return _build_array_result(context, intents, kind="npc")


def _build_array_result(
    context: Format3RuntimeContext,
    intents: list[Format3Intent],
    *,
    kind: str,
) -> Format3DispatchResult:
    field = "dye_color_data_list" if kind == "color" else "dye_color_group_data_list"
    grouped: dict[int, list[Format3Intent]] = defaultdict(list)
    skipped: list[Format3SkippedIntent] = []
    by_name = {
        bounds[2]: key
        for key, bounds in context.entry_bounds.items()
        if bounds[2]
    }
    for intent in intents:
        if intent.field != field or intent.op != "array_append" or not isinstance(intent.new, dict):
            skipped.append(
                Format3SkippedIntent(intent, f"{context.table_name} 仅支持 {field} array_append")
            )
            continue
        key = intent.key if intent.key in context.entry_bounds else by_name.get(intent.entry)
        if key is None:
            skipped.append(Format3SkippedIntent(intent, "目标记录未命中"))
            continue
        grouped[key].append(intent)

    replacements: dict[int, tuple[int, bytes]] = {}
    deltas: list[tuple[int, int]] = []
    for key, entry_intents in grouped.items():
        start, end, _name, name_end = context.entry_bounds[key]
        entry = context.body[start:end]
        if kind == "color":
            count_offset = name_end + 1 - start
            element_size = 8
            located = _locate_dye_color_array(entry, count_offset)
        else:
            located = _locate_npc_dye_arrays(entry, name_end - start)
            count_offset = located[0] if located is not None else None
            element_size = 8
        if located is None or count_offset is None:
            skipped.extend(
                Format3SkippedIntent(intent, f"{field} count 锚点越界")
                for intent in entry_intents
            )
            continue
        count = struct.unpack_from("<I", entry, count_offset)[0]
        old_end = count_offset + 4 + count * element_size

        additions = bytearray()
        accepted = 0
        for intent in entry_intents:
            try:
                if kind == "color":
                    additions += struct.pack(
                        "<II",
                        _u32(intent.new, "texture_lookup"),
                        _u32(intent.new, "raw_color"),
                    )
                else:
                    additions += struct.pack(
                        "<II",
                        _u32(intent.new, "dye_color_group_key"),
                        _u32(intent.new, "dye_target_key"),
                    )
                accepted += 1
            except (KeyError, TypeError, ValueError, struct.error):
                skipped.append(Format3SkippedIntent(intent, f"{field} 元素字段不完整"))
        if not accepted:
            continue

        patched_entry = bytearray(entry)
        struct.pack_into("<I", patched_entry, count_offset, count + accepted)
        patched_entry[old_end:old_end] = additions
        replacements[start] = (end, bytes(patched_entry))
        deltas.append((start, len(additions)))

    if not replacements:
        return Format3DispatchResult((), tuple(skipped))

    patched_body = bytearray(context.body)
    for start in sorted(replacements, reverse=True):
        end, patched_entry = replacements[start]
        patched_body[start:end] = patched_entry
    patched_header = _rebuild_header(context.header, context.key_size, deltas)

    changes: list[dict] = [
        {
            "offset": 0,
            "original": context.body.hex(),
            "patched": bytes(patched_body).hex(),
            "label": f"{context.table_name} whole-table array append",
        }
    ]
    if patched_header != context.header:
        changes.append(
            {
                "offset": 0,
                "original": context.header.hex(),
                "patched": patched_header.hex(),
                "label": f"{context.table_name}.pabgh offset rebuild",
                "_target_file": context.game_file.rsplit(".", 1)[0] + ".pabgh",
            }
        )
    return Format3DispatchResult(tuple(changes), tuple(skipped))


def _u32(value: dict, field: str) -> int:
    item = value[field]
    if isinstance(item, bool) or not isinstance(item, int) or not 0 <= item <= 0xFFFFFFFF:
        raise ValueError(field)
    return item


def _locate_dye_color_array(entry: bytes, count_offset: int) -> tuple[int, int] | None:
    """Validate the color array and its LocalizableString/u32 footer."""
    if count_offset < 0 or count_offset + 4 > len(entry):
        return None
    count = struct.unpack_from("<I", entry, count_offset)[0]
    array_end = count_offset + 4 + count * 8
    footer_end = _consume_localizable_string(entry, array_end)
    if footer_end is None or footer_end + 4 != len(entry):
        return None
    return count_offset, array_end


def _locate_npc_dye_arrays(entry: bytes, name_end: int) -> tuple[int, int, int] | None:
    """Walk the complete NpcInfo 1.18 prefix and locate its dye arrays."""
    cursor = name_end
    fixed_prefix_size = 1 + 4 + 2 + 4 * 4 + 2
    if cursor < 0 or cursor + fixed_prefix_size > len(entry):
        return None
    cursor += fixed_prefix_size

    for _ in range(3):
        consumed = _consume_localizable_string(entry, cursor)
        if consumed is None:
            return None
        cursor = consumed

    # 1.18 inserted an opaque u32 and two localizable strings here.
    if cursor + 4 > len(entry):
        return None
    cursor += 4
    for _ in range(2):
        consumed = _consume_localizable_string(entry, cursor)
        if consumed is None:
            return None
        cursor = consumed

    # This lookup precedes the dye-group array and is not its count.
    if cursor + 4 > len(entry):
        return None
    cursor += 4

    count_offset = cursor
    if count_offset + 4 > len(entry):
        return None
    count = struct.unpack_from("<I", entry, count_offset)[0]
    if count > 4096:
        return None
    first_end = count_offset + 4 + count * 8
    if first_end + 4 > len(entry):
        return None
    texture_count = struct.unpack_from("<I", entry, first_end)[0]
    if texture_count > 4096:
        return None
    texture_end = first_end + 4 + texture_count * 6
    if texture_end + 4 > len(entry):
        return None

    # 1.18 added a final u16 lookup array after DyeTextureSetData.
    final_count = struct.unpack_from("<I", entry, texture_end)[0]
    if final_count > 4096 or texture_end + 4 + final_count * 2 != len(entry):
        return None
    return count_offset, first_end, texture_count


def _consume_localizable_string(data: bytes, offset: int) -> int | None:
    if offset < 0 or offset + 13 > len(data):
        return None
    string_size = struct.unpack_from("<I", data, offset + 9)[0]
    end = offset + 13 + string_size
    return end if end <= len(data) else None


def _rebuild_header(
    header: bytes,
    key_size: int,
    deltas: list[tuple[int, int]],
) -> bytes:
    for count_size, fmt in ((2, "<H"), (4, "<I")):
        if len(header) < count_size:
            continue
        count = struct.unpack_from(fmt, header, 0)[0]
        if count_size + count * (key_size + 4) == len(header):
            break
    else:
        raise ValueError("PABGH count/length mismatch")

    output = bytearray(header)
    pos = count_size
    for _ in range(count):
        old_offset = struct.unpack_from("<I", header, pos + key_size)[0]
        new_offset = old_offset + sum(delta for start, delta in deltas if old_offset > start)
        struct.pack_into("<I", output, pos + key_size, new_offset)
        pos += key_size + 4
    return bytes(output)
