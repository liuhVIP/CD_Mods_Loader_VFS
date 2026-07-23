"""事务性文件写入与回滚工具。"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from cdmm.common.constants import GAME_DIR_NAME_LENGTH, META_DIR_NAME
from cdmm.common.constants import PRE_APPLY_SUFFIX
from cdmm.utils.path_utils import fs_rel_path

logger = logging.getLogger(__name__)


class Transaction:
    """使用 staging + rename 的方式提交游戏文件修改。"""

    def __init__(self, game_dir: Path, staging_dir: Path) -> None:
        self._game_dir = game_dir
        self._staging_dir = staging_dir
        self._staged_files: list[str] = []

    def stage_file(self, rel_path: str, data: bytes) -> None:
        """写入暂存文件，rel_path 必须是 game_dir 相对路径。"""
        rel = Path(rel_path)
        if rel.is_absolute():
            raise ValueError(f"stage_file 只接受相对路径：{rel_path}")
        target = self._staging_dir / fs_rel_path(rel_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        if rel_path not in self._staged_files:
            self._staged_files.append(rel_path)

    def stage_file_from_path(self, rel_path: str, source: Path) -> None:
        """把已构建文件复制到暂存区，避免大型 PAZ 全量读入内存。"""
        rel = Path(rel_path)
        if rel.is_absolute():
            raise ValueError(f"stage_file_from_path 只接受相对路径：{rel_path}")
        if not source.is_file():
            raise FileNotFoundError(2, "待物化文件不存在", str(source))
        target = self._staging_dir / fs_rel_path(rel_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if rel_path not in self._staged_files:
            self._staged_files.append(rel_path)

    def commit(self) -> None:
        """将所有暂存文件提交到游戏目录，失败时恢复原文件。"""
        renamed: list[str] = []
        try:
            for rel_path in self._staged_files:
                original = self._game_dir / fs_rel_path(rel_path)
                backup = original.with_name(original.name + PRE_APPLY_SUFFIX)
                if backup.exists():
                    backup.unlink()
                if original.exists():
                    original.rename(backup)
                    renamed.append(rel_path)

            for rel_path in self._staged_files:
                staged = self._staging_dir / fs_rel_path(rel_path)
                target = self._game_dir / fs_rel_path(rel_path)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(staged), str(target))
        except Exception:
            self._rollback(renamed)
            raise

        for rel_path in renamed:
            backup = (self._game_dir / fs_rel_path(rel_path)).with_name(
                Path(rel_path).name + PRE_APPLY_SUFFIX
            )
            if backup.exists():
                backup.unlink()

    def cleanup_staging(self) -> None:
        """删除 staging 目录。"""
        if self._staging_dir.exists():
            shutil.rmtree(self._staging_dir)

    def _rollback(self, renamed: list[str]) -> None:
        """提交失败时还原 .pre-apply 文件。"""
        for rel_path in renamed:
            original = self._game_dir / fs_rel_path(rel_path)
            backup = original.with_name(original.name + PRE_APPLY_SUFFIX)
            if original.exists():
                original.unlink()
            if backup.exists():
                backup.rename(original)
        logger.info("事务回滚完成：%d 个文件", len(renamed))


def recover_interrupted(game_dir: Path) -> int:
    """恢复上次中断提交留下的 .pre-apply 文件。"""
    count = 0
    for backup in _iter_pre_apply_backups(game_dir):
        original = backup.with_name(backup.name.removesuffix(PRE_APPLY_SUFFIX))
        if original.exists():
            original.unlink()
        backup.rename(original)
        count += 1
    return count


def _iter_pre_apply_backups(game_dir: Path) -> list[Path]:
    """只扫描加载器会写入的位置，避免每次 apply 遍历完整游戏目录。"""
    backups: list[Path] = []
    meta_dir = game_dir / META_DIR_NAME
    if meta_dir.is_dir():
        backups.extend(sorted(meta_dir.glob(f"*{PRE_APPLY_SUFFIX}"), key=_path_sort_key))
    for directory in sorted(game_dir.iterdir(), key=_path_sort_key):
        if not _is_numbered_game_dir(directory):
            continue
        backups.extend(sorted(directory.glob(f"*{PRE_APPLY_SUFFIX}"), key=_path_sort_key))
    return backups


def _is_numbered_game_dir(path: Path) -> bool:
    """判断路径是否为四位数字游戏数据目录。"""
    return path.is_dir() and path.name.isdigit() and len(path.name) == GAME_DIR_NAME_LENGTH


def _path_sort_key(path: Path) -> str:
    """统一路径排序，保证恢复顺序稳定。"""
    return path.as_posix().lower()
