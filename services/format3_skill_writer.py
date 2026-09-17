"""Skill Format 3 writer。

`skill` 记录只有 `buff_level_list[*][*].base.carray_u16` 这类路径会被模组改写，
因此这里不做整表解析，而是：

1. 用 `services/skill_record_locator.py` 定位目标数组的字节区间；
2. 按 `list_union` / `list_append` / `array_append` / `set` 语义生成新数组；
3. 字节级拼接回记录；
4. **复验**：对拼接后的记录重新定位，确认结构层级与数组内容都与预期一致，
   否则整条 intent 跳过，绝不写坏数据。

记录长度变化会让后续记录偏移整体位移，所以 body 只从**首个被修改记录**开始
输出一个 change，并携带重写后的 `.pabgh` companion。
"""

from __future__ import annotations

import re

from cdmm.services.format3_parser import Format3Intent
from cdmm.services.format3_runtime import (
    Format3DispatchResult,
    Format3RuntimeContext,
    Format3SkippedIntent,
)
from cdmm.services.pabgh_rewrite import rewrite_pabgh_offsets
from cdmm.services.skill_record_locator import (
    SkillLocateError,
    encode_u32_array,
    locate_array,
    locate_record,
    read_u32_array,
)

TABLE_NAME = "skill"

SKILL_SUPPORTED_FIELD_REASON = (
    "skill 当前支持 buff_level_list[L][B].base.carray_u16 的 "
    "list_union / list_append / array_append / set"
)

_ARRAY_PATH = re.compile(
    r"^(?:buff_level_list|_buffLevelList)\[(\d+)]\[(\d+)]\.base\.carray_u16$"
)

_LIST_OPS = frozenset({"list_union", "list_merge", "list_append", "array_append"})


def build_skill_whole_table_result(
    context: Format3RuntimeContext,
    intents: list[Format3Intent],
) -> Format3DispatchResult:
    """把 skill intents 转成传统 byte patch changes。"""
    bounds = _record_bounds(context)
    if not bounds:
        return Format3DispatchResult(
            changes=(),
            skipped=tuple(
                _skip_intent(intent, "skill 缺少可用 PABGH 记录边界") for intent in intents
            ),
        )

    body = bytearray(context.body)
    skipped: list[Format3SkippedIntent] = []
    first_modified: int | None = None

    # 先解析出每条 intent 的目标记录，再**按偏移从后往前**拼接：这样处理靠前的
    # 记录时，它自己的 start/end 仍与原始 body 一致，不会被后面的长度变化污染。
    plan: list[tuple[int, int, int, int, Format3Intent]] = []
    for intent in intents:
        match = _ARRAY_PATH.match(intent.field)
        if match is None:
            skipped.append(_skip_intent(intent, SKILL_SUPPORTED_FIELD_REASON))
            continue
        offset = _resolve_offset(intent, bounds, context.body)
        if offset is None:
            skipped.append(_skip_intent(intent, "目标 entry key/名称 都未命中"))
            continue
        record_end = _record_end(bounds, offset)
        if record_end is None:
            skipped.append(_skip_intent(intent, "目标记录边界不可用"))
            continue
        plan.append((offset, record_end, int(match.group(1)), int(match.group(2)), intent))

    deltas: dict[int, int] = {}
    for offset, record_end, level, buff, intent in sorted(plan, key=lambda item: -item[0]):
        reason, delta = _apply_intent(body, offset, record_end, level, buff, intent)
        if reason is not None:
            skipped.append(_skip_intent(intent, reason))
            continue
        deltas[offset] = deltas.get(offset, 0) + delta
        if first_modified is None or offset < first_modified:
            first_modified = offset

    if first_modified is None:
        return Format3DispatchResult(changes=(), skipped=tuple(skipped))

    new_body = bytes(body)
    new_offsets = _offsets_after(bounds, deltas)
    label = f"skill ({len(intents)} intents)"
    change: dict = {
        "offset": first_modified,
        "original": context.body[first_modified:].hex(),
        "patched": new_body[first_modified:].hex(),
        "label": label,
    }
    if context.header:
        new_header = rewrite_pabgh_offsets(context.header, TABLE_NAME, new_offsets)
        if new_header is None:
            return Format3DispatchResult(
                changes=(),
                skipped=tuple(
                    _skip_intent(intent, "skill companion pabgh 偏移重写失败")
                    for intent in intents
                ),
            )
        if new_header != context.header:
            change["_pabgh_companion"] = {
                "offset": 0,
                "original": context.header.hex(),
                "patched": new_header.hex(),
                "label": f"{label} companion pabgh",
            }
    return Format3DispatchResult(changes=(change,), skipped=tuple(skipped))


def _record_bounds(context: Format3RuntimeContext) -> list[tuple[int, int, int, str]]:
    """返回按偏移升序的 `(key, start, end, name)` 列表。"""
    entries = sorted(
        ((key, value) for key, value in context.entry_bounds.items() if value),
        key=lambda item: item[1][0],
    )
    out: list[tuple[int, int, int, str]] = []
    for index, (key, value) in enumerate(entries):
        start = value[0]
        end = entries[index + 1][1][0] if index + 1 < len(entries) else len(context.body)
        out.append((key, start, end, value[2]))
    return out


def _resolve_offset(
    intent: Format3Intent,
    bounds: list[tuple[int, int, int, str]],
    body: bytes,
) -> int | None:
    """优先按 entry 名称定位记录，缺失或不唯一时回退到 key。"""
    if intent.entry:
        matches = [start for _key, start, _end, name in bounds if name == intent.entry]
        if len(matches) == 1:
            return matches[0]
    for key, start, _end, _name in bounds:
        if key == intent.key:
            return start
    return None


def _record_end(bounds: list[tuple[int, int, int, str]], offset: int) -> int | None:
    """按起始偏移查回记录结束位置。"""
    for _key, start, end, _name in bounds:
        if start == offset:
            return end
    return None


def merge_values(
    existing: list[int],
    new_values: list[int],
    operation: str,
) -> list[int]:
    """按 intent 语义计算数组的新内容。"""
    if operation == "set":
        return list(new_values)
    if operation in ("list_append", "array_append"):
        return list(existing) + [value for value in new_values]
    merged = list(existing)
    seen = set(existing)
    for value in new_values:
        if value in seen:
            continue
        seen.add(value)
        merged.append(value)
    return merged


def _apply_intent(
    body: bytearray,
    start: int,
    end: int,
    level: int,
    buff: int,
    intent: Format3Intent,
) -> tuple[str | None, int]:
    """定位目标数组并就地拼接新内容；返回 `(跳过原因, 长度增量)`。"""
    if intent.op not in _LIST_OPS and intent.op != "set":
        return (
            "skill 当前仅支持 list_union / list_merge / list_append / "
            "array_append / set",
            0,
        )
    if not isinstance(intent.new, list):
        return "skill 新值必须是数组", 0
    for value in intent.new:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return "skill 新值必须是非负整数", 0

    snapshot = bytes(body)
    try:
        record = locate_record(snapshot, start, end)
        array_start, array_end = locate_array(snapshot, start, end, level, buff)
    except SkillLocateError as exc:
        return f"skill 记录定位失败：{exc}", 0

    existing = read_u32_array(snapshot, array_start, array_end)
    merged = merge_values(existing, intent.new, intent.op)
    if merged == existing:
        return "目标数组已包含全部新条目", 0

    body[array_start:array_end] = encode_u32_array(merged)
    delta = (len(merged) - len(existing)) * 4

    # 复验：拼接后必须仍能定位到同一数组，且层级结构与内容完全符合预期。
    new_end = end + delta
    try:
        verified = locate_record(bytes(body), start, new_end)
        verified_start, verified_end = locate_array(bytes(body), start, new_end, level, buff)
    except SkillLocateError as exc:
        body[array_start:array_start + len(merged) * 4 + 4] = encode_u32_array(existing)
        return f"skill 拼接后复验失败：{exc}", 0
    if _shape(verified["spans"]) != _shape(record["spans"]):
        body[array_start:array_start + len(merged) * 4 + 4] = encode_u32_array(existing)
        return "skill 拼接后复验失败：buff_level_list 结构发生变化", 0
    if read_u32_array(bytes(body), verified_start, verified_end) != merged:
        body[array_start:array_start + len(merged) * 4 + 4] = encode_u32_array(existing)
        return "skill 拼接后复验失败：数组内容与预期不一致", 0
    return None, delta


def _shape(spans: list[list[tuple[int, int, int] | None]]) -> list[list[int | None]]:
    """把定位结果压成“层级 + 每个 slot 的 tag”结构指纹。"""
    return [
        [None if span is None else span[0] for span in level]
        for level in spans
    ]


def _offsets_after(
    bounds: list[tuple[int, int, int, str]],
    deltas: dict[int, int],
) -> dict[int, int]:
    """按每条被修改记录的长度增量顺推全部记录偏移。

    记录只会在原位置就地变长（不新增记录），所以起点之后的偏移整体平移。
    """
    result: dict[int, int] = {}
    shift = 0
    for key, start, _end, _name in bounds:
        result[key] = start + shift
        shift += deltas.get(start, 0)
    return result


def _skip_intent(intent: Format3Intent, reason: str) -> Format3SkippedIntent:
    """构造单条 skipped 结果。"""
    return Format3SkippedIntent(intent=intent, reason=reason)
