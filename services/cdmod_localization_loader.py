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
from cdmm.services.cdmod_package import CdmodLocalizationPatch, CdmodPackage
from cdmm.services.json_loader import extract_plaintext
from cdmm.services.paloc import parse_paloc, serialize_paloc
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
    for target, patches in patches_by_target.items():
        source = sources_by_target[target]
        content, output_template = _read_source(source, vanilla_store)
        try:
            document = parse_paloc(content)
        except ValueError as exc:
            errors.append(f"cdmod 本地化目标 {target} 解析失败：{exc}")
            continue
        values = {record.key: record.value for record in document.records}
        touched_by: dict[str, str] = {}
        target_failed = False
        applied = 0
        already_applied = 0
        for package, patch in patches:
            for change in patch.changes:
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
            changed_values = {key: values[key] for key in touched_by}
            rebuilt = serialize_paloc(document.replace_values(changed_values))
        except ValueError as exc:
            errors.append(f"cdmod 本地化目标 {target} 重建失败：{exc}")
            continue
        results.append(_with_content(output_template, rebuilt))
        warnings.append(
            f"cdmod 本地化：{target} 应用 {applied} 条，已是目标值 {already_applied} 条"
        )
    return results


def _expand_patch_sources(
    game_dir: Path,
    target: str,
    base_entries: list[OverlayInputEntry],
) -> dict[str, OverlayInputEntry | PazEntry]:
    """展开单目标或语言通配 PALOC，低编号 vanilla 优先。"""
    if "*" not in target:
        source = _resolve_source_entry(game_dir, target, base_entries)
        return {lower_game_rel_path(target): source} if source is not None else {}
    pattern = lower_game_rel_path(target)
    if pattern.count("*") != 1 or not Path(pattern).name.startswith("localizationstring_*"):
        raise ValueError(f"不安全的 PALOC 通配目标：{target}")
    matches: dict[str, OverlayInputEntry | PazEntry] = {}
    for entry in base_entries:
        normalized = lower_game_rel_path(entry.entry_path)
        if fnmatchcase(normalized, pattern):
            matches[normalized] = entry
    for pamt_path in sorted(game_dir.glob("[0-9][0-9][0-9][0-9]/0.pamt")):
        try:
            if b"localizationstring_" not in pamt_path.read_bytes().lower():
                continue
            entries = parse_pamt(pamt_path, pamt_path.parent)
        except (OSError, ValueError):
            continue
        for entry in entries:
            normalized = lower_game_rel_path(entry.path)
            if fnmatchcase(normalized, pattern):
                matches.setdefault(normalized, entry)
    if not matches:
        return matches
    active_language = detect_active_paloc_language(game_dir)
    suffix = f"_{active_language}.paloc"
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
    )
