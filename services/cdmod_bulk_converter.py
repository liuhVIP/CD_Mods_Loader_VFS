"""批量扫描 mods 并转换当前已支持的 ``.cdmod`` 类型。"""

from __future__ import annotations

import json
import hashlib
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path

from cdmm.services.cdmod_converter import (
    CDMOD_FORMAT_NAME,
    CDMOD_FORMAT_VERSION,
    CDMOD_LEGACY_JSON_COMPONENT_TYPE,
    CDMOD_STANDALONE_COMPONENT_TYPE,
    _write_cdmod_zip,
    convert_format3_to_cdmod,
)
from cdmm.services.cdmod_general_loose_converter import (
    collect_general_loose_targets,
    convert_general_loose_to_cdmod,
    has_general_loose_files,
)
from cdmm.services.cdmod_loose_converter import convert_numbered_loose_to_cdmod
from cdmm.services.pamt_index_service import register_game_pamt_targets

# 批量转换只并行执行相互独立的 ZIP 构建，限制并发避免大型 loose 模组占满内存。
DEFAULT_BULK_WORKERS = 2

# 批量报告固定文件名，方便后续继续补齐未支持类型。
BULK_REPORT_FILE_NAME = "conversion-report.json"


@dataclass(frozen=True)
class BulkConversionItem:
    """单个模组的批量转换结果。"""

    source: str
    source_type: str
    status: str
    output: str | None = None
    detail: str = ""


@dataclass(frozen=True)
class BulkConversionResult:
    """一次批量转换的汇总结果。"""

    mods_dir: Path
    output_dir: Path
    report_path: Path
    items: tuple[BulkConversionItem, ...]


@dataclass(frozen=True)
class _ConversionTask:
    source: Path
    source_type: str
    output: Path
    partial: bool = False


def convert_mods_directory_to_cdmod(
    mods_dir: Path,
    output_dir: Path,
    *,
    workers: int = DEFAULT_BULK_WORKERS,
) -> BulkConversionResult:
    """批量转换 Format 3 与 ``files/NNNN``，并报告所有暂不支持项。"""
    mods_dir = mods_dir.resolve()
    output_dir = output_dir.resolve()
    if not mods_dir.is_dir():
        raise ValueError(f"mods 目录不存在：{mods_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    tasks: list[_ConversionTask] = []
    items: list[BulkConversionItem] = []
    for source in sorted(mods_dir.iterdir(), key=lambda path: path.name.lower()):
        task, item = _classify_source(source, output_dir)
        if task is not None:
            tasks.append(task)
        elif item is not None:
            items.append(item)

    # PAMT 索引是全局按需缓存，root loose 目标解析必须串行，避免多个线程重复
    # 扫描大型 PAMT 并争用缓存；纯 JSON/ZIP 任务仍可并行。
    pamt_tasks = [task for task in tasks if task.source_type == "general-loose"]
    parallel_tasks = [task for task in tasks if task.source_type != "general-loose"]
    all_loose_targets = [
        target
        for task in pamt_tasks
        for target in collect_general_loose_targets(task.source)
    ]
    register_game_pamt_targets(mods_dir.parent, all_loose_targets)
    for task in pamt_tasks:
        item = _run_task(task, mods_dir.parent)
        items.append(item)

    max_workers = max(1, min(int(workers), 4))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_convert_task, task, mods_dir.parent): task for task in parallel_tasks
        }
        for future in as_completed(futures):
            task = futures[future]
            try:
                future.result()
                status = "partial" if task.partial else "converted"
                detail = "仅转换 files/NNNN 组件，目录还包含其他组件" if task.partial else ""
                items.append(
                    BulkConversionItem(
                        str(task.source),
                        task.source_type,
                        status,
                        str(task.output),
                        detail,
                    )
                )
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                items.append(
                    BulkConversionItem(
                        str(task.source),
                        task.source_type,
                        "failed",
                        str(task.output),
                        str(exc),
                    )
                )

    items.sort(key=lambda item: item.source.lower())
    report_path = output_dir / BULK_REPORT_FILE_NAME
    report = {
        "schema": 1,
        "mods_dir": str(mods_dir),
        "output_dir": str(output_dir),
        "summary": _summarize(items),
        "items": [asdict(item) for item in items],
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return BulkConversionResult(mods_dir, output_dir, report_path, tuple(items))


def _run_task(task: _ConversionTask, game_dir: Path) -> BulkConversionItem:
    """串行执行任务并转换为统一报告项。"""
    try:
        _convert_task(task, game_dir)
        status = "partial" if task.partial else "converted"
        detail = "仅转换已识别 loose 组件" if task.partial else ""
        return BulkConversionItem(
            str(task.source), task.source_type, status, str(task.output), detail
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return BulkConversionItem(
            str(task.source), task.source_type, "failed", str(task.output), str(exc)
        )


def bulk_result_to_json(result: BulkConversionResult) -> dict[str, object]:
    """生成适合 CLI 输出的精简汇总。"""
    summary = _summarize(list(result.items))
    return {
        "mods_dir": str(result.mods_dir),
        "output_dir": str(result.output_dir),
        "report_path": str(result.report_path),
        "summary": summary,
    }


def _classify_source(
    source: Path,
    output_dir: Path,
) -> tuple[_ConversionTask | None, BulkConversionItem | None]:
    """识别单个顶层候选，严格区分可转换与暂不支持类型。"""
    if source.is_file():
        suffix = source.suffix.lower()
        if suffix == ".cdmod":
            return None, BulkConversionItem(str(source), "cdmod", "existing", detail="已是 cdmod")
        if suffix != ".json":
            return None, BulkConversionItem(str(source), "file", "unsupported", detail="未知顶层文件")
        try:
            document = json.loads(source.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            return None, BulkConversionItem(str(source), "json", "failed", detail=str(exc))
        if _is_format3(document):
            output = output_dir / f"{_safe_name(source.stem)}.cdmod"
            return _ConversionTask(source, "format3", output), None
        if isinstance(document, dict) and isinstance(document.get("patches"), list):
            output = output_dir / f"{_safe_name(source.stem)}.cdmod"
            return _ConversionTask(source, "json-byte-patch", output), None
        return None, BulkConversionItem(str(source), "json", "unsupported", detail="未知 JSON 结构")

    if not source.is_dir():
        return None, None
    if has_general_loose_files(source):
        output = output_dir / f"{_safe_name(source.name)}.cdmod"
        return _ConversionTask(source, "general-loose", output), None

    files = [path for path in source.rglob("*") if path.is_file()]
    if not files:
        return None, BulkConversionItem(str(source), "empty-directory", "ignored", detail="空目录")
    if _has_standalone(files):
        output = output_dir / f"{_safe_name(source.name)}.cdmod"
        return _ConversionTask(source, "standalone-paz-pamt", output), None
    elif _has_root_loose(source, files):
        source_type = "root-loose"
        detail = "根路径 loose 尚未支持自动 PAMT 目标声明"
    elif any(path.suffix.lower() == ".json" for path in files):
        source_type = "mixed-or-nested-json"
        detail = "目录包含嵌套 JSON 或专用安装结构，需继续分类"
    else:
        source_type = "unknown-directory"
        detail = "未识别目录结构"
    return None, BulkConversionItem(str(source), source_type, "unsupported", detail=detail)


def _convert_task(task: _ConversionTask, game_dir: Path) -> None:
    """执行一个已分类任务。"""
    if task.source_type == "format3":
        convert_format3_to_cdmod(task.source, task.output)
        return
    if task.source_type == "numbered-loose":
        convert_numbered_loose_to_cdmod(task.source, task.output)
        return
    if task.source_type == "general-loose":
        convert_general_loose_to_cdmod(game_dir, task.source, task.output)
        return
    if task.source_type == "json-byte-patch":
        _convert_legacy_json(task.source, task.output)
        return
    if task.source_type == "standalone-paz-pamt":
        _convert_standalone(task.source, task.output)
        return
    raise ValueError(f"没有转换器：{task.source_type}")


def _is_format3(document: object) -> bool:
    """识别 Format 3 顶层结构。"""
    if not isinstance(document, dict):
        return False
    return document.get("format") == 3 or "targets" in document or "intents" in document


def _convert_legacy_json(source: Path, output: Path) -> None:
    """把传统 JSON 原语封装为确定性 cdmod。"""
    document = json.loads(source.read_text(encoding="utf-8-sig"))
    metadata = document.get("modinfo") if isinstance(document, dict) else None
    metadata = metadata if isinstance(metadata, dict) else {}
    name = str(metadata.get("title") or metadata.get("name") or source.stem)
    manifest = {
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": _safe_id(f"{metadata.get('author', 'unknown')}.{name}"),
        "name": name,
        "version": str(metadata.get("version") or "legacy"),
        "author": str(metadata.get("author") or "unknown"),
        "description": str(metadata.get("description") or ""),
        "dependencies": [],
        "source": {"format": "legacy-json-byte-patch", "payload_bytes": source.stat().st_size},
        "components": [
            {"type": CDMOD_LEGACY_JSON_COMPONENT_TYPE, "path": "patches/legacy.json"}
        ],
    }
    _write_cdmod_zip(output, {"manifest.json": manifest, "patches/legacy.json": document})


def _safe_id(value: str) -> str:
    """生成容器依赖 ID。"""
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized or "legacy-json-mod"


def _convert_standalone(source: Path, output: Path) -> None:
    """把一个目录内的 standalone PAZ/PAMT 对封装为 cdmod。"""
    archive_dirs = [
        path
        for path in source.iterdir()
        if path.is_dir() and (path / "0.paz").is_file() and (path / "0.pamt").is_file()
    ]
    if not archive_dirs:
        raise ValueError("没有发现 standalone PAZ/PAMT 对")
    documents: dict[str, dict[str, object] | bytes] = {}
    components: list[dict[str, object]] = []
    payload_bytes = 0
    for index, archive_dir in enumerate(sorted(archive_dirs, key=lambda path: path.name)):
        paz = (archive_dir / "0.paz").read_bytes()
        pamt = (archive_dir / "0.pamt").read_bytes()
        base = f"archives/{index:03d}"
        descriptor_path = f"{base}/archive.json"
        paz_path = f"{base}/0.paz"
        pamt_path = f"{base}/0.pamt"
        documents[paz_path] = paz
        documents[pamt_path] = pamt
        documents[descriptor_path] = {
            "schema": 1,
            "name": archive_dir.name,
            "paz": paz_path,
            "pamt": pamt_path,
            "paz_sha256": hashlib.sha256(paz).hexdigest(),
            "pamt_sha256": hashlib.sha256(pamt).hexdigest(),
        }
        components.append({"type": CDMOD_STANDALONE_COMPONENT_TYPE, "path": descriptor_path})
        payload_bytes += len(paz) + len(pamt)
    manifest = {
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": _safe_id(source.name),
        "name": source.name,
        "version": "legacy",
        "author": "unknown",
        "description": "",
        "dependencies": [],
        "source": {"format": "standalone-paz-pamt", "payload_bytes": payload_bytes},
        "components": components,
    }
    documents["manifest.json"] = manifest
    _write_cdmod_zip(output, documents)


def _has_non_numbered_payload(source: Path, numbered_dirs: list[Path]) -> bool:
    """判断 ``files/NNNN`` 外是否还有会影响游戏的组件。"""
    allowed_roots = {path.resolve() for path in numbered_dirs}
    for path in source.rglob("*"):
        if not path.is_file() or path.name.lower() == "manifest.json":
            continue
        if not any(root in path.resolve().parents for root in allowed_roots):
            return True
    return False


def _has_standalone(files: list[Path]) -> bool:
    """判断目录是否包含完整 PAZ/PAMT 对。"""
    return any(path.name.lower() == "0.pamt" and (path.parent / "0.paz").is_file() for path in files)


def _has_root_loose(source: Path, files: list[Path]) -> bool:
    """判断是否存在根游戏路径 loose 文件。"""
    known_roots = {"gamedata", "ui", "character", "sequencer"}
    return any(path.relative_to(source).parts[0].lower() in known_roots for path in files)


def _is_pamt_dir(value: str) -> bool:
    """判断四位 PAMT 目录名。"""
    return len(value) == 4 and value.isdigit()


def _safe_name(value: str) -> str:
    """生成 Windows 可用且稳定的输出文件名。"""
    normalized = re.sub(r'[<>:"/\\|?*]+', "_", value).strip(" .")
    return normalized or "converted-mod"


def _summarize(items: list[BulkConversionItem]) -> dict[str, int]:
    """按状态汇总报告。"""
    summary: dict[str, int] = {}
    for item in items:
        summary[item.status] = summary.get(item.status, 0) + 1
    return summary
