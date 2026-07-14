"""准备“克里夫女性语音 - 中文版”Nexus 发布目录。"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path


# 发布内容常量：统一约束页面标题、容器名称和版本。
ENGLISH_TITLE = "Female Voice for Kliff - Chinese"
CHINESE_TITLE = "克里夫女性语音 - 中文版"
BILINGUAL_TITLE = f"{ENGLISH_TITLE} | {CHINESE_TITLE}"
RELEASE_VERSION = "1.13.01"
RELEASE_DIRECTORY = "14-female-voice-for-kliff-chinese-1.13.01-cdmod"
CDMOD_FILE_NAME = f"{ENGLISH_TITLE}-{RELEASE_VERSION}.cdmod"
ZIP_FILE_NAME = f"{ENGLISH_TITLE}-{RELEASE_VERSION}.zip"
PACKAGE_ID = "female-voice-for-kliff-chinese"
VFS_RELEASES_URL = "https://github.com/liuhVIP/cdmm/releases"

# 输入和生成文件常量。
DEFAULT_SOURCE = Path(
    r"G:\SteamLibrary\steamapps\common\Crimson Desert\mods\kliff_female_voice_chinese.cdmod"
)
DEFAULT_COVER = Path(r"D:\Downloads\F536AC8AC3F1B54156F9BCCC9B62E1D0.png")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
COVER_SCRIPT = (
    PROJECT_ROOT
    / ".codex"
    / "skills"
    / "publish-nexus-cdmod"
    / "scripts"
    / "add_bilingual_cover_title.ps1"
)
POWERSHELL_EXE = Path(r"C:\Program Files\PowerShell\7\pwsh.exe")
MAXIMUM_COVER_BYTES = 2 * 1024 * 1024
EXPECTED_FILE_COUNT = 2052
EXPECTED_PAYLOAD_BYTES = 67862478
FILE_DESCRIPTION = (
    "Replaces Kliff's Chinese male voice with a female voice using 2,052 WEM audio files. "
    "Requires cdloader-VFS."
)


def sha256(path: Path) -> str:
    """计算发布文件 SHA-256。"""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_deterministic_zip(path: Path, files: dict[str, bytes]) -> None:
    """以固定时间戳写入 ZIP，确保重复构建结果稳定。"""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=(2026, 7, 14, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, files[name], compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def build_cdmod(source: Path, destination: Path) -> tuple[int, int]:
    """复制并规范化发布副本，不修改游戏 mods 下的源文件。"""
    with zipfile.ZipFile(source) as archive:
        if archive.testzip() is not None:
            raise ValueError("源 .cdmod ZIP 校验失败")
        files = {
            info.filename: archive.read(info.filename)
            for info in archive.infolist()
            if not info.is_dir()
        }

    manifest = json.loads(files["manifest.json"].decode("utf-8-sig"))
    components = manifest.get("components", [])
    if [component.get("type") for component in components] != ["file-replacement"]:
        raise ValueError("源包不是单一 file-replacement 组件")

    replacements = json.loads(files["files/replacements.json"].decode("utf-8-sig"))
    replacement_files = replacements.get("files", [])
    payload_bytes = sum(int(item.get("size", 0)) for item in replacement_files)
    if len(replacement_files) != EXPECTED_FILE_COUNT or payload_bytes != EXPECTED_PAYLOAD_BYTES:
        raise ValueError("源包 WEM 数量或资源总字节数与核验基线不一致")
    if any(not item.get("target", "").lower().endswith(".wem") for item in replacement_files):
        raise ValueError("源包包含非 WEM 替换目标")

    manifest.update(
        {
            "description": (
                "Replaces Kliff's Chinese male voice with a female voice through "
                "2,052 complete WEM audio replacements."
            ),
            "id": PACKAGE_ID,
            "name": ENGLISH_TITLE,
            "version": RELEASE_VERSION,
        }
    )
    # 保留源 manifest 作者字段，仅规范化正式发布名称、版本和说明。
    files["manifest.json"] = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    write_deterministic_zip(destination, files)
    return len(replacement_files), payload_bytes


def build_cover(source: Path, destination: Path) -> None:
    """调用技能脚本生成 1600x900 中英双语封面。"""
    subprocess.run(
        [
            str(POWERSHELL_EXE),
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(COVER_SCRIPT),
            "-InputPath",
            str(source),
            "-OutputPath",
            str(destination),
            "-EnglishTitle",
            ENGLISH_TITLE.upper(),
            "-ChineseTitle",
            CHINESE_TITLE,
            "-MaximumFileSizeBytes",
            str(MAXIMUM_COVER_BYTES),
        ],
        check=True,
    )


def build_bbcode() -> str:
    """生成与完整音频资源替换能力一致的中英双语 BBCode。"""
    return f"""[center][size=6][b]{BILINGUAL_TITLE}[/b][/size][/center]

[size=5][b]ENGLISH[/b][/size]

Replaces Kliff's Chinese male voice with a female voice. This package contains 2,052 complete WEM audio replacements covering Kliff's regular dialogue, combat and skill voice lines included by the source package.

This is the Chinese-language edition. The package manifest describes the skill-related voice coverage as largely complete, but the result has not been independently verified in game as part of this Nexus packaging step.

[size=4][b]HOW .CDMOD AND VFS WORK[/b][/size]

This package contains a `file-replacement` component. The WEM audio files are complete resources required for the voice replacement; this is not a semantic table patch and does not provide field-level merging.

cdloader first composes enabled mods in final load order. Its VFS then maps the final audio resources to the paths the game normally reads at runtime, without permanently rewriting the vanilla PAZ/PAMT archives on disk during normal use. This is similar in concept to MO2 virtual loading, but cdloader-VFS is not an MO2 plugin and does not use MO2's implementation.

After switching, disabling, or reordering mods, run cdloader-VFS again so it can rebuild or reuse the correct VFS data. If another mod replaces the same WEM path, the later mod in the final load order wins.

[size=4][b]JSON v3.1 AND .CDMOD[/b][/size]

JSON v3.1 / Format 3 is a semantic language for describing table targets, record selectors, fields, operations, and values. `.cdmod` is the complete mod container and runtime contract. It can carry semantic operations, PALOC, textures, models, animations, audio, legacy byte patches, resource transforms, or standalone archives when required.

This voice mod uses complete audio resources because WEM files cannot be rebuilt from table-field rules. `.cdmod` improves packaging, integrity checks, ordering, conflict analysis, and unified VFS loading, but it does not make complete resources immune to game updates. A changed game path or resource format may still require an updated package or loader support.

[size=4][b]INSTALLATION[/b][/size]
[list=1]
[*]Download and extract `{ZIP_FILE_NAME}` from this Nexus page.
[*]Do not extract the `.cdmod` inside it. Put `{CDMOD_FILE_NAME}` in `.../Crimson Desert/mods`.
[*]Download the matching cdloader-VFS from [url={VFS_RELEASES_URL}]GitHub Releases[/url]. GitHub provides the loader and converter, not this mod file.
[*]Run cdloader-VFS, then verify the Chinese female voice in game.
[/list]

Normally VFS reduces the need for Steam file verification because it does not permanently overwrite vanilla archives, but it cannot guarantee verification will never be needed.

[size=4][b]CONVERT EXISTING MODS TO .CDMOD[/b][/size]

The converter is available from the same [url={VFS_RELEASES_URL}]GitHub Releases[/url]. Choose `Convert one mod` for one source or `Convert a mods directory` for a complete `mods` folder, then review `conversion-report.json`. Supported Format 3 / JSON v3.1 operations become semantic rules; loose and binary resources retain the complete files required to work; standalone PAZ/PAMT remains a complete archive.

If DMM or another manager previously mounted mods or wrote into the game archives, restore clean vanilla game files before converting or loading. cdloader-VFS must build from clean PAZ/PAMT/PABGB/PALOC source data.

[hr]
[size=5][b]中文说明[/b][/size]

将男性主角克里夫的中文语音替换为女性声音。本包包含 2052 个完整 WEM 音频替换资源，覆盖源包所包含的克里夫常规对白、战斗及技能语音。

这是中文语音版本。包内 manifest 说明技能等语音已基本完善，但本次 Nexus 打包过程没有另行完成游戏内实测，因此不会把“打包成功”表述为“已实机验证”。

[size=4][b].CDMOD 与 VFS 原理[/b][/size]

本包使用 `file-replacement` 组件。WEM 音频是语音替换实际需要的完整资源；它不是语义表补丁，也不具备字段级合并能力。

cdloader 会先按最终加载顺序合成已启用模组，再由 VFS 在游戏运行时把最终音频资源映射到游戏原本读取的路径，正常使用时不会永久改写磁盘上的原版 PAZ/PAMT 归档。这种理念类似 MO2 虚拟加载，但 cdloader-VFS 不是 MO2 插件，也没有使用 MO2 的同一套实现。

切换、禁用或调整模组排序后，重新运行 cdloader-VFS 即可重建或复用正确的 VFS 数据。如果其他模组替换相同 WEM 路径，则由最终加载顺序中更靠后的模组覆盖。

[size=4][b]JSON v3.1 与 .CDMOD[/b][/size]

JSON v3.1 / Format 3 是描述目标表、记录选择器、字段、操作和值的语义语言；`.cdmod` 是完整模组容器与运行规范，可以按实际需要承载语义补丁、PALOC、贴图、模型、动画、音频、传统 byte patch、资源变换或 standalone 归档。

本语音模组必须携带完整 WEM 音频，不能从表格字段规则重新生成。`.cdmod` 提供统一封装、完整性校验、排序、冲突分析和 VFS 加载，但不会让完整资源天然免疫游戏更新；若游戏路径或资源格式改变，仍可能需要更新模组包或加载器支持。

[size=4][b]安装[/b][/size]
[list=1]
[*]从本 Nexus 页面下载并解压 `{ZIP_FILE_NAME}`。
[*]不要继续解压其中的 `.cdmod`；把 `{CDMOD_FILE_NAME}` 放进 `.../Crimson Desert/mods`。
[*]从 [url={VFS_RELEASES_URL}]GitHub Releases[/url] 下载匹配版本的 cdloader-VFS。GitHub 只提供加载器和转换器，不提供本模组文件。
[*]运行 cdloader-VFS，再进入游戏确认中文女性语音效果。
[/list]

正常情况下，VFS 不会永久覆盖原版归档，因此通常可减少 Steam 完整性验证需求，但不能承诺永远无需验证。

[size=4][b]转换已有模组为 .CDMOD[/b][/size]

转换器与 cdloader-VFS 位于同一 [url={VFS_RELEASES_URL}]GitHub Releases[/url]。单个来源选择 `Convert one mod`，整个 `mods` 目录选择 `Convert a mods directory`，完成后检查 `conversion-report.json`。受支持的 Format 3 / JSON v3.1 操作会保存为语义规则；loose 与二进制资源保留生效所需的完整文件；standalone PAZ/PAMT 保持完整归档。

若 DMM 或其他管理器曾挂载模组或写入游戏归档，转换或加载前必须先恢复纯净原版游戏文件。cdloader-VFS 必须基于干净的 PAZ/PAMT/PABGB/PALOC 源数据构建。
"""


def write_documents(release_dir: Path, cdmod_hash: str, zip_hash: str) -> None:
    """写入 Nexus 页面可直接粘贴的资料和上传核对信息。"""
    (release_dir / "模组名称.txt").write_text(f"{BILINGUAL_TITLE}\n", encoding="utf-8")
    (release_dir / "模组描述-中英双语-BBCode.txt").write_text(
        build_bbcode(), encoding="utf-8"
    )
    (release_dir / "文件描述-Nexus不超过200字符.txt").write_text(
        f"{FILE_DESCRIPTION}\n\nCharacters: {len(FILE_DESCRIPTION)}\n", encoding="utf-8"
    )
    (release_dir / "发布信息.txt").write_text(
        f"""Nexus 中英双语标题：{BILINGUAL_TITLE}
英文标题：{ENGLISH_TITLE}
中文标题：{CHINESE_TITLE}
建议分类：Audio
发布版本：{RELEASE_VERSION}
主文件：{ZIP_FILE_NAME}
内部文件：{CDMOD_FILE_NAME}
组件类型：file-replacement
音频资源：2052 个 WEM 文件
资源总字节数：67862478
目标归档分区：0004、0035
封面：模组封面.jpg
加载器与转换器：{VFS_RELEASES_URL}

ZIP SHA-256：{zip_hash}
CDMOD SHA-256：{cdmod_hash}

英文短描述：{FILE_DESCRIPTION}
中文短描述：将男性主角克里夫的中文语音替换为女性声音，包含 2052 个完整 WEM 音频替换资源，使用 cdloader-VFS 加载。
""",
        encoding="utf-8",
    )
    (release_dir / "上传步骤.txt").write_text(
        f"""1. Nexus Mods 页面标题使用：{BILINGUAL_TITLE}
2. 分类选择 Audio，版本填写 {RELEASE_VERSION}。
3. 上传“{ZIP_FILE_NAME}”作为 Main File。
4. 文件描述复制“文件描述-Nexus不超过200字符.txt”的第一行。
5. 页面描述粘贴“模组描述-中英双语-BBCode.txt”全文。
6. 封面上传“模组封面.jpg”。
7. 上传前按“发布信息.txt”核对 ZIP 与内部 .cdmod 的 SHA-256。
""",
        encoding="utf-8",
    )


def validate_release(release_dir: Path, cdmod_path: Path, zip_path: Path) -> None:
    """严格回读容器、外层 ZIP、短描述与封面。"""
    if len(FILE_DESCRIPTION) > 200:
        raise ValueError(f"Nexus 文件描述超过 200 字符：{len(FILE_DESCRIPTION)}")
    with zipfile.ZipFile(cdmod_path) as archive:
        if archive.testzip() is not None:
            raise ValueError("发布 .cdmod ZIP 回读失败")
        manifest = json.loads(archive.read("manifest.json").decode("utf-8-sig"))
        replacements = json.loads(
            archive.read("files/replacements.json").decode("utf-8-sig")
        )["files"]
        if manifest.get("version") != RELEASE_VERSION:
            raise ValueError("发布 manifest 版本未规范化")
        if len(replacements) != EXPECTED_FILE_COUNT:
            raise ValueError("发布包音频替换数量不一致")
    with zipfile.ZipFile(zip_path) as archive:
        if archive.namelist() != [CDMOD_FILE_NAME]:
            raise ValueError("Nexus ZIP 根部必须只包含一个 .cdmod")
        if archive.read(CDMOD_FILE_NAME) != cdmod_path.read_bytes():
            raise ValueError("Nexus ZIP 内部 .cdmod 与发布副本不一致")

    cover_path = release_dir / "模组封面.jpg"
    if not cover_path.is_file() or cover_path.stat().st_size > MAXIMUM_COVER_BYTES:
        raise ValueError("封面缺失或超过 2 MiB")


def prepare_release(source: Path, cover: Path, release_root: Path) -> Path:
    """生成完整的带序号 Nexus 上传目录。"""
    if not source.is_file() or not cover.is_file():
        raise FileNotFoundError("源 .cdmod 或用户封面不存在")
    release_dir = release_root.resolve() / RELEASE_DIRECTORY
    release_dir.mkdir(parents=True, exist_ok=True)
    if any(release_dir.iterdir()):
        raise FileExistsError(f"发布目录不是空目录：{release_dir}")

    cdmod_path = release_dir / CDMOD_FILE_NAME
    build_cdmod(source, cdmod_path)
    zip_path = release_dir / ZIP_FILE_NAME
    write_deterministic_zip(zip_path, {CDMOD_FILE_NAME: cdmod_path.read_bytes()})
    build_cover(cover, release_dir / "模组封面.jpg")
    cdmod_hash = sha256(cdmod_path)
    zip_hash = sha256(zip_path)
    write_documents(release_dir, cdmod_hash, zip_hash)
    validate_release(release_dir, cdmod_path, zip_path)
    print(f"发布目录：{release_dir}")
    print(f"ZIP SHA-256：{zip_hash}")
    print(f"CDMOD SHA-256：{cdmod_hash}")
    print(f"文件描述字符数：{len(FILE_DESCRIPTION)}")
    return release_dir


def parse_args() -> argparse.Namespace:
    """读取发布输入参数。"""
    parser = argparse.ArgumentParser(description="准备克里夫中文女性语音 Nexus 发布资料")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--cover", type=Path, default=DEFAULT_COVER)
    parser.add_argument("--release-root", type=Path, default=PROJECT_ROOT / "nexusmods")
    return parser.parse_args()


def main() -> int:
    """命令行入口。"""
    args = parse_args()
    try:
        prepare_release(args.source, args.cover, args.release_root)
    except (OSError, ValueError, KeyError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        print(f"Nexus 发布资料生成失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
