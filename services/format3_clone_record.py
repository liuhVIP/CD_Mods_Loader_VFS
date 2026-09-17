"""Format 3 `clone_record` 通用引擎。

DMM Field JSON v3.1 用 `clone_record` 表达“复制一条已有记录到新 key，再按
`patches[]` 覆盖若干字段”。对 PABGB 表来说它等价于两步：

1. 在 `.pabgb` 末尾追加一条新记录（内容 = 源记录 + 字段覆盖）；
2. 在 companion `.pabgh` 末尾追加 `(new_key, new_offset)`。

实测 2.02.00 的 iteminfo / dropsetinfo / multichangeinfo / itemgroupinfo /
characterinfo / skill 六张表，`.pabgh` 条目顺序就是 `.pabgb` 的物理布局顺序
（offset 随条目单调递增，key 本身并不升序），因此“追加到末尾”与 vanilla
结构一致，不需要按 key 重排。

本模块只提供与表无关的骨架：真实字段写入由各表 writer 传入的 `patch_item`
回调完成，避免在这里重新实现每张表的 schema。
"""

from __future__ import annotations

import copy
import struct
from dataclasses import dataclass, field
from typing import Any, Callable

from cdmm.services.format3_parser import Format3Intent
from cdmm.services.format3_runtime import Format3SkippedIntent
from cdmm.services.pab_table_service import UINT_COUNT_TABLES


@dataclass
class CloneRecordOutcome:
    """`clone_record` 批量执行结果。"""

    items: list[Any]
    applied: int = 0
    appended_keys: list[int] = field(default_factory=list)
    skipped: list[Format3SkippedIntent] = field(default_factory=list)


def clone_records(
    items: list[Any],
    intents: list[Format3Intent],
    *,
    patch_item: Callable[[Any, str, Any], str | None],
    key_field: str = "key",
) -> CloneRecordOutcome:
    """按 `clone_record` intents 在 `items` 末尾克隆新记录。

    `patch_item(item, path, value)` 负责把单条 `patches[]` 写进克隆体；返回
    非 None 字符串表示失败原因，该条 intent 整体跳过（不会半写入）。
    """
    outcome = CloneRecordOutcome(items=list(items))
    known_keys = {
        item[key_field]
        for item in items
        if isinstance(item, dict) and isinstance(item.get(key_field), int)
    }
    by_key = {
        item[key_field]: item
        for item in items
        if isinstance(item, dict) and isinstance(item.get(key_field), int)
    }

    for intent in intents:
        payload = intent.new
        if not isinstance(payload, dict):
            outcome.skipped.append(
                _skip(intent, "clone_record 缺少 source_key/patches 结构")
            )
            continue
        source_key = payload.get("source_key")
        patches = payload.get("patches")
        if isinstance(source_key, bool) or not isinstance(source_key, int):
            outcome.skipped.append(_skip(intent, "clone_record source_key 不是整数"))
            continue
        if not isinstance(patches, list) or not patches:
            outcome.skipped.append(_skip(intent, "clone_record patches 为空"))
            continue
        if intent.key in known_keys:
            outcome.skipped.append(
                _skip(intent, f"clone_record 新 key={intent.key} 已存在")
            )
            continue
        source = by_key.get(source_key)
        if source is None:
            outcome.skipped.append(
                _skip(intent, f"clone_record 源 key={source_key} 不在当前表中")
            )
            continue

        clone = copy.deepcopy(source)
        clone[key_field] = intent.key
        reason = None
        for patch in patches:
            if not isinstance(patch, dict):
                reason = "clone_record patch 不是对象"
                break
            path = patch.get("path")
            if not isinstance(path, str) or not path:
                reason = "clone_record patch 缺少 path"
                break
            reason = patch_item(clone, path, patch.get("new"))
            if reason is not None:
                reason = f"clone_record {path} 写入失败：{reason}"
                break
        if reason is not None:
            outcome.skipped.append(_skip(intent, reason))
            continue

        outcome.items.append(clone)
        outcome.appended_keys.append(intent.key)
        known_keys.add(intent.key)
        by_key[intent.key] = clone
        outcome.applied += 1

    return outcome


def append_pabgh_entries(
    header: bytes,
    table_name: str,
    entries: list[tuple[int, int]],
) -> bytes | None:
    """在 companion `.pabgh` 末尾追加 `(key, offset)` 条目。

    返回 None 表示 header 结构不可信（key_size 推导失败、长度或计数溢出）。
    """
    if not entries:
        return header
    count_size = 4 if table_name.lower() in UINT_COUNT_TABLES else 2
    if len(header) < count_size:
        return None
    count = struct.unpack_from("<I" if count_size == 4 else "<H", header, 0)[0]
    if count == 0:
        return None
    total_key_bytes = len(header) - count_size - count * 4
    if total_key_bytes <= 0 or total_key_bytes % count:
        return None
    key_size = total_key_bytes // count
    if key_size not in (2, 4):
        return None
    if count + len(entries) > (0xFFFFFFFF if count_size == 4 else 0xFFFF):
        return None
    for key, _offset in entries:
        if key < 0 or key >= (1 << (key_size * 8)):
            return None

    output = bytearray(header)
    struct.pack_into("<I" if count_size == 4 else "<H", output, 0, count + len(entries))
    for key, offset in entries:
        output += key.to_bytes(key_size, "little") + struct.pack("<I", offset)
    return bytes(output)


def build_append_body_change(
    body: bytes,
    appended: bytes,
    *,
    label: str,
    header: bytes | None = None,
    new_header: bytes | None = None,
) -> dict:
    """构造“在 `.pabgb` 末尾追加记录”的 change，可携带 `.pabgh` companion。"""
    change: dict[str, Any] = {
        "offset": len(body),
        "original": "",
        "patched": appended.hex(),
        "label": label,
    }
    if header is not None and new_header is not None:
        change["_pabgh_companion"] = {
            "offset": 0,
            "original": header.hex(),
            "patched": new_header.hex(),
            "label": f"{label} companion pabgh",
        }
    return change


def _skip(intent: Format3Intent, reason: str) -> Format3SkippedIntent:
    """构造单条 skipped 结果。"""
    return Format3SkippedIntent(intent=intent, reason=reason)
