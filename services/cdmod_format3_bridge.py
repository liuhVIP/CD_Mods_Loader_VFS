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

# 桥接文件格式版本，参与诊断但仍保持DMM Format 3兼容形态。
CDMOD_FORMAT3_BRIDGE_VERSION = 1

# ItemInfo不同字段必须进入现有writer的正确分流，不能混成一个巨型目标。
ITEMINFO_PREFAB_NARROW_PATTERN = re.compile(
    r"^prefab_data_list\[\d+]\.tribe_gender_list$"
)


def build_format3_bridge_document(plan: CdmodBuildPlan) -> dict[str, Any]:
    """将一个VALID计划转换为现有Format 3多目标文档。"""
    if plan.status != CDMOD_PLAN_VALID:
        raise ValueError("只有VALID的cdmod构建计划可以桥接到Format 3")
    targets: list[dict[str, Any]] = []
    for target_plan in plan.targets:
        intent_batches: dict[str, list[dict[str, Any]]] = {}
        for operation in target_plan.operations:
            selector = operation.selector
            intent: dict[str, Any] = {
                "entry": str(selector.get("string_key") or ""),
                "key": selector.get("key", 0),
                "field": operation.path,
                "op": "set",
                "new": operation.payload,
            }
            family = _bridge_family(target_plan.target, operation.path)
            intent_batches.setdefault(family, []).append(intent)
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


def _bridge_family(target: str, field: str) -> str:
    """把ItemInfo操作路由到已验证的窄/整表writer批次。"""
    if target.rsplit("/", 1)[-1].lower() != "iteminfo.pabgb":
        return "default"
    if field == "prefab_data_list":
        return "iteminfo-prefab-whole"
    if ITEMINFO_PREFAB_NARROW_PATTERN.fullmatch(field):
        return "iteminfo-prefab-narrow"
    if field.startswith("drop_default_data."):
        return "iteminfo-drop-default"
    return "iteminfo-whole-fields"


def _ordered_bridge_families(batches: dict[str, list[dict[str, Any]]]) -> list[str]:
    """固定批次顺序，保证同输入输出稳定且保持基础表到细粒度修改的层次。"""
    preferred = (
        "default",
        "iteminfo-prefab-whole",
        "iteminfo-prefab-narrow",
        "iteminfo-whole-fields",
        "iteminfo-drop-default",
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
