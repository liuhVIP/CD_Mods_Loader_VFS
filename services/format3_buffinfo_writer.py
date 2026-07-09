"""BuffInfo Format 3 byte-patch writer。

当前独立版不把 buffinfo 直接走 whole-table，而是先复用 clean-room
`buffinfo_parser.py` 提供的字段定位能力，把可解析的 wrapper / item path
转换成 `entry + rel_offset` 传统 byte patch，继续复用现有 JSON apply 主链。
"""

from __future__ import annotations

import struct

from cdmm.services.buffinfo_parser import locate_buff_field
from cdmm.services.format3_parser import Format3Intent
from cdmm.services.format3_runtime import (
    Format3DispatchResult,
    Format3RuntimeContext,
    Format3SkippedIntent,
)

BUFFINFO_SUPPORTED_FIELD_REASON = (
    "buffinfo 当前仅支持 clean-room parser 可解析的 wrapper 字段和 buff_data_list item path"
)

_BUFFINFO_DTYPE_PACK = {
    "u8": ("B", 1),
    "u16": ("H", 2),
    "u32": ("I", 4),
    "u64": ("Q", 8),
}


def build_buffinfo_byte_patch_result(
    context: Format3RuntimeContext,
    intents: list[Format3Intent],
) -> Format3DispatchResult:
    """把 buffinfo intents 转成 entry+rel_offset byte patch。"""
    if context.key_size != 4:
        return _skip_all(intents, f"buffinfo.pabgh key_size={context.key_size} 无效")

    changes: list[dict] = []
    skipped: list[Format3SkippedIntent] = []
    for intent in intents:
        change, reason = _build_single_change(context, intent)
        if change is None:
            skipped.append(_skip_intent(intent, reason or "buffinfo writer 未生成补丁"))
            continue
        changes.append(change)
    return Format3DispatchResult(
        changes=tuple(changes),
        skipped=tuple(skipped),
    )


def _build_single_change(
    context: Format3RuntimeContext,
    intent: Format3Intent,
) -> tuple[dict | None, str | None]:
    """定位单条 buffinfo intent 并生成 byte patch。"""
    if intent.op != "set":
        return None, "buffinfo 当前仅支持 op=set"
    bounds, reason = _resolve_entry_bounds(context.entry_bounds, intent)
    if bounds is None:
        return None, reason or "目标 entry 未命中"

    entry_off, entry_end, entry_name, name_end = bounds
    entry_bytes = bytes(context.body[entry_off:entry_end])

    located = None
    for candidate in _buffinfo_field_candidates(intent.field):
        try:
            hit = locate_buff_field(entry_bytes, candidate)
        except (ValueError, struct.error):
            hit = None
        if hit is not None:
            located = hit
            break
    if located is None:
        return None, BUFFINFO_SUPPORTED_FIELD_REASON

    rel_in_entry, width, dtype = located
    abs_off = entry_off + rel_in_entry
    if abs_off + width > entry_end:
        return None, "buffinfo 目标字段范围越界"

    if dtype == "cstring":
        return _build_cstring_change(
            context.body,
            entry_end,
            entry_name,
            name_end,
            abs_off,
            intent,
        )

    current_bytes = bytes(context.body[abs_off:abs_off + width])
    if intent.field.endswith(".data.variant.type") or intent.field.endswith(".data.base.tag"):
        packed = _pack_tag_value(dtype, width, current_bytes, intent.new)
        if packed is None:
            return None, "buffinfo variant.type 当前仅支持 no-op 确认写入"
    else:
        packed = _pack_dtype_value(dtype, width, intent.new)
        if packed is None:
            return None, "buffinfo new 值类型不匹配或超出字段范围"

    if current_bytes == packed:
        return None, "目标字节已是期望值"
    return {
        "entry": entry_name or intent.entry,
        "rel_offset": abs_off - name_end,
        "original": current_bytes.hex(),
        "patched": packed.hex(),
        "label": f"{intent.entry}.{intent.field}",
    }, None


def _build_cstring_change(
    body: bytes,
    entry_end: int,
    entry_name: str,
    name_end: int,
    length_pos: int,
    intent: Format3Intent,
) -> tuple[dict | None, str | None]:
    """处理 buffinfo cstring 同长度写入。"""
    if not isinstance(intent.new, str):
        return None, "buffinfo cstring 新值必须是字符串"
    if length_pos + 4 > entry_end:
        return None, "buffinfo cstring length 越界"
    current_len = struct.unpack_from("<I", body, length_pos)[0]
    new_bytes = intent.new.encode("utf-8")
    if len(new_bytes) != current_len:
        return None, "buffinfo cstring 当前仅支持等长写入"
    body_pos = length_pos + 4
    if body_pos + current_len > entry_end:
        return None, "buffinfo cstring 内容越界"
    original = bytes(body[body_pos:body_pos + current_len])
    if original == new_bytes:
        return None, "目标字节已是期望值"
    return {
        "entry": entry_name or intent.entry,
        "rel_offset": body_pos - name_end,
        "original": original.hex(),
        "patched": new_bytes.hex(),
        "label": f"{intent.entry}.{intent.field}",
    }, None


def _resolve_entry_bounds(
    entry_bounds: dict[int, tuple[int, int, str, int]],
    intent: Format3Intent,
) -> tuple[tuple[int, int, str, int] | None, str | None]:
    """优先按 key，缺省或未命中时回退到 entry 名称。"""
    bounds = entry_bounds.get(intent.key)
    if bounds is not None:
        return bounds, None
    if not intent.entry:
        return None, "目标 entry key 未命中"
    matches = [bounds for bounds in entry_bounds.values() if bounds[2] == intent.entry]
    if len(matches) == 1:
        return matches[0], None
    if not matches:
        return None, "目标 entry key/名称 都未命中"
    return None, "目标 entry 名称命中多个记录，存在歧义"


def _buffinfo_field_candidates(field: str) -> list[str]:
    """生成 buffinfo 字段名候选，兼容 snake/camel/leading underscore。"""
    if "[" in field or "." in field:
        return [field]
    candidates = [field, f"_{field}"]
    if "_" in field:
        camel = _snake_to_camel(field)
        if camel != field:
            candidates.extend([camel, f"_{camel}"])
    if any(char.isupper() for char in field):
        candidates.append(_camel_to_snake(field))
    if field.startswith("_"):
        stripped = field[1:]
        candidates.append(stripped)
        if any(char.isupper() for char in stripped):
            candidates.append(_camel_to_snake(stripped))
    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            deduped.append(candidate)
    return deduped


def _snake_to_camel(field: str) -> str:
    """把 snake_case 转成 lowerCamelCase。"""
    head, *tail = field.split("_")
    return head + "".join(chunk[:1].upper() + chunk[1:] for chunk in tail)


def _camel_to_snake(field: str) -> str:
    """把 camelCase 转成 snake_case。"""
    chars: list[str] = []
    for index, char in enumerate(field):
        if char.isupper() and index > 0:
            chars.append("_")
        chars.append(char.lower())
    return "".join(chars)


def _pack_dtype_value(dtype: str, width: int, value: object) -> bytes | None:
    """按 parser 返回的 dtype 打包新值。"""
    spec = _BUFFINFO_DTYPE_PACK.get(dtype)
    if spec is None:
        return None
    fmt, expected_width = spec
    if expected_width != width:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    try:
        return struct.pack(f"<{fmt}", value)
    except struct.error:
        return None


def _pack_tag_value(dtype: str, width: int, current_bytes: bytes, value: object) -> bytes | None:
    """tag 写入只允许 no-op 确认，避免破坏 variant tail 布局。"""
    if dtype != "u8" or width != 1:
        return None
    current_tag = current_bytes[0]
    if isinstance(value, int) and not isinstance(value, bool) and value == current_tag:
        return bytes([current_tag])
    if isinstance(value, str):
        return None
    return None


def _skip_all(intents: list[Format3Intent], reason: str) -> Format3DispatchResult:
    """把整批 buffinfo intents 统一标记为跳过。"""
    return Format3DispatchResult(
        changes=(),
        skipped=tuple(_skip_intent(intent, reason) for intent in intents),
    )


def _skip_intent(intent: Format3Intent, reason: str) -> Format3SkippedIntent:
    """构造单条 skipped 结果。"""
    return Format3SkippedIntent(intent=intent, reason=reason)
