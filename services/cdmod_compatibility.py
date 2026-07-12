"""Crimson Mod Package 字段级兼容性分析。

分析器不读取游戏文件，只根据新格式的稳定选择器、字段路径和操作语义判断
哪些修改可以组合。真正执行合并前仍需由表 writer 校验目标记录和字段类型。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from cdmm.services.cdmod_package import CdmodOperation, CdmodPackage, load_cdmod_package

# 无需用户干预即可组合的关系。
RELATION_AUTO_MERGE = "auto-merge"

# 两个模组写入相同结果，不需要重复执行后者。
RELATION_REDUNDANT = "redundant"

# 同字段写入不同结果，最终必须由加载顺序或用户规则决定。
RELATION_DIRECT_CONFLICT = "direct-conflict"

# 一个操作覆盖父结构，另一个修改其子字段，不能独立执行。
RELATION_STRUCTURAL_CONFLICT = "structural-conflict"


@dataclass(frozen=True)
class CdmodCompatibilityFinding:
    """两个模组之间的一条字段关系。"""

    relation: str
    mod_a: str
    mod_b: str
    target: str
    selector: str
    path_a: str
    path_b: str
    op_a: str
    op_b: str
    resolution: str


@dataclass(frozen=True)
class CdmodCompatibilityReport:
    """一组 cdmod 的兼容性报告。"""

    packages: tuple[CdmodPackage, ...]
    findings: tuple[CdmodCompatibilityFinding, ...]
    missing_dependencies: tuple[str, ...]
    unresolved_dynamic_selectors: tuple[str, ...]
    resolution_errors: tuple[str, ...]
    disjoint_field_pair_count: int

    @property
    def conflict_count(self) -> int:
        """返回需要加载顺序或用户处理的冲突数量。"""
        return sum(
            finding.relation in {RELATION_DIRECT_CONFLICT, RELATION_STRUCTURAL_CONFLICT}
            for finding in self.findings
        )

    @property
    def auto_merge_count(self) -> int:
        """返回可自动组合的关系数量。"""
        return sum(finding.relation == RELATION_AUTO_MERGE for finding in self.findings)


def analyze_cdmod_compatibility(
    paths: list[Path],
    *,
    game_dir: Path | None = None,
) -> CdmodCompatibilityReport:
    """按传入顺序读取多个 cdmod，可选结合真实游戏表展开动态选择器。"""
    packages = tuple(load_cdmod_package(path) for path in paths)
    return analyze_cdmod_packages(packages, game_dir=game_dir)


def analyze_cdmod_packages(
    packages: tuple[CdmodPackage, ...],
    *,
    game_dir: Path | None = None,
) -> CdmodCompatibilityReport:
    """分析已经标准化的包，供旧Format 3与cdmod统一顺序复用。"""
    _validate_unique_mod_ids(packages)
    resolution_errors: tuple[str, ...] = ()
    if game_dir is not None:
        from cdmm.services.cdmod_game_resolver import resolve_cdmod_dynamic_selectors

        packages, resolution_errors = resolve_cdmod_dynamic_selectors(packages, game_dir)
    groups: dict[tuple[str, str], list[tuple[CdmodPackage, CdmodOperation]]] = {}
    for package in packages:
        for operation in package.operations:
            for selector_identity in _selector_identities(operation.selector):
                key = (operation.target, selector_identity)
                groups.setdefault(key, []).append((package, operation))

    findings: list[CdmodCompatibilityFinding] = []
    disjoint_field_pairs = 0
    compared_pairs: set[tuple[str, int, str, int]] = set()
    for (_target, selector_identity), entries in groups.items():
        for left_index, (left_package, left_operation) in enumerate(entries):
            for right_package, right_operation in entries[left_index + 1 :]:
                if left_package.mod_id == right_package.mod_id:
                    continue
                pair_key = _operation_pair_key(
                    left_package,
                    left_operation,
                    right_package,
                    right_operation,
                )
                if pair_key in compared_pairs:
                    continue
                compared_pairs.add(pair_key)
                finding = _compare_operations(
                    left_package,
                    left_operation,
                    right_package,
                    right_operation,
                    selector_identity,
                )
                if finding is None:
                    disjoint_field_pairs += 1
                else:
                    findings.append(finding)

    return CdmodCompatibilityReport(
        packages=packages,
        findings=tuple(findings),
        missing_dependencies=_find_missing_dependencies(packages),
        unresolved_dynamic_selectors=_find_unresolved_dynamic_selectors(packages),
        resolution_errors=resolution_errors,
        disjoint_field_pair_count=disjoint_field_pairs,
    )


def compatibility_report_to_json(report: CdmodCompatibilityReport) -> dict[str, Any]:
    """把兼容性报告转换为便于 CLI/GUI 消费的 JSON。"""
    return {
        "schema": 1,
        "summary": {
            "package_count": len(report.packages),
            "operation_count": sum(len(package.operations) for package in report.packages),
            "finding_count": len(report.findings),
            "auto_merge_count": report.auto_merge_count,
            "conflict_count": report.conflict_count,
            "missing_dependency_count": len(report.missing_dependencies),
            "unresolved_dynamic_selector_count": len(report.unresolved_dynamic_selectors),
            "resolution_error_count": len(report.resolution_errors),
            "disjoint_field_pair_count": report.disjoint_field_pair_count,
        },
        "packages": [
            {
                "id": package.mod_id,
                "name": package.name,
                "version": package.version,
                "path": str(package.path),
                "operation_count": len(package.operations),
            }
            for package in report.packages
        ],
        "missing_dependencies": list(report.missing_dependencies),
        "unresolved_dynamic_selectors": list(report.unresolved_dynamic_selectors),
        "resolution_errors": list(report.resolution_errors),
        "findings": [asdict(finding) for finding in report.findings],
    }


def write_compatibility_report(report: CdmodCompatibilityReport, output_path: Path) -> None:
    """以 UTF-8 JSON 写出兼容性报告。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(compatibility_report_to_json(report), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _compare_operations(
    left_package: CdmodPackage,
    left: CdmodOperation,
    right_package: CdmodPackage,
    right: CdmodOperation,
    selector_identity: str,
) -> CdmodCompatibilityFinding | None:
    """比较同一目标记录内的两条操作。"""
    if not _paths_overlap(left.path, right.path):
        return None
    if left.path != right.path:
        return _build_finding(
            RELATION_STRUCTURAL_CONFLICT,
            left_package,
            left,
            right_package,
            right,
            selector_identity,
            "父字段与子字段存在覆盖关系，需按最终表结构重新合成",
        )
    if left.op == right.op == "list_union":
        return _build_finding(
            RELATION_AUTO_MERGE,
            left_package,
            left,
            right_package,
            right,
            selector_identity,
            "对两个数组做稳定去重并集",
        )
    if left.op == right.op == "set" and left.payload == right.payload:
        return _build_finding(
            RELATION_REDUNDANT,
            left_package,
            left,
            right_package,
            right,
            selector_identity,
            "两个模组设置相同值，仅执行一次",
        )
    return _build_finding(
        RELATION_DIRECT_CONFLICT,
        left_package,
        left,
        right_package,
        right,
        selector_identity,
        "按唯一加载顺序决定，后一个模组优先",
    )


def _build_finding(
    relation: str,
    left_package: CdmodPackage,
    left: CdmodOperation,
    right_package: CdmodPackage,
    right: CdmodOperation,
    selector: str,
    resolution: str,
) -> CdmodCompatibilityFinding:
    """构建字段关系对象。"""
    return CdmodCompatibilityFinding(
        relation=relation,
        mod_a=left_package.mod_id,
        mod_b=right_package.mod_id,
        target=left.target,
        selector=selector,
        path_a=left.path,
        path_b=right.path,
        op_a=left.op,
        op_b=right.op,
        resolution=resolution,
    )


def _selector_identities(selector: dict[str, Any]) -> tuple[str, ...]:
    """返回选择器的全部稳定别名，使 key/string_key 可以跨模组相互命中。"""
    identities: list[str] = []
    key = selector.get("key")
    if isinstance(key, int) and not isinstance(key, bool) and key != 0:
        identities.append(f"key:{key}")
    string_key = selector.get("string_key")
    if isinstance(string_key, str) and string_key:
        identities.append(f"string_key:{string_key.lower()}")
    match = selector.get("match")
    if isinstance(match, dict) and match:
        identities.append("match:" + json.dumps(match, ensure_ascii=False, sort_keys=True))
    return tuple(identities)


def _operation_pair_key(
    left_package: CdmodPackage,
    left: CdmodOperation,
    right_package: CdmodPackage,
    right: CdmodOperation,
) -> tuple[str, int, str, int]:
    """生成与分组别名无关的操作对 ID，避免 key/string_key 重复报告。"""
    left_key = (left_package.mod_id, left.index)
    right_key = (right_package.mod_id, right.index)
    if left_key <= right_key:
        return (*left_key, *right_key)
    return (*right_key, *left_key)


def _paths_overlap(left: str, right: str) -> bool:
    """判断字段相同，或一方是另一方的结构化父路径。"""
    if left == right:
        return True
    return _is_parent_path(left, right) or _is_parent_path(right, left)


def _is_parent_path(parent: str, child: str) -> bool:
    """检查 ``parent`` 是否为点路径或数组路径的父级。"""
    if not child.startswith(parent) or len(child) == len(parent):
        return False
    return child[len(parent)] in ".["


def _validate_unique_mod_ids(packages: tuple[CdmodPackage, ...]) -> None:
    """同一分析集合不允许出现重复 ID，避免依赖和顺序含义不明确。"""
    seen: set[str] = set()
    duplicates: set[str] = set()
    for package in packages:
        if package.mod_id in seen:
            duplicates.add(package.mod_id)
        seen.add(package.mod_id)
    if duplicates:
        raise ValueError(f"发现重复 cdmod id：{', '.join(sorted(duplicates))}")


def _find_missing_dependencies(packages: tuple[CdmodPackage, ...]) -> tuple[str, ...]:
    """检查 manifest 中声明但未出现在当前集合的依赖。"""
    available = {package.mod_id for package in packages}
    missing = {
        f"{package.mod_id} -> {dependency}"
        for package in packages
        for dependency in package.dependencies
        if dependency not in available
    }
    return tuple(sorted(missing))


def _find_unresolved_dynamic_selectors(packages: tuple[CdmodPackage, ...]) -> tuple[str, ...]:
    """列出必须结合真实表展开后才能做精确冲突分析的 match 操作。"""
    unresolved = {
        (
            f"{package.mod_id}#{operation.index} {operation.target} "
            f"{json.dumps(operation.selector['match'], ensure_ascii=False, sort_keys=True)} "
            f"-> {operation.path}"
        )
        for package in packages
        for operation in package.operations
        if isinstance(operation.selector.get("match"), dict)
        and operation.selector["match"]
        and not (
            isinstance(operation.selector.get("key"), int)
            and not isinstance(operation.selector.get("key"), bool)
            and operation.selector["key"] != 0
        )
        and not operation.selector.get("string_key")
    }
    return tuple(sorted(unresolved))
