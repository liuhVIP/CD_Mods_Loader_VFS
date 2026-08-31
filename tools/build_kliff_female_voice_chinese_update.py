"""基于当前游戏 PAMT 合并中文 Kliff 女性配音与英文临时回退载荷。"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from cdmm.services.cdmod_converter import (
    CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE,
    CDMOD_FORMAT_NAME,
    CDMOD_FORMAT_VERSION,
    CDMOD_MANIFEST_PATH,
    CDMOD_REPORT_PATH,
    _write_cdmod_zip,
)
from cdmm.services.pamt_index_service import get_game_pamt_index


@dataclass(frozen=True)
class VoiceBuildResult:
    output_path: Path
    package_sha256: str
    file_count: int
    chinese_count: int
    english_fallback_count: int
    skipped_obsolete_count: int
    current_missing_count: int


def build_package(
    game_dir: Path,
    chinese_source: Path,
    english_zip: Path,
    output_path: Path,
) -> VoiceBuildResult:
    """按当前游戏 PAMT 构建中文优先的更新包。"""
    game_dir = game_dir.resolve()
    chinese_source = chinese_source.resolve()
    english_zip = english_zip.resolve()
    output_path = output_path.resolve()
    index = get_game_pamt_index(game_dir)
    current_names = {
        pamt_dir: {
            Path(entry.path).name.lower()
            for entry in index.entries_in_dir(pamt_dir)
            if entry.path.lower().endswith(".wem")
        }
        for pamt_dir in ("0004", "0035")
    }

    selected: dict[tuple[str, str], tuple[str, bytes, str]] = {}
    skipped_obsolete = 0
    for pamt_dir in ("0004", "0035"):
        source_dir = chinese_source / "files" / pamt_dir
        for path in sorted(source_dir.rglob("*.wem")):
            name = path.name.lower()
            if name not in current_names[pamt_dir]:
                skipped_obsolete += 1
                continue
            selected[(pamt_dir, name)] = (name, path.read_bytes(), "chinese")

    with tempfile.TemporaryDirectory(prefix="kliff_voice_update_") as temp_dir:
        extracted = Path(temp_dir)
        with zipfile.ZipFile(english_zip) as archive:
            roots = [
                Path(item)
                for item in archive.namelist()
                if item.endswith("/files/0004/") or item.endswith("/files/0004")
            ]
            if not roots:
                raise ValueError("英文 ZIP 缺少 files/0004 目录")
            archive.extractall(extracted)

        package_root = next(
            (path.parent.parent for path in extracted.rglob("files/0004") if path.is_dir()),
            None,
        )
        if package_root is None:
            raise ValueError("英文 ZIP 无法定位 files 根目录")

        _add_english_fallbacks(
            selected,
            package_root / "files" / "0004",
            current_names["0004"],
            "0004",
            "sound/windows/media/chinese(prc)",
        )
        _add_english_fallbacks(
            selected,
            package_root / "files" / "0006",
            current_names["0035"],
            "0035",
            "sound/windows/chinese(prc)",
        )

    current_kliff_names = {
        Path(entry.path).name.lower()
        for entry in index.entries_in_dir("0035")
        if Path(entry.path).name.lower().startswith("unique_kliff")
        and entry.path.lower().endswith(".wem")
    }
    current_missing = sorted(
        current_kliff_names
        - {name for pamt, name in selected if pamt == "0035"}
    )
    documents: dict[str, dict[str, object] | bytes] = {}
    file_specs: list[dict[str, object]] = []
    chinese_count = 0
    fallback_count = 0
    for index_number, ((pamt_dir, name), (_source_name, content, origin)) in enumerate(
        sorted(selected.items())
    ):
        target_parent = (
            "sound/windows/media/chinese(prc)"
            if pamt_dir == "0004"
            else "sound/windows/chinese(prc)"
        )
        target = f"{target_parent}/{name}"
        payload = f"assets/{pamt_dir}/{index_number:04d}_{name}"
        file_specs.append(
            {
                "target": target,
                "pamt_dir": pamt_dir,
                "payload": payload,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
                "origin": origin,
            }
        )
        documents[payload] = content
        if origin == "chinese":
            chinese_count += 1
        else:
            fallback_count += 1

    manifest = {
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": "kliff-female-voice-chinese-2-00-01",
        "name": "Kliff 女性中文配音（2.00.01 中文优先+英文回退）",
        "version": "2.00.01.1",
        "author": "leebibilili + Banasura720 fallback",
        "description": (
            "当前游戏版本的 Kliff 女性中文配音更新包。保留仍存在的中文语音，"
            "对当前中文 PAMT 新增但暂无中文载荷的语音临时使用英文女性载荷。"
            "仅替换 WEM，不修改字幕或其他数据表。"
        ),
        "dependencies": [],
        "source": {"format": "merged-numbered-loose", "game_version": "2.00.01"},
        "components": [
            {
                "type": CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE,
                "path": "files/replacements.json",
                "file_count": len(file_specs),
            }
        ],
    }
    report = {
        "schema": 1,
        "source": {
            "chinese": str(chinese_source),
            "english_zip": str(english_zip),
            "game_exe": str(game_dir / "bin64" / "CrimsonDesert.exe"),
        },
        "summary": {
            "file_count": len(file_specs),
            "chinese_count": chinese_count,
            "english_fallback_count": fallback_count,
            "skipped_obsolete_count": skipped_obsolete,
            "current_missing_count": len(current_missing),
            "current_missing": current_missing,
        },
        "safety": {
            "target_must_exist_in_current_pamt": True,
            "subtitle_tables_modified": False,
            "english_fallback_is_temporary": True,
        },
    }
    documents["files/replacements.json"] = {"schema": 1, "files": file_specs}
    documents[CDMOD_MANIFEST_PATH] = manifest
    documents[CDMOD_REPORT_PATH] = report
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_cdmod_zip(output_path, documents)
    return VoiceBuildResult(
        output_path=output_path,
        package_sha256=hashlib.sha256(output_path.read_bytes()).hexdigest(),
        file_count=len(file_specs),
        chinese_count=chinese_count,
        english_fallback_count=fallback_count,
        skipped_obsolete_count=skipped_obsolete,
        current_missing_count=len(current_missing),
    )


def _add_english_fallbacks(
    selected: dict[tuple[str, str], tuple[str, bytes, str]],
    source_dir: Path,
    current_names: set[str],
    target_pamt_dir: str,
    _target_parent: str,
) -> None:
    if not source_dir.is_dir():
        raise ValueError(f"英文 ZIP 缺少 files/{source_dir.name} 目录")
    for path in sorted(source_dir.rglob("*.wem")):
        name = path.name.lower()
        key = (target_pamt_dir, name)
        if name in current_names and key not in selected:
            selected[key] = (name, path.read_bytes(), "english-fallback")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-dir", type=Path, required=True)
    parser.add_argument("--chinese-source", type=Path, required=True)
    parser.add_argument("--english-zip", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build_package(
        args.game_dir,
        args.chinese_source,
        args.english_zip,
        args.output,
    )
    print(json.dumps(result.__dict__ | {"output_path": str(result.output_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
