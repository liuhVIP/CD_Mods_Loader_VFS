"""Crimson Mod Package 原型转换服务。

第一版只负责把 Format 3 语义补丁封装为可审计的 ``.cdmod`` 容器，
不接入现有 apply/VFS 链路。转换结果同时保留规范化操作和转换报告，供后续
实现加载器、冲突分析器及 Workbench 导出器时复用。
"""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from cdmm.services.format3_parser import Format3Intent, parse_format3_file

# 新容器格式名称，写入 manifest 供扫描器稳定识别。
CDMOD_FORMAT_NAME = "crimson-mod-package"

# 当前原型主版本；不兼容的容器结构变更必须提升该值。
CDMOD_FORMAT_VERSION = 1

# 语义操作文档版本，与 ZIP 容器版本分开演进。
CDMOD_PATCH_SCHEMA_VERSION = 1

# ZIP 内固定文件路径，避免不同导出器产生多套目录约定。
CDMOD_MANIFEST_PATH = "manifest.json"
CDMOD_PATCH_PATH = "patches/semantic.json"
CDMOD_REPORT_PATH = "reports/conversion.json"

# 本地化组件类型独立于表语义组件，允许后续按资源能力扩展容器。
CDMOD_LOCALIZATION_COMPONENT_TYPE = "localization-patch"

# 资源变换组件保存源目标映射或等长字节规则，不携带可从游戏重建的大文件。
CDMOD_RESOURCE_TRANSFORM_COMPONENT_TYPE = "resource-transform"

# 完整资源替换组件用于无法安全语义化或从游戏动态重建的二进制资源。
CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE = "file-replacement"

# 按最终游戏资源指纹选择预构建载荷，用于体型、骨架等离散兼容配置。
CDMOD_PROFILED_FILE_REPLACEMENT_COMPONENT_TYPE = "profiled-file-replacement"

# 传统 JSON byte patch 作为原语组件封装，运行时复用现有稳定补丁器。
CDMOD_LEGACY_JSON_COMPONENT_TYPE = "legacy-byte-patch"

# standalone 组件携带原始 PAZ/PAMT，由加载器统一分配目录和重建 PAPGT。
CDMOD_STANDALONE_COMPONENT_TYPE = "standalone-archive"

# ZIP 固定时间戳，保证相同输入生成字节一致的包，便于缓存和分发校验。
DETERMINISTIC_ZIP_TIMESTAMP = (2026, 1, 1, 0, 0, 0)

# EquipSlotInfo 的哈希集合字段适合加法合并，不应继续整数组覆盖。
EQUIPSLOT_HASH_FIELD_PATTERN = re.compile(r"^entries\[\d+]\.etl_hashes$")


@dataclass(frozen=True)
class CdmodConversionResult:
    """一次 Format 3 转换的结果摘要。"""

    output_path: Path
    source_sha256: str
    package_sha256: str
    target_count: int
    operation_count: int
    optimized_operation_count: int
    conservative_operation_count: int


def convert_format3_to_cdmod(
    source_path: Path, output_path: Path
) -> CdmodConversionResult:
    """将一个 Format 3 JSON 转换成确定性的 ``.cdmod`` ZIP。"""
    source_path = source_path.resolve()
    output_path = output_path.resolve()
    source_bytes = source_path.read_bytes()
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    source_document = _read_json_object(source_path)
    target_specs = parse_format3_file(source_path)

    targets: list[dict[str, Any]] = []
    optimized_count = 0
    conservative_count = 0
    for target_spec in target_specs:
        operations: list[dict[str, Any]] = []
        for intent in target_spec.intents:
            operation, optimized = convert_format3_intent(target_spec.target, intent)
            operations.append(operation)
            if optimized:
                optimized_count += 1
            else:
                conservative_count += 1
        targets.append({"file": target_spec.target, "operations": operations})

    metadata = _extract_metadata(source_document, source_path)
    patch_document = {
        "schema": CDMOD_PATCH_SCHEMA_VERSION,
        "targets": targets,
    }
    operation_count = optimized_count + conservative_count
    report_document = {
        "schema": 1,
        "source": {
            "name": source_path.name,
            "format": "format3",
            "sha256": source_sha256,
        },
        "summary": {
            "target_count": len(targets),
            "operation_count": operation_count,
            "optimized_operation_count": optimized_count,
            "conservative_operation_count": conservative_count,
        },
        "compatibility": {
            "equipslot_hashes": (
                "entries[N].etl_hashes 已转换为 list_union；该操作保留其他模组添加的哈希，"
                "属于兼容性优化，不承诺与旧 set 操作字节等价"
            ),
            "prefab_data_list": (
                "完整列表仍使用 set；真实样本存在重复 prefab_names，当前无法证明稳定元素主键，"
                "因此不做不安全的 list_merge 推断"
            ),
        },
    }
    manifest_document = {
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": _build_mod_id(metadata, source_path),
        "name": metadata["name"],
        "version": metadata["version"],
        "author": metadata["author"],
        "description": metadata["description"],
        "dependencies": metadata["dependencies"],
        "source": {"format": "format3", "sha256": source_sha256},
        "components": [
            {
                "type": "semantic-patch",
                "path": CDMOD_PATCH_PATH,
                "target_count": len(targets),
                "operation_count": operation_count,
            }
        ],
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_cdmod_zip(
        output_path,
        {
            CDMOD_MANIFEST_PATH: manifest_document,
            CDMOD_PATCH_PATH: patch_document,
            CDMOD_REPORT_PATH: report_document,
        },
    )
    package_sha256 = hashlib.sha256(output_path.read_bytes()).hexdigest()
    return CdmodConversionResult(
        output_path=output_path,
        source_sha256=source_sha256,
        package_sha256=package_sha256,
        target_count=len(targets),
        operation_count=operation_count,
        optimized_operation_count=optimized_count,
        conservative_operation_count=conservative_count,
    )


def result_to_json(result: CdmodConversionResult) -> dict[str, Any]:
    """把转换结果转换为可直接输出的 JSON 对象。"""
    value = asdict(result)
    value["output_path"] = str(result.output_path)
    return value


def convert_format3_intent(
    target: str, intent: Format3Intent
) -> tuple[dict[str, Any], bool]:
    """把单条 Format 3 intent 转换为新格式操作。"""
    selector: dict[str, Any] = {"key": intent.key}
    if intent.entry:
        selector["string_key"] = intent.entry
    if intent.match:
        selector["match"] = intent.match

    target_name = target.replace("\\", "/").rsplit("/", 1)[-1].lower()
    can_union = (
        target_name == "equipslotinfo.pabgb"
        and intent.op == "set"
        and EQUIPSLOT_HASH_FIELD_PATTERN.fullmatch(intent.field) is not None
        and _is_u32_list(intent.new)
    )
    if can_union:
        return (
            {
                "selector": selector,
                "path": intent.field,
                "op": "list_union",
                "values": _dedupe_preserving_order(intent.new),
                "conversion": "compatibility-optimized",
            },
            True,
        )

    operation = {
        "selector": selector,
        "path": intent.field,
        "op": intent.op,
        "value": intent.new,
        "conversion": "conservative",
    }
    if intent.old is not None:
        operation["expect"] = intent.old
    return operation, False


def _extract_metadata(document: dict[str, Any], source_path: Path) -> dict[str, Any]:
    """兼容 DMM modinfo 和 Workbench _meta 元数据。"""
    raw = document.get("modinfo") or document.get("_meta") or {}
    if not isinstance(raw, dict):
        raw = {}
    name = raw.get("title") or raw.get("name") or source_path.stem
    dependencies = raw.get("dependencies", [])
    if not isinstance(dependencies, list):
        dependencies = []
    return {
        "name": str(name),
        "version": str(raw.get("version") or "0.0.0"),
        "author": str(raw.get("author") or "unknown"),
        "description": str(raw.get("description") or ""),
        "dependencies": [str(value) for value in dependencies],
    }


def _build_mod_id(metadata: dict[str, Any], source_path: Path) -> str:
    """生成稳定、适合作为依赖标识的模组 ID。"""
    raw = f"{metadata['author']}.{metadata['name']}".lower()
    normalized = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    return normalized or re.sub(r"[^a-z0-9]+", "-", source_path.stem.lower()).strip("-")


def _read_json_object(path: Path) -> dict[str, Any]:
    """读取 UTF-8/UTF-8 BOM JSON 对象。"""
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("Format 3 根节点必须是 JSON 对象")
    return value


def _write_cdmod_zip(
    output_path: Path, documents: dict[str, dict[str, Any] | bytes]
) -> None:
    """按固定顺序、固定时间戳写入确定性 ZIP。"""
    with zipfile.ZipFile(
        output_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for archive_path in sorted(documents):
            document = documents[archive_path]
            payload = (
                document
                if isinstance(document, bytes)
                else json.dumps(
                    document,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ).encode("utf-8")
                + b"\n"
            )
            info = zipfile.ZipInfo(archive_path, DETERMINISTIC_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, payload, compresslevel=9)


def _is_u32_list(value: Any) -> bool:
    """判断值是否为合法的 u32 数组。"""
    return isinstance(value, list) and all(
        isinstance(item, int) and not isinstance(item, bool) and 0 <= item <= 0xFFFFFFFF
        for item in value
    )


def _dedupe_preserving_order(values: list[int]) -> list[int]:
    """按首次出现顺序去重，避免集合操作携带重复哈希。"""
    return list(dict.fromkeys(values))
