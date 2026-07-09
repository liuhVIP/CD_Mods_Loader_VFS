"""StringInfo Format 3 writer 适配层。

`stringinfo_writer.py` 负责实际的 buffer 替换和 companion `.pabgh` 重建；
本模块只负责接入独立版 Format 3 runtime/result 协议。
"""

from __future__ import annotations

from cdmm.services.format3_parser import Format3Intent
from cdmm.services.format3_runtime import (
    Format3DispatchResult,
    Format3RuntimeContext,
    Format3SkippedIntent,
)
from cdmm.services.stringinfo_writer import build_stringinfo_changes

STRINGINFO_SUPPORTED_FIELD_REASON = "stringinfo 当前仅支持 buffer / _buffer 字段"


def build_stringinfo_result(
    context: Format3RuntimeContext,
    intents: list[Format3Intent],
) -> Format3DispatchResult:
    """把 stringinfo intents 转成传统 byte patch changes。"""
    tuple_intents = [
        (intent.entry, intent.key, intent.field, intent.new)
        for intent in intents
    ]
    pabgb_changes, pabgh_change = build_stringinfo_changes(
        context.body,
        context.header,
        tuple_intents,
    )

    changes = list(pabgb_changes)
    if pabgh_change is not None:
        routed = dict(pabgh_change)
        routed["_target_file"] = context.game_file.rsplit(".", 1)[0] + ".pabgh"
        changes.append(routed)

    if changes:
        return Format3DispatchResult(changes=tuple(changes), skipped=())
    return Format3DispatchResult(
        changes=(),
        skipped=tuple(
            Format3SkippedIntent(
                intent=intent,
                reason="stringinfo writer 未生成补丁，可能是 key 未命中、字段不支持或新值不是字符串",
            )
            for intent in intents
        ),
    )
