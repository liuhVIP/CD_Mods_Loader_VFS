"""克里夫中文女性配音的字幕清单、VoxCPM2、Wwise 与 cdmod 工作流。

该工具只在独立工作目录中生成文件，不修改游戏 PAZ/PAMT/PALOC。
典型流程：prepare -> generate -> convert -> package。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import xml.sax.saxutils as xml_utils
import zipfile
from pathlib import Path
from typing import Any

# 允许从项目根目录直接执行 ``python tools\...py``。
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from cdmm.services.cdmod_converter import _write_cdmod_zip
from cdmm.services.json_loader import extract_plaintext
from cdmm.services.paloc import parse_paloc
from cdmm.services.pamt_index_service import get_game_pamt_index


DEFAULT_GAME_DIR = Path(r"G:\SteamLibrary\steamapps\common\Crimson Desert")
DEFAULT_BASE_CDMOD = Path(
    r"G:\NppMODdown\crimsondesert\【角色】女性角色配音v1.02.00"
    r"\kliff_female_voice_chinese_2.00.01_enfallback.cdmod"
)
DEFAULT_WORK_DIR = Path(r"T:\Ai TTS\kliff_female_voice_chinese_work\2.00.01")
DEFAULT_TTS_ROOT = Path(r"T:\Ai TTS\yzylauncher-win-voxcpm20-260619")
DEFAULT_WWISE = Path(
    r"E:\Wwise_2025.1.10.9233\Authoring\x64\Release\bin\WwiseConsole.exe"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def clean_tts_text(value: str) -> str:
    """去除 PALOC 控制标签，保留标签中的可朗读名称。"""
    text = value.replace("\r", " ").replace("\n", " ")
    text = re.sub(r"\{StaticInfo:[^{}#]*#([^{}]*)\}", r"\1", text, flags=re.I)
    text = re.sub(r"\{[^{}]*\}", "", text)
    text = re.sub(r"<[^>]*>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _target_for_entry(entry: Any) -> str:
    basename = Path(entry.path).name
    resolved = (entry.resolved_dir_path or "").strip("/")
    return f"{resolved}/{basename}" if resolved else entry.path.replace("\\", "/")


def prepare(args: argparse.Namespace) -> int:
    game_dir = args.game_dir.resolve()
    work_dir = args.work_dir.resolve()
    index = get_game_pamt_index(game_dir)
    # 先完整读取 0035，再查询 PALOC。find_best 会按目标过滤其他 PAMT；
    # 若顺序相反，0035 可能只保留 PALOC 候选而看不到 Kliff WEM。
    pamt_entries = index.entries_in_dir("0035")
    paloc_entry = index.find_best("gamedata/localizationstring_zho-cn.paloc")
    if paloc_entry is None:
        raise RuntimeError("当前游戏 PAMT 中找不到简中 localizationstring PALOC")
    paloc_bytes, _ = extract_plaintext(paloc_entry)
    localization = parse_paloc(paloc_bytes).by_key()

    entries = [
        entry
        for entry in pamt_entries
        if entry.path.lower().endswith(".wem")
        and Path(entry.path).name.lower().startswith("unique_kliff_")
    ]
    by_name: dict[str, Any] = {}
    for entry in entries:
        name = Path(entry.path).name.lower()
        if name in by_name:
            raise RuntimeError(f"0035 中出现重复 Kliff WEM basename：{name}")
        by_name[name] = entry

    records: list[dict[str, Any]] = []
    no_subtitle: list[str] = []
    for name in sorted(by_name):
        key = Path(name).stem.removeprefix("unique_kliff_")
        record = localization.get(key)
        if record is None:
            no_subtitle.append(name)
            continue
        tts_text = clean_tts_text(record.value)
        records.append(
            {
                "id": len(records),
                "pamt_dir": "0035",
                "wem_name": Path(name).name,
                "target": _target_for_entry(by_name[name]),
                "subtitle_key": key,
                "subtitle": record.value,
                "tts_text": tts_text,
                "wav_name": f"{Path(name).stem}.wav",
                "status": "pending" if tts_text else "needs-review",
                "error": None,
            }
        )

    if not records:
        raise RuntimeError("没有找到带简中字幕的 Kliff WEM")
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "wav").mkdir(exist_ok=True)
    (work_dir / "wem").mkdir(exist_ok=True)
    (work_dir / "runs").mkdir(exist_ok=True)
    (work_dir / "tts_input.txt").write_text(
        "\n".join(item["tts_text"] for item in records if item["status"] == "pending") + "\n",
        encoding="utf-8",
    )
    _write_json(work_dir / "records.json", records)
    _write_json(
        work_dir / "workflow.json",
        {
            "schema": 1,
            "game_dir": str(game_dir),
            "game_exe": str(game_dir / "bin64" / "CrimsonDesert.exe"),
            "game_exe_size": (game_dir / "bin64" / "CrimsonDesert.exe").stat().st_size,
            "game_exe_mtime_ns": (game_dir / "bin64" / "CrimsonDesert.exe").stat().st_mtime_ns,
            "paloc_entry": _target_for_entry(paloc_entry),
            "paloc_sha256": hashlib.sha256(paloc_bytes).hexdigest(),
            "pamt_kliff_count": len(entries),
            "subtitle_count": len(records),
            "no_subtitle_count": len(no_subtitle),
            "no_subtitle_wem_names": no_subtitle,
            "reference_audio": None,
            "base_cdmod": str(args.base_cdmod.resolve()),
            "source": "current-game-pamt-and-zho-cn-paloc",
        },
    )
    print(f"清单已生成：{work_dir}")
    print(f"当前 Kliff WEM：{len(entries)}，有字幕：{len(records)}，无字幕：{len(no_subtitle)}")
    print(f"待生成 TTS：{sum(item['status'] == 'pending' for item in records)}")
    return 0


def _ensure_reference_wav(args: argparse.Namespace, work_dir: Path) -> Path:
    output = work_dir / "reference.wav"
    source = args.reference_audio.resolve()
    if output.exists() and output.stat().st_mtime_ns >= source.stat().st_mtime_ns:
        return output
    ffmpeg = args.ffmpeg.resolve()
    result = subprocess.run(
        [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
         "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(output)],
        check=False,
    )
    if result.returncode != 0 or not output.exists():
        raise RuntimeError("参考音频转换为 reference.wav 失败")
    return output


def generate(args: argparse.Namespace) -> int:
    work_dir = args.work_dir.resolve()
    records = _read_json(work_dir / "records.json")
    reference = _ensure_reference_wav(args, work_dir)
    pending = [item for item in records if item["status"] in {"pending", "tts-failed"}]
    if args.limit:
        pending = pending[: args.limit]
    if not pending:
        print("没有待生成的 TTS 条目")
        return 0
    run_id = len(list((work_dir / "runs").glob("tts-*"))) + 1
    run_dir = work_dir / "runs" / f"tts-{run_id:04d}"
    run_dir.mkdir(parents=True)
    input_path = run_dir / "input.txt"
    input_path.write_text("\n".join(item["tts_text"] for item in pending) + "\n", encoding="utf-8")
    output_dir = run_dir / "output"
    command = [
        str(args.voxcpm_python.resolve()), "-m", "voxcpm.cli", "batch", "--input", str(input_path),
        "--output-dir", str(output_dir), "--reference-audio", str(reference),
        "--model-path", str(args.model_path.resolve()), "--local-files-only",
        "--inference-timesteps", str(args.inference_timesteps), "--cfg-value", str(args.cfg_value),
        "--normalize",
    ]
    with (run_dir / "command.txt").open("w", encoding="utf-8") as stream:
        stream.write(subprocess.list2cmdline(command) + "\n")
    tts_python_root = args.voxcpm_python.resolve().parent
    tts_app_root = tts_python_root.parents[1]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(tts_app_root / "src")
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=tts_app_root,
        env=environment,
    )
    (run_dir / "stdout.log").write_text(result.stdout or "", encoding="utf-8")
    (run_dir / "stderr.log").write_text(result.stderr or "", encoding="utf-8")
    by_index = {index: item for index, item in enumerate(pending, 1)}
    for index, item in by_index.items():
        source = output_dir / f"output_{index:03d}.wav"
        destination = work_dir / "wav" / item["wav_name"]
        if source.exists() and source.stat().st_size > 44:
            shutil.copy2(source, destination)
            item["status"] = "wav-generated"
            item["error"] = None
            item["wav_sha256"] = _sha256(destination)
        else:
            item["status"] = "tts-failed"
            item["error"] = f"未找到 {source.name}"
    _write_json(work_dir / "records.json", records)
    succeeded = sum(item["status"] == "wav-generated" for item in pending)
    print(f"TTS 批次完成：{succeeded}/{len(pending)}，返回码：{result.returncode}")
    return 0 if succeeded else 1


def _create_wwise_project(wwise: Path, project: Path) -> None:
    if project.exists():
        return
    # Wwise 会自行创建与项目同名的目录；提前创建该目录会被判定为项目已存在。
    project.parent.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run([str(wwise), "create-new-project", str(project), "--platform", "Windows", "--quiet"], check=False)
    if result.returncode != 0 or not project.exists():
        raise RuntimeError("Wwise 项目创建失败")


def convert(args: argparse.Namespace) -> int:
    work_dir = args.work_dir.resolve()
    records = _read_json(work_dir / "records.json")
    wav_records = [item for item in records if item["status"] in {"wav-generated", "wem-failed"}]
    if args.limit:
        wav_records = wav_records[: args.limit]
    if not wav_records:
        print("没有待转换的 WAV")
        return 0
    wwise = args.wwise.resolve()
    if not wwise.is_file():
        raise FileNotFoundError(f"找不到 WwiseConsole.exe：{wwise}")
    project = work_dir / "wwise" / "kliff_voice_convert" / "kliff_voice_convert.wproj"
    _create_wwise_project(wwise, project)
    wav_dir = work_dir / "wav"
    wem_dir = work_dir / "wem"
    wem_dir.mkdir(parents=True, exist_ok=True)
    for offset in range(0, len(wav_records), args.chunk_size):
        batch = wav_records[offset : offset + args.chunk_size]
        batch_dir = work_dir / "wwise" / f"batch-{offset // args.chunk_size:04d}"
        batch_dir.mkdir(parents=True, exist_ok=True)
        wsources = batch_dir / "sources.wsources"
        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            f'<ExternalSourcesList SchemaVersion="1" Root="{xml_utils.escape(str(wav_dir))}">',
        ]
        for item in batch:
            lines.append(f'  <Source Path="{xml_utils.escape(item["wav_name"])}" Conversion="Vorbis Quality High"/>')
        lines.append("</ExternalSourcesList>")
        wsources.write_text("\n".join(lines) + "\n", encoding="utf-8")
        output_dir = batch_dir / "out"
        result = subprocess.run(
            [str(wwise), "convert-external-source", str(project), "--source-file", str(wsources),
             "--output", "Windows", str(output_dir), "--quiet"],
            check=False, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        (batch_dir / "stdout.log").write_text(result.stdout or "", encoding="utf-8")
        (batch_dir / "stderr.log").write_text(result.stderr or "", encoding="utf-8")
        produced = {path.stem.lower(): path for path in output_dir.rglob("*.wem")} if output_dir.exists() else {}
        for item in batch:
            source = produced.get(Path(item["wav_name"]).stem.lower())
            destination = wem_dir / item["wem_name"]
            if source is None:
                item["status"] = "wem-failed"
                item["error"] = f"Wwise 未生成 {item['wem_name']}"
                continue
            shutil.copy2(source, destination)
            item["status"] = "wem-generated"
            item["error"] = None
            item["wem_sha256"] = _sha256(destination)
        _write_json(work_dir / "records.json", records)
        print(f"Wwise 转换进度：{min(offset + len(batch), len(wav_records))}/{len(wav_records)}")
    succeeded = sum(item["status"] == "wem-generated" for item in wav_records)
    print(f"WEM 转换完成：{succeeded}/{len(wav_records)}")
    return 0 if succeeded else 1


def package(args: argparse.Namespace) -> int:
    work_dir = args.work_dir.resolve()
    records = _read_json(work_dir / "records.json")
    generated = [item for item in records if item["status"] == "wem-generated"]
    if not generated:
        raise RuntimeError("没有可打包的 WEM")
    with zipfile.ZipFile(args.base_cdmod.resolve()) as archive:
        documents: dict[str, dict[str, Any] | bytes] = {
            info.filename: archive.read(info.filename)
            for info in archive.infolist()
            if not info.is_dir()
        }
    replacements = json.loads(documents["files/replacements.json"].decode("utf-8"))
    specs = replacements["files"]
    by_key = {(item.get("pamt_dir"), Path(item["target"]).name.lower()): item for item in specs}
    replaced = 0
    appended = 0
    is_damian_clone2 = "damian_clone2" in str(work_dir).casefold()
    voice_label = "达米安 clone_2 克隆音色" if is_damian_clone2 else "女-阅历姐姐"
    origin_label = "tts-damian-clone2" if is_damian_clone2 else "tts-female-yueli-jiejie"
    for item in generated:
        spec = by_key.get((item["pamt_dir"], item["wem_name"].lower()))
        if spec is None:
            # 旧基础包可能早于当前 PAMT，新增字幕语音需要追加完整替换条目。
            payload = f"assets/{item['pamt_dir']}/tts_{Path(item['wem_name']).stem}.wem"
            spec = {
                "target": item["target"],
                "pamt_dir": item["pamt_dir"],
                "payload": payload,
                "sha256": "",
                "size": 0,
                "origin": origin_label,
            }
            specs.append(spec)
            by_key[(item["pamt_dir"], item["wem_name"].lower())] = spec
            appended += 1
        payload = (work_dir / "wem" / item["wem_name"]).read_bytes()
        documents[spec["payload"]] = payload
        spec["sha256"] = hashlib.sha256(payload).hexdigest()
        spec["size"] = len(payload)
        spec["origin"] = origin_label
        spec["subtitle_key"] = item["subtitle_key"]
        replaced += 1
    manifest = json.loads(documents["manifest.json"].decode("utf-8"))
    manifest.update({
        "id": "kliff-female-voice-chinese-2-00-01-tts-damian-clone2" if is_damian_clone2 else "kliff-female-voice-chinese-2-00-01-tts-yueli-jiejie",
        "name": f"Kliff 女性中文配音（{voice_label} TTS 更新）",
        "version": "2.00.01.3" if is_damian_clone2 else "2.00.01.2",
        "description": f"基于当前 2.00.01 简中字幕重新生成的 Kliff 女性中文 WEM（音色：{voice_label}）；无字幕 key 保留基础包资源。",
    })
    for component in manifest.get("components", []):
        if component.get("type") == "file-replacement":
            component["file_count"] = len(specs)
    report = {
        "schema": 1,
        "type": "kliff-voice-tts-update",
        "base_cdmod": str(args.base_cdmod.resolve()),
        "work_dir": str(work_dir),
        "voice": voice_label,
        "subtitle_replaced_count": replaced,
        "appended_new_target_count": appended,
        "total_wem_count": len(specs),
        "unregenerated_count": len(specs) - replaced,
        "subtitle_tables_modified": False,
        "game_version": "2.00.01",
    }
    documents["files/replacements.json"] = replacements
    documents["manifest.json"] = manifest
    documents["reports/tts-update.json"] = report
    _write_cdmod_zip(args.output.resolve(), documents)
    print(f"新 cdmod 已生成：{args.output.resolve()}")
    print(f"重新生成并替换：{replaced} 个 WEM；基础包其余资源保持不变")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    common.add_argument("--game-dir", type=Path, default=DEFAULT_GAME_DIR)
    common.add_argument("--base-cdmod", type=Path, default=DEFAULT_BASE_CDMOD)

    p = sub.add_parser("prepare", parents=[common])
    p.set_defaults(func=prepare)

    p = sub.add_parser("generate", parents=[common])
    p.add_argument("--reference-audio", type=Path, default=DEFAULT_TTS_ROOT / "win-unpacked/python/voices/女-阅历姐姐.mp3")
    p.add_argument("--ffmpeg", type=Path, default=DEFAULT_TTS_ROOT / "win-unpacked/python/ffmpeg/bin/ffmpeg.exe")
    p.add_argument("--voxcpm-python", type=Path, default=DEFAULT_TTS_ROOT / "win-unpacked/python/build_venv/python/python.exe")
    p.add_argument("--model-path", type=Path, default=DEFAULT_TTS_ROOT / "win-unpacked/python/models/VoxCPM2")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--inference-timesteps", type=int, default=10)
    p.add_argument("--cfg-value", type=float, default=2.0)
    p.set_defaults(func=generate)

    p = sub.add_parser("convert", parents=[common])
    p.add_argument("--wwise", type=Path, default=DEFAULT_WWISE)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--chunk-size", type=int, default=100)
    p.set_defaults(func=convert)

    p = sub.add_parser("package", parents=[common])
    p.add_argument("--output", type=Path, required=True)
    p.set_defaults(func=package)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.func(args)
    except (OSError, ValueError, KeyError, RuntimeError, zipfile.BadZipFile) as exc:
        print(f"Kliff TTS 工作流失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
