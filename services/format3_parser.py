"""Format 3 JSON 解析与基础标准化。

本模块只负责把磁盘上的 Format 3 JSON 解析成统一结构，不直接参与
PABGB/PABGH 读写。这样后续继续迁移 `skill`、`storeinfo`、`stringinfo`
等 table writer 时，可以复用同一个入口，而不是继续把解析逻辑散落在
`format3_loader.py` 里。
"""

from __future__ import annotations

import json
from dataclasses import replace
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FORMAT3_DEFAULT_OP = "set"
FORMAT3_DEFAULT_KEY = 0

# 先迁入参考仓库里已经被真实模组验证过的 iteminfo 字段别名，统一在解析层
# 归一化，避免 capability / writer 重复维护多套命名。
_ITEMINFO_FIELD_ALIASES: dict[str, str] = {
    "cooltime.a": "cooltime",
    "cooltime.b": "unk_post_cooltime_a",
    "cooltime.c": "unk_post_cooltime_b",
    "max_charged_useable_count.a": "max_charged_useable_count",
    "max_charged_useable_count.b": "unk_post_max_charged_a",
    "max_charged_useable_count.c": "unk_post_max_charged_b",
    "gimmickInfo": "gimmick_info",
    "_gimmickInfo": "gimmick_info",
    "itemChargeType": "item_charge_type",
    "_itemChargeType": "item_charge_type",
    "_addSocketMaterialItemList": "drop_default_data.add_socket_material_item_list",
    "_socketValidCount": "drop_default_data.socket_valid_count",
    "_useSocket": "drop_default_data.use_socket",
}

# DMM 示例和部分导出会使用带下划线的目标文件名；游戏实际 entry 使用
# 无下划线表名。这里仅做已确认的安全别名，不做模糊猜测。
_FORMAT3_TARGET_ALIASES: dict[str, str] = {
    "gimmick_info.pabgb": "gimmickinfo.pabgb",
}


@dataclass(frozen=True)
class Format3Intent:
    """标准化后的单条 Format 3 intent。"""

    entry: str
    key: int
    field: str
    op: str
    new: Any
    old: str | None = None
    match: dict[str, Any] | None = None

    def to_legacy_dict(self) -> dict[str, Any]:
        """转换为当前独立加载器 writer 仍在使用的 dict 结构。"""
        return {
            "entry": self.entry,
            "key": self.key,
            "field": self.field,
            "op": self.op,
            "new": self.new,
            "old": self.old,
            "match": self.match,
        }


@dataclass(frozen=True)
class Format3TargetSpec:
    """一个目标文件及其 intents。"""

    target: str
    intents: tuple[Format3Intent, ...]


def parse_format3_file(path: Path) -> list[Format3TargetSpec]:
    """解析 Format 3 单目标 `target/intents` 或多目标 `targets[]`。"""
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"读取 Format 3 文件失败：{exc}") from exc

    if not isinstance(data, dict) or data.get("format") != 3:
        raise ValueError("缺少 format: 3 标记")

    _validate_format_minor(data.get("format_minor"))

    has_single = "target" in data
    has_multi = "targets" in data
    if has_single and has_multi:
        raise ValueError("同时存在 target 和 targets，无法判断目标结构")

    if has_single:
        target = _normalize_target_alias(_require_str(data.get("target"), "target"))
        return [
            Format3TargetSpec(
                target=target,
                intents=_normalize_intents_for_target(
                    target,
                    _parse_intents(data.get("intents"), "intents"),
                ),
            )
        ]

    raw_targets = data.get("targets")
    if not isinstance(raw_targets, list):
        raise ValueError("缺少 target 或 targets")

    specs: list[Format3TargetSpec] = []
    for index, raw_target in enumerate(raw_targets):
        if not isinstance(raw_target, dict):
            raise ValueError(f"targets[{index}] 不是对象")
        target = _normalize_target_alias(_parse_target_file(raw_target, index))
        specs.append(
            Format3TargetSpec(
                target=target,
                intents=_normalize_intents_for_target(
                    target,
                    _parse_intents(raw_target.get("intents"), f"targets[{index}].intents"),
                ),
            )
        )
    return specs


def _parse_intents(raw_intents: object, label: str) -> tuple[Format3Intent, ...]:
    """把 intents 列表标准化成独立加载器统一结构。"""
    if not isinstance(raw_intents, list):
        raise ValueError(f"{label} 不是数组")

    intents: list[Format3Intent] = []
    for index, raw_intent in enumerate(raw_intents):
        if not isinstance(raw_intent, dict):
            raise ValueError(f"{label}[{index}] 不是对象")
        match_spec = _parse_match(raw_intent.get("match"), f"{label}[{index}].match")
        if "entry" in raw_intent:
            entry = _require_entry(raw_intent.get("entry"), f"{label}[{index}].entry")
        elif match_spec is not None:
            entry = ""
        else:
            entry = _require_entry(raw_intent.get("entry"), f"{label}[{index}].entry")
        field = _require_str(raw_intent.get("field"), f"{label}[{index}].field")
        if "new" not in raw_intent:
            raise ValueError(f"{label}[{index}] 缺少 new")
        raw_key = raw_intent.get("key", 0)
        if isinstance(raw_key, bool) or not isinstance(raw_key, int):
            raise ValueError(f"{label}[{index}].key 必须是整数")
        raw_old = raw_intent.get("old")
        if raw_old is not None and not isinstance(raw_old, str):
            raise ValueError(f"{label}[{index}].old 必须是字符串")
        intents.append(
            Format3Intent(
                entry=entry,
                key=raw_key,
                field=field,
                op=str(raw_intent.get("op", FORMAT3_DEFAULT_OP)),
                new=raw_intent["new"],
                old=raw_old,
                match=match_spec,
            )
        )
    return tuple(intents)


def _parse_match(value: object, label: str) -> dict[str, Any] | None:
    """解析 DMM v3.1 match capability，当前仅在运行层做表级窄支持。"""
    if value is None:
        return None
    if not isinstance(value, dict) or not value:
        raise ValueError(f"{label} 必须是非空对象")

    normalized: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        if not isinstance(raw_key, str) or not raw_key:
            raise ValueError(f"{label} 的字段名必须是非空字符串")
        _validate_match_value(raw_value, f"{label}.{raw_key}")
        normalized[raw_key] = raw_value
    return normalized


def _validate_match_value(value: object, label: str) -> None:
    """限制 match 值为可比较的简单 JSON 标量或标量数组。"""
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return
    if isinstance(value, list) and all(
        isinstance(item, (str, int, float)) and not isinstance(item, bool)
        for item in value
    ):
        return
    raise ValueError(f"{label} 必须是字符串、数字或这两类值的数组")


def _parse_target_file(raw_target: dict[str, object], index: int) -> str:
    """兼容 multi-target 中 `file`/`target` 两种目标字段命名。"""
    for key in ("file", "target"):
        value = raw_target.get(key)
        if isinstance(value, str) and value:
            return value
    raise ValueError(f"targets[{index}] 缺少 file/target 字符串")


def _normalize_intents_for_target(
    target: str,
    intents: tuple[Format3Intent, ...],
) -> tuple[Format3Intent, ...]:
    """按目标表归一化已知字段别名。"""
    if _is_iteminfo_target(target):
        return tuple(
            replace(intent, field=_ITEMINFO_FIELD_ALIASES.get(intent.field, intent.field))
            for intent in intents
        )
    return intents


def _is_iteminfo_target(target: str) -> bool:
    """判断目标是否为 iteminfo 表。"""
    normalized = target.replace("\\", "/").lower()
    return normalized == "iteminfo.pabgb" or normalized.endswith("/iteminfo.pabgb")


def _normalize_target_alias(target: str) -> str:
    """归一化已知 Format 3 目标文件别名。"""
    normalized = target.replace("\\", "/").lower()
    basename = normalized.rsplit("/", 1)[-1]
    alias = _FORMAT3_TARGET_ALIASES.get(basename)
    if alias is None:
        return target
    if "/" not in normalized:
        return alias
    prefix = target.replace("\\", "/").rsplit("/", 1)[0]
    return f"{prefix}/{alias}"


def _validate_format_minor(value: object) -> None:
    """校验可选的 format_minor，避免明显错误的导出结构悄悄混入。"""
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("format_minor 必须是非负整数")


def _require_str(value: object, label: str) -> str:
    """保证字段存在且为字符串。"""
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} 必须是非空字符串")
    return value


def _require_entry(value: object, label: str) -> str:
    """entry 可为空字符串；真实 v3.1 模组允许只用 key 定位记录。"""
    if not isinstance(value, str):
        raise ValueError(f"{label} 必须是字符串")
    return value
