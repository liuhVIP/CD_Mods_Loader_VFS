"""把 Hospade Blood Mod 转为 DDS 完整资源加 XML 增量补丁的 ``.cdmod``。"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from cdmm.services.cdmod_converter import (
    CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE,
    CDMOD_FORMAT_NAME,
    CDMOD_FORMAT_VERSION,
    CDMOD_LEGACY_JSON_COMPONENT_TYPE,
    _write_cdmod_zip,
)
from cdmm.services.cdmod_general_loose_converter import (
    _collect_loose_sources,
    _prepare_target_matches,
    _resolve_target,
)
from cdmm.services.cdmod_package import load_cdmod_package
from cdmm.services.json_loader import apply_byte_patches, extract_plaintext
from cdmm.services.pamt_index_service import get_game_pamt_index, register_game_pamt_targets

# 成品固定组件路径，便于加载器轻量收集目标。
LEGACY_PATCH_PATH = "patches/xml-incremental.json"
FILE_PATCH_PATH = "files/replacements.json"

# 差异上下文从一行逐步扩大；超过该值仍不唯一就拒绝转换。
MAX_CONTEXT_LINES = 32


@dataclass(frozen=True)
class HospadeBloodCompatBuildResult:
    """Hospade 增量兼容包构建摘要。"""

    output_path: str
    package_sha256: str
    package_bytes: int
    dds_files: int
    dds_payload_bytes: int
    xml_targets: int
    xml_changes: int
    xml_original_bytes: int
    xml_patched_bytes: int


def build_hospade_blood_compat_mod(
    game_dir: Path,
    source_dir: Path,
    output_path: Path,
) -> HospadeBloodCompatBuildResult:
    """以当前 vanilla 为基底，把八份 XML 整表转换为唯一上下文差异。"""
    game_dir = game_dir.resolve()
    source_dir = source_dir.resolve()
    output_path = output_path.resolve()
    _validate_inputs(game_dir, source_dir, output_path)

    sources = _collect_loose_sources(source_dir)
    xml_sources = [item for item in sources if item.path.suffix.casefold() == ".xml"]
    dds_sources = [item for item in sources if item.path.suffix.casefold() == ".dds"]
    unsupported = [
        item.path
        for item in sources
        if item.path.suffix.casefold() not in {".xml", ".dds"}
    ]
    if unsupported:
        raise ValueError(f"发现未分类 loose 资源：{', '.join(map(str, unsupported[:5]))}")
    if len(xml_sources) != 8 or len(dds_sources) != 39:
        raise ValueError(
            f"源模组文件数量异常：xml={len(xml_sources)} dds={len(dds_sources)}"
        )

    register_game_pamt_targets(game_dir, [item.target for item in sources])
    index = get_game_pamt_index(game_dir)
    resolved, hints = _prepare_target_matches(index, sources)
    documents: dict[str, dict[str, object] | bytes] = {}
    file_specs: list[dict[str, object]] = []
    dds_payload_bytes = 0
    for item in dds_sources:
        target, pamt_dir, allow_new = _resolve_target(
            game_dir,
            item,
            resolved.get(item.path),
            hints,
        )
        content = item.path.read_bytes()
        payload_path = f"assets/{len(file_specs):05d}/{Path(target).name.casefold()}"
        file_specs.append(
            {
                "target": target,
                "pamt_dir": pamt_dir,
                "payload": payload_path,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
                "allow_new": allow_new,
                "allow_table_replace": False,
            }
        )
        documents[payload_path] = content
        dds_payload_bytes += len(content)

    patch_blocks: list[dict[str, object]] = []
    xml_original_bytes = 0
    xml_patched_bytes = 0
    xml_change_count = 0
    for item in xml_sources:
        entry = resolved.get(item.path)
        if entry is None:
            raise ValueError(f"当前游戏未找到 XML 原版目标：{item.target}")
        target, pamt_dir, allow_new = _resolve_target(game_dir, item, entry, hints)
        if pamt_dir != item.declared_pamt_dir or allow_new:
            raise ValueError(f"XML 目标解析异常：{item.target} -> {pamt_dir}/{target}")
        vanilla = extract_plaintext(entry)[0]
        patched = item.path.read_bytes()
        changes = build_unique_context_changes(vanilla, patched, target)
        _validate_changes(vanilla, patched, changes, target)
        patch_blocks.append(
            {
                "game_file": target,
                "changes": changes,
            }
        )
        xml_original_bytes += len(vanilla)
        xml_patched_bytes += len(patched)
        xml_change_count += len(changes)

    manifest = {
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": "hospade-blood-mod-compatible",
        "name": "Hospade's Blood Mod - Compatible",
        "version": "1.4-1.15.00",
        "author": "Hospade",
        "description": (
            "Blood DDS resources plus incremental XML differences generated from Crimson "
            "Desert 1.15 vanilla; no complete XML tables are included."
        ),
        "dependencies": [],
        "source": {
            "format": "numbered-loose",
            "name": source_dir.name,
            "xml_policy": "unique-context-byte-diff",
        },
        "components": [
            {
                "type": CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE,
                "path": FILE_PATCH_PATH,
                "file_count": len(file_specs),
            },
            {
                "type": CDMOD_LEGACY_JSON_COMPONENT_TYPE,
                "path": LEGACY_PATCH_PATH,
                "target_count": len(patch_blocks),
                "change_count": xml_change_count,
            },
        ],
    }
    documents["manifest.json"] = manifest
    documents[FILE_PATCH_PATH] = {"schema": 1, "files": file_specs}
    documents[LEGACY_PATCH_PATH] = {
        "name": "Hospade Blood XML Incremental Compatibility",
        "version": "1.4-1.15.00",
        "author": "Hospade",
        "patches": patch_blocks,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_cdmod_zip(output_path, documents)

    package = load_cdmod_package(output_path)
    replacement_files = tuple(
        replacement
        for patch in package.file_patches
        for replacement in patch.files
    )
    if len(replacement_files) != 39:
        raise ValueError("成品 DDS 回读数量异常")
    if any(replacement.target.casefold().endswith(".xml") for replacement in replacement_files):
        raise ValueError("成品误把 XML 放入完整资源组件")
    if len(package.legacy_json_patches) != 1:
        raise ValueError("成品 XML 增量组件回读数量异常")

    return HospadeBloodCompatBuildResult(
        output_path=str(output_path),
        package_sha256=_sha256_file(output_path),
        package_bytes=output_path.stat().st_size,
        dds_files=len(replacement_files),
        dds_payload_bytes=dds_payload_bytes,
        xml_targets=len(patch_blocks),
        xml_changes=xml_change_count,
        xml_original_bytes=xml_original_bytes,
        xml_patched_bytes=xml_patched_bytes,
    )


def build_unique_context_changes(
    vanilla: bytes,
    patched: bytes,
    label: str,
) -> list[dict[str, object]]:
    """按行生成不重叠差异块，并扩大上下文直到每个旧块唯一。"""
    if vanilla == patched:
        raise ValueError(f"{label} 与当前原版完全相同，无需转换")
    vanilla_lines = vanilla.splitlines(keepends=True)
    patched_lines = patched.splitlines(keepends=True)
    matcher = difflib.SequenceMatcher(None, vanilla_lines, patched_lines, autojunk=False)
    for context_lines in range(1, MAX_CONTEXT_LINES + 1):
        changes = _changes_from_groups(
            vanilla,
            vanilla_lines,
            patched_lines,
            matcher.get_grouped_opcodes(context_lines),
            label,
        )
        if changes is not None:
            return changes
    raise ValueError(f"{label} 的差异在 {MAX_CONTEXT_LINES} 行上下文内仍不能唯一定位")


def _changes_from_groups(
    vanilla: bytes,
    vanilla_lines: list[bytes],
    patched_lines: list[bytes],
    groups,
    label: str,
) -> list[dict[str, object]] | None:
    """把 SequenceMatcher 分组转换为传统 JSON replace 规则。"""
    line_offsets = [0]
    for line in vanilla_lines:
        line_offsets.append(line_offsets[-1] + len(line))
    changes: list[dict[str, object]] = []
    for index, group in enumerate(groups):
        group = list(group)
        if not group:
            continue
        old_start = group[0][1]
        old_end = group[-1][2]
        new_start = group[0][3]
        new_end = group[-1][4]
        old = b"".join(vanilla_lines[old_start:old_end])
        new = b"".join(patched_lines[new_start:new_end])
        if not old or old == new or vanilla.count(old) != 1:
            return None
        changes.append(
            {
                "offset": line_offsets[old_start],
                "original": old.hex(),
                "patched": new.hex(),
                "label": f"{label} incremental block {index + 1}",
            }
        )
    return changes or None


def _validate_changes(
    vanilla: bytes,
    patched: bytes,
    changes: list[dict[str, object]],
    label: str,
) -> None:
    """复用真实加载器补丁器，要求增量规则逐字重建源模组。"""
    output = bytearray(vanilla)
    applied, mismatched, _relocated = apply_byte_patches(
        output,
        changes,
        vanilla_data=vanilla,
    )
    if applied != len(changes) or mismatched != 0 or bytes(output) != patched:
        raise ValueError(
            f"{label} 增量重建失败：applied={applied}/{len(changes)} "
            f"mismatched={mismatched} exact={bytes(output) == patched}"
        )


def _validate_inputs(game_dir: Path, source_dir: Path, output_path: Path) -> None:
    """校验游戏、源模组与输出边界。"""
    if not (game_dir / "bin64" / "CrimsonDesert.exe").is_file():
        raise ValueError(f"不是有效游戏目录：{game_dir}")
    if not source_dir.is_dir():
        raise ValueError(f"源模组目录不存在：{source_dir}")
    if output_path.suffix.casefold() != ".cdmod":
        raise ValueError("输出必须使用 .cdmod 后缀")


def _sha256_file(path: Path) -> str:
    """流式计算大型成品 SHA-256。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="生成 Hospade Blood Mod 增量兼容 .cdmod")
    parser.add_argument("--game-dir", type=Path, required=True)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    """执行构建并打印 JSON 摘要。"""
    args = parse_args()
    result = build_hospade_blood_compat_mod(args.game_dir, args.source_dir, args.output)
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
