"""把编号与根游戏路径 loose 模组转换为完整资源 ``.cdmod``。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from cdmm.archive.pamt import derive_pamt_dir
from cdmm.common.constants import KNOWN_GAME_TOP_DIRS
from cdmm.services.cdmod_converter import (
    CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE,
    CDMOD_FORMAT_NAME,
    CDMOD_FORMAT_VERSION,
    _write_cdmod_zip,
)
from cdmm.services.loose_file_service import _infer_dds_target_from_pathc
from cdmm.services.pamt_index_service import get_game_pamt_index, register_game_pamt_targets
from cdmm.utils.path_utils import lower_game_rel_path

# 全新资源目录没有 vanilla sibling 时，只允许按稳定的游戏顶层分区回退。
NEW_RESOURCE_PAMT_BY_TOP = {"character": "0009", "ui": "0012"}


@dataclass(frozen=True)
class GeneralLooseConversionResult:
    """通用 loose 转换结果。"""

    output_path: Path
    file_count: int
    payload_bytes: int


@dataclass(frozen=True)
class _LooseSource:
    """一个待解析的 loose 文件及其声明目标。"""

    path: Path
    target: str
    declared_pamt_dir: str | None


def convert_general_loose_to_cdmod(
    game_dir: Path,
    source_dir: Path,
    output_path: Path,
) -> GeneralLooseConversionResult:
    """解析 loose 文件真实 PAMT 位置并生成严格 file-replacement 包。"""
    game_dir = game_dir.resolve()
    source_dir = source_dir.resolve()
    output_path = output_path.resolve()
    sources = _collect_loose_sources(source_dir)
    if not sources:
        raise ValueError("没有发现可转换 loose 文件")

    register_game_pamt_targets(game_dir, [item.target for item in sources])
    index = get_game_pamt_index(game_dir)
    resolved, hints = _prepare_target_matches(index, sources)
    specs: list[dict[str, object]] = []
    documents: dict[str, dict[str, object] | bytes] = {}
    payload_bytes = 0
    for item in sources:
        target, pamt_dir, allow_new = _resolve_target(
            game_dir, item, resolved.get(item.path), hints
        )
        content = item.path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        payload_path = f"assets/{len(specs):05d}/{Path(target).name.lower()}"
        specs.append(
            {
                "target": target,
                "pamt_dir": pamt_dir,
                "payload": payload_path,
                "sha256": digest,
                "size": len(content),
                "allow_new": allow_new,
                "allow_table_replace": target.endswith((".pabgb", ".pabgh")),
            }
        )
        documents[payload_path] = content
        payload_bytes += len(content)

    metadata = _read_metadata(source_dir)
    title = str(metadata.get("title") or metadata.get("name") or source_dir.name)
    author = str(metadata.get("author") or "unknown")
    manifest = {
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": _safe_id(f"{author}.{title}"),
        "name": title,
        "version": str(metadata.get("version") or "legacy"),
        "author": author,
        "description": str(metadata.get("description") or ""),
        "dependencies": [],
        "source": {"format": "general-loose", "payload_bytes": payload_bytes},
        "components": [
            {
                "type": CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE,
                "path": "files/replacements.json",
                "file_count": len(specs),
            }
        ],
    }
    documents["manifest.json"] = manifest
    documents["files/replacements.json"] = {"schema": 1, "files": specs}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_cdmod_zip(output_path, documents)
    return GeneralLooseConversionResult(output_path, len(specs), payload_bytes)


def has_general_loose_files(source_dir: Path) -> bool:
    """判断目录是否包含任一种已支持 loose 路径。"""
    return bool(_collect_loose_sources(source_dir))


def collect_general_loose_targets(source_dir: Path) -> list[str]:
    """收集批量预注册所需的全部 loose 目标。"""
    return [item.target for item in _collect_loose_sources(source_dir)]


def _collect_loose_sources(source_dir: Path) -> list[_LooseSource]:
    """枚举四类 loose 根并按最终声明去重。"""
    result: dict[tuple[str | None, str], _LooseSource] = {}
    for root in (source_dir, source_dir / "files"):
        if not root.is_dir():
            continue
        for child in sorted((path for path in root.iterdir() if path.is_dir()), key=lambda p: p.name.lower()):
            if _is_pamt_dir(child.name):
                if (child / "0.paz").is_file() and (child / "0.pamt").is_file():
                    continue
                for file_path in sorted(path for path in child.rglob("*") if path.is_file()):
                    target = lower_game_rel_path(file_path.relative_to(child).as_posix())
                    result[(child.name, target)] = _LooseSource(file_path, target, child.name)
                continue
            if child.name.lower() not in KNOWN_GAME_TOP_DIRS:
                continue
            for file_path in sorted(path for path in child.rglob("*") if path.is_file()):
                target = lower_game_rel_path(file_path.relative_to(root).as_posix())
                result[(None, target)] = _LooseSource(file_path, target, None)
    return list(result.values())


def _prepare_target_matches(index, sources: list[_LooseSource]):
    """一次匹配全部目标，并从同目录命中项生成新增文件 sibling 提示。"""
    resolved = {}
    hint_candidates: dict[str, set[tuple[str, str]]] = {}
    for item in sources:
        entry = (
            index.find_in_dir(item.declared_pamt_dir, item.target)
            if item.declared_pamt_dir is not None
            else index.find_best(item.target, require_unique_best=False)
        )
        resolved[item.path] = entry
        if entry is None or item.declared_pamt_dir is not None:
            continue
        source_parent = str(Path(item.target).parent).replace("\\", "/").lower()
        entry_parent = _entry_parent_path(entry)
        hint_candidates.setdefault(source_parent, set()).add(
            (derive_pamt_dir(entry.paz_file), entry_parent)
        )
    hints = {
        parent: next(iter(candidates))
        for parent, candidates in hint_candidates.items()
        if len(candidates) == 1
    }
    return resolved, hints


def _resolve_target(
    game_dir: Path,
    item: _LooseSource,
    entry,
    hints: dict[str, tuple[str, str]],
) -> tuple[str, str, bool]:
    """优先精确目录解析，根路径则使用当前游戏最佳目标。"""
    if entry is None:
        if item.declared_pamt_dir is not None:
            return item.target, item.declared_pamt_dir, True
        source_parent = str(Path(item.target).parent).replace("\\", "/").lower()
        hint = hints.get(source_parent)
        if hint is None:
            top = item.target.split("/", 1)[0]
            fallback_dir = NEW_RESOURCE_PAMT_BY_TOP.get(top)
            if fallback_dir is None:
                raise ValueError(f"当前游戏未找到 loose 目标且无法从 sibling 推断：{item.target}")
            inferred = _infer_dds_target_from_pathc(game_dir, item.target)
            return inferred or item.target, fallback_dir, True
        pamt_dir, target_parent = hint
        target = f"{target_parent}/{Path(item.target).name}" if target_parent != "." else Path(item.target).name
        inferred = _infer_dds_target_from_pathc(game_dir, item.target)
        return inferred or lower_game_rel_path(target), pamt_dir, True
    # PAMT 的 entry.path 可能只有扁平 basename，真实目录保存在 folder record。
    # cdmod 必须保存完整最终路径，否则男女动作等同名资源会再次产生歧义。
    target = _entry_final_path(entry)
    if item.target.endswith(".dds"):
        inferred = _infer_dds_target_from_pathc(game_dir, item.target)
        if inferred is not None:
            target = inferred
    return target, derive_pamt_dir(entry.paz_file), False


def _entry_final_path(entry) -> str:
    """使用 PAMT folder record 还原资源的真实最终路径。"""
    entry_path = lower_game_rel_path(entry.path)
    parent = _entry_parent_path(entry)
    basename = entry_path.rsplit("/", 1)[-1]
    return f"{parent}/{basename}" if parent and parent != "." else entry_path


def _entry_parent_path(entry) -> str:
    """优先返回 PAMT folder record，缺失时退回扁平 entry 父目录。"""
    if entry.resolved_dir_path:
        return lower_game_rel_path(entry.resolved_dir_path).rstrip("/")
    return lower_game_rel_path(str(Path(entry.path).parent))


def _read_metadata(source_dir: Path) -> dict[str, object]:
    """读取常见模组元数据文件。"""
    for name in ("manifest.json", "modinfo.json", "mod.json"):
        path = source_dir / name
        if not path.is_file():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            return value
    return {}


def _is_pamt_dir(value: str) -> bool:
    """判断四位编号目录。"""
    return len(value) == 4 and value.isdigit()


def _safe_id(value: str) -> str:
    """生成稳定模组 ID。"""
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized or "general-loose-mod"
