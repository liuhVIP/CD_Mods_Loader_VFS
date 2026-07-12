"""把 Male Glide Animation 转换为无资源载荷的生成型 ``.cdmod``。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from cdmm.archive.pamt import parse_pamt
from cdmm.common.models import PazEntry
from cdmm.services.cdmod_converter import (
    CDMOD_FORMAT_NAME,
    CDMOD_FORMAT_VERSION,
    CDMOD_MANIFEST_PATH,
    CDMOD_REPORT_PATH,
    CDMOD_RESOURCE_TRANSFORM_COMPONENT_TYPE,
    _write_cdmod_zip,
)
from cdmm.services.json_loader import extract_plaintext

# 资源变换文档固定路径，内容不含绝对机器路径。
CDMOD_RESOURCE_TRANSFORM_PATH = "patches/resources.json"

# PAAC 字符串表只能做等长替换，避免移动后续字节码 offset。
_SHIELD_REPLACEMENTS = (
    (b"equip_shield", b"equip_shielx"),
    (b"CD_MainWeapon_Shield_R", b"CD_MainWeapon_Shielx_R"),
)


@dataclass(frozen=True)
class MaleGlideConversionResult:
    """Male Glide 生成型转换摘要。"""

    output_path: Path
    package_sha256: str
    source_sha256: str
    copy_entry_count: int
    replace_bytes_count: int
    omitted_payload_bytes: int


def convert_male_glide_to_cdmod(
    source_dir: Path,
    game_dir: Path,
    output_path: Path,
) -> MaleGlideConversionResult:
    """验证真实资源等价性并生成 resource-transform 包。"""
    source_dir = source_dir.resolve()
    game_dir = game_dir.resolve()
    output_path = output_path.resolve()
    manifest = _read_json(source_dir / "manifest.json")
    mod_document = _read_json(source_dir / "mod.json")
    raw_files = manifest.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise ValueError("manifest.files 必须是非空数组")

    entries_by_dir = {
        "0009": _entry_basename_index(game_dir, "0009"),
        "0010": _entry_basename_index(game_dir, "0010"),
    }
    operations: list[dict[str, object]] = []
    source_digest = hashlib.sha256()
    omitted_payload_bytes = 0
    copy_count = 0
    replace_count = 0
    for index, raw_file in enumerate(raw_files):
        if not isinstance(raw_file, dict):
            raise ValueError(f"manifest.files[{index}] 必须是对象")
        target = _normalize_path(str(raw_file.get("path") or ""))
        pamt_dir = str(raw_file.get("package_group") or "")
        if pamt_dir not in entries_by_dir:
            raise ValueError(f"不支持的 package_group：{pamt_dir}")
        mod_path = source_dir / "files" / Path(target.replace("/", "\\"))
        mod_bytes = mod_path.read_bytes()
        omitted_payload_bytes += len(mod_bytes)
        source_digest.update(target.encode("utf-8"))
        source_digest.update(b"\0")
        source_digest.update(mod_bytes)
        source_digest.update(b"\0")
        if target.endswith(".paa"):
            source_name = _phw_to_phm(Path(target).name)
            source_entry = entries_by_dir[pamt_dir].get(source_name)
            if source_entry is None:
                raise ValueError(f"当前游戏缺少 PHM 动画源：{source_name}")
            current_source, _detected = extract_plaintext(source_entry)
            if current_source != mod_bytes:
                raise ValueError(f"模组 PAA 不再等于当前游戏 PHM 源：{target} <- {source_name}")
            operations.append(
                {
                    "op": "copy-entry",
                    "target": target,
                    "target_pamt_dir": pamt_dir,
                    "source": f"character/{source_name}",
                    "source_pamt_dir": pamt_dir,
                }
            )
            copy_count += 1
            continue
        if target.endswith(".paac"):
            target_entry = entries_by_dir[pamt_dir].get(Path(target).name.lower())
            if target_entry is None:
                raise ValueError(f"当前游戏缺少 PAAC 目标：{target}")
            current_target, _detected = extract_plaintext(target_entry)
            expected = current_target
            for old, new in _SHIELD_REPLACEMENTS:
                expected = expected.replace(old, new)
            if expected != mod_bytes:
                raise ValueError(f"模组 PAAC 不等于当前原版的等长补丁结果：{target}")
            operations.append(
                {
                    "op": "replace-bytes",
                    "target": target,
                    "target_pamt_dir": pamt_dir,
                    "replacements": [
                        {"old_hex": old.hex(), "new_hex": new.hex()}
                        for old, new in _SHIELD_REPLACEMENTS
                    ],
                }
            )
            replace_count += 1
            continue
        raise ValueError(f"不支持的 Male Glide 资源类型：{target}")

    metadata = mod_document.get("modinfo")
    if not isinstance(metadata, dict):
        metadata = {}
    source_sha256 = source_digest.hexdigest()
    patch_document = {"schema": 1, "operations": operations}
    manifest_document = {
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": "deletriuz-male-glide-animation",
        "name": str(metadata.get("title") or manifest.get("title") or "Male Glide Animation"),
        "version": str(metadata.get("version") or manifest.get("version") or "0.0.0"),
        "author": str(metadata.get("author") or manifest.get("author") or "unknown"),
        "description": str(metadata.get("description") or ""),
        "dependencies": [],
        "source": {
            "format": "loose-resource-transform",
            "sha256": source_sha256,
            "omitted_payload_bytes": omitted_payload_bytes,
        },
        "components": [
            {
                "type": CDMOD_RESOURCE_TRANSFORM_COMPONENT_TYPE,
                "path": CDMOD_RESOURCE_TRANSFORM_PATH,
                "operation_count": len(operations),
            }
        ],
    }
    report_document = {
        "schema": 1,
        "source": {"name": source_dir.name, "sha256": source_sha256},
        "summary": {
            "copy_entry_count": copy_count,
            "replace_bytes_count": replace_count,
            "omitted_payload_bytes": omitted_payload_bytes,
        },
        "safety": {
            "paa_policy": "current-game-source-byte-equality-required",
            "paac_policy": "current-game-equal-length-patch-equality-required",
            "game_update_policy": "rebuild-from-current-game-or-reject",
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_cdmod_zip(
        output_path,
        {
            CDMOD_MANIFEST_PATH: manifest_document,
            CDMOD_RESOURCE_TRANSFORM_PATH: patch_document,
            CDMOD_REPORT_PATH: report_document,
        },
    )
    return MaleGlideConversionResult(
        output_path=output_path,
        package_sha256=hashlib.sha256(output_path.read_bytes()).hexdigest(),
        source_sha256=source_sha256,
        copy_entry_count=copy_count,
        replace_bytes_count=replace_count,
        omitted_payload_bytes=omitted_payload_bytes,
    )


def male_glide_result_to_json(result: MaleGlideConversionResult) -> dict[str, object]:
    """把转换结果变为 CLI 可序列化对象。"""
    payload = asdict(result)
    payload["output_path"] = str(result.output_path)
    return payload


def _entry_basename_index(game_dir: Path, pamt_dir: str) -> dict[str, PazEntry]:
    """为指定 vanilla PAMT 建立唯一 basename 索引。"""
    entries = parse_pamt(game_dir / pamt_dir / "0.pamt", game_dir / pamt_dir)
    result: dict[str, PazEntry] = {}
    duplicates: set[str] = set()
    for entry in entries:
        basename = Path(entry.path).name.lower()
        if basename in result:
            duplicates.add(basename)
        else:
            result[basename] = entry
    for basename in duplicates:
        result.pop(basename, None)
    return result


def _phw_to_phm(name: str) -> str:
    """按作者 v2.8 已验证规则把 PHW 目标名映射到 PHM 源名。"""
    name = name.lower().replace("cd_phw_", "cd_phm_")
    name = name.replace("air_base_gliding_move_", "nor_move_gliding_")
    name = name.replace("air_base_gliding_std_", "nor_move_gliding_")
    name = name.replace("air_base_gliding_", "nor_move_gliding_")
    for old, new in (
        ("acceleate2_f_ing", "acceleate2_ing"),
        ("acceleate2_f_stt", "acceleate2_stt"),
        ("acceleate_f_ing", "acceleate_ing"),
        ("acceleate_f_stt", "acceleate_stt"),
        ("move_acceleate2_f", "acceleate2"),
        ("move_acceleate_f", "acceleate"),
        ("move_f_landing", "off"),
        ("f_landing", "off"),
        ("move_deceleate_b_00", "deceleate_stt_00"),
        ("move_deceleate_b_01", "deceleate_ing_01"),
        ("deceleate_b_00", "deceleate_stt_00"),
        ("deceleate_b_01", "deceleate_ing_01"),
        ("std_idle", "idle"),
        ("std_turnl", "turnl"),
        ("std_turnr", "turnr"),
        ("_at_shield_01", "_at_cloak_00"),
        ("gliding_f_stt", "gliding_stt"),
        ("off_00_at_cloak_00", "off_00"),
        ("cd_phm_rpr_01_01_air_base_std_gliding_reflex_", "cd_phm_swd_01_01_air_base_std_gliding_reflex_"),
        ("cd_phm_rpr_", "cd_phm_swd_"),
        ("nor_move_gliding_dash_f", "air_base_move_gliding_acceleate_dash_f"),
        ("nor_move_gliding_f_jijeongta_hand_l_end", "air_att_move_gliding_jijengta_end"),
        ("nor_move_gliding_f_jijeongta_hand_l_ing", "air_att_move_gliding_jijengta_ing"),
        ("nor_move_gliding_f_jijeongta_hand_l_stt", "air_att_move_gliding_jijengta_stt"),
        ("nor_move_gliding_f_jijeongta", "air_att_move_gliding_jijengta"),
        ("_01_01_air_att_move_gliding", "_00_00_air_att_move_gliding"),
    ):
        name = name.replace(old, new)
    return name


def _normalize_path(value: str) -> str:
    """规范化 manifest 游戏相对路径。"""
    normalized = value.replace("\\", "/").strip("/").lower()
    if not normalized or ".." in normalized.split("/"):
        raise ValueError(f"非法资源路径：{value}")
    return normalized


def _read_json(path: Path) -> dict[str, object]:
    """读取 UTF-8 JSON 对象。"""
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON 根节点必须是对象：{path.name}")
    return value
