"""EquipSlotInfo Format 3 writer 适配层。

`equipslotinfo_writer.py` 负责安全解析和改写 `entries[N].etl_hashes`；
本模块只把独立版 runtime/intents 接到该 writer，并将 companion `.pabgh`
change 路由给 `json_loader` 的 `_target_file` 机制。
"""

from __future__ import annotations

from cdmm.services.equipslotinfo_writer import (
    EquipslotWriteRefused,
    build_equipslotinfo_changes,
)
from cdmm.services.format3_parser import Format3Intent
from cdmm.services.format3_runtime import (
    Format3DispatchResult,
    Format3RuntimeContext,
    Format3SkippedIntent,
)

EQUIPSLOTINFO_SUPPORTED_FIELD_REASON = (
    "equipslotinfo 当前仅支持 entries[N].etl_hashes 字段"
)


def build_equipslotinfo_result(
    context: Format3RuntimeContext,
    intents: list[Format3Intent],
) -> Format3DispatchResult:
    """把 equipslotinfo intents 转成传统 byte patch changes。"""
    try:
        pabgb_changes, pabgh_change = build_equipslotinfo_changes(
            context.body,
            context.header,
            intents,
        )
    except EquipslotWriteRefused as exc:
        return Format3DispatchResult(
            changes=(),
            skipped=tuple(
                Format3SkippedIntent(intent=intent, reason=f"equipslotinfo writer 拒绝写入：{exc}")
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
                reason="equipslotinfo writer 未生成补丁，可能是目标记录/字段未命中或值类型不合法",
            )
            for intent in intents
        ),
    )
