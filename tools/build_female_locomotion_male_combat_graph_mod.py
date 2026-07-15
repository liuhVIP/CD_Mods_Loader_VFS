"""生成“男性状态机、女性非战斗移动混合树”单变量测试包。

本工具不修改 characterinfo，也不替换 PAA/PAA_metabin。它只在当前原版
Basic_Lower.paac 中把五个普通移动 motionblending 引用由 PHM 改为 PHW，
同时保留 alert 战斗移动引用为 PHM。所有替换保持文件长度不变。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from cdmm.archive.pamt import parse_pamt
from cdmm.common.models import PazEntry
from cdmm.services.cdmod_converter import (
    CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE,
    CDMOD_FORMAT_NAME,
    CDMOD_FORMAT_VERSION,
    _write_cdmod_zip,
)
from cdmm.services.cdmod_package import load_cdmod_package
from cdmm.services.json_loader import extract_plaintext

# 男性默认图中的普通移动混合树。只改性别目录名，路径长度完全一致。
LOCOMOTION_REFERENCE_REPLACEMENTS = (
    (
        b"character/binary/motionblending/phm_locomotion/basic_move_walk.motionblending",
        b"character/binary/motionblending/phw_locomotion/basic_move_walk.motionblending",
    ),
    (
        b"character/binary/motionblending/phm_locomotion/basic_move_lv2.motionblending",
        b"character/binary/motionblending/phw_locomotion/basic_move_lv2.motionblending",
    ),
    (
        b"character/binary/motionblending/phm_locomotion/basic_move_lv3.motionblending",
        b"character/binary/motionblending/phw_locomotion/basic_move_lv3.motionblending",
    ),
    (
        b"character/binary/motionblending/phm_locomotion/basic_move_lv4.motionblending",
        b"character/binary/motionblending/phw_locomotion/basic_move_lv4.motionblending",
    ),
    (
        b"character/binary/motionblending/phm_locomotion/basic_move_lv4_2.motionblending",
        b"character/binary/motionblending/phw_locomotion/basic_move_lv4_2.motionblending",
    ),
)

# alert_move_lv2/lv3 必须继续指向 PHM，作为战斗状态隔离断言。
MALE_COMBAT_REFERENCES = (
    b"character/binary/motionblending/phm_locomotion/alert_move_lv2.motionblending",
    b"character/binary/motionblending/phm_locomotion/alert_move_lv3.motionblending",
)

TARGET_PAAC = "actionchart/bin__/loweraction/1_pc/1_phm/basic_lower.paac"
MOD_NAME = "Female Locomotion Graph - Native Male Combat"
MOD_VERSION = "1.4-test"
OUTPUT_FILE_NAME = f"{MOD_NAME} v{MOD_VERSION}.cdmod"


def _full_virtual_path(entry: PazEntry) -> str:
    """还原 PAMT folder record 与扁平 basename 组成的完整路径。"""
    basename = Path(entry.path.replace("\\", "/")).name
    resolved_dir = entry.resolved_dir_path.replace("\\", "/").strip("/")
    return f"{resolved_dir}/{basename}".strip("/") if resolved_dir else entry.path


def _find_entry(game_dir: Path, pamt_dir: str, target: str) -> PazEntry:
    """在指定原版 PAMT 中按完整路径查找唯一资源。"""
    matches = [
        entry
        for entry in parse_pamt(game_dir / pamt_dir / "0.pamt")
        if _full_virtual_path(entry).casefold() == target.casefold()
    ]
    if len(matches) != 1:
        raise ValueError(f"{pamt_dir}/{target} 预期唯一命中，实际 {len(matches)}")
    return matches[0]


def _patch_basic_lower(data: bytes) -> bytes:
    """执行五处同长度引用修改，并验证战斗引用保持男性。"""
    patched = data
    for old, new in LOCOMOTION_REFERENCE_REPLACEMENTS:
        if len(old) != len(new):
            raise ValueError("普通移动引用替换长度不一致")
        old_count = patched.count(old)
        new_count = patched.count(new)
        if old_count != 1 or new_count != 1:
            raise ValueError(
                f"动作图引用数量异常：{old!r} old={old_count} new={new_count}"
            )
        patched = patched.replace(old, new, 1)

    if len(patched) != len(data):
        raise ValueError("Basic_Lower 修改后长度发生变化")
    changed_offsets = [index for index, pair in enumerate(zip(data, patched)) if pair[0] != pair[1]]
    if len(changed_offsets) != len(LOCOMOTION_REFERENCE_REPLACEMENTS):
        raise ValueError(
            "Basic_Lower 差异字节数异常："
            f"预期 {len(LOCOMOTION_REFERENCE_REPLACEMENTS)}，实际 {len(changed_offsets)}"
        )
    for reference in MALE_COMBAT_REFERENCES:
        if patched.count(reference) != 1:
            raise ValueError(f"男性战斗引用丢失：{reference!r}")
    return patched


def build_mod(game_dir: Path, output_path: Path) -> Path:
    """从当前原版 Basic_Lower 构建 v1.4 测试包。"""
    target_entry = _find_entry(game_dir, "0010", TARGET_PAAC)
    vanilla, _ = extract_plaintext(target_entry)
    patched = _patch_basic_lower(vanilla)
    payload_path = "assets/000/basic_lower.paac"
    replacement_document = {
        "schema": 1,
        "files": [
            {
                "allow_new": False,
                "allow_table_replace": False,
                "pamt_dir": "0010",
                "payload": payload_path,
                "sha256": hashlib.sha256(patched).hexdigest(),
                "size": len(patched),
                "target": TARGET_PAAC,
            }
        ],
    }
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
            "Keeps the native male default action and combat graph. Only five ordinary "
            "Basic_Lower motionblending references are retargeted from PHM to PHW."
        ),
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": "cdmm.female-locomotion-graph-native-male-combat",
        "name": MOD_NAME,
        "source": {
            "changed_byte_count": len(LOCOMOTION_REFERENCE_REPLACEMENTS),
            "format": "same-length-paac-reference-patch",
            "target": TARGET_PAAC,
        },
        "version": MOD_VERSION,
    }
    documents: dict[str, dict[str, object] | bytes] = {
        "manifest.json": manifest,
        "files/replacements.json": replacement_document,
        payload_path: patched,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_cdmod_zip(output_path, documents)

    package = load_cdmod_package(output_path)
    files = [file for patch in package.file_patches for file in patch.files]
    if package.legacy_json_patches or len(files) != 1 or files[0].content != patched:
        raise ValueError("生成后的 v1.4 cdmod 严格解析结果与预期不一致")
    return output_path


def main() -> int:
    """生成游戏 mods 目录下的新测试包。"""
    game_dir = Path(r"G:\SteamLibrary\steamapps\common\Crimson Desert")
    output_path = game_dir / "mods" / OUTPUT_FILE_NAME
    result = build_mod(game_dir, output_path)
    print(f"已生成：{result}")
    print(f"SHA-256：{hashlib.sha256(result.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
