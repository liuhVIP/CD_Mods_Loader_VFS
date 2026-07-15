"""保留已证伪的 v1.8 transition sequence 危险实验生成器。

后续结构分析已确认：重复的 7..13 不是 motionblending lane，而是相邻 16 字节
inline transition 的 sequence 字段。本工具把 36 条 transition 的 sequence 13 改成 14，
实机会在 LOGO 阶段触发 AppHangB1。文件只用于复盘错误，不得再次生成或加载。
"""

from __future__ import annotations

from pathlib import Path

from cdmm.services.cdmod_converter import (
    CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE,
    CDMOD_FORMAT_NAME,
    CDMOD_FORMAT_VERSION,
    _write_cdmod_zip,
)
from cdmm.services.cdmod_package import load_cdmod_package
from cdmm.services.json_loader import extract_plaintext
from cdmm.tools.build_female_locomotion_male_combat_graph_mod import (
    TARGET_PAAC,
    _find_entry,
    _patch_basic_lower,
)
from cdmm.tools.build_female_walk_single_branch_mod import (
    PAAC_PAMT_DIR,
    _file_document,
)

# 连续 16 字节 inline transition 的 sequence 序列。
WALK_DESCRIPTOR_SEQUENCE = tuple(range(7, 14))

# 已证伪实验修改的 transition sequence 原值与错误目标值。
MALE_WALK_LANE_INDEX = 13
FEMALE_WALK_LANE_INDEX = 14

# 当前 1.13.01 原版 Basic_Lower 中已验证的完整序列数量。
EXPECTED_WALK_DESCRIPTOR_COUNT = 36
DESCRIPTOR_SIZE = 16

MOD_NAME = "Female Walk PHW Lane - Native Male Combat"
MOD_VERSION = "1.8-test"
OUTPUT_FILE_NAME = f"{MOD_NAME} v{MOD_VERSION}.cdmod"


def _find_walk_descriptor_offsets(data: bytes) -> tuple[int, ...]:
    """定位所有以 7..13 连续排列的 inline transition sequence 末项。"""
    sequence_size = len(WALK_DESCRIPTOR_SEQUENCE) * DESCRIPTOR_SIZE
    offsets: list[int] = []
    for start in range(0, len(data) - sequence_size + 1):
        if not all(
            data[start + index * DESCRIPTOR_SIZE:start + index * DESCRIPTOR_SIZE + 4]
            == value.to_bytes(4, "little")
            for index, value in enumerate(WALK_DESCRIPTOR_SEQUENCE)
        ):
            continue
        offsets.append(start + (len(WALK_DESCRIPTOR_SEQUENCE) - 1) * DESCRIPTOR_SIZE)
    if len(offsets) != EXPECTED_WALK_DESCRIPTOR_COUNT:
        raise ValueError(
            "持续 walk 描述符数量变化："
            f"预期 {EXPECTED_WALK_DESCRIPTOR_COUNT}，实际 {len(offsets)}"
        )
    if len(offsets) != len(set(offsets)):
        raise ValueError("持续 walk 描述符出现重复 offset")
    return tuple(offsets)


def _retarget_walk_lane(data: bytes) -> tuple[bytes, tuple[int, ...]]:
    """复现已证伪的 transition sequence 13 -> 14 危险修改。"""
    offsets = _find_walk_descriptor_offsets(data)
    patched = bytearray(data)
    original = MALE_WALK_LANE_INDEX.to_bytes(4, "little")
    replacement = FEMALE_WALK_LANE_INDEX.to_bytes(4, "little")
    for offset in offsets:
        if patched[offset:offset + 4] != original:
            raise ValueError(f"transition sequence 原值异常：0x{offset:X}")
        patched[offset:offset + 4] = replacement
    if len(patched) != len(data):
        raise ValueError("transition sequence 修改后 PAAC 长度发生变化")
    changed_offsets = [
        index
        for index, (old_byte, new_byte) in enumerate(zip(data, patched))
        if old_byte != new_byte
    ]
    if changed_offsets != list(offsets):
        raise ValueError("transition sequence 差异不再是单字节 13 -> 14")
    return bytes(patched), offsets


def build_mod(game_dir: Path, output_path: Path) -> Path:
    """仅为回归复盘构建已证伪的 v1.8 transition sequence 包。"""
    basic_lower_entry = _find_entry(game_dir, PAAC_PAMT_DIR, TARGET_PAAC)
    vanilla_paac, _ = extract_plaintext(basic_lower_entry)
    female_paths_paac = _patch_basic_lower(vanilla_paac)
    patched_paac, descriptor_offsets = _retarget_walk_lane(female_paths_paac)

    payload_path = "assets/000/basic_lower.paac"
    replacements = [
        _file_document(
            target=TARGET_PAAC,
            pamt_dir=PAAC_PAMT_DIR,
            payload_path=payload_path,
            content=patched_paac,
        )
    ]
    manifest = {
        "author": "Khione, Slinky, CDMM",
        "components": [
            {
                "file_count": 1,
                "path": "files/replacements.json",
                "type": CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE,
            }
        ],
        "dependencies": [],
        "description": (
            "Retired unsafe diagnostic that changed 36 inline-transition sequence "
            "values from 13 to 14. It caused a reproducible logo-stage AppHangB1 "
            "and must never be enabled."
        ),
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": "cdmm.female-walk-phw-lane-native-male-combat",
        "name": MOD_NAME,
        "source": {
            "descriptor_count": len(descriptor_offsets),
            "format": "retired-unsafe-inline-transition-sequence-retarget",
            "from_sequence": MALE_WALK_LANE_INDEX,
            "retired_unsafe": True,
            "target": TARGET_PAAC,
            "to_sequence": FEMALE_WALK_LANE_INDEX,
        },
        "version": MOD_VERSION,
    }
    documents: dict[str, dict[str, object] | bytes] = {
        "manifest.json": manifest,
        "files/replacements.json": {"schema": 1, "files": replacements},
        payload_path: patched_paac,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_cdmod_zip(output_path, documents)

    package = load_cdmod_package(output_path)
    files = [file for patch in package.file_patches for file in patch.files]
    if package.legacy_json_patches or len(files) != 1:
        raise ValueError("生成后的 v1.8 cdmod 组件数量异常")
    if files[0].target != TARGET_PAAC or files[0].content != patched_paac:
        raise ValueError("生成后的 v1.8 PAAC 载荷与预期不一致")
    return output_path


def main() -> int:
    """阻止命令行再次生成已确认会卡 LOGO 的危险包。"""
    raise RuntimeError("v1.8 已证实会破坏 transition sequence 并导致 LOGO AppHangB1")


if __name__ == "__main__":
    raise SystemExit(main())
