"""CharacterInfo Format 3 byte-patch writer。

当前独立版先迁入参考仓库已验证的 `characterinfo.pabgb` 字段子集。由于这些
字段都是固定宽度 primitive，writer 只需要通过专用 parser 解析记录并定位
绝对偏移，再输出传统 byte patch 即可，不涉及 whole-table / companion
`.pabgh` 重建。
"""

from __future__ import annotations

import struct

from cdmm.services.characterinfo_full_parser import parse_entry
from cdmm.services.format3_parser import Format3Intent
from cdmm.services.format3_runtime import (
    Format3DispatchResult,
    Format3RuntimeContext,
    Format3SkippedIntent,
)

_FIELD_MAP: dict[str, tuple[str, str, int]] = {
    "upper_chart.group_lookup": ("_upperActionChartPackageGroupName_offset", "<I", 4),
    "lower_chart.group_lookup": ("_lowerActionChartPackageGroupName_offset", "<I", 4),
    "lookup_22": ("_appearanceName_stream_offset", "<I", 4),
    "lookup_24": ("_characterPrefabPath_stream_offset", "<I", 4),
    "skeleton_name": ("_skeletonName_offset", "<I", 4),
    "lookup_25": ("_skeletonVariationName_offset", "<I", 4),
    "flag_c": ("_flagC_offset", "<B", 1),
}

# 真实 CD 1.13 CharacterInfo 中，奖励列表前依次是三个空 farm 数组和
# `_farmBreedingCoolTime=0xffffffff`。该窄锚点已在本次模组 129 条目标记录中逐条验证。
_CHARACTER_REWARD_PREFIX = b"\x00" * 12 + b"\xff" * 4
_CHARACTER_REWARD_FIELD = "character_reward_data_list"
_CHARACTER_REWARD_ITEM_SIZE = 12
_CHARACTER_REWARD_MAX_COUNT = 1024
_UINT32_MAX = 0xFFFFFFFF

CHARACTERINFO_SUPPORTED_FIELDS = frozenset((*_FIELD_MAP, _CHARACTER_REWARD_FIELD))
CHARACTERINFO_SUPPORTED_FIELD_REASON = (
    "characterinfo 当前仅支持 upper_chart.group_lookup、lower_chart.group_lookup、"
    "lookup_22、lookup_24、skeleton_name、lookup_25、flag_c、character_reward_data_list"
)


def build_characterinfo_byte_patch_result(
    context: Format3RuntimeContext,
    intents: list[Format3Intent],
) -> Format3DispatchResult:
    """把 characterinfo intents 转成传统 byte patch。"""
    parsed, name_to_key = _parse_records(context.body, context.entry_bounds)
    changes: list[dict] = []
    skipped: list[Format3SkippedIntent] = []

    for intent in intents:
        if intent.field == _CHARACTER_REWARD_FIELD:
            change, reason = _build_character_reward_change(context, intent)
        else:
            change, reason = _build_single_change(context.body, parsed, name_to_key, intent)
        if change is None:
            skipped.append(_skip_intent(intent, reason or "characterinfo writer 未生成补丁"))
            continue
        changes.append(change)

    return Format3DispatchResult(
        changes=tuple(changes),
        skipped=tuple(skipped),
    )


def _parse_records(
    body: bytes,
    entry_bounds: dict[int, tuple[int, int, str, int]],
) -> tuple[dict[int, dict], dict[str, int]]:
    """按 entry_bounds 解析 characterinfo 记录，并建立名称索引。"""
    parsed: dict[int, dict] = {}
    name_to_key: dict[str, int] = {}
    for key, (start, end, _name, _name_end) in entry_bounds.items():
        record = parse_entry(body, start, end)
        if record is None:
            continue
        parsed[key] = record
        name = record.get("name")
        if isinstance(name, str) and name and name not in name_to_key:
            name_to_key[name] = key
    return parsed, name_to_key


def _build_single_change(
    body: bytes,
    parsed: dict[int, dict],
    name_to_key: dict[str, int],
    intent: Format3Intent,
) -> tuple[dict | None, str | None]:
    """构造单条 characterinfo patch。"""
    spec = _FIELD_MAP.get(intent.field)
    if spec is None:
        return None, CHARACTERINFO_SUPPORTED_FIELD_REASON
    if intent.op != "set":
        return None, "characterinfo 当前仅支持 op=set"
    if isinstance(intent.new, bool) or not isinstance(intent.new, int):
        return None, "characterinfo 新值必须是整数"

    key = name_to_key.get(intent.entry)
    if key is None and intent.key:
        key = intent.key
    record = parsed.get(key) if key is not None else None
    if record is None:
        return None, "目标 entry key/名称 都未命中"

    offset_key, fmt, width = spec
    abs_off = record.get(offset_key)
    if not isinstance(abs_off, int):
        return None, "characterinfo 目标字段定位失败"
    if abs_off + width > len(body):
        return None, "characterinfo 目标字段范围越界"
    try:
        patched = struct.pack(fmt, intent.new)
    except struct.error:
        return None, "characterinfo 新值超出字段范围"

    original = bytes(body[abs_off:abs_off + width])
    if original == patched:
        return None, "目标字节已是期望值"
    return {
        "offset": abs_off,
        "original": original.hex(),
        "patched": patched.hex(),
        "label": f"{intent.entry}.{intent.field}",
    }, None


def _build_character_reward_change(
    context: Format3RuntimeContext,
    intent: Format3Intent,
) -> tuple[dict | None, str | None]:
    """整段替换一条 CharacterInfo 记录内的 CharacterRewardData 数组。"""
    if intent.op != "set":
        return None, "characterinfo character_reward_data_list 当前仅支持 op=set"
    patched, reason = _serialize_character_rewards(intent.new)
    if patched is None:
        return None, reason

    resolved = _resolve_bounds(context.entry_bounds, intent)
    if resolved is None:
        return None, "目标 entry key/名称 都未命中"
    entry_start, entry_end, entry_name, name_end = resolved
    record = context.body[entry_start:entry_end]
    list_offset = record.find(_CHARACTER_REWARD_PREFIX)
    if list_offset < 0:
        return None, "characterinfo character_reward_data_list 窄锚点未命中"
    list_offset += len(_CHARACTER_REWARD_PREFIX)
    if list_offset + 4 > len(record):
        return None, "characterinfo character_reward_data_list count 越界"
    old_count = struct.unpack_from("<I", record, list_offset)[0]
    if old_count > _CHARACTER_REWARD_MAX_COUNT:
        return None, "characterinfo character_reward_data_list 原记录数量不可信"
    old_end = list_offset + 4 + old_count * _CHARACTER_REWARD_ITEM_SIZE
    if old_end > len(record):
        return None, "characterinfo character_reward_data_list 原记录范围越界"

    original = bytes(record[list_offset:old_end])
    if original == patched:
        return None, "目标字节已是期望值"
    return {
        "entry": entry_name,
        "rel_offset": entry_start + list_offset - name_end,
        "original": original.hex(),
        "patched": patched.hex(),
        "label": f"{entry_name or intent.key}.{_CHARACTER_REWARD_FIELD}",
        "_dynamic_entry_offset": True,
    }, None


def _serialize_character_rewards(value: object) -> tuple[bytes | None, str | None]:
    """序列化 `u32 count + CharacterRewardData[count]`。"""
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        return None, "characterinfo character_reward_data_list 新值必须是对象数组"
    if len(value) > _CHARACTER_REWARD_MAX_COUNT:
        return None, "characterinfo character_reward_data_list 新记录数量过大"

    output = bytearray(struct.pack("<I", len(value)))
    fields = ("drop_set_info", "reward_tag_type_flag", "repeat_count")
    for item in value:
        numbers: list[int] = []
        for field in fields:
            raw = item.get(field)
            if isinstance(raw, bool) or not isinstance(raw, int) or not 0 <= raw <= _UINT32_MAX:
                return None, f"characterinfo character_reward_data_list.{field} 必须是 u32"
            numbers.append(raw)
        output += struct.pack("<III", *numbers)
    return bytes(output), None


def _resolve_bounds(
    entry_bounds: dict[int, tuple[int, int, str, int]],
    intent: Format3Intent,
) -> tuple[int, int, str, int] | None:
    """优先按 key 定位，缺省 key 时再按唯一名称定位。"""
    bounds = entry_bounds.get(intent.key)
    if bounds is not None:
        return bounds
    if not intent.entry:
        return None
    matches = [bounds for bounds in entry_bounds.values() if bounds[2] == intent.entry]
    if len(matches) == 1:
        return matches[0]
    return None


def _skip_intent(intent: Format3Intent, reason: str) -> Format3SkippedIntent:
    """构造单条 skipped 结果。"""
    return Format3SkippedIntent(intent=intent, reason=reason)
