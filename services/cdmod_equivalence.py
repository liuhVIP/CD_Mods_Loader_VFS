"""验证旧Format 3与cdmod桥接结果的最终overlay字节等价性。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from cdmm.common.models import DiscoveredMod, OverlayInputEntry
from cdmm.services.cdmod_semantic_loader import build_semantic_overlay_entries
from cdmm.services.format3_loader import build_format3_overlay_entries
from cdmm.services.scanner import MOD_TYPE_CDMOD, MOD_TYPE_FORMAT3
from cdmm.storage.vanilla_store import VanillaStore


@dataclass(frozen=True)
class CdmodEquivalenceResult:
    """新旧格式最终overlay比较结果。"""

    equivalent: bool
    original_entries: dict[str, str]
    cdmod_entries: dict[str, str]
    original_warnings: tuple[str, ...]
    cdmod_warnings: tuple[str, ...]
    errors: tuple[str, ...]


def verify_format3_cdmod_equivalence(
    original_format3: Path,
    cdmod_path: Path,
    game_dir: Path,
) -> CdmodEquivalenceResult:
    """在同一vanilla基底上比较旧格式与新格式生成的最终entry字节。"""
    game_dir = game_dir.resolve()
    original_warnings: list[str] = []
    cdmod_warnings: list[str] = []
    original_errors: list[str] = []
    cdmod_errors: list[str] = []
    vanilla_store = VanillaStore(game_dir)
    original_entries = build_format3_overlay_entries(
        game_dir,
        [_discovered_mod(original_format3, MOD_TYPE_FORMAT3)],
        vanilla_store,
        original_warnings,
        original_errors,
    )
    cdmod_entries = build_semantic_overlay_entries(
        game_dir,
        [_discovered_mod(cdmod_path, MOD_TYPE_CDMOD)],
        vanilla_store,
        cdmod_warnings,
        cdmod_errors,
    )

    original_hashes = _entry_hashes(original_entries)
    cdmod_hashes = _entry_hashes(cdmod_entries)
    errors = tuple([*original_errors, *cdmod_errors])
    return CdmodEquivalenceResult(
        equivalent=not errors and original_hashes == cdmod_hashes,
        original_entries=original_hashes,
        cdmod_entries=cdmod_hashes,
        original_warnings=tuple(original_warnings),
        cdmod_warnings=tuple(cdmod_warnings),
        errors=errors,
    )


def _discovered_mod(path: Path, mod_type: str) -> DiscoveredMod:
    """构造只供内存验证使用的扫描结果。"""
    resolved = path.resolve()
    return DiscoveredMod(
        name=resolved.name,
        path=resolved,
        mod_type=mod_type,
        fingerprint=hashlib.sha256(resolved.read_bytes()).hexdigest(),
    )


def _entry_hashes(entries: list[OverlayInputEntry]) -> dict[str, str]:
    """按最终游戏entry路径计算明文字节SHA256。"""
    return {
        entry.entry_path.lower(): hashlib.sha256(entry.content).hexdigest()
        for entry in entries
    }
