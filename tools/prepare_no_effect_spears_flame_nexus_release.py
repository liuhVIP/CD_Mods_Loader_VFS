"""准备全部无特殊效果长枪火焰版 Nexus V2 发布目录。"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import zipfile
from pathlib import Path


PROJECT_PARENT = Path(__file__).resolve().parents[2]
if str(PROJECT_PARENT) not in sys.path:
    sys.path.insert(0, str(PROJECT_PARENT))

from cdmm.services.cdmod_package import load_cdmod_package  # noqa: E402
from cdmm.tools.prepare_no_effect_spears_lightning_nexus_release import (  # noqa: E402
    COVER_TITLE_SCRIPT,
    MAXIMUM_COVER_BYTES,
    POWERSHELL_EXE,
    ReleaseResult,
    _clear_release_directory,
    _json_bytes,
    _sha256,
    _write_zip,
)


RELEASE_NAME = "All Non-Elemental Spears - Flame Effects"
CHINESE_RELEASE_NAME = "全部无特殊效果长枪 · 火焰效果"
RELEASE_VERSION = "2.0"
RELEASE_SLUG = "13-all-non-elemental-spears-flame-effects-v2"
SOURCE_FILE_NAME = "NoSpecialEffect_Spears_Flame_V2.field.cdmod"
CDMOD_FILE_NAME = f"{RELEASE_NAME}-{RELEASE_VERSION}.cdmod"
ZIP_FILE_NAME = f"{RELEASE_NAME}-{RELEASE_VERSION}.zip"
PACKAGE_ID = "crimsongamemods-itembuffs-no-effect-spears-flame-v2"
EXPECTED_TARGET_COUNT = 23
EXPECTED_OPERATION_COUNT = 253
MANIFEST_PATH = "manifest.json"
SEMANTIC_PATCH_PATH = "patches/semantic.json"
VFS_RELEASES_URL = "https://github.com/liuhVIP/cdmm/releases"
FILE_DESCRIPTION = (
    "Adds verified flame visuals, weapon attribute, and hit damage to all 23 vanilla "
    "two-handed spears without existing special effects. Requires cdloader-VFS."
)


def prepare_release(
    source_cdmod: Path,
    cover_source: Path,
    release_root: Path,
) -> ReleaseResult:
    """生成单 `.cdmod` 主文件、双语封面和完整 Nexus 文案。"""
    if not source_cdmod.is_file():
        raise FileNotFoundError(f"待发布火焰包不存在：{source_cdmod}")
    if not cover_source.is_file():
        raise FileNotFoundError(f"封面文件不存在：{cover_source}")
    release_dir = release_root.resolve() / RELEASE_SLUG
    release_dir.mkdir(parents=True, exist_ok=True)
    _clear_release_directory(release_dir)

    cdmod_path = release_dir / CDMOD_FILE_NAME
    _copy_normalized_cdmod(source_cdmod, cdmod_path)
    zip_path = release_dir / ZIP_FILE_NAME
    _write_zip(zip_path, {CDMOD_FILE_NAME: cdmod_path.read_bytes()})
    cover_path = release_dir / "模组封面.jpg"
    _build_bilingual_cover(cover_source, cover_path)

    result = ReleaseResult(
        release_dir=release_dir,
        cdmod_path=cdmod_path,
        zip_path=zip_path,
        cover_path=cover_path,
        cdmod_sha256=_sha256(cdmod_path),
        zip_sha256=_sha256(zip_path),
    )
    _write_documents(result)
    _validate_release(result)
    return result


def _copy_normalized_cdmod(source_path: Path, output_path: Path) -> None:
    """规范化发布副本 manifest，不修改游戏 mods 中的源包。"""
    with zipfile.ZipFile(source_path) as archive:
        documents = {
            info.filename: archive.read(info.filename)
            for info in archive.infolist()
            if not info.is_dir()
        }
    manifest = json.loads(documents[MANIFEST_PATH].decode("utf-8-sig"))
    manifest.update(
        {
            "description": (
                "Verified flame visuals, flame weapon attribute, and flame hit damage for "
                "23 vanilla two-handed spears without existing special effects."
            ),
            "id": PACKAGE_ID,
            "name": RELEASE_NAME,
            "version": RELEASE_VERSION,
        }
    )
    documents[MANIFEST_PATH] = _json_bytes(manifest)
    _write_zip(output_path, documents)


def _build_bilingual_cover(source_path: Path, output_path: Path) -> None:
    """使用技能本地脚本生成中英双语、2 MiB 内的封面。"""
    subprocess.run(
        [
            str(POWERSHELL_EXE),
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(COVER_TITLE_SCRIPT),
            "-InputPath",
            str(source_path),
            "-OutputPath",
            str(output_path),
            "-EnglishTitle",
            RELEASE_NAME.upper(),
            "-ChineseTitle",
            CHINESE_RELEASE_NAME,
            "-MaximumFileSizeBytes",
            str(MAXIMUM_COVER_BYTES),
        ],
        check=True,
    )


def _write_documents(result: ReleaseResult) -> None:
    """写入名称、BBCode、短描述、发布信息和上传步骤。"""
    release_dir = result.release_dir
    (release_dir / "模组名称.txt").write_text(f"{RELEASE_NAME}\n", encoding="utf-8")
    (release_dir / "模组描述-中英双语-BBCode.txt").write_text(
        _build_bbcode(), encoding="utf-8"
    )
    (release_dir / "文件描述-Nexus不超过200字符.txt").write_text(
        f"{FILE_DESCRIPTION}\n\nCharacters: {len(FILE_DESCRIPTION)}\n",
        encoding="utf-8",
    )
    (release_dir / "发布信息.txt").write_text(
        _build_release_info(result), encoding="utf-8"
    )
    (release_dir / "上传步骤.txt").write_text(_build_upload_steps(), encoding="utf-8")


def _build_bbcode() -> str:
    """生成与火焰 semantic-patch 能力一致的双语 BBCode。"""
    return f"""[center][size=6][b]{RELEASE_NAME}[/b][/size][/center]

[size=5][b]ENGLISH[/b][/size]

Adds flame weapon visuals, the flame weapon attribute, and flame hit damage to all 23 eligible vanilla two-handed spears and giant spears that originally have no special effect. Every included spear has been verified in game.

Halberds are excluded. Spears that already use Fire, Lightning, Wind, EMP, Bismuth, Soul, laser, or another special effect are also excluded, so this package does not overwrite their original element.

[size=4][b]IMPORTANT VARIANT RULE[/b][/size]

The Flame and Lightning editions modify the same ItemInfo fields and are mutually exclusive. Install only one edition. If both are enabled, the edition placed later in cdloader load order wins.

[size=4][b]HOW .CDMOD AND VFS WORK[/b][/size]

This package contains a `semantic-patch` for `iteminfo.pabgb`, not a complete old item table.
[list]
[*]It stores normalized record selectors and field operations for 23 spear records.
[*]cdloader reads the player's current vanilla table, merges compatible changes in final load order, and builds one final PABGB/PABGH result.
[*]Changes to different records or fields can normally coexist; a later mod wins when both change the same field.
[/list]

VFS is similar in concept to MO2 virtual loading: mods remain separate and final generated files are mapped to the paths read by the game at runtime. Vanilla PAZ/PAMT archives are not permanently rewritten during normal VFS use. cdloader-VFS is not an MO2 plugin and does not use the same implementation.

[size=4][b]JSON v3.1 AND .CDMOD[/b][/size]

JSON v3.1 is a semantic language for table targets, record selectors, fields, operations, and values. `.cdmod` is the complete container and runtime contract that can carry these operations with metadata, dependencies, resources, and integrity information. A real binary schema change may still require an updated cdloader writer; permanent compatibility with every future update cannot be guaranteed.

[size=4][b]INSTALLATION[/b][/size]
[list=1]
[*]Download and extract `{ZIP_FILE_NAME}` from this Nexus page.
[*]Do not extract the `.cdmod` inside it. Put `{CDMOD_FILE_NAME}` in `.../Crimson Desert/mods`.
[*]Download the matching cdloader-VFS from [url={VFS_RELEASES_URL}]GitHub Releases[/url]. GitHub provides the loader and converter, not this mod file.
[*]Run cdloader-VFS after installing, removing, disabling, or reordering mods, then verify the result in game.
[/list]

Normally VFS avoids permanent archive writes and reduces the need for Steam verification, but it cannot guarantee verification will never be needed.

[size=4][b]CONVERT EXISTING MODS TO .CDMOD[/b][/size]

The converter is available from the same [url={VFS_RELEASES_URL}]GitHub Releases[/url]. Use `Convert one mod` for one source or `Convert a mods directory` for a complete `mods` folder, then review `conversion-report.json`. Supported Format 3 / JSON v3.1 operations become semantic rules; required loose resources remain complete resources; standalone PAZ/PAMT remains a complete archive. If DMM or another manager previously mounted mods or wrote into game archives, restore clean vanilla files before converting or loading.

[hr]
[size=5][b]中文说明[/b][/size]

为当前原版中 23 把原本没有特殊效果的双手长枪和巨型双手长枪添加武器火焰特效、火焰属性词条和命中火焰伤害。全部 23 把目标长枪均已完成游戏内实测。

本模组不包含战戟，也不会修改原版已经带有火焰、雷电、风力、EMP、铋、灵魂、激光或其他特殊效果的长枪，避免覆盖它们原有属性。

[size=4][b]重要变体规则[/b][/size]

火焰版与雷电版修改相同 ItemInfo 字段，属于互斥变体。建议只安装一个版本；若同时启用，则由 cdloader 加载顺序中更靠后的版本覆盖生效。

[size=4][b].CDMOD 与 VFS 原理[/b][/size]

本包是针对 `iteminfo.pabgb` 的 `semantic-patch`，不携带旧版本完整物品表。
[list]
[*]容器保存 23 条长枪记录的明确选择器和字段操作。
[*]cdloader 读取玩家当前版本原版表，按最终加载顺序合并兼容修改，并重建一份最终 PABGB/PABGH。
[*]同表不同记录或不同字段的修改通常可以共存；同一字段冲突时由后加载模组覆盖。
[/list]

VFS 的使用理念类似 MO2 虚拟加载：模组保持独立，运行时才把最终生成文件映射到游戏原本读取的路径，正常情况下不会永久改写原版 PAZ/PAMT。cdloader-VFS 不是 MO2 插件，也没有使用 MO2 的同一套实现。

[size=4][b]JSON v3.1 与 .CDMOD[/b][/size]

JSON v3.1 是描述目标表、记录选择器、字段、操作和值的语义语言；`.cdmod` 是可承载这些操作以及元数据、依赖、资源和完整性信息的容器与运行规范。游戏若真正改变字段二进制结构，对应 cdloader writer 仍可能需要更新，不能承诺永远适配所有未来版本。

[size=4][b]安装[/b][/size]
[list=1]
[*]从本 Nexus 页面下载并解压 `{ZIP_FILE_NAME}`。
[*]不要继续解压其中的 `.cdmod`；把 `{CDMOD_FILE_NAME}` 放进 `.../Crimson Desert/mods`。
[*]从 [url={VFS_RELEASES_URL}]GitHub Releases[/url] 下载匹配版本的 cdloader-VFS。GitHub 只提供加载器和转换器，不提供本模组文件。
[*]安装、删除、禁用或调整排序后重新运行 cdloader-VFS，再进入游戏确认效果。
[/list]

正常情况下 VFS 不会永久写入游戏归档，并可减少 Steam 完整性验证需求，但不能承诺永远无需验证。

[size=4][b]转换已有模组为 .CDMOD[/b][/size]

转换器与 cdloader-VFS 位于同一 [url={VFS_RELEASES_URL}]GitHub Releases[/url]。单个来源使用 `Convert one mod`，整个 `mods` 目录使用 `Convert a mods directory`，完成后检查 `conversion-report.json`。受支持的 Format 3 / JSON v3.1 操作会保存为语义规则；loose 资源保留完整文件；standalone PAZ/PAMT 保持完整归档。若 DMM 或其他管理器曾挂载模组或写入游戏归档，转换或加载前必须先恢复纯净原版文件。
"""


def _build_release_info(result: ReleaseResult) -> str:
    """生成版本、分类、哈希和文件核对资料。"""
    return f"""英文标题：{RELEASE_NAME}
中文参考名：全部无特殊效果长枪 - 火焰效果
建议分类：Weapons
发布版本：{RELEASE_VERSION}
主文件：{ZIP_FILE_NAME}
内部文件：{CDMOD_FILE_NAME}
组件类型：semantic-patch（iteminfo.pabgb）
目标数量：{EXPECTED_TARGET_COUNT}
字段操作数量：{EXPECTED_OPERATION_COUNT}
封面：{result.cover_path.name}
加载器与转换器：{VFS_RELEASES_URL}

ZIP SHA-256：{result.zip_sha256}
CDMOD SHA-256：{result.cdmod_sha256}

英文短描述：{FILE_DESCRIPTION}
中文短描述：为 23 把原版无特殊效果长枪添加火焰特效、火属性词条和命中火焰伤害；排除战戟及已有特殊效果武器。
"""


def _build_upload_steps() -> str:
    """生成 Nexus 网页上传步骤。"""
    return f"""1. Nexus Mods 页面标题使用：{RELEASE_NAME}
2. 分类选择 Weapons，版本填写 {RELEASE_VERSION}。
3. 上传“{ZIP_FILE_NAME}”作为 Main File。
4. 文件描述复制“文件描述-Nexus不超过200字符.txt”的第一行。
5. 页面描述粘贴“模组描述-中英双语-BBCode.txt”全文。
6. 封面上传“模组封面.jpg”。
7. 上传前按“发布信息.txt”核对 ZIP 与内部 .cdmod 的 SHA-256。
8. 不要把雷电版和火焰版放进同一个主文件。
"""


def _validate_release(result: ReleaseResult) -> None:
    """验证容器、语义目标、ZIP、封面、哈希和短描述。"""
    if len(FILE_DESCRIPTION) > 200:
        raise ValueError(f"Nexus 文件描述超过 200 字符：{len(FILE_DESCRIPTION)}")
    if not result.cover_path.is_file() or result.cover_path.stat().st_size <= 0:
        raise ValueError("火焰版封面缺失")
    if result.cover_path.stat().st_size > MAXIMUM_COVER_BYTES:
        raise ValueError("火焰版封面超过 2 MiB")
    package = load_cdmod_package(result.cdmod_path)
    identities = {
        (operation.selector.get("key"), operation.selector.get("string_key"))
        for operation in package.operations
    }
    if package.mod_id != PACKAGE_ID or package.version != RELEASE_VERSION:
        raise ValueError("正式火焰 V2 manifest 标识或版本错误")
    if len(identities) != EXPECTED_TARGET_COUNT or len(package.operations) != EXPECTED_OPERATION_COUNT:
        raise ValueError("正式火焰 V2 目标或操作数量错误")
    if any("Alebard" in str(name) for _, name in identities):
        raise ValueError("正式火焰 V2 中仍包含战戟")
    with zipfile.ZipFile(result.cdmod_path) as archive:
        manifest = json.loads(archive.read(MANIFEST_PATH).decode("utf-8-sig"))
        if [item.get("type") for item in manifest.get("components", [])] != ["semantic-patch"]:
            raise ValueError("正式火焰 V2 不是单一 semantic-patch")
        patch = json.loads(archive.read(SEMANTIC_PATCH_PATH).decode("utf-8-sig"))
        if len(patch["targets"][0]["operations"]) != EXPECTED_OPERATION_COUNT:
            raise ValueError("正式火焰 V2 语义操作回读失败")
    with zipfile.ZipFile(result.zip_path) as archive:
        if archive.namelist() != [CDMOD_FILE_NAME]:
            raise ValueError("Nexus ZIP 根部必须只包含一个火焰 V2 .cdmod")
        if archive.read(CDMOD_FILE_NAME) != result.cdmod_path.read_bytes():
            raise ValueError("Nexus ZIP 内部火焰包不匹配")
    if _sha256(result.cdmod_path) != result.cdmod_sha256:
        raise ValueError("火焰 CDMOD SHA-256 回读失败")
    if _sha256(result.zip_path) != result.zip_sha256:
        raise ValueError("火焰 ZIP SHA-256 回读失败")


def _parse_args() -> argparse.Namespace:
    """读取火焰源包、封面和 Nexus 输出目录。"""
    parser = argparse.ArgumentParser(description="准备全部无特殊效果长枪火焰效果 Nexus V2")
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(r"G:\SteamLibrary\steamapps\common\Crimson Desert\mods") / SOURCE_FILE_NAME,
    )
    parser.add_argument(
        "--cover",
        type=Path,
        default=Path(r"D:\Downloads\QQ20260714-152052.png"),
    )
    parser.add_argument("--release-root", type=Path, default=Path("nexusmods"))
    return parser.parse_args()


def main() -> int:
    """构建火焰版 Nexus 发布资料并输出最终哈希。"""
    args = _parse_args()
    try:
        result = prepare_release(args.source, args.cover, args.release_root)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        print(f"火焰版 Nexus 发布资料构建失败：{exc}", file=sys.stderr)
        return 1
    print(f"发布目录：{result.release_dir}")
    print(f"主文件：{result.zip_path.name}")
    print(f"ZIP SHA-256：{result.zip_sha256}")
    print(f"CDMOD SHA-256：{result.cdmod_sha256}")
    print(f"封面字节数：{result.cover_path.stat().st_size}")
    print(f"文件描述字符数：{len(FILE_DESCRIPTION)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
