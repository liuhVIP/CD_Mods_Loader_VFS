"""生成“v2.3 稳定资源 + 选择性 PHW 普通移动节点”测试包。

原版 Damian 与 Kliff 共用同一份 Basic_Lower.paac，区别是 CharacterInfo.lookup_84
分别为 phw 与 0。v2.3 只替换资源载荷，第四步仍会沿默认男性节点链进入持续 walk
状态并重置 phase/root-motion。本工具给 Kliff 写入独立 CDMM locomotion 标签，只把
Basic_Lower 中两组已审计的普通移动四方向 PHW 选择条件改为该标签。其余 PHW 条件，
尤其战斗区域条件，保持原版不变。
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
from cdmm.tools.build_female_outside_combat_smooth_walk_mod import _file_document

# 已实机确认除第四步抖动外全部正常的稳定基础包。
V23_PACKAGE_NAME = "Female Outside Combat Smooth Walk - Male Combat v2.3-test.cdmod"
V23_PACKAGE_SHA256 = "f8f40df58068ea9a5a643193f2282299c75a14d9e02508a76f25a0f556478a61"
EXPECTED_V23_FILE_COUNT = 57

# 当前游戏原版 Basic_Lower 的固定身份；游戏更新后必须重新审计，禁止盲用旧 offset。
TARGET_PAAC = "actionchart/bin__/loweraction/1_pc/1_phm/basic_lower.paac"
TARGET_PAAC_PAMT_DIR = "0010"
EXPECTED_PAAC_SIZE = 483_276
EXPECTED_NODE_COUNT_BYTES = (624).to_bytes(4, "little")

# 游戏原生 PHW 标签与本实验独立标签。独立标签只会命中本工具明确修改的节点条件。
PHW_SELECTOR_HASH = hashlittle(b"phw", HASH_SEED)
SELECTIVE_SELECTOR_NAME = "cdmm_phw_locomotion"
SELECTIVE_SELECTOR_HASH = hashlittle(SELECTIVE_SELECTOR_NAME.encode("ascii"), HASH_SEED)
PHW_SELECTOR_BYTES = PHW_SELECTOR_HASH.to_bytes(4, "little")
SELECTIVE_SELECTOR_BYTES = SELECTIVE_SELECTOR_HASH.to_bytes(4, "little")

# 两组已从原版 transition 结构交叉核验的普通移动 PHW 四方向选择条件：
# 第一组进入基础移动入口，第二、三组覆盖持续移动方向表。这里只改条件标签，
# 不改 transition target、sequence、threshold、资源字符串或任何战斗节点。
LOCOMOTION_PHW_SELECTOR_OFFSETS = (
    1_859,
    2_509,
    3_484,
    4_459,
    19_436,
    21_218,
    24_820,
    26_698,
    28_576,
    30_454,
    32_332,
    34_210,
)

# 当前 1.13.01 CharacterInfo 中四条玩家记录的 lookup_84 绝对偏移。
CHARACTER_LOOKUP84_OFFSETS = (
    (464, "Kliff"),
    (4_503, "Kliff_Clone"),
    (8_414, "Kliff_AI"),
    (43_321, "PlayerAll"),
)

MOD_NAME = "Female Outside Combat Selective Nodes - Male Combat"
MOD_VERSION = "2.6-test"
OUTPUT_FILE_NAME = f"{MOD_NAME} v{MOD_VERSION}.cdmod"


def _load_v23_files(package_path: Path) -> tuple[tuple[str, str, bytes], ...]:
    """读取并严格验证 v2.3 的有序稳定资源集合。"""
    digest = hashlib.sha256(package_path.read_bytes()).hexdigest()
    if digest != V23_PACKAGE_SHA256:
        raise ValueError(f"v2.3 基线 SHA-256 不匹配：预期 {V23_PACKAGE_SHA256}，实际 {digest}")
    package = load_cdmod_package(package_path)
    files = [file for patch in package.file_patches for file in patch.files]
    if package.legacy_json_patches or len(files) != EXPECTED_V23_FILE_COUNT:
        raise ValueError("v2.3 基线组件数量或类型异常")
    if any(file.allow_new or file.allow_table_replace for file in files):
        raise ValueError("v2.3 基线意外包含新增资源或表覆盖")
    targets = [file.target for file in files]
    if len(targets) != len(set(targets)):
        raise ValueError("v2.3 基线包含重复目标")
    return tuple((file.target, file.pamt_dir, file.content) for file in files)


def _patch_selective_locomotion_nodes(vanilla: bytes) -> bytes:
    """只把十二处普通移动 PHW 条件改为独立 CDMM 标签。"""
    if len(vanilla) != EXPECTED_PAAC_SIZE or vanilla[:4] != EXPECTED_NODE_COUNT_BYTES:
        raise ValueError("Basic_Lower 版本身份不匹配，必须重新审计节点 offset")
    if PHW_SELECTOR_BYTES == SELECTIVE_SELECTOR_BYTES:
        raise ValueError("独立 locomotion 标签与原生 phw 标签发生哈希碰撞")
    if vanilla.count(SELECTIVE_SELECTOR_BYTES):
        raise ValueError("Basic_Lower 已包含独立 locomotion 标签，拒绝重复修改")

    patched = bytearray(vanilla)
    for offset in LOCOMOTION_PHW_SELECTOR_OFFSETS:
        original = bytes(patched[offset:offset + 4])
        if original != PHW_SELECTOR_BYTES:
            raise ValueError(
                f"Basic_Lower 节点标签 offset={offset} 不再是 phw：{original.hex()}"
            )
        patched[offset:offset + 4] = SELECTIVE_SELECTOR_BYTES

    result = bytes(patched)
    changed = [index for index, (old, new) in enumerate(zip(vanilla, result)) if old != new]
    expected_changed = sum(
        1
        for old, new in zip(PHW_SELECTOR_BYTES, SELECTIVE_SELECTOR_BYTES)
        if old != new
    ) * len(LOCOMOTION_PHW_SELECTOR_OFFSETS)
    if len(changed) != expected_changed:
        raise ValueError(
            f"Basic_Lower 标签差异字节数异常：预期 {expected_changed}，实际 {len(changed)}"
        )
    if result.count(SELECTIVE_SELECTOR_BYTES) != len(LOCOMOTION_PHW_SELECTOR_OFFSETS):
        raise ValueError("Basic_Lower 独立 locomotion 标签数量异常")
    return result


def _legacy_patch_document() -> dict[str, object]:
    """给四条玩家 CharacterInfo 记录写入独立 locomotion 标签。"""
    return {
        "author": "Khione, Slinky, CDMM",
        "description": "Selects only the audited CDMM PHW locomotion nodes.",
        "name": MOD_NAME,
        "patches": [
            {
                "changes": [
                    {
                        "label": f"{character}.lookup_84 -> {SELECTIVE_SELECTOR_NAME}",
                        "offset": offset,
                        "original": "00000000",
                        "patched": SELECTIVE_SELECTOR_BYTES.hex().upper(),
                    }
                    for offset, character in CHARACTER_LOOKUP84_OFFSETS
                ],
                "game_file": "gamedata/characterinfo.pabgb",
            }
        ],
        "version": MOD_VERSION,
    }


def build_mod(game_dir: Path, baseline_path: Path, output_path: Path) -> Path:
    """完整继承 v2.3，只新增选择性 locomotion 节点与 CharacterInfo 标签。"""
    baseline = _load_v23_files(baseline_path)
    target_entry = _find_entry(game_dir, TARGET_PAAC_PAMT_DIR, TARGET_PAAC)
    vanilla_paac, _ = extract_plaintext(target_entry)
    patched_paac = _patch_selective_locomotion_nodes(vanilla_paac)

    replacements: list[dict[str, object]] = []
    documents: dict[str, dict[str, object] | bytes] = {}
    expected_content: dict[str, bytes] = {}
    for index, (target, pamt_dir, content) in enumerate(baseline):
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
        expected_content[target] = content

    paac_payload_path = f"assets/{len(replacements):03d}/basic_lower.paac"
    replacements.append(
        _file_document(
            target=TARGET_PAAC,
            pamt_dir=TARGET_PAAC_PAMT_DIR,
            payload_path=paac_payload_path,
            content=patched_paac,
        )
    )
    documents[paac_payload_path] = patched_paac
    expected_content[TARGET_PAAC] = patched_paac

    manifest = {
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
            "Preserves every verified v2.3 resource byte. Gives Kliff a private locomotion "
            "selector and retags only twelve audited PHW start/walk direction nodes, while "
            "all remaining PHW combat selectors stay native and unmatched."
        ),
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": "cdmm.female-outside-combat-selective-nodes-male-combat",
        "name": MOD_NAME,
        "source": {
            "baseline_package": V23_PACKAGE_NAME,
            "baseline_sha256": V23_PACKAGE_SHA256,
            "format": "v2.3-plus-private-locomotion-selector",
            "selector_hash": f"0x{SELECTIVE_SELECTOR_HASH:08X}",
            "selector_name": SELECTIVE_SELECTOR_NAME,
            "selector_offset_count": len(LOCOMOTION_PHW_SELECTOR_OFFSETS),
        },
        "version": MOD_VERSION,
    }
    documents["manifest.json"] = manifest
    documents["patches/legacy.json"] = _legacy_patch_document()
    documents["files/replacements.json"] = {"schema": 1, "files": replacements}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_cdmod_zip(output_path, documents)

    package = load_cdmod_package(output_path)
    files = [file for patch in package.file_patches for file in patch.files]
    parsed_content = {file.target: file.content for file in files}
    if len(package.legacy_json_patches) != 1 or len(files) != EXPECTED_V23_FILE_COUNT + 1:
        raise ValueError("生成后的 v2.6 cdmod 组件数量异常")
    if parsed_content != expected_content:
        raise ValueError("生成后的 v2.6 资源载荷与预期不一致")
    return output_path


def main() -> int:
    """生成游戏 mods 目录下的新测试包。"""
    game_dir = Path(r"G:\SteamLibrary\steamapps\common\Crimson Desert")
    baseline_path = game_dir / "mods" / V23_PACKAGE_NAME
    output_path = game_dir / "mods" / OUTPUT_FILE_NAME
    result = build_mod(game_dir, baseline_path, output_path)
    print(f"已生成：{result}")
    print(f"选择性标签：{SELECTIVE_SELECTOR_NAME}=0x{SELECTIVE_SELECTOR_HASH:08X}")
    print(f"SHA-256：{hashlib.sha256(result.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
