"""文件、目录和模组清单指纹工具。"""

from __future__ import annotations

import hashlib
from pathlib import Path

from cdmm.common.models import DiscoveredMod


def fingerprint_path(path: Path) -> str:
    """计算文件或目录的稳定 SHA-256 指纹。"""
    digest = hashlib.sha256()
    if path.is_file():
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
        return digest.hexdigest()

    for item in sorted(p for p in path.rglob("*") if p.is_file()):
        rel = item.relative_to(path).as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(item.read_bytes())
    return digest.hexdigest()


def fingerprint_mods(mods: list[DiscoveredMod]) -> str:
    """计算本次完整 mods 清单的总指纹。"""
    digest = hashlib.sha256()
    for mod in mods:
        digest.update(mod.name.encode("utf-8"))
        digest.update(mod.fingerprint.encode("ascii"))
    return digest.hexdigest()
