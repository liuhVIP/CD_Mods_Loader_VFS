"""Crimson Mod Package 确定性构建计划编译器。

编译器把按加载顺序排列的包合成为唯一操作集合，并为整体及每个目标生成
内容哈希。这里只生成中间计划，不写游戏文件；后续表 writer 和 VFS 缓存
必须以这些哈希作为输入身份。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cdmm.services.cdmod_compatibility import (
    RELATION_DIRECT_CONFLICT,
    RELATION_STRUCTURAL_CONFLICT,
    CdmodCompatibilityFinding,
    analyze_cdmod_compatibility,
    analyze_cdmod_packages,
)
from cdmm.services.cdmod_package import CdmodOperation, CdmodPackage

# 构建计划全部满足约束，可以交给 writer。
CDMOD_PLAN_VALID = "VALID"

# 构建计划存在无法安全解决的问题，禁止发布任何新VFS快照。
CDMOD_PLAN_REJECTED = "REJECTED"

# 构建计划schema，参与整体哈希，结构变化时必须提升。
CDMOD_BUILD_PLAN_SCHEMA = 2

_STORE_STOCK_RAW_C_PATH = re.compile(r"^stock_data_list\[\d+]\.raw_c$")


@dataclass(frozen=True)
class CdmodPlannedOperation:
    """合并后的单条最终语义操作。"""

    target: str
    selector: dict[str, Any]
    path: str
    op: str
    payload: Any
    sources: tuple[str, ...]


@dataclass(frozen=True)
class CdmodTargetPlan:
    """单个目标表的确定性操作计划和输入哈希。"""

    target: str
    input_hash: str
    operations: tuple[CdmodPlannedOperation, ...]


@dataclass(frozen=True)
class CdmodBuildPlan:
    """一次cdmod组合构建的最终计划。"""

    status: str
    plan_hash: str
    load_order: tuple[str, ...]
    targets: tuple[CdmodTargetPlan, ...]
    resolutions: tuple[str, ...]
    rejection_reasons: tuple[str, ...]


def compile_cdmod_build_plan(
    paths: list[Path],
    *,
    game_dir: Path,
) -> CdmodBuildPlan:
    """结合真实游戏表，按输入加载顺序编译确定性构建计划。"""
    report = analyze_cdmod_compatibility(paths, game_dir=game_dir)
    return compile_cdmod_report_plan(report)


def compile_cdmod_package_plan(
    packages: tuple[CdmodPackage, ...],
    *,
    game_dir: Path,
) -> CdmodBuildPlan:
    """编译已按唯一加载顺序标准化的语义包。"""
    report = analyze_cdmod_packages(packages, game_dir=game_dir)
    return compile_cdmod_report_plan(report)


def compile_cdmod_report_plan(report) -> CdmodBuildPlan:
    """从已经完成动态解析的兼容报告生成计划。"""
    rejection_reasons = _collect_rejection_reasons(report)
    if rejection_reasons:
        return _rejected_plan(report.packages, rejection_reasons)

    structural_conflicts = [
        finding
        for finding in report.findings
        if finding.relation == RELATION_STRUCTURAL_CONFLICT
    ]
    if structural_conflicts:
        reasons = tuple(_format_structural_conflict(finding) for finding in structural_conflicts)
        return _rejected_plan(report.packages, reasons)

    targets, resolutions, merge_errors = _merge_packages(report.packages)
    if merge_errors:
        return _rejected_plan(report.packages, tuple(merge_errors))
    direct_conflicts = [
        finding
        for finding in report.findings
        if finding.relation == RELATION_DIRECT_CONFLICT
    ]
    resolutions.extend(_format_load_order_resolution(finding) for finding in direct_conflicts)
    plan_hash = _hash_json(
        {
            "schema": CDMOD_BUILD_PLAN_SCHEMA,
            "load_order": [package.mod_id for package in report.packages],
            "targets": [_target_plan_payload(target) for target in targets],
        }
    )
    return CdmodBuildPlan(
        status=CDMOD_PLAN_VALID,
        plan_hash=plan_hash,
        load_order=tuple(package.mod_id for package in report.packages),
        targets=tuple(targets),
        resolutions=tuple(dict.fromkeys(resolutions)),
        rejection_reasons=(),
    )


def build_plan_to_json(plan: CdmodBuildPlan) -> dict[str, Any]:
    """把构建计划转换为稳定JSON对象。"""
    return {
        "schema": CDMOD_BUILD_PLAN_SCHEMA,
        "status": plan.status,
        "plan_hash": plan.plan_hash,
        "load_order": list(plan.load_order),
        "summary": {
            "target_count": len(plan.targets),
            "operation_count": sum(len(target.operations) for target in plan.targets),
            "resolution_count": len(plan.resolutions),
            "rejection_count": len(plan.rejection_reasons),
        },
        "targets": [_target_plan_payload(target) for target in plan.targets],
        "resolutions": list(plan.resolutions),
        "rejection_reasons": list(plan.rejection_reasons),
    }


def write_cdmod_build_plan(plan: CdmodBuildPlan, output_path: Path) -> None:
    """以UTF-8写出可供缓存和writer消费的计划文件。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(build_plan_to_json(plan), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _merge_packages(
    packages: tuple[CdmodPackage, ...],
) -> tuple[list[CdmodTargetPlan], list[str], list[str]]:
    """按包顺序合并所有坐标相同的操作。"""
    merged: dict[tuple[str, str, str], CdmodPlannedOperation] = {}
    record_paths: dict[tuple[str, str], list[tuple[str, str, int, str]]] = {}
    resolutions: list[str] = []
    errors: list[str] = []
    for package in packages:
        for operation in package.operations:
            selector_identity = _selector_identity(operation)
            if selector_identity is None:
                errors.append(f"{package.mod_id}#{operation.index}: 选择器未解析为稳定记录")
                continue
            record_key = (operation.target, selector_identity)
            seen_paths = record_paths.setdefault(record_key, [])
            for existing_path, source_id, source_index, source_op in seen_paths:
                if (
                    _paths_structurally_overlap(existing_path, operation.path)
                    and not _is_ordered_store_stock_refinement(
                        existing_path=existing_path,
                        incoming_path=operation.path,
                        source_id=source_id,
                        incoming_id=package.mod_id,
                        source_index=source_index,
                        incoming_index=operation.index,
                        source_op=source_op,
                        incoming_op=operation.op,
                    )
                ):
                    errors.append(
                        f"{package.mod_id}#{operation.index}: {operation.target} {selector_identity} "
                        f"字段 {existing_path} 与 {operation.path} 存在父子覆盖"
                    )
            seen_paths.append(
                (
                    operation.path,
                    package.mod_id,
                    operation.index,
                    operation.op,
                )
            )
            coordinate = (*record_key, operation.path)
            incoming = _planned_from_operation(package, operation)
            existing = merged.get(coordinate)
            if existing is None:
                merged[coordinate] = incoming
                continue
            combined, resolution, error = _merge_same_coordinate(existing, incoming)
            if error is not None:
                errors.append(error)
                continue
            merged[coordinate] = combined
            if resolution is not None:
                resolutions.append(resolution)

    by_target: dict[str, list[CdmodPlannedOperation]] = {}
    for operation in merged.values():
        by_target.setdefault(operation.target, []).append(operation)
    target_plans: list[CdmodTargetPlan] = []
    for target in sorted(by_target):
        operations = tuple(sorted(by_target[target], key=_planned_operation_sort_key))
        input_hash = _hash_json(
            {
                "schema": CDMOD_BUILD_PLAN_SCHEMA,
                "target": target,
                "operations": [_operation_payload(operation) for operation in operations],
            }
        )
        target_plans.append(CdmodTargetPlan(target=target, input_hash=input_hash, operations=operations))
    return target_plans, resolutions, errors


def _merge_same_coordinate(
    existing: CdmodPlannedOperation,
    incoming: CdmodPlannedOperation,
) -> tuple[CdmodPlannedOperation, str | None, str | None]:
    """按加载顺序合并同一字段坐标的操作。"""
    sources = (*existing.sources, *incoming.sources)
    coordinate = f"{existing.target} {_selector_text(existing.selector)} {existing.path}"
    if existing.op == incoming.op == "array_append":
        return (
            CdmodPlannedOperation(
                target=existing.target,
                selector=existing.selector,
                path=existing.path,
                op="array_append",
                payload=[*existing.payload, *incoming.payload],
                sources=sources,
            ),
            None,
            None,
        )
    if existing.op == incoming.op == "list_union":
        payload = _stable_union(existing.payload, incoming.payload)
        return (
            CdmodPlannedOperation(
                target=existing.target,
                selector=existing.selector,
                path=existing.path,
                op="list_union",
                payload=payload,
                sources=sources,
            ),
            f"{coordinate}: list_union 自动合并",
            None,
        )
    if existing.op == "set" and incoming.op == "list_union":
        if not isinstance(existing.payload, list):
            return existing, None, f"{coordinate}: 非数组set无法与list_union合并"
        return (
            CdmodPlannedOperation(
                target=existing.target,
                selector=existing.selector,
                path=existing.path,
                op="set",
                payload=_stable_union(existing.payload, incoming.payload),
                sources=sources,
            ),
            f"{coordinate}: 在前序set结果上应用list_union",
            None,
        )
    # 后续set具有明确覆盖语义；相同值也统一去重为一条操作。
    return (
        CdmodPlannedOperation(
            target=incoming.target,
            selector=incoming.selector,
            path=incoming.path,
            op=incoming.op,
            payload=incoming.payload,
            sources=sources,
        ),
        f"{coordinate}: 按加载顺序由 {incoming.sources[-1]} 覆盖",
        None,
    )


def _collect_rejection_reasons(report) -> tuple[str, ...]:
    """收集无法进入构建阶段的确定性失败原因。"""
    reasons = [*report.missing_dependencies, *report.resolution_errors]
    reasons.extend(f"动态选择器未解析：{value}" for value in report.unresolved_dynamic_selectors)
    return tuple(reasons)


def _rejected_plan(packages: tuple[CdmodPackage, ...], reasons: tuple[str, ...]) -> CdmodBuildPlan:
    """构造不携带任何目标产物的拒绝计划。"""
    load_order = tuple(package.mod_id for package in packages)
    plan_hash = _hash_json(
        {
            "schema": CDMOD_BUILD_PLAN_SCHEMA,
            "status": CDMOD_PLAN_REJECTED,
            "load_order": load_order,
            "reasons": reasons,
        }
    )
    return CdmodBuildPlan(
        status=CDMOD_PLAN_REJECTED,
        plan_hash=plan_hash,
        load_order=load_order,
        targets=(),
        resolutions=(),
        rejection_reasons=reasons,
    )


def _planned_from_operation(package: CdmodPackage, operation: CdmodOperation) -> CdmodPlannedOperation:
    """保留来源模组ID并转为计划操作。"""
    return CdmodPlannedOperation(
        target=operation.target,
        selector=operation.selector,
        path=operation.path,
        op=operation.op,
        payload=[operation.payload] if operation.op == "array_append" else operation.payload,
        sources=(package.mod_id,),
    )


def _selector_identity(operation: CdmodOperation) -> str | None:
    """动态match展开后优先用key，缺少key时使用规范化string_key。"""
    key = operation.selector.get("key")
    if isinstance(key, int) and not isinstance(key, bool) and key != 0:
        return f"key:{key}"
    string_key = operation.selector.get("string_key")
    if isinstance(string_key, str) and string_key:
        return f"string_key:{string_key.lower()}"
    return None


def _paths_structurally_overlap(left: str, right: str) -> bool:
    """只判断不同路径之间的父子覆盖；相同路径由正常合并处理。"""
    if left == right:
        return False
    return _is_parent(left, right) or _is_parent(right, left)


def _is_ordered_store_stock_refinement(
    *,
    existing_path: str,
    incoming_path: str,
    source_id: str,
    incoming_id: str,
    source_index: int,
    incoming_index: int,
    source_op: str,
    incoming_op: str,
) -> bool:
    """放行已验证的 V3 库存追加后按索引细化 raw_c 的同一包模式。"""
    # 不依赖 legacy-format3- 前缀：cdmod 转换器保留相同 op/path/顺序，
    # 同一包内先 array_append 整表、后 set 索引 raw_c 的窄模式同样经过 V3 实机验证。
    # A full indexed record may precede the append operation in the exported
    # 2.00.01 field files: it addresses an existing vanilla slot, while the
    # later append only adds new slots.  A raw_c refinement is different: it
    # must follow the parent list operation so that the index is unambiguous.
    indexed_path = re.fullmatch(r"stock_data_list\[\d+\]", incoming_path)
    reverse_indexed_path = re.fullmatch(r"stock_data_list\[\d+\]", existing_path)
    if existing_path == "stock_data_list" and indexed_path:
        return source_op == "array_append" and incoming_op == "set"
    if incoming_path == "stock_data_list" and reverse_indexed_path:
        return incoming_op == "array_append" and source_op == "set"
    return (
        source_id == incoming_id
        and source_index < incoming_index
        and source_op == "array_append"
        and incoming_op == "set"
        and existing_path == "stock_data_list"
        and _STORE_STOCK_RAW_C_PATH.fullmatch(incoming_path) is not None
    )


def _is_parent(parent: str, child: str) -> bool:
    """判断点路径或数组路径父子关系。"""
    return child.startswith(parent) and len(child) > len(parent) and child[len(parent)] in ".["


def _stable_union(left: Any, right: Any) -> list[Any]:
    """对JSON值数组执行支持对象元素的确定性去重并集。"""
    if not isinstance(left, list) or not isinstance(right, list):
        raise ValueError("list_union payload 必须是数组")
    result: list[Any] = []
    seen: set[str] = set()
    for value in [*left, *right]:
        identity = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if identity in seen:
            continue
        seen.add(identity)
        result.append(value)
    return result


def _planned_operation_sort_key(operation: CdmodPlannedOperation) -> tuple[str, str]:
    """返回与输入文件枚举无关的稳定排序键。"""
    return _selector_text(operation.selector), operation.path


def _selector_text(selector: dict[str, Any]) -> str:
    """把选择器编码为确定性文本。"""
    return json.dumps(selector, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _operation_payload(operation: CdmodPlannedOperation) -> dict[str, Any]:
    """生成参与哈希和输出的操作对象。"""
    return {
        "selector": operation.selector,
        "path": operation.path,
        "op": operation.op,
        "payload": operation.payload,
        "sources": list(operation.sources),
    }


def _target_plan_payload(target: CdmodTargetPlan) -> dict[str, Any]:
    """生成目标计划JSON对象。"""
    return {
        "target": target.target,
        "input_hash": target.input_hash,
        "operations": [_operation_payload(operation) for operation in target.operations],
    }


def _hash_json(value: Any) -> str:
    """对规范化JSON生成SHA256内容哈希。"""
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _format_structural_conflict(finding: CdmodCompatibilityFinding) -> str:
    """格式化必须拒绝的父子字段冲突。"""
    return (
        f"{finding.target} {finding.selector}: {finding.mod_a}/{finding.path_a} 与 "
        f"{finding.mod_b}/{finding.path_b} 存在父子结构覆盖"
    )


def _format_load_order_resolution(finding: CdmodCompatibilityFinding) -> str:
    """记录明确按最终加载顺序解决的同字段冲突。"""
    return (
        f"{finding.target} {finding.selector} {finding.path_a}: "
        f"{finding.mod_b} 按加载顺序覆盖 {finding.mod_a}"
    )
