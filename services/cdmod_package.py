"""Crimson Mod Package 读取与结构校验。

本模块只把 ``.cdmod`` 容器解析成稳定的数据模型，不负责决定加载顺序或
执行表写入。兼容分析器和未来 VFS 构建器必须共享这一入口。
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cdmm.services.cdmod_converter import (
    CDMOD_FORMAT_NAME,
    CDMOD_FORMAT_VERSION,
    CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE,
    CDMOD_LEGACY_JSON_COMPONENT_TYPE,
    CDMOD_LOCALIZATION_COMPONENT_TYPE,
    CDMOD_MANIFEST_PATH,
    CDMOD_PATCH_PATH,
    CDMOD_PROFILED_FILE_REPLACEMENT_COMPONENT_TYPE,
    CDMOD_RESOURCE_TRANSFORM_COMPONENT_TYPE,
    CDMOD_STANDALONE_COMPONENT_TYPE,
)

# 第一版允许的语义操作，未知操作必须显式拒绝，不能静默跳过。
SUPPORTED_CDMOD_OPERATIONS = frozenset({"set", "list_union"})

# 同一次 scan/apply 会在目标收集、JSON、语义、资源和 standalone 阶段重复读取包。
# 以文件状态为键缓存严格解析结果，文件变化后自然失效。
_CDMOD_PACKAGE_CACHE: dict[tuple[str, int, int], "CdmodPackage"] = {}


@dataclass(frozen=True)
class CdmodPrefabRiskOperation:
    """扫描阶段可从组件 JSON 读取的轻量 Prefab 操作证据。"""

    method: str
    target: str
    source: str | None = None
    payload_sha256: str | None = None


def validate_cdmod_header(path: Path) -> None:
    """轻量校验容器清单和组件索引，不在扫描阶段解压大型资源载荷。"""
    try:
        with zipfile.ZipFile(path) as archive:
            manifest = _read_zip_json(archive, CDMOD_MANIFEST_PATH)
            components = _validate_manifest(manifest)
            archive_names = set(archive.namelist())
            missing = [
                component["path"]
                for component in components
                if component["path"] not in archive_names
            ]
            if missing:
                raise ValueError(f"cdmod 缺少组件：{', '.join(missing)}")
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError(f"无法读取 cdmod：{exc}") from exc


def collect_cdmod_declared_targets(path: Path) -> list[str]:
    """只读组件JSON收集PAMT目标，大型payload延迟到实际构建阶段解压。"""
    targets: list[str] = []
    try:
        with zipfile.ZipFile(path) as archive:
            manifest = _read_zip_json(archive, CDMOD_MANIFEST_PATH)
            components = _validate_manifest(manifest)
            for component in components:
                component_type = component["type"]
                if component_type == CDMOD_STANDALONE_COMPONENT_TYPE:
                    continue
                document = _read_zip_json(archive, component["path"])
                targets.extend(
                    _declared_targets_from_component(component_type, document)
                )
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError(f"无法读取 cdmod：{exc}") from exc
    return list(dict.fromkeys(targets))


def collect_cdmod_prefab_risk_targets(path: Path) -> list[tuple[str, str]]:
    """轻量读取组件 JSON，返回直接修改 prefab 的操作，不解压资源载荷。"""
    return list(
        dict.fromkeys(
            (operation.method, operation.target)
            for operation in collect_cdmod_prefab_risk_operations(path)
        )
    )


def collect_cdmod_prefab_risk_operations(path: Path) -> list[CdmodPrefabRiskOperation]:
    """读取 Prefab 目标、复制来源和声明 SHA，不读取二进制载荷。"""
    operations: list[CdmodPrefabRiskOperation] = []
    try:
        with zipfile.ZipFile(path) as archive:
            manifest = _read_zip_json(archive, CDMOD_MANIFEST_PATH)
            components = _validate_manifest(manifest)
            for component in components:
                component_type = component["type"]
                if component_type not in {
                    CDMOD_RESOURCE_TRANSFORM_COMPONENT_TYPE,
                    CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE,
                    CDMOD_PROFILED_FILE_REPLACEMENT_COMPONENT_TYPE,
                    CDMOD_LEGACY_JSON_COMPONENT_TYPE,
                }:
                    continue
                document = _read_zip_json(archive, component["path"])
                if component_type == CDMOD_RESOURCE_TRANSFORM_COMPONENT_TYPE:
                    for operation in document.get("operations") or []:
                        if not isinstance(operation, dict):
                            continue
                        target = operation.get("target")
                        op = operation.get("op")
                        if isinstance(target, str) and _is_prefab_target(target):
                            method = (
                                f"resource-transform {op}"
                                if isinstance(op, str)
                                else "resource-transform"
                            )
                            source = operation.get("source")
                            operations.append(
                                CdmodPrefabRiskOperation(
                                    method=method,
                                    target=target,
                                    source=source if isinstance(source, str) else None,
                                )
                            )
                    continue
                if component_type == CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE:
                    for file_item in document.get("files") or []:
                        if not isinstance(file_item, dict):
                            continue
                        target = file_item.get("target")
                        if isinstance(target, str) and _is_prefab_target(target):
                            payload_sha256 = file_item.get("sha256")
                            operations.append(
                                CdmodPrefabRiskOperation(
                                    method="file-replacement",
                                    target=target,
                                    payload_sha256=(
                                        payload_sha256.casefold()
                                        if isinstance(payload_sha256, str)
                                        else None
                                    ),
                                )
                            )
                    continue
                if component_type == CDMOD_PROFILED_FILE_REPLACEMENT_COMPONENT_TYPE:
                    for file_item in document.get("files") or []:
                        if not isinstance(file_item, dict):
                            continue
                        target = file_item.get("target")
                        if isinstance(target, str) and _is_prefab_target(target):
                            operations.append(
                                CdmodPrefabRiskOperation(
                                    method="profiled-file-replacement",
                                    target=target,
                                )
                            )
                    continue
                for patch in document.get("patches") or []:
                    if not isinstance(patch, dict):
                        continue
                    target = patch.get("game_file")
                    if isinstance(target, str) and _is_prefab_target(target):
                        operations.append(
                            CdmodPrefabRiskOperation("cdmod legacy-json", target)
                        )
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError(f"无法读取 cdmod：{exc}") from exc
    return list(dict.fromkeys(operations))


def _is_prefab_target(target: str) -> bool:
    """判断 cdmod 声明路径是否为 prefab。"""
    return target.replace("\\", "/").casefold().endswith(".prefab")


def _declared_targets_from_component(
    component_type: str, document: dict[str, Any]
) -> list[str]:
    """从轻量组件文档提取游戏目标路径。"""
    if component_type == "semantic-patch":
        return [
            item["file"]
            for item in document.get("targets") or []
            if isinstance(item, dict) and isinstance(item.get("file"), str)
        ]
    if component_type == CDMOD_LEGACY_JSON_COMPONENT_TYPE:
        patches = document.get("patches")
        return [
            item["game_file"]
            for item in patches or []
            if isinstance(item, dict) and isinstance(item.get("game_file"), str)
        ]
    if component_type == CDMOD_LOCALIZATION_COMPONENT_TYPE:
        target = document.get("target")
        return [target] if isinstance(target, str) else []
    if component_type == CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE:
        files = document.get("files")
        return [
            item["target"]
            for item in files or []
            if isinstance(item, dict) and isinstance(item.get("target"), str)
        ]
    if component_type == CDMOD_PROFILED_FILE_REPLACEMENT_COMPONENT_TYPE:
        result: list[str] = []
        probe = document.get("probe")
        if isinstance(probe, dict) and isinstance(probe.get("target"), str):
            result.append(probe["target"])
        result.extend(
            item["target"]
            for item in document.get("files") or []
            if isinstance(item, dict) and isinstance(item.get("target"), str)
        )
        return result
    if component_type == CDMOD_RESOURCE_TRANSFORM_COMPONENT_TYPE:
        result: list[str] = []
        for item in document.get("operations") or []:
            if not isinstance(item, dict):
                continue
            for key in ("target", "source"):
                value = item.get(key)
                if isinstance(value, str):
                    result.append(value)
        return result
    return []


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
class CdmodLocalizationChange:
    """一条带游戏更新前置条件的本地化文本修改。"""

    key: str
    value: str
    expect: str
    index: int
    op: str = "set"
    suffix: str | None = None


@dataclass(frozen=True)
class CdmodLocalizationPatch:
    """一个 PALOC 目标的有序本地化修改集合。"""

    target: str
    language: str
    changes: tuple[CdmodLocalizationChange, ...]


@dataclass(frozen=True)
class CdmodByteReplacement:
    """一条必须保持长度不变的资源字节替换规则。"""

    old: bytes
    new: bytes


@dataclass(frozen=True)
class CdmodResourceTransform:
    """一个从当前游戏资源动态生成目标 entry 的操作。"""

    op: str
    target: str
    target_pamt_dir: str
    index: int
    source: str | None = None
    source_pamt_dir: str | None = None
    replacements: tuple[CdmodByteReplacement, ...] = ()


@dataclass(frozen=True)
class CdmodResourcePatch:
    """一个有序资源变换组件。"""

    operations: tuple[CdmodResourceTransform, ...]


@dataclass(frozen=True)
class CdmodFileReplacement:
    """一个经过哈希校验的完整资源载荷。"""

    target: str
    pamt_dir: str
    payload_path: str
    sha256: str
    content: bytes
    index: int
    allow_new: bool = False
    allow_table_replace: bool = False


@dataclass(frozen=True)
class CdmodFilePatch:
    """一个完整资源替换组件。"""

    files: tuple[CdmodFileReplacement, ...]


@dataclass(frozen=True)
class CdmodProfileDefinition:
    """一个由探针资源 SHA-256 唯一识别的兼容配置。"""

    profile_id: str
    probe_sha256: str


@dataclass(frozen=True)
class CdmodProfiledFileVariant:
    """一个兼容配置对应的完整资源载荷。"""

    profile_id: str
    payload_path: str
    sha256: str
    content: bytes


@dataclass(frozen=True)
class CdmodProfiledFileReplacement:
    """一个根据探针结果选择载荷的游戏资源目标。"""

    target: str
    pamt_dir: str
    variants: tuple[CdmodProfiledFileVariant, ...]
    fallback: CdmodProfiledFileVariant
    index: int


@dataclass(frozen=True)
class CdmodProfiledFilePatch:
    """共享一个探针资源的条件完整替换组件。"""

    probe_target: str
    probe_pamt_dir: str
    profiles: tuple[CdmodProfileDefinition, ...]
    files: tuple[CdmodProfiledFileReplacement, ...]


@dataclass(frozen=True)
class CdmodStandaloneArchive:
    """一个经过哈希校验的 standalone PAZ/PAMT 载荷。"""

    name: str
    paz_bytes: bytes
    pamt_bytes: bytes


@dataclass(frozen=True)
class CdmodPackage:
    """一个已读取的 cdmod 包。"""

    path: Path
    mod_id: str
    name: str
    version: str
    dependencies: tuple[str, ...]
    operations: tuple[CdmodOperation, ...]
    localization_patches: tuple[CdmodLocalizationPatch, ...] = ()
    resource_patches: tuple[CdmodResourcePatch, ...] = ()
    file_patches: tuple[CdmodFilePatch, ...] = ()
    legacy_json_patches: tuple[dict[str, Any], ...] = ()
    standalone_archives: tuple[CdmodStandaloneArchive, ...] = ()
    profiled_file_patches: tuple[CdmodProfiledFilePatch, ...] = ()


def load_cdmod_package(path: Path) -> CdmodPackage:
    """读取并严格校验一个 ``.cdmod`` 文件。"""
    path = path.resolve()
    stat = path.stat()
    cache_key = (str(path), stat.st_mtime_ns, stat.st_size)
    cached = _CDMOD_PACKAGE_CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        with zipfile.ZipFile(path) as archive:
            manifest = _read_zip_json(archive, CDMOD_MANIFEST_PATH)
            component_specs = _validate_manifest(manifest)
            semantic_paths = [
                component["path"]
                for component in component_specs
                if component["type"] == "semantic-patch"
            ]
            if len(semantic_paths) > 1:
                raise ValueError("cdmod 只能声明一个 semantic-patch 组件")
            operations = (
                _parse_operations(_read_zip_json(archive, semantic_paths[0]))
                if semantic_paths
                else []
            )
            localization_patches = _read_localization_components(
                archive, component_specs
            )
            resource_patches = _read_resource_components(archive, component_specs)
            file_patches = _read_file_components(archive, component_specs)
            profiled_file_patches = _read_profiled_file_components(
                archive, component_specs
            )
            legacy_json_patches = _read_legacy_json_components(archive, component_specs)
            standalone_archives = _read_standalone_components(archive, component_specs)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError(f"无法读取 cdmod：{exc}") from exc

    dependencies = manifest.get("dependencies", [])
    if not isinstance(dependencies, list) or not all(
        isinstance(item, str) for item in dependencies
    ):
        raise ValueError("manifest.dependencies 必须是字符串数组")
    package = CdmodPackage(
        path=path,
        mod_id=_require_non_empty_string(manifest.get("id"), "manifest.id"),
        name=_require_non_empty_string(manifest.get("name"), "manifest.name"),
        version=_require_non_empty_string(manifest.get("version"), "manifest.version"),
        dependencies=tuple(dependencies),
        operations=tuple(operations),
        localization_patches=tuple(localization_patches),
        resource_patches=tuple(resource_patches),
        file_patches=tuple(file_patches),
        legacy_json_patches=tuple(legacy_json_patches),
        standalone_archives=tuple(standalone_archives),
        profiled_file_patches=tuple(profiled_file_patches),
    )
    _CDMOD_PACKAGE_CACHE[cache_key] = package
    return package


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


def _validate_manifest(manifest: dict[str, Any]) -> list[dict[str, str]]:
    """校验容器格式和当前支持的主版本。"""
    if manifest.get("format") != CDMOD_FORMAT_NAME:
        raise ValueError("manifest.format 不是 crimson-mod-package")
    if manifest.get("format_version") != CDMOD_FORMAT_VERSION:
        raise ValueError(f"当前仅支持 cdmod format_version={CDMOD_FORMAT_VERSION}")
    raw_components = manifest.get("components")
    # 早期 v1 原型包未写 components，约定其语义文档位于固定标准路径。
    if raw_components is None:
        return [{"type": "semantic-patch", "path": CDMOD_PATCH_PATH}]
    if not isinstance(raw_components, list) or not raw_components:
        raise ValueError("manifest.components 必须是非空数组")
    components: list[dict[str, str]] = []
    supported_types = {
        "semantic-patch",
        CDMOD_LOCALIZATION_COMPONENT_TYPE,
        CDMOD_RESOURCE_TRANSFORM_COMPONENT_TYPE,
        CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE,
        CDMOD_PROFILED_FILE_REPLACEMENT_COMPONENT_TYPE,
        CDMOD_LEGACY_JSON_COMPONENT_TYPE,
        CDMOD_STANDALONE_COMPONENT_TYPE,
    }
    seen_paths: set[str] = set()
    for index, raw_component in enumerate(raw_components):
        label = f"manifest.components[{index}]"
        if not isinstance(raw_component, dict):
            raise ValueError(f"{label} 必须是对象")
        component_type = _require_non_empty_string(
            raw_component.get("type"), f"{label}.type"
        )
        if component_type not in supported_types:
            raise ValueError(f"{label}.type 暂不支持：{component_type}")
        component_path = _normalize_archive_path(
            _require_non_empty_string(raw_component.get("path"), f"{label}.path")
        )
        if component_path in seen_paths:
            raise ValueError(f"manifest.components 存在重复 path：{component_path}")
        seen_paths.add(component_path)
        components.append({"type": component_type, "path": component_path})
    return components


def _read_legacy_json_components(
    archive: zipfile.ZipFile,
    components: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """读取传统 JSON byte patch，并校验其至少包含一个补丁块。"""
    patches: list[dict[str, Any]] = []
    for component in components:
        if component["type"] != CDMOD_LEGACY_JSON_COMPONENT_TYPE:
            continue
        document = _read_zip_json(archive, component["path"])
        raw_patches = document.get("patches")
        if not isinstance(raw_patches, list) or not raw_patches:
            raise ValueError(f"{component['path']}.patches 必须是非空数组")
        patches.append(document)
    return patches


def _read_standalone_components(
    archive: zipfile.ZipFile,
    components: list[dict[str, str]],
) -> list[CdmodStandaloneArchive]:
    """读取并校验 standalone PAZ/PAMT 载荷。"""
    result: list[CdmodStandaloneArchive] = []
    for component in components:
        if component["type"] != CDMOD_STANDALONE_COMPONENT_TYPE:
            continue
        document = _read_zip_json(archive, component["path"])
        name = _require_non_empty_string(
            document.get("name"), f"{component['path']}.name"
        )
        paz_path = _normalize_archive_path(
            _require_non_empty_string(document.get("paz"), f"{component['path']}.paz")
        )
        pamt_path = _normalize_archive_path(
            _require_non_empty_string(document.get("pamt"), f"{component['path']}.pamt")
        )
        try:
            paz_bytes = archive.read(paz_path)
            pamt_bytes = archive.read(pamt_path)
        except KeyError as exc:
            raise ValueError(f"standalone 载荷缺失：{exc}") from exc
        for label, content in (("paz", paz_bytes), ("pamt", pamt_bytes)):
            expected = _require_sha256(
                document.get(f"{label}_sha256"), f"{component['path']}.{label}_sha256"
            )
            if hashlib.sha256(content).hexdigest() != expected:
                raise ValueError(f"standalone {label.upper()} SHA256 不匹配")
        result.append(CdmodStandaloneArchive(name, paz_bytes, pamt_bytes))
    return result


def _read_localization_components(
    archive: zipfile.ZipFile,
    components: list[dict[str, str]],
) -> list[CdmodLocalizationPatch]:
    """读取并严格校验所有本地化组件。"""
    patches: list[CdmodLocalizationPatch] = []
    for component in components:
        if component["type"] != CDMOD_LOCALIZATION_COMPONENT_TYPE:
            continue
        document = _read_zip_json(archive, component["path"])
        patches.append(_parse_localization_patch(document, component["path"]))
    return patches


def _parse_localization_patch(
    document: dict[str, Any],
    archive_path: str,
) -> CdmodLocalizationPatch:
    """解析一个 schema=1 的 PALOC 文本差异文档。"""
    if document.get("schema") != 1:
        raise ValueError(f"{archive_path} 当前仅支持 schema=1")
    target = _normalize_target(
        _require_non_empty_string(document.get("target"), f"{archive_path}.target")
    )
    if not target.endswith(".paloc"):
        raise ValueError(f"{archive_path}.target 必须是 .paloc 文件或安全通配模式")
    language = _require_non_empty_string(
        document.get("language"), f"{archive_path}.language"
    )
    raw_changes = document.get("changes")
    if not isinstance(raw_changes, list) or not raw_changes:
        raise ValueError(f"{archive_path}.changes 必须是非空数组")
    changes: list[CdmodLocalizationChange] = []
    seen_keys: set[str] = set()
    for index, raw_change in enumerate(raw_changes):
        label = f"{archive_path}.changes[{index}]"
        if not isinstance(raw_change, dict):
            raise ValueError(f"{label} 必须是对象")
        key = _require_non_empty_string(raw_change.get("key"), f"{label}.key")
        if key in seen_keys:
            raise ValueError(f"{archive_path} 存在重复本地化 key：{key}")
        seen_keys.add(key)
        op = str(raw_change.get("op") or "set")
        if op == "set":
            value = raw_change.get("value")
            expect = raw_change.get("expect")
            if not isinstance(value, str):
                raise ValueError(f"{label}.value 必须是字符串")
            if not isinstance(expect, str):
                raise ValueError(f"{label}.expect 必须是字符串")
            changes.append(CdmodLocalizationChange(key, value, expect, index))
            continue
        if op == "append":
            suffix = raw_change.get("suffix")
            if not isinstance(suffix, str) or not suffix:
                raise ValueError(f"{label}.suffix 必须是非空字符串")
            changes.append(CdmodLocalizationChange(key, "", "", index, op, suffix))
            continue
        raise ValueError(f"{label}.op 暂不支持：{op}")
    return CdmodLocalizationPatch(target, language, tuple(changes))


def _read_resource_components(
    archive: zipfile.ZipFile,
    components: list[dict[str, str]],
) -> list[CdmodResourcePatch]:
    """读取并严格校验所有资源变换组件。"""
    patches: list[CdmodResourcePatch] = []
    for component in components:
        if component["type"] != CDMOD_RESOURCE_TRANSFORM_COMPONENT_TYPE:
            continue
        document = _read_zip_json(archive, component["path"])
        patches.append(_parse_resource_patch(document, component["path"]))
    return patches


def _parse_resource_patch(
    document: dict[str, Any], archive_path: str
) -> CdmodResourcePatch:
    """解析 copy-entry 与 replace-bytes 资源变换。"""
    if document.get("schema") != 1:
        raise ValueError(f"{archive_path} 当前仅支持 schema=1")
    raw_operations = document.get("operations")
    if not isinstance(raw_operations, list) or not raw_operations:
        raise ValueError(f"{archive_path}.operations 必须是非空数组")
    operations: list[CdmodResourceTransform] = []
    seen_targets: set[tuple[str, str]] = set()
    for index, raw_operation in enumerate(raw_operations):
        label = f"{archive_path}.operations[{index}]"
        if not isinstance(raw_operation, dict):
            raise ValueError(f"{label} 必须是对象")
        op = _require_non_empty_string(raw_operation.get("op"), f"{label}.op")
        target = _normalize_target(
            _require_non_empty_string(raw_operation.get("target"), f"{label}.target")
        )
        target_pamt_dir = _parse_pamt_dir(
            raw_operation.get("target_pamt_dir"), f"{label}.target_pamt_dir"
        )
        target_identity = (target_pamt_dir, target)
        if target_identity in seen_targets:
            raise ValueError(
                f"{archive_path} 存在重复资源目标：{target_pamt_dir}/{target}"
            )
        seen_targets.add(target_identity)
        if op == "copy-entry":
            source = _normalize_target(
                _require_non_empty_string(
                    raw_operation.get("source"), f"{label}.source"
                )
            )
            source_pamt_dir = _parse_pamt_dir(
                raw_operation.get("source_pamt_dir"),
                f"{label}.source_pamt_dir",
            )
            operations.append(
                CdmodResourceTransform(
                    op,
                    target,
                    target_pamt_dir,
                    index,
                    source,
                    source_pamt_dir,
                )
            )
            continue
        if op == "replace-bytes":
            replacements = _parse_byte_replacements(
                raw_operation.get("replacements"), label
            )
            operations.append(
                CdmodResourceTransform(
                    op,
                    target,
                    target_pamt_dir,
                    index,
                    replacements=replacements,
                )
            )
            continue
        raise ValueError(f"{label}.op 暂不支持：{op}")
    return CdmodResourcePatch(tuple(operations))


def _read_file_components(
    archive: zipfile.ZipFile,
    components: list[dict[str, str]],
) -> list[CdmodFilePatch]:
    """读取完整资源替换组件及其二进制载荷。"""
    patches: list[CdmodFilePatch] = []
    for component in components:
        if component["type"] != CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE:
            continue
        document = _read_zip_json(archive, component["path"])
        patches.append(_parse_file_patch(archive, document, component["path"]))
    return patches


def _parse_file_patch(
    archive: zipfile.ZipFile,
    document: dict[str, Any],
    archive_path: str,
) -> CdmodFilePatch:
    """解析并校验 file-replacement schema=1。"""
    if document.get("schema") != 1:
        raise ValueError(f"{archive_path} 当前仅支持 schema=1")
    raw_files = document.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise ValueError(f"{archive_path}.files 必须是非空数组")
    files: list[CdmodFileReplacement] = []
    seen_targets: set[tuple[str, str]] = set()
    for index, raw_file in enumerate(raw_files):
        label = f"{archive_path}.files[{index}]"
        if not isinstance(raw_file, dict):
            raise ValueError(f"{label} 必须是对象")
        target = _normalize_target(
            _require_non_empty_string(raw_file.get("target"), f"{label}.target")
        )
        pamt_dir = _parse_pamt_dir(raw_file.get("pamt_dir"), f"{label}.pamt_dir")
        identity = (pamt_dir, target)
        if identity in seen_targets:
            raise ValueError(f"{archive_path} 存在重复资源目标：{pamt_dir}/{target}")
        seen_targets.add(identity)
        payload_path = _normalize_archive_path(
            _require_non_empty_string(raw_file.get("payload"), f"{label}.payload")
        )
        expected_sha256 = _require_sha256(raw_file.get("sha256"), f"{label}.sha256")
        try:
            content = archive.read(payload_path)
        except KeyError as exc:
            raise ValueError(f"cdmod 缺少资源载荷 {payload_path}") from exc
        actual_sha256 = hashlib.sha256(content).hexdigest()
        if actual_sha256 != expected_sha256:
            raise ValueError(f"{payload_path} SHA256 不匹配")
        files.append(
            CdmodFileReplacement(
                target,
                pamt_dir,
                payload_path,
                expected_sha256,
                content,
                index,
                bool(raw_file.get("allow_new", False)),
                bool(raw_file.get("allow_table_replace", False)),
            )
        )
    return CdmodFilePatch(tuple(files))


def _read_profiled_file_components(
    archive: zipfile.ZipFile,
    components: list[dict[str, str]],
) -> list[CdmodProfiledFilePatch]:
    """读取按探针资源指纹选择载荷的完整资源组件。"""
    patches: list[CdmodProfiledFilePatch] = []
    for component in components:
        if component["type"] != CDMOD_PROFILED_FILE_REPLACEMENT_COMPONENT_TYPE:
            continue
        document = _read_zip_json(archive, component["path"])
        patches.append(_parse_profiled_file_patch(archive, document, component["path"]))
    return patches


def _parse_profiled_file_patch(
    archive: zipfile.ZipFile,
    document: dict[str, Any],
    archive_path: str,
) -> CdmodProfiledFilePatch:
    """严格解析 profiled-file-replacement schema=1。"""
    if document.get("schema") != 1:
        raise ValueError(f"{archive_path} 当前仅支持 schema=1")
    probe = document.get("probe")
    if not isinstance(probe, dict):
        raise ValueError(f"{archive_path}.probe 必须是对象")
    probe_target = _normalize_target(
        _require_non_empty_string(probe.get("target"), f"{archive_path}.probe.target")
    )
    probe_pamt_dir = _parse_pamt_dir(
        probe.get("pamt_dir"), f"{archive_path}.probe.pamt_dir"
    )

    raw_profiles = document.get("profiles")
    if not isinstance(raw_profiles, list) or not raw_profiles:
        raise ValueError(f"{archive_path}.profiles 必须是非空数组")
    profiles: list[CdmodProfileDefinition] = []
    profile_ids: set[str] = set()
    probe_hashes: set[str] = set()
    for index, raw_profile in enumerate(raw_profiles):
        label = f"{archive_path}.profiles[{index}]"
        if not isinstance(raw_profile, dict):
            raise ValueError(f"{label} 必须是对象")
        profile_id = _require_non_empty_string(raw_profile.get("id"), f"{label}.id")
        probe_sha256 = _require_sha256(
            raw_profile.get("probe_sha256"), f"{label}.probe_sha256"
        )
        if profile_id in profile_ids:
            raise ValueError(f"{archive_path} 存在重复 profile id：{profile_id}")
        if probe_sha256 in probe_hashes:
            raise ValueError(f"{archive_path} 存在重复探针 SHA256：{probe_sha256}")
        profile_ids.add(profile_id)
        probe_hashes.add(probe_sha256)
        profiles.append(CdmodProfileDefinition(profile_id, probe_sha256))

    raw_files = document.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise ValueError(f"{archive_path}.files 必须是非空数组")
    files: list[CdmodProfiledFileReplacement] = []
    seen_targets: set[tuple[str, str]] = set()
    fallback_profile_ids: set[str] = set()
    for index, raw_file in enumerate(raw_files):
        label = f"{archive_path}.files[{index}]"
        if not isinstance(raw_file, dict):
            raise ValueError(f"{label} 必须是对象")
        target = _normalize_target(
            _require_non_empty_string(raw_file.get("target"), f"{label}.target")
        )
        pamt_dir = _parse_pamt_dir(raw_file.get("pamt_dir"), f"{label}.pamt_dir")
        identity = (pamt_dir, target)
        if identity in seen_targets:
            raise ValueError(
                f"{archive_path} 存在重复条件资源目标：{pamt_dir}/{target}"
            )
        seen_targets.add(identity)
        raw_variants = raw_file.get("variants")
        if not isinstance(raw_variants, list) or not raw_variants:
            raise ValueError(f"{label}.variants 必须是非空数组")
        variants = tuple(
            _parse_profiled_variant(archive, item, f"{label}.variants[{variant_index}]")
            for variant_index, item in enumerate(raw_variants)
        )
        variant_ids = [variant.profile_id for variant in variants]
        if len(set(variant_ids)) != len(variant_ids):
            raise ValueError(f"{label}.variants 存在重复 profile")
        if set(variant_ids) != profile_ids:
            raise ValueError(
                f"{label}.variants 必须完整覆盖 profiles："
                f"expected={sorted(profile_ids)} actual={sorted(variant_ids)}"
            )
        fallback = _parse_profiled_variant(
            archive, raw_file.get("fallback"), f"{label}.fallback"
        )
        if fallback.profile_id in profile_ids:
            raise ValueError(f"{label}.fallback.profile 不得与已知 profile 重复")
        fallback_profile_ids.add(fallback.profile_id)
        files.append(
            CdmodProfiledFileReplacement(
                target,
                pamt_dir,
                variants,
                fallback,
                index,
            )
        )
    if len(fallback_profile_ids) != 1:
        raise ValueError(f"{archive_path} 的所有 fallback.profile 必须一致")
    return CdmodProfiledFilePatch(
        probe_target,
        probe_pamt_dir,
        tuple(profiles),
        tuple(files),
    )


def _parse_profiled_variant(
    archive: zipfile.ZipFile,
    raw_variant: object,
    label: str,
) -> CdmodProfiledFileVariant:
    """读取一个已预构建且带载荷哈希的体型资源变体。"""
    if not isinstance(raw_variant, dict):
        raise ValueError(f"{label} 必须是对象")
    profile_id = _require_non_empty_string(
        raw_variant.get("profile"), f"{label}.profile"
    )
    payload_path = _normalize_archive_path(
        _require_non_empty_string(raw_variant.get("payload"), f"{label}.payload")
    )
    expected_sha256 = _require_sha256(raw_variant.get("sha256"), f"{label}.sha256")
    try:
        content = archive.read(payload_path)
    except KeyError as exc:
        raise ValueError(f"cdmod 缺少条件资源载荷 {payload_path}") from exc
    if hashlib.sha256(content).hexdigest() != expected_sha256:
        raise ValueError(f"{payload_path} SHA256 不匹配")
    return CdmodProfiledFileVariant(
        profile_id,
        payload_path,
        expected_sha256,
        content,
    )


def _parse_byte_replacements(
    raw: object, label: str
) -> tuple[CdmodByteReplacement, ...]:
    """读取等长十六进制替换列表。"""
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{label}.replacements 必须是非空数组")
    replacements: list[CdmodByteReplacement] = []
    for index, item in enumerate(raw):
        item_label = f"{label}.replacements[{index}]"
        if not isinstance(item, dict):
            raise ValueError(f"{item_label} 必须是对象")
        try:
            old = bytes.fromhex(
                _require_non_empty_string(item.get("old_hex"), f"{item_label}.old_hex")
            )
            new = bytes.fromhex(
                _require_non_empty_string(item.get("new_hex"), f"{item_label}.new_hex")
            )
        except ValueError as exc:
            raise ValueError(f"{item_label} 包含非法十六进制：{exc}") from exc
        if len(old) != len(new):
            raise ValueError(f"{item_label} 必须等长替换：{len(old)} != {len(new)}")
        replacements.append(CdmodByteReplacement(old, new))
    return tuple(replacements)


def _parse_pamt_dir(value: object, label: str) -> str:
    """资源变换当前只允许明确的四位 vanilla PAMT 目录。"""
    result = _require_non_empty_string(value, label)
    if len(result) != 4 or not result.isdigit():
        raise ValueError(f"{label} 必须是四位数字目录")
    return result


def _require_sha256(value: object, label: str) -> str:
    """校验小写十六进制 SHA-256。"""
    result = _require_non_empty_string(value, label).lower()
    if len(result) != 64 or any(char not in "0123456789abcdef" for char in result):
        raise ValueError(f"{label} 必须是64位SHA256")
    return result


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
            _require_non_empty_string(
                target.get("file"), f"patch.targets[{target_index}].file"
            )
        )
        raw_operations = target.get("operations")
        if not isinstance(raw_operations, list):
            raise ValueError(f"patch.targets[{target_index}].operations 必须是数组")
        for operation_index, raw_operation in enumerate(raw_operations):
            label = f"patch.targets[{target_index}].operations[{operation_index}]"
            result.append(
                _parse_operation(target_file, raw_operation, label, len(result))
            )
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


def _normalize_archive_path(value: str) -> str:
    """校验组件路径位于 ZIP 内部且不包含目录穿越。"""
    normalized = value.replace("\\", "/").strip("/")
    parts = normalized.split("/")
    if not normalized or any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"非法 cdmod 组件路径：{value}")
    return normalized


def _require_non_empty_string(value: object, label: str) -> str:
    """读取必需的非空字符串。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} 必须是非空字符串")
    return value.strip()
