"""生成“v2.3 稳定资源 + 原生 PHW 持续 walk 节点克隆”测试包。

v1.7 已实机证明第四步会进入 Basic_Lower 的持续 walk 槽。进一步解析原版 PAAC
尾部资源表与固定节点记录后，男性持续 walk 节点引用 motionblending 索引 13，原生
PHW 持续 walk 节点引用索引 14；两个节点槽都为 325 字节。本工具完整继承 v2.3，
只把原生 PHW walk 节点记录克隆到男性 walk 目标槽，不修改 CharacterInfo、transition、
其他节点或战斗资源。
"""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path

from cdmm.services.cdmod_converter import (
    CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE,
    CDMOD_FORMAT_NAME,
    CDMOD_FORMAT_VERSION,
    _write_cdmod_zip,
)
from cdmm.services.cdmod_package import load_cdmod_package
from cdmm.services.json_loader import extract_plaintext
from cdmm.tools.build_female_locomotion_male_combat_graph_mod import _find_entry
from cdmm.tools.build_female_outside_combat_selective_nodes_mod import (
    EXPECTED_NODE_COUNT_BYTES,
    EXPECTED_PAAC_SIZE,
    EXPECTED_V23_FILE_COUNT,
    TARGET_PAAC,
    TARGET_PAAC_PAMT_DIR,
    V23_PACKAGE_NAME,
    V23_PACKAGE_SHA256,
    _load_v23_files,
)
from cdmm.tools.build_female_outside_combat_smooth_walk_mod import _file_document

# 原版 Basic_Lower 中两个已按 325 字节固定步长交叉验证的持续 walk 节点槽。
NODE_RECORD_SIZE = 325
MALE_WALK_NODE_START = 4_420
PHW_WALK_NODE_START = 5_070
NODE_SECOND_MARKER_OFFSET = 117
NODE_RESOURCE_INDEX_OFFSET = 171
MALE_WALK_RESOURCE_INDEX = 13
PHW_WALK_RESOURCE_INDEX = 14
NODE_SENTINEL = 0xFFFF

# 当前原版两个节点记录的身份哈希，防止游戏更新后继续套用旧 offset。
EXPECTED_MALE_WALK_NODE_SHA256 = (
    "2741ec1437b1cc706e13b7496e76eb57f7f1c47be951afa488da545ab2a35754"
)
EXPECTED_PHW_WALK_NODE_SHA256 = (
    "5836c87067320899e2763ceb1cdcd2ac05e76a68d49cf911795266f04c940607"
)

MOD_NAME = "Female Outside Combat Native PHW Walk Node - Male Combat"
MOD_VERSION = "2.7-test"
OUTPUT_FILE_NAME = f"{MOD_NAME} v{MOD_VERSION}.cdmod"


def _validate_node_record(record: bytes, expected_index: int, expected_sha256: str) -> None:
    """验证固定节点的长度、标记、资源索引和原版身份。"""
    if len(record) != NODE_RECORD_SIZE:
        raise ValueError("Basic_Lower walk 节点记录长度异常")
    first_marker = struct.unpack_from("<HH", record, 0)
    second_marker = struct.unpack_from("<HH", record, NODE_SECOND_MARKER_OFFSET)
    resource_fields = struct.unpack_from("<HHH", record, NODE_RESOURCE_INDEX_OFFSET)
    if first_marker != (0, NODE_SENTINEL) or second_marker != (0, NODE_SENTINEL):
        raise ValueError("Basic_Lower walk 节点固定标记不匹配")
    if resource_fields != (expected_index, expected_index, NODE_SENTINEL):
        raise ValueError(
            f"Basic_Lower walk 节点资源索引异常：预期 {expected_index}，实际 {resource_fields}"
        )
    digest = hashlib.sha256(record).hexdigest()
    if digest != expected_sha256:
        raise ValueError(
            f"Basic_Lower walk 节点 SHA-256 不匹配：预期 {expected_sha256}，实际 {digest}"
        )


def _clone_native_phw_walk_node(vanilla: bytes) -> bytes:
    """把原生 PHW 持续 walk 节点完整克隆到男性 walk 目标槽。"""
    if len(vanilla) != EXPECTED_PAAC_SIZE or vanilla[:4] != EXPECTED_NODE_COUNT_BYTES:
        raise ValueError("Basic_Lower 版本身份不匹配，必须重新审计节点记录")
    male_end = MALE_WALK_NODE_START + NODE_RECORD_SIZE
    phw_end = PHW_WALK_NODE_START + NODE_RECORD_SIZE
    male_record = vanilla[MALE_WALK_NODE_START:male_end]
    phw_record = vanilla[PHW_WALK_NODE_START:phw_end]
    _validate_node_record(
        male_record,
        MALE_WALK_RESOURCE_INDEX,
        EXPECTED_MALE_WALK_NODE_SHA256,
    )
    _validate_node_record(
        phw_record,
        PHW_WALK_RESOURCE_INDEX,
        EXPECTED_PHW_WALK_NODE_SHA256,
    )

    patched = bytearray(vanilla)
    patched[MALE_WALK_NODE_START:male_end] = phw_record
    result = bytes(patched)
    if len(result) != len(vanilla):
        raise ValueError("Basic_Lower walk 节点克隆后长度发生变化")
    if result[MALE_WALK_NODE_START:male_end] != vanilla[PHW_WALK_NODE_START:phw_end]:
        raise ValueError("Basic_Lower PHW walk 节点克隆结果不一致")
    if result[:MALE_WALK_NODE_START] != vanilla[:MALE_WALK_NODE_START]:
        raise ValueError("Basic_Lower walk 节点前方字节意外变化")
    if result[male_end:] != vanilla[male_end:]:
        raise ValueError("Basic_Lower walk 节点后方字节意外变化")
    return result


def build_mod(game_dir: Path, baseline_path: Path, output_path: Path) -> Path:
    """完整继承 v2.3，只新增单个 PHW walk 节点克隆后的 Basic_Lower。"""
    baseline = _load_v23_files(baseline_path)
    target_entry = _find_entry(game_dir, TARGET_PAAC_PAMT_DIR, TARGET_PAAC)
    vanilla_paac, _ = extract_plaintext(target_entry)
    patched_paac = _clone_native_phw_walk_node(vanilla_paac)

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
            {
                "file_count": len(replacements),
                "path": "files/replacements.json",
                "type": CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE,
            }
        ],
        "dependencies": [],
        "description": (
            "Preserves every verified v2.3 resource byte and clones only the native "
            "PHW continuous-walk node record over the male continuous-walk target slot."
        ),
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": "cdmm.female-outside-combat-native-phw-walk-node-male-combat",
        "name": MOD_NAME,
        "source": {
            "baseline_package": V23_PACKAGE_NAME,
            "baseline_sha256": V23_PACKAGE_SHA256,
            "format": "v2.3-plus-one-native-phw-walk-node-clone",
            "node_record_size": NODE_RECORD_SIZE,
            "source_node_start": PHW_WALK_NODE_START,
            "target_node_start": MALE_WALK_NODE_START,
        },
        "version": MOD_VERSION,
    }
    documents["manifest.json"] = manifest
    documents["files/replacements.json"] = {"schema": 1, "files": replacements}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_cdmod_zip(output_path, documents)

    package = load_cdmod_package(output_path)
    files = [file for patch in package.file_patches for file in patch.files]
    parsed_content = {file.target: file.content for file in files}
    if package.legacy_json_patches or len(files) != EXPECTED_V23_FILE_COUNT + 1:
        raise ValueError("生成后的 v2.7 cdmod 组件数量异常")
    if parsed_content != expected_content:
        raise ValueError("生成后的 v2.7 资源载荷与预期不一致")
    return output_path


def main() -> int:
    """生成游戏 mods 目录下的新测试包。"""
    game_dir = Path(r"G:\SteamLibrary\steamapps\common\Crimson Desert")
    baseline_path = game_dir / "mods" / V23_PACKAGE_NAME
    output_path = game_dir / "mods" / OUTPUT_FILE_NAME
    result = build_mod(game_dir, baseline_path, output_path)
    print(f"已生成：{result}")
    print(f"SHA-256：{hashlib.sha256(result.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
