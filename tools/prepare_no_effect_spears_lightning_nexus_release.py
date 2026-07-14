"""准备“全部无特殊效果长枪雷电化”Nexus Mods V2 发布目录。

发布包只包含一个 semantic-patch `.cdmod`。页面资料准确说明 23 把长枪
已经实机验证，排除战戟与原版已有特殊效果的武器。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_PARENT = Path(__file__).resolve().parents[2]
if str(PROJECT_PARENT) not in sys.path:
    sys.path.insert(0, str(PROJECT_PARENT))

from cdmm.services.cdmod_package import load_cdmod_package  # noqa: E402


RELEASE_NAME = "All Non-Elemental Spears - Lightning Effects"
RELEASE_VERSION = "2.0"
RELEASE_SLUG = "all-non-elemental-spears-lightning-effects-v2"
SOURCE_FILE_NAME = "NoSpecialEffect_Polearms_Lightning_V2.field.cdmod"
CDMOD_FILE_NAME = f"{RELEASE_NAME}-{RELEASE_VERSION}.cdmod"
ZIP_FILE_NAME = f"{RELEASE_NAME}-{RELEASE_VERSION}.zip"
PACKAGE_ID = "crimsongamemods-itembuffs-no-effect-spears-lightning-v2"
EXPECTED_TARGET_COUNT = 23
EXPECTED_OPERATION_COUNT = 253
MAXIMUM_COVER_BYTES = 2 * 1024 * 1024
MANIFEST_PATH = "manifest.json"
SEMANTIC_PATCH_PATH = "patches/semantic.json"
ZIP_TIMESTAMP = (2026, 1, 1, 0, 0, 0)
VFS_RELEASES_URL = "https://github.com/liuhVIP/cdmm/releases"
POWERSHELL_EXE = Path(r"C:\Program Files\PowerShell\7\pwsh.exe")
COVER_TITLE_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / ".codex"
    / "skills"
    / "publish-nexus-cdmod"
    / "scripts"
    / "add_bilingual_cover_title.ps1"
)
CHINESE_RELEASE_NAME = "全部无特殊效果长枪 · 雷电效果"
FILE_DESCRIPTION = (
    "Adds verified lightning visuals, weapon attribute, and hit damage to all 23 vanilla "
    "two-handed spears without existing special effects. Requires cdloader-VFS."
)


@dataclass(frozen=True)
class ReleaseResult:
    """一个完成严格回读的 Nexus 主文件结果。"""

    release_dir: Path
    cdmod_path: Path
    zip_path: Path
    cover_path: Path
    cdmod_sha256: str
    zip_sha256: str


def prepare_release(
    source_cdmod: Path,
    cover_source: Path,
    release_root: Path,
) -> ReleaseResult:
    """复制正式 V2、生成单文件 ZIP、封面和中英文发布资料。"""
    if not source_cdmod.is_file():
        raise FileNotFoundError(f"待发布 cdmod 不存在：{source_cdmod}")
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

    cdmod_sha256 = _sha256(cdmod_path)
    zip_sha256 = _sha256(zip_path)
    _write_documents(release_dir, cover_path.name, cdmod_sha256, zip_sha256)
    result = ReleaseResult(
        release_dir=release_dir,
        cdmod_path=cdmod_path,
        zip_path=zip_path,
        cover_path=cover_path,
        cdmod_sha256=cdmod_sha256,
        zip_sha256=zip_sha256,
    )
    _validate_release(result)
    return result


def _build_bilingual_cover(cover_source: Path, cover_path: Path) -> None:
    """调用技能自带的本地脚本添加规范中英双语标题。"""
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
            str(cover_source),
            "-OutputPath",
            str(cover_path),
            "-EnglishTitle",
            RELEASE_NAME.upper(),
            "-ChineseTitle",
            CHINESE_RELEASE_NAME,
            "-MaximumFileSizeBytes",
            str(MAXIMUM_COVER_BYTES),
        ],
        check=True,
    )


def _clear_release_directory(release_dir: Path) -> None:
    """仅清理本次发布目录内的旧文件，避免 V1 产物混入。"""
    for path in release_dir.iterdir():
        if path.is_file():
            path.unlink()


def _copy_normalized_cdmod(source_path: Path, output_path: Path) -> None:
    """规范化正式 V2 manifest，并保持其余容器内容不变。"""
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
                "Verified lightning visuals, lightning weapon attribute, and lightning hit damage "
                "for 23 vanilla two-handed spears without existing special effects."
            ),
            "id": PACKAGE_ID,
            "name": RELEASE_NAME,
            "version": RELEASE_VERSION,
        }
    )
    documents[MANIFEST_PATH] = _json_bytes(manifest)
    _write_zip(output_path, documents)


def _write_zip(output_path: Path, documents: dict[str, bytes]) -> None:
    """以固定顺序和时间戳写入可重复校验的 ZIP。"""
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for archive_path in sorted(documents):
            info = zipfile.ZipInfo(archive_path, ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, documents[archive_path], compresslevel=9)


def _write_documents(
    release_dir: Path,
    cover_name: str,
    cdmod_sha256: str,
    zip_sha256: str,
) -> None:
    """写入 Nexus 页面、文件和上传所需的 UTF-8 文本。"""
    (release_dir / "模组名称.txt").write_text(f"{RELEASE_NAME}\n", encoding="utf-8")
    (release_dir / "模组描述-中英双语-BBCode.txt").write_text(
        _build_bbcode(), encoding="utf-8"
    )
    (release_dir / "文件描述-Nexus不超过200字符.txt").write_text(
        f"{FILE_DESCRIPTION}\n\nCharacters: {len(FILE_DESCRIPTION)}\n",
        encoding="utf-8",
    )
    (release_dir / "发布信息.txt").write_text(
        _build_release_info(cover_name, cdmod_sha256, zip_sha256),
        encoding="utf-8",
    )
    (release_dir / "上传步骤.txt").write_text(_build_upload_steps(), encoding="utf-8")


def _build_bbcode() -> str:
    """生成与 semantic-patch 能力一致的中英文 Nexus BBCode。"""
    return f"""[center][size=6][b]{RELEASE_NAME}[/b][/size][/center]

[size=5][b]ENGLISH[/b][/size]

Adds lightning weapon visuals, the lightning weapon attribute, and lightning hit damage to all 23 eligible vanilla two-handed spears and giant spears that originally have no special effect. Every included spear has been verified in game.

Halberds are not included. Spears that already use Fire, Lightning, Wind, EMP, Bismuth, Soul, laser, or another special effect are also excluded, so this mod does not overwrite their original element.

[size=4][b]V2 CHANGES[/b][/size]
[list]
[*]Expanded the verified Sidmon Spear recipe to all 23 structurally compatible non-elemental spears.
[*]Uses the native Marni lightning-spear entity and the spear hit-event docking tag.
[*]Removed the old V1 package.
[*]Removed Golden Vanguard and every halberd because the spear hit tag does not produce lightning hit damage for that weapon class.
[/list]

[size=4][b]HOW .CDMOD AND VFS WORK[/b][/size]

This package contains a `semantic-patch` for `iteminfo.pabgb`, not a complete old item table.
[list]
[*]The package stores normalized record selectors and field operations for 23 spear records.
[*]cdloader reads the player's current vanilla table, merges compatible changes in final load order, and builds one final PABGB/PABGH result.
[*]Changes to different records or fields in the same table can normally work together. A later mod wins if both change the same field.
[/list]

The VFS design is similar in concept to MO2 virtual loading: mods remain separate, while the final generated files are mapped to the paths read by the game at runtime. Vanilla PAZ/PAMT archives are not permanently rewritten during normal VFS use. cdloader-VFS is not an MO2 plugin and does not use the same implementation.

[size=4][b]JSON v3.1 AND .CDMOD[/b][/size]

JSON v3.1 is a semantic language for describing table targets, record selectors, fields, operations, and values. `.cdmod` is the complete container and runtime contract that can carry those operations with metadata, dependencies, resources, and integrity information. A real binary schema change may still require an updated cdloader writer; no format can guarantee permanent compatibility with every future game update.

[size=4][b]INSTALLATION[/b][/size]
[list=1]
[*]Download and extract `{ZIP_FILE_NAME}` from this Nexus page.
[*]Do not extract the `.cdmod` inside it. Put `{CDMOD_FILE_NAME}` in `.../Crimson Desert/mods`.
[*]Download the matching cdloader-VFS from [url={VFS_RELEASES_URL}]GitHub Releases[/url]. GitHub provides the loader and converter, not this mod file.
[*]Run cdloader-VFS after installing, removing, disabling, or reordering mods, then verify the result in game.
[/list]

Normally, VFS loading avoids permanent archive writes and reduces the need for Steam file verification, but it cannot guarantee that verification will never be needed.

[size=4][b]CONVERT EXISTING MODS TO .CDMOD[/b][/size]

The converter is available from the same [url={VFS_RELEASES_URL}]GitHub Releases[/url]. Use `Convert one mod` for one source or `Convert a mods directory` for a complete `mods` folder, then review `conversion-report.json`. Supported Format 3 / JSON v3.1 operations become semantic rules; required loose resources remain complete resources; standalone PAZ/PAMT archives remain complete archives and are not automatically converted into field differences. If DMM or another manager previously mounted mods or wrote into game archives, restore clean vanilla files before converting or loading.

[hr]
[size=5][b]中文说明[/b][/size]

为当前原版中 23 把原本没有特殊效果的双手长枪和巨型双手长枪添加武器雷电特效、雷电属性词条和命中雷属性伤害。全部 23 把目标长枪均已完成游戏内实测。

本模组不包含战戟，也不会修改原版已经带有火焰、雷电、风力、EMP、铋、灵魂、激光或其他特殊效果的长枪，避免覆盖它们原有的属性效果。

[size=4][b]V2 更新[/b][/size]
[list]
[*]将西德蒙长枪实机验证成功的配方扩展到全部 23 把结构兼容的无特效长枪。
[*]使用原生 Marni 雷电双手长枪实体与长枪命中事件 docking 标签。
[*]删除旧 V1 包。
[*]移除黄金先锋和所有战戟，因为长枪命中标签无法让该武器类别产生命中雷伤。
[/list]

[size=4][b].CDMOD 与 VFS 原理[/b][/size]

本包是针对 `iteminfo.pabgb` 的 `semantic-patch`，不携带旧版本完整物品表。
[list]
[*]容器保存 23 条长枪记录的明确选择器与字段操作。
[*]cdloader 读取玩家当前版本的原版表，按最终加载顺序合并兼容修改，并重建一份最终 PABGB/PABGH。
[*]同表不同记录或不同字段的修改通常可以共存；同一字段冲突时由后加载模组覆盖。
[/list]

VFS 的使用理念类似 MO2 虚拟加载：模组保持独立，游戏运行时才把最终生成文件映射到原本读取的路径，正常情况下不会永久改写原版 PAZ/PAMT。cdloader-VFS 不是 MO2 插件，也没有使用 MO2 的同一套实现。

[size=4][b]JSON v3.1 与 .CDMOD[/b][/size]

JSON v3.1 是描述目标表、记录选择器、字段、操作和值的语义表格补丁语言；`.cdmod` 是可同时承载语义操作、元数据、依赖、资源和完整性信息的容器与运行规范。游戏若真正改变字段的二进制结构，对应 cdloader writer 仍可能需要更新，不能承诺永远适配所有未来版本。

[size=4][b]安装[/b][/size]
[list=1]
[*]从本 Nexus 页面下载并解压 `{ZIP_FILE_NAME}`。
[*]不要继续解压其中的 `.cdmod`；把 `{CDMOD_FILE_NAME}` 放进 `.../Crimson Desert/mods`。
[*]从 [url={VFS_RELEASES_URL}]GitHub Releases[/url] 下载匹配版本的 cdloader-VFS。GitHub 只提供加载器和转换器，不提供本模组文件。
[*]安装、删除、禁用或调整排序后重新运行 cdloader-VFS，再进入游戏确认效果。
[/list]

正常情况下 VFS 不会永久写入游戏归档，并可减少 Steam 完整性验证需求，但不能承诺永远无需验证。

[size=4][b]转换已有模组为 .CDMOD[/b][/size]

转换器与 cdloader-VFS 位于同一 [url={VFS_RELEASES_URL}]GitHub Releases[/url]。单个来源使用 `Convert one mod`，整个 `mods` 目录使用 `Convert a mods directory`，完成后检查 `conversion-report.json`。受支持的 Format 3 / JSON v3.1 操作会保存为语义规则；loose 资源会保留生效所需完整文件；standalone PAZ/PAMT 会保持完整归档，不会自动反推出字段差异。若 DMM 或其他管理器曾挂载模组或写入游戏归档，转换或加载前必须先恢复纯净原版文件。
"""


def _build_release_info(cover_name: str, cdmod_sha256: str, zip_sha256: str) -> str:
    """生成版本、分类、文件与哈希核对资料。"""
    return f"""英文标题：{RELEASE_NAME}
中文参考名：全部无特殊效果长枪 - 雷电效果
建议分类：Weapons
发布版本：{RELEASE_VERSION}
主文件：{ZIP_FILE_NAME}
内部文件：{CDMOD_FILE_NAME}
组件类型：semantic-patch（iteminfo.pabgb）
目标数量：{EXPECTED_TARGET_COUNT}
字段操作数量：{EXPECTED_OPERATION_COUNT}
封面：{cover_name}
加载器与转换器：{VFS_RELEASES_URL}

ZIP SHA-256：{zip_sha256}
CDMOD SHA-256：{cdmod_sha256}

英文短描述：{FILE_DESCRIPTION}
中文短描述：为 23 把原版无特殊效果长枪添加雷电特效、雷属性词条和命中雷伤；排除战戟及已有特殊效果武器。
"""


def _build_upload_steps() -> str:
    """生成 Nexus 网页实际上传步骤。"""
    return f"""1. Nexus Mods 页面标题使用：{RELEASE_NAME}
2. 分类选择 Weapons，版本填写 {RELEASE_VERSION}。
3. 上传“{ZIP_FILE_NAME}”作为 Main File。
4. 文件描述复制“文件描述-Nexus不超过200字符.txt”的第一行。
5. 页面描述粘贴“模组描述-中英双语-BBCode.txt”全文。
6. 封面上传“模组封面.jpg”。
7. 上传前按“发布信息.txt”核对 ZIP 与内部 .cdmod 的 SHA-256。
8. 不再上传或保留旧 V1 文件。
"""


def _validate_release(result: ReleaseResult) -> None:
    """严格验证容器、目标集合、ZIP 根部、封面和文件描述。"""
    if len(FILE_DESCRIPTION) > 200:
        raise ValueError(f"Nexus 文件描述超过 200 字符：{len(FILE_DESCRIPTION)}")
    if not result.cover_path.is_file() or result.cover_path.stat().st_size <= 0:
        raise ValueError("封面复制失败")
    if result.cover_path.stat().st_size > MAXIMUM_COVER_BYTES:
        raise ValueError("封面超过 2 MiB 上限")
    package = load_cdmod_package(result.cdmod_path)
    identities = {
        (operation.selector.get("key"), operation.selector.get("string_key"))
        for operation in package.operations
    }
    if package.mod_id != PACKAGE_ID or package.version != RELEASE_VERSION:
        raise ValueError("正式 V2 manifest 标识或版本不匹配")
    if len(identities) != EXPECTED_TARGET_COUNT or len(package.operations) != EXPECTED_OPERATION_COUNT:
        raise ValueError("正式 V2 目标数量或字段操作数量不匹配")
    if any("Alebard" in str(name) for _, name in identities):
        raise ValueError("正式 V2 中仍包含战戟")
    with zipfile.ZipFile(result.cdmod_path) as archive:
        manifest = json.loads(archive.read(MANIFEST_PATH).decode("utf-8-sig"))
        components = manifest.get("components")
        if not isinstance(components, list) or [item.get("type") for item in components] != [
            "semantic-patch"
        ]:
            raise ValueError("正式 V2 组件类型不是单一 semantic-patch")
        patch = json.loads(archive.read(SEMANTIC_PATCH_PATH).decode("utf-8-sig"))
        if len(patch["targets"][0]["operations"]) != EXPECTED_OPERATION_COUNT:
            raise ValueError("semantic patch 回读操作数量不匹配")
    with zipfile.ZipFile(result.zip_path) as archive:
        if archive.namelist() != [CDMOD_FILE_NAME]:
            raise ValueError("Nexus ZIP 根部必须只包含一个正式 V2 .cdmod")
        if archive.read(CDMOD_FILE_NAME) != result.cdmod_path.read_bytes():
            raise ValueError("Nexus ZIP 内部 .cdmod 内容不匹配")
    if _sha256(result.cdmod_path) != result.cdmod_sha256:
        raise ValueError("CDMOD SHA-256 回读失败")
    if _sha256(result.zip_path) != result.zip_sha256:
        raise ValueError("ZIP SHA-256 回读失败")


def _sha256(path: Path) -> str:
    """流式计算发布文件 SHA-256。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_bytes(document: dict[str, Any]) -> bytes:
    """输出稳定排序的 UTF-8 JSON。"""
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"


def _parse_args() -> argparse.Namespace:
    """读取正式 V2 源包、封面与发布目录参数。"""
    parser = argparse.ArgumentParser(description="准备全部无特殊效果长枪雷电化 Nexus V2 发布目录")
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(r"G:\SteamLibrary\steamapps\common\Crimson Desert\mods") / SOURCE_FILE_NAME,
    )
    parser.add_argument(
        "--cover",
        type=Path,
        default=Path(r"D:\Downloads\QQ20260714-144037.png"),
    )
    parser.add_argument("--release-root", type=Path, default=Path("nexusmods"))
    return parser.parse_args()


def main() -> int:
    """生成发布目录并输出可核对的最终信息。"""
    args = _parse_args()
    try:
        result = prepare_release(args.source, args.cover, args.release_root)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        print(f"Nexus V2 发布资料构建失败：{exc}", file=sys.stderr)
        return 1
    print(f"发布目录：{result.release_dir}")
    print(f"主文件：{result.zip_path.name}")
    print(f"ZIP SHA-256：{result.zip_sha256}")
    print(f"CDMOD SHA-256：{result.cdmod_sha256}")
    print(f"文件描述字符数：{len(FILE_DESCRIPTION)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
