"""DropSetInfo Format 3 writer 适配层。

参考仓库的 dropset writer 会把 `field=drops` 的 JSON 列表转换成一条
`entry + rel_offset=0` 的 record-body replace change。本模块负责在独立版
runtime 中解析目标记录，并把 key 缺省的 intent 回退到 entry 名称解析出的
真实 key。
"""

from __future__ import annotations

import base64
import binascii
import struct

from cdmm.services.dropset_writer import build_drops_replacement_change, parse_dropset_record
from cdmm.services.format3_parser import FORMAT3_NEW_RECORD_FIELD, Format3Intent
from cdmm.services.format3_runtime import (
    Format3DispatchResult,
    Format3RuntimeContext,
    Format3SkippedIntent,
)

DROPSETINFO_SUPPORTED_FIELD_REASON = "dropsetinfo 当前仅支持 drops 字段和 new_record 完整记录模板"

# dropsetinfo.pabgh 使用 u16 count，随后每项为 u32 key + u32 body offset。
_DROPSET_PABGH_COUNT_SIZE = 2
_DROPSET_PABGH_ENTRY_SIZE = 8


def build_dropsetinfo_result(
    context: Format3RuntimeContext,
    intents: list[Format3Intent],
) -> Format3DispatchResult:
    """把 dropsetinfo intents 转成传统 byte patch changes。"""
    changes: list[dict] = []
    skipped: list[Format3SkippedIntent] = []

    for intent in intents:
        if intent.field == FORMAT3_NEW_RECORD_FIELD:
            change, reason = _build_new_record_change(context, intent)
        else:
            change, reason = _build_single_change(context, intent)
        if change is None:
            skipped.append(_skip_intent(intent, reason or "dropsetinfo writer 未生成补丁"))
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
    """构造单条 dropsetinfo change。"""
    if intent.field != "drops":
        return None, DROPSETINFO_SUPPORTED_FIELD_REASON
    if intent.op != "set":
        return None, "dropsetinfo 当前仅支持 op=set"
    if not isinstance(intent.new, list) or not all(isinstance(item, dict) for item in intent.new):
        return None, "dropsetinfo drops 新值必须是对象数组"

    resolved = _resolve_entry(context.entry_bounds, intent)
    if resolved is None:
        return None, "目标 entry key/名称 都未命中"
    key, bounds = resolved
    entry_start, entry_end, entry_name, _name_end = bounds
    record = bytes(context.body[entry_start:entry_end])
    change = build_drops_replacement_change(
        record,
        intent_key=key,
        intent_entry=entry_name or intent.entry,
        new_drops_json=intent.new,
    )
    if change is None:
        return None, "dropsetinfo record 解析或序列化失败"
    return change, None


def _build_new_record_change(
    context: Format3RuntimeContext,
    intent: Format3Intent,
) -> tuple[dict | None, str | None]:
    """把 `_blob_b64` 完整记录追加到 PABGB，并同步扩展 PABGH。"""
    if intent.op != "new_record":
        return None, "dropsetinfo 新记录必须使用 op=new_record"
    if intent.key in context.entry_bounds:
        return None, f"dropsetinfo new_record key={intent.key} 已存在"
    if not isinstance(intent.new, dict):
        return None, "dropsetinfo new_record template 必须是对象"
    blob_text = intent.new.get("_blob_b64")
    if not isinstance(blob_text, str) or not blob_text:
        return None, "dropsetinfo new_record template 缺少 _blob_b64"
    try:
        record = base64.b64decode(blob_text, validate=True)
    except (ValueError, binascii.Error):
        return None, "dropsetinfo new_record _blob_b64 不是有效 Base64"
    try:
        parsed = parse_dropset_record(record)
    except (IndexError, struct.error, UnicodeDecodeError, ValueError):
        return None, "dropsetinfo new_record blob 不是完整有效记录"
    if parsed.key != intent.key:
        return None, "dropsetinfo new_record blob key 与 new_key 不一致"

    new_header, reason = _append_pabgh_entry(context, intent.key)
    if new_header is None:
        return None, reason
    change = {
        "offset": len(context.body),
        "original": "",
        "patched": record.hex(),
        "label": f"{parsed.name or intent.key}.new_record",
        "_pabgh_companion": {
            "offset": 0,
            "original": context.header.hex(),
            "patched": new_header.hex(),
            "label": "dropsetinfo new_record companion pabgh",
        },
    }
    return change, None


def _append_pabgh_entry(
    context: Format3RuntimeContext,
    new_key: int,
) -> tuple[bytes | None, str | None]:
    """在 dropsetinfo PABGH 末尾追加新 key 与当前 body 尾偏移。"""
    if context.key_size != 4 or len(context.header) < _DROPSET_PABGH_COUNT_SIZE:
        return None, "dropsetinfo new_record companion PABGH 结构不支持"
    count = struct.unpack_from("<H", context.header, 0)[0]
    expected_size = _DROPSET_PABGH_COUNT_SIZE + count * _DROPSET_PABGH_ENTRY_SIZE
    if expected_size != len(context.header):
        return None, "dropsetinfo new_record companion PABGH 长度不匹配"
    if count >= 0xFFFF:
        return None, "dropsetinfo new_record companion PABGH 记录数已满"

    output = bytearray(context.header)
    struct.pack_into("<H", output, 0, count + 1)
    output += struct.pack("<II", new_key, len(context.body))
    return bytes(output), None


def _resolve_entry(
    entry_bounds: dict[int, tuple[int, int, str, int]],
    intent: Format3Intent,
) -> tuple[int, tuple[int, int, str, int]] | None:
    """优先按 key，回退到 entry 名称解析真实 key。"""
    bounds = entry_bounds.get(intent.key)
    if bounds is not None:
        return intent.key, bounds
    if not intent.entry:
        return None
    matches = [
        (key, bounds)
        for key, bounds in entry_bounds.items()
        if bounds[2] == intent.entry
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def _skip_intent(intent: Format3Intent, reason: str) -> Format3SkippedIntent:
    """构造单条 skipped 结果。"""
    return Format3SkippedIntent(intent=intent, reason=reason)
