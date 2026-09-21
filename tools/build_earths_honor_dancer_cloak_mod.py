"""生成“大地荣誉皮制披风”替换为 0141 舞者披风的独立 ``.cdmod``。

原 ``Demenissian Clothing`` loose 模组直接提供了一份长度不同的目标 Prefab。
本工具以当前原版 0163_t Prefab 为基底，把内部唯一主 PAC 路径等长替换为原生
0141 披风，保留目标组件、UID、骨骼插槽和完整字节布局。

只换网格会丢背挂武器净空：背挂武器挂在 ``Spine2_B_MainWeapon_Socket`` 上，其父骨
``Bip_Weapon_Attach_In_02`` 由身体 socket 文件的
``StackEquipInfo EquipTypeName="Back"`` 声明为可被披风推开，推离量来自披风材质属性
``.pac_xml`` 的 ``_customGameData/_offsetLength``。0163 皮制披风声明 ``0.060000``，
0141 舞者披风没有任何 ``_customGameData``；网格换成 0141 后游戏按网格路径读取 0141
的 ``.pac_xml``，净空随之消失，背挂武器直接穿进披风。本工具因此在同一个包里同时
覆盖 0141 的 ``.pac_xml``，只插入一条 Back 净空，其余字节与当前原版逐字节一致。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
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

# 披风 Prefab、模型、材质和物理资源都由原版 0009 PAMT 索引。
PAMT_DIR = "0009"

# “大地荣誉皮制披风”的女性目标 Prefab 与原始主 PAC。
TARGET_PREFAB_PATH = "character/cd_phw_00_cloak_00_0163_t.prefab"
TARGET_MAIN_PAC = (
    b"character/model/1_pc/2_phw/armor/19_cloak/cd_phw_00_cloak_00_0163.pac"
)

# Demenissian Clothing 实际借用的 0141 舞者披风原生资源链。
SOURCE_PREFAB_PATH = "character/cd_phw_00_cloak_00_0141.prefab"
SOURCE_MAIN_PAC_PATH = "character/cd_phw_00_cloak_00_0141.pac"
SOURCE_PROPERTY_PATH = "character/cd_phw_00_cloak_00_0141.pac_xml"
SOURCE_PHYSICS_PATH = "character/cd_phw_00_cloak_00_0141.hkx"
SOURCE_MAIN_PAC = (
    b"character/model/1_pc/2_phw/armor/19_cloak/cd_phw_00_cloak_00_0141.pac"
)

# 背挂武器净空：0163 皮制披风在材质属性里声明把 Back 装备推离 0.060000，
# 0141 舞者披风完全没有该字段。网格换成 0141 之后游戏按网格路径读取它的
# 材质属性，净空随之消失，所以本工具必须同时覆盖 0141 的材质属性。
#
# 取值依据（2026-09-19，用原版自身数据回归，不要再按“最大外扩 + 0163 的 0.06”
# 硬推：那把 0141 估到 0.14，实机表现为武器完全悬空）：
#   1. 取全部 46 件声明了 _offsetLength 的玩家女性披风（cd_phw_*）顶点，
#      逐件算背面分带深度，对 44 件做线性回归，最好的特征（背中区域平均 z）
#      r≈+0.44、残差 rms≈0.023 米，对 0141 的预测落在 0.067~0.077；
#   2. 按背面剖面形状取最近邻（0160/0161/0146/0162/…）的取值区间是
#      0.05~0.09，中位数 0.065；
#   3. 与 0141 同系列（19_cloak 护甲批）的 14 件取值中位数 0.06。
# 三条独立证据一致指向 0.065~0.077，因此默认 0.07（比同件原版皮革披风的
# 0.06 只多 1 厘米），可用范围 0.06~0.09；明显更大的值会让背挂武器离开背部。
PROPERTY_PAYLOAD_PATH = "assets/00001/cd_phw_00_cloak_00_0141.pac_xml"
BACK_EQUIP_TYPE = "Back"
DEFAULT_BACK_CLEARANCE = 0.07

# 材质属性文件固定以这段自闭合头开始，注入只能发生在这里。
PROPERTY_BOM = b"\xef\xbb\xbf"
PROPERTY_COMMON_SELF_CLOSED = (
    PROPERTY_BOM + b'<SkinnedMeshPropertyCommon ReflectObjectXMLDataVersion="9"/>'
)
PROPERTY_COMMON_OPEN = (
    b'<SkinnedMeshPropertyCommon ReflectObjectXMLDataVersion="9">'
)
PROPERTY_ID_PATTERN = re.compile(rb'(?:IdBase|ItemID)="(\d+)"')

# 2026-09-19 从 Crimson Desert 2.02.00 原版读取的输入安全锚点。
# 0141 Prefab 在 2.02.00 由 1852 字节变为 1800 字节，其余四个资源未变。
EXPECTED_RESOURCE_SHA256 = {
    TARGET_PREFAB_PATH: (
        "c8fc5aac1c953ee8ea518f9ac3f90b07c611516fd9ac4dc17485e66fdd77de52"
    ),
    SOURCE_PREFAB_PATH: (
        "0660f311b98b7f772882adf6405744254af96f2e2f8d3f5a63636a67089b73bc"
    ),
    SOURCE_MAIN_PAC_PATH: (
        "31bdc2a0d431a7bbe862399f0cc3f9e4f174f9c3f7c4b6519451746222554b0d"
    ),
    SOURCE_PROPERTY_PATH: (
        "18f2417a7c711d7a8bdddf108e28e1680f1ee2398d289f7f0522f577aedd8778"
    ),
    SOURCE_PHYSICS_PATH: (
        "2e1a6383c2f25b8bfef0c2c213ce9133bef805e6ad569dc9715cfe0201c5d8e1"
    ),
}

# cdmod 内组件和载荷使用固定路径，保证重复构建结果可审计。
FILE_REPLACEMENT_PATH = "files/replacements.json"
PREFAB_PAYLOAD_PATH = "assets/00000/cd_phw_00_cloak_00_0163_t.prefab"

PACKAGE_ID = "earths-honor-leather-cloak-dancer-0141"
PACKAGE_NAME = "Earth's Honor Leather Cloak - Dancer Cloak 0141"
PACKAGE_VERSION = "1.1"
OUTPUT_FILENAME = "ZZZ - Earths Honor Leather Cloak to Dancer Cloak-1.1.cdmod"


@dataclass(frozen=True)
class PrefabAudit:
    """记录目标 Prefab 的原版与结构补丁审计结果。"""

    target_path: str
    vanilla_size: int
    vanilla_sha256: str
    patched_size: int
    patched_sha256: str
    changed_byte_count: int
    model_reference_count: int


@dataclass(frozen=True)
class NativeResourceAudit:
    """记录一个无需打包、由游戏原生提供的 0141 资源。"""

    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class BackClearanceAudit:
    """记录 0141 材质属性新增背挂武器净空的审计结果。"""

    target_path: str
    equip_type: str
    offset_length: float
    property_id: int
    vanilla_size: int
    vanilla_sha256: str
    patched_size: int
    patched_sha256: str
    inserted_byte_count: int


@dataclass(frozen=True)
class BuildResult:
    """独立披风包生成结果。"""

    output_path: Path
    package_sha256: str
    prefab: PrefabAudit
    back_clearance: BackClearanceAudit
    native_resources: tuple[NativeResourceAudit, ...]


def build_structural_cloak_prefab(target_content: bytes) -> bytes:
    """在目标结构内等长替换唯一主 PAC 路径。"""
    if len(TARGET_MAIN_PAC) != len(SOURCE_MAIN_PAC):
        raise ValueError("目标与 0141 披风 PAC 路径长度不一致")
    if target_content.count(TARGET_MAIN_PAC) != 1:
        raise ValueError("原版 0163_t Prefab 中主 PAC 路径数量异常")
    if SOURCE_MAIN_PAC in target_content:
        raise ValueError("目标 Prefab 已经引用 0141 舞者披风")

    patched = target_content.replace(TARGET_MAIN_PAC, SOURCE_MAIN_PAC, 1)
    if len(patched) != len(target_content):
        raise ValueError("披风结构补丁意外改变 Prefab 长度")
    if patched.count(SOURCE_MAIN_PAC) != 1 or TARGET_MAIN_PAC in patched:
        raise ValueError("披风结构补丁后的主 PAC 引用异常")
    return patched


def _first_free_property_id(content: bytes) -> int:
    """返回材质属性文件里尚未使用的对象 Id。"""
    used = {int(match) for match in PROPERTY_ID_PATTERN.findall(content)}
    for candidate in range(1, 1 << 31):
        if candidate not in used:
            return candidate
    raise ValueError("材质属性文件对象 Id 已耗尽")


def build_cloak_back_clearance_property(
    property_content: bytes, offset_length: float
) -> tuple[bytes, int]:
    """为 0141 披风材质属性写入背挂武器净空，其余字节保持不变。

    只把自闭合的 ``SkinnedMeshPropertyCommon`` 头展开为带
    ``_customGameData/StackEquipDataContainer`` 的完整节点，字段取值与游戏原生
    披风（例如 0163 的 ``_equipType="Back"``）完全同构，不做任何其它改动。
    """
    if not offset_length > 0:
        raise ValueError("背挂武器净空必须为正数")
    if b"_customGameData" in property_content:
        raise ValueError("0141 披风材质属性已经声明 _customGameData")
    if property_content.count(PROPERTY_COMMON_SELF_CLOSED) != 1:
        raise ValueError("0141 披风材质属性自闭合头部数量异常")
    if not property_content.startswith(PROPERTY_COMMON_SELF_CLOSED):
        raise ValueError("0141 披风材质属性头部结构异常")

    property_id = _first_free_property_id(property_content)
    offset_text = f"{offset_length:.6f}"
    head = b"".join(
        (
            PROPERTY_BOM,
            PROPERTY_COMMON_OPEN,
            b"\r\n\t",
            (
                f'<Vector Name="_customGameData" IdBase="{property_id}" '
                'isOverrided="true">'
            ).encode("ascii"),
            b"\r\n\t\t",
            (
                f'<StackEquipDataContainer ItemID="{property_id}" '
                f'_equipType="{BACK_EQUIP_TYPE}" _offsetLength="{offset_text}"/>'
            ).encode("ascii"),
            b"\r\n\t</Vector>\r\n",
            b"</SkinnedMeshPropertyCommon>",
        )
    )
    patched = head + property_content[len(PROPERTY_COMMON_SELF_CLOSED) :]
    if not patched.startswith(PROPERTY_BOM + PROPERTY_COMMON_OPEN):
        raise ValueError("0141 披风材质属性净空补丁丢失 BOM 或头部结构")
    if patched.replace(head, PROPERTY_COMMON_SELF_CLOSED, 1) != property_content:
        raise ValueError("0141 披风材质属性净空补丁改动了头部以外的字节")
    if patched.count(b"_customGameData") != 1:
        raise ValueError("0141 披风材质属性净空补丁数量异常")
    if patched.count(offset_text.encode("ascii")) != 1:
        raise ValueError("0141 披风材质属性净空取值异常")
    return patched, property_id


def build_dancer_cloak_mod(
    game_dir: Path,
    output_path: Path,
    back_clearance: float = DEFAULT_BACK_CLEARANCE,
) -> BuildResult:
    """从当前原版资源生成覆盖 0163_t Prefab 与 0141 材质属性的独立包。"""
    game_dir = game_dir.resolve()
    output_path = output_path.resolve()
    pamt_path = game_dir / PAMT_DIR / "0.pamt"
    if not pamt_path.is_file():
        raise FileNotFoundError(f"缺少当前游戏 PAMT：{pamt_path}")

    requested_paths = set(EXPECTED_RESOURCE_SHA256)
    entries = parse_pamt_filtered(
        pamt_path,
        paz_dir=pamt_path.parent,
        desired_exact=requested_paths,
    )
    entries_by_path = {entry.path.casefold(): entry for entry in entries}
    missing = sorted(
        path for path in requested_paths if path.casefold() not in entries_by_path
    )
    if missing:
        raise ValueError(f"当前游戏缺少披风资源：{missing}")

    resources: dict[str, bytes] = {}
    native_audits: list[NativeResourceAudit] = []
    for path, expected_sha256 in EXPECTED_RESOURCE_SHA256.items():
        content, _compression_type = extract_plaintext(entries_by_path[path.casefold()])
        actual_sha256 = hashlib.sha256(content).hexdigest()
        if actual_sha256 != expected_sha256:
            raise ValueError(
                f"当前游戏披风资源已变化，拒绝盲目生成：{path} "
                f"expected={expected_sha256} actual={actual_sha256}"
            )
        resources[path] = content
        if path not in (TARGET_PREFAB_PATH, SOURCE_PROPERTY_PATH):
            native_audits.append(
                NativeResourceAudit(
                    path=path,
                    size=len(content),
                    sha256=actual_sha256,
                )
            )

    source_prefab = resources[SOURCE_PREFAB_PATH]
    source_model_references = re.findall(
        rb"character/model/[^\x00]+?\.pac", source_prefab
    )
    if source_prefab.count(SOURCE_MAIN_PAC) != 1:
        raise ValueError("原版 0141 Prefab 中主 PAC 引用数量异常")
    if len(source_model_references) != 1:
        raise ValueError("原版 0141 Prefab 不是已验证的单模型结构")

    target = resources[TARGET_PREFAB_PATH]
    target_model_references = re.findall(rb"character/model/[^\x00]+?\.pac", target)
    if len(target_model_references) != 1:
        raise ValueError("原版 0163_t Prefab 不是已验证的单模型结构")
    patched = build_structural_cloak_prefab(target)
    patched_sha256 = hashlib.sha256(patched).hexdigest()
    audit = PrefabAudit(
        target_path=TARGET_PREFAB_PATH,
        vanilla_size=len(target),
        vanilla_sha256=hashlib.sha256(target).hexdigest(),
        patched_size=len(patched),
        patched_sha256=patched_sha256,
        changed_byte_count=sum(
            before != after for before, after in zip(target, patched, strict=True)
        ),
        model_reference_count=len(target_model_references),
    )

    property_source = resources[SOURCE_PROPERTY_PATH]
    patched_property, property_id = build_cloak_back_clearance_property(
        property_source, back_clearance
    )
    patched_property_sha256 = hashlib.sha256(patched_property).hexdigest()
    clearance = BackClearanceAudit(
        target_path=SOURCE_PROPERTY_PATH,
        equip_type=BACK_EQUIP_TYPE,
        offset_length=back_clearance,
        property_id=property_id,
        vanilla_size=len(property_source),
        vanilla_sha256=hashlib.sha256(property_source).hexdigest(),
        patched_size=len(patched_property),
        patched_sha256=patched_property_sha256,
        inserted_byte_count=len(patched_property) - len(property_source),
    )

    replacements = {
        "schema": 1,
        "files": [
            {
                "target": TARGET_PREFAB_PATH,
                "pamt_dir": PAMT_DIR,
                "payload": PREFAB_PAYLOAD_PATH,
                "sha256": patched_sha256,
                "size": len(patched),
                "allow_new": False,
                "allow_table_replace": False,
            },
            {
                "target": SOURCE_PROPERTY_PATH,
                "pamt_dir": PAMT_DIR,
                "payload": PROPERTY_PAYLOAD_PATH,
                "sha256": patched_property_sha256,
                "size": len(patched_property),
                "allow_new": False,
                "allow_table_replace": False,
            },
        ],
    }
    manifest = {
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": PACKAGE_ID,
        "name": PACKAGE_NAME,
        "version": PACKAGE_VERSION,
        "author": "Eyu94; structural extraction by cdmm",
        "description": (
            "Replaces only the Earth's Honor leather cloak 0163_t prefab with "
            "the native female Dancer cloak 0141 main PAC while preserving the "
            "complete target prefab structure, and restores the back-sheathed "
            "weapon clearance the 0141 material property never declared."
        ),
        "dependencies": [],
        "source": {
            "format": "target-prefab-same-length-pac-path-replacement",
            "game_version": "2.02.00",
            "original_mod": "Demenissian Clothing by Eyu94",
        },
        "components": [
            {
                "type": CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE,
                "path": FILE_REPLACEMENT_PATH,
                "file_count": 2,
            }
        ],
    }
    report = {
        "schema": 1,
        "mapping": {
            "target_item": "Earth's Honor Leather Cloak",
            "target_prefab": TARGET_PREFAB_PATH,
            "old_main_pac": TARGET_MAIN_PAC.decode("ascii"),
            "new_main_pac": SOURCE_MAIN_PAC.decode("ascii"),
            "source_identity": "Dancer cloak 0141",
        },
        "prefab_audit": asdict(audit),
        "back_clearance": {
            "reason": (
                "0163 皮制披风在材质属性声明 Back 装备净空 0.060000；0141 舞者披风"
                "没有该字段，只替换网格会让背挂武器穿进披风。"
            ),
            "audit": asdict(clearance),
        },
        "native_resources": [asdict(item) for item in native_audits],
        "safety": {
            "modifies_vanilla_archives": False,
            "uses_standalone_archive": False,
            "preserves_target_prefab_size": True,
            "preserves_target_component_layout": True,
            "uses_same_length_pac_path_replacement": True,
            "preserves_vanilla_back_clearance_semantics": True,
            "bundles_unrelated_demenissian_clothing_replacements": False,
        },
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_cdmod_zip(
        output_path,
        {
            CDMOD_MANIFEST_PATH: manifest,
            FILE_REPLACEMENT_PATH: replacements,
            PREFAB_PAYLOAD_PATH: patched,
            PROPERTY_PAYLOAD_PATH: patched_property,
            CDMOD_REPORT_PATH: report,
        },
    )
    _verify_package(output_path, patched, patched_property)
    return BuildResult(
        output_path=output_path,
        package_sha256=hashlib.sha256(output_path.read_bytes()).hexdigest(),
        prefab=audit,
        back_clearance=clearance,
        native_resources=tuple(native_audits),
    )


def _verify_package(
    output_path: Path,
    expected_payload: bytes,
    expected_property_payload: bytes,
) -> None:
    """用正式加载器回读，确认成品只有两个已审计的替换。"""
    package = load_cdmod_package(output_path)
    if package.dependencies or package.standalone_archives or package.resource_patches:
        raise ValueError("独立披风包只能包含无依赖的 file-replacement")
    files = [item for patch in package.file_patches for item in patch.files]
    if len(files) != 2:
        raise ValueError(f"独立披风包替换文件数量异常：{len(files)}")
    by_target = {item.target: item for item in files}
    if set(by_target) != {TARGET_PREFAB_PATH, SOURCE_PROPERTY_PATH}:
        raise ValueError("独立披风包最终目标异常")
    if any(item.pamt_dir != PAMT_DIR for item in files):
        raise ValueError("独立披风包 PAMT 目录异常")

    prefab_item = by_target[TARGET_PREFAB_PATH]
    if prefab_item.content != expected_payload:
        raise ValueError("独立披风包 Prefab 载荷回读不一致")
    if len(prefab_item.content) != 1800:
        raise ValueError("独立披风包未保持当前原版 Prefab 长度")
    if (
        prefab_item.content.count(SOURCE_MAIN_PAC) != 1
        or TARGET_MAIN_PAC in prefab_item.content
    ):
        raise ValueError("独立披风包主 PAC 路由回读异常")

    property_item = by_target[SOURCE_PROPERTY_PATH]
    if property_item.content != expected_property_payload:
        raise ValueError("独立披风包材质属性载荷回读不一致")
    if property_item.content.count(b"_customGameData") != 1:
        raise ValueError("独立披风包材质属性净空回读异常")


def _parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="生成大地荣誉舞者披风独立 .cdmod")
    parser.add_argument(
        "--game-dir", type=Path, required=True, help="Crimson Desert 根目录"
    )
    parser.add_argument("--output", type=Path, required=True, help="输出 .cdmod 路径")
    parser.add_argument(
        "--back-offset",
        type=float,
        default=DEFAULT_BACK_CLEARANCE,
        help=(
            "背挂武器推离披风的距离（米）。0163 原版为 0.06；0141 由原版披风"
            "数据回归得到 0.07，可用范围 0.06~0.09"
        ),
    )
    return parser.parse_args()


def main() -> int:
    """生成包并输出 UTF-8 JSON 审计摘要。"""
    args = _parse_args()
    result = build_dancer_cloak_mod(args.game_dir, args.output, args.back_offset)
    payload = asdict(result)
    payload["output_path"] = str(result.output_path)
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
