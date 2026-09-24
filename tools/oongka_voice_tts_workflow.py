"""翁卡（Oongka）女性中文配音 TTS 工作流。

本工具只读取当前游戏归档，以及 ``oongka_voice_workflow.py inventory`` 写出的
``records.json`` 清单；TTS WAV、WEM 与最终 ``.cdmod`` 全部落在独立工作目录，
不修改游戏原始 PAZ/PAMT/PALOC。

子命令：
  prepare   由 inventory 清单生成 TTS 清单 tts_records.json
  generate  VoxCPM2 批量合成女声 WAV
  convert   Wwise 批量转 Vorbis WEM
  package   打包为 file-replacement ``.cdmod``（可与无字幕静音包合并）
  verify    逐条复核成品包对当前 PAMT 的命中情况

典型顺序：prepare -> generate -> convert -> package -> verify
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
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
from cdmm.services.pamt_index_service import get_game_pamt_index

DEFAULT_GAME_DIR = Path(r"G:\SteamLibrary\steamapps\common\Crimson Desert")
DEFAULT_WORK_DIR = Path(r"T:\Ai TTS\oongka_female_voice_chinese_work\2.02.00")
DEFAULT_REFERENCE_AUDIO = DEFAULT_WORK_DIR / "ref_voices" / "C1_奈拉clone1_5.60s.wav"
DEFAULT_SILENCE_CDMOD = DEFAULT_WORK_DIR / "oongka_female_voice_silence_2.02.00.cdmod"
DEFAULT_TTS_ROOT = Path(r"T:\Ai TTS\yzylauncher-win-voxcpm20-260619")
DEFAULT_WWISE = Path(
    r"E:\Wwise_2025.1.10.9233\Authoring\x64\Release\bin\WwiseConsole.exe"
)

VOICE_LABEL = "奈拉 clone_1 克隆音色"
ORIGIN = "oongka-tts-nairah-clone1"
WEM_MAGIC = b"RIFF"
WAV_HEADER_SIZE = 44
# 语音包只能替换资源，禁止携带表、归档或 meta；这些后缀一旦出现必须报错。
FORBIDDEN_TARGET_SUFFIXES = (
    ".paloc",
    ".pabgb",
    ".pabgh",
    ".papgt",
    ".pathc",
    ".pamt",
    ".paz",
    ".staticinfobody",
    ".staticinfoheader",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def game_version(game_dir: Path) -> str:
    raw = (game_dir / "meta" / "0.paver").read_bytes()
    major, minor, patch = struct.unpack_from("<HHH", raw)
    return f"{major}.{minor:02d}.{patch:02d}"


def clean_tts_text(value: str) -> str:
    """去除 PALOC 控制标签，保留标签中的可朗读名称。

    ``{Staticinfo:Knowledge:Knowledge_Kliff#克里夫}`` 这类占位必须还原成
    ``克里夫``，否则 TTS 会把整段标签念出来。
    """
    text = value.replace("\r", " ").replace("\n", " ")
    text = re.sub(r"\{StaticInfo:[^{}#]*#([^{}]*)\}", r"\1", text, flags=re.I)
    text = re.sub(r"\{[^{}]*\}", "", text)
    text = re.sub(r"<[^>]*>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def build_tts_records(
    inventory: list[dict[str, Any]],
    previous: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """把 inventory 清单中 ``action == "tts"`` 的条目转成 TTS 状态记录。

    已存在且 ``tts_text`` 未变的记录会保留原有状态与哈希，保证 generate 可重复
    执行而不会重跑成功的条目。
    """
    done = {
        (item["pamt_dir"], item["wem_name"].lower()): item
        for item in (previous or [])
        if isinstance(item, dict) and item.get("wem_name")
    }
    records: list[dict[str, Any]] = []
    for item in inventory:
        if item.get("action") != "tts":
            continue
        wem_name = item["wem_name"]
        subtitle = item.get("subtitle") or ""
        text = clean_tts_text(subtitle)
        record = {
            "id": len(records),
            "pamt_dir": item["pamt_dir"],
            "target": item["target"],
            "wem_name": wem_name,
            "wav_name": f"{Path(wem_name).stem}.wav",
            "subtitle_key": item.get("subtitle_key"),
            "subtitle": subtitle,
            "tts_text": text,
            "vanilla_size": item.get("vanilla_size"),
            "status": "pending" if text else "needs-review",
            "error": None,
            "wav_sha256": None,
            "wem_sha256": None,
        }
        old = done.get((item["pamt_dir"], wem_name.lower()))
        if old is not None and old.get("tts_text") == text:
            for key in ("status", "error", "wav_sha256", "wem_sha256"):
                record[key] = old.get(key)
            if not text:
                record["status"] = "needs-review"
        records.append(record)
    return records


def group_pending(
    pending: list[dict[str, Any]], dedupe: bool
) -> list[tuple[str, list[dict[str, Any]]]]:
    """按 ``tts_text`` 分组，返回 (文本, 记录列表) 顺序表。

    ``dedupe`` 开启时，文本完全相同的多条语音共用一次 TTS 生成，省算力并保证
    同句同音；关闭时每条记录各自生成一次。
    """
    if not dedupe:
        return [(item["tts_text"], [item]) for item in pending]
    entries: list[tuple[str, list[dict[str, Any]]]] = []
    index_by_text: dict[str, int] = {}
    for item in pending:
        text = item["tts_text"]
        position = index_by_text.get(text)
        if position is None:
            index_by_text[text] = len(entries)
            entries.append((text, [item]))
        else:
            entries[position][1].append(item)
    return entries


def command_prepare(args: argparse.Namespace) -> int:
    work_dir = args.work_dir.resolve()
    source = args.records.resolve()
    if not source.is_file():
        raise FileNotFoundError(
            f"找不到 inventory 清单 {source}；请先运行 "
            "tools\\oongka_voice_workflow.py inventory"
        )
    inventory = _read_json(source)
    target = work_dir / "tts_records.json"
    previous = _read_json(target) if target.is_file() else None
    records = build_tts_records(inventory, previous)
    _write_json(target, records)
    pending = sum(item["status"] in {"pending", "tts-failed"} for item in records)
    review = sum(item["status"] == "needs-review" for item in records)
    reused = sum(len(group) - 1 for _, group in group_pending(records, True))
    _write_json(
        work_dir / "workflow.json",
        {
            "schema": 1,
            "character": "oongka",
            "game_dir": str(args.game_dir.resolve()),
            "game_version": game_version(args.game_dir.resolve()),
            "inventory": str(source),
            "voice": VOICE_LABEL,
            "reference_audio": str(args.reference_audio.resolve()),
            "tts_total": len(records),
            "tts_pending": pending,
            "tts_needs_review": review,
            "dedupe_saving": reused,
        },
    )
    print(f"TTS 清单已写入：{target}")
    print(f"有字幕对白：{len(records)}（待生成 {pending}，需人工处理 {review}）")
    print(f"完全重复文本可复用：{reused} 条，实际生成次数 {len(records) - reused}")
    return 0


def _ensure_reference_wav(args: argparse.Namespace, work_dir: Path) -> Path:
    output = work_dir / "reference.wav"
    source = args.reference_audio.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"找不到参考音频：{source}")
    if output.exists() and output.stat().st_mtime_ns >= source.stat().st_mtime_ns:
        return output
    ffmpeg = args.ffmpeg.resolve()
    result = subprocess.run(
        [
            str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(source),
            "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(output),
        ],
        check=False,
    )
    if result.returncode != 0 or not output.exists():
        raise RuntimeError("参考音频转换为 reference.wav 失败")
    return output


def command_generate(args: argparse.Namespace) -> int:
    work_dir = args.work_dir.resolve()
    records_path = work_dir / "tts_records.json"
    records = _read_json(records_path)
    reference = _ensure_reference_wav(args, work_dir)
    pending = [
        item
        for item in records
        if item["status"] in {"pending", "tts-failed"} and item["tts_text"]
    ]
    if args.limit:
        pending = pending[: args.limit]
    if not pending:
        print("没有待生成的 TTS 条目")
        return 0
    entries = group_pending(pending, not args.no_dedupe)
    run_id = len(list((work_dir / "runs").glob("tts-*"))) + 1
    run_dir = work_dir / "runs" / f"tts-{run_id:04d}"
    run_dir.mkdir(parents=True)
    input_path = run_dir / "input.txt"
    input_path.write_text(
        "\n".join(text for text, _ in entries) + "\n", encoding="utf-8"
    )
    output_dir = run_dir / "output"
    command = [
        str(args.voxcpm_python.resolve()), "-m", "voxcpm.cli", "batch",
        "--input", str(input_path),
        "--output-dir", str(output_dir),
        "--reference-audio", str(reference),
        "--model-path", str(args.model_path.resolve()),
        "--local-files-only",
        "--inference-timesteps", str(args.inference_timesteps),
        "--cfg-value", str(args.cfg_value),
        "--normalize",
    ]
    (run_dir / "command.txt").write_text(
        subprocess.list2cmdline(command) + "\n", encoding="utf-8"
    )
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

    wav_dir = work_dir / "wav"
    wav_dir.mkdir(parents=True, exist_ok=True)
    succeeded = 0
    for index, (_, group) in enumerate(entries, 1):
        source = output_dir / f"output_{index:03d}.wav"
        if not source.exists() or source.stat().st_size <= WAV_HEADER_SIZE:
            for item in group:
                item["status"] = "tts-failed"
                item["error"] = f"未找到 {source.name}"
            continue
        digest = _sha256(source)
        for item in group:
            destination = wav_dir / item["wav_name"]
            shutil.copy2(source, destination)
            item["status"] = "wav-generated"
            item["error"] = None
            item["wav_sha256"] = digest
            succeeded += 1
    _write_json(records_path, records)
    print(
        f"TTS 批次完成：{succeeded}/{len(pending)} 条"
        f"（{len(entries)} 次生成），返回码：{result.returncode}"
    )
    print(f"输出目录：{wav_dir}")
    print(f"日志目录：{run_dir}")
    return 0 if succeeded else 1


def _create_wwise_project(wwise: Path, project: Path) -> None:
    if project.exists():
        return
    # Wwise 会自行创建与项目同名的目录；提前创建该目录会被判定为项目已存在。
    project.parent.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [str(wwise), "create-new-project", str(project), "--platform", "Windows", "--quiet"],
        check=False,
    )
    if result.returncode != 0 or not project.exists():
        raise RuntimeError("Wwise 项目创建失败")


def command_convert(args: argparse.Namespace) -> int:
    work_dir = args.work_dir.resolve()
    records_path = work_dir / "tts_records.json"
    records = _read_json(records_path)
    converting = [
        item for item in records if item["status"] in {"wav-generated", "wem-failed"}
    ]
    if args.limit:
        converting = converting[: args.limit]
    if not converting:
        print("没有待转换的 WAV")
        return 0
    wwise = args.wwise.resolve()
    if not wwise.is_file():
        raise FileNotFoundError(f"找不到 WwiseConsole.exe：{wwise}")
    project = work_dir / "wwise" / "oongka_voice_convert" / "oongka_voice_convert.wproj"
    _create_wwise_project(wwise, project)
    wav_dir = work_dir / "wav"
    wem_dir = work_dir / "wem"
    wem_dir.mkdir(parents=True, exist_ok=True)
    for offset in range(0, len(converting), args.chunk_size):
        batch = converting[offset : offset + args.chunk_size]
        batch_dir = work_dir / "wwise" / f"batch-{offset // args.chunk_size:04d}"
        batch_dir.mkdir(parents=True, exist_ok=True)
        sources = batch_dir / "sources.wsources"
        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            f'<ExternalSourcesList SchemaVersion="1" Root="{xml_utils.escape(str(wav_dir))}">',
        ]
        for item in batch:
            lines.append(
                f'  <Source Path="{xml_utils.escape(item["wav_name"])}"'
                ' Conversion="Vorbis Quality High"/>'
            )
        lines.append("</ExternalSourcesList>")
        sources.write_text("\n".join(lines) + "\n", encoding="utf-8")
        output_dir = batch_dir / "out"
        result = subprocess.run(
            [
                str(wwise), "convert-external-source", str(project),
                "--source-file", str(sources), "--output", "Windows",
                str(output_dir), "--quiet",
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        (batch_dir / "stdout.log").write_text(result.stdout or "", encoding="utf-8")
        (batch_dir / "stderr.log").write_text(result.stderr or "", encoding="utf-8")
        produced = (
            {path.stem.lower(): path for path in output_dir.rglob("*.wem")}
            if output_dir.exists()
            else {}
        )
        for item in batch:
            source = produced.get(Path(item["wav_name"]).stem.lower())
            if source is None:
                item["status"] = "wem-failed"
                item["error"] = f"Wwise 未生成 {item['wem_name']}"
                continue
            destination = wem_dir / item["wem_name"]
            shutil.copy2(source, destination)
            item["status"] = "wem-generated"
            item["error"] = None
            item["wem_sha256"] = _sha256(destination)
        _write_json(records_path, records)
        print(f"Wwise 转换进度：{min(offset + len(batch), len(converting))}/{len(converting)}")
    succeeded = sum(item["status"] == "wem-generated" for item in converting)
    print(f"WEM 转换完成：{succeeded}/{len(converting)}")
    print(f"输出目录：{wem_dir}")
    return 0 if succeeded else 1


def _load_silence_files(silence_cdmod: Path) -> list[tuple[dict[str, Any], bytes]]:
    with zipfile.ZipFile(silence_cdmod) as archive:
        replacements = json.loads(archive.read("files/replacements.json").decode("utf-8"))
        return [(spec, archive.read(spec["payload"])) for spec in replacements["files"]]


def command_package(args: argparse.Namespace) -> int:
    work_dir = args.work_dir.resolve()
    records = _read_json(work_dir / "tts_records.json")
    generated = [item for item in records if item["status"] == "wem-generated"]
    if not generated:
        raise RuntimeError("没有可打包的 WEM，请先执行 generate 与 convert")

    documents: dict[str, Any] = {}
    files: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in generated:
        payload_path = work_dir / "wem" / item["wem_name"]
        payload = payload_path.read_bytes()
        if not payload.startswith(WEM_MAGIC):
            raise RuntimeError(f"{payload_path} 不是 WEM 数据")
        archive_path = f"assets/{item['pamt_dir']}/tts_{item['wem_name']}"
        documents[archive_path] = payload
        seen.add((item["pamt_dir"], item["wem_name"].lower()))
        files.append(
            {
                "origin": ORIGIN,
                "pamt_dir": item["pamt_dir"],
                "payload": archive_path,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size": len(payload),
                "target": item["target"],
                "subtitle_key": item["subtitle_key"],
                "voice": VOICE_LABEL,
            }
        )

    silenced = 0
    if args.include_silence:
        silence_cdmod = args.silence_cdmod.resolve()
        if not silence_cdmod.is_file():
            raise FileNotFoundError(f"找不到静音包：{silence_cdmod}")
        for spec, payload in _load_silence_files(silence_cdmod):
            key = (str(spec["pamt_dir"]), Path(str(spec["target"])).name.lower())
            if key in seen:
                raise RuntimeError(
                    f"TTS 与静音包出现同一目标：{spec['pamt_dir']}/{spec['target']}"
                )
            documents[spec["payload"]] = payload
            files.append(spec)
            silenced += 1

    version = game_version(args.game_dir.resolve())
    tts_count = len(files) - silenced
    documents["files/replacements.json"] = {"schema": 1, "files": files}
    documents["manifest.json"] = {
        "author": "cdmm",
        "components": [
            {
                "file_count": len(files),
                "path": "files/replacements.json",
                "type": "file-replacement",
            }
        ],
        "dependencies": [],
        "description": (
            f"翁卡（Oongka）性转中文配音：{tts_count} 条有字幕对白使用 TTS 女声"
            f"（音色：{VOICE_LABEL}）；无字幕对白与战斗/喘息音共 {silenced} 条静音。"
        ),
        "format": "crimson-mod-package",
        "format_version": 1,
        "id": f"oongka-female-voice-chinese-{version.replace('.', '-')}",
        "name": f"翁卡女性中文配音（{version}）",
        "source": {"format": "loose-file-replacement", "game_version": version},
        "version": f"{version}.1",
    }
    documents["reports/tts.json"] = {
        "schema": 1,
        "type": "oongka-voice-tts",
        "game_version": version,
        "voice": VOICE_LABEL,
        "reference_audio": str(args.reference_audio.resolve()),
        "reference_sha256": _sha256(args.reference_audio.resolve()),
        "tts_replaced": tts_count,
        "silence_replaced": silenced,
        "total": len(files),
        "subtitle_tables_modified": False,
    }
    output = args.output.resolve()
    _write_cdmod_zip(output, documents)
    print(f"已生成：{output}")
    print(f"TTS 女声 {tts_count} 条；静音 {silenced} 条；合计 {len(files)} 条")
    print(f"SHA-256：{_sha256(output)}")
    return 0


def command_verify(args: argparse.Namespace) -> int:
    cdmod = args.cdmod.resolve()
    game_dir = args.game_dir.resolve()
    index = get_game_pamt_index(game_dir)
    with zipfile.ZipFile(cdmod) as archive:
        replacements = json.loads(archive.read("files/replacements.json").decode("utf-8"))
        specs = replacements["files"]
        missing: list[str] = []
        forbidden: list[str] = []
        sha_mismatch: list[str] = []
        bad_wem: list[str] = []
        per_dir: dict[str, int] = {}
        per_origin: dict[str, int] = {}
        total_bytes = 0
        for spec in specs:
            pamt_dir = str(spec["pamt_dir"])
            target = str(spec["target"])
            payload = archive.read(spec["payload"])
            total_bytes += len(payload)
            per_dir[pamt_dir] = per_dir.get(pamt_dir, 0) + 1
            origin = str(spec.get("origin") or "unknown")
            per_origin[origin] = per_origin.get(origin, 0) + 1
            if target.lower().endswith(FORBIDDEN_TARGET_SUFFIXES) or pamt_dir == "meta":
                forbidden.append(f"{pamt_dir}/{target}")
            if hashlib.sha256(payload).hexdigest() != str(spec.get("sha256")):
                sha_mismatch.append(f"{pamt_dir}/{target}")
            if not payload.startswith(WEM_MAGIC) or len(payload) != int(spec.get("size", -1)):
                bad_wem.append(f"{pamt_dir}/{target}")
            if index.find_in_dir(pamt_dir, target) is None:
                missing.append(f"{pamt_dir}/{target}")
    print(f"成品：{cdmod}")
    print(f"条目：{len(specs)}；载荷合计 {total_bytes} 字节")
    print(f"按目录：{dict(sorted(per_dir.items()))}")
    print(f"按来源：{dict(sorted(per_origin.items()))}")
    for label, items in (
        ("未命中当前 PAMT", missing),
        ("禁止的 target 类型", forbidden),
        ("SHA-256 不一致", sha_mismatch),
        ("非 WEM 或尺寸不符", bad_wem),
    ):
        if items:
            print(f"[失败] {label}：{len(items)} 条")
            for item in items[:10]:
                print(f"    {item}")
        else:
            print(f"[通过] {label}：0 条")
    return 0 if not (missing or forbidden or sha_mismatch or bad_wem) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="翁卡女性中文配音 TTS 工作流")
    sub = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    common.add_argument("--game-dir", type=Path, default=DEFAULT_GAME_DIR)

    p = sub.add_parser("prepare", parents=[common], help="生成 TTS 清单")
    p.add_argument("--records", type=Path, default=None)
    p.add_argument("--reference-audio", type=Path, default=DEFAULT_REFERENCE_AUDIO)
    p.set_defaults(handler=command_prepare)

    p = sub.add_parser("generate", parents=[common], help="VoxCPM2 批量生成 WAV")
    p.add_argument("--reference-audio", type=Path, default=DEFAULT_REFERENCE_AUDIO)
    p.add_argument("--ffmpeg", type=Path, default=DEFAULT_TTS_ROOT / "win-unpacked/python/ffmpeg/bin/ffmpeg.exe")
    p.add_argument("--voxcpm-python", type=Path, default=DEFAULT_TTS_ROOT / "win-unpacked/python/build_venv/python/python.exe")
    p.add_argument("--model-path", type=Path, default=DEFAULT_TTS_ROOT / "win-unpacked/python/models/VoxCPM2")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--inference-timesteps", type=int, default=10)
    p.add_argument("--cfg-value", type=float, default=2.0)
    p.add_argument("--no-dedupe", action="store_true", help="同文本不共用生成结果")
    p.set_defaults(handler=command_generate)

    p = sub.add_parser("convert", parents=[common], help="Wwise 转 WEM")
    p.add_argument("--wwise", type=Path, default=DEFAULT_WWISE)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--chunk-size", type=int, default=100)
    p.set_defaults(handler=command_convert)

    p = sub.add_parser("package", parents=[common], help="打包 cdmod")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--reference-audio", type=Path, default=DEFAULT_REFERENCE_AUDIO)
    p.add_argument("--silence-cdmod", type=Path, default=DEFAULT_SILENCE_CDMOD)
    p.add_argument("--no-silence", dest="include_silence", action="store_false")
    p.set_defaults(include_silence=True)
    p.set_defaults(handler=command_package)

    p = sub.add_parser("verify", parents=[common], help="复核成品包")
    p.add_argument("--cdmod", type=Path, required=True)
    p.set_defaults(handler=command_verify)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(args, "records") and args.records is None:
        args.records = args.work_dir / "records.json"
    try:
        return args.handler(args)
    except Exception as exc:  # noqa: BLE001 - CLI 顶层错误提示
        print(f"翁卡 TTS 工作流失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())