"""翁卡（Oongka）女性配音工作流：语音定位与无字幕静音包。

本工具只读取当前游戏归档，不修改原版 PAZ/PAMT/PALOC；产物写入独立工作目录，
最终以 ``.cdmod`` file-replacement 包交付。

子命令：
  inventory  按当前 PAMT 定位翁卡对白语音与战斗音声库，输出 records.json
  silence    为无字幕条目生成静音 WEM，打包成只含这些条目的 ``.cdmod``

战斗音定位原理（2026-09-21 实测，2.02.00）：
  每个角色的战斗/喘息音有专属 voice 声库，文件名是该角色事件名
  ``vce_effort_<角色>`` 小写后的 FNV-1 哈希十进制值，例如
  ``FNV1("vce_effort_woongka") == 3428473071`` → ``3428473071.bnk``，
  存放在各语言 voice 目录（简中为 ``0035``）。该声库只含 BKHD/HIRC，
  引用的媒体全部外链到 ``0004/sound/windows/media/<语言>/<媒体ID>.wem``。
  用克里夫做对照：``vce_effort_kliff`` 声库 + ``vce_pc_kliff_event`` 声库
  覆盖了第三方 FemaleKliffVoice 包的全部 292 个战斗音，且两者与翁卡集合互斥。
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import struct
import subprocess
import sys
import wave
import xml.sax.saxutils as xml_utils
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from cdmm.archive.pamt import parse_pamt
from cdmm.services.cdmod_converter import _write_cdmod_zip
from cdmm.services.json_loader import extract_plaintext
from cdmm.services.paloc import parse_paloc

DEFAULT_GAME_DIR = Path(r"G:\SteamLibrary\steamapps\common\Crimson Desert")
DEFAULT_WORK_ROOT = Path(r"T:\Ai TTS\oongka_female_voice_chinese_work")
DEFAULT_WWISE = Path(
    r"E:\Wwise_2025.1.10.9233\Authoring\x64\Release\bin\WwiseConsole.exe"
)

CHARACTER = "oongka"
EFFORT_EVENT = "vce_effort_woongka"
STRING_DIR = "0032"
VOICE_DIR = "0035"
MEDIA_DIR = "sound/windows/media/chinese(prc)"
SILENCE_MS = 100
NUMBERED_DIR_RE = __import__("re").compile(r"00\d\d")


def _fnv1_32(value: str) -> int:
    """Wwise 短 ID：名字转小写后的 FNV-1 32 位哈希。"""
    digest = 2166136261
    for byte in value.lower().encode("utf-8"):
        digest = (digest * 16777619) & 0xFFFFFFFF
        digest ^= byte
    return digest


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def game_version(game_dir: Path) -> str:
    raw = (game_dir / "meta" / "0.paver").read_bytes()
    major, minor, patch = struct.unpack_from("<HHH", raw)
    return f"{major}.{minor:02d}.{patch:02d}"


def numbered_entries(game_dir: Path) -> dict[str, list[Any]]:
    return {
        name: parse_pamt(str(game_dir / name / "0.pamt"))
        for name in sorted(os.listdir(game_dir))
        if NUMBERED_DIR_RE.fullmatch(name)
    }


def subtitle_map(entries: dict[str, list[Any]]) -> dict[str, str]:
    """合并简中拆分 PALOC。个别表的当前解析器不支持，跳过即可。"""
    merged: dict[str, str] = {}
    for entry in entries[STRING_DIR]:
        if not entry.path.lower().endswith(".paloc"):
            continue
        try:
            raw, _ = extract_plaintext(entry)
            for key, record in parse_paloc(raw).by_key().items():
                merged.setdefault(key, record.value)
        except Exception:
            continue
    return merged


def _target_of(entry: Any) -> str:
    resolved = (entry.resolved_dir_path or "").strip("/")
    return f"{resolved}/{Path(entry.path).name}" if resolved else entry.path.replace("\\", "/")


def dialogue_targets(entries: dict[str, list[Any]], subtitles: dict[str, str]) -> list[dict[str, Any]]:
    prefix = f"unique_{CHARACTER}_"
    records = []
    for entry in entries[VOICE_DIR]:
        basename = Path(entry.path).name
        if not basename.lower().startswith(prefix) or not basename.lower().endswith(".wem"):
            continue
        key = basename[len(prefix) : -4]
        text = subtitles.get(key)
        records.append(
            {
                "kind": "dialogue",
                "pamt_dir": VOICE_DIR,
                "target": _target_of(entry),
                "wem_name": basename,
                "subtitle_key": key,
                "subtitle": text,
                "action": "tts" if text else "silence",
                "vanilla_size": entry.orig_size,
            }
        )
    return records


def effort_targets(entries: dict[str, list[Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """按角色专属 voice 声库反查战斗/喘息音的媒体 WEM。"""
    bank_name = f"{_fnv1_32(EFFORT_EVENT)}.bnk"
    bank = next(
        (e for e in entries[VOICE_DIR] if Path(e.path).name == bank_name),
        None,
    )
    if bank is None:
        raise RuntimeError(f"当前 {VOICE_DIR} 中找不到翁卡战斗音声库 {bank_name}")

    media = {}
    for entry in entries["0004"]:
        basename = Path(entry.path).name
        if not basename.lower().endswith(".wem") or not basename[:-4].isdigit():
            continue
        if (entry.resolved_dir_path or "").replace("\\", "/") != MEDIA_DIR:
            continue
        media[int(basename[:-4])] = entry
    if not media:
        raise RuntimeError(f"当前归档中找不到语音媒体目录 {MEDIA_DIR}")

    payload, _ = extract_plaintext(bank)
    referenced = {
        struct.unpack_from("<I", payload, offset)[0] for offset in range(len(payload) - 4)
    }
    ids = sorted(referenced & set(media))
    if not ids:
        raise RuntimeError("声库中未解析出任何媒体 ID，格式可能已变化")

    records = [
        {
            "kind": "effort",
            "pamt_dir": "0004",
            "target": _target_of(media[media_id]),
            "wem_name": Path(media[media_id].path).name,
            "subtitle_key": None,
            "subtitle": None,
            "action": "silence",
            "vanilla_size": media[media_id].orig_size,
        }
        for media_id in ids
    ]
    meta = {
        "bank": bank_name,
        "event": EFFORT_EVENT,
        "media_dir": MEDIA_DIR,
        "media_count": len(ids),
        "media_ids": ids,
    }
    return records, meta


def command_inventory(args: argparse.Namespace) -> int:
    game_dir = args.game_dir.resolve()
    work_dir = args.work_dir.resolve()
    version = game_version(game_dir)
    entries = numbered_entries(game_dir)
    subtitles = subtitle_map(entries)

    dialogue = dialogue_targets(entries, subtitles)
    effort, effort_meta = effort_targets(entries)
    records = dialogue + effort

    with_subtitle = sum(1 for item in dialogue if item["action"] == "tts")
    _write_json(work_dir / "records.json", records)
    _write_json(
        work_dir / "workflow.json",
        {
            "schema": 1,
            "game_dir": str(game_dir),
            "game_version": version,
            "paver": (game_dir / "meta" / "0.paver").read_bytes().hex(),
            "character": CHARACTER,
            "effort": effort_meta,
            "dialogue_count": len(dialogue),
            "dialogue_with_subtitle": with_subtitle,
            "dialogue_without_subtitle": len(dialogue) - with_subtitle,
            "silence_count": sum(1 for item in records if item["action"] == "silence"),
        },
    )
    print(f"游戏版本：{version}")
    print(f"对白语音：{len(dialogue)}（有简中字幕 {with_subtitle}，无字幕 {len(dialogue) - with_subtitle}）")
    print(f"战斗音：{effort_meta['media_count']} 个媒体（声库 {effort_meta['bank']}）")
    print(f"静音条目合计：{sum(1 for item in records if item['action'] == 'silence')}")
    print(f"清单已写入：{work_dir / 'records.json'}")
    return 0


def _ensure_silence_wem(work_dir: Path, sample_rate: int, wwise: Path, ms: int) -> Path:
    target = work_dir / "silence" / f"silence_{sample_rate}_{ms}ms.wem"
    if target.exists():
        return target
    source_dir = work_dir / "silence" / f"src_{sample_rate}_{ms}ms"
    source_dir.mkdir(parents=True, exist_ok=True)
    wav_path = source_dir / f"silence_{sample_rate}.wav"
    with wave.open(str(wav_path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(b"\x00\x00" * int(sample_rate * ms / 1000))

    project = work_dir / "wwise" / "silence_convert" / "silence_convert.wproj"
    if not project.exists():
        project.parent.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [str(wwise), "create-new-project", str(project), "--platform", "Windows", "--quiet"],
            check=False,
            capture_output=True,
            text=True,
        )
        if not project.exists():
            raise RuntimeError("Wwise 项目创建失败")

    sources = source_dir / "sources.wsources"
    sources.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<ExternalSourcesList SchemaVersion="1" Root="{xml_utils.escape(str(source_dir))}">\n'
        f'  <Source Path="{xml_utils.escape(wav_path.name)}" Conversion="Vorbis Quality High"/>\n'
        "</ExternalSourcesList>\n",
        encoding="utf-8",
    )
    output_dir = source_dir / "out"
    result = subprocess.run(
        [
            str(wwise), "convert-external-source", str(project),
            "--source-file", str(sources), "--output", "Windows", str(output_dir), "--quiet",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    produced = sorted(output_dir.rglob("*.wem")) if output_dir.exists() else []
    if result.returncode != 0 or not produced:
        raise RuntimeError(f"Wwise 静音转换失败：{result.returncode}")
    target.write_bytes(produced[0].read_bytes())
    return target


def _payload_sample_rate(target: str) -> int:
    """战斗音媒体为 44.1 kHz、对白为 48 kHz，按目标目录选择静音采样率。"""
    return 44100 if "/media/" in target.replace("\\", "/") else 48000


def command_silence(args: argparse.Namespace) -> int:
    work_dir = args.work_dir.resolve()
    records = _read_json(work_dir / "records.json")
    targets = [item for item in records if item["action"] == "silence"]
    if not targets:
        print("没有需要静音的条目")
        return 0
    wwise = args.wwise.resolve()
    if not wwise.is_file():
        raise FileNotFoundError(f"找不到 WwiseConsole.exe：{wwise}")

    cache: dict[int, tuple[Path, bytes]] = {}
    documents: dict[str, Any] = {}
    files = []
    replaced = collections.Counter()
    for index, item in enumerate(targets):
        rate = _payload_sample_rate(item["target"])
        if rate not in cache:
            path = _ensure_silence_wem(work_dir, rate, wwise, args.silence_ms)
            cache[rate] = (path, path.read_bytes())
        _, payload = cache[rate]
        if len(payload) > item["vanilla_size"]:
            raise RuntimeError(
                f"静音载荷 {len(payload)} 字节大于原版 {item['vanilla_size']} 字节：{item['target']}"
            )
        archive_path = f"assets/{item['pamt_dir']}/{index:04d}_{item['wem_name']}"
        documents[archive_path] = payload
        files.append(
            {
                "origin": "oongka-silence",
                "pamt_dir": item["pamt_dir"],
                "payload": archive_path,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size": len(payload),
                "target": item["target"],
                "kind": item["kind"],
            }
        )
        replaced[item["kind"]] += 1

    version = game_version(args.game_dir.resolve())
    documents["files/replacements.json"] = {"schema": 1, "files": files}
    documents["manifest.json"] = {
        "author": "cdmm",
        "components": [
            {"file_count": len(files), "path": "files/replacements.json", "type": "file-replacement"}
        ],
        "dependencies": [],
        "description": (
            f"翁卡女性配音配套：无字幕对白 {replaced['dialogue']} 条与战斗/喘息音 "
            f"{replaced['effort']} 条改为静音 WEM。"
        ),
        "format": "crimson-mod-package",
        "format_version": 1,
        "id": f"oongka-female-voice-silence-{version.replace('.', '-')}",
        "name": f"翁卡女性配音（无字幕静音补充 {version}）",
        "source": {"format": "loose-file-replacement", "game_version": version},
        "version": f"{version}.1",
    }
    documents["reports/silence.json"] = {
        "schema": 1,
        "type": "oongka-voice-silence",
        "game_version": version,
        "silence_ms": args.silence_ms,
        "dialogue_silenced": replaced["dialogue"],
        "effort_silenced": replaced["effort"],
        "total": len(files),
    }
    output = args.output.resolve()
    _write_cdmod_zip(output, documents)
    print(f"已生成：{output}")
    print(f"静音条目：无字幕对白 {replaced['dialogue']}，战斗音 {replaced['effort']}，合计 {len(files)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="翁卡女性配音工作流")
    parser.add_argument("--game-dir", type=Path, default=DEFAULT_GAME_DIR)
    parser.add_argument("--work-root", type=Path, default=DEFAULT_WORK_ROOT)
    parser.add_argument("--wwise", type=Path, default=DEFAULT_WWISE)
    subparsers = parser.add_subparsers(dest="command", required=True)

    inventory = subparsers.add_parser("inventory", help="定位翁卡语音并写出 records.json")
    inventory.set_defaults(handler=command_inventory)

    silence = subparsers.add_parser("silence", help="生成无字幕/战斗音静音 .cdmod")
    silence.add_argument("--work-dir", type=Path, default=None)
    silence.add_argument("--output", type=Path, required=True)
    silence.add_argument("--silence-ms", type=int, default=SILENCE_MS)
    silence.set_defaults(handler=command_silence)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "work_dir", None) is None:
        args.work_dir = args.work_root / game_version(args.game_dir.resolve())
    try:
        return args.handler(args)
    except Exception as exc:  # noqa: BLE001 - CLI 顶层错误提示
        print(f"翁卡配音工作流失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
