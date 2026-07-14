"""从四个火焰长枪 .cdmod 生成对应的闪电属性版本。

仅替换已经由 RighteousVerdict_Lightning.field.cdmod 验证的雷电特效、
被动技能和 docking 标签哈希；各武器原有的 selector、冷却和充能数保持不变。
输出采用与项目转换器一致的确定性 ZIP 格式，便于校验与重复构建。
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# .cdmod 容器内固定的语义补丁路径。
SEMANTIC_PATCH_PATH = "patches/semantic.json"

# .cdmod 容器内固定的清单路径。
MANIFEST_PATH = "manifest.json"

# 固定 ZIP 时间戳保证同一输入可得到字节一致的构建结果。
DETERMINISTIC_ZIP_TIMESTAMP = (2026, 1, 1, 0, 0, 0)

# 已验证的火焰与闪电游戏字段映射，来自 RighteousVerdict 雷电包。
FLAME_GIMMICK_INFO_KEY = 1_001_492
LIGHTNING_GIMMICK_INFO_KEY = 1_001_961
FLAME_PASSIVE_SKILL_KEY = 91_105
LIGHTNING_PASSIVE_SKILL_KEY = 91_101
FLAME_DOCKING_TAG_HASH = 666_382_090
LIGHTNING_DOCKING_TAG_HASH = 3_365_725_887

# 四把长枪火焰包与输出雷电包的稳定映射。
SOURCE_TO_OUTPUT_NAMES = {
    "AeserionSpear_Flame.field.cdmod": "AeserionSpear_Lightning.field.cdmod",
    "GraspingMoon_Flame.field.cdmod": "GraspingMoon_Lightning.field.cdmod",
    "DivergingMoon_Flame.field.cdmod": "DivergingMoon_Lightning.field.cdmod",
    "CirclingMoon_Flame.field.cdmod": "CirclingMoon_Lightning.field.cdmod",
}


@dataclass(frozen=True)
class BuildResult:
    """单个闪电长枪包的构建与校验摘要。"""

    output_path: Path
    sha256: str
    operation_count: int
    changed_count: int


def build_lightning_spear_mods(mods_dir: Path) -> list[BuildResult]:
    """从指定 mods 目录中的火焰包生成四个闪电包。"""
    results: list[BuildResult] = []
    for source_name, output_name in SOURCE_TO_OUTPUT_NAMES.items():
        source_path = mods_dir / source_name
        output_path = mods_dir / output_name
        if not source_path.is_file():
            raise FileNotFoundError(f"缺少火焰源模组：{source_path}")
        results.append(build_lightning_spear_mod(source_path, output_path))
    return results


def build_lightning_spear_mod(source_path: Path, output_path: Path) -> BuildResult:
    """读取一个火焰包，转换其语义补丁并写入一个闪电包。"""
    manifest, patch_document = _read_source_documents(source_path)
    lightning_patch, changed_count, operation_count = _convert_patch_document(patch_document)
    lightning_manifest = _convert_manifest(manifest, source_path, lightning_patch, operation_count)
    _write_cdmod_zip(
        output_path,
        {
            MANIFEST_PATH: lightning_manifest,
            SEMANTIC_PATCH_PATH: lightning_patch,
        },
    )
    _validate_lightning_package(output_path, operation_count, changed_count)
    return BuildResult(
        output_path=output_path,
        sha256=hashlib.sha256(output_path.read_bytes()).hexdigest(),
        operation_count=operation_count,
        changed_count=changed_count,
    )


def _read_source_documents(source_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """从 ZIP 读取并校验火焰包的基础清单和语义补丁。"""
    with zipfile.ZipFile(source_path) as archive:
        manifest = _read_zip_json(archive, MANIFEST_PATH)
        patch_document = _read_zip_json(archive, SEMANTIC_PATCH_PATH)
    return manifest, patch_document


def _read_zip_json(archive: zipfile.ZipFile, path: str) -> dict[str, Any]:
    """读取 ZIP 内的 UTF-8 JSON 对象。"""
    value = json.loads(archive.read(path).decode("utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} 根节点必须是对象")
    return value


def _convert_patch_document(document: dict[str, Any]) -> tuple[dict[str, Any], int, int]:
    """把火焰特效相关字段转换为雷电值，并拒绝不完整的输入。"""
    result = copy.deepcopy(document)
    targets = result.get("targets")
    if not isinstance(targets, list) or len(targets) != 1:
        raise ValueError("火焰长枪包必须包含唯一的语义目标")

    changed_count = 0
    operation_count = 0
    for target in targets:
        if not isinstance(target, dict) or target.get("file") != "iteminfo.pabgb":
            raise ValueError("火焰长枪包的目标必须是 iteminfo.pabgb")
        operations = target.get("operations")
        if not isinstance(operations, list):
            raise ValueError("火焰长枪包缺少 operations 数组")
        for operation in operations:
            if not isinstance(operation, dict):
                raise ValueError("operations 内存在非对象内容")
            operation_count += 1
            changed_count += _convert_operation(operation)

    # 每包应替换：docking 1 项、passive skill 1 项、gimmick 1 项，共 3 项；
    # Aeserion 的完整 docking 对象中还含有标签哈希，故额外发生 1 次替换。
    if changed_count not in (3, 4):
        raise ValueError(f"火焰字段替换数量异常：期望 3 或 4，实际 {changed_count}")
    return result, changed_count, operation_count


def _convert_operation(operation: dict[str, Any]) -> int:
    """转换单条操作中的雷电相关值，返回发生的字段替换次数。"""
    path = operation.get("path")
    value = operation.get("value")
    if path in {"gimmick_info", "docking_child_data.gimmick_info_key"}:
        if value != FLAME_GIMMICK_INFO_KEY:
            raise ValueError(f"{path} 不是预期的火焰特效 ID")
        operation["value"] = LIGHTNING_GIMMICK_INFO_KEY
        return 1
    if path == "equip_passive_skill_list":
        if not isinstance(value, list) or value != [{"level": 3, "skill": FLAME_PASSIVE_SKILL_KEY}]:
            raise ValueError("equip_passive_skill_list 不是预期的火焰被动技能")
        operation["value"] = [{"level": 3, "skill": LIGHTNING_PASSIVE_SKILL_KEY}]
        return 1
    if path == "docking_child_data":
        if not isinstance(value, dict):
            raise ValueError("docking_child_data 必须是对象")
        changed_count = 0
        if value.get("gimmick_info_key") != FLAME_GIMMICK_INFO_KEY:
            raise ValueError("docking_child_data.gimmick_info_key 不是火焰特效 ID")
        value["gimmick_info_key"] = LIGHTNING_GIMMICK_INFO_KEY
        changed_count += 1
        tag_hash = value.get("docking_tag_name_hash")
        if tag_hash != [FLAME_DOCKING_TAG_HASH, 0, 0, 0]:
            raise ValueError("docking_child_data.docking_tag_name_hash 不是火焰标签哈希")
        value["docking_tag_name_hash"] = [LIGHTNING_DOCKING_TAG_HASH, 0, 0, 0]
        return changed_count + 1
    return 0


def _convert_manifest(
    manifest: dict[str, Any],
    source_path: Path,
    patch_document: dict[str, Any],
    operation_count: int,
) -> dict[str, Any]:
    """生成与火焰源包分离、可被扫描器独立识别的雷电清单。"""
    result = copy.deepcopy(manifest)
    stem = source_path.name.removesuffix("_Flame.field.cdmod")
    lightning_name = f"{stem}_Lightning"
    result["id"] = f"crimsongamemods-itembuffs-{stem.lower()}-lightning"
    result["name"] = lightning_name
    result["description"] = f"{operation_count} field-level lightning intent(s)"
    result["source"] = {
        "format": "derived-lightning-semantic-patch",
        "sha256": hashlib.sha256(_json_bytes(patch_document)).hexdigest(),
        "derived_from": source_path.name,
    }
    return result


def _validate_lightning_package(path: Path, expected_operation_count: int, expected_changed_count: int) -> None:
    """重新读取输出包，确认清单、操作数和雷电字段均已正确落盘。"""
    manifest, document = _read_source_documents(path)
    if not str(manifest.get("name", "")).endswith("_Lightning"):
        raise ValueError("输出包名称未标记为 Lightning")
    serialized = _json_bytes(document)
    if str(FLAME_GIMMICK_INFO_KEY).encode() in serialized:
        raise ValueError("输出包仍包含火焰特效 ID")
    if str(FLAME_PASSIVE_SKILL_KEY).encode() in serialized:
        raise ValueError("输出包仍包含火焰被动技能 ID")
    if str(FLAME_DOCKING_TAG_HASH).encode() in serialized:
        raise ValueError("输出包仍包含火焰 docking 标签哈希")
    _, changed_count, operation_count = _convert_patch_document(_reverse_lightning_values(document))
    if operation_count != expected_operation_count or changed_count != expected_changed_count:
        raise ValueError("输出包操作数量或雷电字段数量不一致")


def _reverse_lightning_values(document: dict[str, Any]) -> dict[str, Any]:
    """将校验输入临时还原为火焰值，以复用严格的结构校验逻辑。"""
    result = copy.deepcopy(document)
    encoded = json.dumps(result, ensure_ascii=False)
    encoded = encoded.replace(str(LIGHTNING_GIMMICK_INFO_KEY), str(FLAME_GIMMICK_INFO_KEY))
    encoded = encoded.replace(str(LIGHTNING_PASSIVE_SKILL_KEY), str(FLAME_PASSIVE_SKILL_KEY))
    encoded = encoded.replace(str(LIGHTNING_DOCKING_TAG_HASH), str(FLAME_DOCKING_TAG_HASH))
    return json.loads(encoded)


def _write_cdmod_zip(output_path: Path, documents: dict[str, dict[str, Any]]) -> None:
    """按固定文件顺序和时间戳写入 UTF-8 确定性 ZIP。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for archive_path in sorted(documents):
            info = zipfile.ZipInfo(archive_path, DETERMINISTIC_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, _json_bytes(documents[archive_path]), compresslevel=9)


def _json_bytes(document: dict[str, Any]) -> bytes:
    """以项目统一的 UTF-8 JSON 格式序列化对象。"""
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"


def _parse_args() -> argparse.Namespace:
    """读取构建命令参数。"""
    parser = argparse.ArgumentParser(description="从火焰长枪包生成四个闪电属性 .cdmod")
    parser.add_argument(
        "--mods-dir",
        type=Path,
        default=Path(r"G:\SteamLibrary\steamapps\common\Crimson Desert\mods"),
        help="包含四个火焰源包和输出闪电包的 mods 目录",
    )
    return parser.parse_args()


def main() -> int:
    """执行构建并输出可审计摘要。"""
    args = _parse_args()
    try:
        results = build_lightning_spear_mods(args.mods_dir)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f"构建失败：{exc}", file=sys.stderr)
        return 1
    for result in results:
        print(
            f"已生成 {result.output_path.name}: operations={result.operation_count}, "
            f"changed={result.changed_count}, sha256={result.sha256}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
