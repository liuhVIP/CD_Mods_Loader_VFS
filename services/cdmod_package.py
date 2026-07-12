"""Crimson Mod Package 读取与结构校验。

本模块只把 ``.cdmod`` 容器解析成稳定的数据模型，不负责决定加载顺序或
执行表写入。兼容分析器和未来 VFS 构建器必须共享这一入口。
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cdmm.services.cdmod_converter import (
    CDMOD_FORMAT_NAME,
    CDMOD_FORMAT_VERSION,
    CDMOD_MANIFEST_PATH,
    CDMOD_PATCH_PATH,
)

# 第一版允许的语义操作，未知操作必须显式拒绝，不能静默跳过。
SUPPORTED_CDMOD_OPERATIONS = frozenset({"set", "list_union"})


@dataclass(frozen=True)
class CdmodOperation:
    """一条经过结构校验的语义操作。"""

    target: str
    selector: dict[str, Any]
    path: str
    op: str
    payload: Any
    conversion: str
    index: int


@dataclass(frozen=True)
class CdmodPackage:
    """一个已读取的 cdmod 包。"""

    path: Path
    mod_id: str
    name: str
    version: str
    dependencies: tuple[str, ...]
    operations: tuple[CdmodOperation, ...]


def load_cdmod_package(path: Path) -> CdmodPackage:
    """读取并严格校验一个 ``.cdmod`` 文件。"""
    path = path.resolve()
    try:
        with zipfile.ZipFile(path) as archive:
            manifest = _read_zip_json(archive, CDMOD_MANIFEST_PATH)
            patch = _read_zip_json(archive, CDMOD_PATCH_PATH)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError(f"无法读取 cdmod：{exc}") from exc

    _validate_manifest(manifest)
    operations = _parse_operations(patch)
    dependencies = manifest.get("dependencies", [])
    if not isinstance(dependencies, list) or not all(isinstance(item, str) for item in dependencies):
        raise ValueError("manifest.dependencies 必须是字符串数组")
    return CdmodPackage(
        path=path,
        mod_id=_require_non_empty_string(manifest.get("id"), "manifest.id"),
        name=_require_non_empty_string(manifest.get("name"), "manifest.name"),
        version=_require_non_empty_string(manifest.get("version"), "manifest.version"),
        dependencies=tuple(dependencies),
        operations=tuple(operations),
    )


def _read_zip_json(archive: zipfile.ZipFile, archive_path: str) -> dict[str, Any]:
    """从 ZIP 读取一个必需的 JSON 对象。"""
    try:
        payload = archive.read(archive_path)
    except KeyError as exc:
        raise ValueError(f"cdmod 缺少 {archive_path}") from exc
    try:
        value = json.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{archive_path} 不是有效 UTF-8 JSON：{exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{archive_path} 根节点必须是对象")
    return value


def _validate_manifest(manifest: dict[str, Any]) -> None:
    """校验容器格式和当前支持的主版本。"""
    if manifest.get("format") != CDMOD_FORMAT_NAME:
        raise ValueError("manifest.format 不是 crimson-mod-package")
    if manifest.get("format_version") != CDMOD_FORMAT_VERSION:
        raise ValueError(f"当前仅支持 cdmod format_version={CDMOD_FORMAT_VERSION}")


def _parse_operations(patch: dict[str, Any]) -> list[CdmodOperation]:
    """解析所有目标的语义操作并分配稳定全局序号。"""
    if patch.get("schema") != 1:
        raise ValueError("当前仅支持 patches/semantic.json schema=1")
    targets = patch.get("targets")
    if not isinstance(targets, list):
        raise ValueError("patch.targets 必须是数组")

    result: list[CdmodOperation] = []
    for target_index, target in enumerate(targets):
        if not isinstance(target, dict):
            raise ValueError(f"patch.targets[{target_index}] 必须是对象")
        target_file = _normalize_target(
            _require_non_empty_string(target.get("file"), f"patch.targets[{target_index}].file")
        )
        raw_operations = target.get("operations")
        if not isinstance(raw_operations, list):
            raise ValueError(f"patch.targets[{target_index}].operations 必须是数组")
        for operation_index, raw_operation in enumerate(raw_operations):
            label = f"patch.targets[{target_index}].operations[{operation_index}]"
            result.append(_parse_operation(target_file, raw_operation, label, len(result)))
    return result


def _parse_operation(
    target: str,
    raw: object,
    label: str,
    index: int,
) -> CdmodOperation:
    """解析单条操作并统一 set/list_union 的 payload 字段。"""
    if not isinstance(raw, dict):
        raise ValueError(f"{label} 必须是对象")
    selector = raw.get("selector")
    if not isinstance(selector, dict) or not selector:
        raise ValueError(f"{label}.selector 必须是非空对象")
    if not _has_stable_selector(selector):
        raise ValueError(f"{label}.selector 缺少 key/string_key/match")
    path = _require_non_empty_string(raw.get("path"), f"{label}.path")
    op = _require_non_empty_string(raw.get("op"), f"{label}.op")
    if op not in SUPPORTED_CDMOD_OPERATIONS:
        raise ValueError(f"{label}.op 暂不支持：{op}")
    payload_key = "values" if op == "list_union" else "value"
    if payload_key not in raw:
        raise ValueError(f"{label} 缺少 {payload_key}")
    payload = raw[payload_key]
    if op == "list_union" and not isinstance(payload, list):
        raise ValueError(f"{label}.values 必须是数组")
    return CdmodOperation(
        target=target,
        selector=selector,
        path=path,
        op=op,
        payload=payload,
        conversion=str(raw.get("conversion") or "native"),
        index=index,
    )


def _has_stable_selector(selector: dict[str, Any]) -> bool:
    """确认选择器至少提供一种可用于冲突分组的定位方式。"""
    key = selector.get("key")
    if isinstance(key, int) and not isinstance(key, bool) and key != 0:
        return True
    if isinstance(selector.get("string_key"), str) and selector["string_key"]:
        return True
    return isinstance(selector.get("match"), dict) and bool(selector["match"])


def _normalize_target(value: str) -> str:
    """统一目标路径分隔符和大小写，避免同文件被分成多个冲突域。"""
    return value.replace("\\", "/").strip("/").lower()


def _require_non_empty_string(value: object, label: str) -> str:
    """读取必需的非空字符串。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} 必须是非空字符串")
    return value.strip()
