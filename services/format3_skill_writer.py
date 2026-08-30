"""Skill Format 3 whole-table writer。

当前独立版先迁入参考仓库已验证的 `_useResourceStatList` 与 `_buffLevelList`
两类 list 字段能力。实现上复用本地 vendored `skill_native_parser.py` 做
parse -> mutate -> serialize，再通过 `_pabgh_companion` 协议把 companion
`.pabgh` 一并交给主链路。
"""

from __future__ import annotations

import logging

from cdmm.services.format3_parser import Format3Intent
from cdmm.services.format3_runtime import (
    Format3DispatchResult,
    Format3RuntimeContext,
    Format3SkippedIntent,
)
from cdmm.services.pab_table_service import parse_pabgh_index
from cdmm.services.pabgh_rewrite import rewrite_pabgh_offsets
from cdmm.services.skill_native_parser import parse_all, serialize_all

logger = logging.getLogger(__name__)

SKILL_SUPPORTED_FIELDS = frozenset(
    {
        "_useResourceStatList",
        "_buffLevelList",
    }
)

SKILL_SUPPORTED_FIELD_REASON = (
    "skill 当前仅支持 _useResourceStatList、_buffLevelList"
)


def build_skill_whole_table_result(
    context: Format3RuntimeContext,
    intents: list[Format3Intent],
) -> Format3DispatchResult:
    """用 whole-table 方式处理 skill intents。"""
    try:
        entries = parse_all(context.header, context.body)
    except Exception as exc:
        return _skip_all(intents, f"skill 解析失败：{exc}")

    try:
        identity_header, identity_body = serialize_all(entries)
    except Exception as exc:
        return _skip_all(intents, f"skill identity serialize 失败：{exc}")
    if identity_body != context.body:
        return _skip_all(intents, "skill whole-table 预检失败：identity roundtrip 不一致")

    _, identity_offsets = parse_pabgh_index(identity_header, "skill")
    if not identity_offsets:
        return _skip_all(intents, "skill whole-table 预检失败：identity offsets 无效")
    identity_rewritten = rewrite_pabgh_offsets(context.header, "skill", identity_offsets)
    if identity_rewritten != context.header:
        return _skip_all(intents, "skill whole-table 预检失败：pabgh identity rewrite 不一致")

    by_key = {
        entry["key"]: entry
        for entry in entries
        if isinstance(entry, dict) and "key" in entry
    }
    by_name: dict[str, dict] = {}
    ambiguous_names: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if isinstance(name, str) and name:
            if name in by_name:
                ambiguous_names.add(name)
            else:
                by_name[name] = entry

    skipped: list[Format3SkippedIntent] = []
    applied = 0
    for intent in intents:
        if intent.field not in SKILL_SUPPORTED_FIELDS:
            skipped.append(_skip_intent(intent, SKILL_SUPPORTED_FIELD_REASON))
            continue
        if intent.op != "set":
            skipped.append(_skip_intent(intent, "skill whole-table 当前仅支持 op=set"))
            continue
        entry = _resolve_skill_entry(intent, by_key, by_name, ambiguous_names)
        if entry is None:
            skipped.append(_skip_intent(intent, "目标 entry key/名称 都未命中"))
            continue
        if not _shape_matches(intent.field, intent.new):
            skipped.append(_skip_intent(intent, "skill whole-table 新值结构不匹配"))
            continue
        try:
            entry[intent.field] = intent.new
            applied += 1
        except Exception as exc:  # pragma: no cover - 防御性分支
            skipped.append(_skip_intent(intent, f"skill whole-table 写入失败：{exc}"))

    if applied == 0:
        return Format3DispatchResult(changes=(), skipped=tuple(skipped))

    try:
        new_header_synth, new_body = serialize_all(entries)
    except Exception as exc:
        return Format3DispatchResult(
            changes=(),
            skipped=tuple(
                skipped + [_skip_intent(intent, f"skill serialize 失败：{exc}") for intent in intents]
            ),
        )

    if new_body == context.body:
        return Format3DispatchResult(changes=(), skipped=tuple(skipped))

    change = {
        "offset": 0,
        "original": context.body.hex(),
        "patched": new_body.hex(),
        "label": f"skill whole-table ({applied} applied)",
    }

    _, new_offsets = parse_pabgh_index(new_header_synth, "skill")
    if not new_offsets:
        return Format3DispatchResult(
            changes=(),
            skipped=tuple(
                skipped + [_skip_intent(intent, "skill companion pabgh offsets 无效") for intent in intents]
            ),
        )
    if new_offsets != identity_offsets:
        new_header = rewrite_pabgh_offsets(context.header, "skill", new_offsets)
        if new_header is None:
            return Format3DispatchResult(
                changes=(),
                skipped=tuple(
                    skipped + [_skip_intent(intent, "skill companion pabgh rewrite 失败") for intent in intents]
                ),
            )
        change["_pabgh_companion"] = {
            "offset": 0,
            "original": context.header.hex(),
            "patched": new_header.hex(),
            "label": "skill whole-table companion pabgh",
        }

    return Format3DispatchResult(
        changes=(change,),
        skipped=tuple(skipped),
    )


def _skip_all(intents: list[Format3Intent], reason: str) -> Format3DispatchResult:
    """把整批 skill whole-table intents 统一标记为跳过。"""
    return Format3DispatchResult(
        changes=(),
        skipped=tuple(_skip_intent(intent, reason) for intent in intents),
    )


def _skip_intent(intent: Format3Intent, reason: str) -> Format3SkippedIntent:
    """构造单条 skipped 结果。"""
    return Format3SkippedIntent(intent=intent, reason=reason)


def _resolve_skill_entry(
    intent: Format3Intent,
    by_key: dict[int, dict],
    by_name: dict[str, dict],
    ambiguous_names: set[str],
) -> dict | None:
    """优先按唯一 entry 名称，缺失或不唯一时回退到 key 查找 skill 记录。"""
    if intent.entry and intent.entry not in ambiguous_names:
        entry = by_name.get(intent.entry)
        if entry is not None:
            return entry
    return by_key.get(intent.key)


def _shape_matches(field: str, new: object) -> bool:
    """对 skill 已声明字段做轻量结构校验。"""
    if field == "_useResourceStatList":
        return isinstance(new, list) and all(isinstance(item, dict) for item in new)
    if field == "_buffLevelList":
        return (
            isinstance(new, list)
            and all(
                isinstance(level, list)
                and all(isinstance(item, dict) for item in level)
                for level in new
            )
        )
    return False
