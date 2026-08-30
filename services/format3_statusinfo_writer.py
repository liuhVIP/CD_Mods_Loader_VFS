"""StatusInfo Format 3 窄字段写入器。

当前支持原版记录中的 ``stat_level_data[N]``：一个 u32 数量后紧跟
对应数量的 u64 等级值。只在候选唯一时写入，避免误认记录内其他数组。
"""

from __future__ import annotations

import re
import struct

from cdmm.services.format3_parser import Format3Intent
from cdmm.services.format3_runtime import (
    Format3DispatchResult,
    Format3RuntimeContext,
    Format3SkippedIntent,
)

STAT_LEVEL_FIELD_RE = re.compile(r"^stat_level_data\[(?P<index>\d+)]$")
MAX_STATUS_LEVEL_COUNT = 1024


def build_statusinfo_result(
    context: Format3RuntimeContext,
    intents: list[Format3Intent],
) -> Format3DispatchResult:
    """把 statusinfo 等级数组 intent 转为 entry-relative u64 patch。"""
    changes: list[dict] = []
    skipped: list[Format3SkippedIntent] = []
    required_index_by_entry: dict[tuple[int, str], int] = {}
    for intent in intents:
        match = STAT_LEVEL_FIELD_RE.match(intent.field)
        if match is None:
            continue
        identity = (intent.key, intent.entry or "")
        required_index_by_entry[identity] = max(
            required_index_by_entry.get(identity, -1),
            int(match.group("index")),
        )
    for intent in intents:
        required_index = required_index_by_entry.get((intent.key, intent.entry or ""))
        change, reason = _build_change(context, intent, required_index)
        if change is None:
            skipped.append(Format3SkippedIntent(intent=intent, reason=reason))
        else:
            changes.append(change)
    return Format3DispatchResult(tuple(changes), tuple(skipped))


def _build_change(
    context: Format3RuntimeContext,
    intent: Format3Intent,
    required_index: int | None,
) -> tuple[dict | None, str]:
    """定位一条 statusinfo 等级值并生成补丁。"""
    match = STAT_LEVEL_FIELD_RE.match(intent.field)
    if intent.op != "set" or match is None:
        return None, "statusinfo 当前仅支持 stat_level_data[N] op=set"
    if isinstance(intent.new, bool) or not isinstance(intent.new, int) or not 0 <= intent.new <= 0xFFFFFFFFFFFFFFFF:
        return None, "statusinfo stat_level_data 新值必须是u64"
    bounds = None
    if intent.entry:
        if context.entry_name_index is not None:
            bounds = context.entry_name_index.get(intent.entry)
        else:
            matches = [item for item in context.entry_bounds.values() if item[2] == intent.entry]
            bounds = matches[0] if len(matches) == 1 else None
    if bounds is None:
        bounds = context.entry_bounds.get(intent.key)
    if bounds is None:
        return None, "statusinfo 目标entry未唯一命中"

    entry_off, entry_end, entry_name, name_end = bounds
    level_index = int(match.group("index"))
    record = context.body[entry_off:entry_end]
    array_start = _locate_stat_level_array(
        record,
        name_end - entry_off,
        max(level_index, required_index or 0),
    )
    if array_start is None:
        return None, "statusinfo stat_level_data数组未唯一定位"
    value_off = entry_off + array_start + 4 + level_index * 8
    original = bytes(context.body[value_off:value_off + 8])
    patched = struct.pack("<Q", intent.new)
    if original == patched:
        return None, "目标字节已是期望值"
    return {
        "entry": entry_name or intent.entry,
        "rel_offset": value_off - name_end,
        "original": original.hex(),
        "patched": patched.hex(),
        "label": f"{entry_name}.stat_level_data[{level_index}]",
    }, ""


def _locate_stat_level_array(record: bytes, scan_start: int, required_index: int) -> int | None:
    """在记录内唯一定位 count + u64[count] 数组。"""
    candidates: list[int] = []
    for offset in range(max(0, scan_start), len(record) - 4):
        count = struct.unpack_from("<I", record, offset)[0]
        if count <= required_index or count > MAX_STATUS_LEVEL_COUNT:
            continue
        end = offset + 4 + count * 8
        if end <= len(record):
            candidates.append(offset)
    return candidates[0] if len(candidates) == 1 else None
