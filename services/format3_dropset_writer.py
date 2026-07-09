"""DropSetInfo Format 3 writer 适配层。

参考仓库的 dropset writer 会把 `field=drops` 的 JSON 列表转换成一条
`entry + rel_offset=0` 的 record-body replace change。本模块负责在独立版
runtime 中解析目标记录，并把 key 缺省的 intent 回退到 entry 名称解析出的
真实 key。
"""

from __future__ import annotations

from cdmm.services.dropset_writer import build_drops_replacement_change
from cdmm.services.format3_parser import Format3Intent
from cdmm.services.format3_runtime import (
    Format3DispatchResult,
    Format3RuntimeContext,
    Format3SkippedIntent,
)

DROPSETINFO_SUPPORTED_FIELD_REASON = "dropsetinfo 当前仅支持 drops 字段"


def build_dropsetinfo_result(
    context: Format3RuntimeContext,
    intents: list[Format3Intent],
) -> Format3DispatchResult:
    """把 dropsetinfo intents 转成传统 byte patch changes。"""
    changes: list[dict] = []
    skipped: list[Format3SkippedIntent] = []

    for intent in intents:
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
