"""把无字幕 Kliff WEM 换成指定来源包的载荷并重新打包。"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from cdmm.services.cdmod_converter import (
    CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE,
    CDMOD_FORMAT_NAME,
    CDMOD_FORMAT_VERSION,
    CDMOD_MANIFEST_PATH,
    _write_cdmod_zip,
)
from cdmm.services.cdmod_package import load_cdmod_package
from cdmm.services.json_loader import extract_plaintext
from cdmm.services.paloc import parse_paloc
from cdmm.services.pamt_index_service import get_game_pamt_index

# 无字幕来源载荷在 replacements.json 中的 origin 标记。
SOURCE_ORIGIN = "cn-nonsubtitle-swap"
KLIFF_PREFIX = "unique_kliff_"
TARGET_PAMT_DIRS = ("0004", "0035")
SUBTITLE_PAMT_CANDIDATES = ("0032", "0033", "0034", "0036")
# 与原版相比允许的时长偏差区间，超出即认为来源载荷会破坏播放。
DURATION_TOLERANCE = (0.5, 1.5)


def _normalize(value: str) -> str:
    return value.replace("\\", "/").strip("/")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def wem_header(data: bytes) -> dict[str, int]:
    """解析 Wwise RIFF 头，读取编码、采样率与声明样本数。"""
    if data[0:4] != b"RIFF":
        raise ValueError("载荷不是 Wwise RIFF")
    offset = 12
    info: dict[str, int] = {}
    while offset + 8 <= len(data):
        chunk_id = data[offset : offset + 4]
        size = struct.unpack_from("<I", data, offset + 4)[0]
        body = offset + 8
        if chunk_id == b"fmt ":
            info["codec"] = struct.unpack_from("<H", data, body)[0]
            info["channels"] = struct.unpack_from("<H", data, body + 2)[0]
            info["sample_rate"] = struct.unpack_from("<I", data, body + 4)[0]
            info["samples"] = struct.unpack_from("<i", data, body + 0x18)[0]
        offset = body + size + (size & 1)
    if "samples" not in info:
        raise ValueError("Wwise RIFF 缺少 fmt chunk")
    return info


def zho_cn_subtitle_keys(index) -> tuple[set[str], str]:
    """读取当前简中拆分 PALOC 的全部 key。"""
    best_dir = ""
    best_count = 0
    for pamt_dir in SUBTITLE_PAMT_CANDIDATES:
        zho_count = sum(
            1
            for entry in index.entries_in_dir(pamt_dir)
            if entry.path.lower().endswith(".paloc")
            and _normalize(entry.resolved_dir_path or "").endswith("zho-cn")
        )
        if zho_count > best_count:
            best_dir, best_count = pamt_dir, zho_count
    if not best_count:
        raise RuntimeError("当前游戏 PAMT 中找不到简中拆分 PALOC 目录")
    keys: set[str] = set()
    for entry in index.entries_in_dir(best_dir):
        if not entry.path.lower().endswith(".paloc"):
            continue
        if not _normalize(entry.resolved_dir_path or "").endswith("zho-cn"):
            continue
        try:
            keys |= set(parse_paloc(extract_plaintext(entry)[0]).by_key())
        except Exception:
            continue
    return keys, best_dir


def has_subtitle(basename: str, keys: set[str]) -> bool:
    """没有 unique_kliff_ 前缀或没有同名简中 key 的语音都视为无字幕。"""
    if not basename.lower().startswith(KLIFF_PREFIX):
        return False
    return Path(basename).stem[len(KLIFF_PREFIX) :] in keys


def target_lookup(index) -> dict[tuple[str, str, str], object]:
    """建立 (pamt_dir, resolved_dir_path, basename) -> PAMT entry 索引。"""
    lookup: dict[tuple[str, str, str], object] = {}
    for pamt_dir in TARGET_PAMT_DIRS:
        for entry in index.entries_in_dir(pamt_dir):
            if not entry.path.lower().endswith(".wem"):
                continue
            resolved = _normalize(entry.resolved_dir_path or "")
            lookup[(pamt_dir, resolved, Path(entry.path).name.lower())] = entry
    return lookup


@dataclass
class SwapEntry:
    """一条被来源载荷替换或新增目标的时长记录。"""

    target: str
    vanilla_seconds: float
    vanilla_rate: int
    base_seconds: float | None
    new_seconds: float
    new_rate: int
    def ratio(self) -> float:
        return self.new_seconds / self.vanilla_seconds if self.vanilla_seconds else 0.0


@dataclass
class SwapReport:
    base_file_count: int = 0
    source_file_count: int = 0
    source_skipped_subtitle: int = 0
    source_unmatched: list[str] = field(default_factory=list)
    replaced: int = 0
    added: int = 0
    kept_subtitle: int = 0
    kept_no_source: list[str] = field(default_factory=list)
    entries: list[SwapEntry] = field(default_factory=list)


def _collect_source_payloads(source_files: Path, lookup, subtitle_keys, report):
    """按当前 PAMT 目标收集来源无字幕载荷。"""
    payloads: dict[tuple[str, str], tuple[bytes, object]] = {}
    for path in sorted(source_files.rglob("*.wem")):
        parts = list(path.relative_to(source_files).parts)
        if len(parts) < 3 or parts[0] not in TARGET_PAMT_DIRS:
            continue
        report.source_file_count += 1
        basename = parts[-1]
        if has_subtitle(basename, subtitle_keys):
            report.source_skipped_subtitle += 1
            continue
        source_dir = "/".join(parts[1:-1])
        entry = lookup.get((parts[0], source_dir, basename.lower()))
        if entry is None:
            report.source_unmatched.append(f"{parts[0]}/{source_dir}/{basename}")
            continue
        target = f"{_normalize(entry.resolved_dir_path)}/{basename}"
        payloads[(parts[0], target)] = (path.read_bytes(), entry)
    return payloads


def _record_swap(report: SwapReport, before: bytes | None, after: bytes, entry) -> None:
    """记录来源载荷相对原版的时长/采样率差异。"""
    try:
        vanilla = wem_header(extract_plaintext(entry)[0])
        current = wem_header(after)
    except Exception:
        return
    base_seconds = None
    if before is not None:
        try:
            previous = wem_header(before)
            base_seconds = previous["samples"] / previous["sample_rate"]
        except Exception:
            base_seconds = None
    report.entries.append(
        SwapEntry(
            target=f"{_normalize(entry.resolved_dir_path)}/{Path(entry.path).name}",
            vanilla_seconds=vanilla["samples"] / vanilla["sample_rate"],
            vanilla_rate=vanilla["sample_rate"],
            base_seconds=base_seconds,
            new_seconds=current["samples"] / current["sample_rate"],
            new_rate=current["sample_rate"],
        )
    )


def build(
    game_dir: Path,
    base_path: Path,
    source_files: Path,
    output_path: Path,
    *,
    game_version: str,
    voice_label: str,
) -> tuple[SwapReport, dict]:
    """把来源无字幕载荷合并进基础包并写出新的 cdmod。"""
    index = get_game_pamt_index(game_dir)
    subtitle_keys, paloc_dir = zho_cn_subtitle_keys(index)
    lookup = target_lookup(index)
    base = load_cdmod_package(base_path)
    base_files = [item for patch in base.file_patches for item in patch.files]
    report = SwapReport(base_file_count=len(base_files))
    source_payloads = _collect_source_payloads(source_files, lookup, subtitle_keys, report)

    entries: dict[tuple[str, str], tuple[bytes, str]] = {}
    for item in base_files:
        key = (item.pamt_dir, item.target)
        if has_subtitle(Path(item.target).name, subtitle_keys):
            entries[key] = (item.content, "base")
            report.kept_subtitle += 1
            continue
        source = source_payloads.get(key)
        if source is None:
            entries[key] = (item.content, "base")
            report.kept_no_source.append(f"{item.pamt_dir}/{item.target}")
            continue
        entries[key] = (source[0], SOURCE_ORIGIN)
        report.replaced += 1
        _record_swap(report, item.content, source[0], source[1])
    for key, (payload, entry) in source_payloads.items():
        if key in entries:
            continue
        entries[key] = (payload, SOURCE_ORIGIN)
        report.added += 1
        _record_swap(report, None, payload, entry)

    documents: dict[str, dict | bytes] = {}
    records: list[dict] = []
    counters: dict[str, int] = {}
    for (pamt_dir, target), (payload, origin) in sorted(entries.items()):
        position = counters.get(pamt_dir, 0)
        counters[pamt_dir] = position + 1
        archive_path = f"assets/{pamt_dir}/{position:04d}_{Path(target).name}"
        documents[archive_path] = payload
        records.append(
            {
                "origin": origin,
                "pamt_dir": pamt_dir,
                "payload": archive_path,
                "sha256": _sha256(payload),
                "size": len(payload),
                "target": target,
            }
        )

    manifest = _base_manifest(base_path)
    manifest["components"] = [
        {
            "file_count": len(records),
            "path": "files/replacements.json",
            "type": CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE,
        }
    ]
    manifest["description"] = (
        f"基于当前 {game_version} PAMT 的 Kliff 女性中文 WEM（音色：{voice_label}）；"
        "有字幕条目保留简中 TTS，无字幕条目改用匹配原版时长的中文来源载荷。"
    )
    manifest["format"] = CDMOD_FORMAT_NAME
    manifest["format_version"] = CDMOD_FORMAT_VERSION
    manifest["id"] = f"kliff-female-voice-chinese-{game_version.replace('.', '-')}-{voice_label}"
    manifest["name"] = f"Kliff 女性中文配音（{voice_label} TTS 更新 {game_version}）"
    manifest["version"] = f"{game_version}.1"
    documents[CDMOD_MANIFEST_PATH] = manifest
    documents["files/replacements.json"] = {"files": records, "schema": 1}
    report_document = _report_document(report, game_version, paloc_dir)
    documents["reports/voice-swap.json"] = report_document
    for name in _base_report_paths(base_path):
        documents[name] = json.loads(_read_zip_member(base_path, name))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_cdmod_zip(output_path, documents)
    return report, {
        "paloc_pamt_dir": paloc_dir,
        "report": report_document,
        "sha256": _sha256(output_path.read_bytes()),
    }


def _report_document(report: SwapReport, game_version: str, paloc_dir: str) -> dict:
    """把替换结果整理成可复核的 JSON 报告。"""
    improved = []
    regressed = []
    out_of_tolerance = []
    for item in report.entries:
        before = item.base_seconds
        delta_before = abs(before - item.vanilla_seconds) if before is not None else None
        delta_after = abs(item.new_seconds - item.vanilla_seconds)
        if delta_before is not None:
            if delta_after + 0.02 < delta_before:
                improved.append(item)
            elif delta_after > delta_before + 0.02:
                regressed.append(item)
        if not DURATION_TOLERANCE[0] <= item.ratio() <= DURATION_TOLERANCE[1]:
            out_of_tolerance.append(item)
    deltas = sorted(
        report.entries,
        key=lambda item: abs(
            (item.base_seconds if item.base_seconds is not None else item.new_seconds)
            - item.vanilla_seconds
        ),
        reverse=True,
    )[:60]

    def describe(item: SwapEntry) -> dict:
        return {
            "target": item.target,
            "vanilla_seconds": round(item.vanilla_seconds, 3),
            "before_seconds": (
                round(item.base_seconds, 3) if item.base_seconds is not None else None
            ),
            "after_seconds": round(item.new_seconds, 3),
            "vanilla_rate": item.vanilla_rate,
            "after_rate": item.new_rate,
        }

    return {
        "schema": 1,
        "game_version": game_version,
        "source_origin": SOURCE_ORIGIN,
        "subtitle_paloc_pamt_dir": paloc_dir,
        "duration_tolerance": list(DURATION_TOLERANCE),
        "summary": {
            "base_file_count": report.base_file_count,
            "source_file_count": report.source_file_count,
            "source_skipped_with_subtitle": report.source_skipped_subtitle,
            "source_target_unmatched": len(report.source_unmatched),
            "replaced_without_subtitle": report.replaced,
            "added_without_subtitle": report.added,
            "kept_with_subtitle": report.kept_subtitle,
            "kept_without_source": len(report.kept_no_source),
            "swapped_source_duration_within_tolerance": len(report.entries) - len(out_of_tolerance),
            "swapped_source_duration_out_of_tolerance": len(out_of_tolerance),
            "swapped_source_sample_rate_changed_vs_vanilla": sum(
                1 for item in report.entries if item.new_rate != item.vanilla_rate
            ),
            "duration_closer_to_vanilla": len(improved),
            "duration_further_from_vanilla": len(regressed),
        },
        "source_target_unmatched_samples": report.source_unmatched[:40],
        "kept_without_source_samples": report.kept_no_source[:40],
        "swapped_out_of_tolerance": [describe(item) for item in out_of_tolerance[:60]],
        "duration_further_from_vanilla_samples": [describe(item) for item in regressed[:60]],
        "largest_base_duration_deltas": [describe(item) for item in deltas],
    }


def verify(output_path: Path, game_dir: Path) -> dict:
    """重新读取成品并核对每个目标都在当前 PAMT 中。"""
    package = load_cdmod_package(output_path)
    lookup = target_lookup(get_game_pamt_index(game_dir))
    files = [item for patch in package.file_patches for item in patch.files]
    missing = [
        f"{item.pamt_dir}/{item.target}"
        for item in files
        if (
            item.pamt_dir,
            _normalize(str(Path(item.target).parent.as_posix())),
            Path(item.target).name.lower(),
        )
        not in lookup
    ]
    return {"file_count": len(files), "missing_targets": missing}


def _base_report_paths(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        return sorted(
            name
            for name in archive.namelist()
            if name.startswith("reports/") and name.endswith(".json")
        )


def _read_zip_member(path: Path, name: str) -> bytes:
    with zipfile.ZipFile(path) as archive:
        return archive.read(name)


def _base_manifest(path: Path) -> dict:
    return json.loads(_read_zip_member(path, CDMOD_MANIFEST_PATH))


def main() -> int:
    parser = argparse.ArgumentParser(description="替换无字幕 Kliff WEM 并重新打包 cdmod")
    parser.add_argument("--game-dir", required=True, type=Path)
    parser.add_argument("--base", required=True, type=Path)
    parser.add_argument("--source-files", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--game-version", default="2.02.00")
    parser.add_argument("--voice-label", default="damian_clone2")
    args = parser.parse_args()
    report, extra = build(
        args.game_dir,
        args.base,
        args.source_files,
        args.output,
        game_version=args.game_version,
        voice_label=args.voice_label,
    )
    check = verify(args.output, args.game_dir)
    summary = extra["report"]["summary"]
    print(f"简中字幕 PALOC：{extra['paloc_pamt_dir']}")
    print(
        f"来源语音文件：{report.source_file_count}"
        f"（有字幕跳过 {report.source_skipped_subtitle}，目标未命中 {len(report.source_unmatched)}）"
    )
    print(
        f"替换无字幕：{report.replaced}；新增无字幕：{report.added}；"
        f"保留有字幕：{report.kept_subtitle}；无来源保留：{len(report.kept_no_source)}"
    )
    print(
        f"来源时长落在容差内：{summary['swapped_source_duration_within_tolerance']}；"
        f"超出容差：{summary['swapped_source_duration_out_of_tolerance']}；"
        f"采样率与原版不同：{summary['swapped_source_sample_rate_changed_vs_vanilla']}"
    )
    print(
        f"时长更接近原版：{summary['duration_closer_to_vanilla']}；"
        f"更偏离原版：{summary['duration_further_from_vanilla']}"
    )
    print(f"成品：{args.output}")
    print(f"成品条目：{check['file_count']}，sha256 {extra['sha256']}")
    if check["missing_targets"]:
        print(f"警告：{len(check['missing_targets'])} 个目标不在当前 PAMT：{check['missing_targets'][:5]}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
