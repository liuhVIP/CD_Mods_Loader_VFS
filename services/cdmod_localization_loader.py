"""将 ``.cdmod`` 本地化组件合并为最终 PALOC overlay entry。"""

from __future__ import annotations

from collections import defaultdict
from fnmatch import fnmatchcase
import locale
import os
from pathlib import Path
import re

from cdmm.archive.pamt import derive_pamt_dir, parse_pamt
from cdmm.common.models import OverlayInputEntry, PazEntry
from cdmm.services.cdmod_package import (
    CdmodLocalizationChange,
    CdmodLocalizationPatch,
    CdmodPackage,
)
from cdmm.services.json_loader import extract_plaintext
from cdmm.services.paloc import PalocLayoutError, PalocRecord, parse_paloc, serialize_paloc
from cdmm.services.pamt_index_service import get_game_pamt_index
from cdmm.storage.vanilla_store import VanillaStore
from cdmm.utils.path_utils import lower_game_rel_path

# Steam 语言名到游戏 PALOC 文件后缀的稳定映射。
_STEAM_LANGUAGE_TO_PALOC = {
    "schinese": "zho-cn",
    "tchinese": "zho-tw",
    "english": "eng",
    "korean": "kor",
    "japanese": "jpn",
    "russian": "rus",
    "turkish": "tur",
    "spanish": "spa-es",
    "latam": "spa-mx",
    "french": "fre",
    "german": "ger",
    "italian": "ita",
    "polish": "pol",
    "brazilian": "por-br",
}

# 系统区域只用于非 Steam 或 manifest 缺失时的保守回退。
_LOCALE_TO_PALOC = {
    "zh_cn": "zho-cn",
    "zh_tw": "zho-tw",
    "zh_hk": "zho-tw",
    "en": "eng",
    "ko": "kor",
    "ja": "jpn",
    "ru": "rus",
    "tr": "tur",
    "es": "spa-es",
    "fr": "fre",
    "de": "ger",
    "it": "ita",
    "pl": "pol",
    "pt_br": "por-br",
}

# 无法从环境变量、Steam manifest 或系统区域识别语言时统一使用简体中文。
# 该默认值避免语言通配本地化模组阻断整个 VFS 构建和游戏启动。
DEFAULT_PALOC_LANGUAGE = "zho-cn"


def collect_localization_pamt_targets(packages: list[CdmodPackage]) -> list[str]:
    """收集本地化组件需要查询的 PALOC 目标。"""
    return list(
        dict.fromkeys(
            patch.target
            for package in packages
            for patch in package.localization_patches
            if "*" not in patch.target
        )
    )


def build_localization_overlay_entries(
    game_dir: Path,
    packages: list[CdmodPackage],
    vanilla_store: VanillaStore,
    warnings: list[str],
    errors: list[str],
    base_entries: list[OverlayInputEntry] | None = None,
) -> list[OverlayInputEntry]:
    """按包顺序合并 PALOC key 修改，原版漂移时严格拒绝。"""
    source_entries = base_entries or []
    patches_by_target: dict[str, list[tuple[CdmodPackage, CdmodLocalizationPatch]]] = defaultdict(list)
    sources_by_target: dict[str, OverlayInputEntry | PazEntry] = {}
    for package in packages:
        for patch in package.localization_patches:
            try:
                matches = _expand_patch_sources(game_dir, patch.target, source_entries)
            except ValueError as exc:
                errors.append(f"{package.name}: {exc}")
                continue
            if not matches:
                errors.append(f"cdmod 本地化目标未找到：{patch.target}")
                continue
            for actual_target, source in matches.items():
                patches_by_target[actual_target].append((package, patch))
                sources_by_target.setdefault(actual_target, source)

    results: list[OverlayInputEntry] = []
    pending_inserts = _collect_insert_changes(packages)
    for target, patches in patches_by_target.items():
        source = sources_by_target[target]
        content, output_template = _read_source(source, vanilla_store)
        try:
            document = parse_paloc(content)
        except PalocLayoutError as exc:
            # 实测有 12 个拆分字符串表使用未知布局变体（category 高位非 0）。
            # 它们不是任何 category 的归属文件，跳过即可，不能中断整个构建。
            warnings.append(f"cdmod 本地化目标 {target} 布局变体暂不支持：{exc}")
            continue
        except ValueError as exc:
            errors.append(f"cdmod 本地化目标 {target} 解析失败：{exc}")
            continue
        values = {record.key: record.value for record in document.records}
        by_key = document.by_key()
        document_categories = document.categories()
        inserts: dict[str, PalocRecord] = {}
        touched_by: dict[str, str] = {}
        target_failed = False
        applied = 0
        already_applied = 0
        for package, patch in patches:
            for change in patch.changes:
                if change.op == "insert":
                    status, already = _apply_insert_change(
                        package,
                        change,
                        target,
                        values,
                        by_key,
                        inserts,
                        document_categories,
                        warnings,
                        errors,
                    )
                    if status == "skip":
                        continue
                    if status == "failed":
                        target_failed = True
                        continue
                    pending_inserts.pop((package.name, change.index), None)
                    touched_by[change.key] = package.name
                    if already:
                        already_applied += 1
                    else:
                        applied += 1
                    continue
                current = values.get(change.key)
                if current is None:
                    if change.op == "append":
                        warnings.append(
                            f"{package.name}: PALOC {target} 缺少 key={change.key}，已跳过该语言"
                        )
                        continue
                    errors.append(
                        f"{package.name}: PALOC {target} 缺少 key={change.key}，"
                        "疑似游戏版本不兼容"
                    )
                    target_failed = True
                    continue
                if change.op == "append":
                    suffix = change.suffix or ""
                    if current.endswith(suffix):
                        already_applied += 1
                        touched_by[change.key] = package.name
                        continue
                    previous = touched_by.get(change.key)
                    if previous is not None:
                        warnings.append(
                            f"cdmod 本地化冲突：{target} key={change.key} "
                            f"由 {package.name} 继续叠加在 {previous} 之后"
                        )
                    values[change.key] = current + suffix
                    touched_by[change.key] = package.name
                    applied += 1
                    continue
                if current == change.value:
                    already_applied += 1
                    touched_by[change.key] = package.name
                    continue
                if current != change.expect:
                    previous = touched_by.get(change.key)
                    if previous is None:
                        errors.append(
                            f"{package.name}: PALOC {target} key={change.key} 原值不匹配，"
                            "疑似游戏更新后文本已变化"
                        )
                        target_failed = True
                        continue
                    warnings.append(
                        f"cdmod 本地化冲突：{target} key={change.key} "
                        f"按加载顺序由 {package.name} 覆盖 {previous}"
                    )
                values[change.key] = change.value
                touched_by[change.key] = package.name
                applied += 1
        if target_failed:
            continue
        try:
            changed_values = {
                key: values[key] for key in touched_by if key not in inserts
            }
            rebuilt_document = document.replace_values(changed_values)
            if inserts:
                rebuilt_document = rebuilt_document.with_inserted(tuple(inserts.values()))
            rebuilt = serialize_paloc(rebuilt_document)
        except ValueError as exc:
            errors.append(f"cdmod 本地化目标 {target} 重建失败：{exc}")
            continue
        if rebuilt == content:
            # 语言通配目标会展开到该语言的全部拆分表；没有改动到内容的表
            # 不应写进 overlay，否则一个模组会平白重写几十个大文件。
            warnings.append(f"cdmod 本地化：{target} 无改动，已跳过写入")
            continue
        results.append(_with_content(output_template, rebuilt))
        warnings.append(
            f"cdmod 本地化：{target} 应用 {applied} 条，已是目标值 {already_applied} 条"
        )
    for (package_name, index), (change,) in sorted(pending_inserts.items()):
        errors.append(
            f"{package_name}: PALOC 没有任何字符串表包含 category={change.category}，"
            f"无法新增 key={change.key}（entry#{index}）"
        )
    return results


def _collect_insert_changes(
    packages: list[CdmodPackage],
) -> dict[tuple[str, int], tuple[CdmodLocalizationChange,]]:
    """收集所有待新增记录，用于事后报告“没有归属字符串表”的失败。"""
    pending: dict[tuple[str, int], tuple[CdmodLocalizationChange,]] = {}
    for package in packages:
        for patch in package.localization_patches:
            for change in patch.changes:
                if change.op == "insert":
                    pending[(package.name, change.index)] = (change,)
    return pending


def _apply_insert_change(
    package: CdmodPackage,
    change: CdmodLocalizationChange,
    target: str,
    values: dict[str, str],
    by_key: dict[str, PalocRecord],
    inserts: dict[str, PalocRecord],
    document_categories: set[int],
    warnings: list[str],
    errors: list[str],
) -> tuple[str, bool]:
    """把一条 PALOC 新增记录写入归属文件。

    返回 ``(status, already)``：``skip`` 表示该拆分表不拥有这个 category，
    应交给归属文件处理；``failed`` 表示归属文件里出现结构冲突，必须拒绝构建；
    ``placed`` 表示已经写入。
    """
    if change.category is None:
        errors.append(f"{package.name}: PALOC 新增记录缺少 category（key={change.key}）")
        return "failed", False
    existing = by_key.get(change.key)
    if change.category not in document_categories and existing is None:
        return "skip", False
    if existing is not None and existing.category != change.category:
        errors.append(
            f"{package.name}: PALOC {target} key={change.key} 已存在且 category 不同"
            f"（原 {existing.category}，模组 {change.category}）"
        )
        return "failed", False
    if existing is not None:
        if existing.value == change.value:
            return "placed", True
        warnings.append(
            f"cdmod 本地化：{target} key={change.key} 已存在，按加载顺序由 "
            f"{package.name} 覆盖原文"
        )
        values[change.key] = change.value
        inserts.pop(change.key, None)
        return "placed", False
    previous = inserts.get(change.key)
    if previous is not None:
        warnings.append(
            f"cdmod 本地化冲突：{target} key={change.key} 由 {package.name} "
            "覆盖同一加载阶段的重复新增"
        )
    inserts[change.key] = PalocRecord(change.category, change.key, change.value)
    return "placed", False


def _expand_patch_sources(
    game_dir: Path,
    target: str,
    base_entries: list[OverlayInputEntry],
) -> dict[str, OverlayInputEntry | PazEntry]:
    """展开单目标或语言通配 PALOC，低编号 vanilla 优先。"""
    if "*" not in target:
        # On 2.01 the legacy merged filename is replaced by split per-table
        # PALOC files. Treat it as an implicit language wildcard.
        name = Path(lower_game_rel_path(target)).name
        if not (name.startswith("localizationstring_") and name.endswith(".paloc")):
            source = _resolve_source_entry(game_dir, target, base_entries)
            return {lower_game_rel_path(target): source} if source is not None else {}
        target = lower_game_rel_path(target)
    pattern = lower_game_rel_path(target)
    # Legacy target localizationstring_<lang>.paloc represented one merged
    # table.  Since 2.01 each language is split into 39 ``*.paloc`` files;
    # accept the legacy wildcard and enumerate the active language directory.
    legacy_language = None
    legacy_name = Path(pattern).name
    if legacy_name.startswith("localizationstring_") and legacy_name.endswith(".paloc"):
        legacy_language = legacy_name[len("localizationstring_") : -len(".paloc")]
        if legacy_language == "*":
            legacy_language = None
    if pattern.count("*") != 1 and legacy_language is None:
        raise ValueError(f"不安全的 PALOC 通配目标：{target}")
    # Legacy wildcard is still matched against base entries (unit tests and
    # pre-2.01 installs) before scanning split PAMT directories.
    matches: dict[str, OverlayInputEntry | PazEntry] = {}
    for entry in base_entries:
        normalized = lower_game_rel_path(entry.entry_path)
        language_dir = lower_game_rel_path(entry.resolved_dir_path or "")
        # 2.01 拆分表在 basename 里没有语言后缀，语言只记录在 PAMT 的
        # resolved_dir_path 上；前序阶段产出的 base entry 必须用同一条规则
        # 匹配，否则同一构建里后面的本地化补丁会找不到归属文件。
        is_legacy_language = (
            legacy_language not in (None, "*")
            and normalized.endswith(f"_{legacy_language}.paloc")
        )
        is_split_language = (
            legacy_language not in (None, "*")
            and normalized.endswith(".paloc")
            and language_dir.endswith("/" + legacy_language)
        )
        if fnmatchcase(normalized, pattern) or is_legacy_language or is_split_language:
            matches[normalized] = entry
    for pamt_path in sorted(game_dir.glob("[0-9][0-9][0-9][0-9]/0.pamt")):
        try:
            entries = parse_pamt(pamt_path, pamt_path.parent)
        except (OSError, ValueError):
            continue
        for entry in entries:
            normalized = lower_game_rel_path(entry.path)
            language_dir = lower_game_rel_path(entry.resolved_dir_path or "")
            is_split_language = (
                legacy_language is not None
                and normalized.endswith(".paloc")
                and language_dir.endswith("/" + legacy_language)
            )
            if fnmatchcase(normalized, pattern) or is_split_language:
                matches.setdefault(normalized, entry)
    if not matches:
        return matches
    active_language = detect_active_paloc_language(game_dir)
    suffix = f"_{active_language}.paloc"
    if legacy_language not in (None, "*"):
        # Split-table 2.01 entries have no language suffix in basename; the
        # language was encoded by PAMT resolved_dir_path. Keep all entries
        # discovered from the requested legacy language directory.
        if legacy_language != active_language:
            return {}
        return matches
    selected = {
        target_path: source
        for target_path, source in matches.items()
        if target_path.endswith(suffix)
    }
    if not selected:
        raise ValueError(f"当前语言 {active_language} 对应的 PALOC 不存在")
    return selected


def detect_active_paloc_language(game_dir: Path) -> str:
    """按显式覆盖、Steam manifest、系统区域识别，失败时回退简体中文。"""
    explicit = os.environ.get("CDLOADER_LANGUAGE", "").strip().lower()
    if explicit:
        return _STEAM_LANGUAGE_TO_PALOC.get(explicit, explicit)
    steam_language = _read_steam_manifest_language(game_dir)
    if steam_language is not None:
        return _STEAM_LANGUAGE_TO_PALOC.get(steam_language, steam_language)
    locale_name = (locale.getlocale()[0] or "").lower().replace("-", "_")
    if locale_name in _LOCALE_TO_PALOC:
        return _LOCALE_TO_PALOC[locale_name]
    language_prefix = locale_name.split("_", 1)[0]
    return _LOCALE_TO_PALOC.get(language_prefix, DEFAULT_PALOC_LANGUAGE)


def _read_steam_manifest_language(game_dir: Path) -> str | None:
    """只读取与目标 installdir 匹配的 Steam appmanifest language。"""
    common_dir = game_dir.parent
    steamapps_dir = common_dir.parent
    if common_dir.name.lower() != "common" or steamapps_dir.name.lower() != "steamapps":
        return None
    for manifest in sorted(steamapps_dir.glob("appmanifest_*.acf")):
        try:
            content = manifest.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        install_match = re.search(r'"installdir"\s+"([^"]+)"', content, re.IGNORECASE)
        if install_match is None or install_match.group(1).casefold() != game_dir.name.casefold():
            continue
        language_match = re.search(r'"language"\s+"([^"]+)"', content, re.IGNORECASE)
        if language_match is not None:
            return language_match.group(1).lower()
    return None


def _resolve_source_entry(
    game_dir: Path,
    target: str,
    base_entries: list[OverlayInputEntry],
) -> OverlayInputEntry | PazEntry | None:
    """优先使用前序合成 base，其次从最新 vanilla PAMT 定位。"""
    normalized = lower_game_rel_path(target)
    basename = Path(normalized).name
    exact_base = [
        entry
        for entry in base_entries
        if lower_game_rel_path(entry.entry_path) == normalized
    ]
    if exact_base:
        return exact_base[-1]
    basename_base = [
        entry
        for entry in base_entries
        if Path(lower_game_rel_path(entry.entry_path)).name == basename
    ]
    if len({lower_game_rel_path(entry.entry_path) for entry in basename_base}) == 1:
        return basename_base[-1] if basename_base else None
    return get_game_pamt_index(game_dir).find_best(
        target,
        suffix=".paloc",
        require_unique_best=False,
    )


def _read_source(
    source: OverlayInputEntry | PazEntry,
    vanilla_store: VanillaStore,
) -> tuple[bytes, OverlayInputEntry]:
    """读取 source 明文并生成可复用的 overlay 元数据模板。"""
    if isinstance(source, OverlayInputEntry):
        return source.content, source
    vanilla_entry = vanilla_store.ensure_entry_backup(source)
    content, detected_entry = extract_plaintext(vanilla_entry)
    return content, OverlayInputEntry(
        content=b"",
        entry_path=detected_entry.path,
        pamt_dir=derive_pamt_dir(detected_entry.paz_file),
        compression_type=detected_entry.compression_type,
        encrypted=detected_entry.encrypted,
        crypto_filename=Path(detected_entry.path).name,
        resolved_dir_path=detected_entry.resolved_dir_path,
    )


def _with_content(template: OverlayInputEntry, content: bytes) -> OverlayInputEntry:
    """保留目标 PAMT 元数据，仅替换最终 PALOC 明文。"""
    return OverlayInputEntry(
        content=content,
        entry_path=template.entry_path,
        pamt_dir=template.pamt_dir,
        compression_type=template.compression_type,
        encrypted=template.encrypted,
        crypto_filename=template.crypto_filename,
        resolved_dir_path=template.resolved_dir_path,
    )
