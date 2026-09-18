"""把有效的cdmod构建计划桥接为现有Format 3运行时输入。

游戏不识别cdmod；该桥接层确保新格式继续复用已经实机验证的表writer、
PABGH修复和overlay合成链路。计划中的集合操作已完成全局合并，桥接后统一
写成最终set值，避免旧writer需要理解新的包级操作语义。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from cdmm.services.cdmod_build_plan import CDMOD_PLAN_VALID, CdmodBuildPlan
from cdmm.services.format3_iteminfo_price_writer import is_iteminfo_price_field
from cdmm.services.format3_iteminfo_record_writer import ITEMINFO_RECORD_DIRECT_FIELDS
from cdmm.services.format3_parser import FORMAT3_NEW_RECORD_FIELD

# 桥接文件格式版本，参与诊断但仍保持DMM Format 3兼容形态。
CDMOD_FORMAT3_BRIDGE_VERSION = 4

# ItemInfo不同字段必须进入现有writer的正确分流，不能混成一个巨型目标。
ITEMINFO_PREFAB_NARROW_PATTERN = re.compile(
    r"^prefab_data_list\[\d+]\.tribe_gender_list$"
)

# live ItemInfo中已验证的EnchantData窄路径，必须与whole-table批次隔离。
ITEMINFO_ENCHANT_EQUIP_BUFFS_PATTERN = re.compile(
    r"^enchant_data_list\[\d+]\.equip_buffs$"
)


def build_format3_bridge_document(plan: CdmodBuildPlan) -> dict[str, Any]:
    """将一个VALID计划转换为现有Format 3多目标文档。"""
    if plan.status != CDMOD_PLAN_VALID:
        raise ValueError("只有VALID的cdmod构建计划可以桥接到Format 3")
    targets: list[dict[str, Any]] = []
    for target_plan in plan.targets:
        intent_batches: dict[str, list[dict[str, Any]]] = {}
        visual_selectors = {
            json.dumps(operation.selector, ensure_ascii=False, sort_keys=True)
            for operation in target_plan.operations
            if operation.path == "gimmick_visual_prefab_data_list"
        }
        for operation in target_plan.operations:
            selector = operation.selector
            family = _bridge_family(
                target_plan.target,
                operation.path,
                operation.selector,
                visual_selectors,
            )
            batch = intent_batches.setdefault(family, [])
            batch.extend(_bridge_intents(selector, operation))
        for family in _ordered_bridge_families(intent_batches):
            targets.append(
                {
                    "file": target_plan.target,
                    "intents": intent_batches[family],
                    "_cdmod_writer_family": family,
                }
            )
    return {
        "modinfo": {
            "title": "cdmod-build-plan",
            "version": str(CDMOD_FORMAT3_BRIDGE_VERSION),
            "author": "cdloader",
            "description": f"deterministic cdmod bridge {plan.plan_hash}",
        },
        "format": 3,
        "targets": targets,
        "_cdmod": {
            "bridge_version": CDMOD_FORMAT3_BRIDGE_VERSION,
            "plan_hash": plan.plan_hash,
            "load_order": list(plan.load_order),
            "target_hashes": {
                target_plan.target: target_plan.input_hash
                for target_plan in plan.targets
            },
        },
    }


def _bridge_intents(
    selector: dict[str, Any],
    operation: Any,
) -> list[dict[str, Any]]:
    """把一条计划操作还原为 writer 可直接消费的 Format 3 intent。

    计划层已经完成跨模组合并，但 `clone_record` 与列表操作族
    （list_append/list_merge/list_union/array_append）无法用 `set` 表达：

    * `clone_record` 需要 `source_key`/`new_key`/`patches` 三件套；
    * 列表追加/并集需要保留 op 名称，否则 writer 会按“整体替换”处理，
      静默丢掉原版成员和前序模组追加的内容。

    因此这里保留原 op 名称，只做 `array_append` 的逐元素展开。
    """
    if operation.op == "array_append":
        if not isinstance(operation.payload, list):
            raise ValueError("array_append 计划 payload 必须是列表")
        return [
            _bridge_intent(selector, operation.path, "array_append", value)
            for value in operation.payload
        ]
    if operation.op == "clone_record":
        payload = operation.payload
        if (
            not isinstance(payload, dict)
            or "source_key" not in payload
            or "patches" not in payload
        ):
            raise ValueError("clone_record 计划 payload 必须携带 source_key/patches")
        new_key = selector.get("key")
        if isinstance(new_key, bool) or not isinstance(new_key, int):
            raise ValueError("clone_record 计划缺少整数 new_key")
        return [
            {
                "op": "clone_record",
                "source_key": payload["source_key"],
                "new_key": new_key,
                "patches": payload["patches"],
            }
        ]
    if operation.op == "new_record" or operation.path == FORMAT3_NEW_RECORD_FIELD:
        # `new_record` 必须按 DMM 原生形态回写：`new_key` + `template`。
        # 如果按通用分支写成 `entry/key/field/new`，回读时会被当成 PALOC 的
        # `new_record` 而报 “entry 必须是非空字符串”，把整个目标连带丢掉。
        new_key = selector.get("key")
        if isinstance(new_key, bool) or not isinstance(new_key, int):
            raise ValueError("new_record 计划缺少整数 new_key")
        return [
            {
                "op": "new_record",
                "new_key": new_key,
                "template": operation.payload,
            }
        ]
    return [
        _bridge_intent(
            selector,
            operation.path,
            operation.op,
            operation.payload,
            merge_key=operation.merge_key,
        )
    ]


def _bridge_intent(
    selector: dict[str, Any],
    path: str,
    op: str,
    value: Any,
    *,
    merge_key: str | None = None,
) -> dict[str, Any]:
    """Build one legacy Format 3 intent without losing append order."""
    intent = {
        "entry": str(selector.get("string_key") or ""),
        "key": selector.get("key", 0),
        "field": path,
        "op": op,
        "new": value,
    }
    if merge_key is not None:
        intent["merge_key"] = merge_key
    return intent


def _bridge_family(
    target: str,
    field: str,
    selector: dict[str, Any],
    visual_selectors: set[str],
) -> str:
    """把ItemInfo操作路由到已验证的窄/整表writer批次。"""
    if target.rsplit("/", 1)[-1].lower() != "iteminfo.pabgb":
        return "default"
    if field == "prefab_data_list":
        selector_key = json.dumps(selector, ensure_ascii=False, sort_keys=True)
        if selector_key in visual_selectors:
            return "iteminfo-visual-prefab"
        return "iteminfo-prefab-whole"
    if field == "gimmick_visual_prefab_data_list":
        return "iteminfo-visual-prefab"
    if ITEMINFO_PREFAB_NARROW_PATTERN.fullmatch(field):
        return "iteminfo-prefab-narrow"
    if field.startswith("drop_default_data."):
        return "iteminfo-drop-default"
    if ITEMINFO_ENCHANT_EQUIP_BUFFS_PATTERN.fullmatch(field):
        return "iteminfo-enchant-equip-buffs"
    if is_iteminfo_price_field(field):
        return "iteminfo-price"
    if field in ITEMINFO_RECORD_DIRECT_FIELDS:
        return "iteminfo-record"
    return "iteminfo-whole-fields"


def _ordered_bridge_families(batches: dict[str, list[dict[str, Any]]]) -> list[str]:
    """固定批次顺序，保证同输入输出稳定且保持基础表到细粒度修改的层次。"""
    preferred = (
        "default",
        "iteminfo-whole-fields",
        "iteminfo-price",
        "iteminfo-record",
        "iteminfo-enchant-equip-buffs",
        "iteminfo-drop-default",
        "iteminfo-prefab-whole",
        "iteminfo-prefab-narrow",
        "iteminfo-visual-prefab",
    )
    return [family for family in preferred if family in batches]


def write_format3_bridge(plan: CdmodBuildPlan, output_path: Path) -> None:
    """确定性写出桥接JSON，供现有Format 3加载器消费。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            build_format3_bridge_document(plan),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
