"""在 v2.8 稳定基线上只选择 PHW 女性飞行节点。

Kliff 与 Damian 共用 PHM 目录下的飞行 PAAC，其中同时存在 PHM/PHW 候选节点。
本工具给四条玩家 CharacterInfo 记录写入私有选择标签，并只把两份飞行专用
PAAC 内的 PHW 标签改成该值。普通移动、战斗 PAAC、PAA 和武器图保持不变。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from cdmm.common.constants import HASH_SEED
from cdmm.common.hashlittle import hashlittle
from cdmm.services.cdmod_converter import (
    CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE,
    CDMOD_FORMAT_NAME,
    CDMOD_FORMAT_VERSION,
    CDMOD_LEGACY_JSON_COMPONENT_TYPE,
    _write_cdmod_zip,
)
from cdmm.services.cdmod_package import load_cdmod_package
from cdmm.services.json_loader import extract_plaintext
from cdmm.tools.build_female_locomotion_male_combat_graph_mod import _find_entry
from cdmm.tools.build_female_walk_single_branch_mod import _file_document

# 已实机确认可进入游戏且除飞行外满足稳定要求的 v2.8 基线。
STABLE_BASELINE_NAME = "Female Outside Combat Native Male Walk - Male Combat v2.8-test.cdmod"
STABLE_BASELINE_SHA256 = "b5960c4c9ac45dfcc2750d70b59031fe9631eb580f62ea9ee8370c09ea7aed4c"
BASELINE_FILE_COUNT = 57

# 飞行专用 PAAC 的当前 1.13.01 身份约束，更新后必须重新审计。
GLIDE_PAAC_SPECS = (
    (
        "actionchart/bin__/loweraction/1_pc/1_phm/basic_lower_glide.paac",
        79_279,
        15,
    ),
    (
        "actionchart/bin__/upperaction/1_pc/1_phm/basic_upper_glide.paac",
        62_982,
        23,
    ),
)
GLIDE_PAAC_PAMT_DIR = "0010"

# 私有标签只存在于本包修改的飞行 PAAC，其他动作图找不到时继续走男性默认分支。
PHW_SELECTOR_HASH = hashlittle(b"phw", HASH_SEED)
GLIDE_SELECTOR_NAME = "cdmm_phw_glide"
GLIDE_SELECTOR_HASH = hashlittle(GLIDE_SELECTOR_NAME.encode("ascii"), HASH_SEED)
PHW_SELECTOR_BYTES = PHW_SELECTOR_HASH.to_bytes(4, "little")
GLIDE_SELECTOR_BYTES = GLIDE_SELECTOR_HASH.to_bytes(4, "little")

# 当前 CharacterInfo 中四条玩家记录的 lookup_84 绝对偏移。
CHARACTER_LOOKUP84_OFFSETS = (
    (464, "Kliff"),
    (4_503, "Kliff_Clone"),
    (8_414, "Kliff_AI"),
    (43_321, "PlayerAll"),
)

MOD_NAME = "New Female Animations for Kliff - Stable - Selective Female Glide"
MOD_VERSION = "1.13.01-female-glide-selector-test"


def _sha256(path: Path) -> str:
    """计算文件 SHA-256。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_baseline(path: Path) -> tuple[tuple[str, str, bytes], ...]:
    """读取并严格验证 v2.8 稳定资源。"""
    if not path.is_file():
        raise FileNotFoundError(f"缺少 v2.8 基线：{path}")
    digest = _sha256(path)
    if digest != STABLE_BASELINE_SHA256:
        raise ValueError(
            f"v2.8 基线 SHA-256 不匹配：预期 {STABLE_BASELINE_SHA256}，实际 {digest}"
        )
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


def _patch_glide_paac(
    vanilla: bytes,
    expected_size: int,
    expected_selector_count: int,
) -> bytes:
    """把一份飞行专用 PAAC 的全部 PHW 标签改为私有飞行标签。"""
    if len(vanilla) != expected_size:
        raise ValueError(
            f"飞行 PAAC 大小不匹配：预期 {expected_size}，实际 {len(vanilla)}"
        )
    if PHW_SELECTOR_BYTES == GLIDE_SELECTOR_BYTES:
        raise ValueError("私有飞行标签与原生 phw 标签哈希碰撞")
    if vanilla.count(PHW_SELECTOR_BYTES) != expected_selector_count:
        raise ValueError("飞行 PAAC 的 phw 标签数量变化，必须重新审计")
    if vanilla.count(GLIDE_SELECTOR_BYTES):
        raise ValueError("飞行 PAAC 已包含私有标签，拒绝重复修改")

    patched = vanilla.replace(PHW_SELECTOR_BYTES, GLIDE_SELECTOR_BYTES)
    if patched.count(PHW_SELECTOR_BYTES) or patched.count(GLIDE_SELECTOR_BYTES) != expected_selector_count:
        raise ValueError("飞行 PAAC 私有标签替换数量异常")
    expected_changed = sum(
        old != new for old, new in zip(PHW_SELECTOR_BYTES, GLIDE_SELECTOR_BYTES)
    ) * expected_selector_count
    actual_changed = sum(old != new for old, new in zip(vanilla, patched))
    if actual_changed != expected_changed:
        raise ValueError(
            f"飞行 PAAC 差异字节数异常：预期 {expected_changed}，实际 {actual_changed}"
        )
    return patched


def _legacy_patch_document() -> dict[str, object]:
    """只把四条玩家记录的 lookup_84 写为私有飞行标签。"""
    return {
        "author": "Khione, Slinky, CDMM",
        "description": "Selects PHW nodes only inside the two dedicated glide PAAC files.",
        "name": MOD_NAME,
        "patches": [
            {
                "changes": [
                    {
                        "label": f"{character}.lookup_84 -> {GLIDE_SELECTOR_NAME}",
                        "offset": offset,
                        "original": "00000000",
                        "patched": GLIDE_SELECTOR_BYTES.hex().upper(),
                    }
                    for offset, character in CHARACTER_LOOKUP84_OFFSETS
                ],
                "game_file": "gamedata/characterinfo.pabgb",
            }
        ],
        "version": MOD_VERSION,
    }


def build_mod(game_dir: Path, baseline_path: Path, output_path: Path) -> Path:
    """生成 v2.8 加选择性女性飞行节点的单变量包。"""
    baseline = _load_baseline(baseline_path)
    combined = list(baseline)
    for target, expected_size, expected_count in GLIDE_PAAC_SPECS:
        entry = _find_entry(game_dir, GLIDE_PAAC_PAMT_DIR, target)
        vanilla, _ = extract_plaintext(entry)
        combined.append(
            (
                target,
                GLIDE_PAAC_PAMT_DIR,
                _patch_glide_paac(vanilla, expected_size, expected_count),
            )
        )

    identities = [(pamt_dir, target) for target, pamt_dir, _content in combined]
    if len(identities) != BASELINE_FILE_COUNT + len(GLIDE_PAAC_SPECS):
        raise ValueError("选择性女性飞行资源数量异常")
    if len(identities) != len(set(identities)):
        raise ValueError("选择性女性飞行目标与基线冲突")

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
            {"path": "patches/legacy.json", "type": CDMOD_LEGACY_JSON_COMPONENT_TYPE},
            {
                "file_count": len(replacements),
                "path": "files/replacements.json",
                "type": CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE,
            },
        ],
        "dependencies": [],
        "description": (
            "Preserves the byte-verified v2.8 stable baseline. Gives Kliff a private "
            "selector and retags PHW candidates only in dedicated lower/upper glide PAAC."
        ),
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": "cdmm.new-female-animations-for-kliff.stable.selective-female-glide",
        "name": MOD_NAME,
        "source": {
            "baseline_package": STABLE_BASELINE_NAME,
            "baseline_sha256": STABLE_BASELINE_SHA256,
            "glide_paac_count": len(GLIDE_PAAC_SPECS),
            "glide_selector_hash": f"0x{GLIDE_SELECTOR_HASH:08X}",
            "glide_selector_name": GLIDE_SELECTOR_NAME,
            "phw_selector_count": sum(spec[2] for spec in GLIDE_PAAC_SPECS),
        },
        "version": MOD_VERSION,
    }
    documents["patches/legacy.json"] = _legacy_patch_document()
    documents["files/replacements.json"] = {"schema": 1, "files": replacements}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_cdmod_zip(output_path, documents)

    package = load_cdmod_package(output_path)
    files = [file for patch in package.file_patches for file in patch.files]
    parsed = {(file.pamt_dir, file.target): file.content for file in files}
    if len(files) != len(combined) or parsed != expected:
        raise ValueError("生成后的选择性女性飞行载荷与预期不一致")
    if len(package.legacy_json_patches) != 1 or package.resource_patches or package.standalone_archives:
        raise ValueError("生成后的选择性女性飞行组件结构异常")
    return output_path


def main() -> int:
    """生成项目 build 目录下的选择性女性飞行测试包。"""
    project_dir = Path(__file__).resolve().parents[1]
    game_dir = Path(r"G:\SteamLibrary\steamapps\common\Crimson Desert")
    baseline_path = (
        project_dir
        / "docs"
        / "克里夫女性动画研究归档"
        / "测试版本"
        / STABLE_BASELINE_NAME
    )
    output_path = (
        project_dir
        / "build"
        / "new-female-animations-for-kliff"
        / "stable-selective-female-glide-test.cdmod"
    )
    result = build_mod(game_dir, baseline_path, output_path)
    print(f"已生成：{result}")
    print(f"私有飞行标签：{GLIDE_SELECTOR_NAME}=0x{GLIDE_SELECTOR_HASH:08X}")
    print(f"资源总数：{BASELINE_FILE_COUNT + len(GLIDE_PAAC_SPECS)}")
    print(f"PHW 飞行选择标签：{sum(spec[2] for spec in GLIDE_PAAC_SPECS)}")
    print(f"SHA-256：{_sha256(result)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
