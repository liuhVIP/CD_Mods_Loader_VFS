"""Format 3 语义 JSON 转传统 byte patch 的独立加载器桥接层。"""

from __future__ import annotations

import logging
import os
import struct
from pathlib import Path
from typing import Any

from cdmm.archive.pamt import derive_pamt_dir, find_pamt_entry, parse_pamt
from cdmm.common.constants import GAME_DIR_NAME_LENGTH, OVERLAY_PAMT_NAME
from cdmm.common.models import DiscoveredMod, OverlayInputEntry, PazEntry
from cdmm.services.format3_iteminfo_writer import build_iteminfo_prefab_changes
from cdmm.services.json_loader import build_patch_overlay_entries, extract_plaintext
from cdmm.services.scanner import load_json_file
from cdmm.storage.vanilla_store import VanillaStore
from cdmm.utils.path_utils import lower_game_rel_path

logger = logging.getLogger(__name__)

# PABGH 使用 u32 count 的表名，保持与完整管理器 semantic.parser 一致。
UINT_COUNT_TABLES = frozenset(
    {
        "characterappearanceindexinfo",
        "globalstagesequencerinfo",
        "sequencerspawninfo",
        "sheetmusicinfo",
        "spawningpoolautospawninfo",
        "itemuseinfo",
        "terrainregionautospawninfo",
        "textguideinfo",
        "validscheduleaction",
        "stageinfo",
        "questinfo",
        "gimmickeventtableinfo",
        "reviepointinfo",
        "aidialogstringinfo",
        "dialogsetinfo",
        "vibratepatterninfo",
        "platformachievementinfo",
        "levelgimmicksceneobjectinfo",
        "fieldlevelnametableinfo",
        "levelinfo",
        "board",
        "gameplaytrigger",
        "characterchange",
        "materialrelationinfo",
    }
)


def collect_format3_warnings(mods: list[DiscoveredMod]) -> list[str]:
    """扫描阶段提示 Format 3 已进入 apply 尝试链路。"""
    return [f"{mod.name}: 已识别为 Format 3，apply 时将尝试语义转换" for mod in mods]


def build_format3_overlay_entries(
    game_dir: Path,
    mods: list[DiscoveredMod],
    vanilla_store: VanillaStore,
    warnings: list[str],
    errors: list[str],
    base_entries: list[OverlayInputEntry] | None = None,
) -> list[OverlayInputEntry]:
    """把 Format 3 模组转换为 overlay entry。"""
    grouped = build_format3_patch_items(
        game_dir,
        mods,
        vanilla_store,
        warnings,
        errors,
        base_entries,
    )
    return build_patch_overlay_entries(
        game_dir,
        grouped,
        vanilla_store,
        warnings,
        errors,
        base_entries,
    )


def build_format3_patch_items(
    game_dir: Path,
    mods: list[DiscoveredMod],
    vanilla_store: VanillaStore,
    warnings: list[str],
    errors: list[str],
    base_entries: list[OverlayInputEntry] | None = None,
) -> dict[str, list[tuple[DiscoveredMod, dict]]]:
    """生成可复用 JSON byte patch 流程的 Format 3 patch block。"""
    grouped: dict[str, list[tuple[DiscoveredMod, dict]]] = {}
    base_by_entry = {
        entry.entry_path.lower(): entry
        for entry in base_entries or []
    }
    processed = 0
    generated = 0
    skipped = 0

    for mod in mods:
        try:
            target_pairs = _parse_format3_targets(mod.path)
        except Exception as exc:
            errors.append(f"{mod.name}: Format 3 解析失败：{exc}")
            continue
        processed += 1

        for target, intents in target_pairs:
            resolved = _resolve_format3_target(game_dir, target)
            if resolved is None:
                errors.append(f"{mod.name}: Format 3 目标未找到：{target}")
                skipped += len(intents)
                continue
            game_file, body_entry, header_entry = resolved
            if header_entry is None:
                warnings.append(f"{mod.name}: {game_file} 缺少 companion PABGH，已跳过")
                skipped += len(intents)
                continue
            try:
                body_backup = vanilla_store.ensure_entry_backup(body_entry)
                header_backup = vanilla_store.ensure_entry_backup(header_entry)
                vanilla_body, _ = extract_plaintext(body_backup)
                vanilla_header, _ = extract_plaintext(header_backup)
            except Exception as exc:
                errors.append(f"{mod.name}: {game_file} vanilla 提取失败：{exc}")
                skipped += len(intents)
                continue

            # Format 3 是最后一层语义补丁，必须叠加在 loose/JSON 已合成的
            # 基底上，否则同目标 iteminfo.pabgb 会覆盖前面已经生效的模组。
            body_base = base_by_entry.get(body_entry.path.lower())
            header_base = base_by_entry.get(header_entry.path.lower())
            current_body = body_base.content if body_base is not None else vanilla_body
            current_header = header_base.content if header_base is not None else vanilla_header
            changes, skipped_count = _format3_intents_to_changes(
                game_file,
                current_body,
                current_header,
                intents,
            )
            skipped += skipped_count
            if not changes:
                warnings.append(
                    f"{mod.name}: Format 3 没有可应用 intent，已跳过；跳过 {len(intents)} 个"
                )
                continue

            grouped.setdefault(lower_game_rel_path(game_file), []).append(
                (
                    mod,
                    {
                        "game_file": game_file,
                        "changes": changes,
                    },
                )
            )
            generated += len(changes)

    if mods:
        logger.info("Format 3 bridge: 处理 %d 个模组，%d 个生成补丁", processed, generated)
        if skipped:
            logger.info("Format 3 bridge: 跳过 %d 个 intent", skipped)
    return grouped


def _parse_format3_targets(path: Path) -> list[tuple[str, list[dict[str, Any]]]]:
    """解析 Format 3 单目标 target/intents 或新版多目标 targets[]。"""
    data = load_json_file(path)
    if data.get("format") != 3:
        raise ValueError("缺少 format: 3 标记")
    has_single = "target" in data
    has_multi = "targets" in data
    if has_single and has_multi:
        raise ValueError("同时存在 target 和 targets，无法判断目标结构")
    if has_single:
        target = data.get("target")
        intents = data.get("intents")
        if not isinstance(target, str) or not isinstance(intents, list):
            raise ValueError("target/intents 结构无效")
        return [(target, [item for item in intents if isinstance(item, dict)])]
    targets = data.get("targets")
    if not isinstance(targets, list):
        raise ValueError("缺少 target 或 targets")
    pairs: list[tuple[str, list[dict[str, Any]]]] = []
    for index, item in enumerate(targets):
        if not isinstance(item, dict):
            raise ValueError(f"targets[{index}] 不是对象")
        target = item.get("file")
        intents = item.get("intents")
        if not isinstance(target, str) or not isinstance(intents, list):
            raise ValueError(f"targets[{index}] 缺少 file/intents")
        pairs.append((target, [intent for intent in intents if isinstance(intent, dict)]))
    return pairs


def _format3_intents_to_changes(
    game_file: str,
    vanilla_body: bytes,
    vanilla_header: bytes,
    intents: list[dict[str, Any]],
) -> tuple[list[dict], int]:
    """按目标表分发 Format 3 intent 写入器。"""
    table_name = Path(game_file.replace("\\", "/")).stem.lower()
    key_size, offsets = _parse_pabgh_index(vanilla_header, table_name)
    if key_size not in (2, 4) or not offsets:
        return [], len(intents)
    entry_bounds = _build_entry_bounds(vanilla_body, key_size, offsets)
    if table_name == "iteminfo":
        return build_iteminfo_prefab_changes(vanilla_body, key_size, entry_bounds, intents)
    return [], len(intents)


def _build_entry_bounds(
    body: bytes,
    key_size: int,
    offsets: dict[int, int],
) -> dict[int, tuple[int, int, str, int]]:
    """构建 record key -> (entry_start, entry_end, name, name_end)。"""
    bounds: dict[int, tuple[int, int, str, int]] = {}
    sorted_offsets = sorted(offsets.items(), key=lambda item: item[1])
    for index, (key, offset) in enumerate(sorted_offsets):
        entry_end = sorted_offsets[index + 1][1] if index + 1 < len(sorted_offsets) else len(body)
        parsed = _parse_entry_name_end(body, offset, key_size)
        if parsed is None:
            continue
        name, name_end = parsed
        bounds[key] = (offset, entry_end, name, name_end)
    return bounds


def _parse_entry_name_end(body: bytes, entry_offset: int, key_size: int) -> tuple[str, int] | None:
    """解析 entry 名称和 name_end 锚点。"""
    eid_size = 2 if key_size == 2 else 4
    head_size = eid_size + 4
    if entry_offset < 0 or entry_offset + head_size > len(body):
        return None
    name_len = struct.unpack_from("<I", body, entry_offset + eid_size)[0]
    if name_len > 500 or entry_offset + head_size + name_len > len(body):
        return None
    name_start = entry_offset + head_size
    name_end = name_start + name_len
    try:
        return body[name_start:name_end].decode("utf-8"), name_end
    except UnicodeDecodeError:
        return "", name_end


def _parse_pabgh_index(header: bytes, table_name: str) -> tuple[int, dict[int, int]]:
    """解析 PABGH key -> body offset。"""
    count_size = 4 if table_name.lower() in UINT_COUNT_TABLES else 2
    if len(header) < count_size:
        return 0, {}
    count = struct.unpack_from("<I" if count_size == 4 else "<H", header, 0)[0]
    if count <= 0:
        return 0, {}
    total_key_bytes = len(header) - count_size - count * 4
    if total_key_bytes <= 0 or total_key_bytes % count:
        return 0, {}
    key_size = total_key_bytes // count
    offsets: dict[int, int] = {}
    pos = count_size
    for _ in range(count):
        if pos + key_size + 4 > len(header):
            break
        key = int.from_bytes(header[pos:pos + key_size], "little")
        offsets[key] = struct.unpack_from("<I", header, pos + key_size)[0]
        pos += key_size + 4
    return key_size, offsets


def _resolve_format3_target(game_dir: Path, target: str) -> tuple[str, PazEntry, PazEntry | None] | None:
    """优先从原版低编号 PAMT 查找 Format 3 目标，避免误命中旧 overlay。"""
    body_entry = _find_preferred_game_entry(game_dir, target, suffix=".pabgb")
    if body_entry is None:
        return None
    body_path = body_entry.path
    header_target = body_path.rsplit(".", 1)[0] + ".pabgh"
    header_entry = _find_preferred_game_entry(game_dir, header_target, suffix=".pabgh")
    return body_path, body_entry, header_entry


def _find_preferred_game_entry(game_dir: Path, target: str, suffix: str) -> PazEntry | None:
    """查找目标 entry，完整路径优先、gamedata 优先、低编号目录优先。"""
    normalized = lower_game_rel_path(target)
    if not normalized.endswith(suffix):
        normalized += suffix
    basename = os.path.basename(normalized)
    candidates: list[PazEntry] = []
    for directory in sorted(game_dir.iterdir(), key=lambda item: item.name):
        if not _is_numbered_game_dir(directory):
            continue
        pamt_path = directory / OVERLAY_PAMT_NAME
        if not pamt_path.exists():
            continue
        try:
            entries = parse_pamt(pamt_path, paz_dir=directory)
        except Exception as exc:
            logger.warning("跳过无法解析的 PAMT：%s (%s)", pamt_path, exc)
            continue
        for entry in entries:
            entry_path = lower_game_rel_path(entry.path)
            if entry_path == normalized or os.path.basename(entry_path) == basename:
                candidates.append(entry)
    if not candidates:
        # 保留旧查找作为兜底，避免极端路径命名完全不在编号目录扫描中。
        return find_pamt_entry(target, game_dir)
    candidates.sort(key=lambda entry: _format3_entry_score(entry, normalized, basename))
    best = candidates[0]
    if lower_game_rel_path(best.path) != normalized:
        logger.info("按 Format 3 basename 匹配 %s -> %s", target, best.path)
    return best


def _format3_entry_score(entry: PazEntry, normalized: str, basename: str) -> tuple[int, int, int]:
    """Format 3 目标候选排序，避免 overlay/ui 同名文件抢先命中。"""
    entry_path = lower_game_rel_path(entry.path)
    pamt_dir = derive_pamt_dir(entry.paz_file)
    try:
        dir_number = int(pamt_dir)
    except ValueError:
        dir_number = 9999
    exact_score = 0 if entry_path == normalized else 1
    gamedata_score = 0 if entry_path.startswith("gamedata/") else 1
    basename_score = 0 if os.path.basename(entry_path) == basename else 1
    return exact_score, gamedata_score + basename_score, dir_number


def _is_numbered_game_dir(path: Path) -> bool:
    """判断是否为 NNNN 游戏归档目录。"""
    return path.is_dir() and path.name.isdigit() and len(path.name) == GAME_DIR_NAME_LENGTH
