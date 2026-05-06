"""vanilla 文件备份与读取。"""

from __future__ import annotations

import shutil
from pathlib import Path

from cdmm.common.constants import META_DIR_NAME, PAPGT_FILE_NAME, PATHC_FILE_NAME, VANILLA_DIR_NAME, WORK_DIR_NAME
from cdmm.common.models import PazEntry
from cdmm.utils.path_utils import fs_rel_path


class VanillaStore:
    """管理独立加载器自己的干净文件备份。"""

    def __init__(self, game_dir: Path) -> None:
        self.game_dir = game_dir
        self.root = game_dir / WORK_DIR_NAME / VANILLA_DIR_NAME

    def ensure_meta_backup(self) -> None:
        """首次运行时备份 PAPGT/PATHC，后续保持不覆盖。"""
        for rel in (f"{META_DIR_NAME}/{PAPGT_FILE_NAME}", f"{META_DIR_NAME}/{PATHC_FILE_NAME}"):
            source = self.game_dir / rel
            if source.exists():
                self.ensure_file_backup(rel)

    def ensure_entry_backup(self, entry: PazEntry) -> PazEntry:
        """确保目标 entry 所在 PAZ/PAMT 已备份，并返回指向备份 PAZ 的 entry。"""
        pamt_dir = Path(entry.paz_file).parent.name
        paz_name = Path(entry.paz_file).name
        self.ensure_file_backup(f"{pamt_dir}/0.pamt")
        self.ensure_file_backup(f"{pamt_dir}/{paz_name}")
        return PazEntry(
            path=entry.path,
            paz_file=str(self.root / pamt_dir / paz_name),
            offset=entry.offset,
            comp_size=entry.comp_size,
            orig_size=entry.orig_size,
            flags=entry.flags,
            paz_index=entry.paz_index,
            encrypted_override=entry.encrypted_override,
        )

    def ensure_file_backup(self, rel_path: str) -> Path:
        """若备份不存在则从游戏目录复制文件。"""
        source = self.game_dir / fs_rel_path(rel_path)
        target = self.root / fs_rel_path(rel_path)
        if not source.exists():
            raise FileNotFoundError(2, "需要备份的游戏文件不存在", str(source))
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        return target

    def has_file(self, rel_path: str) -> bool:
        """判断 vanilla 备份中是否存在文件。"""
        return (self.root / fs_rel_path(rel_path)).exists()

    def read_file(self, rel_path: str) -> bytes:
        """读取 vanilla 备份文件。"""
        return (self.root / fs_rel_path(rel_path)).read_bytes()
