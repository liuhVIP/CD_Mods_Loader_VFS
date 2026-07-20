"""生成“战场之光”替换为旧版白色尖帽的完整材质测试包。

白色尖帽主网格来自旧版 ``0009/1.paz`` 中的 0129 PAC。当前游戏仍保留
相同字节的 PAC、材质 XML、物理 HKX 与纹理，因此成品保留 0151 Prefab
结构，并为 0129 建立等长私有别名，同时携带 PAC、PAC_XML 与 HKX，避免
私有 PAC 因找不到同名材质定义而回退成高光塑料材质。
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from cdmm.archive.pamt import parse_pamt_filtered
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
from cdmm.tools.build_battlefield_light_white_crow_hat_swap import (
    BATTLEFIELD_LIGHT_HAT_PATH,
    BATTLEFIELD_LIGHT_MAIN_PAC,
    EXPECTED_TARGET_SHA256,
    HAT_PAMT_DIR,
)

# 旧版白色尖帽的规范资源路径。
SOURCE_PAC_ENTRY_PATH = "character/cd_phw_00_hel_0129.pac"
SOURCE_PAC_XML_ENTRY_PATH = "character/cd_phw_00_hel_0129.pac_xml"
SOURCE_HKX_ENTRY_PATH = "character/cd_phw_00_hel_0129.hkx"

# 当前 1.14 与用户提供旧版 1.paz 中相同的白色尖帽资源指纹。
EXPECTED_SOURCE_PAC_SHA256 = (
    "4b78c36da7ff854a684a944ecb97a294ed71e3eb94db58bcc500eaf52dd4971f"
)
EXPECTED_SOURCE_PAC_XML_SHA256 = (
    "d5b999e77e751e190cbb6b0d10db399f9fbc1e82104c9cabe0c509751a27fe68"
)
EXPECTED_SOURCE_HKX_SHA256 = (
    "dcc188b4e55ded354850720dd148935b79c13531c88764b47d82ac937e315c7c"
)

# 私有别名保持与 0151 主 PAC 路径等长，确保 Prefab 只发生原位字符串替换。
ALIAS_BASENAME = "cd_phw_00_hel_00_9129"
ALIAS_MODEL_PAC_PATH = (
    f"character/model/1_pc/2_phw/armor/13_hel/{ALIAS_BASENAME}.pac"
)
ALIAS_MODELPROPERTY_PATH = (
    f"character/modelproperty/1_pc/2_phw/armor/13_hel/{ALIAS_BASENAME}.pac_xml"
)
ALIAS_MESHPHYSICS_PATH = (
    f"character/bin__/meshphysics/1_pc/2_phw/armor/13_hel/{ALIAS_BASENAME}.hkx"
)

# cdmod 内组件与资源载荷路径。
FILE_REPLACEMENT_PATH = "files/replacements.json"
PREFAB_PAYLOAD_PATH = "assets/00000/cd_phw_00_hel_00_0151.prefab"
PAC_PAYLOAD_PATH = "assets/00001/cd_phw_00_hel_00_9129.pac"
PAC_XML_PAYLOAD_PATH = "assets/00002/cd_phw_00_hel_00_9129.pac_xml"
HKX_PAYLOAD_PATH = "assets/00003/cd_phw_00_hel_00_9129.hkx"

PACKAGE_ID = "battlefield-light-white-pointed-hat-0129"
PACKAGE_NAME = "Light of the Battlefield - White Pointed Hat 0129"
PACKAGE_VERSION = "0.1-test"
OUTPUT_FILENAME = "ZZZ - Light of the Battlefield White Pointed Hat-0.1-test.cdmod"

# “暗黑执行者板金头盔”的当前德米安女性目标资源。
DARK_EXECUTOR_HAT_PATH = "character/cd_phw_00_hel_00_0169.prefab"
DARK_EXECUTOR_MAIN_PAC = (
    b"character/model/1_pc/2_phw/armor/13_hel/cd_phw_00_hel_00_0169.pac"
)
EXPECTED_DARK_EXECUTOR_SHA256 = (
    "84a11ea7ba2c6a9c5b4a1df672c141cab995859d93ba4fc0d2aab9b7493e765f"
)


@dataclass(frozen=True)
class HatTargetPreset:
    """一个可替换为白色尖帽的德米安女性头饰目标。"""

    key: str
    prefab_path: str
    main_pac: bytes
    expected_prefab_sha256: str
    package_id: str
    package_name: str
    output_filename: str


HAT_TARGET_PRESETS = (
    HatTargetPreset(
        key="battlefield-light",
        prefab_path=BATTLEFIELD_LIGHT_HAT_PATH,
        main_pac=BATTLEFIELD_LIGHT_MAIN_PAC,
        expected_prefab_sha256=EXPECTED_TARGET_SHA256,
        package_id=PACKAGE_ID,
        package_name=PACKAGE_NAME,
        output_filename=OUTPUT_FILENAME,
    ),
    HatTargetPreset(
        key="dark-executor",
        prefab_path=DARK_EXECUTOR_HAT_PATH,
        main_pac=DARK_EXECUTOR_MAIN_PAC,
        expected_prefab_sha256=EXPECTED_DARK_EXECUTOR_SHA256,
        package_id="dark-executor-white-pointed-hat-0129",
        package_name="Executioner of Darkness Plate Helm - White Pointed Hat 0129",
        output_filename=(
            "ZZZ - Dark Executor Plate Helm White Pointed Hat-0.1-test.cdmod"
        ),
    ),
)
HAT_TARGET_PRESETS_BY_KEY = {preset.key: preset for preset in HAT_TARGET_PRESETS}


@dataclass(frozen=True)
class ResourceAudit:
    """单个来源资源的指纹与来源审计。"""

    source: str
    entry_path: str
    size: int
    sha256: str
    alias_path: str


@dataclass(frozen=True)
class WhitePointedHatBuildResult:
    """白色尖帽测试包生成结果。"""

    target_key: str
    output_path: Path
    package_sha256: str
    prefab_sha256: str
    resources: tuple[ResourceAudit, ...]


def _sha256(content: bytes) -> str:
    """计算资源 SHA-256。"""
    return hashlib.sha256(content).hexdigest()


def _read_exact_resource(
    pamt_path: Path,
    entry_path: str,
    expected_sha256: str,
) -> bytes:
    """从指定 PAMT/PAZ 组合读取并校验唯一资源。"""
    entries = parse_pamt_filtered(
        pamt_path,
        paz_dir=pamt_path.parent,
        desired_exact={entry_path},
    )
    if len(entries) != 1:
        raise ValueError(f"未唯一找到资源：{entry_path}")
    content, _entry = extract_plaintext(entries[0])
    actual_sha256 = _sha256(content)
    if actual_sha256 != expected_sha256:
        raise ValueError(
            f"资源指纹已变化：{entry_path}，实际 SHA256={actual_sha256}"
        )
    return content


def _replacement(
    target: str,
    payload: str,
    content: bytes,
    *,
    allow_new: bool,
) -> dict[str, object]:
    """构造一个固定目录 0009 的文件替换声明。"""
    return {
        "target": target,
        "pamt_dir": HAT_PAMT_DIR,
        "payload": payload,
        "sha256": _sha256(content),
        "size": len(content),
        "allow_new": allow_new,
        "allow_table_replace": False,
    }


def get_hat_target_preset(target_key: str) -> HatTargetPreset:
    """按稳定 key 获取德米安女性头饰目标。"""
    try:
        return HAT_TARGET_PRESETS_BY_KEY[target_key]
    except KeyError as exc:
        choices = ", ".join(HAT_TARGET_PRESETS_BY_KEY)
        raise ValueError(f"未知白色尖帽目标 {target_key!r}，可选：{choices}") from exc


def build_target_prefab(
    target_content: bytes,
    target_main_pac: bytes,
    alias_main_pac: bytes,
) -> bytes:
    """在目标 Prefab 内原位等长替换唯一主 PAC 路径。"""
    if len(target_main_pac) != len(alias_main_pac):
        raise ValueError("目标主 PAC 与 0129 私有别名长度不一致")
    if target_content.count(target_main_pac) != 1:
        raise ValueError("目标 Prefab 中主 PAC 路径数量异常")
    if alias_main_pac in target_content:
        raise ValueError("目标 Prefab 已经引用 0129 私有 PAC")
    patched = target_content.replace(target_main_pac, alias_main_pac, 1)
    if len(patched) != len(target_content):
        raise ValueError("0129 结构补丁意外改变目标 Prefab 长度")
    if patched.count(alias_main_pac) != 1 or target_main_pac in patched:
        raise ValueError("0129 结构补丁后的主 PAC 引用异常")
    return patched


def build_white_pointed_hat_mod(
    game_dir: Path,
    old_pamt_path: Path,
    output_path: Path,
    target_key: str = "battlefield-light",
) -> WhitePointedHatBuildResult:
    """从旧版 1.paz 回收 0129 PAC，并生成完整材质链测试包。"""
    game_dir = game_dir.resolve()
    old_pamt_path = old_pamt_path.resolve()
    output_path = output_path.resolve()
    target = get_hat_target_preset(target_key)
    current_pamt_path = game_dir / HAT_PAMT_DIR / "0.pamt"
    if not current_pamt_path.is_file():
        raise FileNotFoundError(f"缺少当前游戏 PAMT：{current_pamt_path}")
    if not old_pamt_path.is_file():
        raise FileNotFoundError(f"缺少旧版 PAMT：{old_pamt_path}")

    target_prefab = _read_exact_resource(
        current_pamt_path,
        target.prefab_path,
        target.expected_prefab_sha256,
    )
    old_pac = _read_exact_resource(
        old_pamt_path,
        SOURCE_PAC_ENTRY_PATH,
        EXPECTED_SOURCE_PAC_SHA256,
    )
    current_pac = _read_exact_resource(
        current_pamt_path,
        SOURCE_PAC_ENTRY_PATH,
        EXPECTED_SOURCE_PAC_SHA256,
    )
    if old_pac != current_pac:
        raise ValueError("旧版与当前 0129 PAC 不一致，拒绝混合材质链")
    pac_xml = _read_exact_resource(
        current_pamt_path,
        SOURCE_PAC_XML_ENTRY_PATH,
        EXPECTED_SOURCE_PAC_XML_SHA256,
    )
    hkx = _read_exact_resource(
        current_pamt_path,
        SOURCE_HKX_ENTRY_PATH,
        EXPECTED_SOURCE_HKX_SHA256,
    )

    alias_pac_bytes = ALIAS_MODEL_PAC_PATH.encode("ascii")
    if len(alias_pac_bytes) != len(target.main_pac):
        raise ValueError("0129 私有 PAC 别名长度与目标主 PAC 路径不一致")
    patched_prefab = build_target_prefab(
        target_prefab,
        target.main_pac,
        alias_pac_bytes,
    )

    resources = (
        ResourceAudit(
            source=str(old_pamt_path.parent / "1.paz"),
            entry_path=SOURCE_PAC_ENTRY_PATH,
            size=len(old_pac),
            sha256=_sha256(old_pac),
            alias_path=ALIAS_MODEL_PAC_PATH,
        ),
        ResourceAudit(
            source="current-game-material-chain",
            entry_path=SOURCE_PAC_XML_ENTRY_PATH,
            size=len(pac_xml),
            sha256=_sha256(pac_xml),
            alias_path=ALIAS_MODELPROPERTY_PATH,
        ),
        ResourceAudit(
            source="current-game-physics-chain",
            entry_path=SOURCE_HKX_ENTRY_PATH,
            size=len(hkx),
            sha256=_sha256(hkx),
            alias_path=ALIAS_MESHPHYSICS_PATH,
        ),
    )
    replacements = {
        "schema": 1,
        "files": [
            _replacement(
                target.prefab_path,
                PREFAB_PAYLOAD_PATH,
                patched_prefab,
                allow_new=False,
            ),
            _replacement(
                ALIAS_MODEL_PAC_PATH,
                PAC_PAYLOAD_PATH,
                old_pac,
                allow_new=True,
            ),
            _replacement(
                ALIAS_MODELPROPERTY_PATH,
                PAC_XML_PAYLOAD_PATH,
                pac_xml,
                allow_new=True,
            ),
            _replacement(
                ALIAS_MESHPHYSICS_PATH,
                HKX_PAYLOAD_PATH,
                hkx,
                allow_new=True,
            ),
        ],
    }
    manifest = {
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": target.package_id,
        "name": target.package_name,
        "version": PACKAGE_VERSION,
        "author": "cdmm research",
        "description": (
            "Restores the old white pointed 0129 hat on a Demian headgear while "
            "preserving the target prefab contract and the "
            "complete PAC material/physics alias chain."
        ),
        "dependencies": [],
        "source": {
            "format": "old-paz-resource-recovery-with-material-alias-chain",
            "old_pamt": str(old_pamt_path),
            "old_paz": str(old_pamt_path.parent / "1.paz"),
            "source_pac": SOURCE_PAC_ENTRY_PATH,
        },
        "components": [
            {
                "type": CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE,
                "path": FILE_REPLACEMENT_PATH,
            }
        ],
    }
    report = {
        "schema": 1,
        "target_key": target.key,
        "target_prefab": target.prefab_path,
        "target_prefab_sha256": _sha256(target_prefab),
        "patched_prefab_sha256": _sha256(patched_prefab),
        "resources": [asdict(resource) for resource in resources],
        "preserved": [
            "target-prefab-length",
            "target-component-count",
            "target-scene-object-uid",
            "target-bone-socket",
            "source-pac-exact-bytes",
            "source-material-pac-xml",
            "source-physics-hkx",
            "source-dds-paths",
        ],
        "compatibility": {
            "uses_conditional_table": False,
            "overwrites_native_0129": False,
            "mutually_exclusive_with_other_0151_hat_variants": True,
        },
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_cdmod_zip(
        output_path,
        {
            CDMOD_MANIFEST_PATH: manifest,
            FILE_REPLACEMENT_PATH: replacements,
            PREFAB_PAYLOAD_PATH: patched_prefab,
            PAC_PAYLOAD_PATH: old_pac,
            PAC_XML_PAYLOAD_PATH: pac_xml,
            HKX_PAYLOAD_PATH: hkx,
            CDMOD_REPORT_PATH: report,
        },
    )
    _verify_package(
        output_path,
        target,
        patched_prefab,
        old_pac,
        pac_xml,
        hkx,
    )
    return WhitePointedHatBuildResult(
        target_key=target.key,
        output_path=output_path,
        package_sha256=_sha256(output_path.read_bytes()),
        prefab_sha256=_sha256(patched_prefab),
        resources=resources,
    )


def _verify_package(
    output_path: Path,
    target: HatTargetPreset,
    prefab: bytes,
    pac: bytes,
    pac_xml: bytes,
    hkx: bytes,
) -> None:
    """重读 cdmod，确认四条替换及完整材质链载荷。"""
    package = load_cdmod_package(output_path)
    files = [file for patch in package.file_patches for file in patch.files]
    files_by_target = {file.target: file for file in files}
    expected = {
        target.prefab_path: (prefab, False),
        ALIAS_MODEL_PAC_PATH: (pac, True),
        ALIAS_MODELPROPERTY_PATH: (pac_xml, True),
        ALIAS_MESHPHYSICS_PATH: (hkx, True),
    }
    if set(files_by_target) != set(expected):
        raise ValueError("白色尖帽测试包替换目标集合异常")
    for target_path, (content, allow_new) in expected.items():
        file = files_by_target[target_path]
        if file.content != content or file.allow_new != allow_new:
            raise ValueError(f"白色尖帽测试包资源声明异常：{target_path}")
    alias_pac_bytes = ALIAS_MODEL_PAC_PATH.encode("ascii")
    if prefab.count(alias_pac_bytes) != 1:
        raise ValueError("白色尖帽 Prefab 未唯一引用私有 0129 PAC")
    if target.main_pac in prefab:
        raise ValueError("白色尖帽 Prefab 仍残留目标原主 PAC")


def result_to_json(result: WhitePointedHatBuildResult) -> dict[str, object]:
    """转换生成结果为 UTF-8 JSON。"""
    document = asdict(result)
    document["output_path"] = str(result.output_path)
    return document


def main() -> int:
    """解析当前游戏、旧版 PAMT 与输出路径。"""
    parser = argparse.ArgumentParser(description="生成战场之光白色尖帽 0129 测试包")
    parser.add_argument("game_dir", type=Path, help="当前 Crimson Desert 游戏根目录")
    parser.add_argument("old_pamt", type=Path, help="旧版本 0009 的 0.pamt")
    parser.add_argument("output", type=Path, help="输出 cdmod 路径")
    parser.add_argument(
        "--target",
        choices=tuple(HAT_TARGET_PRESETS_BY_KEY),
        default="battlefield-light",
        help="替换目标，默认战场之光",
    )
    args = parser.parse_args()
    result = build_white_pointed_hat_mod(
        args.game_dir,
        args.old_pamt,
        args.output,
        args.target,
    )
    print(json.dumps(result_to_json(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
