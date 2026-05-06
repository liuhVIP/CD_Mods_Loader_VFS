"""vanilla 文件读取与小型 meta 备份。"""

from __future__ import annotations

import shutil
from pathlib import Path

from cdmm.common.constants import META_DIR_NAME, PAPGT_FILE_NAME, PATHC_FILE_NAME, VANILLA_DIR_NAME, WORK_DIR_NAME
from cdmm.common.models import PazEntry
from cdmm.utils.path_utils import fs_rel_path


class VanillaStore:
    """管理加载器需要读取的原始游戏文件。

    当前加载器只负责加载模组，不负责恢复纯净游戏；因此大型 PAZ/PAMT
    不再复制到 .cdloader/vanilla，避免首次加载生成数 GB 备份并拖慢加载。
    """

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
        """返回原始游戏 entry，不再复制大型 PAZ/PAMT。"""
        return entry

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
        """优先读取小型 meta 备份，不存在时读取游戏源文件。"""
        backup = self.root / fs_rel_path(rel_path)
        if backup.exists():
            return backup.read_bytes()
        return (self.game_dir / fs_rel_path(rel_path)).read_bytes()
