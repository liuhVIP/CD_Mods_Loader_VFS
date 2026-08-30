"""StoreInfo Format 3 writer 适配层。

`storeinfo_writer.py` 负责安全改写 `stock_data_list`；本模块只负责接入
独立版 Format 3 runtime/result 协议，并把 companion `.pabgh` change 路由
给 `json_loader` 的 `_target_file` 机制。
"""

from __future__ import annotations

from cdmm.services.format3_parser import Format3Intent
from cdmm.services.format3_runtime import (
    Format3DispatchResult,
    Format3RuntimeContext,
    Format3SkippedIntent,
)
from cdmm.services.storeinfo_writer import StoreinfoWriteRefused, build_storeinfo_changes
from cdmm.services.storeinfo_native_parser import StoreinfoParseError

STOREINFO_SUPPORTED_FIELD_REASON = (
    "storeinfo 当前支持库存列表、库存项窄替换、库存计数、reset_day、"
    "sell_percents、raw_c 与贡献购买货币字段"
)


def build_storeinfo_result(
    context: Format3RuntimeContext,
    intents: list[Format3Intent],
) -> Format3DispatchResult:
    """把 storeinfo intents 转成传统 byte patch changes。"""
    try:
        pabgb_changes, pabgh_change = build_storeinfo_changes(
            context.body,
            context.header,
            intents,
        )
    except (StoreinfoWriteRefused, StoreinfoParseError) as exc:
        return Format3DispatchResult(
            changes=(),
            skipped=tuple(
                Format3SkippedIntent(intent=intent, reason=f"storeinfo writer 拒绝写入：{exc}")
                for intent in intents
            ),
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
                reason="storeinfo writer 未生成补丁，可能是目标记录/字段未命中或值类型不合法",
            )
            for intent in intents
        ),
    )
