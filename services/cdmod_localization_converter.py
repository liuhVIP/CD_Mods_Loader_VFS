"""将完整 PALOC 替换文件转换为可更新适配的 ``.cdmod`` 差异组件。"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from cdmm.services.cdmod_converter import (
    CDMOD_FORMAT_NAME,
    CDMOD_FORMAT_VERSION,
    CDMOD_LOCALIZATION_COMPONENT_TYPE,
    CDMOD_MANIFEST_PATH,
    CDMOD_REPORT_PATH,
    _write_cdmod_zip,
)
from cdmm.services.paloc import parse_paloc, serialize_paloc

# 本地化差异文档使用独立 schema，后续可在不破坏表语义操作的情况下演进。
CDMOD_LOCALIZATION_SCHEMA = 1

# 容器内使用稳定路径；语言代码会先经过安全字符规范化。
CDMOD_LOCALIZATION_PATH_TEMPLATE = "patches/localization/{language}.json"

# 未筛选时差异过大通常意味着使用了旧版整表，必须拒绝静默转换。
DEFAULT_MAX_UNFILTERED_CHANGES = 1000


@dataclass(frozen=True)
class CdmodLocalizationConversionResult:
    """一次 PALOC 转换的可审计结果。"""

    output_path: Path
    package_sha256: str
    vanilla_sha256: str
    modified_sha256: str
    vanilla_record_count: int
    modified_record_count: int
    total_changed_count: int
    selected_change_count: int
    ignored_change_count: int
    vanilla_only_count: int
    modified_only_count: int


def convert_paloc_to_cdmod(
    modified_path: Path,
    vanilla_path: Path,
    output_path: Path,
    *,
    target: str | None = None,
    value_contains: str | None = None,
    name: str | None = None,
    version: str = "1.0.0",
    author: str = "unknown",
    description: str = "",
    max_unfiltered_changes: int = DEFAULT_MAX_UNFILTERED_CHANGES,
    vanilla_source_label: str | None = None,
    append_suffix: str | None = None,
    all_languages: bool = False,
) -> CdmodLocalizationConversionResult:
    """比较 PALOC 并输出确定性本地化差异包。

    ``value_contains`` 用于从旧版整表中提取可审计的功能性改动。未提供筛选时，
    若存在新增/缺失键或差异数量过大，会拒绝转换，避免把旧翻译覆盖到新游戏。
    """
    modified_path = modified_path.resolve()
    vanilla_path = vanilla_path.resolve()
    output_path = output_path.resolve()
    modified_bytes = modified_path.read_bytes()
    vanilla_bytes = vanilla_path.read_bytes()
    modified = parse_paloc(modified_bytes)
    vanilla = parse_paloc(vanilla_bytes)
    _require_roundtrip(modified_bytes, modified, "模组 PALOC")
    _require_roundtrip(vanilla_bytes, vanilla, "原版 PALOC")

    modified_by_key = modified.by_key()
    vanilla_by_key = vanilla.by_key()
    common_keys = modified_by_key.keys() & vanilla_by_key.keys()
    changed_keys = [
        record.key
        for record in modified.records
        if record.key in common_keys
        and record.value != vanilla_by_key[record.key].value
    ]
    modified_only = modified_by_key.keys() - vanilla_by_key.keys()
    vanilla_only = vanilla_by_key.keys() - modified_by_key.keys()
    if value_contains is None:
        if modified_only or vanilla_only:
            raise ValueError(
                "PALOC 键集合与当前原版不同，疑似跨游戏版本整表；"
                "请提供 value_contains 只提取可审计改动"
            )
        if len(changed_keys) > max_unfiltered_changes:
            raise ValueError(
                f"PALOC 检测到 {len(changed_keys)} 条差异，超过安全阈值 "
                f"{max_unfiltered_changes}；请提供 value_contains"
            )
        selected_keys = changed_keys
    else:
        if not value_contains:
            raise ValueError("value_contains 不能为空字符串")
        selected_keys = [
            key for key in changed_keys if value_contains in modified_by_key[key].value
        ]
    if not selected_keys:
        raise ValueError("筛选后没有可转换的 PALOC 文本差异")

    language = _infer_language(modified_path.name)
    normalized_target = _normalize_target(target or f"gamedata/{modified_path.name}")
    component_language = "*" if all_languages else language
    if all_languages:
        if append_suffix is None:
            raise ValueError("all_languages 只能与 append_suffix 一起使用")
        normalized_target = _all_language_target(normalized_target, language)
    component_path = CDMOD_LOCALIZATION_PATH_TEMPLATE.format(
        language="all" if all_languages else language
    )
    if append_suffix is not None:
        if not append_suffix:
            raise ValueError("append_suffix 不能为空字符串")
        selected_keys = [
            key
            for key in selected_keys
            if modified_by_key[key].value.endswith(append_suffix)
        ]
        if not selected_keys:
            raise ValueError("筛选后没有以 append_suffix 结尾的 PALOC 差异")
        changes = [
            {"key": key, "op": "append", "suffix": append_suffix}
            for key in selected_keys
        ]
    else:
        changes = [
            {
                "key": key,
                "expect": vanilla_by_key[key].value,
                "value": modified_by_key[key].value,
            }
            for key in selected_keys
        ]
    patch_document = {
        "schema": CDMOD_LOCALIZATION_SCHEMA,
        "target": normalized_target,
        "language": component_language,
        "changes": changes,
    }
    package_name = name or modified_path.stem
    vanilla_sha256 = hashlib.sha256(vanilla_bytes).hexdigest()
    modified_sha256 = hashlib.sha256(modified_bytes).hexdigest()
    manifest_document = {
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": _build_mod_id(author, package_name),
        "name": package_name,
        "version": version,
        "author": author,
        "description": description,
        "dependencies": [],
        "source": {
            "format": "paloc-full-replacement",
            "modified_sha256": modified_sha256,
            "vanilla_sha256": vanilla_sha256,
        },
        "components": [
            {
                "type": CDMOD_LOCALIZATION_COMPONENT_TYPE,
                "path": component_path,
                "target": normalized_target,
                "language": component_language,
                "change_count": len(changes),
            }
        ],
    }
    report_document = {
        "schema": 1,
        "source": {
            "modified": modified_path.name,
            "vanilla": vanilla_source_label or vanilla_path.name,
            "modified_sha256": modified_sha256,
            "vanilla_sha256": vanilla_sha256,
        },
        "selection": {
            "value_contains": value_contains,
            "total_changed_count": len(changed_keys),
            "selected_change_count": len(changes),
            "ignored_change_count": len(changed_keys) - len(changes),
            "vanilla_only_count": len(vanilla_only),
            "modified_only_count": len(modified_only),
        },
        "safety": {
            "mode": "filtered" if value_contains is not None else "complete-diff",
            "game_update_policy": "expect-mismatch-reject",
            "record_add_delete_supported": False,
            "operation": "append" if append_suffix is not None else "set",
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_cdmod_zip(
        output_path,
        {
            CDMOD_MANIFEST_PATH: manifest_document,
            component_path: patch_document,
            CDMOD_REPORT_PATH: report_document,
        },
    )
    return CdmodLocalizationConversionResult(
        output_path=output_path,
        package_sha256=hashlib.sha256(output_path.read_bytes()).hexdigest(),
        vanilla_sha256=vanilla_sha256,
        modified_sha256=modified_sha256,
        vanilla_record_count=len(vanilla.records),
        modified_record_count=len(modified.records),
        total_changed_count=len(changed_keys),
        selected_change_count=len(changes),
        ignored_change_count=len(changed_keys) - len(changes),
        vanilla_only_count=len(vanilla_only),
        modified_only_count=len(modified_only),
    )


def localization_result_to_json(result: CdmodLocalizationConversionResult) -> dict[str, object]:
    """把转换结果转换为 CLI 可序列化对象。"""
    payload = asdict(result)
    payload["output_path"] = str(result.output_path)
    return payload


def _require_roundtrip(source: bytes, document, label: str) -> None:
    """转换前必须证明 parser/writer 不改变未知结构。"""
    if serialize_paloc(document) != source:
        raise ValueError(f"{label} 无法无损 round-trip，拒绝转换")


def _infer_language(filename: str) -> str:
    """从 localizationstring_<language>.paloc 提取语言代码。"""
    match = re.fullmatch(r"localizationstring_([a-z0-9-]+)\.paloc", filename.lower())
    if match is None:
        raise ValueError(f"无法从 PALOC 文件名识别语言：{filename}")
    return match.group(1)


def _normalize_target(value: str) -> str:
    """规范化 PALOC 最终游戏路径。"""
    normalized = value.replace("\\", "/").strip("/").lower()
    if not normalized or not normalized.endswith(".paloc"):
        raise ValueError("PALOC target 必须是非空 .paloc 路径")
    return normalized


def _all_language_target(target: str, language: str) -> str:
    """把单语言 PALOC 路径转换为安全的语言通配目标。"""
    marker = f"_{language}.paloc"
    if not target.endswith(marker):
        raise ValueError(f"PALOC target 与语言代码不匹配：{target} / {language}")
    return target[: -len(marker)] + "_*.paloc"


def _build_mod_id(author: str, name: str) -> str:
    """生成稳定、适合作为依赖标识的模组 ID。"""
    normalized = re.sub(r"[^a-z0-9]+", "-", f"{author}.{name}".lower()).strip("-")
    return normalized or "paloc-localization-mod"
