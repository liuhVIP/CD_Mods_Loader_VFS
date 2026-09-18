"""MultiChangeInfo Format 3 writer。

2.02.00 起本 writer 有了完整的表解析/序列化实现
（`services/multichangeinfo_native_parser.py`，整表恒等回环通过），因此
`clone_record` 与记录字段 `set` 都走“内存合成最终记录 → 输出整表或末尾追加”：

- 只追加克隆记录时输出 `offset=len(body)` 的追加 change，避免传整表 hex；
- 已有记录内容需要就地变化时输出整表 change，并带 `_pabgh_companion`。

模组侧字段路径支持记录级字段名，以及 `fixed_material_data_list[N].<field>`
这类定宽元素下标路径；未实现的路径一律明确跳过，不做半写入。
"""

from __future__ import annotations

import copy

from cdmm.services.format3_clone_record import append_pabgh_entries, build_append_body_change
from cdmm.services.format3_parser import (
    FORMAT3_CLONE_RECORD_FIELD,
    FORMAT3_NEW_RECORD_FIELD,
    Format3Intent,
)
from cdmm.services.format3_runtime import (
    Format3DispatchResult,
    Format3RuntimeContext,
    Format3SkippedIntent,
)
from cdmm.services.multichangeinfo_native_parser import (
    MULTICHANGEINFO_PATCHABLE_FIELDS,
    MultichangeParseError,
    parse_record,
    serialize_record,
    serialize_table,
)
from cdmm.services.pabgh_rewrite import rewrite_pabgh_offsets

TABLE_NAME = "multichangeinfo"

MULTICHANGEINFO_SUPPORTED_FIELD_REASON = (
    "multichangeinfo 当前支持 clone_record 复制记录、记录字段 set，以及 "
    "fixed_material_data_list[N].<field> / recipe_item_group_info_list[N].<field> 下标写入"
)

# 支持按元素下标写入的定宽数组字段。
_INDEXED_LIST_FIELDS = {
    "fixed_material_data_list": (
        "item_info",
        "gimmick_info",
        "character_info",
        "count",
        "coupon_count",
        "enchant_level",
    ),
    "recipe_item_group_info_list": ("item_group_info", "count", "enchant_level"),
}

# 支持 list_append / list_merge / list_union / array_append 的整型数组字段。
_APPEND_LIST_FIELDS = frozenset(
    {
        "result_drop_info_list",
        "additional_drop_info_list",
        "fixed_material_data_list",
        "recipe_item_group_info_list",
        "elemental_material_state_list",
    }
)

_LIST_OPS = frozenset({"list_append", "list_merge", "list_union", "array_append"})


def build_multichangeinfo_result(
    context: Format3RuntimeContext,
    intents: list[Format3Intent],
) -> Format3DispatchResult:
    """把 multichangeinfo intents 转成传统 byte patch changes。"""
    records, by_key, opaque_keys = _parse_records(context)
    if not records:
        return Format3DispatchResult(
            changes=(),
            skipped=tuple(
                _skip_intent(intent, "multichangeinfo 缺少可用记录边界") for intent in intents
            ),
        )

    skipped: list[Format3SkippedIntent] = []
    appended_keys: list[int] = []
    changed = False

    for intent in intents:
        if intent.field == FORMAT3_CLONE_RECORD_FIELD:
            applied, reason = _apply_clone_record(intent, records, by_key, opaque_keys)
            if applied:
                appended_keys.append(intent.key)
        elif intent.field == FORMAT3_NEW_RECORD_FIELD:
            applied, reason = _apply_new_record(intent, records, by_key)
            if applied:
                appended_keys.append(intent.key)
        else:
            applied, reason = _apply_set_intent(intent, by_key, opaque_keys)
        if reason is not None:
            skipped.append(_skip_intent(intent, reason))
            continue
        if applied:
            changed = True

    if not changed:
        return Format3DispatchResult(changes=(), skipped=tuple(skipped))

    try:
        new_body, new_offsets = serialize_table(records)
    except MultichangeParseError as exc:
        return Format3DispatchResult(
            changes=(),
            skipped=tuple(
                _skip_intent(intent, f"multichangeinfo 序列化失败：{exc}") for intent in intents
            ),
        )

    new_header = None
    if context.header:
        rewritten = rewrite_pabgh_offsets(context.header, TABLE_NAME, new_offsets)
        if rewritten is None:
            return Format3DispatchResult(
                changes=(),
                skipped=tuple(
                    _skip_intent(intent, "multichangeinfo companion pabgh 偏移重写失败")
                    for intent in intents
                ),
            )
        entries = [(key, new_offsets[key]) for key in appended_keys if key in new_offsets]
        appended_header = append_pabgh_entries(rewritten, TABLE_NAME, entries)
        if appended_header is None:
            return Format3DispatchResult(
                changes=(),
                skipped=tuple(
                    _skip_intent(intent, "multichangeinfo companion pabgh 追加失败")
                    for intent in intents
                ),
            )
        # 只有 offset 真正变化时才携带 companion，避免 in-place 写入白白传输整表 pabgh。
        new_header = appended_header if appended_header != context.header else None

    label = f"multichangeinfo ({len(intents)} intents)"
    if new_body.startswith(context.body):
        change = build_append_body_change(
            context.body,
            new_body[len(context.body):],
            label=label,
            header=context.header if new_header else None,
            new_header=new_header,
        )
    else:
        change = {
            "offset": 0,
            "original": context.body.hex(),
            "patched": new_body.hex(),
            "label": label,
        }
        if new_header is not None:
            change["_pabgh_companion"] = {
                "offset": 0,
                "original": context.header.hex(),
                "patched": new_header.hex(),
                "label": f"{label} companion pabgh",
            }
    return Format3DispatchResult(changes=(change,), skipped=tuple(skipped))


def _parse_records(
    context: Format3RuntimeContext,
) -> tuple[list[dict], dict[int, dict], set[int]]:
    """解析整表；无法解析的记录保留原字节并标记为 opaque。"""
    bounds = {
        key: value[0] for key, value in context.entry_bounds.items() if value
    }
    records: list[dict] = []
    by_key: dict[int, dict] = {}
    opaque: set[int] = set()

    if bounds:
        ordered = sorted(bounds.items(), key=lambda item: item[1])
        for index, (key, offset) in enumerate(ordered):
            end = ordered[index + 1][1] if index + 1 < len(ordered) else len(context.body)
            try:
                record = parse_record(context.body, offset, end)
            except MultichangeParseError:
                record = {"key": key, "_opaque": True, "bytes": bytes(context.body[offset:end])}
                opaque.add(key)
            records.append(record)
            by_key[key] = record
        return records, by_key, opaque

    cursor = 0
    while cursor < len(context.body):
        try:
            record = parse_record(context.body, cursor)
        except MultichangeParseError:
            break
        records.append(record)
        by_key[record["key"]] = record
        cursor = record["_entry_end"]
    return records, by_key, opaque


def _apply_set_intent(
    intent: Format3Intent,
    by_key: dict[int, dict],
    opaque_keys: set[int],
) -> tuple[bool, str | None]:
    """把 `set` 写进目标记录。"""
    if intent.op not in ("set",) and intent.op not in _LIST_OPS:
        return False, "multichangeinfo 当前仅支持 op=set / list_append / list_merge / list_union"
    record = _resolve_record(intent, by_key, opaque_keys)
    if record is None:
        return False, "目标 entry key/名称 都未命中"
    if record["key"] in opaque_keys:
        return False, "目标记录无法按当前 schema 解析，已跳过"
    if intent.op in _LIST_OPS:
        return _apply_list_op(record, intent)
    reason = patch_record(record, intent.field, intent.new)
    if reason is not None:
        return False, reason
    return True, None


def _apply_list_op(record: dict, intent: Format3Intent) -> tuple[bool, str | None]:
    """把新条目并入目标整型数组字段。"""
    field = intent.field
    if field not in _APPEND_LIST_FIELDS:
        return False, f"multichangeinfo 不支持对 {field} 执行 {intent.op}"
    existing = record.get(field)
    if not isinstance(existing, list):
        return False, f"multichangeinfo {field} 不是数组"
    new_items = intent.new
    if intent.op == "array_append" and not isinstance(new_items, list):
        new_items = [new_items]
    if not isinstance(new_items, list):
        return False, f"multichangeinfo {field} 新值必须是数组"
    if intent.op in ("list_append", "array_append"):
        merged = list(existing) + list(new_items)
    else:
        merged = _merge_items(existing, new_items, intent.merge_key)
    if merged == existing:
        return False, f"multichangeinfo {field} 已包含全部新条目"
    record[field] = merged
    return True, None


def _merge_items(existing: list, new_items: list, merge_key: str | None) -> list:
    """按 `merge_key`（或值本身）去重合并，保留原有顺序。"""
    merged = list(existing)
    if merge_key and existing and isinstance(existing[0], dict):
        seen = {item.get(merge_key) for item in existing if isinstance(item, dict)}
        for item in new_items:
            key = item.get(merge_key) if isinstance(item, dict) else item
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
        return merged
    seen = set()
    for item in existing:
        try:
            seen.add(item if not isinstance(item, dict) else tuple(sorted(item.items())))
        except TypeError:  # pragma: no cover - 防御性分支
            return list(existing) + [item for item in new_items if item not in existing]
    for item in new_items:
        marker = item if not isinstance(item, dict) else tuple(sorted(item.items()))
        if marker in seen:
            continue
        seen.add(marker)
        merged.append(item)
    return merged


def patch_record(record: dict, path: str, value: object) -> str | None:
    """写入单条记录字段；返回非 None 表示失败原因。"""
    if "[" in path:
        return _patch_indexed(record, path, value)
    if path not in MULTICHANGEINFO_PATCHABLE_FIELDS:
        return f"multichangeinfo schema 中不存在字段 {path}"
    record[path] = value
    return None


def _patch_indexed(record: dict, path: str, value: object) -> str | None:
    """写入 `list[N].field` 形式的定宽元素字段。"""
    name, _, rest = path.partition("[")
    if name not in _INDEXED_LIST_FIELDS:
        return f"multichangeinfo 不支持下标字段 {name}"
    index_text, sep, field = rest.partition("].")
    if not sep or not index_text.isdigit() or not field:
        return f"multichangeinfo 无法解析字段路径 {path}"
    index = int(index_text)
    items = record.get(name)
    if not isinstance(items, list):
        return f"multichangeinfo {name} 不是数组"
    if index > len(items):
        return f"multichangeinfo {name}[{index}] 越过数组末尾（长度 {len(items)}）"
    if index == len(items):
        items.append({key: 0 for key in _INDEXED_LIST_FIELDS[name]})
    element = items[index]
    if field not in _INDEXED_LIST_FIELDS[name]:
        return f"multichangeinfo {name} 元素没有字段 {field}"
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return f"multichangeinfo {name}[{index}].{field} 必须是非负整数"
    element[field] = value
    return None


def _apply_clone_record(
    intent: Format3Intent,
    records: list[dict],
    by_key: dict[int, dict],
    opaque_keys: set[int],
) -> tuple[bool, str | None]:
    """复制源记录为新 key 并追加到表尾。"""
    payload = intent.new
    if not isinstance(payload, dict):
        return False, "clone_record 缺少 source_key/patches 结构"
    source_key = payload.get("source_key")
    patches = payload.get("patches")
    if isinstance(source_key, bool) or not isinstance(source_key, int):
        return False, "clone_record source_key 不是整数"
    if not isinstance(patches, list) or not patches:
        return False, "clone_record patches 为空"
    if intent.key in by_key:
        return False, f"clone_record 新 key={intent.key} 已存在"
    if source_key in opaque_keys:
        return False, f"clone_record 源 key={source_key} 无法按当前 schema 解析"
    source = by_key.get(source_key)
    if source is None:
        return False, f"clone_record 源 key={source_key} 不在当前表中"

    clone = copy.deepcopy(source)
    clone.pop("_entry_start", None)
    clone.pop("_entry_end", None)
    clone["key"] = intent.key
    for patch in patches:
        if not isinstance(patch, dict):
            return False, "clone_record patch 不是对象"
        path = patch.get("path")
        if not isinstance(path, str) or not path:
            return False, "clone_record patch 缺少 path"
        reason = patch_record(clone, path, patch.get("new"))
        if reason is not None:
            return False, f"clone_record {path} 写入失败：{reason}"

    try:
        serialize_record(clone)
    except MultichangeParseError as exc:
        return False, f"clone_record 序列化校验失败：{exc}"

    records.append(clone)
    by_key[intent.key] = clone
    return True, None


def _apply_new_record(
    intent: Format3Intent,
    records: list[dict],
    by_key: dict[int, dict],
) -> tuple[bool, str | None]:
    """按 DMM v3.1 的完整字段模板新建一条记录并追加到表尾。"""
    template = intent.new
    if not isinstance(template, dict):
        return False, "new_record template 必须是对象"
    if intent.key in by_key:
        return False, f"new_record key={intent.key} 已存在"
    record = dict(template)
    record["key"] = intent.key
    try:
        serialize_record(record)
    except MultichangeParseError as exc:
        return False, f"new_record 序列化校验失败：{exc}"
    records.append(record)
    by_key[intent.key] = record
    return True, None


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
