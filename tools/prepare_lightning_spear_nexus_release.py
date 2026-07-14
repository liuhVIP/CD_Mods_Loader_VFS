"""构建“四把传奇长枪闪电效果”Nexus Mods 上传资料。

每个 `.cdmod` 独立封装为一个 ZIP，避免互斥或独立文件混装。
发布目录包含 Nexus 页面 BBCode、中英资料、封面、副本与校验哈希；
源模组目录不会被修改。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# 发布版本：用户未指定时使用 Nexus .cdmod 发布流程约定的默认版本。
RELEASE_VERSION = "1.13.01"

# Nexus 页面稳定名称与本地发布目录名。
RELEASE_NAME = "Four Legendary Spears - Lightning Effects"
RELEASE_SLUG = "11-lightning-spears-1.13.01-cdmod"

# 用户明确指定的原作者与灵感来源页面。
ORIGINAL_AUTHOR = "GamingModsOn"
ORIGINAL_PAGE_URL = "https://www.nexusmods.com/crimsondesert/mods/3105"

# GitHub 仅用于分发加载器和转换器，不承载此模组的下载文件。
VFS_RELEASES_URL = "https://github.com/liuhVIP/cdmm/releases"

# ZIP 内固定 manifest 路径与确定性写入时间戳。
MANIFEST_PATH = "manifest.json"
DETERMINISTIC_ZIP_TIMESTAMP = (2026, 1, 1, 0, 0, 0)

# 每个 Nexus 文件只包含一个内部 .cdmod，便于玩家独立选择四把长枪。
PACKAGE_SPECS = (
    ("AeserionSpear_Lightning.field.cdmod", "Aeserion Spear Lightning"),
    ("GraspingMoon_Lightning.field.cdmod", "Grasping Moon Lightning"),
    ("DivergingMoon_Lightning.field.cdmod", "Diverging Moon Lightning"),
    ("CirclingMoon_Lightning.field.cdmod", "Circling Moon Lightning"),
)


@dataclass(frozen=True)
class ReleaseFile:
    """单个 Nexus 主文件的可审计产物信息。"""

    display_name: str
    cdmod_name: str
    zip_name: str
    cdmod_sha256: str
    zip_sha256: str
    file_description: str


def prepare_release(mods_dir: Path, cover_source: Path, release_root: Path) -> list[ReleaseFile]:
    """生成完整上传目录；仅在发布目录写入副本和文档。"""
    if not cover_source.is_file():
        raise FileNotFoundError(f"封面文件不存在：{cover_source}")
    release_dir = release_root / RELEASE_SLUG
    release_dir.mkdir(parents=True, exist_ok=True)
    _clear_previous_release_files(release_dir)

    release_files: list[ReleaseFile] = []
    for source_name, display_name in PACKAGE_SPECS:
        source_path = mods_dir / source_name
        if not source_path.is_file():
            raise FileNotFoundError(f"缺少待发布模组：{source_path}")
        release_files.append(_prepare_single_file(source_path, display_name, release_dir))

    cover_target = release_dir / f"模组封面{cover_source.suffix.lower()}"
    shutil.copy2(cover_source, cover_target)
    _write_release_documents(release_dir, release_files, cover_target.name)
    _validate_release(release_dir, release_files, cover_target)
    return release_files


def _clear_previous_release_files(release_dir: Path) -> None:
    """只清理本发布目录的旧产物，防止更新时遗留过期 ZIP。"""
    for path in release_dir.iterdir():
        if path.is_file():
            path.unlink()


def _prepare_single_file(source_path: Path, display_name: str, release_dir: Path) -> ReleaseFile:
    """复制、规范化版本并将单个 `.cdmod` 装入独立 Nexus ZIP。"""
    cdmod_name = f"{display_name}-{RELEASE_VERSION}.cdmod"
    cdmod_path = release_dir / cdmod_name
    _copy_cdmod_with_release_version(source_path, cdmod_path)
    zip_name = f"{display_name}-{RELEASE_VERSION}.zip"
    zip_path = release_dir / zip_name
    _write_single_cdmod_zip(zip_path, cdmod_path)
    return ReleaseFile(
        display_name=display_name,
        cdmod_name=cdmod_name,
        zip_name=zip_name,
        cdmod_sha256=_sha256(cdmod_path),
        zip_sha256=_sha256(zip_path),
        file_description=(
            f"VFS-ready {display_name} lightning .cdmod v{RELEASE_VERSION}. "
            "Extract and place the .cdmod in Crimson Desert\\mods."
        ),
    )


def _copy_cdmod_with_release_version(source_path: Path, output_path: Path) -> None:
    """复制容器内容并只更新副本 manifest 版本，不修改游戏 mods 中的源包。"""
    with zipfile.ZipFile(source_path) as source_archive:
        documents = {
            info.filename: source_archive.read(info.filename)
            for info in source_archive.infolist()
            if not info.is_dir()
        }
    manifest = json.loads(documents[MANIFEST_PATH].decode("utf-8-sig"))
    if not isinstance(manifest, dict):
        raise ValueError(f"{source_path.name} 的 manifest.json 不是对象")
    manifest["version"] = RELEASE_VERSION
    documents[MANIFEST_PATH] = _json_bytes(manifest)
    _write_deterministic_zip(output_path, documents)


def _write_single_cdmod_zip(zip_path: Path, cdmod_path: Path) -> None:
    """写入根部仅含一个 `.cdmod` 的 Nexus 上传 ZIP。"""
    _write_deterministic_zip(zip_path, {cdmod_path.name: cdmod_path.read_bytes()})


def _write_deterministic_zip(output_path: Path, documents: dict[str, bytes]) -> None:
    """按固定顺序和时间戳写 ZIP，确保重复构建的文件内容可校验。"""
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for archive_path in sorted(documents):
            info = zipfile.ZipInfo(archive_path, DETERMINISTIC_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, documents[archive_path], compresslevel=9)


def _write_release_documents(release_dir: Path, files: list[ReleaseFile], cover_name: str) -> None:
    """写入 Nexus 页面所需的名称、BBCode、短描述和发布信息。"""
    (release_dir / "模组名称.txt").write_text(f"{RELEASE_NAME}\n", encoding="utf-8")
    (release_dir / "模组描述-中英双语-BBCode.txt").write_text(
        _build_bbcode(files), encoding="utf-8"
    )
    (release_dir / "文件描述-Nexus不超过200字符.txt").write_text(
        _build_file_descriptions(files), encoding="utf-8"
    )
    (release_dir / "发布信息.txt").write_text(
        _build_release_information(files, cover_name), encoding="utf-8"
    )
    (release_dir / "上传步骤.txt").write_text(_build_upload_steps(files), encoding="utf-8")


def _build_bbcode(files: list[ReleaseFile]) -> str:
    """按 semantic-patch 的真实能力生成可直接粘贴的双语 Nexus BBCode。"""
    file_list_en = "\n".join(f"[*]{entry.display_name}" for entry in files)
    file_list_zh = "\n".join(f"[*]{entry.display_name}" for entry in files)
    return f"""[center][size=6][b]{RELEASE_NAME} - .CDMOD REBUILD[/b][/size][/center]

[size=5][b]ENGLISH[/b][/size]

Adds a verified lightning weapon effect and lightning passive skill to four legendary spears:
[list]
{file_list_en}
[/list]
Each download is a separate main file containing one `.cdmod`. Install only the spear files you want. Do not enable the matching Flame package for the same spear, because both packages edit the same item fields and the later load order would override the other effect.

[size=4][b]INSPIRATION AND CREDIT[/b][/size]

This independent lightning `.cdmod` rebuild was inspired by the original weapon-effect mod by [url={ORIGINAL_PAGE_URL}]{ORIGINAL_AUTHOR}[/url]. It is not the original upload and does not imply endorsement or authorization by the original author.

[size=4][b]HOW .CDMOD AND VFS WORK[/b][/size]

These files contain a `semantic-patch` for `iteminfo.pabgb`, not an old complete item table. The converter stores normalized record selectors and field operations for the four spear entries. cdloader reads the player's current vanilla table, merges compatible changes in final load order, and builds one final result. Changes to different records or fields in the same table can normally work together; a later mod wins when both change the same field.

The VFS approach is similar in concept to MO2 virtual loading: mods remain separate and the final files are mapped to paths read by the game only at runtime, without permanently rewriting vanilla PAZ/PAMT archives. cdloader-VFS is not an MO2 plugin and does not use the same implementation.

[size=4][b]JSON v3.1 AND .CDMOD[/b][/size]

JSON v3.1 is a semantic language for table modifications. `.cdmod` is the complete container and runtime contract that can carry semantic operations with metadata, dependencies, resources, and integrity checks. This does not make a mod immune to game updates: if the game changes a target field's binary layout, cdloader's corresponding writer may need an update.

[size=4][b]INSTALLATION[/b][/size]

[list=1]
[*]Download and extract one or more archives from this Nexus page.
[*]Do not extract the `.cdmod` inside the archive. Put it in `.../Crimson Desert/mods`.
[*]Download the matching cdloader-VFS from [url={VFS_RELEASES_URL}]VFS loader releases[/url].
[*]Start cdloader-VFS and verify the lightning effect in game.
[/list]

After adding, removing, disabling, or reordering mods, run cdloader-VFS again. It rebuilds or reuses its VFS data as needed. Normally this avoids permanent archive writes and reduces the need for Steam file verification, but it cannot guarantee verification will never be needed.

[size=4][b]CONVERT YOUR EXISTING MODS TO .CDMOD[/b][/size]

The converter is available from the same [url={VFS_RELEASES_URL}]GitHub Releases[/url] as cdloader-VFS. Choose `Convert one mod` for a single mod or `Convert a mods directory` for a full `mods` folder, then inspect `conversion-report.json`. If DMM or another manager previously mounted mods or wrote into game archives, restore clean vanilla game files first. cdloader-VFS must build from clean PAZ/PAMT/PABGB/PALOC sources.

[hr]
[size=5][b]中文说明[/b][/size]

本页为四把传奇长枪提供独立的闪电属性 `.cdmod` 文件：
[list]
{file_list_zh}
[/list]
每个下载文件只包含一把长枪的 `.cdmod`，按需下载即可。同一把武器不要同时启用对应的火焰版和闪电版，因为二者修改同一组物品字段，最终会由更靠后的加载顺序覆盖。

[size=4][b]灵感与署名[/b][/size]

本独立闪电 `.cdmod` 重构版受到原作者 [url={ORIGINAL_PAGE_URL}]{ORIGINAL_AUTHOR}[/url] 的武器效果模组启发。本页不是原始发布，也不表示获得原作者背书或授权。

[size=4][b].CDMOD 与 VFS 原理[/b][/size]

这四个包实际包含的是 `iteminfo.pabgb` 的 `semantic-patch`，而不是旧版本完整物品表。容器保存四把长枪的记录选择器和字段操作；cdloader 会读取玩家当前游戏原版表，按最终加载顺序合并兼容修改，并生成一份最终结果。同表不同记录或不同字段的修改通常可共存；同一字段冲突时由后加载模组覆盖。

VFS 的使用理念类似 MO2 的虚拟加载：模组保持独立，游戏运行时才把最终文件映射到原本读取的路径，不会永久改写原版 PAZ/PAMT。cdloader-VFS 不是 MO2 插件，也没有使用 MO2 的同一套实现。

[size=4][b]JSON v3.1 与 .CDMOD[/b][/size]

JSON v3.1 是描述目标表、记录、字段和值的语义表格补丁语言；`.cdmod` 是可承载这些操作以及元数据、依赖、资源和完整性校验的容器与运行规范。它不能保证永远适配游戏更新：游戏若改变目标字段的二进制结构，对应的 cdloader writer 仍可能需要更新。

[size=4][b]安装[/b][/size]

[list=1]
[*]从本 Nexus 页面下载并解压需要的 ZIP。
[*]不要继续解压 ZIP 内的 `.cdmod`；直接把它放进 `.../Crimson Desert/mods`。
[*]从 [url={VFS_RELEASES_URL}]GitHub Releases[/url] 下载版本匹配的 cdloader-VFS。
[*]启动 cdloader-VFS，并在游戏内确认闪电效果。
[/list]

切换、禁用、增删或调整排序后，再运行一次 cdloader-VFS 即可重建或复用 VFS 数据。正常情况下这会避免永久修改归档并减少 Steam 完整性校验需要，但不能承诺永远无需验证。

[size=4][b]转换已有模组为 .CDMOD[/b][/size]

转换器与 cdloader-VFS 都在同一 [url={VFS_RELEASES_URL}]GitHub Releases[/url] 提供。单个模组使用 `Convert one mod`，整个 `mods` 目录使用 `Convert a mods directory`，完成后查看 `conversion-report.json`。若 DMM 或其他管理器曾挂载模组或写入游戏归档，请先恢复纯净原版文件；cdloader-VFS 必须以未污染的 PAZ/PAMT/PABGB/PALOC 为构建基础。
"""


def _build_file_descriptions(files: list[ReleaseFile]) -> str:
    """输出四条均少于 Nexus 200 字符上限的英文文件描述。"""
    lines: list[str] = []
    for entry in files:
        if len(entry.file_description) > 200:
            raise ValueError(f"Nexus 文件描述超过 200 字符：{entry.display_name}")
        lines.extend((f"{entry.zip_name}", entry.file_description, f"Characters: {len(entry.file_description)}", ""))
    return "\n".join(lines).rstrip() + "\n"


def _build_release_information(files: list[ReleaseFile], cover_name: str) -> str:
    """记录上传页面需要填写的版本、分类、哈希和明确署名信息。"""
    records = "\n".join(
        f"- {entry.zip_name}\n  内部文件：{entry.cdmod_name}\n  ZIP SHA-256：{entry.zip_sha256}\n  CDMOD SHA-256：{entry.cdmod_sha256}"
        for entry in files
    )
    return f"""英文名称：{RELEASE_NAME}
中文参考名：四把传奇长枪 - 闪电效果
建议分类：Weapons
发布版本：{RELEASE_VERSION}
封面：{cover_name}
组件类型：semantic-patch（iteminfo.pabgb）
加载器：cdloader-VFS
加载器与转换器下载：{VFS_RELEASES_URL}

原作者：{ORIGINAL_AUTHOR}
原始灵感页面：{ORIGINAL_PAGE_URL}
署名说明：本独立闪电 .cdmod 重构版受上述原始武器效果模组启发；不是原始发布，不暗示授权或背书。

主文件：
{records}
"""


def _build_upload_steps(files: list[ReleaseFile]) -> str:
    """提供实际 Nexus 网页上传时的最短操作清单。"""
    file_names = "\n".join(f"- {entry.zip_name}" for entry in files)
    return f"""1. 在 Nexus Mods 创建 Crimson Desert 的新模组页面，名称使用“{RELEASE_NAME}”。
2. 将“模组描述-中英双语-BBCode.txt”全文粘贴到页面描述。
3. 上传“模组封面.png”作为封面。
4. 新建四个 Main File，版本均填写 {RELEASE_VERSION}，分别上传：
{file_names}
5. 每个文件的描述从“文件描述-Nexus不超过200字符.txt”复制对应条目。
6. 在 Credits / Description 保留 GamingModsOn 与原始链接：{ORIGINAL_PAGE_URL}
7. 上传前按“发布信息.txt”核对 ZIP 和内部 .cdmod 的 SHA-256。
"""


def _validate_release(release_dir: Path, files: list[ReleaseFile], cover_path: Path) -> None:
    """严格回读包结构、版本、哈希和封面，避免把错误资料交给 Nexus。"""
    if not cover_path.is_file() or cover_path.stat().st_size <= 0:
        raise ValueError("发布封面缺失或为空")
    for entry in files:
        cdmod_path = release_dir / entry.cdmod_name
        zip_path = release_dir / entry.zip_name
        if _sha256(cdmod_path) != entry.cdmod_sha256 or _sha256(zip_path) != entry.zip_sha256:
            raise ValueError(f"哈希回读失败：{entry.display_name}")
        with zipfile.ZipFile(cdmod_path) as archive:
            manifest = json.loads(archive.read(MANIFEST_PATH).decode("utf-8-sig"))
            if manifest.get("version") != RELEASE_VERSION:
                raise ValueError(f"内部 manifest 版本错误：{entry.cdmod_name}")
        with zipfile.ZipFile(zip_path) as archive:
            if archive.namelist() != [entry.cdmod_name]:
                raise ValueError(f"Nexus ZIP 必须只含一个 .cdmod：{entry.zip_name}")
            if archive.read(entry.cdmod_name) != cdmod_path.read_bytes():
                raise ValueError(f"Nexus ZIP 内部 .cdmod 不匹配：{entry.zip_name}")


def _sha256(path: Path) -> str:
    """以流式方式计算任意发布文件 SHA-256。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_bytes(document: dict[str, Any]) -> bytes:
    """输出 UTF-8、稳定排序的 manifest JSON。"""
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"


def _parse_args() -> argparse.Namespace:
    """读取发布源、封面和输出位置参数。"""
    parser = argparse.ArgumentParser(description="准备四把传奇长枪闪电效果的 Nexus 上传目录")
    parser.add_argument(
        "--mods-dir",
        type=Path,
        default=Path(r"G:\SteamLibrary\steamapps\common\Crimson Desert\mods"),
    )
    parser.add_argument(
        "--cover",
        type=Path,
        default=Path(r"D:\Downloads\QQ20260713-232902.png"),
    )
    parser.add_argument("--release-root", type=Path, default=Path("nexusmods"))
    return parser.parse_args()


def main() -> int:
    """执行发布资料构建并输出主文件与哈希。"""
    args = _parse_args()
    try:
        files = prepare_release(args.mods_dir, args.cover, args.release_root)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f"发布资料构建失败：{exc}", file=sys.stderr)
        return 1
    print(f"发布目录：{(args.release_root / RELEASE_SLUG).resolve()}")
    for entry in files:
        print(f"{entry.zip_name}: {entry.zip_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
