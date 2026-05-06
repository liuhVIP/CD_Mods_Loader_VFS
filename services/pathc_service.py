"""DDS loose 文件的 PATHC 注册服务。"""

from __future__ import annotations

import logging
from pathlib import Path

from cdmm.archive.pathc_handler import make_dds_record, read_pathc, serialize_pathc, update_entry
from cdmm.common.constants import DDS_SUFFIX, META_DIR_NAME, PATHC_FILE_NAME
from cdmm.common.models import BuiltOverlayEntry, OverlayInputEntry
from cdmm.storage.vanilla_store import VanillaStore

logger = logging.getLogger(__name__)


def build_pathc_for_overlay(
    game_dir: Path,
    vanilla_store: VanillaStore,
    overlay_inputs: list[OverlayInputEntry],
    built_entries: list[BuiltOverlayEntry],
    warnings: list[str],
) -> bytes | None:
    """根据本轮 overlay DDS entries 重建 meta/0.pathc，失败时返回 None。"""
    dds_sources = {
        item.entry_path.lower(): item
        for item in overlay_inputs
        if item.entry_path.lower().endswith(DDS_SUFFIX)
    }
    if not dds_sources:
        return None

    pathc_rel = f"{META_DIR_NAME}/{PATHC_FILE_NAME}"
    if not vanilla_store.has_file(pathc_rel):
        warnings.append("发现 DDS overlay，但未找到 vanilla meta/0.pathc，已跳过 PATHC 更新")
        return None

    try:
        pathc = read_pathc(vanilla_store.root / META_DIR_NAME / PATHC_FILE_NAME)
    except Exception as exc:
        warnings.append(f"meta/0.pathc 解析失败，已跳过 DDS 注册：{exc}")
        return None

    updated = 0
    added_records = 0
    skipped = 0
    for built in built_entries:
        if not built.entry_path.lower().endswith(DDS_SUFFIX):
            continue
        source = dds_sources.get(built.entry_path.lower())
        if source is None:
            skipped += 1
            continue
        m_values = built.dds_m_values
        if m_values is None or not any(m_values):
            skipped += 1
            warnings.append(f"{built.entry_path}: DDS m-values 为空，已跳过 PATHC 注册")
            continue

        virtual_path = _pathc_virtual_path(built)
        try:
            dds_record = make_dds_record(source.content, pathc.header.dds_record_size)
        except Exception as exc:
            skipped += 1
            warnings.append(f"{built.entry_path}: DDS record 构造失败，已跳过 PATHC 注册：{exc}")
            continue
        if built.dds_last4 and len(dds_record) >= 128:
            record_buffer = bytearray(dds_record)
            import struct

            struct.pack_into("<I", record_buffer, 124, built.dds_last4)
            dds_record = bytes(record_buffer)

        try:
            dds_index = pathc.dds_records.index(dds_record)
        except ValueError:
            pathc.dds_records.append(dds_record)
            dds_index = len(pathc.dds_records) - 1
            added_records += 1
        if update_entry(pathc, virtual_path, dds_index, m_values):
            updated += 1

    if updated == 0 and added_records == 0:
        warnings.append(f"发现 DDS overlay，但 PATHC 未产生变化，跳过写入（skipped={skipped}）")
        return None

    logger.info("PATHC: 更新 %d 条 DDS 映射，新增 %d 条 DDS record，跳过 %d 条", updated, added_records, skipped)
    return serialize_pathc(pathc)


def _pathc_virtual_path(entry: BuiltOverlayEntry) -> str:
    """PATHC 使用完整层级路径，优先使用 overlay PAMT 的 dir_path。"""
    if entry.dir_path:
        return f"/{entry.dir_path.strip('/')}/{entry.filename}"
    return f"/{entry.entry_path.strip('/')}"
