"""准备无特殊效果双手剑火焰版与雷电版的两个 Nexus 发布目录。"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import zipfile
from dataclasses import dataclass
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


# Nexus 页面和 VFS 下载地址的统一发布版本。
RELEASE_VERSION = "1.13.01"
VFS_RELEASES_URL = "https://github.com/liuhVIP/cdmm/releases"
EXPECTED_TARGET_COUNT = 20
EXPECTED_OPERATION_COUNT = 220
MANIFEST_PATH = "manifest.json"
SEMANTIC_PATCH_PATH = "patches/semantic.json"


@dataclass(frozen=True)
class ReleaseSpec:
    """一个双手剑元素变体的独立 Nexus 发布规格。"""

    slug: str
    release_name: str
    chinese_name: str
    source_file_name: str
    package_id: str
    element_en: str
    element_zh: str
    cover_source: Path

    @property
    def cdmod_file_name(self) -> str:
        """返回发布目录内规范化后的 `.cdmod` 文件名。"""
        return f"{self.release_name}-{RELEASE_VERSION}.cdmod"

    @property
    def zip_file_name(self) -> str:
        """返回 Nexus 主文件 ZIP 名称。"""
        return f"{self.release_name}-{RELEASE_VERSION}.zip"

    @property
    def file_description(self) -> str:
        """返回不超过 200 字符的 Nexus 英文文件描述。"""
        return (
            f"Adds verified {self.element_en.lower()} visuals, attribute, and hit damage to "
            "20 vanilla two-handed swords without existing special effects. Requires cdloader-VFS."
        )


# 用户指定先发布火焰版，再发布雷电版，因此连续使用 15、16。
FLAME_SPEC = ReleaseSpec(
    slug="15-all-non-elemental-two-handed-swords-flame-effects-v2",
    release_name="All Non-Elemental Two-Handed Swords - Flame Effects",
    chinese_name="全部无特殊效果双手剑 · 火焰效果",
    source_file_name="NoSpecialEffect_TwoHandSwords_Flame_V2.field.cdmod",
    package_id="crimsongamemods-itembuffs-no-effect-two-hand-swords-flame-v2",
    element_en="Flame",
    element_zh="火焰",
    cover_source=Path(r"D:\Downloads\QQ20260714-164938.png"),
)

LIGHTNING_SPEC = ReleaseSpec(
    slug="16-all-non-elemental-two-handed-swords-lightning-effects-v2",
    release_name="All Non-Elemental Two-Handed Swords - Lightning Effects",
    chinese_name="全部无特殊效果双手剑 · 雷电效果",
    source_file_name="NoSpecialEffect_TwoHandSwords_Lightning_V2.field.cdmod",
    package_id="crimsongamemods-itembuffs-no-effect-two-hand-swords-lightning-v2",
    element_en="Lightning",
    element_zh="雷电",
    cover_source=Path(r"D:\Downloads\QQ20260714-165210.png"),
)


def prepare_releases(
    game_mods_dir: Path,
    release_root: Path,
    specs: tuple[ReleaseSpec, ...] = (FLAME_SPEC, LIGHTNING_SPEC),
) -> list[tuple[ReleaseSpec, ReleaseResult]]:
    """按顺序生成火焰与雷电两个完整 Nexus 上传目录。"""
    results: list[tuple[ReleaseSpec, ReleaseResult]] = []
    for spec in specs:
        source_cdmod = game_mods_dir / spec.source_file_name
        if not source_cdmod.is_file():
            raise FileNotFoundError(f"待发布源包不存在：{source_cdmod}")
        if not spec.cover_source.is_file():
            raise FileNotFoundError(f"封面文件不存在：{spec.cover_source}")
        release_dir = release_root.resolve() / spec.slug
        release_dir.mkdir(parents=True, exist_ok=True)
        _clear_release_directory(release_dir)

        cdmod_path = release_dir / spec.cdmod_file_name
        _copy_normalized_cdmod(source_cdmod, cdmod_path, spec)
        zip_path = release_dir / spec.zip_file_name
        _write_zip(zip_path, {spec.cdmod_file_name: cdmod_path.read_bytes()})
        cover_path = release_dir / "模组封面.jpg"
        _build_bilingual_cover(spec.cover_source, cover_path, spec)

        result = ReleaseResult(
            release_dir=release_dir,
            cdmod_path=cdmod_path,
            zip_path=zip_path,
            cover_path=cover_path,
            cdmod_sha256=_sha256(cdmod_path),
            zip_sha256=_sha256(zip_path),
        )
        _write_documents(result, spec)
        _validate_release(result, spec)
        results.append((spec, result))
    return results


def _copy_normalized_cdmod(
    source_path: Path,
    output_path: Path,
    spec: ReleaseSpec,
) -> None:
    """规范化发布副本 manifest，不修改游戏 `mods` 中的实测源包。"""
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
                f"In-game verified {spec.element_en.lower()} visuals, weapon attribute, and "
                f"hit damage for {EXPECTED_TARGET_COUNT} vanilla two-handed swords without "
                "existing special effects."
            ),
            "id": spec.package_id,
            "name": spec.release_name,
            "version": RELEASE_VERSION,
        }
    )
    documents[MANIFEST_PATH] = _json_bytes(manifest)
    _write_zip(output_path, documents)


def _build_bilingual_cover(
    source_path: Path,
    output_path: Path,
    spec: ReleaseSpec,
) -> None:
    """使用技能脚本本地生成 1600x900 双语封面。"""
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
            spec.release_name.upper(),
            "-ChineseTitle",
            spec.chinese_name,
            "-MaximumFileSizeBytes",
            str(MAXIMUM_COVER_BYTES),
        ],
        check=True,
    )


def _write_documents(result: ReleaseResult, spec: ReleaseSpec) -> None:
    """写入名称、BBCode、文件描述、发布信息和上传步骤。"""
    release_dir = result.release_dir
    (release_dir / "模组名称.txt").write_text(
        f"{spec.release_name}\n",
        encoding="utf-8",
    )
    (release_dir / "模组描述-中英双语-BBCode.txt").write_text(
        _build_bbcode(spec),
        encoding="utf-8",
    )
    (release_dir / "文件描述-Nexus不超过200字符.txt").write_text(
        f"{spec.file_description}\n\nCharacters: {len(spec.file_description)}\n",
        encoding="utf-8",
    )
    (release_dir / "发布信息.txt").write_text(
        _build_release_info(result, spec),
        encoding="utf-8",
    )
    (release_dir / "上传步骤.txt").write_text(
        _build_upload_steps(spec),
        encoding="utf-8",
    )


def _build_bbcode(spec: ReleaseSpec) -> str:
    """生成与 semantic-patch 能力一致的中英文 Nexus 页面。"""
    return f"""[center][size=6][b]{spec.release_name}[/b][/size][/center]

[size=5][b]ENGLISH[/b][/size]

Adds {spec.element_en.lower()} weapon visuals, the {spec.element_en.lower()} attribute, and {spec.element_en.lower()} hit damage to all 20 eligible vanilla two-handed swords that originally have no special effect. All 20 target swords have been verified in game.

The original Marni Machine Knight two-handed sword is excluded because it already has a special lightning effect. Other weapons with an existing docking effect or elemental passive are also excluded.

[size=4][b]IMPORTANT VARIANT RULE[/b][/size]

The Flame and Lightning editions modify the same ItemInfo fields and are mutually exclusive. Enable only one two-handed sword edition while testing or playing. If both are enabled, the edition placed later in cdloader load order wins.

[size=4][b]HOW .CDMOD AND VFS WORK[/b][/size]

This package contains a `semantic-patch` for `iteminfo.pabgb`, not a complete old item table.
[list]
[*]It stores normalized record selectors and field operations for 20 two-handed sword records.
[*]cdloader reads the player's current vanilla table, merges compatible changes in final load order, and builds one final PABGB/PABGH result.
[*]Changes to different records or fields can normally coexist; a later mod wins when both change the same field.
[/list]

VFS is similar in concept to MO2 virtual loading: mods remain separate and final generated files are mapped to the paths read by the game at runtime. Vanilla PAZ/PAMT archives are not permanently rewritten during normal VFS use. cdloader-VFS is not an MO2 plugin and does not use the same implementation.

[size=4][b]JSON v3.1 AND .CDMOD[/b][/size]

JSON v3.1 is a semantic language for table targets, record selectors, fields, operations, and values. `.cdmod` is the complete container and runtime contract that can carry these operations with metadata, dependencies, resources, and integrity information. A real binary schema change may still require an updated cdloader writer; permanent compatibility with every future update cannot be guaranteed.

[size=4][b]INSTALLATION[/b][/size]
[list=1]
[*]Download and extract `{spec.zip_file_name}` from this Nexus page.
[*]Do not extract the `.cdmod` inside it. Put `{spec.cdmod_file_name}` in `.../Crimson Desert/mods`.
[*]Download the matching cdloader-VFS from [url={VFS_RELEASES_URL}]GitHub Releases[/url]. GitHub provides the loader and converter, not this mod file.
[*]Run cdloader-VFS after installing, removing, disabling, or reordering mods, then verify the result in game.
[/list]

Normally VFS avoids permanent archive writes and reduces the need for Steam verification, but it cannot guarantee verification will never be needed.

[size=4][b]CONVERT EXISTING MODS TO .CDMOD[/b][/size]

The converter is available from the same [url={VFS_RELEASES_URL}]GitHub Releases[/url]. Use `Convert one mod` for one source or `Convert a mods directory` for a complete `mods` folder, then review `conversion-report.json`. Supported Format 3 / JSON v3.1 operations become semantic rules; required loose resources remain complete resources; standalone PAZ/PAMT remains a complete archive. If DMM or another manager previously mounted mods or wrote into game archives, restore clean vanilla files before converting or loading.

[hr]
[size=5][b]中文说明[/b][/size]

为当前原版中 20 把原本没有特殊效果的双手剑添加武器{spec.element_zh}特效、{spec.element_zh}属性词条和命中{spec.element_zh}伤害。全部 20 把目标双手剑均已完成游戏内实测。

原版马罗尼机械骑士双手剑已经自带特殊雷电效果，因此不在修改范围内。其他已经带有 docking 特效或元素被动的武器同样会被排除。

[size=4][b]重要变体规则[/b][/size]

火焰版与雷电版修改相同 ItemInfo 字段，属于互斥变体。测试和游玩时只启用一个双手剑版本；若同时启用，则由 cdloader 加载顺序中更靠后的版本覆盖生效。

[size=4][b].CDMOD 与 VFS 原理[/b][/size]

本包是针对 `iteminfo.pabgb` 的 `semantic-patch`，不携带旧版本完整物品表。
[list]
[*]容器保存 20 条双手剑记录的明确选择器和字段操作。
[*]cdloader 读取玩家当前版本原版表，按最终加载顺序合并兼容修改，并重建一份最终 PABGB/PABGH。
[*]同表不同记录或不同字段的修改通常可以共存；同一字段冲突时由后加载模组覆盖。
[/list]

VFS 的使用理念类似 MO2 虚拟加载：模组保持独立，运行时才把最终生成文件映射到游戏原本读取的路径，正常情况下不会永久改写原版 PAZ/PAMT。cdloader-VFS 不是 MO2 插件，也没有使用 MO2 的同一套实现。

[size=4][b]JSON v3.1 与 .CDMOD[/b][/size]

JSON v3.1 是描述目标表、记录选择器、字段、操作和值的语义语言；`.cdmod` 是可承载这些操作以及元数据、依赖、资源和完整性信息的容器与运行规范。游戏若真正改变字段二进制结构，对应 cdloader writer 仍可能需要更新，不能承诺永远适配所有未来版本。

[size=4][b]安装[/b][/size]
[list=1]
[*]从本 Nexus 页面下载并解压 `{spec.zip_file_name}`。
[*]不要继续解压其中的 `.cdmod`；把 `{spec.cdmod_file_name}` 放进 `.../Crimson Desert/mods`。
[*]从 [url={VFS_RELEASES_URL}]GitHub Releases[/url] 下载匹配版本的 cdloader-VFS。GitHub 只提供加载器和转换器，不提供本模组文件。
[*]安装、删除、禁用或调整排序后重新运行 cdloader-VFS，再进入游戏确认效果。
[/list]

正常情况下 VFS 不会永久写入游戏归档，并可减少 Steam 完整性验证需求，但不能承诺永远无需验证。

[size=4][b]转换已有模组为 .CDMOD[/b][/size]

转换器与 cdloader-VFS 位于同一 [url={VFS_RELEASES_URL}]GitHub Releases[/url]。单个来源使用 `Convert one mod`，整个 `mods` 目录使用 `Convert a mods directory`，完成后检查 `conversion-report.json`。受支持的 Format 3 / JSON v3.1 操作会保存为语义规则；loose 资源保留完整文件；standalone PAZ/PAMT 保持完整归档。若 DMM 或其他管理器曾挂载模组或写入游戏归档，转换或加载前必须先恢复纯净原版文件。
"""


def _build_release_info(result: ReleaseResult, spec: ReleaseSpec) -> str:
    """生成分类、版本、验证状态、哈希与文件核对资料。"""
    return f"""英文标题：{spec.release_name}
中文参考名：{spec.chinese_name}
建议分类：Weapons
发布版本：{RELEASE_VERSION}
主文件：{spec.zip_file_name}
内部文件：{spec.cdmod_file_name}
组件类型：semantic-patch（iteminfo.pabgb）
目标数量：{EXPECTED_TARGET_COUNT}
字段操作数量：{EXPECTED_OPERATION_COUNT}
游戏内验证：20 把目标双手剑已确认正常生效
封面：{result.cover_path.name}
加载器与转换器：{VFS_RELEASES_URL}

ZIP SHA-256：{result.zip_sha256}
CDMOD SHA-256：{result.cdmod_sha256}

英文短描述：{spec.file_description}
中文短描述：为 20 把原版无特殊效果双手剑添加{spec.element_zh}特效、{spec.element_zh}属性词条和命中{spec.element_zh}伤害；排除已有特殊效果武器。
"""


def _build_upload_steps(spec: ReleaseSpec) -> str:
    """生成单个 Nexus 页面对应的上传步骤。"""
    return f"""1. Nexus Mods 页面标题使用：{spec.release_name}
2. 分类选择 Weapons，版本填写 {RELEASE_VERSION}。
3. 上传“{spec.zip_file_name}”作为 Main File。
4. 文件描述复制“文件描述-Nexus不超过200字符.txt”的第一行。
5. 页面描述粘贴“模组描述-中英双语-BBCode.txt”全文。
6. 封面上传“模组封面.jpg”。
7. 上传前按“发布信息.txt”核对 ZIP 与内部 .cdmod 的 SHA-256。
8. 火焰版与雷电版建立两个独立 Nexus 页面，不要合并成一个主文件。
"""


def _validate_release(result: ReleaseResult, spec: ReleaseSpec) -> None:
    """严格验证容器、目标、ZIP、封面、哈希和 Nexus 文件描述。"""
    if len(spec.file_description) > 200:
        raise ValueError(f"Nexus 文件描述超过 200 字符：{len(spec.file_description)}")
    if not result.cover_path.is_file() or result.cover_path.stat().st_size <= 0:
        raise ValueError(f"{spec.element_zh}版封面缺失")
    if result.cover_path.stat().st_size > MAXIMUM_COVER_BYTES:
        raise ValueError(f"{spec.element_zh}版封面超过 2 MiB")
    package = load_cdmod_package(result.cdmod_path)
    identities = {
        (operation.selector.get("key"), operation.selector.get("string_key"))
        for operation in package.operations
    }
    if package.mod_id != spec.package_id or package.version != RELEASE_VERSION:
        raise ValueError(f"{spec.element_zh}版 manifest 标识或版本错误")
    if len(identities) != EXPECTED_TARGET_COUNT:
        raise ValueError(f"{spec.element_zh}版目标数量错误")
    if len(package.operations) != EXPECTED_OPERATION_COUNT:
        raise ValueError(f"{spec.element_zh}版操作数量错误")
    if (1_001_062, "Marni_MachineKnight_TwoHandSword") in identities:
        raise ValueError(f"{spec.element_zh}版错误包含已有雷电效果双手剑")
    with zipfile.ZipFile(result.cdmod_path) as archive:
        manifest = json.loads(archive.read(MANIFEST_PATH).decode("utf-8-sig"))
        component_types = [item.get("type") for item in manifest.get("components", [])]
        if component_types != ["semantic-patch"]:
            raise ValueError(f"{spec.element_zh}版不是单一 semantic-patch")
        patch = json.loads(archive.read(SEMANTIC_PATCH_PATH).decode("utf-8-sig"))
        if len(patch["targets"][0]["operations"]) != EXPECTED_OPERATION_COUNT:
            raise ValueError(f"{spec.element_zh}版语义操作回读失败")
    with zipfile.ZipFile(result.zip_path) as archive:
        if archive.namelist() != [spec.cdmod_file_name]:
            raise ValueError(f"{spec.element_zh}版 Nexus ZIP 根部文件错误")
        if archive.read(spec.cdmod_file_name) != result.cdmod_path.read_bytes():
            raise ValueError(f"{spec.element_zh}版 Nexus ZIP 内部包不匹配")
    if _sha256(result.cdmod_path) != result.cdmod_sha256:
        raise ValueError(f"{spec.element_zh}版 CDMOD SHA-256 回读失败")
    if _sha256(result.zip_path) != result.zip_sha256:
        raise ValueError(f"{spec.element_zh}版 ZIP SHA-256 回读失败")


def _parse_args() -> argparse.Namespace:
    """读取游戏 mods 与 Nexus 发布根目录。"""
    parser = argparse.ArgumentParser(description="准备无特效双手剑火焰与雷电 Nexus 发布")
    parser.add_argument(
        "--game-mods-dir",
        type=Path,
        default=Path(r"G:\SteamLibrary\steamapps\common\Crimson Desert\mods"),
    )
    parser.add_argument("--release-root", type=Path, default=Path("nexusmods"))
    return parser.parse_args()


def main() -> int:
    """构建两个 Nexus 发布目录并输出最终核对信息。"""
    args = _parse_args()
    try:
        results = prepare_releases(args.game_mods_dir, args.release_root)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError, zipfile.BadZipFile) as exc:
        print(f"双手剑 Nexus 发布资料构建失败：{exc}", file=sys.stderr)
        return 1
    for spec, result in results:
        print(f"发布目录：{result.release_dir}")
        print(f"主文件：{result.zip_path.name}")
        print(f"ZIP SHA-256：{result.zip_sha256}")
        print(f"CDMOD SHA-256：{result.cdmod_sha256}")
        print(f"封面字节数：{result.cover_path.stat().st_size}")
        print(f"文件描述字符数：{len(spec.file_description)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
