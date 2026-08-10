"""执行 ``.cdmod`` 资源变换并生成最终 overlay entry。"""

from __future__ import annotations

from pathlib import Path

from cdmm.archive.pamt import derive_pamt_dir
from cdmm.common.models import OverlayInputEntry, PazEntry
from cdmm.services.cdmod_package import CdmodPackage, CdmodResourceTransform
from cdmm.services.json_loader import extract_plaintext
from cdmm.services.pamt_index_service import get_game_pamt_index
from cdmm.storage.vanilla_store import VanillaStore
from cdmm.utils.path_utils import lower_game_rel_path

# resource-transform 需要显式处理游戏使用的下划线 XML；该规则只作用于
# 资源复制链，不扩大 PazEntry 的全局加密后缀判断。
ENCRYPTED_RESOURCE_TRANSFORM_SUFFIXES = ("_xml",)


def collect_resource_pamt_targets(packages: list[CdmodPackage]) -> list[str]:
    """收集资源变换需要精确查询的源和目标。"""
    targets: list[str] = []
    for package in packages:
        for patch in package.resource_patches:
            for operation in patch.operations:
                targets.append(operation.target)
                if operation.source is not None:
                    targets.append(operation.source)
    return list(dict.fromkeys(targets))


def build_resource_overlay_entries(
    game_dir: Path,
    packages: list[CdmodPackage],
    vanilla_store: VanillaStore,
    warnings: list[str],
    errors: list[str],
    base_entries: list[OverlayInputEntry] | None = None,
) -> list[OverlayInputEntry]:
    """按包顺序执行资源复制和等长字节替换。"""
    base_entries = base_entries or []
    outputs: dict[tuple[str, str], OverlayInputEntry] = {}
    owners: dict[tuple[str, str], str] = {}
    for package in packages:
        for patch in package.resource_patches:
            for operation in patch.operations:
                identity = (operation.target_pamt_dir, lower_game_rel_path(operation.target))
                previous_owner = owners.get(identity)
                try:
                    if operation.op == "copy-entry":
                        output = _build_copy_entry(
                            game_dir,
                            operation,
                            vanilla_store,
                            base_entries,
                        )
                        detail = "copy-entry"
                    else:
                        current = outputs.get(identity)
                        output, applied, already = _build_replace_entry(
                            game_dir,
                            operation,
                            vanilla_store,
                            base_entries,
                            current,
                        )
                        detail = f"replace-bytes 应用 {applied}，已存在 {already}"
                except (OSError, ValueError) as exc:
                    errors.append(f"{package.name}: 资源变换失败 {operation.target}：{exc}")
                    continue
                if previous_owner is not None:
                    warnings.append(
                        f"cdmod 资源冲突：{operation.target_pamt_dir}/{operation.target} "
                        f"按加载顺序由 {package.name} 覆盖/叠加 {previous_owner}"
                    )
                outputs[identity] = output
                owners[identity] = package.name
                warnings.append(f"{package.name}: 资源变换 {detail} -> {operation.target}")
    return list(outputs.values())


def _build_copy_entry(
    game_dir: Path,
    operation: CdmodResourceTransform,
    vanilla_store: VanillaStore,
    base_entries: list[OverlayInputEntry],
) -> OverlayInputEntry:
    """读取当前合成 source 内容并写到 target 的 PAMT 元数据位置。"""
    if operation.source is None or operation.source_pamt_dir is None:
        raise ValueError("copy-entry 缺少 source")
    source = _resolve_resource_source(
        game_dir,
        operation.source_pamt_dir,
        operation.source,
        base_entries,
    )
    target = _resolve_target_source(
        game_dir,
        operation.target_pamt_dir,
        operation.target,
        base_entries,
    )
    if isinstance(source, OverlayInputEntry):
        content = source.content
    else:
        content, _detected_source = _extract_resource_plaintext(source)
    template = (
        target
        if isinstance(target, OverlayInputEntry)
        else _entry_template(target, vanilla_store)
    )
    return _with_content(template, content)


def _resolve_resource_source(
    game_dir: Path,
    pamt_dir: str,
    source: str,
    base_entries: list[OverlayInputEntry],
) -> OverlayInputEntry | PazEntry:
    """优先读取前序模组的最终 source，未覆盖时回退当前原版。"""
    normalized = lower_game_rel_path(source)
    matches = [
        entry
        for entry in base_entries
        if _matches_base_entry(entry, pamt_dir, normalized)
    ]
    if matches:
        return matches[-1]
    return _find_vanilla_entry(game_dir, pamt_dir, source)


def _build_replace_entry(
    game_dir: Path,
    operation: CdmodResourceTransform,
    vanilla_store: VanillaStore,
    base_entries: list[OverlayInputEntry],
    current: OverlayInputEntry | None,
) -> tuple[OverlayInputEntry, int, int]:
    """在当前合成 base 上执行等长替换，允许目标已经被同规则处理。"""
    source: OverlayInputEntry | PazEntry = current or _resolve_target_source(
        game_dir,
        operation.target_pamt_dir,
        operation.target,
        base_entries,
    )
    if isinstance(source, OverlayInputEntry):
        content = source.content
        template = source
    else:
        content, detected = _extract_resource_plaintext(source)
        template = _entry_template(detected, vanilla_store)
    applied = 0
    already = 0
    recognized = 0
    for replacement in operation.replacements:
        old_count = content.count(replacement.old)
        new_count = content.count(replacement.new)
        recognized += old_count + new_count
        if old_count:
            content = content.replace(replacement.old, replacement.new)
            applied += old_count
        elif new_count:
            already += new_count
    if recognized == 0:
        raise ValueError("所有预期旧值和目标值均不存在，疑似游戏更新后资源结构已变化")
    return _with_content(template, content), applied, already


def _resolve_target_source(
    game_dir: Path,
    pamt_dir: str,
    target: str,
    base_entries: list[OverlayInputEntry],
) -> OverlayInputEntry | PazEntry:
    """同目录同路径 base 优先，否则读取 vanilla target。"""
    normalized = lower_game_rel_path(target)
    matches = [
        entry
        for entry in base_entries
        if _matches_base_entry(entry, pamt_dir, normalized)
    ]
    if matches:
        return matches[-1]
    return _find_vanilla_entry(game_dir, pamt_dir, target)


def _matches_base_entry(
    entry: OverlayInputEntry,
    pamt_dir: str,
    normalized_path: str,
) -> bool:
    """同时识别完整 entry_path 与 PAMT 扁平路径加真实目录的表示。"""
    if entry.pamt_dir != pamt_dir:
        return False
    entry_path = lower_game_rel_path(entry.entry_path)
    if entry_path == normalized_path:
        return True
    if not entry.resolved_dir_path:
        return False
    basename = entry_path.rsplit("/", 1)[-1]
    final_path = lower_game_rel_path(f"{entry.resolved_dir_path}/{basename}")
    return final_path == normalized_path


def _find_vanilla_entry(game_dir: Path, pamt_dir: str, target: str) -> PazEntry:
    """在指定 vanilla PAMT 中精确或唯一 basename 查找 entry。"""
    entry = get_game_pamt_index(game_dir).find_in_dir(pamt_dir, target)
    if entry is None:
        raise ValueError(f"{pamt_dir} 中未找到 {target}")
    return entry


def _entry_template(
    source: PazEntry,
    vanilla_store: VanillaStore,
) -> OverlayInputEntry:
    """从 target 原版 entry 生成 overlay 元数据模板。"""
    entry = vanilla_store.ensure_entry_backup(source)
    return OverlayInputEntry(
        content=b"",
        entry_path=entry.path,
        pamt_dir=derive_pamt_dir(entry.paz_file),
        compression_type=entry.compression_type,
        encrypted=_is_resource_transform_encrypted(entry),
        crypto_filename=Path(entry.path).name,
    )


def _extract_resource_plaintext(entry: PazEntry) -> tuple[bytes, PazEntry]:
    """读取资源变换明文，并对下划线 XML 显式启用加密探测。"""
    if _is_resource_transform_encrypted(entry):
        entry = entry.with_encrypted_override(True)
    return extract_plaintext(entry)


def _is_resource_transform_encrypted(entry: PazEntry) -> bool:
    """判断资源变换目标是否使用按文件名派生密钥的加密。"""
    return entry.encrypted or entry.path.casefold().endswith(
        ENCRYPTED_RESOURCE_TRANSFORM_SUFFIXES
    )


def _with_content(template: OverlayInputEntry, content: bytes) -> OverlayInputEntry:
    """保留 target 元数据并替换最终明文内容。"""
    return OverlayInputEntry(
        content=content,
        entry_path=template.entry_path,
        pamt_dir=template.pamt_dir,
        compression_type=template.compression_type,
        encrypted=template.encrypted,
        crypto_filename=template.crypto_filename,
        preserve_entry_dir=template.preserve_entry_dir,
        resolved_dir_path=template.resolved_dir_path,
    )
