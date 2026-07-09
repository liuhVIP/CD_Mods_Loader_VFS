"""MultiChangeInfo Format 3 writer 适配层。

参考仓库的 `multichangeinfo_writer.py` 已经是纯函数实现；本模块只负责把
独立版 runtime/intents 接到该 writer，并把 companion `.pabgh` change 路由
给 `json_loader` 的 `_target_file` 机制。
"""

from __future__ import annotations

from cdmm.services.format3_parser import Format3Intent
from cdmm.services.format3_runtime import (
    Format3DispatchResult,
    Format3RuntimeContext,
    Format3SkippedIntent,
)
from cdmm.services.multichangeinfo_writer import build_multichangeinfo_changes

MULTICHANGEINFO_SUPPORTED_FIELD_REASON = (
    "multichangeinfo 当前仅支持 fixed_material_data_list[N].item_info 和 "
    "fixed_material_data_list[N].count"
)


def build_multichangeinfo_result(
    context: Format3RuntimeContext,
    intents: list[Format3Intent],
) -> Format3DispatchResult:
    """把 multichangeinfo intents 转成传统 byte patch changes。"""
    tuple_intents = [
        (intent.entry, intent.key, intent.field, intent.new)
        for intent in intents
    ]
    pabgb_changes, pabgh_change = build_multichangeinfo_changes(
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
                reason="multichangeinfo writer 未生成补丁，可能是目标记录/字段未命中或值类型不合法",
            )
            for intent in intents
        ),
    )
