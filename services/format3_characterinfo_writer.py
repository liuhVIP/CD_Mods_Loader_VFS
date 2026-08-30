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

_LEGACY_FIELD_MAP: dict[str, tuple[str, str, int]] = {
    "upper_chart.group_lookup": ("_upperActionChartPackageGroupName_offset", "<I", 4),
    "lower_chart.group_lookup": ("_lowerActionChartPackageGroupName_offset", "<I", 4),
    "lookup_22": ("_appearanceName_stream_offset", "<I", 4),
    "lookup_24": ("_characterPrefabPath_stream_offset", "<I", 4),
    "skeleton_name": ("_skeletonName_offset", "<I", 4),
    "lookup_25": ("_skeletonVariationName_offset", "<I", 4),
    "flag_c": ("_flagC_offset", "<B", 1),
}

# CrimsonForge 新版 field JSON 使用 CharacterInfo 展开字段名。它与旧版
# Female Animations 的字段命名来自不同 schema，尤其 `skeleton_name` 和
# `lookup_24` 不能直接共用旧映射，必须按整组 intent 方言分流。
_EXPANDED_FIELD_MAP: dict[str, tuple[str, str, int]] = {
    "appearance_name": ("_appearanceName_direct_offset", "<I", 4),
    "character_prefab_path": ("_characterPrefabPath_direct_offset", "<I", 4),
    "skeleton_name": ("_skeletonName_direct_offset", "<I", 4),
    "lookup_22": ("_lookup22_direct_offset", "<I", 4),
    "lookup_24": ("_lookup24_direct_offset", "<I", 4),
    "lookup_25": ("_lookup25_direct_offset", "<I", 4),
    "f36": ("_field36_offset", "<B", 1),
}

_EXPANDED_DIALECT_MARKERS = frozenset(
    {
        "appearance_name",
        "character_prefab_path",
        "default_action_action_index",
        "f36",
        "character_weight",
    }
)
_DEFAULT_ACTION_FIELDS = frozenset({"default_action_action_index", "character_weight"})

# 普通玩家记录的 default-action selector 位于 2.0f 后 4 字节；新版
# CrimsonForge 把 Clone 的同类 selector 错标成 character_weight，它位于
# 1.4f 后 16 字节。两种布局不能共用“锚点后第一项”的定位规则。
_DEFAULT_ACTION_LAYOUTS = {
    "default_action_action_index": (struct.pack("<f", 2.0), 4),
    "character_weight": (struct.pack("<f", 1.4), 16),
}
_DEFAULT_ACTION_MIN_DELTA_FROM_FLAG = 140
_DEFAULT_ACTION_MAX_DELTA_FROM_FLAG = 240
_DEFAULT_ACTION_FOLLOWING_VALUE_MAX = 3

# 真实 CD 1.13 CharacterInfo 中，奖励列表前依次是三个空 farm 数组和
# `_farmBreedingCoolTime=0xffffffff`。该窄锚点已在本次模组 129 条目标记录中逐条验证。
_CHARACTER_REWARD_PREFIX = b"\x00" * 12 + b"\xff" * 4
_CHARACTER_REWARD_FIELD = "character_reward_data_list"
_CHARACTER_REWARD_ITEM_SIZE = 12
_CHARACTER_REWARD_MAX_COUNT = 1024
_UINT32_MAX = 0xFFFFFFFF

CHARACTERINFO_SUPPORTED_FIELDS = frozenset(
    (*_LEGACY_FIELD_MAP, *_EXPANDED_FIELD_MAP, *_DEFAULT_ACTION_FIELDS, _CHARACTER_REWARD_FIELD)
)
CHARACTERINFO_SUPPORTED_FIELD_REASON = (
    "characterinfo 当前仅支持 upper_chart.group_lookup、lower_chart.group_lookup、"
    "lookup_22、lookup_24、skeleton_name、lookup_25、flag_c、"
    "appearance_name、character_prefab_path、default_action_action_index、"
    "character_weight、f36、character_reward_data_list"
)


def build_characterinfo_byte_patch_result(
    context: Format3RuntimeContext,
    intents: list[Format3Intent],
) -> Format3DispatchResult:
    """把 characterinfo intents 转成传统 byte patch。"""
    parsed, name_to_key = _parse_records(context.body, context.entry_bounds)
    changes: list[dict] = []
    skipped: list[Format3SkippedIntent] = []
    expanded_dialect = any(intent.field in _EXPANDED_DIALECT_MARKERS for intent in intents)

    for intent in intents:
        if intent.field == _CHARACTER_REWARD_FIELD:
            change, reason = _build_character_reward_change(context, intent)
        else:
            change, reason = _build_single_change(
                context.body,
                parsed,
                name_to_key,
                intent,
                expanded_dialect=expanded_dialect,
            )
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
        record["_entry_start"] = start
        record["_entry_end"] = end
        name = record.get("name")
        if isinstance(name, str) and name and name not in name_to_key:
            name_to_key[name] = key
    return parsed, name_to_key


def _build_single_change(
    body: bytes,
    parsed: dict[int, dict],
    name_to_key: dict[str, int],
    intent: Format3Intent,
    *,
    expanded_dialect: bool,
) -> tuple[dict | None, str | None]:
    """构造单条 characterinfo patch。"""
    field_map = _EXPANDED_FIELD_MAP if expanded_dialect else _LEGACY_FIELD_MAP
    spec = field_map.get(intent.field)
    is_default_action = expanded_dialect and intent.field in _DEFAULT_ACTION_FIELDS
    if intent.field == "character_weight" and not _is_clone_selector_alias(intent):
        return None, "character_weight 仅兼容 Kliff_Clone 的 PHW selector 错名"
    if spec is None and not is_default_action:
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

    if is_default_action:
        abs_off = _locate_default_action_selector(body, record, intent.field)
        fmt, width = "<I", 4
    else:
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


def _is_clone_selector_alias(intent: Format3Intent) -> bool:
    """限制新 schema 把 Clone selector 错标为 character_weight 的兼容边界。"""
    return intent.entry == "Kliff_Clone" or intent.key == 1001367


def _locate_default_action_selector(
    body: bytes,
    record: dict,
    field: str,
) -> int | None:
    """在当前记录内通过已验证结构锚点定位 PHW 默认动作 selector。"""
    entry_start = record.get("_entry_start")
    entry_end = record.get("_entry_end")
    flag_offset = record.get("_flagC_offset")
    if not all(isinstance(value, int) for value in (entry_start, entry_end, flag_offset)):
        return None

    search_start = flag_offset + _DEFAULT_ACTION_MIN_DELTA_FROM_FLAG
    search_end = min(flag_offset + _DEFAULT_ACTION_MAX_DELTA_FROM_FLAG, entry_end - 12)
    layout = _DEFAULT_ACTION_LAYOUTS.get(field)
    if layout is None:
        return None
    anchor, selector_delta = layout
    candidates: list[int] = []
    pos = search_start
    while pos <= search_end:
        found = body.find(anchor, pos, search_end + len(anchor))
        if found < 0:
            break
        selector_offset = found + selector_delta
        following_offset = selector_offset + 4
        if following_offset + 4 <= entry_end:
            following = struct.unpack_from("<I", body, following_offset)[0]
            if following <= _DEFAULT_ACTION_FOLLOWING_VALUE_MAX:
                candidates.append(selector_offset)
        pos = found + 1
    unique_candidates = sorted(set(candidates))
    return unique_candidates[0] if len(unique_candidates) == 1 else None


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

    resolved = _resolve_bounds(
        context.entry_bounds,
        context.entry_name_index,
        intent,
    )
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
    name_index: dict[str, tuple[int, int, str, int] | None] | None,
    intent: Format3Intent,
) -> tuple[int, int, str, int] | None:
    """优先按唯一 entry 名称定位，缺失或不唯一时回退到 key。

    游戏更新常会重排 PABGB 数字 key，但很少重命名 entry，所以 DMM
    语义里 entry 才是记录的主身份；只有名称缺失或歧义时才信任 key。
    """
    if intent.entry:
        if name_index is not None:
            bounds = name_index.get(intent.entry)
            if bounds is not None:
                return bounds
        else:
            matches = [
                bounds for bounds in entry_bounds.values() if bounds[2] == intent.entry
            ]
            if len(matches) == 1:
                return matches[0]
    bounds = entry_bounds.get(intent.key)
    if bounds is not None:
        return bounds
    return None

def _skip_intent(intent: Format3Intent, reason: str) -> Format3SkippedIntent:
    """构造单条 skipped 结果。"""
    return Format3SkippedIntent(intent=intent, reason=reason)
