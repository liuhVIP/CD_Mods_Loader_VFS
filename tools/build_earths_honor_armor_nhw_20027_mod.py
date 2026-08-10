"""生成“大地荣誉皮制盔甲”替换为自然深 V Goddess 礼服的独立模组。

本工具保留大地荣誉原版 ``cd_phw_00_ub_00_0163.prefab`` 的三组件装配契约，
不再修改 ItemInfo，也不再加载或修改带 ``CD_Cloak`` 的 NHW 20027 NPC Prefab。
主上身 PAC 以原生 NHW 20027 为二进制 donor，只移植 Goddess 作者的自然深 V
顶点坐标，保留原生子网格名称、顶点记录、蒙皮权重与染色身份。原版和身材 91
使用完全自然的 Goddess 几何；身材 99 只应用裸体 91→99 位移场到四片白色前胸
衣片和一片前胸绑带，背部、长裙、腰片与腿环保持作者原始坐标和空间距离。
PAC_XML 保留 NHW 20027 原生染色通道，仅统一白色 Silk 外层并隐藏金色项链；
不再把胸背绑带改名，也不再把腿环材质参数硬套到绑带上。
三份 0163 Prefab 只把主上身收缩标签从
``Upperbody`` 等长改为 ``NoShrink_``，让真实裸体上身填充开口。0163 原下身和
附属上身网格仅在 PAC 的全部 LOD 索引层退化。
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import sys
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable
from zipfile import ZipFile

from cdmm.services.cdmod_converter import (
    CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE,
    CDMOD_FORMAT_NAME,
    CDMOD_FORMAT_VERSION,
    CDMOD_MANIFEST_PATH,
    CDMOD_PROFILED_FILE_REPLACEMENT_COMPONENT_TYPE,
    CDMOD_REPORT_PATH,
    _write_cdmod_zip,
)
from cdmm.services.cdmod_package import load_cdmod_package
from cdmm.services.json_loader import extract_plaintext
from cdmm.services.pamt_index_service import get_game_pamt_index
from cdmm.tools.build_meshless_flight_cloak_mod import (
    FlightCloakPacSpec,
    degenerate_flight_cloak_pac,
)
from cdmm.utils.path_utils import lower_game_rel_path

# 角色装备 Prefab、PAC、PAC_XML 和 HKX 均位于当前游戏 0009 分包。
PAMT_DIR = "0009"

# 完整资源替换组件在 cdmod 内的固定路径。
FILE_REPLACEMENT_PATH = "files/replacements.json"
PROFILED_FILE_REPLACEMENT_PATH = "files/profiled-replacements.json"

# Goddess 白色参考目录内只读取已审计资源，不复制其偏黄色 DDS。
GODDESS_UPPER_PAC_RELATIVE_PATH = Path(
    "character/model/1_pc/2_phw/armor/9_upperbody/cd_phw_00_ub_00_0163.pac"
)
GODDESS_UPPER_PAC_SHA256 = (
    "278786af0e444c4762545ac5b8c7d47d55e7842b98e7358e78c07375ddda5c4d"
)
GODDESS_UPPER_PROPERTY_RELATIVE_PATH = Path(
    "character/modelproperty/1_pc/2_phw/armor/9_upperbody/"
    "cd_phw_00_ub_00_0163.pac_xml"
)
GODDESS_UPPER_PROPERTY_SHA256 = (
    "1994a0272e43ee19a560ecd0074806573f643faa0ff2fa18aa73f077650d6d4f"
)
GODDESS_COLOR_TEXTURE_RELATIVE_PATH = Path(
    "character/texture/cd_phw_00_ub_00_0135_00_02_01_o.dds"
)
GODDESS_COLOR_TEXTURE_SHA256 = (
    "1547547f672fc339019269fe52714273439f7ae7a63f90a14a2194be83ff1056"
)

# 身材 91/99 压缩包及其真实载荷指纹。99 包没有更新 20027，不能直接拿它当新体型源。
BODY_SHAPE_91_ZIP_SHA256 = (
    "9ce83d2758fe731fa12a1858e18b8b29d638f42d45bf6e2c247951824ecd99c6"
)
BODY_SHAPE_99_ZIP_SHA256 = (
    "5daffcc2b982dad20ac463243a2ac5a812979605cf9a3ecf633b5e7e831beab7"
)
BODY_SHAPE_NUDE_PAC_SUFFIX = "cd_phw_00_nude_00_0001_damian.pac"
BODY_SHAPE_91_NUDE_PAC_SHA256 = (
    "84545b832bb0fd17ef7ae7cb74e6343b51ebd561c3212752b625229baf813156"
)
BODY_SHAPE_99_NUDE_PAC_SHA256 = (
    "a0c8cddc0284a7a92334447c63cbaf199400eb8ab6ea5b4d7dd76dfdc3413983"
)
BODY_SHAPE_FIT_PAC_SUFFIX = "cd_phw_00_ub_00_0163.pac"
BODY_SHAPE_91_FIT_PAC_SHA256 = (
    "7ffb5920b0230209e24a225d75f1b7ebc9d4ff17d84cf036d28d50082fdf582e"
)
BODY_SHAPE_99_FIT_PAC_SHA256 = (
    "5c756cdc81a24e11c6d323084d8b6463d04f9d0b6b4176e53c1d602f08c1e3bd"
)

# 加载器用最终裸体 PAC 作为体型探针；未命中已知指纹时使用原版体型载荷。
BODY_PROFILE_PROBE_TARGET = (
    "character/model/1_pc/2_phw/nude/cd_phw_00_nude_00_0001_damian.pac"
)
VANILLA_BODY_PROBE_SHA256 = (
    "dfc61960c941acbe34050a69ca45de3262a9f15e4ee58a688a62b6a6f0b3e46b"
)

# 两套体型包里的 20027 完全相同，只保留指纹作为拒绝旧 1.16 路线的审计证据。
BODY_SHAPE_UPPER_PAC_RELATIVE_PATH = Path(
    "0009/character/model/3_npc/2_nhw/armor_north/9_upperbody/"
    "cd_nhw_00_no_ub_00_20027.pac"
)
BODY_SHAPE_UPPER_PAC_SHA256 = (
    "df34325c9dbcfe544705e0ae58c70d1fc7a809904687a22d6039d0e226100ce7"
)

# 身材位移场只改变与身体贴合有关的三个子网格，避免腿环和项链被错误拉伸。
BODY_FITTED_GODDESS_SUBMESH_NAMES = frozenset(
    {
        "cd_phw_00_ub_0135_00_01_01",
        "cd_phw_00_ub_0135_00_02_01",
        "cd_phw_00_ub_0135_00_01_02",
    }
)
BODY_DISPLACEMENT_NEIGHBOR_COUNT = 8
BODY_DISPLACEMENT_GRID_SIZE = 0.03
BODY_DISPLACEMENT_CHANGED_THRESHOLD = 0.0001
BODY_FIT_VALIDATION_THRESHOLD = 0.001
BODY_FIT_VALIDATION_MAX_MEAN_ERROR = 0.002
# PAC 每个子网格使用 uint16 包围盒量化，回读允许最多 0.03 mm 误差。
PAC_VERTEX_ROUNDTRIP_MAX_ERROR = 0.00003

# 深 V 白色网格由多个互不相连的片组成；这里只吸附四片胸前网格，背片和长裙不动。
CHEST_FIT_COMPONENT_COUNT = 4
CHEST_FIT_VERTEX_COUNT = 372
CHEST_FIT_COMPONENT_MIN_Y = 1.12
CHEST_FIT_COMPONENT_MAX_Y = 1.58
CHEST_FIT_COMPONENT_FRONT_Z = -0.09
# 静态姿势保留 10 mm 余量，兼顾胸型贴合和骨骼动画期间的摆动空间。
CHEST_FIT_SURFACE_CLEARANCE = 0.010
CHEST_FIT_BLEND_START_Z = -0.02
CHEST_FIT_BLEND_FULL_Z = -0.07
CHEST_FIT_MAX_MOVE = 0.06
CHEST_FIT_SURFACE_CELL_SIZE = 0.035
CHEST_FIT_NEAREST_VERTEX_COUNT = 32

# 前束胸与后背带属于同一材质结构，只修复与白色衣片近乎重合的顶点。
# 运动时两套网格的蒙皮权重不同，因此可靠接触点保留 4 mm、其余点至少保留 3 mm。
HARNESS_VERTEX_COUNT = 387
HARNESS_COMPONENT_COUNT = 5
# 身材 99 只移动唯一的前胸绑带组件；其余四个组件组成肩背绑带。
HARNESS_FRONT_COMPONENT_COUNT = 1
HARNESS_FRONT_VERTEX_COUNT = 130
HARNESS_FRONT_MAX_Z = 0.0
HARNESS_FRONT_MIN_Z = -0.09
HARNESS_MIN_DRESS_CLEARANCE = 0.004
HARNESS_FALLBACK_MIN_DRESS_CLEARANCE = 0.003
HARNESS_FALLBACK_CLEARANCE_TOLERANCE = 0.00002
HARNESS_MAX_SOURCE_DRESS_DISTANCE = 0.005
HARNESS_MAX_TARGET_DRESS_DISTANCE = 0.010
HARNESS_MIN_DIRECTION_ALIGNMENT = 0.5
HARNESS_FIT_MAX_ITERATIONS = 12
HARNESS_FIT_CLEARANCE_TOLERANCE = 0.0008

# Goddess 长裙与腿环完整保留。
FOOT_BELT_SUBMESH_NAME = "cd_phw_00_foot_belt_0135_00_01_01"
LOWER_PANEL_SUBMESH_NAME = "cd_phw_00_ub_0135_00_01_02"

# PAC 网格工具仅在生成适配源时使用，成品模组不依赖 Workbench。
DEFAULT_WORKBENCH_ROOT = Path(r"T:\C++\crimson-desert-mod-workbench")
WORKBENCH_ROOT_ENV = "CDMW_ROOT"

# 大地荣誉原版女性上身主 Prefab。
TARGET_PREFAB_PATH = (
    "character/bin__/prefab/1_pc/02_phw/armor/9_upperbody/cd_phw_00_ub_00_0163.prefab"
)
TARGET_PREFAB_SHA256 = (
    "539f0b3ea9bedd64790020c43a646f3c8c9788a32acdd68e8f57543b73476345"
)

# PAC_XML 只隐藏金色项链；胸背绑带保留 NHW 20027 原版织物表面纹理。
HIDDEN_UPPER_SUBMESH_NAMES = frozenset(
    {
        "cd_phw_00_acc_0116_02_01_01",
    }
)

# 礼服白色外层子网格，以及原版白色 Silk 参考材质的关键参数。
CHEST_LINING_SUBMESH_NAME = "cd_phw_00_ub_0135_00_01_01"
WHITE_UPPER_SUBMESH_NAME = "cd_phw_00_ub_0135_00_02_01"
# 金色腿环参数只用于腰片和胸背绑带的独立金色通道转换。
# Index 0/1 的腿环参数略有差异，生成时必须逐索引读取，禁止硬编码单一色值。
GOLD_COLOR_PARAMETER_NAMES = (
    "_tintColorR",
    "_dyeingDetailLayerColorMaskR",
    "_dyeingPropertyBlend",
    "_colorBlendingFlag",
    "_dyeingGlobalOpacity",
)
NON_RED_COLOR_PARAMETER_NAMES = (
    "_tintColorG",
    "_tintColorB",
    "_dyeingDetailLayerColorMaskG",
    "_dyeingDetailLayerColorMaskB",
)
# 0163 染色表已登记且与来源名称同为 26 字节的别名，只用于白色可见外层。
DYEABLE_UPPER_SUBMESH_NAME = "cd_phw_00_sho_00_0163_edge"
DYEABLE_UPPER_SUBMESH_SOURCE_BYTES = b"CD_PHW_00_UB_0135_00_02_01"
DYEABLE_UPPER_SUBMESH_TARGET_BYTES = b"CD_PHW_00_Sho_00_0163_edge"
# 等长中性别名避开 UB 0163 染色身份与 FootBelt 联动分组，但材质仍保留
# NHW 20027 原始织物的 normal、height、detail 与 grime 纹理链。
GOLD_HARNESS_SUBMESH_NAME = "cd_phw_00_gd_0163_00_01_01"
GOLD_HARNESS_SUBMESH_SOURCE_BYTES = b"CD_PHW_00_UB_0135_00_01_01"
GOLD_HARNESS_SUBMESH_TARGET_BYTES = b"CD_PHW_00_GD_0163_00_01_01"
GOLD_HARNESS_SOURCE_OCCURRENCE_COUNT = 2
WHITE_SILK_MATERIAL_NAME = "SkinnedMeshCloth_Ver2"
WHITE_SILK_REQUIRED_VALUES = {
    "_tintColorR": "#ffe7b3ff",
    "_dyeingDetailLayerColorMaskR": "#ffffecff",
    "_dyeingPropertyBlend": "2139062038",
    "_clothCategory": "Silk",
}
NO_RENDER_MATERIAL_NAME = "SkinnedMeshNoRender"

# 游戏原生纯红通道遮罩：只让第一染色区域覆盖白色外层，不影响胸口皮肤材质。
PRIMARY_DYE_MASK_PATH = "character/texture/cd_temp_r_m.dds"
PRIMARY_DYE_MASK_SHA256 = (
    "2493c445d9f9dcb830ab4c2208c5c6d94caea9db58bb7791bfbfa3a1da6848fb"
)
PRIMARY_DYE_MASK_SIZE = 2872
PRIMARY_DYE_REQUIRED_VALUES = {
    "_colorBlendingFlag": "15",
    "_dyeingGlobalOpacity": "16777215",
}

# Dark Nun 参考包证明了“原生材质参数 + 单红通道 MA 遮罩”的可染色路线。
# 只在生成阶段校验参考指纹，不复制其 Prefab、模型或纹理到成品。
DYE_REFERENCE_ZIP_SHA256 = (
    "55969187565e00148b9eebb2dbbbd36d78ea06ae2aecf8faa52b4553b45fc4b7"
)
DYE_REFERENCE_MASK_ENTRIES = (
    "cd_phw_00_ub_00_0166_ma.dds",
    "cd_phw_00_jacket_00_0166_ma.dds",
)
DYE_REFERENCE_MASK_SHA256 = (
    "49d513955ab4ae673a27c5a00891ccc5cb41445b6047881dfc42bc3537fd967a"
)
DYE_REFERENCE_MASK_SIZE = 699192

# Goddess Dress 三份 Prefab 只作为解除身体收缩遮罩的结构证据，不整份复制。
GODDESS_PREFAB_RELATIVE_ROOT = Path(
    "character/bin__/prefab/1_pc/02_phw/armor/9_upperbody"
)
UPPERBODY_SHRINK_SEQUENCE = b"\x09\x00\x00\x00Upperbody\x07\x00\x00\x00Chest01"
NO_SHRINK_SEQUENCE = b"\x09\x00\x00\x00NoShrink_\x07\x00\x00\x00Chest01"

# 版本和文件名必须同步递增，旧版本由调用方改成 .123 禁用。
PACKAGE_VERSION = "1.31"
OUTPUT_FILENAME = "ZZZ - Earths Honor Armor to Goddess Dress - Native Dye Local Chest Fit-1.31.cdmod"


@dataclass(frozen=True)
class DirectAssetSpec:
    """一个把当前原版来源载荷写到 0163 目标路径的资源映射。"""

    target: str
    target_sha256: str
    source: str
    source_sha256: str


@dataclass(frozen=True)
class HiddenPacSpec:
    """一个保留 PAC 结构、只退化全部 LOD 三角形的目标。"""

    target: str
    source_sha256: str
    section_index_counts: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class TargetPrefabSpec:
    """一份只解除主上身身体收缩遮罩的 0163 Prefab。"""

    target: str
    target_sha256: str
    reference_sha256: str


@dataclass(frozen=True)
class ResourceAudit:
    """记录一个参与构建的当前原版资源。"""

    path: str
    size: int
    sha256: str
    role: str


@dataclass(frozen=True)
class BuildResult:
    """记录最终独立模组的生成与回读结果。"""

    output_path: Path
    package_sha256: str
    replacement_count: int
    hidden_triangle_count: int
    changed_index_count: int
    body_fitted_vertex_count: int
    fit_validation_mean_error: float
    resources: tuple[ResourceAudit, ...]


# 只把 NHW 20027 的上身资源链装入 0163 原生上身槽位。
# Prefab 仍然引用 0163 路径，因此不会携带 NPC Prefab 的 CD_Cloak 组件。
DIRECT_ASSET_SPECS = (
    DirectAssetSpec(
        target=(
            "character/model/1_pc/2_phw/armor/9_upperbody/cd_phw_00_ub_00_0163.pac"
        ),
        target_sha256=(
            "5224caa0f36d6892052237492eced6b3c0778cb32d2a5f5b2e0f20cac90af9c6"
        ),
        source=(
            "character/model/3_npc/2_nhw/armor_north/9_upperbody/"
            "cd_nhw_00_no_ub_00_20027.pac"
        ),
        source_sha256=(
            "7dedf163d7ee0d81ca3f59531e606871eda5dac994d4dd67699600a4ccf57ff1"
        ),
    ),
    DirectAssetSpec(
        target=(
            "character/modelproperty/1_pc/2_phw/armor/9_upperbody/"
            "cd_phw_00_ub_00_0163.pac_xml"
        ),
        target_sha256=(
            "397aa120f81d3af9ab8261b722a6470291e67e3ba721aab998921a6c027e8c0c"
        ),
        source=(
            "character/modelproperty/3_npc/2_nhw/armor_north/9_upperbody/"
            "cd_nhw_00_no_ub_00_20027.pac_xml"
        ),
        source_sha256=(
            "9914560195a89e5cb885691f1284a69449d2d51e7c4954dce8d55627874124d6"
        ),
    ),
    DirectAssetSpec(
        target=(
            "character/bin__/meshphysics/1_pc/2_phw/armor/9_upperbody/"
            "cd_phw_00_ub_00_0163.hkx"
        ),
        target_sha256=(
            "5e20bdc13bfefda399a7a3010eae8d77799fb97cb07fdd0aaeb7d5a67d9d5c29"
        ),
        source=(
            "character/bin__/meshphysics/3_npc/2_nhw/armor_north/9_upperbody/"
            "cd_nhw_00_no_ub_00_20027.hkx"
        ),
        source_sha256=(
            "9beaa6b91ab31572f32fe624f8246f194f899afe5837ad7a337b9647f07809c0"
        ),
    ),
)

# 当前原版三份 0163 Prefab 与 Goddess Dress 白色参考 Prefab 的精确指纹。
TARGET_PREFAB_SPECS = (
    TargetPrefabSpec(
        target=TARGET_PREFAB_PATH,
        target_sha256=TARGET_PREFAB_SHA256,
        reference_sha256=(
            "34bbf5ab503f7b2c5e73e84f6b414bb941e3aa352953d2d37c91d5a1a2917457"
        ),
    ),
    TargetPrefabSpec(
        target=(
            "character/bin__/prefab/1_pc/02_phw/armor/9_upperbody/"
            "cd_phw_00_ub_00_0163_index01.prefab"
        ),
        target_sha256=(
            "4d69633371829e770f3f6b282d3b1c680d1c227d3fc9ed9601449c3dee8f04d9"
        ),
        reference_sha256=(
            "ce9fb2590dba1ef862863b39b1f4f47edb23765dbcde951dfc19b524958e1256"
        ),
    ),
    TargetPrefabSpec(
        target=(
            "character/bin__/prefab/1_pc/02_phw/armor/9_upperbody/"
            "cd_phw_00_ub_00_0163_index02.prefab"
        ),
        target_sha256=(
            "7dbb387f9c7a62755e394312e9610cf954548f7892baa0772d3baee0ca572667"
        ),
        reference_sha256=(
            "58b5fd4008ce482d409e7c0fd92d1a097efbfc662584f1b4fbabccf0fb39d9c6"
        ),
    ),
)

# 0163 原下身和附属上身网格保留资源身份，只把所有 LOD 三角形退化。
HIDDEN_PAC_SPECS = (
    HiddenPacSpec(
        target=(
            "character/model/1_pc/2_phw/armor/10_lowerbody/cd_phw_00_lb_00_0163.pac"
        ),
        source_sha256=(
            "41b0385ba51263b769a847ef46d5321063ced7797b20eaf576565631c4330ae2"
        ),
        section_index_counts=((1, 96), (2, 567), (3, 1881), (4, 4092)),
    ),
    HiddenPacSpec(
        target=(
            "character/model/1_pc/2_phw/armor/9_upperbody/"
            "cd_phw_00_ub_00_0163_sub01.pac"
        ),
        source_sha256=(
            "f62e883682be471a61da40952309fe4bd617d123c3ef5ae106383a466a78d3b4"
        ),
        section_index_counts=((1, 69), (2, 438), (3, 2076), (4, 3813)),
    ),
)


def output_targets() -> tuple[str, ...]:
    """返回 1.31 会写入的最终游戏路径，供生成和回归测试共用。"""
    return (
        tuple(spec.target for spec in DIRECT_ASSET_SPECS)
        + tuple(spec.target for spec in TARGET_PREFAB_SPECS)
        + tuple(spec.target for spec in HIDDEN_PAC_SPECS)
    )


def _read_current_asset(game_dir: Path, target: str) -> bytes:
    """从当前 0009 精确读取资源，并拒绝跨目录 basename 误匹配。"""
    entry = get_game_pamt_index(game_dir).find_in_dir(PAMT_DIR, target)
    if entry is None:
        raise ValueError(f"当前游戏 {PAMT_DIR} 中未找到资源：{target}")
    final_path = lower_game_rel_path(
        f"{entry.resolved_dir_path}/{Path(entry.path).name}"
    )
    if final_path != lower_game_rel_path(target):
        raise ValueError(f"资源路径发生歧义：{target} -> {final_path}")
    return extract_plaintext(entry)[0]


def _verify_hash(path: str, content: bytes, expected_sha256: str) -> str:
    """校验当前游戏资源指纹，游戏更新后拒绝套用旧布局。"""
    actual_sha256 = hashlib.sha256(content).hexdigest()
    if actual_sha256 != expected_sha256:
        raise ValueError(
            f"当前游戏资源已变化，拒绝盲目生成：{path} "
            f"expected={expected_sha256} actual={actual_sha256}"
        )
    return actual_sha256


def _read_revealing_dress_prefabs(reference_dir: Path) -> dict[str, bytes]:
    """读取并校验 Goddess Dress 白色目录的三份 0163 Prefab。"""
    reference_dir = reference_dir.resolve()
    if not reference_dir.is_dir():
        raise FileNotFoundError(f"缺少 Goddess Dress 白色参考目录：{reference_dir}")
    contents: dict[str, bytes] = {}
    for spec in TARGET_PREFAB_SPECS:
        path = reference_dir / GODDESS_PREFAB_RELATIVE_ROOT / Path(spec.target).name
        if not path.is_file():
            raise FileNotFoundError(f"Goddess Dress 参考目录缺少 Prefab：{path}")
        content = path.read_bytes()
        _verify_hash(str(path), content, spec.reference_sha256)
        if content.count(NO_SHRINK_SEQUENCE) != 1:
            raise ValueError(f"Goddess Dress Prefab 缺少唯一 NoShrink 证据：{path}")
        contents[spec.target] = content
    return contents


def build_no_shrink_target_prefab(vanilla: bytes, reference: bytes) -> bytes:
    """只解除主上身身体收缩遮罩，保留当前目标 Prefab 的其余全部字节。"""
    if vanilla.count(UPPERBODY_SHRINK_SEQUENCE) != 1:
        raise ValueError("0163 Prefab 主上身 Upperbody 收缩序列不是唯一命中")
    if reference.count(NO_SHRINK_SEQUENCE) != 1:
        raise ValueError("Goddess Dress Prefab NoShrink 参考序列不是唯一命中")
    transformed = vanilla.replace(UPPERBODY_SHRINK_SEQUENCE, NO_SHRINK_SEQUENCE, 1)
    if len(transformed) != len(vanilla):
        raise ValueError("0163 Prefab NoShrink 变换意外改变文件长度")
    changed = sum(left != right for left, right in zip(vanilla, transformed))
    expected_changed = sum(
        left != right for left, right in zip(b"Upperbody", b"NoShrink_")
    )
    if changed != expected_changed:
        raise ValueError(f"0163 Prefab NoShrink 变换字节数异常：{changed}")
    return transformed


def _read_dye_reference_masks(reference_zip: Path) -> tuple[bytes, ...]:
    """校验 Dark Nun 参考包及其中两张单红通道染色遮罩。"""
    reference_zip = reference_zip.resolve()
    if not reference_zip.is_file():
        raise FileNotFoundError(f"缺少 Dark Nun 染色参考包：{reference_zip}")
    _verify_hash(
        str(reference_zip), reference_zip.read_bytes(), DYE_REFERENCE_ZIP_SHA256
    )

    contents: list[bytes] = []
    with ZipFile(reference_zip) as archive:
        for basename in DYE_REFERENCE_MASK_ENTRIES:
            matches = [
                item
                for item in archive.infolist()
                if Path(item.filename).name.casefold() == basename.casefold()
            ]
            if len(matches) != 1:
                raise ValueError(
                    f"Dark Nun 染色参考遮罩不是唯一命中：{basename} "
                    f"matches={len(matches)}"
                )
            content = archive.read(matches[0])
            if len(content) != DYE_REFERENCE_MASK_SIZE:
                raise ValueError(f"Dark Nun 染色参考遮罩长度异常：{basename}")
            _verify_hash(basename, content, DYE_REFERENCE_MASK_SHA256)
            contents.append(content)
    return tuple(contents)


def _material_parameters(material: ET.Element) -> dict[str, str]:
    """提取材质参数值，供白色 Silk 身份校验使用。"""
    return {
        element.get("_name", ""): element.get("_value", "")
        for element in material.iter()
        if element.get("_name") and element.get("_value") is not None
    }


def _material_texture_path(material: ET.Element, parameter_name: str) -> str | None:
    """读取一个材质纹理参数的规范游戏路径。"""
    matches = [
        element for element in material.iter() if element.get("_name") == parameter_name
    ]
    if len(matches) != 1:
        return None
    paths = [
        element.get("_path") for element in matches[0].iter() if element.get("_path")
    ]
    if len(paths) != 1:
        return None
    return lower_game_rel_path(paths[0])


def _set_material_texture_path(
    material: ET.Element,
    parameter_name: str,
    path: str,
) -> None:
    """修改一个现有材质纹理路径，保持 XML 节点结构不变。"""
    matches = [
        element for element in material.iter() if element.get("_name") == parameter_name
    ]
    if len(matches) != 1:
        raise ValueError(f"材质纹理参数不是唯一命中：{parameter_name}")
    path_nodes = [element for element in matches[0].iter() if element.get("_path")]
    if len(path_nodes) != 1:
        raise ValueError(f"材质纹理路径不是唯一命中：{parameter_name}")
    path_nodes[0].set("_path", path)


def _replace_material_parameter_from_reference(
    material: ET.Element,
    source_parameter_name: str,
    reference_material: ET.Element,
    reference_parameter_name: str,
    value: str,
) -> None:
    """用参考节点替换颜色参数，同时保留目标材质的其余独立结构。"""
    source_matches = [
        element
        for element in material.iter()
        if element.get("_name") == source_parameter_name
        and element.get("_value") is not None
    ]
    reference_matches = [
        element
        for element in reference_material.iter()
        if element.get("_name") == reference_parameter_name
        and element.get("_value") is not None
    ]
    if len(source_matches) != 1 or len(reference_matches) != 1:
        raise ValueError(
            "材质参数节点无法唯一替换："
            f"source={source_parameter_name} reference={reference_parameter_name}"
        )
    source = source_matches[0]
    parent = next(
        (candidate for candidate in material.iter() if source in list(candidate)),
        None,
    )
    if parent is None:
        raise ValueError(f"材质参数缺少父节点：{source_parameter_name}")
    replacement = copy.deepcopy(reference_matches[0])
    replacement.set("_value", value)
    index = list(parent).index(source)
    parent.remove(source)
    parent.insert(index, replacement)


def _remove_material_parameter(material: ET.Element, parameter_name: str) -> None:
    """移除目标材质中多余的非红通道颜色节点。"""
    matches = [
        element for element in material.iter() if element.get("_name") == parameter_name
    ]
    if len(matches) > 1:
        raise ValueError(f"材质参数重复，无法安全移除：{parameter_name}")
    if not matches:
        return
    target = matches[0]
    parent = next(
        (candidate for candidate in material.iter() if target in list(candidate)),
        None,
    )
    if parent is None:
        raise ValueError(f"材质参数缺少父节点：{parameter_name}")
    parent.remove(target)


def _apply_gold_color_identity(
    material: ET.Element,
    gold_reference: ET.Element,
) -> None:
    """只转换材质颜色通道，保留其独立表面纹理与子网格身份。"""
    reference_values = _material_parameters(gold_reference)
    missing = [
        name for name in GOLD_COLOR_PARAMETER_NAMES if name not in reference_values
    ]
    if missing:
        raise ValueError(f"腿环金色材质缺少颜色参数：{missing}")

    for parameter_name in GOLD_COLOR_PARAMETER_NAMES:
        target_names = {
            element.get("_name")
            for element in material.iter()
            if element.get("_value") is not None
        }
        source_name = parameter_name
        if parameter_name == "_tintColorR" and source_name not in target_names:
            source_name = next(
                (
                    name
                    for name in ("_tintColorG", "_tintColorB")
                    if name in target_names
                ),
                "",
            )
        if not source_name:
            raise ValueError(f"目标材质缺少可替换颜色节点：{parameter_name}")
        _replace_material_parameter_from_reference(
            material,
            source_name,
            gold_reference,
            parameter_name,
            reference_values[parameter_name],
        )

    for parameter_name in NON_RED_COLOR_PARAMETER_NAMES:
        _remove_material_parameter(material, parameter_name)
    gold_mask_path = _material_texture_path(
        gold_reference,
        "_colorBlendingMaskTexture",
    )
    if gold_mask_path is None:
        raise ValueError("腿环金色材质缺少混色遮罩")
    _set_material_texture_path(
        material,
        "_colorBlendingMaskTexture",
        gold_mask_path,
    )


def _upper_material_wrappers(model_property: ET.Element) -> dict[str, ET.Element]:
    """按子网格名建立材质包装器索引，并拒绝重复名称。"""
    wrappers: dict[str, ET.Element] = {}
    for wrapper in model_property.findall(".//SkinnedMeshMaterialWrapper"):
        name = wrapper.get("_subMeshName")
        if not name:
            raise ValueError("NHW 20027 PAC_XML 存在无名称子网格材质")
        if name in wrappers:
            raise ValueError(f"NHW 20027 PAC_XML 子网格材质重复：{name}")
        wrappers[name] = wrapper
    return wrappers


def _replace_wrapper_material(wrapper: ET.Element, material: ET.Element) -> None:
    """保持包装器结构，只替换其中唯一的 Material 节点。"""
    materials = wrapper.findall("./Material")
    if len(materials) != 1:
        raise ValueError("NHW 20027 PAC_XML 子网格 Material 数量异常")
    current = materials[0]
    index = list(wrapper).index(current)
    wrapper.remove(current)
    wrapper.insert(index, copy.deepcopy(material))


def build_white_revealing_upper_property(
    source: bytes,
) -> bytes:
    """统一白色可染外层，保留 20027 原生染色链并只隐藏项链。"""
    try:
        text = source.decode("utf-8-sig")
        root = ET.fromstring(f"<CdmmRoot>{text}</CdmmRoot>")
    except (UnicodeDecodeError, ET.ParseError) as exc:
        raise ValueError(f"NHW 20027 PAC_XML 无法解析：{exc}") from exc
    model_properties = {
        item.get("Index", ""): item
        for item in root.findall("./ModelPropertyList/ModelProperty")
    }
    if set(model_properties) != {"0", "1"}:
        raise ValueError("NHW 20027 PAC_XML 必须且只能包含 Index 0/1")
    wrappers_by_index = {
        index: _upper_material_wrappers(model_property)
        for index, model_property in model_properties.items()
    }
    required_names = HIDDEN_UPPER_SUBMESH_NAMES | {
        WHITE_UPPER_SUBMESH_NAME,
        CHEST_LINING_SUBMESH_NAME,
        LOWER_PANEL_SUBMESH_NAME,
        FOOT_BELT_SUBMESH_NAME,
    }
    for index, wrappers in wrappers_by_index.items():
        missing = sorted(required_names - wrappers.keys())
        if missing:
            raise ValueError(f"NHW 20027 PAC_XML Index {index} 缺少子网格：{missing}")

    white_reference = wrappers_by_index["1"][WHITE_UPPER_SUBMESH_NAME].find(
        "./Material"
    )
    if white_reference is None:
        raise ValueError("NHW 20027 PAC_XML 缺少白色外层参考材质")
    if white_reference.get("_materialName") != WHITE_SILK_MATERIAL_NAME:
        raise ValueError("NHW 20027 PAC_XML 白色外层不再使用 Silk 材质")
    parameters = _material_parameters(white_reference)
    mismatched = {
        name: (parameters.get(name), expected)
        for name, expected in WHITE_SILK_REQUIRED_VALUES.items()
        if parameters.get(name) != expected
    }
    if mismatched:
        raise ValueError(f"NHW 20027 白色 Silk 参考参数变化：{mismatched}")
    dye_mismatched = {
        name: (parameters.get(name), expected)
        for name, expected in PRIMARY_DYE_REQUIRED_VALUES.items()
        if parameters.get(name) != expected
    }
    if dye_mismatched:
        raise ValueError(f"NHW 20027 第一染色区域参数变化：{dye_mismatched}")
    dye_mask_path = _material_texture_path(white_reference, "_colorBlendingMaskTexture")
    if dye_mask_path != PRIMARY_DYE_MASK_PATH:
        raise ValueError(
            "NHW 20027 白色外层染色遮罩变化："
            f"expected={PRIMARY_DYE_MASK_PATH} actual={dye_mask_path}"
        )

    native_harness_materials = {
        index: ET.tostring(
            wrappers[CHEST_LINING_SUBMESH_NAME].find("./Material"),
            encoding="unicode",
        )
        for index, wrappers in wrappers_by_index.items()
    }
    for index, wrappers in wrappers_by_index.items():
        _replace_wrapper_material(wrappers[WHITE_UPPER_SUBMESH_NAME], white_reference)
        for submesh_name in HIDDEN_UPPER_SUBMESH_NAMES:
            material = wrappers[submesh_name].find("./Material")
            if material is None:
                raise ValueError(f"NHW 20027 子网格缺少材质：{submesh_name}")
            material.set("_materialName", NO_RENDER_MATERIAL_NAME)
        final_harness = wrappers[CHEST_LINING_SUBMESH_NAME].find("./Material")
        if ET.tostring(final_harness, encoding="unicode") != (
            native_harness_materials[index]
        ):
            raise ValueError(f"NHW 20027 Index {index} 原生绑带染色链意外变化")

    children = [ET.tostring(child, encoding="unicode") for child in root]
    return b"\xef\xbb\xbf" + "\r\n".join(children).encode("utf-8") + b"\r\n"


def _audit_target_prefabs(game_dir: Path) -> list[ResourceAudit]:
    """确认三份 0163 Prefab 仍是无披风的原版三组件结构。"""
    audits: list[ResourceAudit] = []
    required = (b"CD_Upperbody", b"CD_Lowerbody", b"CD_Upperbody_Acc")
    cloak_name = b"CD_Cloak"
    for spec in TARGET_PREFAB_SPECS:
        content = _read_current_asset(game_dir, spec.target)
        sha256 = _verify_hash(spec.target, content, spec.target_sha256)
        if any(
            content.count(len(name).to_bytes(4, "little") + name) != 1
            for name in required
        ):
            raise ValueError(f"0163 Prefab 三组件结构已变化：{spec.target}")
        if len(cloak_name).to_bytes(4, "little") + cloak_name in content:
            raise ValueError(f"0163 Prefab 意外包含 CD_Cloak：{spec.target}")
        audits.append(
            ResourceAudit(
                path=spec.target,
                size=len(content),
                sha256=sha256,
                role="target-prefab-before-no-shrink-transform",
            )
        )
    return audits


def _audit_primary_dye_mask(game_dir: Path) -> ResourceAudit:
    """确认白色外层引用的是当前游戏原生纯红通道遮罩。"""
    entry = get_game_pamt_index(game_dir).find_best(PRIMARY_DYE_MASK_PATH)
    if entry is None:
        raise ValueError(f"当前游戏未找到染色遮罩：{PRIMARY_DYE_MASK_PATH}")
    content = extract_plaintext(entry)[0]
    if len(content) != PRIMARY_DYE_MASK_SIZE:
        raise ValueError(f"当前游戏染色遮罩长度异常：{PRIMARY_DYE_MASK_PATH}")
    sha256 = _verify_hash(PRIMARY_DYE_MASK_PATH, content, PRIMARY_DYE_MASK_SHA256)
    return ResourceAudit(
        path=PRIMARY_DYE_MASK_PATH,
        size=len(content),
        sha256=sha256,
        role="preserved-native-primary-dye-mask",
    )


def _file_spec(target: str, payload_path: str, content: bytes) -> dict[str, object]:
    """构造一个禁止新增、禁止表覆盖的完整资源替换声明。"""
    return {
        "target": target,
        "pamt_dir": PAMT_DIR,
        "payload": payload_path,
        "sha256": hashlib.sha256(content).hexdigest(),
        "size": len(content),
        "allow_new": False,
        "allow_table_replace": False,
    }


def _verify_package(
    output_path: Path,
    expected_payloads: dict[str, bytes],
    profiled_payloads: dict[str, bytes],
) -> None:
    """回读 cdmod，确认 Goddess 上身与其余静态资源的组件结构。"""
    package = load_cdmod_package(output_path)
    if (
        package.dependencies
        or package.operations
        or package.localization_patches
        or package.legacy_json_patches
        or package.standalone_archives
    ):
        raise ValueError("成品包含未预期的表或 standalone 组件")
    if len(package.file_patches) != 1:
        raise ValueError("成品完整资源组件数量异常")
    if len(package.profiled_file_patches) != 1:
        raise ValueError("成品条件完整资源组件数量异常")
    if package.resource_patches:
        raise ValueError("成品不应包含动态资源组件")

    files = package.file_patches[0].files
    actual_targets = tuple(item.target for item in files)
    expected_file_targets = tuple(expected_payloads)
    if actual_targets != expected_file_targets:
        raise ValueError(f"成品静态目标顺序异常：{actual_targets}")
    for item in files:
        expected = expected_payloads.get(item.target)
        if item.pamt_dir != PAMT_DIR or expected is None or item.content != expected:
            raise ValueError(f"成品资源回读不一致：{item.target}")
    profiled_patch = package.profiled_file_patches[0]
    if (
        profiled_patch.probe_target != BODY_PROFILE_PROBE_TARGET
        or profiled_patch.probe_pamt_dir != PAMT_DIR
        or len(profiled_patch.files) != 1
    ):
        raise ValueError("成品体型探针或条件目标结构异常")
    profiled_file = profiled_patch.files[0]
    if profiled_file.target != DIRECT_ASSET_SPECS[0].target:
        raise ValueError("成品条件主上身目标异常")
    actual_profiled = {
        variant.profile_id: variant.content for variant in profiled_file.variants
    }
    actual_profiled[profiled_file.fallback.profile_id] = profiled_file.fallback.content
    if actual_profiled != profiled_payloads:
        raise ValueError("成品原版/91/99体型载荷回读不一致")


def _load_mesh_tooling() -> tuple[Callable[..., Any], Callable[..., bytes]]:
    """加载开发用 PAC 解析与原位回写工具。"""
    configured = os.environ.get(WORKBENCH_ROOT_ENV, "").strip()
    workbench_root = Path(configured) if configured else DEFAULT_WORKBENCH_ROOT
    parser_path = workbench_root / "cdmw" / "modding" / "mesh_parser.py"
    if not parser_path.is_file():
        raise FileNotFoundError(
            f"缺少 Crimson Desert Mod Workbench：{workbench_root}；"
            f"可通过环境变量 {WORKBENCH_ROOT_ENV} 指定目录"
        )
    root_text = str(workbench_root.resolve())
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    from cdmw.modding.mesh_importer import build_pac
    from cdmw.modding.mesh_parser import parse_pac

    return parse_pac, build_pac


def _read_zip_entry_by_suffix(
    archive_path: Path,
    archive_sha256: str,
    entry_suffix: str,
    entry_sha256: str,
) -> bytes:
    """从体型 ZIP 中按稳定英文后缀读取唯一载荷并校验双层指纹。"""
    archive_path = archive_path.resolve()
    if not archive_path.is_file():
        raise FileNotFoundError(f"缺少体型参考 ZIP：{archive_path}")
    _verify_hash(str(archive_path), archive_path.read_bytes(), archive_sha256)
    with ZipFile(archive_path) as archive:
        matches = [
            item
            for item in archive.infolist()
            if item.filename.replace("\\", "/")
            .casefold()
            .endswith(entry_suffix.casefold())
        ]
        if len(matches) != 1:
            raise ValueError(
                f"体型 ZIP 载荷不是唯一命中：{entry_suffix} matches={len(matches)}"
            )
        content = archive.read(matches[0])
    _verify_hash(f"{archive_path}!/{entry_suffix}", content, entry_sha256)
    return content


def _read_body_shape_references(
    body_shape_91_zip: Path,
    body_shape_99_zip: Path,
) -> tuple[bytes, bytes, bytes, bytes]:
    """读取身材 91/99 的裸体真值与作者 0163 服装适配真值。"""
    return (
        _read_zip_entry_by_suffix(
            body_shape_91_zip,
            BODY_SHAPE_91_ZIP_SHA256,
            BODY_SHAPE_NUDE_PAC_SUFFIX,
            BODY_SHAPE_91_NUDE_PAC_SHA256,
        ),
        _read_zip_entry_by_suffix(
            body_shape_99_zip,
            BODY_SHAPE_99_ZIP_SHA256,
            BODY_SHAPE_NUDE_PAC_SUFFIX,
            BODY_SHAPE_99_NUDE_PAC_SHA256,
        ),
        _read_zip_entry_by_suffix(
            body_shape_91_zip,
            BODY_SHAPE_91_ZIP_SHA256,
            BODY_SHAPE_FIT_PAC_SUFFIX,
            BODY_SHAPE_91_FIT_PAC_SHA256,
        ),
        _read_zip_entry_by_suffix(
            body_shape_99_zip,
            BODY_SHAPE_99_ZIP_SHA256,
            BODY_SHAPE_FIT_PAC_SUFFIX,
            BODY_SHAPE_99_FIT_PAC_SHA256,
        ),
    )


def should_hide_goddess_face(
    submesh_name: str,
    vertices: tuple[tuple[float, float, float], ...],
) -> bool:
    """1.17 不隐藏 Goddess 的任何上身 PAC 网格。"""
    del submesh_name, vertices
    return False


class _BodyDisplacementField:
    """使用均匀网格实现的裸体表面 K 近邻位移场。"""

    def __init__(
        self,
        points: list[tuple[float, float, float]],
        deltas: list[tuple[float, float, float]],
    ) -> None:
        if len(points) != len(deltas) or not points:
            raise ValueError("裸体位移场点与位移数量不一致")
        self.points = points
        self.deltas = deltas
        self.cells: dict[tuple[int, int, int], list[int]] = {}
        for index, point in enumerate(points):
            self.cells.setdefault(self._cell(point), []).append(index)

    @staticmethod
    def _cell(point: tuple[float, float, float]) -> tuple[int, int, int]:
        return tuple(math.floor(value / BODY_DISPLACEMENT_GRID_SIZE) for value in point)

    def sample(self, point: tuple[float, float, float]) -> tuple[float, float, float]:
        """对一个服装顶点返回八近邻反距离加权裸体位移。"""
        center = self._cell(point)
        candidates: set[int] = set()
        ranked: list[tuple[float, int]] = []
        for radius in range(0, 17):
            for x in range(center[0] - radius, center[0] + radius + 1):
                for y in range(center[1] - radius, center[1] + radius + 1):
                    for z in range(center[2] - radius, center[2] + radius + 1):
                        candidates.update(self.cells.get((x, y, z), ()))
            if len(candidates) < BODY_DISPLACEMENT_NEIGHBOR_COUNT:
                continue
            ranked = sorted(
                (
                    sum(
                        (point[axis] - self.points[index][axis]) ** 2
                        for axis in range(3)
                    ),
                    index,
                )
                for index in candidates
            )[:BODY_DISPLACEMENT_NEIGHBOR_COUNT]
            # 未扫描单元与查询点的最短距离不小于当前半径对应的网格距离。
            if ranked[-1][0] <= (radius * BODY_DISPLACEMENT_GRID_SIZE) ** 2:
                break
        if not ranked:
            ranked = sorted(
                (
                    sum((point[axis] - source[axis]) ** 2 for axis in range(3)),
                    index,
                )
                for index, source in enumerate(self.points)
            )[:BODY_DISPLACEMENT_NEIGHBOR_COUNT]
        if ranked[0][0] <= 1e-14:
            return self.deltas[ranked[0][1]]
        weights = [1.0 / (distance_sq + 1e-8) for distance_sq, _index in ranked]
        weight_sum = sum(weights)
        return tuple(
            sum(
                weight * self.deltas[index][axis]
                for weight, (_distance_sq, index) in zip(weights, ranked, strict=True)
            )
            / weight_sum
            for axis in range(3)
        )


def _subtract(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
) -> tuple[float, float, float]:
    """返回两个三维点或向量的逐轴差。"""
    return tuple(left[axis] - right[axis] for axis in range(3))


def _add_scaled(
    origin: tuple[float, float, float],
    vector: tuple[float, float, float],
    scale: float,
) -> tuple[float, float, float]:
    """把缩放后的三维向量加到起点。"""
    return tuple(origin[axis] + vector[axis] * scale for axis in range(3))


def _dot(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
) -> float:
    """计算三维向量点积。"""
    return sum(left[axis] * right[axis] for axis in range(3))


def _unit_direction_away_from_surface(
    vertex: tuple[float, float, float],
    surface_point: tuple[float, float, float],
    distance: float,
) -> tuple[float, float, float]:
    """返回从最近表面点指向当前顶点的单位方向。"""
    if distance <= 1e-8:
        raise ValueError("顶点与表面点重合，无法计算外侧单位方向")
    return tuple(
        (vertex[axis] - surface_point[axis]) / distance for axis in range(3)
    )


def _closest_point_on_triangle(
    point: tuple[float, float, float],
    first: tuple[float, float, float],
    second: tuple[float, float, float],
    third: tuple[float, float, float],
) -> tuple[float, float, float]:
    """按照三角形 Voronoi 区域返回表面最近点。"""
    edge_ab = _subtract(second, first)
    edge_ac = _subtract(third, first)
    from_a = _subtract(point, first)
    d1 = _dot(edge_ab, from_a)
    d2 = _dot(edge_ac, from_a)
    if d1 <= 0.0 and d2 <= 0.0:
        return first

    from_b = _subtract(point, second)
    d3 = _dot(edge_ab, from_b)
    d4 = _dot(edge_ac, from_b)
    if d3 >= 0.0 and d4 <= d3:
        return second

    vc = d1 * d4 - d3 * d2
    if vc <= 0.0 and d1 >= 0.0 and d3 <= 0.0:
        return _add_scaled(first, edge_ab, d1 / (d1 - d3))

    from_c = _subtract(point, third)
    d5 = _dot(edge_ab, from_c)
    d6 = _dot(edge_ac, from_c)
    if d6 >= 0.0 and d5 <= d6:
        return third

    vb = d5 * d2 - d1 * d6
    if vb <= 0.0 and d2 >= 0.0 and d6 <= 0.0:
        return _add_scaled(first, edge_ac, d2 / (d2 - d6))

    va = d3 * d6 - d5 * d4
    if va <= 0.0 and (d4 - d3) >= 0.0 and (d5 - d6) >= 0.0:
        edge_bc = _subtract(third, second)
        ratio = (d4 - d3) / ((d4 - d3) + (d5 - d6))
        return _add_scaled(second, edge_bc, ratio)

    denominator = 1.0 / (va + vb + vc)
    ratio_b = vb * denominator
    ratio_c = vc * denominator
    return tuple(
        first[axis] + edge_ab[axis] * ratio_b + edge_ac[axis] * ratio_c
        for axis in range(3)
    )


class _BodySurfaceProjector:
    """用裸体网格顶点空间桶缩小最近三角面查询范围。"""

    def __init__(
        self,
        vertices: list[tuple[float, float, float]],
        faces: list[tuple[int, int, int]],
    ) -> None:
        if not vertices or not faces:
            raise ValueError("裸体网格缺少顶点或三角面")
        self.vertices = vertices
        self.faces = faces
        self.cells: dict[tuple[int, int, int], list[int]] = {}
        self.incident_faces: list[list[int]] = [[] for _item in vertices]
        for vertex_index, vertex in enumerate(vertices):
            self.cells.setdefault(self._cell(vertex), []).append(vertex_index)
        for face_index, face in enumerate(faces):
            for vertex_index in face:
                self.incident_faces[vertex_index].append(face_index)

    @staticmethod
    def _cell(point: tuple[float, float, float]) -> tuple[int, int, int]:
        return tuple(
            math.floor(value / CHEST_FIT_SURFACE_CELL_SIZE) for value in point
        )

    def _candidate_faces(
        self,
        point: tuple[float, float, float],
    ) -> set[int]:
        center = self._cell(point)
        vertex_candidates: set[int] = set()
        for radius in range(17):
            for x in range(center[0] - radius, center[0] + radius + 1):
                for y in range(center[1] - radius, center[1] + radius + 1):
                    for z in range(center[2] - radius, center[2] + radius + 1):
                        vertex_candidates.update(self.cells.get((x, y, z), ()))
            if len(vertex_candidates) >= CHEST_FIT_NEAREST_VERTEX_COUNT:
                break
        if not vertex_candidates:
            raise ValueError(f"裸体表面附近没有候选顶点：{point}")
        nearest_vertices = sorted(
            vertex_candidates,
            key=lambda index: sum(
                (point[axis] - self.vertices[index][axis]) ** 2
                for axis in range(3)
            ),
        )[:CHEST_FIT_NEAREST_VERTEX_COUNT]
        return {
            face_index
            for vertex_index in nearest_vertices
            for face_index in self.incident_faces[vertex_index]
        }

    def closest_point(
        self,
        point: tuple[float, float, float],
    ) -> tuple[tuple[float, float, float], float]:
        """返回裸体三角面上的最近点及欧氏距离。"""
        best_point: tuple[float, float, float] | None = None
        best_distance_sq = math.inf
        for face_index in self._candidate_faces(point):
            face = self.faces[face_index]
            candidate = _closest_point_on_triangle(
                point,
                self.vertices[face[0]],
                self.vertices[face[1]],
                self.vertices[face[2]],
            )
            distance_sq = sum(
                (point[axis] - candidate[axis]) ** 2 for axis in range(3)
            )
            if distance_sq < best_distance_sq:
                best_point = candidate
                best_distance_sq = distance_sq
        if best_point is None:
            raise ValueError(f"裸体表面附近没有候选三角面：{point}")
        return best_point, math.sqrt(best_distance_sq)


def _connected_vertex_components(submesh: Any) -> list[list[int]]:
    """按三角面邻接关系拆分同一子网格中的独立网格片。"""
    graph: list[set[int]] = [set() for _item in submesh.vertices]
    for first, second, third in submesh.faces:
        for left, right in (
            (first, second),
            (second, third),
            (third, first),
        ):
            graph[left].add(right)
            graph[right].add(left)
    components: list[list[int]] = []
    visited: set[int] = set()
    for root in range(len(graph)):
        if root in visited:
            continue
        pending = [root]
        visited.add(root)
        component: list[int] = []
        while pending:
            vertex_index = pending.pop()
            component.append(vertex_index)
            for neighbor in graph[vertex_index]:
                if neighbor in visited:
                    continue
                visited.add(neighbor)
                pending.append(neighbor)
        components.append(component)
    return components


def _is_front_chest_component(
    vertices: list[tuple[float, float, float]],
) -> bool:
    """通过锁定 Goddess 源网格的空间边界识别四片前胸白布。"""
    return (
        min(vertex[1] for vertex in vertices) >= CHEST_FIT_COMPONENT_MIN_Y
        and max(vertex[1] for vertex in vertices) <= CHEST_FIT_COMPONENT_MAX_Y
        and min(vertex[2] for vertex in vertices) <= CHEST_FIT_COMPONENT_FRONT_Z
    )


def _is_front_harness_component(
    vertices: list[tuple[float, float, float]],
) -> bool:
    """识别绑带中唯一位于胸前、不得牵连后背的独立组件。"""
    return (
        min(vertex[2] for vertex in vertices) <= HARNESS_FRONT_MIN_Z
        and max(vertex[2] for vertex in vertices) <= HARNESS_FRONT_MAX_Z
    )


def _chest_fit_influence(vertex: tuple[float, float, float]) -> float:
    """让前胸完全贴合，并在肩颈方向平滑衰减到零。"""
    if vertex[2] >= CHEST_FIT_BLEND_START_Z:
        return 0.0
    if vertex[2] <= CHEST_FIT_BLEND_FULL_Z:
        return 1.0
    return (CHEST_FIT_BLEND_START_Z - vertex[2]) / (
        CHEST_FIT_BLEND_START_Z - CHEST_FIT_BLEND_FULL_Z
    )


def _fit_goddess_chest_to_body(
    parse_pac: Callable[..., Any],
    dress: Any,
    target_body_pac: bytes,
    profile_name: str,
) -> int:
    """贴合深 V 白布，并沿原始外侧方向保留束胸动画余量。"""
    body = parse_pac(target_body_pac, f"{profile_name}-nude-surface.pac")
    body_matches = [
        submesh
        for submesh in body.submeshes
        if submesh.name.casefold() == "cd_phw_00_nude_0001_damian"
    ]
    if len(body_matches) != 1:
        raise ValueError(f"{profile_name} 裸体表面子网格不是唯一命中")
    white_matches = [
        submesh
        for submesh in dress.submeshes
        if submesh.name.casefold() == WHITE_UPPER_SUBMESH_NAME
    ]
    if len(white_matches) != 1:
        raise ValueError(f"{profile_name} 白色胸裙子网格不是唯一命中")
    white = white_matches[0]
    components = _connected_vertex_components(white)
    chest_components = [
        component
        for component in components
        if _is_front_chest_component([white.vertices[index] for index in component])
    ]
    selected_vertex_count = sum(len(component) for component in chest_components)
    if (
        len(chest_components) != CHEST_FIT_COMPONENT_COUNT
        or selected_vertex_count != CHEST_FIT_VERTEX_COUNT
    ):
        raise ValueError(
            f"{profile_name} 胸前独立网格片结构变化："
            f"components={len(chest_components)} vertices={selected_vertex_count}"
        )

    body_submesh = body_matches[0]
    projector = _BodySurfaceProjector(body_submesh.vertices, body_submesh.faces)
    moved_count = 0
    pre_fit_white_vertices = list(white.vertices)
    fitted_vertices = list(white.vertices)
    for component in chest_components:
        for vertex_index in component:
            vertex = white.vertices[vertex_index]
            influence = _chest_fit_influence(vertex)
            if influence <= 0.0:
                continue
            surface_point, distance = projector.closest_point(vertex)
            if distance <= 1e-8:
                continue
            # 双向收敛到安全余量：过远则贴近，过近则沿已验证的外侧方向推出。
            signed_move = (
                distance - CHEST_FIT_SURFACE_CLEARANCE
            ) * influence
            if abs(signed_move) > CHEST_FIT_MAX_MOVE:
                raise ValueError(
                    f"{profile_name} 胸前顶点贴合距离异常："
                    f"index={vertex_index} move={signed_move:.6f}"
                )
            direction = _unit_direction_away_from_surface(
                vertex,
                surface_point,
                distance,
            )
            fitted_vertices[vertex_index] = tuple(
                vertex[axis] - direction[axis] * signed_move for axis in range(3)
            )
            if abs(signed_move) > BODY_DISPLACEMENT_CHANGED_THRESHOLD:
                moved_count += 1
    if moved_count == 0:
        raise ValueError(f"{profile_name} 胸前表面贴合没有移动任何顶点")
    white.vertices = fitted_vertices
    white_fit_field = _BodyDisplacementField(
        pre_fit_white_vertices,
        [
            tuple(after[axis] - before[axis] for axis in range(3))
            for before, after in zip(
                pre_fit_white_vertices,
                fitted_vertices,
                strict=True,
            )
        ],
    )

    harness_matches = [
        submesh
        for submesh in dress.submeshes
        if submesh.name.casefold() == CHEST_LINING_SUBMESH_NAME
    ]
    if len(harness_matches) != 1:
        raise ValueError(f"{profile_name} 束胸/背带子网格不是唯一命中")
    harness = harness_matches[0]
    harness_components = _connected_vertex_components(harness)
    if (
        len(harness.vertices) != HARNESS_VERTEX_COUNT
        or len(harness_components) != HARNESS_COMPONENT_COUNT
    ):
        raise ValueError(
            f"{profile_name} 束胸/背带结构变化："
            f"components={len(harness_components)} vertices={len(harness.vertices)}"
        )

    dress_projector = _BodySurfaceProjector(white.vertices, white.faces)
    source_dress_projector = _BodySurfaceProjector(
        pre_fit_white_vertices,
        white.faces,
    )
    preferred_directions: dict[int, tuple[float, float, float]] = {}
    for vertex_index, vertex in enumerate(harness.vertices):
        source_surface_point, source_distance = source_dress_projector.closest_point(
            vertex
        )
        if not (
            1e-8 < source_distance <= HARNESS_MAX_SOURCE_DRESS_DISTANCE
        ):
            continue
        preferred_directions[vertex_index] = _unit_direction_away_from_surface(
            vertex,
            source_surface_point,
            source_distance,
        )
    if not preferred_directions:
        raise ValueError(f"{profile_name} 束胸/背带没有靠近白色衣片的顶点")

    moved_harness_vertices: set[int] = set()
    harness_vertices: list[tuple[float, float, float]] = []
    for vertex_index, vertex in enumerate(harness.vertices):
        displacement = white_fit_field.sample(vertex)
        harness_vertices.append(
            tuple(vertex[axis] + displacement[axis] for axis in range(3))
        )
        if math.sqrt(sum(value * value for value in displacement)) > (
            BODY_DISPLACEMENT_CHANGED_THRESHOLD
        ):
            moved_harness_vertices.add(vertex_index)
    active_directions: dict[int, tuple[float, float, float]] = {}
    for vertex_index, direction in preferred_directions.items():
        _surface_point, current_distance = dress_projector.closest_point(
            harness_vertices[vertex_index]
        )
        if current_distance <= HARNESS_MAX_TARGET_DRESS_DISTANCE:
            active_directions[vertex_index] = direction
    if not active_directions:
        raise ValueError(f"{profile_name} 束胸/背带没有目标姿势近衣片顶点")

    for _iteration in range(HARNESS_FIT_MAX_ITERATIONS):
        iteration_moved_count = 0
        for vertex_index, direction in active_directions.items():
            vertex = harness_vertices[vertex_index]
            surface_point, current_distance = dress_projector.closest_point(vertex)
            if not (1e-8 < current_distance <= HARNESS_MAX_TARGET_DRESS_DISTANCE):
                continue
            current_direction = _unit_direction_away_from_surface(
                vertex,
                surface_point,
                current_distance,
            )
            alignment = sum(
                current_direction[axis] * direction[axis] for axis in range(3)
            )
            if alignment < HARNESS_MIN_DIRECTION_ALIGNMENT:
                continue
            signed_distance = current_distance * alignment
            if signed_distance >= HARNESS_MIN_DRESS_CLEARANCE:
                continue
            outward_move = HARNESS_MIN_DRESS_CLEARANCE - signed_distance
            if outward_move > CHEST_FIT_MAX_MOVE:
                raise ValueError(
                    f"{profile_name} 束胸/背带衣片间距异常："
                    f"index={vertex_index} move={outward_move:.6f}"
                )
            # 必须使用当前顶点的最近表面距离；旧实现误用了胸片循环遗留变量，
            # 会把束胸外推向量错误缩放到肉眼几乎不可见。
            direction = current_direction
            harness_vertices[vertex_index] = tuple(
                vertex[axis] + direction[axis] * outward_move for axis in range(3)
            )
            iteration_moved_count += 1
            if outward_move > BODY_DISPLACEMENT_CHANGED_THRESHOLD:
                moved_harness_vertices.add(vertex_index)
        if iteration_moved_count == 0:
            break

    signed_clearances: list[float] = []
    for vertex_index, direction in active_directions.items():
        vertex = harness_vertices[vertex_index]
        surface_point, current_distance = dress_projector.closest_point(vertex)
        if not (1e-8 < current_distance <= HARNESS_MAX_TARGET_DRESS_DISTANCE):
            continue
        current_direction = _unit_direction_away_from_surface(
            vertex,
            surface_point,
            current_distance,
        )
        alignment = sum(
            current_direction[axis] * direction[axis] for axis in range(3)
        )
        if alignment < HARNESS_MIN_DIRECTION_ALIGNMENT:
            continue
        signed_clearances.append(
            current_distance * alignment
        )
    if not signed_clearances:
        raise ValueError(f"{profile_name} 束胸/背带没有方向一致的近衣片顶点")
    minimum_clearance = min(signed_clearances)
    if minimum_clearance < (
        HARNESS_MIN_DRESS_CLEARANCE - HARNESS_FIT_CLEARANCE_TOLERANCE
    ):
        raise ValueError(
            f"{profile_name} 束胸/背带衣片间距修复未收敛："
            f"clearance={minimum_clearance:.6f}"
        )

    for _iteration in range(HARNESS_FIT_MAX_ITERATIONS):
        fallback_moved_count = 0
        for vertex_index, vertex in enumerate(harness_vertices):
            surface_point, distance = dress_projector.closest_point(vertex)
            if distance <= 1e-8 or distance >= HARNESS_FALLBACK_MIN_DRESS_CLEARANCE:
                continue
            outward_move = HARNESS_FALLBACK_MIN_DRESS_CLEARANCE - distance
            direction = _unit_direction_away_from_surface(
                vertex,
                surface_point,
                distance,
            )
            harness_vertices[vertex_index] = tuple(
                vertex[axis] + direction[axis] * outward_move for axis in range(3)
            )
            fallback_moved_count += 1
            if outward_move > BODY_DISPLACEMENT_CHANGED_THRESHOLD:
                moved_harness_vertices.add(vertex_index)
        if fallback_moved_count == 0:
            break
    fallback_minimum_clearance = min(
        dress_projector.closest_point(vertex)[1] for vertex in harness_vertices
    )
    if fallback_minimum_clearance < (
        HARNESS_FALLBACK_MIN_DRESS_CLEARANCE
        - HARNESS_FALLBACK_CLEARANCE_TOLERANCE
    ):
        raise ValueError(
            f"{profile_name} 束胸/背带全局兜底间距未收敛："
            f"clearance={fallback_minimum_clearance:.6f}"
        )
    if not moved_harness_vertices:
        raise ValueError(f"{profile_name} 束胸/背带没有产生任何衣片间距修复")
    harness.vertices = harness_vertices
    return moved_count + len(moved_harness_vertices)


def _matching_submesh(left: Any, right: Any, name: str) -> tuple[Any, Any]:
    """按名称读取两份 PAC 中拓扑完全一致的子网格。"""
    left_matches = [item for item in left.submeshes if item.name.casefold() == name]
    right_matches = [item for item in right.submeshes if item.name.casefold() == name]
    if len(left_matches) != 1 or len(right_matches) != 1:
        raise ValueError(f"体型 PAC 子网格不是唯一命中：{name}")
    left_submesh, right_submesh = left_matches[0], right_matches[0]
    if (
        len(left_submesh.vertices) != len(right_submesh.vertices)
        or left_submesh.faces != right_submesh.faces
    ):
        raise ValueError(f"体型 91/99 子网格拓扑不一致：{name}")
    return left_submesh, right_submesh


def _build_body_displacement_field(
    parse_pac: Callable[..., Any],
    body_91_pac: bytes,
    body_99_pac: bytes,
) -> _BodyDisplacementField:
    """从两套同拓扑裸体 PAC 构建身材 91 到 99 的表面位移场。"""
    body_91 = parse_pac(body_91_pac, "body-shape-91-nude.pac")
    body_99 = parse_pac(body_99_pac, "body-shape-99-nude.pac")
    left, right = _matching_submesh(body_91, body_99, "cd_phw_00_nude_0001_damian")
    deltas = [
        tuple(target[axis] - source[axis] for axis in range(3))
        for source, target in zip(left.vertices, right.vertices, strict=True)
    ]
    return _BodyDisplacementField(list(left.vertices), deltas)


def build_native_goddess_donor_pac(
    donor: bytes,
    goddess: bytes,
) -> bytes:
    """以原生 20027 为 donor，只移植 Goddess 顶点坐标。"""
    parse_pac, build_pac = _load_mesh_tooling()
    donor_mesh = parse_pac(donor, "native-nhw-20027-donor.pac")
    goddess_mesh = parse_pac(goddess, "goddess-natural-geometry.pac")
    if len(donor) != len(goddess) or len(donor_mesh.submeshes) != len(
        goddess_mesh.submeshes
    ):
        raise ValueError("NHW 20027 donor 与 Goddess PAC 整体结构不一致")

    working = copy.deepcopy(donor_mesh)
    for donor_submesh, goddess_submesh, target_submesh in zip(
        donor_mesh.submeshes,
        goddess_mesh.submeshes,
        working.submeshes,
        strict=True,
    ):
        if (
            donor_submesh.name != goddess_submesh.name
            or donor_submesh.faces != goddess_submesh.faces
            or len(donor_submesh.vertices) != len(goddess_submesh.vertices)
            or donor_submesh.source_vertex_stride
            != goddess_submesh.source_vertex_stride
            or donor_submesh.source_vertex_offsets
            != goddess_submesh.source_vertex_offsets
        ):
            raise ValueError(
                f"NHW 20027 donor 与 Goddess 子网格结构不一致："
                f"{donor_submesh.name}"
            )
        stride = donor_submesh.source_vertex_stride
        for donor_offset, goddess_offset in zip(
            donor_submesh.source_vertex_offsets,
            goddess_submesh.source_vertex_offsets,
            strict=True,
        ):
            if donor[donor_offset + 6 : donor_offset + stride] != goddess[
                goddess_offset + 6 : goddess_offset + stride
            ]:
                raise ValueError(
                    f"NHW 20027 donor 存在非坐标顶点记录差异："
                    f"{donor_submesh.name} offset={donor_offset}"
                )
        target_submesh.vertices = list(goddess_submesh.vertices)

    output = build_pac(working, donor)
    rebuilt = parse_pac(output, "native-goddess-donor-output.pac")
    for expected, actual in zip(
        goddess_mesh.submeshes,
        rebuilt.submeshes,
        strict=True,
    ):
        if expected.name != actual.name or expected.faces != actual.faces:
            raise ValueError(f"Goddess donor 回读拓扑不一致：{expected.name}")
        max_error = max(
            (
                math.dist(left, right)
                for left, right in zip(
                    expected.vertices,
                    actual.vertices,
                    strict=True,
                )
            ),
            default=0.0,
        )
        if max_error > PAC_VERTEX_ROUNDTRIP_MAX_ERROR:
            raise ValueError(
                f"Goddess donor 坐标回读误差异常："
                f"{expected.name} max_error={max_error:.8f}"
            )
    return output


def build_body99_front_chest_pac(
    natural: bytes,
    body_91_pac: bytes,
    body_99_pac: bytes,
) -> tuple[bytes, int, float]:
    """只把 91→99 位移应用到白布与绑带的前胸独立组件。"""
    parse_pac, build_pac = _load_mesh_tooling()
    source = parse_pac(natural, "natural-goddess-body-91.pac")
    working = copy.deepcopy(source)
    field = _build_body_displacement_field(parse_pac, body_91_pac, body_99_pac)
    selected_indices: dict[str, set[int]] = {}

    for submesh in working.submeshes:
        name = submesh.name.casefold()
        if name not in {WHITE_UPPER_SUBMESH_NAME, CHEST_LINING_SUBMESH_NAME}:
            continue
        components = _connected_vertex_components(submesh)
        if name == WHITE_UPPER_SUBMESH_NAME:
            selected = [
                component
                for component in components
                if _is_front_chest_component(
                    [submesh.vertices[index] for index in component]
                )
            ]
            expected_components = CHEST_FIT_COMPONENT_COUNT
            expected_vertices = CHEST_FIT_VERTEX_COUNT
        else:
            selected = [
                component
                for component in components
                if _is_front_harness_component(
                    [submesh.vertices[index] for index in component]
                )
            ]
            expected_components = HARNESS_FRONT_COMPONENT_COUNT
            expected_vertices = HARNESS_FRONT_VERTEX_COUNT
        selected_count = sum(len(component) for component in selected)
        if len(selected) != expected_components or selected_count != expected_vertices:
            raise ValueError(
                f"body-99 前胸组件结构变化：{name} "
                f"components={len(selected)} vertices={selected_count}"
            )
        selected_indices[name] = {
            index for component in selected for index in component
        }

    if set(selected_indices) != {
        WHITE_UPPER_SUBMESH_NAME,
        CHEST_LINING_SUBMESH_NAME,
    }:
        raise ValueError("body-99 缺少白布或绑带前胸子网格")

    moved_count = 0
    for submesh in working.submeshes:
        selected = selected_indices.get(submesh.name.casefold(), set())
        if not selected:
            continue
        fitted = list(submesh.vertices)
        for vertex_index in selected:
            vertex = submesh.vertices[vertex_index]
            displacement = field.sample(vertex)
            fitted[vertex_index] = tuple(
                vertex[axis] + displacement[axis] for axis in range(3)
            )
            if math.sqrt(sum(value * value for value in displacement)) > (
                BODY_DISPLACEMENT_CHANGED_THRESHOLD
            ):
                moved_count += 1
        submesh.vertices = fitted
    if moved_count == 0:
        raise ValueError("body-99 前胸局部适配没有移动任何顶点")

    output = build_pac(working, natural)
    rebuilt = parse_pac(output, "body-99-front-chest-output.pac")
    max_preserved_error = 0.0
    for expected, actual in zip(
        working.submeshes,
        rebuilt.submeshes,
        strict=True,
    ):
        if expected.name != actual.name or expected.faces != actual.faces:
            raise ValueError(f"body-99 前胸局部适配拓扑不一致：{expected.name}")
        selected = selected_indices.get(expected.name.casefold(), set())
        source_submesh = next(
            item for item in source.submeshes if item.name == expected.name
        )
        for vertex_index, (expected_vertex, actual_vertex) in enumerate(
            zip(expected.vertices, actual.vertices, strict=True)
        ):
            error = math.dist(expected_vertex, actual_vertex)
            if error > PAC_VERTEX_ROUNDTRIP_MAX_ERROR:
                raise ValueError(
                    f"body-99 顶点回读误差异常：{expected.name} "
                    f"index={vertex_index} error={error:.8f}"
                )
            if vertex_index not in selected:
                max_preserved_error = max(
                    max_preserved_error,
                    math.dist(
                        source_submesh.vertices[vertex_index],
                        actual_vertex,
                    ),
                )
    if max_preserved_error > PAC_VERTEX_ROUNDTRIP_MAX_ERROR:
        raise ValueError(
            "body-99 非前胸顶点发生超量变化："
            f"max_error={max_preserved_error:.8f}"
        )
    return output, moved_count, max_preserved_error


def _validate_displacement_field(
    parse_pac: Callable[..., Any],
    field: _BodyDisplacementField,
    fit_91_pac: bytes,
    fit_99_pac: bytes,
) -> float:
    """用作者真实 0163 改模验证裸体位移场，而不是凭外观猜位移量。"""
    fit_91 = parse_pac(fit_91_pac, "body-shape-91-fit-0163.pac")
    fit_99 = parse_pac(fit_99_pac, "body-shape-99-fit-0163.pac")
    errors: list[float] = []
    if len(fit_91.submeshes) != len(fit_99.submeshes):
        raise ValueError("作者 0163 身材 91/99 子网格数量不一致")
    for left, right in zip(fit_91.submeshes, fit_99.submeshes, strict=True):
        if left.name != right.name or left.faces != right.faces:
            raise ValueError(f"作者 0163 身材 91/99 拓扑不一致：{left.name}")
        for source, target in zip(left.vertices, right.vertices, strict=True):
            actual = tuple(target[axis] - source[axis] for axis in range(3))
            if (
                math.sqrt(sum(value * value for value in actual))
                <= BODY_FIT_VALIDATION_THRESHOLD
            ):
                continue
            predicted = field.sample(source)
            errors.append(
                math.sqrt(
                    sum((predicted[axis] - actual[axis]) ** 2 for axis in range(3))
                )
            )
    if not errors:
        raise ValueError("作者 0163 身材 91/99 真值中未找到显著体型适配顶点")
    mean_error = sum(errors) / len(errors)
    if mean_error > BODY_FIT_VALIDATION_MAX_MEAN_ERROR:
        raise ValueError(
            f"裸体位移场未通过作者 0163 真值验证：mean_error={mean_error:.6f}"
        )
    return mean_error


def _build_goddess_profile_pac(
    source: bytes,
    source_body_pac: bytes,
    target_body_pac: bytes,
    profile_name: str,
    validation_fit_source: bytes | None = None,
    validation_fit_target: bytes | None = None,
) -> tuple[bytes, int, int, int, float]:
    """把身材91深V基线贴合到目标裸体体型，并保留全部原拓扑。"""
    parse_pac, build_pac = _load_mesh_tooling()
    mesh_path = DIRECT_ASSET_SPECS[0].target
    original = parse_pac(source, mesh_path)
    required_names = {
        FOOT_BELT_SUBMESH_NAME,
        WHITE_UPPER_SUBMESH_NAME,
        LOWER_PANEL_SUBMESH_NAME,
        CHEST_LINING_SUBMESH_NAME,
    }
    actual_names = {submesh.name.casefold() for submesh in original.submeshes}
    if not required_names.issubset(actual_names):
        raise ValueError(
            f"Goddess 深 V PAC 缺少必要子网格：{sorted(required_names - actual_names)}"
        )
    field = _build_body_displacement_field(parse_pac, source_body_pac, target_body_pac)
    validation_mean_error = 0.0
    if (validation_fit_source is None) != (validation_fit_target is None):
        raise ValueError("体型真值验证必须同时提供来源和目标服装 PAC")
    if validation_fit_source is not None and validation_fit_target is not None:
        validation_mean_error = _validate_displacement_field(
            parse_pac,
            field,
            validation_fit_source,
            validation_fit_target,
        )
    working = copy.deepcopy(original)
    body_fitted_vertex_count = 0
    for submesh in working.submeshes:
        if submesh.name.casefold() not in BODY_FITTED_GODDESS_SUBMESH_NAMES:
            continue
        fitted_vertices = []
        for vertex in submesh.vertices:
            displacement = field.sample(vertex)
            if math.sqrt(sum(value * value for value in displacement)) > (
                BODY_DISPLACEMENT_CHANGED_THRESHOLD
            ):
                body_fitted_vertex_count += 1
            fitted_vertices.append(
                tuple(vertex[axis] + displacement[axis] for axis in range(3))
            )
        submesh.vertices = fitted_vertices
    chest_fitted_vertex_count = _fit_goddess_chest_to_body(
        parse_pac,
        working,
        target_body_pac,
        profile_name,
    )
    rebuilt_geometry = build_pac(working, source)
    if rebuilt_geometry.count(DYEABLE_UPPER_SUBMESH_SOURCE_BYTES) != 1:
        raise ValueError("Goddess 深 V PAC 可染子网格原名不是唯一命中")
    if (
        rebuilt_geometry.count(GOLD_HARNESS_SUBMESH_SOURCE_BYTES)
        != GOLD_HARNESS_SOURCE_OCCURRENCE_COUNT
    ):
        raise ValueError("Goddess 深 V PAC 束胸子网格原名出现次数异常")
    output = rebuilt_geometry.replace(
        DYEABLE_UPPER_SUBMESH_SOURCE_BYTES,
        DYEABLE_UPPER_SUBMESH_TARGET_BYTES,
        1,
    )
    output = output.replace(
        GOLD_HARNESS_SUBMESH_SOURCE_BYTES,
        GOLD_HARNESS_SUBMESH_TARGET_BYTES,
    )
    if (
        output.count(GOLD_HARNESS_SUBMESH_SOURCE_BYTES) != 0
        or output.count(GOLD_HARNESS_SUBMESH_TARGET_BYTES)
        != GOLD_HARNESS_SOURCE_OCCURRENCE_COUNT
    ):
        raise ValueError("Goddess 深 V PAC 束胸中性金色身份替换不完整")
    rebuilt = parse_pac(output, mesh_path)
    rebuilt_by_name = {
        submesh.name.casefold(): submesh for submesh in rebuilt.submeshes
    }
    for expected_submesh in working.submeshes:
        if expected_submesh.name.casefold() == WHITE_UPPER_SUBMESH_NAME:
            expected_name = DYEABLE_UPPER_SUBMESH_NAME
        elif expected_submesh.name.casefold() == CHEST_LINING_SUBMESH_NAME:
            expected_name = GOLD_HARNESS_SUBMESH_NAME
        else:
            expected_name = expected_submesh.name
        actual_submesh = rebuilt_by_name.get(expected_name.casefold())
        if (
            actual_submesh is None
            or len(expected_submesh.vertices) != len(actual_submesh.vertices)
            or expected_submesh.faces != actual_submesh.faces
        ):
            raise ValueError(f"{profile_name} 深 V PAC 拓扑回读不一致：{expected_name}")
        max_vertex_error = max(
            (
                math.dist(expected, actual)
                for expected, actual in zip(
                    expected_submesh.vertices,
                    actual_submesh.vertices,
                    strict=True,
                )
            ),
            default=0.0,
        )
        if max_vertex_error > PAC_VERTEX_ROUNDTRIP_MAX_ERROR:
            raise ValueError(
                f"{profile_name} 深 V PAC 顶点量化回读误差异常："
                f"{expected_name} max_error={max_vertex_error:.8f}"
            )
    if source_body_pac != target_body_pac and body_fitted_vertex_count == 0:
        raise ValueError(f"{profile_name} 深 V PAC 没有产生任何有效顶点位移")
    return (
        output,
        0,
        0,
        body_fitted_vertex_count + chest_fitted_vertex_count,
        validation_mean_error,
    )


def build_goddess_upper_only_pac(
    source: bytes,
    body_91_pac: bytes,
    body_99_pac: bytes,
    fit_91_pac: bytes,
    fit_99_pac: bytes,
) -> tuple[bytes, int, int, int, float]:
    """生成经过作者0163真值验证并贴合胸型的身材99深V网格。"""
    return _build_goddess_profile_pac(
        source,
        body_91_pac,
        body_99_pac,
        "body-99",
        fit_91_pac,
        fit_99_pac,
    )


def build_natural_goddess_pac(source: bytes) -> bytes:
    """只做两个等长子网格别名替换，完整保留 Goddess 原始几何字节。"""
    if source.count(DYEABLE_UPPER_SUBMESH_SOURCE_BYTES) != 1:
        raise ValueError("Goddess 自然 PAC 可染子网格原名不是唯一命中")
    if (
        source.count(GOLD_HARNESS_SUBMESH_SOURCE_BYTES)
        != GOLD_HARNESS_SOURCE_OCCURRENCE_COUNT
    ):
        raise ValueError("Goddess 自然 PAC 束胸子网格原名出现次数异常")

    output = source.replace(
        DYEABLE_UPPER_SUBMESH_SOURCE_BYTES,
        DYEABLE_UPPER_SUBMESH_TARGET_BYTES,
        1,
    ).replace(
        GOLD_HARNESS_SUBMESH_SOURCE_BYTES,
        GOLD_HARNESS_SUBMESH_TARGET_BYTES,
    )
    if len(output) != len(source):
        raise ValueError("Goddess 自然 PAC 等长别名替换改变了文件长度")
    if output.count(DYEABLE_UPPER_SUBMESH_TARGET_BYTES) != 1:
        raise ValueError("Goddess 自然 PAC 可染子网格别名替换不完整")
    if (
        output.count(GOLD_HARNESS_SUBMESH_SOURCE_BYTES) != 0
        or output.count(GOLD_HARNESS_SUBMESH_TARGET_BYTES)
        != GOLD_HARNESS_SOURCE_OCCURRENCE_COUNT
    ):
        raise ValueError("Goddess 自然 PAC 束胸中性金色身份替换不完整")
    return output


def build_goddess_body_91_pac(
    source: bytes,
    body_91_pac: bytes,
) -> tuple[bytes, int]:
    """以1.14身材91深V网格为基线并收紧胸前轮廓。"""
    output, _hidden, _indices, moved, _error = _build_goddess_profile_pac(
        source,
        body_91_pac,
        body_91_pac,
        "body-91",
    )
    return output, moved


def build_goddess_vanilla_body_pac(
    source: bytes,
    body_91_pac: bytes,
    vanilla_body_pac: bytes,
) -> tuple[bytes, int]:
    """把身材91深V网格贴合到当前游戏原版裸体体型。"""
    output, _hidden, _indices, moved, _error = _build_goddess_profile_pac(
        source,
        body_91_pac,
        vanilla_body_pac,
        "vanilla",
    )
    return output, moved


def build_mod(
    game_dir: Path,
    output_path: Path,
    dress_reference_dir: Path,
    dye_reference_zip: Path,
    body_shape_91_zip: Path,
    body_shape_99_zip: Path,
) -> BuildResult:
    """生成三种体型均使用 Goddess 原始自然几何的版本。"""
    game_dir = game_dir.resolve()
    output_path = output_path.resolve()
    dress_reference_dir = dress_reference_dir.resolve()
    dye_reference_zip = dye_reference_zip.resolve()
    body_shape_91_zip = body_shape_91_zip.resolve()
    body_shape_99_zip = body_shape_99_zip.resolve()
    resources = _audit_target_prefabs(game_dir)
    resources.append(_audit_primary_dye_mask(game_dir))
    revealing_dress_prefabs = _read_revealing_dress_prefabs(dress_reference_dir)
    dye_reference_masks = _read_dye_reference_masks(dye_reference_zip)
    resources.append(
        ResourceAudit(
            str(dye_reference_zip),
            dye_reference_zip.stat().st_size,
            DYE_REFERENCE_ZIP_SHA256,
            "dark-nun-dye-reference-zip",
        )
    )
    resources.extend(
        ResourceAudit(
            basename,
            len(content),
            DYE_REFERENCE_MASK_SHA256,
            "dark-nun-single-red-channel-dye-mask",
        )
        for basename, content in zip(
            DYE_REFERENCE_MASK_ENTRIES, dye_reference_masks, strict=True
        )
    )
    documents: dict[str, dict[str, object] | bytes] = {}
    file_specs: list[dict[str, object]] = []
    expected_payloads: dict[str, bytes] = {}
    profiled_payloads: dict[str, bytes] = {}
    source_hashes: dict[str, str] = {
        PRIMARY_DYE_MASK_PATH: PRIMARY_DYE_MASK_SHA256,
        "reference-zip://dark-nun-outfit.zip": DYE_REFERENCE_ZIP_SHA256,
        "reference-mask://dark-nun-uniform-red.dds": DYE_REFERENCE_MASK_SHA256,
        "body-shape://91.zip": BODY_SHAPE_91_ZIP_SHA256,
        "body-shape://99.zip": BODY_SHAPE_99_ZIP_SHA256,
        "body-shape://91-nude.pac": BODY_SHAPE_91_NUDE_PAC_SHA256,
        "body-shape://99-nude.pac": BODY_SHAPE_99_NUDE_PAC_SHA256,
        "body-shape://91-fit-0163.pac": BODY_SHAPE_91_FIT_PAC_SHA256,
        "body-shape://99-fit-0163.pac": BODY_SHAPE_99_FIT_PAC_SHA256,
        "game-vanilla://phw-nude.pac": VANILLA_BODY_PROBE_SHA256,
    }
    hidden_triangle_count = 0
    changed_index_count = 0
    body_91_pac, body_99_pac, fit_91_pac, fit_99_pac = _read_body_shape_references(
        body_shape_91_zip, body_shape_99_zip
    )
    vanilla_body_pac = _read_current_asset(game_dir, BODY_PROFILE_PROBE_TARGET)
    _verify_hash(
        BODY_PROFILE_PROBE_TARGET,
        vanilla_body_pac,
        VANILLA_BODY_PROBE_SHA256,
    )
    goddess_pac_path = dress_reference_dir / GODDESS_UPPER_PAC_RELATIVE_PATH
    if not goddess_pac_path.is_file():
        raise FileNotFoundError(f"Goddess 参考目录缺少主上身 PAC：{goddess_pac_path}")
    goddess_pac = goddess_pac_path.read_bytes()
    _verify_hash(str(goddess_pac_path), goddess_pac, GODDESS_UPPER_PAC_SHA256)
    goddess_property_path = (
        dress_reference_dir / GODDESS_UPPER_PROPERTY_RELATIVE_PATH
    )
    if not goddess_property_path.is_file():
        raise FileNotFoundError(
            f"Goddess 参考目录缺少原版材质：{goddess_property_path}"
        )
    goddess_property = goddess_property_path.read_bytes()
    _verify_hash(
        str(goddess_property_path),
        goddess_property,
        GODDESS_UPPER_PROPERTY_SHA256,
    )
    goddess_color_path = dress_reference_dir / GODDESS_COLOR_TEXTURE_RELATIVE_PATH
    if not goddess_color_path.is_file():
        raise FileNotFoundError(f"Goddess 参考目录缺少颜色纹理：{goddess_color_path}")
    goddess_color = goddess_color_path.read_bytes()
    _verify_hash(
        str(goddess_color_path),
        goddess_color,
        GODDESS_COLOR_TEXTURE_SHA256,
    )
    main_upper_spec = DIRECT_ASSET_SPECS[0]
    target_content = _read_current_asset(game_dir, main_upper_spec.target)
    target_hash = _verify_hash(
        main_upper_spec.target,
        target_content,
        main_upper_spec.target_sha256,
    )
    native_upper = _read_current_asset(game_dir, main_upper_spec.source)
    native_upper_hash = _verify_hash(
        main_upper_spec.source,
        native_upper,
        main_upper_spec.source_sha256,
    )
    natural_upper = build_native_goddess_donor_pac(native_upper, goddess_pac)
    body_99_upper, body_99_fitted_vertex_count, max_preserved_back_error = (
        build_body99_front_chest_pac(
            natural_upper,
            body_91_pac,
            body_99_pac,
        )
    )
    body_91_upper = natural_upper
    vanilla_upper = natural_upper
    goddess_hidden_triangles = 0
    goddess_changed_indices = 0
    body_fitted_vertex_count = body_99_fitted_vertex_count
    body_91_fitted_vertex_count = 0
    vanilla_fitted_vertex_count = 0
    fit_validation_mean_error = 0.0
    profile_payload_paths = {
        "vanilla": "assets/00000/vanilla/cd_phw_00_ub_00_0163.pac",
        "body-91": "assets/00000/body-91/cd_phw_00_ub_00_0163.pac",
        "body-99": "assets/00000/body-99/cd_phw_00_ub_00_0163.pac",
    }
    profiled_payloads.update(
        {
            "vanilla": vanilla_upper,
            "body-91": body_91_upper,
            "body-99": body_99_upper,
        }
    )
    for profile_id, content in profiled_payloads.items():
        documents[profile_payload_paths[profile_id]] = content
    profiled_document = {
        "schema": 1,
        "probe": {
            "target": BODY_PROFILE_PROBE_TARGET,
            "pamt_dir": PAMT_DIR,
        },
        "profiles": [
            {
                "id": "body-91",
                "probe_sha256": BODY_SHAPE_91_NUDE_PAC_SHA256,
            },
            {
                "id": "body-99",
                "probe_sha256": BODY_SHAPE_99_NUDE_PAC_SHA256,
            },
        ],
        "files": [
            {
                "target": main_upper_spec.target,
                "pamt_dir": PAMT_DIR,
                "variants": [
                    {
                        "profile": profile_id,
                        "payload": profile_payload_paths[profile_id],
                        "sha256": hashlib.sha256(
                            profiled_payloads[profile_id]
                        ).hexdigest(),
                    }
                    for profile_id in ("body-91", "body-99")
                ],
                "fallback": {
                    "profile": "vanilla",
                    "payload": profile_payload_paths["vanilla"],
                    "sha256": hashlib.sha256(vanilla_upper).hexdigest(),
                },
            }
        ],
    }
    source_hashes[main_upper_spec.target] = target_hash
    source_hashes[main_upper_spec.source] = native_upper_hash
    source_hashes["reference-dir://goddess-upper.pac"] = GODDESS_UPPER_PAC_SHA256
    source_hashes["reference-dir://goddess-upper.pac_xml"] = (
        GODDESS_UPPER_PROPERTY_SHA256
    )
    source_hashes["excluded-reference://goddess-yellow-overlay.dds"] = (
        GODDESS_COLOR_TEXTURE_SHA256
    )
    resources.extend(
        (
            ResourceAudit(
                str(goddess_pac_path),
                len(goddess_pac),
                GODDESS_UPPER_PAC_SHA256,
                "natural-goddess-geometry-source",
            ),
            ResourceAudit(
                main_upper_spec.source,
                len(native_upper),
                native_upper_hash,
                "native-nhw-20027-binary-donor",
            ),
            ResourceAudit(
                str(goddess_property_path),
                len(goddess_property),
                GODDESS_UPPER_PROPERTY_SHA256,
                "original-goddess-property-structure-baseline",
            ),
            ResourceAudit(
                str(goddess_color_path),
                len(goddess_color),
                GODDESS_COLOR_TEXTURE_SHA256,
                "excluded-yellow-overlay-texture",
            ),
            ResourceAudit(
                str(body_shape_91_zip),
                body_shape_91_zip.stat().st_size,
                BODY_SHAPE_91_ZIP_SHA256,
                "body-shape-91-reference-zip",
            ),
            ResourceAudit(
                str(body_shape_99_zip),
                body_shape_99_zip.stat().st_size,
                BODY_SHAPE_99_ZIP_SHA256,
                "body-shape-99-reference-zip",
            ),
            ResourceAudit(
                BODY_SHAPE_NUDE_PAC_SUFFIX,
                len(body_91_pac),
                BODY_SHAPE_91_NUDE_PAC_SHA256,
                "body-shape-91-nude-source",
            ),
            ResourceAudit(
                BODY_SHAPE_NUDE_PAC_SUFFIX,
                len(body_99_pac),
                BODY_SHAPE_99_NUDE_PAC_SHA256,
                "body-shape-99-nude-target",
            ),
            ResourceAudit(
                BODY_PROFILE_PROBE_TARGET,
                len(vanilla_body_pac),
                VANILLA_BODY_PROBE_SHA256,
                "vanilla-body-fallback-target",
            ),
            ResourceAudit(
                BODY_SHAPE_FIT_PAC_SUFFIX,
                len(fit_91_pac),
                BODY_SHAPE_91_FIT_PAC_SHA256,
                "unused-body-shape-91-historical-audit",
            ),
            ResourceAudit(
                BODY_SHAPE_FIT_PAC_SUFFIX,
                len(fit_99_pac),
                BODY_SHAPE_99_FIT_PAC_SHA256,
                "unused-body-shape-99-historical-audit",
            ),
        )
    )
    hidden_triangle_count += goddess_hidden_triangles
    changed_index_count += goddess_changed_indices

    for index, spec in enumerate(DIRECT_ASSET_SPECS[1:], start=1):
        target_content = _read_current_asset(game_dir, spec.target)
        target_hash = _verify_hash(spec.target, target_content, spec.target_sha256)
        vanilla_source_content = _read_current_asset(game_dir, spec.source)
        source_hash = _verify_hash(
            spec.source, vanilla_source_content, spec.source_sha256
        )
        source_content = vanilla_source_content
        if spec.target.endswith(".pac_xml"):
            source_content = build_white_revealing_upper_property(source_content)
        resources.extend(
            (
                ResourceAudit(spec.target, len(target_content), target_hash, "target"),
                ResourceAudit(
                    spec.source,
                    len(vanilla_source_content),
                    source_hash,
                    "vanilla-upper-source",
                ),
                ResourceAudit(
                    spec.source,
                    len(source_content),
                    hashlib.sha256(source_content).hexdigest(),
                    "upper-source",
                ),
            )
        )
        payload_path = f"assets/{index:05d}/{Path(spec.target).name}"
        documents[payload_path] = source_content
        file_specs.append(_file_spec(spec.target, payload_path, source_content))
        expected_payloads[spec.target] = source_content
        source_hashes[spec.target] = target_hash
        source_hashes[spec.source] = source_hash

    payload_index = len(DIRECT_ASSET_SPECS)
    for spec in TARGET_PREFAB_SPECS:
        source_content = _read_current_asset(game_dir, spec.target)
        source_hash = _verify_hash(spec.target, source_content, spec.target_sha256)
        reference_content = revealing_dress_prefabs[spec.target]
        transformed = build_no_shrink_target_prefab(source_content, reference_content)
        payload_path = f"assets/{payload_index:05d}/{Path(spec.target).name}"
        payload_index += 1
        documents[payload_path] = transformed
        file_specs.append(_file_spec(spec.target, payload_path, transformed))
        expected_payloads[spec.target] = transformed
        source_hashes[spec.target] = source_hash
        source_hashes[f"reference-prefab://{Path(spec.target).name}"] = (
            spec.reference_sha256
        )
        resources.append(
            ResourceAudit(
                spec.target,
                len(transformed),
                hashlib.sha256(transformed).hexdigest(),
                "no-shrink-target-prefab",
            )
        )

    for spec in HIDDEN_PAC_SPECS:
        source_content = _read_current_asset(game_dir, spec.target)
        source_hash = _verify_hash(spec.target, source_content, spec.source_sha256)
        mesh_spec = FlightCloakPacSpec(
            target=spec.target,
            source_sha256=spec.source_sha256,
            section_index_counts=spec.section_index_counts,
        )
        transformed = degenerate_flight_cloak_pac(source_content, mesh_spec)
        resources.append(
            ResourceAudit(
                spec.target, len(source_content), source_hash, "hidden-target"
            )
        )
        payload_path = f"assets/{payload_index:05d}/{Path(spec.target).name}"
        payload_index += 1
        documents[payload_path] = transformed.content
        file_specs.append(_file_spec(spec.target, payload_path, transformed.content))
        expected_payloads[spec.target] = transformed.content
        source_hashes[spec.target] = source_hash
        hidden_triangle_count += transformed.triangle_count
        changed_index_count += transformed.changed_index_count

    manifest_document = {
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": "cdmm-earths-honor-goddess-dress-dyeable-straps",
        "name": "Earth's Honor Armor - Goddess Dress - Natural Deep V",
        "version": PACKAGE_VERSION,
        "author": "cdmm",
        "description": (
            "Preserves the native 0163 prefab structures while changing only each "
            "main upper shrink tag from Upperbody to NoShrink_, uses the native NHW "
            "20027 PAC as the binary and dye-identity donor, transplants the Goddess "
            "natural deep-V coordinates, keeps vanilla/body-91 geometry natural, "
            "applies the 91-to-99 body displacement only to the front chest cloth and "
            "front harness components, preserves all back geometry, keeps native dye "
            "channels, hides only the necklace, preserves the full skirt and leg belt, "
            "and degenerates only the original 0163 lower/accessory meshes."
        ),
        "dependencies": [],
        "source": {
            "format": "profiled-natural-deep-v-full-skirt-file-replacement",
            "files": source_hashes,
        },
        "components": [
            {
                "type": CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE,
                "path": FILE_REPLACEMENT_PATH,
                "file_count": len(file_specs),
            },
            {
                "type": CDMOD_PROFILED_FILE_REPLACEMENT_COMPONENT_TYPE,
                "path": PROFILED_FILE_REPLACEMENT_PATH,
                "file_count": 1,
                "profile_count": 3,
            },
        ],
    }
    report_document = {
        "schema": 1,
        "summary": {
            "replacement_count": len(file_specs) + 1,
            "static_replacement_count": len(file_specs),
            "profiled_replacement_count": 1,
            "body_profile_count": 3,
            "direct_upper_asset_count": len(DIRECT_ASSET_SPECS),
            "hidden_pac_count": len(HIDDEN_PAC_SPECS),
            "no_shrink_prefab_count": len(TARGET_PREFAB_SPECS),
            "natural_goddess_upper_pac_count": 2,
            "body_99_local_front_fit_pac_count": 1,
            "chest_surface_component_count_per_profile": (
                CHEST_FIT_COMPONENT_COUNT + HARNESS_FRONT_COMPONENT_COUNT
            ),
            "chest_surface_vertex_count_per_profile": (
                CHEST_FIT_VERTEX_COUNT + HARNESS_FRONT_VERTEX_COUNT
            ),
            "chest_surface_clearance_meters": 0.0,
            "body_fitted_vertex_count": body_fitted_vertex_count,
            "body_91_fitted_vertex_count": body_91_fitted_vertex_count,
            "vanilla_fitted_vertex_count": vanilla_fitted_vertex_count,
            "fit_validation_mean_error": fit_validation_mean_error,
            "max_non_front_geometry_roundtrip_error": max_preserved_back_error,
            "dyeable_submesh_rename_count": 0,
            "native_harness_material_count": 2,
            "goddess_foot_belt_hidden_triangle_count": 0,
            "goddess_foot_belt_preserved_face_count": 1024,
            "white_silk_material_count": 2,
            "primary_dye_material_count": 2,
            "packaged_dye_texture_count": 0,
            "hidden_upper_material_count": len(HIDDEN_UPPER_SUBMESH_NAMES) * 2,
            "author_fitted_geometry_source_count": 0,
            "manual_vertex_displacement_count": body_fitted_vertex_count,
            "hidden_triangle_count": hidden_triangle_count,
            "changed_index_count": changed_index_count,
            "prefab_target_count": len(TARGET_PREFAB_SPECS),
            "iteminfo_target_count": 0,
        },
        "preserved": [
            "0163 component count and UIDs",
            "0163 equipment slot identities",
            "0163 Prefab PAC paths and byte lengths",
            "external cloak assembly path",
            "ItemInfo prefab_data_list",
            "Goddess source deep-V back geometry and natural spacing",
            "zero chest-surface projection and zero harness clearance projection",
            "identical natural PAC geometry for vanilla and body-91 profiles",
            "body-99 displacement limited to five disconnected front components",
            "Goddess full-length skirt and foot-belt geometry",
            "white Silk outer material",
            "native cd_temp_r_m red-channel dye mask",
            "active nude-body skin material outside the primary dye zone",
            "NHW 20027 original submesh names and complete dye channels",
            "NHW 20027 native harness, waist and foot-belt material identities",
            "active nude body and leg geometry from the user's body mods",
            "all Goddess PAC face indices",
        ],
        "excluded": [
            "character/cd_nhw_no_ub_20027.prefab",
            "NHW 20027 CD_Cloak component",
            "NHW 20027 CD_Lowerbody component",
            "NHW 20027 gold necklace submesh",
            "Goddess fixed nude body PAC and 8K body texture",
            "Goddess yellow overlay DDS",
            "body-91 displacement and body-99 back/body-wide displacement",
            "chest-surface projection and harness clearance projection",
            "runtime mesh deformation or second VFS build",
            "unchanged 91/99 NHW 20027 full green chest-panel geometry",
        ],
        "safety": {
            "source_policy": "exact-current-game-sha256-required",
            "body_mod_policy": "no nude body or leg PAC packaged",
            "reference_policy": "exact-known-Goddess-directory-assets-required",
            "dye_reference_policy": (
                "exact-known-Dark-Nun-zip-and-two-uniform-red-mask-sha256-required"
            ),
            "prefab_policy": (
                "current-target bytes with one exact-length Upperbody-to-NoShrink_ "
                "shrink-tag patch per main/index prefab"
            ),
            "dye_policy": (
                "reuse verified native cd_temp_r_m.dds; no global DDS replacement"
            ),
            "geometry_policy": (
                "native NHW 20027 binary donor plus Goddess coordinate transplant; "
                "vanilla/body-91 stay natural; body-99 moves only four white front "
                "components and one harness front component; no projection or clearance "
                "fit; original submesh names, back vertices, necklace indices and foot belt "
                "stay fixed; "
                "triangle-indices-only for original 0163 lower/accessory PACs"
            ),
        },
    }
    documents.update(
        {
            CDMOD_MANIFEST_PATH: manifest_document,
            FILE_REPLACEMENT_PATH: {"schema": 1, "files": file_specs},
            PROFILED_FILE_REPLACEMENT_PATH: profiled_document,
            CDMOD_REPORT_PATH: report_document,
        }
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_cdmod_zip(output_path, documents)
    _verify_package(output_path, expected_payloads, profiled_payloads)
    return BuildResult(
        output_path=output_path,
        package_sha256=hashlib.sha256(output_path.read_bytes()).hexdigest(),
        replacement_count=len(file_specs) + 1,
        hidden_triangle_count=hidden_triangle_count,
        changed_index_count=changed_index_count,
        body_fitted_vertex_count=body_fitted_vertex_count,
        fit_validation_mean_error=fit_validation_mean_error,
        resources=tuple(resources),
    )


def _parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="生成大地荣誉 Goddess 上身模组")
    parser.add_argument(
        "--game-dir", type=Path, required=True, help="Crimson Desert 根目录"
    )
    parser.add_argument(
        "--dress-reference-dir",
        type=Path,
        required=True,
        help="DAMIANE GODDESS DRESS ONLY 白色参考目录",
    )
    parser.add_argument(
        "--dye-reference-zip",
        type=Path,
        required=True,
        help="Dark Nun Outfit 染色参考 ZIP",
    )
    parser.add_argument(
        "--body-shape-91-zip",
        type=Path,
        required=True,
        help="身材91+千件服装适配 ZIP",
    )
    parser.add_argument(
        "--body-shape-99-zip",
        type=Path,
        required=True,
        help="身材99+主角服装适配 ZIP",
    )
    parser.add_argument("--output", type=Path, required=True, help="输出 .cdmod 路径")
    return parser.parse_args()


def main() -> int:
    """生成模组并输出 UTF-8 JSON 审计结果。"""
    args = _parse_args()
    result = build_mod(
        args.game_dir,
        args.output,
        args.dress_reference_dir,
        args.dye_reference_zip,
        args.body_shape_91_zip,
        args.body_shape_99_zip,
    )
    payload = asdict(result)
    payload["output_path"] = str(result.output_path)
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
