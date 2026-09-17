"""DropSetInfo Format 3 writer 适配层。

2.02.00 起本 writer 有了完整的表解析/序列化实现
（`services/dropsetinfo_native_parser.py`，整表恒等回环验证通过），因此
`clone_record` / `set` 都走“内存合成最终记录 → 输出整表或末尾追加”的路径：

- 只追加克隆记录时输出 `offset=len(body)` 的追加 change，避免传整表 hex；
- 记录内容需要就地变化时输出整表 change，并带 `_pabgh_companion`。

旧版 `drops` / `new_record(_blob_b64)` 兼容分支保留，继续交给
`services/dropset_writer.py` 的窄实现。
"""

from __future__ import annotations

import base64
import binascii
import copy
import struct

from cdmm.services.dropset_writer import build_drops_replacement_change, parse_dropset_record
from cdmm.services.dropsetinfo_native_parser import (
    DROPSETINFO_PATCHABLE_FIELDS,
    DropsetParseError,
    parse_record,
    serialize_record,
    serialize_table,
)
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
from cdmm.services.pabgh_rewrite import rewrite_pabgh_offsets

TABLE_NAME = "dropsetinfo"

DROPSETINFO_SUPPORTED_FIELD_REASON = (
    "dropsetinfo 当前支持 drops 字段、new_record 完整记录模板、clone_record 复制记录，"
    "以及记录的标量/列表字段 set"
)

_LEGACY_FIELDS = frozenset({"drops", FORMAT3_NEW_RECORD_FIELD})


def build_dropsetinfo_result(
    context: Format3RuntimeContext,
    intents: list[Format3Intent],
) -> Format3DispatchResult:
    """把 dropsetinfo intents 转成传统 byte patch changes。"""
    modern = [intent for intent in intents if intent.field not in _LEGACY_FIELDS]
    legacy = [intent for intent in intents if intent.field in _LEGACY_FIELDS]

    changes: list[dict] = []
    skipped: list[Format3SkippedIntent] = []

    if modern:
        modern_change, modern_skipped = _build_modern_change(context, modern)
        skipped.extend(modern_skipped)
        if modern_change is not None:
            changes.append(modern_change)
    if legacy:
        legacy_changes, legacy_skipped = _build_legacy_changes(context, legacy)
        skipped.extend(legacy_skipped)
        changes.extend(legacy_changes)

    return Format3DispatchResult(changes=tuple(changes), skipped=tuple(skipped))

def _build_modern_change(
    context: Format3RuntimeContext,
    intents: list[Format3Intent],
) -> tuple[dict | None, list[Format3SkippedIntent]]:
    """按完整表解析合成 clone_record / set 结果。"""
    key_to_offset = {
        key: bounds[0] for key, bounds in context.entry_bounds.items()
    }
    if not key_to_offset:
        return None, [
            _skip_intent(intent, "dropsetinfo 缺少可用 PABGH 边界") for intent in intents
        ]

    records, by_key, opaque_keys = _parse_records(context.body, key_to_offset)
    skipped: list[Format3SkippedIntent] = []
    appended_keys: list[int] = []
    changed = False

    for intent in intents:
        if intent.field == FORMAT3_CLONE_RECORD_FIELD:
            applied, reason = _apply_clone_record(
                context,
                intent,
                records,
                by_key,
                opaque_keys,
            )
        else:
            applied, reason = _apply_set_intent(intent, by_key, opaque_keys)
        if reason is not None:
            skipped.append(_skip_intent(intent, reason))
            continue
        if applied:
            changed = True
        if applied and intent.field == FORMAT3_CLONE_RECORD_FIELD:
            appended_keys.append(intent.key)

    if not changed:
        return None, skipped

    try:
        new_body, new_offsets = serialize_table(records, context.key_size)
    except DropsetParseError as exc:
        return None, [_skip_intent(intent, f"dropsetinfo 序列化失败：{exc}") for intent in intents]

    new_header = None
    if context.header:
        rewritten = rewrite_pabgh_offsets(context.header, TABLE_NAME, new_offsets)
        if rewritten is None:
            return None, [
                _skip_intent(intent, "dropsetinfo companion pabgh 偏移重写失败")
                for intent in intents
            ]
        entries = [(key, new_offsets[key]) for key in appended_keys if key in new_offsets]
        appended_header = append_pabgh_entries(rewritten, TABLE_NAME, entries)
        if appended_header is None:
            return None, [
                _skip_intent(intent, "dropsetinfo companion pabgh 追加失败")
                for intent in intents
            ]
        new_header = appended_header

    label = f"dropsetinfo ({len(intents)} intents)"
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
    return change, skipped


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
            record = parse_record(body, offset, end)
        except DropsetParseError:
            record = {
                "key": key,
                "_opaque": True,
                "bytes": bytes(body[offset:end]),
            }
            opaque.add(key)
        records.append(record)
        by_key[key] = record
    return records, by_key, opaque


def _apply_set_intent(
    intent: Format3Intent,
    by_key: dict[int, dict],
    opaque_keys: set[int],
) -> tuple[bool, str | None]:
    """把 `set` 写进目标记录（含 `list` 整体替换）。"""
    if intent.op != "set":
        return False, "dropsetinfo 当前仅支持 op=set / clone_record"
    record = _resolve_record(intent, by_key, opaque_keys)
    if record is None:
        return False, "目标 entry key/名称 都未命中"
    if record["key"] in opaque_keys:
        return False, "目标记录无法按当前 schema 解析，已跳过"
    reason = _patch_record(record, intent.field, intent.new)
    if reason is not None:
        return False, reason
    return True, None


def _apply_clone_record(
    context: Format3RuntimeContext,
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
    if isinstance(source_key, bool) or not isinstance(source_key, int):
        return False, "clone_record source_key 不是整数"
    patches = payload.get("patches")
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
        reason = _patch_record(clone, path, patch.get("new"))
        if reason is not None:
            return False, f"clone_record {path} 写入失败：{reason}"

    try:
        serialize_record(clone, key_size=context.key_size)
    except DropsetParseError as exc:
        return False, f"clone_record 序列化校验失败：{exc}"

    records.append(clone)
    by_key[intent.key] = clone
    return True, None


def _patch_record(record: dict, path: str, value: object) -> str | None:
    """写入记录字段；路径必须命中当前 schema 字段。"""
    if "." in path or "[" in path:
        return "dropsetinfo 暂不支持嵌套 patch 路径"
    if path not in DROPSETINFO_PATCHABLE_FIELDS:
        return f"dropsetinfo schema 中不存在字段 {path}"
    record[path] = value
    return None


def _resolve_record(
    intent: Format3Intent,
    by_key: dict[int, dict],
    opaque_keys: set[int],
) -> dict | None:
    """优先按 entry 名称解析真实 key，缺失或歧义时回退到 key。"""
    if intent.entry:
        for key, record in by_key.items():
            if key in opaque_keys:
                continue
            if record.get("string_key") == intent.entry:
                return record
    return by_key.get(intent.key)

# ── 旧版兼容分支：`drops` 与 new_record(_blob_b64) ────────────────────────
_DROPSET_PABGH_COUNT_SIZE = 2
_DROPSET_PABGH_ENTRY_SIZE = 8


def _build_legacy_changes(
    context: Format3RuntimeContext,
    intents: list[Format3Intent],
) -> tuple[list[dict], list[Format3SkippedIntent]]:
    """旧版 `drops` / `new_record` 窄 writer 兼容路径。"""
    changes: list[dict] = []
    skipped: list[Format3SkippedIntent] = []
    name_to_key, ambiguous_names = _build_dropset_name_to_key(context.entry_bounds)

    for intent in intents:
        if intent.field == FORMAT3_NEW_RECORD_FIELD:
            change, reason = _build_new_record_change(context, intent)
        else:
            change, reason = _build_single_change(
                context,
                name_to_key,
                ambiguous_names,
                intent,
            )
        if change is None:
            skipped.append(_skip_intent(intent, reason or "dropsetinfo writer 未生成补丁"))
            continue
        changes.append(change)
    return changes, skipped


def _build_dropset_name_to_key(
    entry_bounds: dict[int, tuple[int, int, str, int]],
) -> tuple[dict[str, int], set[str]]:
    """构建 dropset entry 名称到 PABGH key 的索引，歧义名称单独记录。"""
    name_to_key: dict[str, int] = {}
    ambiguous_names: set[str] = set()
    for key, bounds in entry_bounds.items():
        name = bounds[2]
        if not name:
            continue
        if name in name_to_key:
            ambiguous_names.add(name)
        else:
            name_to_key[name] = key
    return name_to_key, ambiguous_names


def _build_single_change(
    context: Format3RuntimeContext,
    name_to_key: dict[str, int],
    ambiguous_names: set[str],
    intent: Format3Intent,
) -> tuple[dict | None, str | None]:
    """构造单条 dropsetinfo `drops` change。"""
    if intent.field != "drops":
        return None, DROPSETINFO_SUPPORTED_FIELD_REASON
    if intent.op != "set":
        return None, "dropsetinfo 当前仅支持 op=set"
    if not isinstance(intent.new, list) or not all(isinstance(item, dict) for item in intent.new):
        return None, "dropsetinfo drops 新值必须是对象数组"

    resolved = _resolve_entry(
        context.entry_bounds,
        name_to_key,
        ambiguous_names,
        intent,
    )
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


def _build_new_record_change(
    context: Format3RuntimeContext,
    intent: Format3Intent,
) -> tuple[dict | None, str | None]:
    """把 `_blob_b64` 完整记录追加到 PABGB，并同步扩展 PABGH。"""
    if intent.op != "new_record":
        return None, "dropsetinfo 新记录必须使用 op=new_record"
    if intent.key in context.entry_bounds:
        return None, f"dropsetinfo new_record key={intent.key} 已存在"
    if not isinstance(intent.new, dict):
        return None, "dropsetinfo new_record template 必须是对象"
    blob_text = intent.new.get("_blob_b64")
    if not isinstance(blob_text, str) or not blob_text:
        return None, "dropsetinfo new_record template 缺少 _blob_b64"
    try:
        record = base64.b64decode(blob_text, validate=True)
    except (ValueError, binascii.Error):
        return None, "dropsetinfo new_record _blob_b64 不是有效 Base64"
    try:
        parsed = parse_dropset_record(record)
    except (IndexError, struct.error, UnicodeDecodeError, ValueError):
        return None, "dropsetinfo new_record blob 不是完整有效记录"
    if parsed.key != intent.key:
        return None, "dropsetinfo new_record blob key 与 new_key 不一致"

    new_header, reason = _append_pabgh_entry(context, intent.key)
    if new_header is None:
        return None, reason
    change = {
        "offset": len(context.body),
        "original": "",
        "patched": record.hex(),
        "label": f"{parsed.name or intent.key}.new_record",
        "_pabgh_companion": {
            "offset": 0,
            "original": context.header.hex(),
            "patched": new_header.hex(),
            "label": "dropsetinfo new_record companion pabgh",
        },
    }
    return change, None


def _append_pabgh_entry(
    context: Format3RuntimeContext,
    new_key: int,
) -> tuple[bytes | None, str | None]:
    """在 dropsetinfo PABGH 末尾追加新 key 与当前 body 尾偏移。"""
    if context.key_size != 4 or len(context.header) < _DROPSET_PABGH_COUNT_SIZE:
        return None, "dropsetinfo new_record companion PABGH 结构不支持"
    count = struct.unpack_from("<H", context.header, 0)[0]
    expected_size = _DROPSET_PABGH_COUNT_SIZE + count * _DROPSET_PABGH_ENTRY_SIZE
    if expected_size != len(context.header):
        return None, "dropsetinfo new_record companion PABGH 长度不匹配"
    if count >= 0xFFFF:
        return None, "dropsetinfo new_record companion PABGH 记录数已满"

    output = bytearray(context.header)
    struct.pack_into("<H", output, 0, count + 1)
    output += struct.pack("<II", new_key, len(context.body))
    return bytes(output), None


def _resolve_entry(
    entry_bounds: dict[int, tuple[int, int, str, int]],
    name_to_key: dict[str, int],
    ambiguous_names: set[str],
    intent: Format3Intent,
) -> tuple[int, tuple[int, int, str, int]] | None:
    """优先按唯一 entry 名称解析真实 key，缺失或不唯一时回退到 key。"""
    if intent.entry:
        if intent.entry not in ambiguous_names:
            key = name_to_key.get(intent.entry)
            if key is not None:
                return key, entry_bounds[key]
    bounds = entry_bounds.get(intent.key)
    if bounds is not None:
        return intent.key, bounds
    return None


def _skip_intent(intent: Format3Intent, reason: str) -> Format3SkippedIntent:
    """构造单条 skipped 结果。"""
    return Format3SkippedIntent(intent=intent, reason=reason)
