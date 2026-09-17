"""ItemGroupInfo Format 3 writer。

DMM 用 `list_merge` / `list_union` / `list_append` 往物品组的成员列表里追加
条目；本表记录长度会变化，所以统一走“整表解析 → 合成 → 序列化 →
`_pabgh_companion` 偏移重写”，保证 `.pabgh` 与新的记录边界一致。
"""

from __future__ import annotations

from typing import Any

from cdmm.services.format3_parser import Format3Intent
from cdmm.services.format3_runtime import (
    Format3DispatchResult,
    Format3RuntimeContext,
    Format3SkippedIntent,
)
from cdmm.services.itemgroupinfo_native_parser import (
    ITEMGROUPINFO_PATCHABLE_FIELDS,
    ItemGroupParseError,
    parse_record,
    serialize_table,
)
from cdmm.services.pabgh_rewrite import rewrite_pabgh_offsets

TABLE_NAME = "itemgroupinfo"

ITEMGROUPINFO_SUPPORTED_FIELD_REASON = (
    "itemgroupinfo 当前支持 item_group_info_list / item_info_list / "
    "category_type_list 的 list_merge、list_union、list_append，以及记录字段 set"
)

_LIST_OPS = frozenset({"list_merge", "list_union", "list_append", "array_append"})


def build_itemgroupinfo_result(
    context: Format3RuntimeContext,
    intents: list[Format3Intent],
) -> Format3DispatchResult:
    """把 itemgroupinfo intents 转成传统 byte patch changes。"""
    key_to_offset = {key: bounds[0] for key, bounds in context.entry_bounds.items()}
    if not key_to_offset:
        return Format3DispatchResult(
            changes=(),
            skipped=tuple(
                _skip_intent(intent, "itemgroupinfo 缺少可用 PABGH 边界") for intent in intents
            ),
        )

    records, by_key, opaque_keys = _parse_records(context.body, key_to_offset)
    skipped: list[Format3SkippedIntent] = []
    changed = False

    for intent in intents:
        applied, reason = _apply_intent(intent, by_key, opaque_keys)
        if reason is not None:
            skipped.append(_skip_intent(intent, reason))
            continue
        if applied:
            changed = True

    if not changed:
        return Format3DispatchResult(changes=(), skipped=tuple(skipped))

    try:
        new_body, new_offsets = serialize_table(records, context.key_size)
    except ItemGroupParseError as exc:
        return Format3DispatchResult(
            changes=(),
            skipped=tuple(
                _skip_intent(intent, f"itemgroupinfo 序列化失败：{exc}") for intent in intents
            ),
        )

    label = f"itemgroupinfo ({len(intents)} intents)"
    change: dict[str, Any] = {
        "offset": 0,
        "original": context.body.hex(),
        "patched": new_body.hex(),
        "label": label,
    }
    if context.header:
        new_header = rewrite_pabgh_offsets(context.header, TABLE_NAME, new_offsets)
        if new_header is None:
            return Format3DispatchResult(
                changes=(),
                skipped=tuple(
                    _skip_intent(intent, "itemgroupinfo companion pabgh 偏移重写失败")
                    for intent in intents
                ),
            )
        change["_pabgh_companion"] = {
            "offset": 0,
            "original": context.header.hex(),
            "patched": new_header.hex(),
            "label": f"{label} companion pabgh",
        }
    return Format3DispatchResult(changes=(change,), skipped=tuple(skipped))


def _parse_records(
    body: bytes,
    key_to_offset: dict[int, int],
) -> tuple[list[dict], dict[int, dict], set[int]]:
    """按偏移顺序解析整表；解析失败的记录保留原字节。"""
    ordered = sorted(key_to_offset.items(), key=lambda item: item[1])
    records: list[dict] = []
    by_key: dict[int, dict] = {}
    opaque: set[int] = set()
    for index, (key, offset) in enumerate(ordered):
        end = ordered[index + 1][1] if index + 1 < len(ordered) else len(body)
        try:
            record = parse_record(body, offset, end, key_size=_infer_key_size(key))
        except ItemGroupParseError:
            record = {"key": key, "_opaque": True, "bytes": bytes(body[offset:end])}
            opaque.add(key)
        records.append(record)
        by_key[key] = record
    return records, by_key, opaque


def _infer_key_size(key: int) -> int:
    """itemgroupinfo 的 key 是 u16；越界 key 说明 header 不可信。"""
    return 2 if key <= 0xFFFF else 4


def _apply_intent(
    intent: Format3Intent,
    by_key: dict[int, dict],
    opaque_keys: set[int],
) -> tuple[bool, str | None]:
    """写入单条 intent。"""
    record = _resolve_record(intent, by_key, opaque_keys)
    if record is None:
        return False, "目标 entry key/名称 都未命中"
    if record["key"] in opaque_keys:
        return False, "目标记录无法按当前 schema 解析，已跳过"

    if intent.op == "set":
        return _apply_set(record, intent)
    if intent.op in _LIST_OPS:
        return _apply_list_op(record, intent)
    return False, "itemgroupinfo 当前仅支持 set / list_merge / list_union / list_append / array_append"


def _apply_set(record: dict, intent: Format3Intent) -> tuple[bool, str | None]:
    """整字段替换。"""
    if "." in intent.field or "[" in intent.field:
        return False, "itemgroupinfo 暂不支持嵌套 patch 路径"
    if intent.field not in ITEMGROUPINFO_PATCHABLE_FIELDS:
        return False, f"itemgroupinfo schema 中不存在字段 {intent.field}"
    record[intent.field] = intent.new
    return True, None


def _apply_list_op(record: dict, intent: Format3Intent) -> tuple[bool, str | None]:
    """把新条目并入已有的定宽整型数组字段。"""
    if "." in intent.field or "[" in intent.field:
        return False, "itemgroupinfo 暂不支持嵌套 patch 路径"
    if intent.field not in ITEMGROUPINFO_PATCHABLE_FIELDS:
        return False, f"itemgroupinfo schema 中不存在字段 {intent.field}"
    existing = record.get(intent.field)
    if not isinstance(existing, list):
        return False, "itemgroupinfo 目标字段不是数组"
    new_items = intent.new
    if intent.op == "array_append" and not isinstance(new_items, list):
        new_items = [new_items]
    if not isinstance(new_items, list):
        return False, "itemgroupinfo 新值必须是数组"
    if intent.op in ("list_append", "array_append"):
        merged = list(existing) + list(new_items)
    else:
        merged = _merge_items(existing, new_items, intent.merge_key)
    if merged == existing:
        return False, "目标数组已包含全部新条目"
    record[intent.field] = merged
    return True, None


def _merge_items(existing: list, new_items: list, merge_key: str | None) -> list:
    """按 `merge_key`（或值本身）去重合并，保留原有顺序。"""
    merged = list(existing)
    if merge_key and existing and isinstance(existing[0], dict):
        seen = {item.get(merge_key) for item in existing if isinstance(item, dict)}
        for item in new_items:
            if isinstance(item, dict) and item.get(merge_key) in seen:
                continue
            key = item.get(merge_key) if isinstance(item, dict) else item
            seen.add(key)
            merged.append(item)
        return merged
    seen = set(existing)
    for item in new_items:
        if item in seen:
            continue
        seen.add(item)
        merged.append(item)
    return merged


def _resolve_record(
    intent: Format3Intent,
    by_key: dict[int, dict],
    opaque_keys: set[int],
) -> dict | None:
    """优先按 entry 名称解析真实 key，缺失或歧义时回退到 key。"""
    if intent.entry:
        matches = [
            record
            for key, record in by_key.items()
            if key not in opaque_keys and record.get("string_key") == intent.entry
        ]
        if len(matches) == 1:
            return matches[0]
    return by_key.get(intent.key)


def _skip_intent(intent: Format3Intent, reason: str) -> Format3SkippedIntent:
    """构造单条 skipped 结果。"""
    return Format3SkippedIntent(intent=intent, reason=reason)
