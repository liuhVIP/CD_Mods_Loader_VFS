"""把全单手剑完整背挂链参数嵌入 Human Female standalone。

Human Female 的完整 standalone 会晚于普通 nppsa 加载，因此单独的 PHW loose
补丁会被覆盖。本工具只在原 standalone 的两个既有 entry 内执行窄范围修改，
并精确保持明文长度、压缩长度、PAMT、PAZ 总长度和其他 entry offset。
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.etree import ElementTree

import lz4.block

from cdmm.archive.pamt import parse_pamt
from cdmm.archive.paz_crypto import encrypt
from cdmm.services.cdmod_converter import _write_cdmod_zip
from cdmm.services.cdmod_package import load_cdmod_package
from cdmm.services.json_loader import extract_plaintext
from cdmm.tools.build_dual_onehand_swords_back_carry_mod import (
    BODY_SOCKET_REPLACEMENTS,
    PACKAGE_VERSION,
    PHW_DESCRIPTION_REPLACEMENTS,
)
from cdmm.tools.build_full_character_creator_witch_slots_repack import (
    MAX_SAFE_XML_FILL_SEEDS,
    _build_deterministic_safe_fill,
    _find_existing_comment_ascii_positions,
)

# Human Female cdmod 内完整 standalone 的固定组件路径。
MANIFEST_PATH = "manifest.json"
ARCHIVE_INDEX_PATH = "archives/000/archive.json"
ARCHIVE_PAMT_PATH = "archives/000/0.pamt"
ARCHIVE_PAZ_PATH = "archives/000/0.paz"

# 只允许修改这两个已确认 entry，禁止扩展到整张角色资源表。
DESCRIPTION_ENTRY_NAME = "phw_description_player_001.xml"
BODY_SOCKET_ENTRY_NAME = "phw_01.pab.sockets.xml"

# 只允许把最多 4 个既有行首 tab 等长改为空格；1.15.10 的身体基线参数需要
# 第 4 个安全缩进才能把 LZ4 精确恢复到原 standalone 槽位。
MAX_SOCKET_INDENT_CHANGES = 4


@dataclass(frozen=True)
class RepackedEntrySummary:
    """记录一个 exact-size entry 的重打包结果。"""

    entry_name: str
    plaintext_size: int
    compressed_size: int
    adjusted_safe_byte_count: int


@dataclass(frozen=True)
class HumanFemaleWitcherPlacementResult:
    """记录 Human Female 兼容包的输出与结构指纹。"""

    output_path: str
    package_sha256: str
    source_package_sha256: str
    archive_pamt_sha256: str
    archive_paz_sha256: str
    entries: tuple[RepackedEntrySummary, ...]


def repack_human_female_witcher_swords_placement(
    source_package_path: Path,
    output_path: Path,
) -> HumanFemaleWitcherPlacementResult:
    """复制完整 Human Female 包并精确嵌入 PHW 全单手剑背挂参数。"""
    source_package_path = source_package_path.resolve()
    output_path = output_path.resolve()
    if not source_package_path.is_file():
        raise FileNotFoundError(f"Human Female 包不存在：{source_package_path}")
    if output_path.suffix.casefold() != ".cdmod":
        raise ValueError("输出文件必须使用 .cdmod 后缀")

    source_bytes = source_package_path.read_bytes()
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    package = load_cdmod_package(source_package_path)
    if len(package.standalone_archives) != 1:
        raise ValueError(
            f"预期 Human Female 只有 1 个 standalone，实际 "
            f"{len(package.standalone_archives)} 个"
        )
    standalone = package.standalone_archives[0]

    summaries: list[RepackedEntrySummary] = []
    with TemporaryDirectory(prefix="human-female-witcher-placement-") as temp_dir:
        temp_root = Path(temp_dir)
        pamt_path = temp_root / "0.pamt"
        paz_path = temp_root / "0.paz"
        pamt_path.write_bytes(standalone.pamt_bytes)
        paz_path.write_bytes(standalone.paz_bytes)
        entries = parse_pamt(pamt_path, paz_dir=temp_root)
        entries_by_name = {
            Path(entry.path).name.casefold(): entry
            for entry in entries
            if Path(entry.path).name.casefold()
            in {DESCRIPTION_ENTRY_NAME, BODY_SOCKET_ENTRY_NAME}
        }
        missing = sorted(
            {DESCRIPTION_ENTRY_NAME, BODY_SOCKET_ENTRY_NAME} - entries_by_name.keys()
        )
        if missing:
            raise ValueError(f"Human Female standalone 缺少目标 entry：{missing}")

        patched_paz = bytearray(standalone.paz_bytes)
        for entry_name in (DESCRIPTION_ENTRY_NAME, BODY_SOCKET_ENTRY_NAME):
            entry = entries_by_name[entry_name]
            plaintext, _detected = extract_plaintext(entry)
            if entry_name == DESCRIPTION_ENTRY_NAME:
                patched_plaintext = _apply_text_replacements(
                    plaintext,
                    PHW_DESCRIPTION_REPLACEMENTS,
                    entry_name,
                )
                adjusted_plaintext, adjusted_count = (
                    _match_pseudo_xml_compressed_size(
                        patched_plaintext,
                        target_comp_size=entry.comp_size,
                    )
                )
            else:
                patched_plaintext = _apply_byte_replacements(
                    plaintext,
                    BODY_SOCKET_REPLACEMENTS,
                    entry_name,
                )
                adjusted_plaintext, adjusted_count = (
                    _match_socket_xml_compressed_size(
                        patched_plaintext,
                        target_comp_size=entry.comp_size,
                    )
                )

            payload = _build_exact_payload(
                adjusted_plaintext,
                entry_name=entry_name,
                compression_type=entry.compression_type,
                encrypted=entry.encrypted,
                target_comp_size=entry.comp_size,
                target_orig_size=entry.orig_size,
            )
            payload_end = entry.offset + entry.comp_size
            patched_paz[entry.offset:payload_end] = payload
            summaries.append(
                RepackedEntrySummary(
                    entry_name=entry_name,
                    plaintext_size=entry.orig_size,
                    compressed_size=entry.comp_size,
                    adjusted_safe_byte_count=adjusted_count,
                )
            )

        patched_paz_bytes = bytes(patched_paz)
        if len(patched_paz_bytes) != len(standalone.paz_bytes):
            raise ValueError("Human Female PAZ 总长度发生变化")
        paz_path.write_bytes(patched_paz_bytes)
        _verify_repacked_archive(pamt_path, temp_root)

    documents = _read_all_package_documents(source_package_path)
    archive_document = _read_json_document(documents, ARCHIVE_INDEX_PATH)
    archive_document["paz_sha256"] = hashlib.sha256(patched_paz_bytes).hexdigest()
    documents[ARCHIVE_INDEX_PATH] = archive_document
    documents[ARCHIVE_PAZ_PATH] = patched_paz_bytes

    manifest = _read_json_document(documents, MANIFEST_PATH)
    source_info = manifest.get("source")
    if not isinstance(source_info, dict):
        source_info = {}
    source_info["witcher_swords_placement"] = {
        "version": PACKAGE_VERSION,
        "method": "exact-size-standalone-entry-repack",
        "description_entry": DESCRIPTION_ENTRY_NAME,
        "body_socket_entry": BODY_SOCKET_ENTRY_NAME,
    }
    manifest["source"] = source_info
    documents[MANIFEST_PATH] = manifest

    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_cdmod_zip(output_path, documents)
    _verify_output_package(output_path, standalone.pamt_bytes, patched_paz_bytes)
    return HumanFemaleWitcherPlacementResult(
        output_path=str(output_path),
        package_sha256=hashlib.sha256(output_path.read_bytes()).hexdigest().upper(),
        source_package_sha256=source_sha256.upper(),
        archive_pamt_sha256=hashlib.sha256(standalone.pamt_bytes).hexdigest().upper(),
        archive_paz_sha256=hashlib.sha256(patched_paz_bytes).hexdigest().upper(),
        entries=tuple(summaries),
    )


def _apply_text_replacements(
    plaintext: bytes,
    replacements: tuple[tuple[str, str], ...],
    entry_name: str,
) -> bytes:
    """在描述 entry 内唯一应用四条作者路由。"""
    result = plaintext
    for old_text, new_text in replacements:
        old = old_text.encode("utf-8")
        new = new_text.encode("utf-8")
        if len(old) != len(new) or result.count(old) != 1:
            raise ValueError(f"{entry_name} 描述路由前置条件异常")
        result = result.replace(old, new, 1)
    if len(result) != len(plaintext):
        raise ValueError(f"{entry_name} 描述明文长度发生变化")
    return result


def _apply_byte_replacements(
    plaintext: bytes,
    replacements: tuple[tuple[bytes, bytes], ...],
    entry_name: str,
) -> bytes:
    """在身体 socket entry 内唯一应用两条等长放置参数。"""
    result = plaintext
    for old, new in replacements:
        if len(old) != len(new) or result.count(old) != 1:
            raise ValueError(f"{entry_name} 身体 socket 前置条件异常")
        result = result.replace(old, new, 1)
    if len(result) != len(plaintext):
        raise ValueError(f"{entry_name} socket 明文长度发生变化")
    return result


def _match_pseudo_xml_compressed_size(
    plaintext: bytes,
    *,
    target_comp_size: int,
) -> tuple[bytes, int]:
    """只调整描述原有注释体的安全 ASCII，使 LZ4 精确回到原槽位。"""
    base_size = len(lz4.block.compress(plaintext, store_size=False))
    if base_size == target_comp_size:
        return plaintext, 0

    safe_positions = _find_existing_comment_ascii_positions(plaintext)
    if not safe_positions:
        raise ValueError("角色描述没有可用于压缩尺寸恢复的原有注释")
    baseline = bytearray(plaintext)
    if base_size > target_comp_size:
        for position in safe_positions:
            baseline[position] = ord("A")
        baseline_size = len(lz4.block.compress(bytes(baseline), store_size=False))
        if baseline_size > target_comp_size:
            raise ValueError("角色描述规范化注释后仍超过原压缩槽位")
        if baseline_size == target_comp_size:
            return bytes(baseline), _count_changed_bytes(
                plaintext,
                bytes(baseline),
                safe_positions,
            )

    for seed in range(MAX_SAFE_XML_FILL_SEEDS):
        safe_fill = _build_deterministic_safe_fill(len(safe_positions), seed=seed)
        candidate = bytearray(baseline)
        for position, value in zip(safe_positions, safe_fill, strict=True):
            candidate[position] = value
            candidate_bytes = bytes(candidate)
            if len(lz4.block.compress(candidate_bytes, store_size=False)) != target_comp_size:
                continue
            candidate_bytes.decode("utf-8-sig")
            if (
                candidate_bytes.count(b"<!--") != plaintext.count(b"<!--")
                or candidate_bytes.count(b"-->") != plaintext.count(b"-->")
            ):
                raise ValueError("角色描述安全填充破坏了注释边界")
            return candidate_bytes, _count_changed_bytes(
                plaintext,
                candidate_bytes,
                safe_positions,
            )
    raise ValueError(f"角色描述无法恢复原 LZ4 槽位 {target_comp_size}")


def _match_socket_xml_compressed_size(
    plaintext: bytes,
    *,
    target_comp_size: int,
) -> tuple[bytes, int]:
    """只把行首缩进 tab 等长换成空格，恢复 socket XML 的原压缩长度。"""
    if len(lz4.block.compress(plaintext, store_size=False)) == target_comp_size:
        ElementTree.fromstring(plaintext.decode("utf-8-sig"))
        return plaintext, 0

    positions = [
        index
        for index, value in enumerate(plaintext)
        if value == 0x09
        and (index == 0 or plaintext[index - 1] in {0x09, 0x0A, 0x0D})
    ]
    for changed_count in range(1, MAX_SOCKET_INDENT_CHANGES + 1):
        for selected in itertools.combinations(positions, changed_count):
            candidate = bytearray(plaintext)
            for position in selected:
                candidate[position] = 0x20
            candidate_bytes = bytes(candidate)
            if len(lz4.block.compress(candidate_bytes, store_size=False)) != target_comp_size:
                continue
            ElementTree.fromstring(candidate_bytes.decode("utf-8-sig"))
            return candidate_bytes, changed_count
    raise ValueError(f"身体 socket XML 无法恢复原 LZ4 槽位 {target_comp_size}")


def _build_exact_payload(
    plaintext: bytes,
    *,
    entry_name: str,
    compression_type: int,
    encrypted: bool,
    target_comp_size: int,
    target_orig_size: int,
) -> bytes:
    """生成和原 PAMT 尺寸完全一致的 entry 载荷。"""
    if len(plaintext) != target_orig_size:
        raise ValueError(f"{entry_name} 明文长度不匹配")
    if compression_type == 2:
        payload = lz4.block.compress(plaintext, store_size=False)
    elif compression_type == 0:
        payload = plaintext
    else:
        raise ValueError(f"{entry_name} 暂不支持压缩类型 {compression_type}")
    if len(payload) != target_comp_size:
        raise ValueError(f"{entry_name} 压缩长度不匹配")
    return encrypt(payload, entry_name) if encrypted else payload


def _verify_repacked_archive(pamt_path: Path, paz_dir: Path) -> None:
    """重新解析成品 PAZ，验证两条目标 entry 的最终明文。"""
    entries = parse_pamt(pamt_path, paz_dir=paz_dir)
    entries_by_name = {Path(entry.path).name.casefold(): entry for entry in entries}
    description, _ = extract_plaintext(entries_by_name[DESCRIPTION_ENTRY_NAME])
    body_socket, _ = extract_plaintext(entries_by_name[BODY_SOCKET_ENTRY_NAME])
    for _old, new in PHW_DESCRIPTION_REPLACEMENTS:
        if description.count(new.encode("utf-8")) != 1:
            raise ValueError("Human Female 描述重读验证失败")
    for _old, new in BODY_SOCKET_REPLACEMENTS:
        if body_socket.count(new) != 1:
            raise ValueError("Human Female 身体 socket 重读验证失败")


def _read_all_package_documents(package_path: Path) -> dict[str, dict[str, object] | bytes]:
    """读取原 cdmod 全部成员，后续只替换明确的 archive 文档和 PAZ。"""
    with zipfile.ZipFile(package_path, "r") as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _read_json_document(
    documents: dict[str, dict[str, object] | bytes],
    archive_path: str,
) -> dict[str, object]:
    """从原包成员读取一个 UTF-8 JSON 对象。"""
    payload = documents.get(archive_path)
    if not isinstance(payload, bytes):
        raise ValueError(f"原包缺少 JSON：{archive_path}")
    value = json.loads(payload.decode("utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{archive_path} 根节点不是对象")
    return value


def _verify_output_package(
    output_path: Path,
    expected_pamt: bytes,
    expected_paz: bytes,
) -> None:
    """回读输出 cdmod，确认仍只有一个完整 standalone 且载荷一致。"""
    package = load_cdmod_package(output_path)
    if len(package.standalone_archives) != 1:
        raise ValueError("输出 Human Female 包 standalone 数量异常")
    standalone = package.standalone_archives[0]
    if standalone.pamt_bytes != expected_pamt or standalone.paz_bytes != expected_paz:
        raise ValueError("输出 Human Female standalone 载荷回读不一致")


def _count_changed_bytes(original: bytes, adjusted: bytes, positions: list[int]) -> int:
    """统计为恢复精确压缩尺寸而调整的安全字节数量。"""
    return sum(original[position] != adjusted[position] for position in positions)


def _parse_args() -> argparse.Namespace:
    """解析 Human Female 原包与输出包路径。"""
    parser = argparse.ArgumentParser(
        description="把 Witcher Swords PHW 放置参数嵌入 Human Female standalone"
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    return parser.parse_args()


def main() -> int:
    """执行精确重打包并输出 UTF-8 JSON 摘要。"""
    args = _parse_args()
    result = repack_human_female_witcher_swords_placement(args.source, args.output)
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
