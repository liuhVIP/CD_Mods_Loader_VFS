"""把标准 numbered loose 资源目录转换为通用 ``file-replacement`` cdmod。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from cdmm.services.cdmod_converter import (
    CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE,
    CDMOD_FORMAT_NAME,
    CDMOD_FORMAT_VERSION,
    CDMOD_MANIFEST_PATH,
    CDMOD_REPORT_PATH,
    _write_cdmod_zip,
)

# 完整资源组件的固定文档路径。
CDMOD_FILE_REPLACEMENT_PATH = "files/replacements.json"


@dataclass(frozen=True)
class LooseCdmodConversionResult:
    """numbered loose 转换摘要。"""

    output_path: Path
    package_sha256: str
    file_count: int
    payload_bytes: int


def convert_numbered_loose_to_cdmod(
    source_dir: Path,
    output_path: Path,
) -> LooseCdmodConversionResult:
    """转换 ``files/NNNN/...``，其他结构必须由后续适配器明确处理。"""
    source_dir = source_dir.resolve()
    output_path = output_path.resolve()
    files_root = source_dir / "files"
    if not files_root.is_dir():
        raise ValueError("模组缺少 files 目录")
    manifest = _read_optional_json(source_dir / "manifest.json")
    file_specs: list[dict[str, object]] = []
    documents: dict[str, dict[str, object] | bytes] = {}
    payload_bytes = 0
    for pamt_dir_path in sorted(path for path in files_root.iterdir() if path.is_dir()):
        pamt_dir = pamt_dir_path.name
        if len(pamt_dir) != 4 or not pamt_dir.isdigit():
            raise ValueError(f"当前转换器只支持 files/NNNN：{pamt_dir}")
        for file_path in sorted(path for path in pamt_dir_path.rglob("*") if path.is_file()):
            target = file_path.relative_to(pamt_dir_path).as_posix().lower()
            content = file_path.read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            payload_path = f"assets/{pamt_dir}/{target}"
            file_specs.append(
                {
                    "target": target,
                    "pamt_dir": pamt_dir,
                    "payload": payload_path,
                    "sha256": digest,
                    "size": len(content),
                }
            )
            documents[payload_path] = content
            payload_bytes += len(content)
    if not file_specs:
        raise ValueError("没有发现 numbered loose 文件")

    title = str(manifest.get("title") or source_dir.name)
    author = str(manifest.get("author") or "unknown")
    version = str(manifest.get("version") or "0.0.0")
    replacement_document = {"schema": 1, "files": file_specs}
    manifest_document = {
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": _mod_id(str(manifest.get("id") or f"{author}.{title}")),
        "name": title,
        "version": version,
        "author": author,
        "description": str(manifest.get("description") or ""),
        "dependencies": [],
        "source": {"format": "numbered-loose", "payload_bytes": payload_bytes},
        "components": [
            {
                "type": CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE,
                "path": CDMOD_FILE_REPLACEMENT_PATH,
                "file_count": len(file_specs),
            }
        ],
    }
    report_document = {
        "schema": 1,
        "source": {"name": source_dir.name, "format": "files/NNNN"},
        "summary": {"file_count": len(file_specs), "payload_bytes": payload_bytes},
        "safety": {
            "payload_sha256_required": True,
            "target_must_exist_in_current_pamt": True,
            "dds_pathc": "rebuilt-by-loader",
        },
    }
    documents.update(
        {
            CDMOD_MANIFEST_PATH: manifest_document,
            CDMOD_FILE_REPLACEMENT_PATH: replacement_document,
            CDMOD_REPORT_PATH: report_document,
        }
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_cdmod_zip(output_path, documents)
    return LooseCdmodConversionResult(
        output_path,
        hashlib.sha256(output_path.read_bytes()).hexdigest(),
        len(file_specs),
        payload_bytes,
    )


def loose_result_to_json(result: LooseCdmodConversionResult) -> dict[str, object]:
    """把转换结果转换为 CLI 可序列化对象。"""
    payload = asdict(result)
    payload["output_path"] = str(result.output_path)
    return payload


def _read_optional_json(path: Path) -> dict[str, object]:
    """读取可选 UTF-8 manifest。"""
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("manifest.json 根节点必须是对象")
    return value


def _mod_id(value: str) -> str:
    """规范化依赖标识。"""
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized or "numbered-loose-mod"
