"""ItemInfo `clone_record` writer。

DMM Field JSON v3.1 的 Custom Item Creator 导出会把“复制一件原版装备成新
物品”写成 `op=clone_record` + `source_key` + `new_key` + `patches[]`。
本模块复用 `iteminfo_native_parser` 的整表 parse/serialize 能力：

1. 解析当前 base 的整张 iteminfo 表；
2. 按 `source_key` 深拷贝源记录，写入 `new_key`，再逐条应用 `patches[]`；
3. 把新记录追加到表尾后整体重新序列化；
4. 只把“追加出来的那段字节”和新增 `(key, offset)` 的 companion `.pabgh`
   一起交给主链路由，避免把整表 hex 反复传递。

vanilla `.pabgh` 条目顺序即 `.pabgb` 物理布局顺序，所以追加在末尾即可。
写入前先做“原表前缀必须逐字节不变”的预检：预检失败就整批跳过，绝不写入
半张表。
"""

from __future__ import annotations

import logging

from cdmm.services.format3_clone_record import (
    append_pabgh_entries,
    build_append_body_change,
    clone_records,
)
from cdmm.services.format3_iteminfo_whole_writer import (
    _ITEM_FIELD_NAMES,
    _LIST_ELEMENT_KINDS,
    _coerce_iteminfo_value,
    _elements_match_kind,
    _resolve_field_name,
    _resolve_path_target,
    apply_iteminfo_intent_to_item,
    shape_matches,
)
from cdmm.services.format3_parser import Format3Intent
from cdmm.services.format3_runtime import (
    Format3DispatchResult,
    Format3RuntimeContext,
    Format3SkippedIntent,
)
from cdmm.services.iteminfo_native_parser import (
    parse_iteminfo_from_bytes,
    serialize_iteminfo,
)
from cdmm.services.pabgh_rewrite import rewrite_pabgh_offsets

logger = logging.getLogger(__name__)

TABLE_NAME = "iteminfo"


def is_iteminfo_clone_intent(intent: Format3Intent) -> bool:
    """判断 intent 是否为 iteminfo 的 clone_record。"""
    return intent.op == "clone_record"


def build_iteminfo_clone_record_result(
    context: Format3RuntimeContext,
    intents: list[Format3Intent],
) -> Format3DispatchResult:
    """处理 iteminfo `clone_record`，以及同批次的普通 set intent。"""
    record_offsets = [
        bounds[0]
        for bounds in sorted(context.entry_bounds.values(), key=lambda item: item[0])
    ]
    try:
        items = parse_iteminfo_from_bytes(context.body, record_offsets=record_offsets)
    except Exception as exc:
        return _skip_all(intents, f"iteminfo clone_record 解析失败：{exc}")

    by_key: dict[int, dict] = {}
    by_name: dict[str, dict] = {}
    ambiguous_names: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        if isinstance(key, int) and not isinstance(key, bool):
            by_key[key] = item
        string_key = item.get("string_key")
        if isinstance(string_key, str) and string_key:
            if string_key in by_name:
                ambiguous_names.add(string_key)
            else:
                by_name[string_key] = item

    skipped: list[Format3SkippedIntent] = []
    set_intents: list[Format3Intent] = []
    clone_intents: list[Format3Intent] = []
    set_applied = 0
    for intent in intents:
        if is_iteminfo_clone_intent(intent):
            clone_intents.append(intent)
        else:
            set_intents.append(intent)

    # set intent 先叠加到现记录上：clone 的源记录可能正是它改过的版本，
    # 与 DMM“在同一份记录集合上顺序应用”的语义保持一致。
    for intent in set_intents:
        item = _resolve_item(intent, by_key, by_name, ambiguous_names)
        if item is None:
            skipped.append(_skip(intent, "目标 entry key/名称 都未命中"))
            continue
        reason = apply_iteminfo_intent_to_item(item, intent)
        if reason is not None:
            skipped.append(_skip(intent, reason))
            continue
        set_applied += 1

    outcome = clone_records(items, clone_intents, patch_item=_apply_clone_patch)
    skipped.extend(outcome.skipped)

    if outcome.applied == 0 and set_applied == 0:
        return Format3DispatchResult(changes=(), skipped=tuple(skipped))

    new_offsets: dict[int, int] = {}
    try:
        new_body = serialize_iteminfo(outcome.items, offsets_out=new_offsets)
    except Exception as exc:
        return _skip_all(intents, f"iteminfo clone_record 序列化失败：{exc}")

    if new_body == context.body:
        return Format3DispatchResult(changes=(), skipped=tuple(skipped))
    if not new_body.startswith(context.body):
        return _skip_all(intents, "iteminfo clone_record 预检失败：原表前缀被改写")
    if outcome.applied and len(new_body) == len(context.body):
        return _skip_all(intents, "iteminfo clone_record 预检失败：追加记录后长度未增长")

    new_header: bytes | None = None
    if context.header and outcome.appended_keys:
        rewritten = rewrite_pabgh_offsets(context.header, TABLE_NAME, new_offsets)
        if rewritten is None or not rewritten.startswith(context.header):
            return _skip_all(intents, "iteminfo clone_record companion pabgh 偏移重写失败")
        entries = [
            (key, new_offsets[key])
            for key in outcome.appended_keys
            if key in new_offsets
        ]
        new_header = append_pabgh_entries(rewritten, TABLE_NAME, entries)
        if new_header is None:
            return _skip_all(intents, "iteminfo clone_record companion pabgh 追加失败")

    appended = new_body[len(context.body):]
    if not appended:
        change = {
            "offset": 0,
            "original": context.body.hex(),
            "patched": new_body.hex(),
            "label": f"iteminfo clone_record ({outcome.applied} clones)",
        }
    else:
        change = build_append_body_change(
            context.body,
            appended,
            label=f"iteminfo clone_record ({outcome.applied} clones)",
            header=context.header,
            new_header=new_header,
        )
    return Format3DispatchResult(changes=(change,), skipped=tuple(skipped))


def _apply_clone_patch(item: dict, path: str, value: object) -> str | None:
    """把 `patches[]` 的单条 path/new 写进克隆体。"""
    if not isinstance(path, str) or not path:
        return "path 为空"
    if "." in path or "[" in path:
        target = _resolve_path_target(item, path)
        if target is None:
            return "nested path 未命中"
        parent, last_segment = target
        try:
            existing = parent[last_segment]
        except (KeyError, IndexError, TypeError):
            existing = None
        if not shape_matches(existing, value):
            return "nested path 新值结构不匹配"
        try:
            parent[last_segment] = value
        except (KeyError, IndexError, TypeError):
            return "nested path 写入失败"
        return None

    field = _resolve_field_name(path, item)
    if field is None or field not in _ITEM_FIELD_NAMES:
        return "schema 中不存在该字段"
    existing = item.get(field)
    new_value, coerce_reason = _coerce_iteminfo_value(field, existing, value)
    if coerce_reason is not None:
        return coerce_reason
    shape_ok = shape_matches(existing, new_value)
    if shape_ok and (existing is None or (isinstance(existing, list) and not existing)):
        kind = _LIST_ELEMENT_KINDS.get(field)
        if kind is not None:
            shape_ok = isinstance(new_value, list) and _elements_match_kind(new_value, kind)
    if not shape_ok:
        return "新值结构不匹配"
    item[field] = new_value
    return None


def _resolve_item(
    intent: Format3Intent,
    by_key: dict[int, dict],
    by_name: dict[str, dict],
    ambiguous_names: set[str],
) -> dict | None:
    """优先按唯一 entry 名称定位记录，缺失/歧义时回退到 key。"""
    if intent.entry and intent.entry not in ambiguous_names:
        item = by_name.get(intent.entry)
        if item is not None:
            return item
    return by_key.get(intent.key)


def _skip(intent: Format3Intent, reason: str) -> Format3SkippedIntent:
    """构造单条 skipped 结果。"""
    return Format3SkippedIntent(intent=intent, reason=reason)


def _skip_all(intents: list[Format3Intent], reason: str) -> Format3DispatchResult:
    """整批跳过。"""
    return Format3DispatchResult(
        changes=(),
        skipped=tuple(_skip(intent, reason) for intent in intents),
    )
