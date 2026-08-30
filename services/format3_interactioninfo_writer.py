"""InteractionInfo Format 3 byte-patch writer。

当前只接入 Fast Pickup 这类真实模组需要的窄字段：
`interaction_type` / `_interactionType`，以及
`interaction_pivot_list[0].raw_a/raw_b`。后者用于 Increase Range，把拾取、
采集、剥皮等交互的第一个 pivot 范围值改大。
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

INTERACTIONINFO_SUPPORTED_FIELD_REASON = (
    "interactioninfo 当前仅支持 interaction_type / _interactionType，"
    "以及 interaction_pivot_list[0].raw_a/raw_b 字段"
)

_SUPPORTED_FIELDS = frozenset({"interaction_type", "_interactionType"})
_PIVOT_FIELD_RE = re.compile(r"^interaction_pivot_list\[0]\.(raw_a|raw_b)$")
_LOCALIZABLE_PREFIX_SIZE = 12
_INTERACTION_PREFIX_SIZE = 4
_PIVOT_SELECTION_SIZE = 1
_PIVOT_COUNT_SIZE = 4


def build_interactioninfo_result(
    context: Format3RuntimeContext,
    intents: list[Format3Intent],
) -> Format3DispatchResult:
    """把 interactioninfo intents 转成传统 byte patch changes。"""
    name_to_keys = _build_name_index(context.entry_bounds)
    changes: list[dict] = []
    skipped: list[Format3SkippedIntent] = []

    for intent in intents:
        change, reason = _build_single_change(context, name_to_keys, intent)
        if change is None:
            skipped.append(_skip_intent(intent, reason or "interactioninfo writer 未生成补丁"))
            continue
        changes.append(change)

    return Format3DispatchResult(changes=tuple(changes), skipped=tuple(skipped))


def _build_name_index(
    entry_bounds: dict[int, tuple[int, int, str, int]],
) -> dict[str, list[int]]:
    """建立 entry 名称到 key 的索引，供 key 缺省时安全回退。"""
    name_to_keys: dict[str, list[int]] = {}
    for key, (_start, _end, name, _name_end) in entry_bounds.items():
        if not name:
            continue
        name_to_keys.setdefault(name, []).append(key)
        lower_name = name.lower()
        if lower_name != name:
            name_to_keys.setdefault(lower_name, []).append(key)
    return name_to_keys


def _build_single_change(
    context: Format3RuntimeContext,
    name_to_keys: dict[str, list[int]],
    intent: Format3Intent,
) -> tuple[dict | None, str | None]:
    """构造单条 interactioninfo patch。"""
    if intent.field in _SUPPORTED_FIELDS:
        return _build_interaction_type_change(context, name_to_keys, intent)
    if _PIVOT_FIELD_RE.match(intent.field):
        return _build_pivot_field_change(context, name_to_keys, intent)
    return None, INTERACTIONINFO_SUPPORTED_FIELD_REASON


def _build_interaction_type_change(
    context: Format3RuntimeContext,
    name_to_keys: dict[str, list[int]],
    intent: Format3Intent,
) -> tuple[dict | None, str | None]:
    """构造 interaction_type 的 u8 patch。"""
    if intent.field not in _SUPPORTED_FIELDS:
        return None, INTERACTIONINFO_SUPPORTED_FIELD_REASON
    if intent.op != "set":
        return None, "interactioninfo 当前仅支持 op=set"
    if isinstance(intent.new, bool) or not isinstance(intent.new, int):
        return None, "interactioninfo 新值必须是 0..255 的整数"
    if intent.new < 0 or intent.new > 0xFF:
        return None, "interactioninfo 新值超出 u8 范围"

    resolved = _resolve_record(context.entry_bounds, name_to_keys, intent)
    if resolved is None:
        return None, "目标 entry key/名称 未命中或存在歧义"

    key, start, end, name, name_end = resolved
    if intent.key and intent.entry and name != intent.entry:
        return None, "目标 key 命中但 entry 名称不匹配"

    payload_start = _payload_start(context.body, name_end, end)
    if payload_start >= end:
        return None, "interactioninfo payload 为空"

    abs_off = payload_start
    original = bytes(context.body[abs_off:abs_off + 1])
    patched = struct.pack("<B", intent.new)
    if original == patched:
        return None, "目标字节已是期望值"

    return {
        "offset": abs_off,
        "original": original.hex(),
        "patched": patched.hex(),
        "entry": name,
        "label": f"{name}.{intent.field}",
        "_format3_key": key,
    }, None


def _build_pivot_field_change(
    context: Format3RuntimeContext,
    name_to_keys: dict[str, list[int]],
    intent: Format3Intent,
) -> tuple[dict | None, str | None]:
    """构造 interaction_pivot_list[0].raw_a/raw_b 的 u32 patch。"""
    match = _PIVOT_FIELD_RE.match(intent.field)
    if match is None:
        return None, INTERACTIONINFO_SUPPORTED_FIELD_REASON
    if intent.op != "set":
        return None, "interactioninfo 当前仅支持 op=set"
    if isinstance(intent.new, bool) or not isinstance(intent.new, int):
        return None, "interactioninfo pivot 新值必须是 u32 整数"
    if intent.new < 0 or intent.new > 0xFFFFFFFF:
        return None, "interactioninfo pivot 新值超出 u32 范围"

    resolved = _resolve_record(context.entry_bounds, name_to_keys, intent)
    if resolved is None:
        return None, "目标 entry key/名称 未命中或存在歧义"

    key, _start, end, name, name_end = resolved
    if intent.key and intent.entry and name != intent.entry:
        return None, "目标 key 命中但 entry 名称不匹配"

    raw_offsets = _locate_first_pivot_raw_offsets(context.body, name_end, end)
    if raw_offsets is None:
        return None, "interactioninfo 第一个 pivot 定位失败"
    raw_a_offset, raw_b_offset = raw_offsets
    abs_off = raw_a_offset if match.group(1) == "raw_a" else raw_b_offset
    if abs_off + 4 > end:
        return None, "interactioninfo pivot 字段范围越界"

    original = bytes(context.body[abs_off:abs_off + 4])
    patched = struct.pack("<I", intent.new)
    if original == patched:
        return None, "目标字节已是期望值"

    return {
        "offset": abs_off,
        "original": original.hex(),
        "patched": patched.hex(),
        "entry": name,
        "label": f"{name}.{intent.field}",
        "_format3_key": key,
    }, None


def _resolve_record(
    entry_bounds: dict[int, tuple[int, int, str, int]],
    name_to_keys: dict[str, list[int]],
    intent: Format3Intent,
) -> tuple[int, int, int, str, int] | None:
    """按唯一 entry 名称优先、缺失或不唯一时回退 key 解析目标记录。

    游戏更新常会重排 PABGB 数字 key，但很少重命名 entry，所以 DMM
    语义里 entry 才是记录的主身份；只有名称缺失或歧义时才信任 key。
    """
    if intent.entry:
        keys = name_to_keys.get(intent.entry) or name_to_keys.get(intent.entry.lower())
        if keys and len(set(keys)) == 1:
            key = keys[0]
            start, end, name, name_end = entry_bounds[key]
            return key, start, end, name, name_end

    if intent.key:
        bound = entry_bounds.get(intent.key)
        if bound is None:
            return None
        start, end, name, name_end = bound
        return intent.key, start, end, name, name_end

    return None


def _payload_start(body: bytes, name_end: int, entry_end: int) -> int:
    """返回 entry 名称后的 payload 起点。"""
    if name_end < entry_end and name_end < len(body) and body[name_end] == 0:
        return name_end + 1
    return name_end


def _locate_first_pivot_raw_offsets(
    body: bytes,
    name_end: int,
    entry_end: int,
) -> tuple[int, int] | None:
    """定位 `interaction_pivot_list[0].raw_a/raw_b` 的绝对偏移。

    当前只解析 Fast Pickup Range 需要的前缀和第一个 pivot：
    `4*u8 + LocalizableString + pivot_selection_target + CArray count`。
    pivot 内部只走到四组 `CString + vec3` 后面的 raw_a/raw_b。
    """
    payload = _payload_start(body, name_end, entry_end)
    cursor = payload + _INTERACTION_PREFIX_SIZE
    if cursor + _LOCALIZABLE_PREFIX_SIZE > entry_end:
        return None
    string_len = struct.unpack_from("<I", body, cursor + 8)[0]
    if string_len > 50000:
        return None
    cursor += _LOCALIZABLE_PREFIX_SIZE + string_len
    if cursor + _PIVOT_SELECTION_SIZE + _PIVOT_COUNT_SIZE > entry_end:
        return None

    cursor += _PIVOT_SELECTION_SIZE
    pivot_count = struct.unpack_from("<I", body, cursor)[0]
    cursor += _PIVOT_COUNT_SIZE
    if pivot_count < 1:
        return None

    return _locate_pivot_raw_pair(body, cursor, entry_end)


def _locate_pivot_raw_pair(
    body: bytes,
    pivot_start: int,
    entry_end: int,
) -> tuple[int, int] | None:
    """定位单个 InteractionPivotData 的 raw_a/raw_b。"""
    cursor = pivot_start + 4
    if cursor > entry_end:
        return None
    for _index in range(4):
        cursor = _read_cstring_end(body, cursor, entry_end)
        if cursor is None:
            return None
        cursor += 12
        if cursor > entry_end:
            return None
    if cursor + 8 > entry_end:
        return None
    return cursor, cursor + 4


def _read_cstring_end(
    body: bytes,
    offset: int,
    entry_end: int,
) -> int | None:
    """读取 u32 length + bytes 的 CString，并返回结束位置。"""
    if offset + 4 > entry_end:
        return None
    length = struct.unpack_from("<I", body, offset)[0]
    if length > 50000:
        return None
    end = offset + 4 + length
    if end > entry_end:
        return None
    return end


def _skip_intent(intent: Format3Intent, reason: str) -> Format3SkippedIntent:
    """构造单条 skipped 结果。"""
    return Format3SkippedIntent(intent=intent, reason=reason)
