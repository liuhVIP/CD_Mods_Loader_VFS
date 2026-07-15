"""在已验证 v2.8 稳定基线上显式加入女性飞行动画。

v2.8 使用 PHM 男性状态链，因此仅省略 Male Glide 变换不会自动切回女性飞行。
本工具反向使用 Male Glide 的映射关系，把原版 PHW 女性 PAA 写入对应 PHM 目标。
男性链共用一条落地动画，而女性有普通/持盾两条，因此共享目标固定选择普通女性落地。
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path

from cdmm.archive.pamt import parse_pamt
from cdmm.common.models import PazEntry
from cdmm.services.cdmod_converter import (
    CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE,
    CDMOD_FORMAT_NAME,
    CDMOD_FORMAT_VERSION,
    _write_cdmod_zip,
)
from cdmm.services.cdmod_package import CdmodResourceTransform, load_cdmod_package
from cdmm.services.json_loader import extract_plaintext
from cdmm.tools.build_female_walk_single_branch_mod import _file_document

# 两个输入包都经过实机验证，必须锁定哈希避免误用历史版本。
STABLE_BASELINE_NAME = "Female Outside Combat Native Male Walk - Male Combat v2.8-test.cdmod"
STABLE_BASELINE_SHA256 = "b5960c4c9ac45dfcc2750d70b59031fe9631eb580f62ea9ee8370c09ea7aed4c"
MALE_GLIDE_NAME = "Male Glide Animation-2.8.cdmod.123"
MALE_GLIDE_SHA256 = "3dea4647dc488d6e6a824036f8913d02e17df2fb07fd96af9ff2f7539fb82343"

# v2.8 原有 57 项资源；反向去重后新增 55 项女性飞行 PAA。
BASELINE_FILE_COUNT = 57
FEMALE_GLIDE_FILE_COUNT = 55
TOTAL_FILE_COUNT = BASELINE_FILE_COUNT + FEMALE_GLIDE_FILE_COUNT

# 男性共用落地目标对应两条女性源，固定选择不带持盾后缀的普通女性落地。
SHARED_MALE_LANDING_BASENAME = "cd_phm_basic_00_00_nor_move_gliding_off_00.paa"
FEMALE_SHIELD_SUFFIX = "_at_shield_01.paa"

MOD_NAME = "New Female Animations for Kliff - Stable - Explicit Female Glide"
MOD_VERSION = "1.13.01-female-glide-test"


def _sha256(path: Path) -> str:
    """计算文件 SHA-256。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _verify_input(path: Path, expected_sha256: str) -> None:
    """验证构建输入与锁定文件一致。"""
    if not path.is_file():
        raise FileNotFoundError(f"缺少构建输入：{path}")
    actual = _sha256(path)
    if actual != expected_sha256:
        raise ValueError(
            f"{path.name} SHA-256 不匹配：预期 {expected_sha256}，实际 {actual}"
        )


def _full_virtual_path(entry: PazEntry) -> str:
    """组合 PAMT 目录与扁平文件名。"""
    basename = Path(entry.path.replace("\\", "/")).name
    directory = entry.resolved_dir_path.replace("\\", "/").strip("/")
    return f"{directory}/{basename}" if directory else basename


def _basename_index(entries: list[PazEntry]) -> dict[str, list[PazEntry]]:
    """按小写 basename 建立 PAMT 索引。"""
    index: dict[str, list[PazEntry]] = defaultdict(list)
    for entry in entries:
        basename = Path(entry.path.replace("\\", "/")).name.casefold()
        index[basename].append(entry)
    return dict(index)


def _find_unique_entry(
    index: dict[str, list[PazEntry]],
    declared_path: str,
) -> PazEntry:
    """按声明路径的 basename 唯一定位原版 PAMT entry。"""
    basename = Path(declared_path.replace("\\", "/")).name.casefold()
    matches = index.get(basename, [])
    if len(matches) != 1:
        raise ValueError(f"原版 0009/{basename} 预期唯一命中，实际 {len(matches)}")
    return matches[0]


def _select_reverse_copy_operations(
    operations: tuple[CdmodResourceTransform, ...],
) -> tuple[CdmodResourceTransform, ...]:
    """把 56 条男性到女性复制映射归并为 55 个唯一男性目标。"""
    copies = [operation for operation in operations if operation.op == "copy-entry"]
    if len(copies) != 56:
        raise ValueError(f"Male Glide copy-entry 数量异常：{len(copies)}")

    by_male_target: dict[tuple[str, str], list[CdmodResourceTransform]] = defaultdict(list)
    for operation in copies:
        if not operation.source or not operation.source_pamt_dir:
            raise ValueError("Male Glide copy-entry 缺少男性源声明")
        by_male_target[(operation.source_pamt_dir, operation.source)].append(operation)

    selected: list[CdmodResourceTransform] = []
    duplicate_groups = []
    for (_pamt_dir, male_target), candidates in by_male_target.items():
        if len(candidates) == 1:
            selected.append(candidates[0])
            continue
        duplicate_groups.append((male_target, candidates))
        basename = Path(male_target.replace("\\", "/")).name.casefold()
        ordinary = [
            operation
            for operation in candidates
            if not operation.target.casefold().endswith(FEMALE_SHIELD_SUFFIX)
        ]
        if basename != SHARED_MALE_LANDING_BASENAME or len(candidates) != 2 or len(ordinary) != 1:
            raise ValueError(f"出现未知的反向共享目标：{male_target}")
        selected.append(ordinary[0])

    if len(duplicate_groups) != 1 or len(selected) != FEMALE_GLIDE_FILE_COUNT:
        raise ValueError("Male Glide 反向去重结构异常")
    return tuple(selected)


def _load_explicit_female_glide(
    game_dir: Path,
    male_glide_path: Path,
) -> tuple[tuple[str, str, bytes], ...]:
    """从干净原版 PHW entry 提取 55 项女性飞行载荷并映射到 PHM 目标。"""
    _verify_input(male_glide_path, MALE_GLIDE_SHA256)
    male_glide = load_cdmod_package(male_glide_path)
    operations = tuple(
        operation
        for patch in male_glide.resource_patches
        for operation in patch.operations
    )
    selected = _select_reverse_copy_operations(operations)

    entries = parse_pamt(game_dir / "0009" / "0.pamt")
    index = _basename_index(entries)
    replacements: list[tuple[str, str, bytes]] = []
    for operation in selected:
        if not operation.source or not operation.source_pamt_dir:
            raise ValueError("反向复制缺少 PHM 目标")
        female_entry = _find_unique_entry(index, operation.target)
        male_entry = _find_unique_entry(index, operation.source)
        female_content, _ = extract_plaintext(female_entry)
        male_content, _ = extract_plaintext(male_entry)
        if female_content == male_content:
            raise ValueError(f"女性/男性飞行载荷意外相同：{operation.source}")
        replacements.append(
            (
                _full_virtual_path(male_entry),
                operation.source_pamt_dir,
                female_content,
            )
        )

    targets = [(pamt_dir, target) for target, pamt_dir, _content in replacements]
    if len(replacements) != FEMALE_GLIDE_FILE_COUNT or len(targets) != len(set(targets)):
        raise ValueError("女性飞行反向替换目标数量或唯一性异常")
    return tuple(replacements)


def _load_baseline(path: Path) -> tuple[tuple[str, str, bytes], ...]:
    """读取并验证已实测可进入游戏的 v2.8 基线。"""
    _verify_input(path, STABLE_BASELINE_SHA256)
    package = load_cdmod_package(path)
    files = tuple(file for patch in package.file_patches for file in patch.files)
    if (
        len(files) != BASELINE_FILE_COUNT
        or package.resource_patches
        or package.legacy_json_patches
        or package.standalone_archives
    ):
        raise ValueError("v2.8 基线组件结构异常")
    return tuple((file.target, file.pamt_dir, file.content) for file in files)


def build_mod(
    game_dir: Path,
    baseline_path: Path,
    male_glide_path: Path,
    output_path: Path,
) -> Path:
    """生成 v2.8 加 55 项显式女性飞行动画的单变量测试包。"""
    baseline = _load_baseline(baseline_path)
    female_glide = _load_explicit_female_glide(game_dir, male_glide_path)
    combined = (*baseline, *female_glide)
    identities = [(pamt_dir, target) for target, pamt_dir, _content in combined]
    if len(combined) != TOTAL_FILE_COUNT or len(identities) != len(set(identities)):
        raise ValueError("稳定基线与女性飞行目标发生冲突")

    replacements: list[dict[str, object]] = []
    documents: dict[str, dict[str, object] | bytes] = {}
    expected: dict[tuple[str, str], bytes] = {}
    for index, (target, pamt_dir, content) in enumerate(combined):
        payload_path = f"assets/{index:03d}/{Path(target).name}"
        replacements.append(
            _file_document(
                target=target,
                pamt_dir=pamt_dir,
                payload_path=payload_path,
                content=content,
            )
        )
        documents[payload_path] = content
        expected[(pamt_dir, target)] = content

    documents["manifest.json"] = {
        "author": "Khione, Slinky, CDMM",
        "components": [
            {
                "file_count": len(replacements),
                "path": "files/replacements.json",
                "type": CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE,
            }
        ],
        "dependencies": [],
        "description": (
            "Preserves the byte-verified v2.8 stable baseline and changes only "
            "55 unique PHM gliding PAA targets to native PHW female payloads."
        ),
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": "cdmm.new-female-animations-for-kliff.stable.explicit-female-glide",
        "name": MOD_NAME,
        "source": {
            "baseline_package": STABLE_BASELINE_NAME,
            "baseline_sha256": STABLE_BASELINE_SHA256,
            "female_glide_mapping": "inverse-male-glide-copy-entry",
            "female_glide_target_count": FEMALE_GLIDE_FILE_COUNT,
            "male_glide_mapping_sha256": MALE_GLIDE_SHA256,
        },
        "version": MOD_VERSION,
    }
    documents["files/replacements.json"] = {"schema": 1, "files": replacements}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_cdmod_zip(output_path, documents)

    package = load_cdmod_package(output_path)
    files = [file for patch in package.file_patches for file in patch.files]
    parsed = {(file.pamt_dir, file.target): file.content for file in files}
    if len(files) != TOTAL_FILE_COUNT or parsed != expected:
        raise ValueError("生成后的女性飞行包载荷与预期不一致")
    if package.resource_patches or package.legacy_json_patches or package.standalone_archives:
        raise ValueError("生成后的女性飞行包包含未声明组件")
    return output_path


def main() -> int:
    """生成项目 build 目录下的显式女性飞行测试包。"""
    project_dir = Path(__file__).resolve().parents[1]
    game_dir = Path(r"G:\SteamLibrary\steamapps\common\Crimson Desert")
    baseline_path = (
        project_dir
        / "docs"
        / "克里夫女性动画研究归档"
        / "测试版本"
        / STABLE_BASELINE_NAME
    )
    male_glide_path = game_dir / "mods" / MALE_GLIDE_NAME
    output_path = (
        project_dir
        / "build"
        / "new-female-animations-for-kliff"
        / "stable-explicit-female-glide-test.cdmod"
    )
    result = build_mod(game_dir, baseline_path, male_glide_path, output_path)
    print(f"已生成：{result}")
    print(f"资源总数：{TOTAL_FILE_COUNT}")
    print(f"女性飞行替换：{FEMALE_GLIDE_FILE_COUNT}")
    print(f"SHA-256：{_sha256(result)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
