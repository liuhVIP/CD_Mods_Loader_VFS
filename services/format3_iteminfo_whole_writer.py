"""ItemInfo whole-table Format 3 writer。

当前独立版的 iteminfo 已经有一条稳定的 entry+rel_offset 窄路径，但像
`cooltime`、`equip_passive_skill_list` 这类字段可通过完整
parse -> mutate -> serialize 的 whole-table 路径处理；已具备稳定定位器的
`equipable_hash` 则由单记录 writer 处理，避免为固定 4 字节修改解析整张表。

本模块复用迁入的 `iteminfo_native_parser.py`，先在内存里解析为 dict，再按
Format 3 intent 改写目标记录，最后整体序列化回 bytes。若记录长度变化，还会
通过 `_pabgh_companion` 协议把 companion `.pabgh` 一并返回给主链路由。
"""

from __future__ import annotations

import logging
import re

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

# 这些字段来自参考仓库当前已验证过的 iteminfo whole-table writer 能力。
ITEMINFO_WHOLE_TABLE_DIRECT_FIELDS = frozenset(
    {
        "cooltime",
        "unk_post_cooltime_a",
        "unk_post_cooltime_b",
        "max_charged_useable_count",
        "unk_post_max_charged_a",
        "unk_post_max_charged_b",
        "docking_child_data",
        "gimmick_info",
        "item_charge_type",
        "respawn_time_seconds",
        "prefab_data_list",
        "gimmick_visual_prefab_data_list",
        "equip_passive_skill_list",
        "occupied_equip_slot_data_list",
        "item_tag_list",
        "consumable_type_list",
        "item_use_info_list",
        "item_icon_list",
        "sealable_item_info_list",
        "sealable_character_info_list",
        "sealable_gimmick_info_list",
        "sealable_gimmick_tag_list",
        "sealable_tribe_info_list",
        "sealable_money_info_list",
        "transmutation_material_gimmick_list",
        "transmutation_material_item_list",
        "transmutation_material_item_group_list",
        "multi_change_info_list",
        "gimmick_tag_list",
    }
)

ITEMINFO_WHOLE_TABLE_NESTED_PREFIXES = (
    "prefab_data_list[",
    "drop_default_data.",
)

ITEMINFO_UNWRITEABLE_FIELDS = frozenset({"enchant_data_list"})

_LIST_ELEMENT_KINDS: dict[str, type] = {
    "equip_passive_skill_list": dict,
    "occupied_equip_slot_data_list": dict,
    "item_tag_list": int,
    "consumable_type_list": int,
    "item_use_info_list": int,
    "item_icon_list": dict,
    "sealable_item_info_list": dict,
    "sealable_character_info_list": dict,
    "sealable_gimmick_info_list": dict,
    "sealable_gimmick_tag_list": dict,
    "sealable_tribe_info_list": dict,
    "sealable_money_info_list": int,
    "transmutation_material_gimmick_list": int,
    "transmutation_material_item_list": int,
    "transmutation_material_item_group_list": int,
    "multi_change_info_list": int,
    "gimmick_tag_list": str,
    "prefab_data_list": dict,
    "gimmick_visual_prefab_data_list": dict,
}


def supports_iteminfo_whole_table_field(field: str) -> bool:
    """判断字段是否应走 iteminfo whole-table 路径。"""
    return field in ITEMINFO_WHOLE_TABLE_DIRECT_FIELDS


def should_use_iteminfo_whole_table(intents: list[Format3Intent]) -> bool:
    """只要存在 whole-table 专属字段，就把同目标 intents 一起走 whole-table。"""
    return any(supports_iteminfo_whole_table_field(intent.field) for intent in intents)


def build_iteminfo_whole_table_result(
    context: Format3RuntimeContext,
    intents: list[Format3Intent],
) -> Format3DispatchResult:
    """用 whole-table 方式处理 iteminfo intents。"""
    record_offsets = [
        bounds[0]
        for bounds in sorted(context.entry_bounds.values(), key=lambda item: item[0])
    ]
    try:
        items = parse_iteminfo_from_bytes(context.body, record_offsets=record_offsets)
    except Exception as exc:
        return _skip_all(intents, f"iteminfo 解析失败：{exc}")

    identity_offsets: dict[int, int] = {}
    try:
        identity_bytes = serialize_iteminfo(items, offsets_out=identity_offsets)
    except Exception as exc:
        return _skip_all(intents, f"iteminfo identity serialize 失败：{exc}")
    if identity_bytes != context.body:
        return _skip_all(intents, "iteminfo whole-table 预检失败：identity roundtrip 不一致")

    if context.header:
        identity_header = rewrite_pabgh_offsets(context.header, "iteminfo", identity_offsets)
        if identity_header != context.header:
            return _skip_all(intents, "iteminfo whole-table 预检失败：pabgh identity rewrite 不一致")

    by_key = {item["key"]: item for item in items if isinstance(item, dict) and "key" in item}
    by_name: dict[str, dict] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        string_key = item.get("string_key")
        if isinstance(string_key, str) and string_key and string_key not in by_name:
            by_name[string_key] = item

    skipped: list[Format3SkippedIntent] = []
    applied = 0
    for intent in intents:
        if intent.field in ITEMINFO_UNWRITEABLE_FIELDS:
            skipped.append(_skip_intent(intent, "iteminfo 当前未支持该字段的 whole-table 写回"))
            continue
        if intent.op != "set":
            skipped.append(_skip_intent(intent, "iteminfo whole-table 当前仅支持 op=set"))
            continue
        item = _resolve_item_record(intent, by_key, by_name)
        if item is None:
            skipped.append(_skip_intent(intent, "目标 entry key/名称 都未命中"))
            continue

        if "." in intent.field or "[" in intent.field:
            target = _resolve_path_target(item, intent.field)
            if target is None:
                skipped.append(_skip_intent(intent, "nested path 未命中"))
                continue
            parent, last_segment = target
            try:
                existing_nested = parent[last_segment]
            except (KeyError, IndexError, TypeError):
                existing_nested = None
            if not shape_matches(existing_nested, intent.new):
                skipped.append(_skip_intent(intent, "nested path 新值结构不匹配"))
                continue
            try:
                parent[last_segment] = intent.new
                applied += 1
            except (KeyError, IndexError, TypeError):
                skipped.append(_skip_intent(intent, "nested path 写入失败"))
            continue

        target_field = _resolve_field_name(intent.field, item)
        if target_field is None:
            if intent.field in ITEMINFO_WHOLE_TABLE_DIRECT_FIELDS:
                target_field = intent.field
            else:
                skipped.append(_skip_intent(intent, "whole-table 不支持该平铺字段"))
                continue

        existing_value = item.get(target_field)
        new_value, coerce_reason = _coerce_iteminfo_value(
            target_field,
            existing_value,
            intent.new,
        )
        if coerce_reason is not None:
            skipped.append(_skip_intent(intent, coerce_reason))
            continue

        shape_ok = shape_matches(existing_value, new_value)
        if shape_ok and (existing_value is None or (isinstance(existing_value, list) and not existing_value)):
            kind = _LIST_ELEMENT_KINDS.get(target_field)
            if kind is not None:
                shape_ok = isinstance(new_value, list) and _elements_match_kind(new_value, kind)
        if not shape_ok:
            skipped.append(_skip_intent(intent, "whole-table 新值结构不匹配"))
            continue
        try:
            item[target_field] = new_value
            applied += 1
        except Exception as exc:  # pragma: no cover - 防御性分支
            skipped.append(_skip_intent(intent, f"whole-table 写入失败：{exc}"))

    if applied == 0:
        return Format3DispatchResult(changes=(), skipped=tuple(skipped))

    new_offsets: dict[int, int] = {}
    try:
        new_body = serialize_iteminfo(items, offsets_out=new_offsets)
    except Exception as exc:
        return Format3DispatchResult(
            changes=(),
            skipped=tuple(skipped + [_skip_intent(intent, f"iteminfo serialize 失败：{exc}") for intent in intents]),
        )

    if new_body == context.body:
        return Format3DispatchResult(changes=(), skipped=tuple(skipped))

    change = {
        "offset": 0,
        "original": context.body.hex(),
        "patched": new_body.hex(),
        "label": f"iteminfo whole-table ({applied} applied)",
    }
    if context.header and new_offsets != identity_offsets:
        new_header = rewrite_pabgh_offsets(context.header, "iteminfo", new_offsets)
        if new_header is None:
            return Format3DispatchResult(
                changes=(),
                skipped=tuple(skipped + [_skip_intent(intent, "iteminfo companion pabgh rewrite 失败") for intent in intents]),
            )
        change["_pabgh_companion"] = {
            "offset": 0,
            "original": context.header.hex(),
            "patched": new_header.hex(),
            "label": "iteminfo whole-table companion pabgh",
        }

    return Format3DispatchResult(
        changes=(change,),
        skipped=tuple(skipped),
    )


def _skip_all(intents: list[Format3Intent], reason: str) -> Format3DispatchResult:
    """把整批 whole-table intents 统一标记为跳过。"""
    return Format3DispatchResult(
        changes=(),
        skipped=tuple(_skip_intent(intent, reason) for intent in intents),
    )


def _skip_intent(intent: Format3Intent, reason: str) -> Format3SkippedIntent:
    """构造单条 skipped 结果。"""
    return Format3SkippedIntent(intent=intent, reason=reason)


def _resolve_item_record(
    intent: Format3Intent,
    by_key: dict[int, dict],
    by_name: dict[str, dict],
) -> dict | None:
    """优先按 key，回退到 entry 名称查找 whole-table 记录。"""
    item = by_key.get(intent.key)
    if item is not None:
        return item
    if intent.entry:
        return by_name.get(intent.entry)
    return None


def _elements_match_kind(values: list, kind: type) -> bool:
    """校验列表字段元素类型。"""
    if kind is int:
        return all(isinstance(item, int) and not isinstance(item, bool) for item in values)
    return all(isinstance(item, kind) for item in values)


def _coerce_iteminfo_value(
    field: str,
    existing: object,
    new: object,
) -> tuple[object, str | None]:
    """把 DMM 导出的字段值适配为当前 native parser 的内部结构。"""
    if field != "prefab_data_list":
        return new, None
    return _coerce_prefab_data_list(existing, new)


def _coerce_prefab_data_list(existing: object, new: object) -> tuple[object, str | None]:
    """适配 iteminfo `prefab_data_list` 的扁平 Format 3 导出格式。

    真实游戏当前 `PrefabData` 里还有 `tag_name_hash` 和未完全解析的 tribe
    opaque 块。DMM JSON 往往只表达 `prefab_names` 与 `tribe_gender_list`；
    因此这里保留现有未知字段，只覆盖可确认的列表字段，避免 whole-table
    serialize 时丢失或伪造未知二进制结构。
    """
    if not isinstance(existing, list) or not isinstance(new, list):
        return new, None

    coerced: list[object] = []
    for index, incoming in enumerate(new):
        if not isinstance(incoming, dict):
            return new, None
        base = existing[index] if index < len(existing) else None
        if base is None and existing and isinstance(existing[-1], dict):
            # DMM 的扁平导出可能把 1 个 prefab 扩成多个，但新增元素没有
            # tag_name_hash / tribe opaque 这类当前游戏 schema 必需字段。
            # 用同记录最后一个已知 prefab 作为模板，只覆盖可确认字段。
            base = existing[-1]
        if isinstance(base, dict):
            merged = dict(base)
        elif "tag_name_hash" in incoming:
            merged = _empty_prefab_template()
        else:
            return new, "prefab_data_list 新增元素缺少 tag_name_hash，已安全跳过"

        if "tag_name_hash" in incoming:
            tag_name_hash = incoming["tag_name_hash"]
            if not _is_int_list([tag_name_hash]):
                return new, "prefab_data_list.tag_name_hash 类型不合法"
            merged["tag_name_hash"] = tag_name_hash
        elif "tag_name_hash" not in merged:
            return new, "prefab_data_list 缺少可保留的 tag_name_hash"

        for list_field in ("prefab_names", "equip_slot_list"):
            if list_field in incoming:
                values = incoming[list_field]
                if not _is_int_list(values):
                    return new, f"prefab_data_list.{list_field} 类型不合法"
                merged[list_field] = list(values)

        # DMM 的旧/扁平导出把适用族群列表放在 tribe_gender_list；当前 native
        # parser 中这一串 u32 落在 `equip_slot_list` 位置，而真正的 tribe
        # 结构仍以 opaque 块保留，不能直接用 list[int] 覆盖。
        if "tribe_gender_list" in incoming and _is_int_list(incoming["tribe_gender_list"]):
            merged["equip_slot_list"] = list(incoming["tribe_gender_list"])
        elif "tribe_gender_list" in incoming:
            tribe_value = incoming["tribe_gender_list"]
            if not (
                isinstance(tribe_value, list)
                and all(isinstance(item, dict) for item in tribe_value)
            ):
                return new, "prefab_data_list.tribe_gender_list 类型不合法"
            merged["tribe_gender_list"] = tribe_value
            merged["tribe_opaque"] = False
            merged["tribe_count"] = len(tribe_value)

        if "is_craft_material" in incoming:
            craft_material = incoming["is_craft_material"]
            if not isinstance(craft_material, int) or isinstance(craft_material, bool):
                return new, "prefab_data_list.is_craft_material 类型不合法"
            merged["is_craft_material"] = craft_material

        coerced.append(merged)
    return coerced, None


def _empty_prefab_template() -> dict:
    """构造无 tribe 限制的最小 PrefabData 默认值。"""
    return {
        "tag_name_hash": 0,
        "prefab_names": [],
        "equip_slot_list": [],
        "is_craft_material": 0,
        "tribe_count": 0,
        "tribe_opaque": False,
        "tribe_gender_list": [],
    }


def _is_int_list(value: object) -> bool:
    """判断值是否为非 bool 整数列表。"""
    return isinstance(value, list) and all(
        isinstance(item, int) and not isinstance(item, bool)
        for item in value
    )


def shape_matches(existing: object, new: object) -> bool:
    """轻量结构校验，避免把坏值直接送进 serialize。"""
    if isinstance(existing, list):
        if not isinstance(new, list):
            return False
        if existing and new:
            sample = existing[0]
            if isinstance(sample, dict):
                return all(isinstance(item, dict) for item in new)
            if isinstance(sample, list):
                return all(isinstance(item, list) for item in new)
            if isinstance(sample, bool):
                return all(isinstance(item, (bool, int)) for item in new)
            if isinstance(sample, int):
                return all(isinstance(item, int) and not isinstance(item, bool) for item in new)
            if isinstance(sample, float):
                return all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in new)
            if isinstance(sample, str):
                return all(isinstance(item, str) for item in new)
        return True
    if isinstance(existing, dict):
        return isinstance(new, dict)
    if isinstance(existing, bool):
        return isinstance(new, (bool, int))
    if isinstance(existing, int):
        return isinstance(new, int) and not isinstance(new, bool)
    if isinstance(existing, float):
        return isinstance(new, (int, float)) and not isinstance(new, bool)
    if isinstance(existing, str):
        return isinstance(new, str)
    if isinstance(existing, (bytes, bytearray)):
        return isinstance(new, (bytes, bytearray))
    return True


def _resolve_field_name(intent_field: str, item: dict) -> str | None:
    """把 field-name 风格映射到 iteminfo parser 的 dict key。"""
    if intent_field in item:
        return intent_field
    if intent_field.startswith("_"):
        stripped = intent_field.lstrip("_")
        if stripped in item:
            return stripped
        snake = re.sub(r"(?<!^)([A-Z])", r"_\1", stripped).lower()
        if snake in item:
            return snake
        normalized = stripped.replace("_", "").lower()
        matches = [key for key in item if key.replace("_", "").lower() == normalized]
        if len(matches) == 1:
            return matches[0]
    return None


def _resolve_path_target(item: dict, path: str) -> tuple[object, object] | None:
    """解析 iteminfo nested path，返回 `(parent, last_segment)`。"""
    tokens: list[tuple[str, object]] = []
    for match in re.finditer(r"([A-Za-z_]\w*)|\[(\d+)\]", path):
        name, index = match.groups()
        if name is not None:
            tokens.append(("key", name))
        else:
            tokens.append(("idx", int(index)))
    if not tokens:
        return None

    current: object = item
    for kind, value in tokens[:-1]:
        try:
            if kind == "key":
                if isinstance(current, dict) and value not in current:
                    resolved = _resolve_field_name(str(value), current)
                    if resolved is None:
                        return None
                    current = current[resolved]
                else:
                    current = current[value]  # type: ignore[index]
            else:
                current = current[value]  # type: ignore[index]
        except (KeyError, IndexError, TypeError):
            return None

    last_kind, last_value = tokens[-1]
    if last_kind == "key" and isinstance(current, dict) and last_value not in current:
        resolved = _resolve_field_name(str(last_value), current)
        if resolved is not None:
            return current, resolved
        return None
    return current, last_value
