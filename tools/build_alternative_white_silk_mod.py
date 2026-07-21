"""基于当前原版资源生成黑色面罩替换为白色丝巾的 ``.cdmod``。

Crimson Desert 1.14 同时存在男性与女性 0271 面罩 Prefab。旧版 loose 模组
只覆盖女性 PAC，更新后会因为角色选择另一条 Prefab 分支而失效。本工具保留
当前 Prefab 结构，仅把内部 PAC 路径等长改为 0248 丝巾，并把当前 0248 的
默认材质恢复为白色 Silk。工具不会修改原版 PAZ/PAMT。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path

from cdmm.archive.pamt import parse_pamt_filtered
from cdmm.common.models import PazEntry
from cdmm.services.cdmod_converter import (
    CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE,
    CDMOD_FORMAT_NAME,
    CDMOD_FORMAT_VERSION,
    CDMOD_MANIFEST_PATH,
    CDMOD_REPORT_PATH,
    _write_cdmod_zip,
)
from cdmm.services.cdmod_package import load_cdmod_package
from cdmm.services.json_loader import extract_plaintext

# 面具与丝巾资源都位于原版 0009 PAMT。
PAMT_DIR = "0009"

# 当前游戏的女性 0248 丝巾材质资源。
FEMALE_SILK_PROPERTY = (
    "character/modelproperty/1_pc/2_phw/armor/20_mask/cd_phw_01_mask_0248.pac_xml"
)

# 0271 面罩的两条实际 Prefab 选择分支。
MALE_MASK_PREFAB = (
    "character/bin__/prefab/1_pc/01_phm/armor/20_mask/cd_phm_00_mask_00_0271_a.prefab"
)
FEMALE_MASK_PREFAB = (
    "character/bin__/prefab/1_pc/02_phw/armor/20_mask/cd_phw_01_mask_00_0271_a.prefab"
)

# Prefab 内唯一 PAC 路径保持完全等长，只替换末尾资源编号。
PREFAB_PAC_PATHS = {
    MALE_MASK_PREFAB: (
        b"character/model/1_pc/1_phm/armor/20_mask/cd_phm_00_mask_0271.pac",
        b"character/model/1_pc/2_phw/armor/20_mask/cd_phw_01_mask_0248.pac",
    ),
    FEMALE_MASK_PREFAB: (
        b"character/model/1_pc/2_phw/armor/20_mask/cd_phw_01_mask_0271.pac",
        b"character/model/1_pc/2_phw/armor/20_mask/cd_phw_01_mask_0248.pac",
    ),
}

# 2026-07-21 从 Crimson Desert 1.14 原版提取的输入锚点。
EXPECTED_SOURCE_SHA256 = {
    MALE_MASK_PREFAB: "bd6a36886faf6b68c97b50380ef5c8a68d508ac2a69b76568d09d59632d51633",
    FEMALE_MASK_PREFAB: "fc1280648c2832e021a12cc3727a1246ac682aaa90fb91aba531daa768f9704a",
    FEMALE_SILK_PROPERTY: "d0e9819340b12028fcdf2d94bae675d46ceed23367576fd73fab47260ab26e80",
}

# cdmod 内部文档与二进制载荷使用固定路径，保证重复生成字节一致。
FILE_REPLACEMENT_PATH = "files/replacements.json"
PAYLOAD_PATHS = {
    MALE_MASK_PREFAB: "assets/00000/cd_phm_00_mask_00_0271_a.prefab",
    FEMALE_MASK_PREFAB: "assets/00001/cd_phw_01_mask_00_0271_a.prefab",
    FEMALE_SILK_PROPERTY: "assets/00002/cd_phw_01_mask_0248.pac_xml",
}

PACKAGE_ID = "grudger.alternative-mask-white-silk"
PACKAGE_NAME = "Alternative Mask - White Silk"
PACKAGE_VERSION = "1.14.2"


@dataclass(frozen=True)
class ResourceAudit:
    """记录一个来源资源到目标资源的可审计映射。"""

    target: str
    vanilla_size: int
    vanilla_sha256: str
    patched_size: int
    patched_sha256: str
    changed_byte_count: int
    target_vanilla_comp_size: int
    target_vanilla_orig_size: int


@dataclass(frozen=True)
class BuildResult:
    """生成结果与完整包摘要。"""

    output_path: Path
    package_sha256: str
    resources: tuple[ResourceAudit, ...]


def build_white_silk_mod(game_dir: Path, output_path: Path) -> BuildResult:
    """生成双性别 Prefab 路由与白色 Silk 材质替换。"""
    game_dir = game_dir.resolve()
    output_path = output_path.resolve()
    pamt_path = game_dir / PAMT_DIR / "0.pamt"
    if not pamt_path.is_file():
        raise FileNotFoundError(f"缺少当前游戏 PAMT：{pamt_path}")

    requested_names = {Path(path).name for path in EXPECTED_SOURCE_SHA256}
    entries = parse_pamt_filtered(pamt_path, desired_basenames=requested_names)

    replacement_specs: list[dict[str, object]] = []
    documents: dict[str, dict[str, object] | bytes] = {}
    audits: list[ResourceAudit] = []
    for target_path, expected_digest in EXPECTED_SOURCE_SHA256.items():
        target_entry = _find_exact_entry(entries, target_path)
        vanilla, _resolved_entry = extract_plaintext(target_entry)
        vanilla_digest = hashlib.sha256(vanilla).hexdigest()
        if vanilla_digest != expected_digest:
            raise ValueError(
                f"当前游戏输入资源已变化，拒绝盲目生成：{target_path} "
                f"expected={expected_digest} actual={vanilla_digest}"
            )
        patched = _patch_resource(target_path, vanilla)
        patched_digest = hashlib.sha256(patched).hexdigest()
        payload_path = PAYLOAD_PATHS[target_path]

        replacement_specs.append(
            {
                "target": target_path,
                "pamt_dir": PAMT_DIR,
                "payload": payload_path,
                "sha256": patched_digest,
                "size": len(patched),
                "allow_new": False,
                "allow_table_replace": False,
            }
        )
        documents[payload_path] = patched
        audits.append(
            ResourceAudit(
                target=target_path,
                vanilla_size=len(vanilla),
                vanilla_sha256=vanilla_digest,
                patched_size=len(patched),
                patched_sha256=patched_digest,
                changed_byte_count=_changed_byte_count(vanilla, patched),
                target_vanilla_comp_size=target_entry.comp_size,
                target_vanilla_orig_size=target_entry.orig_size,
            )
        )

    manifest = {
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": PACKAGE_ID,
        "name": PACKAGE_NAME,
        "version": PACKAGE_VERSION,
        "author": "Grudger; 1.14 compatibility rebuild by cdmm",
        "description": (
            "Routes both male and female 0271 mask prefabs to the current female "
            "0248 scarf PAC and restores its default material to white Silk."
        ),
        "dependencies": [],
        "source": {
            "format": "current-prefab-same-length-pac-path-remap",
            "game_version": "1.14",
            "pamt_dir": PAMT_DIR,
        },
        "components": [
            {
                "type": CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE,
                "path": FILE_REPLACEMENT_PATH,
                "file_count": len(replacement_specs),
            }
        ],
    }
    report = {
        "schema": 1,
        "purpose": "male-and-female-0271-black-mask-to-0248-white-silk-scarf",
        "game_version": "1.14",
        "resources": [asdict(item) for item in audits],
        "safety": {
            "modifies_vanilla_archives": False,
            "uses_standalone_archive": False,
            "preserves_current_prefab_structure": True,
            "uses_same_length_pac_path_replacement": True,
            "covers_male_and_female_prefab_branches": True,
            "uses_female_mesh_for_cc_gender_conversion": True,
            "target_exists_in_current_pamt": True,
        },
    }
    documents[CDMOD_MANIFEST_PATH] = manifest
    documents[FILE_REPLACEMENT_PATH] = {"schema": 1, "files": replacement_specs}
    documents[CDMOD_REPORT_PATH] = report

    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_cdmod_zip(output_path, documents)
    _verify_package(output_path, documents)
    return BuildResult(
        output_path=output_path,
        package_sha256=hashlib.sha256(output_path.read_bytes()).hexdigest(),
        resources=tuple(audits),
    )


def _patch_resource(target_path: str, vanilla: bytes) -> bytes:
    """按资源类型执行严格、可审计的当前原版变换。"""
    if target_path in PREFAB_PAC_PATHS:
        old_path, new_path = PREFAB_PAC_PATHS[target_path]
        if len(old_path) != len(new_path):
            raise ValueError(f"Prefab PAC 路径不是等长替换：{target_path}")
        if vanilla.count(old_path) != 1:
            raise ValueError(f"Prefab PAC 旧路径不是唯一命中：{target_path}")
        return vanilla.replace(old_path, new_path, 1)
    if target_path == FEMALE_SILK_PROPERTY:
        return _build_white_silk_property(vanilla, target_path)
    raise ValueError(f"没有注册资源变换：{target_path}")


# 旧版白色 Silk 默认材质中已验证的颜色与布料参数。
WHITE_COLOR_VALUES = {
    "_tintColorR": "#ffe7b3ff",
    "_tintColorG": "#ffe7b3ff",
    "_tintColorB": "#ffe7b3ff",
    "_dyeingDetailLayerColorMaskR": "#ffffecff",
    "_dyeingDetailLayerColorMaskG": "#ffffecff",
    "_dyeingDetailLayerColorMaskB": "#ffffecff",
}
WHITE_SCALAR_VALUES = {
    "_grimeBlendingParameterR": "570427143",
    "_grimeBlendingParameterG": "4278203187",
    "_grimeBlendingParameterB": "4281545523",
    "_grimeBlendingOpacityParameter": "4278255431",
    "_dyeingPropertyBlend": "2139062038",
    "_colorBlendingFlag": "15",
    "_dyeingGlobalOpacity": "16777215",
    "_dyeingTransformProperty0": "3084",
    "_dyeingTransformProperty1": "65535",
    "_dyeingTransformProperty3": "13107",
    "_detailScreenSpaceDisplacementScale": "0.010000",
    "_clothMaskBit": "1",
}


def _build_white_silk_property(vanilla: bytes, target_path: str) -> bytes:
    """在当前 PAC_XML 中只修改默认 Index=0 材质，保留其他版本结构。"""
    try:
        text = vanilla.decode("utf-8-sig")
        root = ET.fromstring(f"<CdmmRoot>{text}</CdmmRoot>")
    except (UnicodeDecodeError, ET.ParseError) as exc:
        raise ValueError(f"PAC_XML 无法按当前结构解析：{target_path}: {exc}") from exc

    model_property = root.find("./ModelPropertyList/ModelProperty[@Index='0']")
    if model_property is None:
        raise ValueError(f"PAC_XML 缺少默认 ModelProperty Index=0：{target_path}")

    changed_names: set[str] = set()
    for element in model_property.iter():
        name = element.get("_name")
        if name in WHITE_COLOR_VALUES:
            element.set("_value", WHITE_COLOR_VALUES[name])
            changed_names.add(name)
        elif name in WHITE_SCALAR_VALUES:
            element.set("_value", WHITE_SCALAR_VALUES[name])
            changed_names.add(name)
        elif name == "_clothCategory":
            element.set("_value", "Silk")
            changed_names.add(name)

    required = {"_tintColorR", "_tintColorG", "_colorBlendingFlag", "_clothCategory"}
    missing = sorted(required - changed_names)
    if missing:
        raise ValueError(f"PAC_XML 默认材质缺少必要参数 {missing}：{target_path}")

    children = [ET.tostring(child, encoding="unicode") for child in root]
    return b"\xef\xbb\xbf" + "\r\n".join(children).encode("utf-8") + b"\r\n"


def _changed_byte_count(before: bytes, after: bytes) -> int:
    """统计同位置差异并计入长度变化，供报告审计。"""
    shared = sum(left != right for left, right in zip(before, after))
    return shared + abs(len(before) - len(after))


def _find_exact_entry(entries: list[PazEntry], final_path: str) -> PazEntry:
    """按 folder record 还原的最终路径寻找唯一原版资源。"""
    normalized = final_path.replace("\\", "/").lower().strip("/")
    matches = []
    for entry in entries:
        parent = (entry.resolved_dir_path or "").replace("\\", "/").lower().strip("/")
        basename = Path(entry.path).name.lower()
        candidate = f"{parent}/{basename}" if parent else entry.path.lower().strip("/")
        if candidate == normalized:
            matches.append(entry)
    if len(matches) != 1:
        raise ValueError(
            f"当前游戏目标不是唯一命中：{final_path} matches={len(matches)}"
        )
    return matches[0]


def _verify_package(
    output_path: Path,
    documents: dict[str, dict[str, object] | bytes],
) -> None:
    """使用正式解析器回读并核对目标、目录、载荷与依赖。"""
    package = load_cdmod_package(output_path)
    if package.dependencies or package.standalone_archives or package.resource_patches:
        raise ValueError("白色丝巾包只能包含无依赖的 file-replacement")
    files = [item for patch in package.file_patches for item in patch.files]
    if len(files) != len(PAYLOAD_PATHS):
        raise ValueError(f"白色丝巾包资源数量异常：{len(files)}")
    expected = {target: documents[payload] for target, payload in PAYLOAD_PATHS.items()}
    for item in files:
        if item.pamt_dir != PAMT_DIR or item.target not in expected:
            raise ValueError(f"白色丝巾包最终目标异常：{item.pamt_dir}/{item.target}")
        if item.content != expected[item.target]:
            raise ValueError(f"白色丝巾包载荷回读不一致：{item.target}")

    # 两个 Prefab 都必须能从 0248 往返恢复为当前 0271 原版结构。
    for target_path, (old_path, new_path) in PREFAB_PAC_PATHS.items():
        patched = expected[target_path]
        if patched.count(new_path) != 1 or old_path in patched:
            raise ValueError(f"Prefab PAC 路由回读异常：{target_path}")


def _parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="生成 1.14 白色丝巾面罩替换 .cdmod")
    parser.add_argument(
        "--game-dir", type=Path, required=True, help="Crimson Desert 根目录"
    )
    parser.add_argument("--output", type=Path, required=True, help="输出 .cdmod 路径")
    return parser.parse_args()


def main() -> int:
    """生成包并输出 UTF-8 JSON 审计摘要。"""
    args = _parse_args()
    result = build_white_silk_mod(args.game_dir, args.output)
    payload = asdict(result)
    payload["output_path"] = str(result.output_path)
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
