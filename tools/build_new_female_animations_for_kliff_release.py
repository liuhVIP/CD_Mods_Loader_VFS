"""构建“全新克里夫女性动画”四个正式发布变体。

本工具只组合已经实机验证的 v2.8/v3.6 动画资源与 Male Glide 资源变换，
不重新计算或微调任何动画数据。四个变体互斥，每个上传 ZIP 根部只包含一个
完整自包含的 ``.cdmod`` 文件。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cdmm.services.cdmod_converter import (
    CDMOD_RESOURCE_TRANSFORM_COMPONENT_TYPE,
    DETERMINISTIC_ZIP_TIMESTAMP,
    _write_cdmod_zip,
)
from cdmm.services.cdmod_package import load_cdmod_package

# 正式发布版本必须与 Nexus 页面和四个文件名保持一致。
RELEASE_VERSION = "1.13.01"

# 游戏与项目的默认路径只用于命令行省参，可通过参数覆盖。
DEFAULT_GAME_DIR = Path(r"G:\SteamLibrary\steamapps\common\Crimson Desert")
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[1] / "build" / "new-female-animations-for-kliff"

# 三个经过验证的输入包必须锁定哈希，防止误拿历史实验包生成正式成品。
STABLE_BASELINE_NAME = "Female Outside Combat Native Male Walk - Male Combat v2.8-test.cdmod"
STABLE_BASELINE_SHA256 = "b5960c4c9ac45dfcc2750d70b59031fe9631eb580f62ea9ee8370c09ea7aed4c"
EXPERIMENTAL_BASELINE_NAME = (
    "Female Outside Combat Flat Aligned Shallow Male Steep Slope - Male Combat "
    "v3.6-test.cdmod"
)
EXPERIMENTAL_BASELINE_SHA256 = "999bcbf007bab6962583a818e4019a92197a3afe622f545d50ba8e114a1dc415"
MALE_GLIDE_NAME = "Male Glide Animation-2.8.cdmod.123"
MALE_GLIDE_SHA256 = "3dea4647dc488d6e6a824036f8913d02e17df2fb07fd96af9ff2f7539fb82343"

# 组件数量是正式包结构的硬约束。
STABLE_FILE_COUNT = 57
EXPERIMENTAL_FILE_COUNT = 73
MALE_GLIDE_OPERATION_COUNT = 59
RESOURCE_PATCH_PATH = "patches/resources.json"


@dataclass(frozen=True)
class ReleaseVariant:
    """一个互斥发布变体的固定元数据。"""

    key: str
    display_name: str
    mod_id: str
    baseline_name: str
    baseline_sha256: str
    expected_file_count: int
    male_glide: bool
    author: str
    description: str


@dataclass(frozen=True)
class ReleaseArtifact:
    """一个已生成并验证的 CDMOD 与上传 ZIP。"""

    variant: ReleaseVariant
    cdmod_path: Path
    zip_path: Path
    cdmod_sha256: str
    zip_sha256: str


# 四个版本必须四选一，名称明确表达稳定/实验与飞行动画差异。
RELEASE_VARIANTS = (
    ReleaseVariant(
        key="stable-male-glide",
        display_name="New Female Animations for Kliff - Stable - Male Glide",
        mod_id="cdmm.new-female-animations-for-kliff.stable.male-glide",
        baseline_name=STABLE_BASELINE_NAME,
        baseline_sha256=STABLE_BASELINE_SHA256,
        expected_file_count=STABLE_FILE_COUNT,
        male_glide=True,
        author="Khione, Slinky, Deletriuz, CDMM",
        description=(
            "Stable female non-combat animations with native male ordinary walking, "
            "native male combat, and the embedded Male Glide resource transform."
        ),
    ),
    ReleaseVariant(
        key="stable-female-glide",
        display_name="New Female Animations for Kliff - Stable - Female Glide",
        mod_id="cdmm.new-female-animations-for-kliff.stable.female-glide",
        baseline_name=STABLE_BASELINE_NAME,
        baseline_sha256=STABLE_BASELINE_SHA256,
        expected_file_count=STABLE_FILE_COUNT,
        male_glide=False,
        author="Khione, Slinky, CDMM",
        description=(
            "Stable female non-combat animations with native male ordinary walking "
            "and native male combat while preserving the native female glide."
        ),
    ),
    ReleaseVariant(
        key="experimental-male-glide",
        display_name=(
            "New Female Animations for Kliff - Experimental Female Walk - Male Glide"
        ),
        mod_id="cdmm.new-female-animations-for-kliff.experimental.male-glide",
        baseline_name=EXPERIMENTAL_BASELINE_NAME,
        baseline_sha256=EXPERIMENTAL_BASELINE_SHA256,
        expected_file_count=EXPERIMENTAL_FILE_COUNT,
        male_glide=True,
        author="Andyground, Khione, Slinky, Deletriuz, CDMM",
        description=(
            "Experimental female flat-ground walking, male combat, male steep-slope "
            "fallback, and the embedded Male Glide resource transform."
        ),
    ),
    ReleaseVariant(
        key="experimental-female-glide",
        display_name=(
            "New Female Animations for Kliff - Experimental Female Walk - Female Glide"
        ),
        mod_id="cdmm.new-female-animations-for-kliff.experimental.female-glide",
        baseline_name=EXPERIMENTAL_BASELINE_NAME,
        baseline_sha256=EXPERIMENTAL_BASELINE_SHA256,
        expected_file_count=EXPERIMENTAL_FILE_COUNT,
        male_glide=False,
        author="Andyground, Khione, Slinky, CDMM",
        description=(
            "Experimental female flat-ground walking, male combat, male steep-slope "
            "fallback, and the native female glide."
        ),
    ),
)


def _sha256(path: Path) -> str:
    """计算文件 SHA-256。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _verify_input(path: Path, expected_sha256: str) -> None:
    """验证输入文件存在且与锁定哈希一致。"""
    if not path.is_file():
        raise FileNotFoundError(f"缺少构建输入：{path}")
    actual = _sha256(path)
    if actual != expected_sha256:
        raise ValueError(
            f"构建输入 SHA-256 不匹配：{path.name}，"
            f"预期 {expected_sha256}，实际 {actual}"
        )


def _read_archive_documents(path: Path) -> dict[str, bytes]:
    """读取 CDMOD 全部文件并拒绝重复名称或目录项。"""
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError(f"输入包包含重复 ZIP 路径：{path.name}")
        if any(name.endswith("/") for name in names):
            raise ValueError(f"输入包不应包含目录项：{path.name}")
        return {name: archive.read(name) for name in names}


def _read_json_bytes(payload: bytes, label: str) -> dict[str, Any]:
    """读取 UTF-8 JSON 对象。"""
    value = json.loads(payload.decode("utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} 根节点必须是对象")
    return value


def _load_male_glide(mods_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """读取并验证 Male Glide 清单和 59 条资源变换。"""
    path = mods_dir / MALE_GLIDE_NAME
    _verify_input(path, MALE_GLIDE_SHA256)
    documents = _read_archive_documents(path)
    manifest = _read_json_bytes(documents["manifest.json"], f"{MALE_GLIDE_NAME}/manifest.json")
    resources = _read_json_bytes(documents[RESOURCE_PATCH_PATH], RESOURCE_PATCH_PATH)
    operations = resources.get("operations")
    if not isinstance(operations, list) or len(operations) != MALE_GLIDE_OPERATION_COUNT:
        raise ValueError("Male Glide 资源变换数量异常")
    copy_count = sum(item.get("op") == "copy-entry" for item in operations if isinstance(item, dict))
    byte_count = sum(item.get("op") == "replace-bytes" for item in operations if isinstance(item, dict))
    if (copy_count, byte_count) != (56, 3):
        raise ValueError("Male Glide 操作类型数量异常")
    return manifest, resources


def _build_manifest(
    baseline_manifest: dict[str, Any],
    variant: ReleaseVariant,
    male_glide_manifest: dict[str, Any],
) -> dict[str, Any]:
    """基于锁定基线生成正式清单。"""
    components = list(baseline_manifest.get("components") or [])
    if len(components) != 1 or components[0].get("type") != "file-replacement":
        raise ValueError(f"{variant.baseline_name} 的基线组件结构异常")
    if variant.male_glide:
        components.append(
            {
                "operation_count": MALE_GLIDE_OPERATION_COUNT,
                "path": RESOURCE_PATCH_PATH,
                "type": CDMOD_RESOURCE_TRANSFORM_COMPONENT_TYPE,
            }
        )

    manifest = dict(baseline_manifest)
    manifest.update(
        {
            "author": variant.author,
            "components": components,
            "dependencies": [],
            "description": variant.description,
            "id": variant.mod_id,
            "name": variant.display_name,
            "source": {
                "animation_baseline": variant.baseline_name,
                "animation_baseline_sha256": variant.baseline_sha256,
                "glide": "male" if variant.male_glide else "female",
                "male_glide_author": (
                    male_glide_manifest.get("author") if variant.male_glide else None
                ),
                "male_glide_package_sha256": (
                    MALE_GLIDE_SHA256 if variant.male_glide else None
                ),
                "release_line": variant.key,
            },
            "version": RELEASE_VERSION,
        }
    )
    return manifest


def _write_upload_zip(cdmod_path: Path, output_path: Path) -> None:
    """生成根部只含一个 CDMOD 的确定性 Nexus 上传 ZIP。"""
    with zipfile.ZipFile(
        output_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        info = zipfile.ZipInfo(cdmod_path.name, DETERMINISTIC_ZIP_TIMESTAMP)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o100644 << 16
        archive.writestr(info, cdmod_path.read_bytes(), compresslevel=9)


def _verify_artifact(
    baseline_path: Path,
    artifact_path: Path,
    upload_path: Path,
    variant: ReleaseVariant,
) -> None:
    """严格回读成品并与基线资源逐项比较。"""
    baseline = load_cdmod_package(baseline_path)
    package = load_cdmod_package(artifact_path)
    baseline_files = [item for patch in baseline.file_patches for item in patch.files]
    files = [item for patch in package.file_patches for item in patch.files]
    baseline_payloads = {
        (item.pamt_dir, item.target): item.content for item in baseline_files
    }
    payloads = {(item.pamt_dir, item.target): item.content for item in files}
    operation_count = sum(len(patch.operations) for patch in package.resource_patches)

    if package.version != RELEASE_VERSION or package.mod_id != variant.mod_id:
        raise ValueError(f"{artifact_path.name} 正式身份字段异常")
    if len(files) != variant.expected_file_count or payloads != baseline_payloads:
        raise ValueError(f"{artifact_path.name} 动画资源与锁定基线不一致")
    expected_operations = MALE_GLIDE_OPERATION_COUNT if variant.male_glide else 0
    if operation_count != expected_operations:
        raise ValueError(f"{artifact_path.name} 飞行操作数量异常")
    if (
        package.operations
        or package.localization_patches
        or package.legacy_json_patches
        or package.standalone_archives
        or any(item.allow_new or item.allow_table_replace for item in files)
    ):
        raise ValueError(f"{artifact_path.name} 包含未声明的组件或权限")

    with zipfile.ZipFile(upload_path) as archive:
        if archive.namelist() != [artifact_path.name]:
            raise ValueError(f"{upload_path.name} 根目录结构异常")
        if archive.read(artifact_path.name) != artifact_path.read_bytes():
            raise ValueError(f"{upload_path.name} 内部 CDMOD 字节不一致")


def build_release(mods_dir: Path, output_dir: Path) -> tuple[ReleaseArtifact, ...]:
    """构建并验证四个互斥正式变体。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    male_glide_manifest, male_glide_resources = _load_male_glide(mods_dir)
    results: list[ReleaseArtifact] = []
    for variant in RELEASE_VARIANTS:
        baseline_path = mods_dir / variant.baseline_name
        _verify_input(baseline_path, variant.baseline_sha256)
        documents = _read_archive_documents(baseline_path)
        baseline_manifest = _read_json_bytes(
            documents.pop("manifest.json"),
            f"{variant.baseline_name}/manifest.json",
        )
        documents["manifest.json"] = _build_manifest(
            baseline_manifest,
            variant,
            male_glide_manifest,
        )
        if variant.male_glide:
            documents[RESOURCE_PATCH_PATH] = male_glide_resources

        cdmod_path = output_dir / f"{variant.display_name} v{RELEASE_VERSION}.cdmod"
        zip_path = output_dir / f"{variant.display_name} v{RELEASE_VERSION}.zip"
        _write_cdmod_zip(cdmod_path, documents)
        _write_upload_zip(cdmod_path, zip_path)
        _verify_artifact(baseline_path, cdmod_path, zip_path, variant)
        results.append(
            ReleaseArtifact(
                variant=variant,
                cdmod_path=cdmod_path,
                zip_path=zip_path,
                cdmod_sha256=_sha256(cdmod_path),
                zip_sha256=_sha256(zip_path),
            )
        )
    return tuple(results)


def main() -> int:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mods-dir",
        type=Path,
        default=DEFAULT_GAME_DIR / "mods",
        help="包含三个锁定输入包的游戏 mods 目录",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="四个 CDMOD 与四个上传 ZIP 的输出目录",
    )
    args = parser.parse_args()

    artifacts = build_release(args.mods_dir.resolve(), args.output_dir.resolve())
    for artifact in artifacts:
        print(artifact.cdmod_path)
        print(f"  CDMOD SHA-256: {artifact.cdmod_sha256}")
        print(f"  ZIP SHA-256:   {artifact.zip_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
