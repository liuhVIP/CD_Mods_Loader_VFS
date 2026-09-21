r"""把舞女服的金属件整组换成腰带那套已被实机确认的金色材质。

背景（三次实机反馈都记在这里，避免第四次再踩）：

* 1.18.5 把脚环整组换成腰饰片的图层族，但连腰带 1.18.4 的黄绿 grime 一起抄了过去，实机发绿。
* 1.18.6 抄了参考模组里**象牙/丝绸族**的取值（``#ffe7b3ff`` / ``#ffffecff``），偏白不是金色。
* 1.18.7 把 grime 归零并把 ``_tintColorR`` / ``_dyeingDetailLayerColorMaskR`` 换成金色族
  ``#e6be8aff``，腰带实机确认正确，但脚环反馈仍发绿。

1.18.7 之后的取证结论：脚环 ``cd_phw_00_foot_belt_0135_00_01_01`` 在 1.18.7 就已经是全件金色——
``_colorBlendingMaskTexture`` 是 64x64 纯红 ``cd_temp_r_m.dds``，``_tintColorR`` 作用于整件，
不存在局部残留；真正还在发绿的是 ``cd_phw_00_ub_0135_00_01_01``（上身带子），它的
``_grimeBlendingOpacityParameter = 0x721C9F24`` → 字节 (36, 159, 28, 114)，G 通道主导，
就是那抹绿，而它从 1.18.4 起从未被改过。

因此 1.18.8 不再逐个猜参数，而是按用户要求把这两件（脚环 + 上身带子）的**整组材质参数换成腰带的取值**：
``_BELT_GOLD_MATERIAL`` 提供腰带（``cd_phw_00_ub_0135_00_01_02``，已实机确认金色）的全部 42 个取值，
目标件里凡是腰带也有的参数一律取腰带取值；只有 :data:`_GOLD_MATERIAL_KEEP` 列出的法线、高度、
细节遮罩与两个位移缩放保留各件自己的（它们属于几何/贴图集，不是颜色配方），目标件独有而腰带没有的
参数（脚环的 ``_dyeingTransformProperty4``）也原样保留，既不新增也不删除任何参数节点。

几何（PAC）、Prefab、体型 PAC、HKX 与裤子隐藏行为一律不碰；``_verify_output`` 会在写包前做字节级复核：
区间外逐字符比对、区间内逐参数比对，保留参数必须原值、换入参数必须等于腰带取值。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZipFile

from cdmm.services.cdmod_converter import _write_cdmod_zip

PACKAGE_ID = "cdmm-earths-honor-goddess-dress-dyeable-straps"
PACKAGE_NAME = "Earth's Honor Armor - Goddess Dress - Hidden Pants Full Body"
PACKAGE_VERSION = "1.18.8-gold-material"
OUTPUT_FILENAME = (
    "ZZZ - Earths Honor Armor to Goddess Dress - Dynamic Body Deep V Gold Belts"
    "-1.18.8-GoldMaterial.cdmod"
)

# cdmod 内的固定 entry 名（1.18.4 基线包结构）。
PAC_XML_PAYLOAD_PATH = "assets/00001/cd_phw_00_ub_00_0163.pac_xml"
REPLACEMENTS_PATH = "files/replacements.json"
MANIFEST_PATH = "manifest.json"
REPORT_PATH = "reports/conversion.json"

# 1.18.4 载荷的硬锁：大小 + sha256 任一不符都说明输入层级变了。
PAC_XML_PAYLOAD_SHA256 = "e0b679aa5af7958b6c701caa3ecb556fd366ccf3593b45eb3cea7721f99c0dcc"
PAC_XML_PAYLOAD_SIZE = 79937

# 目标子网格：小腿脚环与腰饰片。两者都在 ModelProperty Index 0/1 各有一份材质。
RING_SUBMESH = "cd_phw_00_foot_belt_0135_00_01_01"
STRAP_SUBMESH = "cd_phw_00_ub_0135_00_01_01"
BELT_SUBMESH = "cd_phw_00_ub_0135_00_01_02"

# 参考模组金色族的两个颜色取值（同一个值同时用于底色与 detail 层染色遮罩）。
GOLD_TINT = "#e6be8aff"
# 参考模组象牙/丝绸族的取值：只用于取证与防回归，绝不能再抄到我们的金属件上。
ELVEN_IVORY_TINT = "#ffe7b3ff"
ELVEN_IVORY_MASK = "#ffffecff"

# 用户已删除原始 1.18.4 整包，本机现有的是内容等价的重新打包版本；两个都接受。
ORIGINAL_SOURCE_PACKAGE_SHA256 = (
    "631d45a3e89606df0fe255e750c49c367d219c7c1113453ab7d3abba35646a24"
)
REBUILT_SOURCE_PACKAGE_SHA256 = (
    "a435f87eec6f1c49dd2b2b370f2ec8e7ed4996f392822d4cb6b4b9f829f02b91"
)
KNOWN_SOURCE_PACKAGE_SHA256: frozenset[str] = frozenset(
    {ORIGINAL_SOURCE_PACKAGE_SHA256, REBUILT_SOURCE_PACKAGE_SHA256}
)

# 1.18.4 基线的 14 个 entry 逐条锁定。
SOURCE_ENTRY_SHA256: Mapping[str, str] = {
    "assets/00000/body-91/cd_phw_00_ub_00_0163.pac": (
        "3ac5feb536ff4ec7de5c40606fc042a669164b8fd160e767cb1633a471f648f2"
    ),
    "assets/00000/body-99/cd_phw_00_ub_00_0163.pac": (
        "ff6dd66497045aced8908843c42731b69d5c12e4e5d9b1c01313163c2f0dc7f9"
    ),
    "assets/00000/vanilla/cd_phw_00_ub_00_0163.pac": (
        "be994afe94a2522d56a788630b19713b21bd1a493980578d5d568eb7f501fb6c"
    ),
    "assets/00001/cd_phw_00_ub_00_0163.pac_xml": PAC_XML_PAYLOAD_SHA256,
    "assets/00002/cd_phw_00_ub_00_0163.hkx": (
        "9beaa6b91ab31572f32fe624f8246f194f899afe5837ad7a337b9647f07809c0"
    ),
    "assets/00003/cd_phw_00_ub_00_0163.prefab": (
        "3c6c303dcfee105bae16dea3521f9f2bd54d1805270cc2e4c6197f234c8d4476"
    ),
    "assets/00004/cd_phw_00_ub_00_0163_index01.prefab": (
        "f1a9549fcf3d7cd04af12bfca36ae5ef95d1d5d607087967bfb508d532759f2d"
    ),
    "assets/00005/cd_phw_00_ub_00_0163_index02.prefab": (
        "0e0c1189c0cffb9cc5a1b15158404fc0e099e0124da28c272b2ca584eb77098b"
    ),
    "assets/00006/cd_phw_00_lb_00_0163.pac": (
        "5fb169cf07947cd1f3f538e4189d96c0039114f92de05d04fa204f24a8f5ca9f"
    ),
    "assets/00007/cd_phw_00_ub_00_0163_sub01.pac": (
        "97500f71f6877d4ee3dcab742f74fe06be303036e257d9d6166971fde3efebb4"
    ),
    "files/profiled-replacements.json": (
        "c5f959648d75821582f4e92d03ebf32c8d14f8965e14b88d1bdb03fed336a458"
    ),
    "files/replacements.json": (
        "d185633cd98d8c05668be832207f9fbc05dfcf7fd15ea43b34ed66cd0e47cbfe"
    ),
    "manifest.json": (
        "6db931c7f59db93f477b5d064b107b7e66db909adb914b68642a0308cdad4e29"
    ),
    "reports/conversion.json": (
        "b30e2cee84fa4d95225f96d1d4800a4b7b99dbdea88912b1945690bd4aa3fa89"
    ),
}

# 金色取证参考件：DamiElf The Complete Elven Package 的 0184 上身材质。
ELVEN_GOLD_REFERENCE_MOD = "DamiElf The Complete Elven Package"
ELVEN_GOLD_REFERENCE_DIR = (
    Path(r"G:\NppMODdown\crimsondesert")
    / "DamiElf - The Complete Elven Package 3364 1.1.1 2026-09-14T21-05Z R07hI2hnz"
    / ELVEN_GOLD_REFERENCE_MOD
)
ELVEN_GOLD_REFERENCE_PATH = (
    "character/modelproperty/1_pc/2_phw/armor/9_upperbody/cd_phw_00_ub_00_0184.pac_xml"
)
ELVEN_GOLD_REFERENCE_SHA256 = (
    "2d72333c3e22e07dab62153ccc9479d36251dcbe96200dad5fe977411c7d15dc"
)
# 取证用的是参考模组的**腰带**（金色族）；参考模组自己的脚环是象牙族，不能回抄。
ELVEN_GOLD_REFERENCE_SUBMESH = BELT_SUBMESH
ELVEN_IVORY_SUBMESH = RING_SUBMESH

DEFAULT_SOURCE_PACKAGE = (
    Path(__file__).resolve().parents[1] / ".work" / "goddess1184"
    / "baseline-1.18.4-rebuilt.cdmod"
)
DEFAULT_RELEASE_DIR = Path(
    r"G:\NppMODdown\crimsondesert\【服装汉化】清凉舞女长裙替换大地荣耀盔甲V1.18-cdmod"
)
DEFAULT_OUTPUT_PATH = DEFAULT_RELEASE_DIR / OUTPUT_FILENAME
DEFAULT_GOLD_REFERENCE = ELVEN_GOLD_REFERENCE_DIR / ELVEN_GOLD_REFERENCE_PATH

_BOM = b"\xef\xbb\xbf"
_MODEL_INDEXES = ("0", "1")

# ---- 三个子网格在 1.18.4 基线里的参数顺序 ----

# 脚环（腿环）：cd_phw_00_foot_belt_0135_00_01_01
_RING_PARAMETER_ORDER: tuple[str, ...] = (
    "_normalTexture",
    "_heightTexture",
    "_screenSpaceDisplacementScale",
    "_overlayColorTexture",
    "_colorBlendingMaskTexture",
    "_detailMaskTexture",
    "_grimeBlendingParameterR",
    "_grimeBlendingOpacityParameter",
    "_tintColorR",
    "_grimeDiffuseTextureR",
    "_grimeNormalTextureR",
    "_grimeMaterialTextureR",
    "_detailDiffuseMaskR",
    "_detailNormalMaskR",
    "_detailHeightMaskR",
    "_detailMaterialMaskR",
    "_dyeingDetailLayerColorMaskR",
    "_dyeingPropertyBlend",
    "_colorBlendingFlag",
    "_dyeingGlobalOpacity",
    "_dyeingTransformProperty0",
    "_dyeingTransformProperty1",
    "_dyeingTransformProperty3",
    "_dyeingTransformProperty4",
    "_detailScreenSpaceDisplacementScale",
    "_damageBlendingParameter",
)

# 上身带子：cd_phw_00_ub_0135_00_01_01
_STRAP_PARAMETER_ORDER: tuple[str, ...] = (
    "_normalTexture",
    "_heightTexture",
    "_screenSpaceDisplacementScale",
    "_overlayColorTexture",
    "_colorBlendingMaskTexture",
    "_detailMaskTexture",
    "_grimeBlendingParameterR",
    "_grimeBlendingParameterG",
    "_grimeBlendingParameterB",
    "_grimeBlendingOpacityParameter",
    "_tintColorR",
    "_grimeDiffuseTextureR",
    "_grimeNormalTextureR",
    "_grimeMaterialTextureR",
    "_grimeDiffuseTextureG",
    "_grimeNormalTextureG",
    "_grimeMaterialTextureG",
    "_grimeDiffuseTextureB",
    "_grimeNormalTextureB",
    "_grimeMaterialTextureB",
    "_detailDiffuseMaskR",
    "_detailNormalMaskR",
    "_detailHeightMaskR",
    "_detailMaterialMaskR",
    "_detailDiffuseMaskG",
    "_detailNormalMaskG",
    "_detailHeightMaskG",
    "_detailMaterialMaskG",
    "_detailDiffuseMaskB",
    "_detailNormalMaskB",
    "_detailHeightMaskB",
    "_detailMaterialMaskB",
    "_dyeingDetailLayerColorMaskR",
    "_dyeingPropertyBlend",
    "_colorBlendingFlag",
    "_dyeingGlobalOpacity",
    "_dyeingTransformProperty0",
    "_dyeingTransformProperty1",
    "_dyeingTransformProperty3",
    "_detailScreenSpaceDisplacementScale",
    "_damageBlendingParameter",
)

# 腰带（材质来源，本模组已实机确认金色）：cd_phw_00_ub_0135_00_01_02
_BELT_PARAMETER_ORDER: tuple[str, ...] = (
    "_normalTexture",
    "_heightTexture",
    "_screenSpaceDisplacementScale",
    "_overlayColorTexture",
    "_colorBlendingMaskTexture",
    "_detailMaskTexture",
    "_grimeBlendingParameterR",
    "_grimeBlendingParameterG",
    "_grimeBlendingParameterB",
    "_grimeBlendingOpacityParameter",
    "_grimeBlendingOpacityParameter1",
    "_tintColorR",
    "_grimeDiffuseTextureR",
    "_grimeNormalTextureR",
    "_grimeMaterialTextureR",
    "_grimeDiffuseTextureG",
    "_grimeNormalTextureG",
    "_grimeMaterialTextureG",
    "_grimeDiffuseTextureB",
    "_grimeNormalTextureB",
    "_grimeMaterialTextureB",
    "_detailDiffuseMaskR",
    "_detailNormalMaskR",
    "_detailHeightMaskR",
    "_detailMaterialMaskR",
    "_detailDiffuseMaskG",
    "_detailNormalMaskG",
    "_detailHeightMaskG",
    "_detailMaterialMaskG",
    "_detailDiffuseMaskB",
    "_detailNormalMaskB",
    "_detailHeightMaskB",
    "_detailMaterialMaskB",
    "_dyeingDetailLayerColorMaskR",
    "_dyeingPropertyBlend",
    "_colorBlendingFlag",
    "_dyeingGlobalOpacity",
    "_dyeingTransformProperty0",
    "_dyeingTransformProperty1",
    "_dyeingTransformProperty3",
    "_detailScreenSpaceDisplacementScale",
    "_damageBlendingParameter",
)

# 换成腰带材质时必须保留的参数：法线、高度、细节遮罩与两个位移缩放属于几何/贴图集，
# 不是颜色配方。三个子网格的腰带材质取值由 _BELT_GOLD_MATERIAL 统一提供。
_GOLD_MATERIAL_KEEP: tuple[str, ...] = (
    "_normalTexture",
    "_heightTexture",
    "_detailMaskTexture",
    "_screenSpaceDisplacementScale",
    "_detailScreenSpaceDisplacementScale",
)

_RING_KEEP_VALUES: Mapping[str, str] = {
    "_normalTexture": "character/texture/cd_phw_00_foot_belt_00_0135_00_01_01_n.dds",
    "_heightTexture": "character/texture/cd_phw_00_foot_belt_00_0135_00_01_01_disp.dds",
    "_screenSpaceDisplacementScale": "0.020000",
    "_detailMaskTexture": "character/texture/cd_phw_00_foot_belt_00_0135_00_01_01_mg.dds",
    "_dyeingTransformProperty4": "2139029504",
    "_detailScreenSpaceDisplacementScale": "0.020000",
}

_STRAP_KEEP_VALUES: Mapping[str, str] = {
    "_normalTexture": "character/texture/cd_phw_00_ub_00_0135_00_01_01_n.dds",
    "_heightTexture": "character/texture/cd_phw_00_ub_00_0135_00_01_01_disp.dds",
    "_screenSpaceDisplacementScale": "0.030000",
    "_detailMaskTexture": "character/texture/cd_phw_00_ub_00_0135_00_01_01_mg.dds",
    "_detailScreenSpaceDisplacementScale": "0.030000",
}

_BELT_LOCKED_PARAMETER_VALUES: Mapping[str, str] = {
    "_normalTexture": "character/texture/cd_phw_00_ub_00_0135_00_01_01_n.dds",
    "_heightTexture": "character/texture/cd_phw_00_ub_00_0135_00_01_01_disp.dds",
    "_screenSpaceDisplacementScale": "0.020000",
    "_overlayColorTexture": "character/texture/cd_common_default_overlay_old.dds",
    "_colorBlendingMaskTexture": "character/texture/cd_temp_r_m.dds",
    "_detailMaskTexture": "character/texture/cd_phw_00_ub_00_0135_00_01_01_mg.dds",
    "_grimeDiffuseTextureR": "character/texture/cd_texturelayer_003_0002.dds",
    "_grimeNormalTextureR": "character/texture/cd_texturelayer_003_0002_n.dds",
    "_grimeMaterialTextureR": "character/texture/cd_texturelayer_003_0002_sp.dds",
    "_grimeDiffuseTextureG": "character/texture/cd_texturelayer_003_0002.dds",
    "_grimeNormalTextureG": "character/texture/cd_texturelayer_003_0002_n.dds",
    "_grimeMaterialTextureG": "character/texture/cd_texturelayer_003_0002_sp.dds",
    "_grimeDiffuseTextureB": "character/texture/cd_texturelayer_001_0003.dds",
    "_grimeNormalTextureB": "character/texture/cd_texturelayer_001_0003_n.dds",
    "_grimeMaterialTextureB": "character/texture/cd_texturelayer_001_0003_sp.dds",
    "_detailDiffuseMaskR": "character/texture/cd_texturelayer_003_0002.dds",
    "_detailNormalMaskR": "character/texture/cd_texturelayer_003_0002_n.dds",
    "_detailHeightMaskR": "character/texture/cd_texturelayer_003_0002_disp.dds",
    "_detailMaterialMaskR": "character/texture/cd_texturelayer_003_0002_sp.dds",
    "_detailDiffuseMaskG": "character/texture/cd_texturelayer_003_0002.dds",
    "_detailNormalMaskG": "character/texture/cd_texturelayer_003_0002_n.dds",
    "_detailHeightMaskG": "character/texture/cd_texturelayer_003_0002_disp.dds",
    "_detailMaterialMaskG": "character/texture/cd_texturelayer_003_0002_sp.dds",
    "_detailDiffuseMaskB": "character/texture/cd_texturelayer_001_0003.dds",
    "_detailNormalMaskB": "character/texture/cd_texturelayer_001_0003_n.dds",
    "_detailHeightMaskB": "character/texture/cd_texturelayer_001_0003_disp.dds",
    "_detailMaterialMaskB": "character/texture/cd_texturelayer_001_0003_sp.dds",
    "_dyeingPropertyBlend": "2130738944",
    "_colorBlendingFlag": "15",
    "_dyeingGlobalOpacity": "16777215",
    "_dyeingTransformProperty0": "22873",
    "_dyeingTransformProperty1": "22873",
    "_dyeingTransformProperty3": "8738",
    "_detailScreenSpaceDisplacementScale": "0.020000",
    "_damageBlendingParameter": "65278",
}

_RING_BASELINE_VALUES: Mapping[str, Mapping[str, str]] = {
    "_overlayColorTexture": {"0": "character/texture/cd_common_default_overlay_old.dds", "1": "character/texture/cd_common_default_overlay_old.dds"},
    "_colorBlendingMaskTexture": {"0": "character/texture/cd_temp_r_m.dds", "1": "character/texture/cd_temp_r_m.dds"},
    "_grimeBlendingParameterR": {"0": "1879060528", "1": "1879060528"},
    "_grimeBlendingOpacityParameter": {"0": "4278255492", "1": "4278255492"},
    "_tintColorR": {"0": "#b28543ff", "1": "#b28543ff"},
    "_grimeDiffuseTextureR": {"0": "character/texture/cd_texturelayer_001_0004.dds", "1": "character/texture/cd_texturelayer_001_0004.dds"},
    "_grimeNormalTextureR": {"0": "character/texture/cd_texturelayer_001_0004_n.dds", "1": "character/texture/cd_texturelayer_001_0004_n.dds"},
    "_grimeMaterialTextureR": {"0": "character/texture/cd_texturelayer_001_0004_sp.dds", "1": "character/texture/cd_texturelayer_001_0004_sp.dds"},
    "_detailDiffuseMaskR": {"0": "character/texture/cd_texturelayer_002_0014.dds", "1": "character/texture/cd_texturelayer_002_0014.dds"},
    "_detailNormalMaskR": {"0": "character/texture/cd_texturelayer_002_0014_n.dds", "1": "character/texture/cd_texturelayer_002_0014_n.dds"},
    "_detailHeightMaskR": {"0": "character/texture/cd_texturelayer_002_0014_disp.dds", "1": "character/texture/cd_texturelayer_002_0014_disp.dds"},
    "_detailMaterialMaskR": {"0": "character/texture/cd_texturelayer_002_0014_sp.dds", "1": "character/texture/cd_texturelayer_002_0014_sp.dds"},
    "_dyeingDetailLayerColorMaskR": {"0": "#ffff35ff", "1": "#ffff64ff"},
    "_dyeingPropertyBlend": {"0": "2130738944", "1": "2130738944"},
    "_colorBlendingFlag": {"0": "15", "1": "15"},
    "_dyeingGlobalOpacity": {"0": "16777215", "1": "16777215"},
    "_dyeingTransformProperty0": {"0": "2570", "1": "2570"},
    "_dyeingTransformProperty1": {"0": "65535", "1": "65535"},
    "_dyeingTransformProperty3": {"0": "13056", "1": "13056"},
    "_damageBlendingParameter": {"0": "65278", "1": "65278"},
}

_STRAP_BASELINE_VALUES: Mapping[str, Mapping[str, str]] = {
    "_overlayColorTexture": {"0": "character/texture/cd_common_default_overlay_old.dds", "1": "character/texture/cd_common_default_overlay_old.dds"},
    "_colorBlendingMaskTexture": {"0": "character/texture/cd_temp_r_m.dds", "1": "character/texture/cd_temp_r_m.dds"},
    "_grimeBlendingParameterR": {"0": "6425", "1": "6425"},
    "_grimeBlendingParameterG": {"0": "1258297628", "1": "1258297628"},
    "_grimeBlendingParameterB": {"0": "255", "1": "255"},
    "_grimeBlendingOpacityParameter": {"0": "1914478372", "1": "1914478372"},
    "_tintColorR": {"0": "#b28543ff", "1": "#b28543ff"},
    "_grimeDiffuseTextureR": {"0": "character/texture/cd_texturelayer_002_0002.dds", "1": "character/texture/cd_texturelayer_002_0002.dds"},
    "_grimeNormalTextureR": {"0": "character/texture/cd_texturelayer_002_0002_n.dds", "1": "character/texture/cd_texturelayer_002_0002_n.dds"},
    "_grimeMaterialTextureR": {"0": "character/texture/cd_texturelayer_002_0002_sp.dds", "1": "character/texture/cd_texturelayer_002_0002_sp.dds"},
    "_grimeDiffuseTextureG": {"0": "character/texture/cd_texturelayer_002_0002.dds", "1": "character/texture/cd_texturelayer_002_0002.dds"},
    "_grimeNormalTextureG": {"0": "character/texture/cd_texturelayer_002_0002_n.dds", "1": "character/texture/cd_texturelayer_002_0002_n.dds"},
    "_grimeMaterialTextureG": {"0": "character/texture/cd_texturelayer_002_0002_sp.dds", "1": "character/texture/cd_texturelayer_002_0002_sp.dds"},
    "_grimeDiffuseTextureB": {"0": "character/texture/cd_texturelayer_001_0005.dds", "1": "character/texture/cd_texturelayer_001_0005.dds"},
    "_grimeNormalTextureB": {"0": "character/texture/cd_texturelayer_001_0005_n.dds", "1": "character/texture/cd_texturelayer_001_0005_n.dds"},
    "_grimeMaterialTextureB": {"0": "character/texture/cd_texturelayer_001_0005_sp.dds", "1": "character/texture/cd_texturelayer_001_0005_sp.dds"},
    "_detailDiffuseMaskR": {"0": "character/texture/cd_texturelayer_002_0002.dds", "1": "character/texture/cd_texturelayer_002_0013.dds"},
    "_detailNormalMaskR": {"0": "character/texture/cd_texturelayer_002_0002_n.dds", "1": "character/texture/cd_texturelayer_002_0013_n.dds"},
    "_detailHeightMaskR": {"0": "character/texture/cd_texturelayer_002_0002_disp.dds", "1": "character/texture/cd_texturelayer_002_0013_disp.dds"},
    "_detailMaterialMaskR": {"0": "character/texture/cd_texturelayer_002_0002_sp.dds", "1": "character/texture/cd_texturelayer_002_0013_sp.dds"},
    "_detailDiffuseMaskG": {"0": "character/texture/cd_texturelayer_002_0002.dds", "1": "character/texture/cd_texturelayer_002_0016.dds"},
    "_detailNormalMaskG": {"0": "character/texture/cd_texturelayer_002_0002_n.dds", "1": "character/texture/cd_texturelayer_002_0016_n.dds"},
    "_detailHeightMaskG": {"0": "character/texture/cd_texturelayer_002_0002_disp.dds", "1": "character/texture/cd_texturelayer_002_0016_disp.dds"},
    "_detailMaterialMaskG": {"0": "character/texture/cd_texturelayer_002_0002_sp.dds", "1": "character/texture/cd_texturelayer_002_0016_sp.dds"},
    "_detailDiffuseMaskB": {"0": "character/texture/cd_texturelayer_001_0004.dds", "1": "character/texture/cd_texturelayer_001_0004.dds"},
    "_detailNormalMaskB": {"0": "character/texture/cd_texturelayer_001_0004_n.dds", "1": "character/texture/cd_texturelayer_001_0004_n.dds"},
    "_detailHeightMaskB": {"0": "character/texture/cd_texturelayer_001_0004_disp.dds", "1": "character/texture/cd_texturelayer_001_0004_disp.dds"},
    "_detailMaterialMaskB": {"0": "character/texture/cd_texturelayer_001_0004_sp.dds", "1": "character/texture/cd_texturelayer_001_0004_sp.dds"},
    "_dyeingDetailLayerColorMaskR": {"0": "#ffff35ff", "1": "#ffff64ff"},
    "_dyeingPropertyBlend": {"0": "2130738944", "1": "2130738944"},
    "_colorBlendingFlag": {"0": "15", "1": "15"},
    "_dyeingGlobalOpacity": {"0": "16777215", "1": "16777215"},
    "_dyeingTransformProperty0": {"0": "18761", "1": "18761"},
    "_dyeingTransformProperty1": {"0": "19275", "1": "19275"},
    "_dyeingTransformProperty3": {"0": "9252", "1": "9252"},
    "_damageBlendingParameter": {"0": "65278", "1": "65278"},
}

_BELT_GOLD_VALUES: Mapping[str, str] = {
    "_grimeBlendingParameterR": "0",
    "_grimeBlendingParameterG": "0",
    "_grimeBlendingParameterB": "0",
    "_grimeBlendingOpacityParameter": "0",
    "_grimeBlendingOpacityParameter1": "0",
    "_tintColorR": "#e6be8aff",
    "_dyeingDetailLayerColorMaskR": "#e6be8aff",
}

_BELT_BASELINE_VALUES: Mapping[str, Mapping[str, str]] = {
    "_grimeBlendingParameterR": {"0": "2348819500", "1": "2348819500"},
    "_grimeBlendingParameterG": {"0": "9260", "1": "9260"},
    "_grimeBlendingParameterB": {"0": "1056971806", "1": "1056971806"},
    "_grimeBlendingOpacityParameter": {"0": "3658405424", "1": "3658405424"},
    "_grimeBlendingOpacityParameter1": {"0": "65398", "1": "65398"},
    "_tintColorR": {"0": "#b28543ff", "1": "#b28543ff"},
    "_dyeingDetailLayerColorMaskR": {"0": "#ffff35ff", "1": "#ffff64ff"},
}

# 基线报告里那条“脚环金色”的旧描述在 1.18.8 之后不再准确：三件金属件统一改成腰带的材质。
_LEGACY_PRESERVED_LINE = "leg-ring gold color identity copied per ModelProperty index"
_GOLD_PRESERVED_LINE = (
    "gold metal materials copied from the confirmed-gold waist belt per ModelProperty index"
)

_DESCRIPTION = (
    "基于 1.18.4，把脚环（cd_phw_00_foot_belt_0135_00_01_01）与上身带子"
    "（cd_phw_00_ub_0135_00_01_01）的整套材质参数换成腰带"
    "（cd_phw_00_ub_0135_00_01_02，本模组已实机确认金色）的金色材质："
    "grime 相关参数归零、_tintColorR 与 _dyeingDetailLayerColorMaskR 换成 #e6be8aff、"
    "图层贴图与染色变换全部改成腰带那一套；两件各自的法线、高度、细节遮罩与位移缩放保留原文。"
    "几何（PAC）、Prefab、体型 PAC、HKX、裤子隐藏行为与其它材质一律不变。"
)


@dataclass(frozen=True)
class GoldPiece:
    """一个要改成金色的子网格。"""

    submesh: str
    parameter_order: tuple[str, ...]
    gold_values: Mapping[str, str]
    baseline_values: Mapping[str, Mapping[str, str]]
    locked_values: Mapping[str, str]

    @property
    def locked_parameter_names(self) -> tuple[str, ...]:
        """不在金色清单里、必须逐字节保持 1.18.4 的参数名。"""
        return tuple(
            name for name in self.parameter_order if name not in self.gold_values
        )


def _belt_gold_material() -> Mapping[str, str]:
    """腰带（已实机确认金色）的完整 42 个材质取值。"""
    return {**_BELT_LOCKED_PARAMETER_VALUES, **_BELT_GOLD_VALUES}


def _gold_material_values(parameter_order: tuple[str, ...]) -> Mapping[str, str]:
    """按目标件的参数顺序取出要抄自腰带的取值。"""
    belt = _belt_gold_material()
    return {
        name: belt[name]
        for name in parameter_order
        if name in belt and name not in _GOLD_MATERIAL_KEEP
    }


RING_GOLD_PIECE = GoldPiece(
    submesh=RING_SUBMESH,
    parameter_order=_RING_PARAMETER_ORDER,
    gold_values=_gold_material_values(_RING_PARAMETER_ORDER),
    baseline_values=_RING_BASELINE_VALUES,
    locked_values=_RING_KEEP_VALUES,
)
STRAP_GOLD_PIECE = GoldPiece(
    submesh=STRAP_SUBMESH,
    parameter_order=_STRAP_PARAMETER_ORDER,
    gold_values=_gold_material_values(_STRAP_PARAMETER_ORDER),
    baseline_values=_STRAP_BASELINE_VALUES,
    locked_values=_STRAP_KEEP_VALUES,
)
BELT_GOLD_PIECE = GoldPiece(
    submesh=BELT_SUBMESH,
    parameter_order=_BELT_PARAMETER_ORDER,
    gold_values=_BELT_GOLD_VALUES,
    baseline_values=_BELT_BASELINE_VALUES,
    locked_values=_BELT_LOCKED_PARAMETER_VALUES,
)
GOLD_PIECES: tuple[GoldPiece, ...] = (RING_GOLD_PIECE, STRAP_GOLD_PIECE, BELT_GOLD_PIECE)


@dataclass(frozen=True)
class IndexAudit:
    """记录一个子网格在一个 ModelProperty 索引上被替换的金色参数。"""

    piece: str
    index: str
    changes: tuple[tuple[str, str, str], ...]


@dataclass(frozen=True)
class GoldTrimResult:
    """成品构建结果。"""

    output_path: Path
    source_package_sha256: str
    pac_xml_sha256: str
    pac_xml_size: int
    audits: tuple[IndexAudit, ...]

    @property
    def parameter_change_count(self) -> int:
        """两件金属件在两个索引上一共改了多少个取值。"""
        return sum(len(audit.changes) for audit in self.audits)


@dataclass(frozen=True)
class _PieceVector:
    """一个子网格在某一个 ModelProperty 索引上的 ``_parameters`` 向量区间。"""

    index: str
    body_begin: int
    body_end: int


_MODEL_PROPERTY_RE = re.compile(
    r"<ModelProperty\b(?P<attrs>[^>]*)>(?P<body>.*?)(?=<ModelProperty\b|\Z)",
    re.DOTALL,
)
_MODEL_INDEX_RE = re.compile(r'\bIndex="(?P<index>\d+)"')
_WRAPPER_RE = re.compile(
    r'<SkinnedMeshMaterialWrapper\b[^>]*_subMeshName="(?P<submesh>[^"]+)"[^>]*>'
    r"(?P<body>.*?)</SkinnedMeshMaterialWrapper>",
    re.DOTALL,
)
_PARAMETERS_VECTOR_RE = re.compile(
    r'<Vector\s+Name="_parameters"\s*>(?P<body>.*?)</Vector>', re.DOTALL
)
_PARAMETER_TAG_RE = re.compile(
    r"<(?P<tag>MaterialParameter[A-Za-z0-9_]*)\b(?P<attrs>[^>]*?)(?:/>|>)"
)
_PARAMETER_NAME_RE = re.compile(r'\s_name="(?P<name>[^"]*)"')
_PARAMETER_VALUE_RE = re.compile(r'\s_value="(?P<value>[^"]*)"')
_TEXTURE_PATH_RE = re.compile(r'\s_path="(?P<path>[^"]*)"')


def _model_property_bodies(text: str) -> tuple[tuple[str, int, str], ...]:
    """按出现顺序返回每个 ModelProperty 的 (Index, body 起点, body 文本)。"""
    blocks = []
    for match in _MODEL_PROPERTY_RE.finditer(text):
        index_match = _MODEL_INDEX_RE.search(match.group("attrs"))
        if index_match is None:
            raise ValueError("ModelProperty 缺少 Index 属性")
        blocks.append((index_match.group("index"), match.start("body"), match.group("body")))
    if not blocks:
        raise ValueError("原文里没有 ModelProperty")
    return tuple(blocks)


def _piece_vectors(text: str, submesh: str) -> tuple[_PieceVector, ...]:
    """按出现顺序返回该子网格在原文里的 ``_parameters`` 向量区间。"""
    vectors = []
    for index, offset, body in _model_property_bodies(text):
        wrappers = [
            match for match in _WRAPPER_RE.finditer(body)
            if match.group("submesh") == submesh
        ]
        if not wrappers:
            continue
        if len(wrappers) != 1:
            raise ValueError(
                f"{submesh} 在 ModelProperty Index={index} 里的材质包装数量异常："
                f"{len(wrappers)}"
            )
        wrapper_body = wrappers[0].group("body")
        base = offset + wrappers[0].start("body")
        matches = list(_PARAMETERS_VECTOR_RE.finditer(wrapper_body))
        if len(matches) != 1:
            raise ValueError(
                f"{submesh} 的 _parameters 向量数量异常：{len(matches)}"
                f"（ModelProperty Index={index}）"
            )
        vectors.append(
            _PieceVector(
                index=index,
                body_begin=base + matches[0].start("body"),
                body_end=base + matches[0].end("body"),
            )
        )
    if not vectors:
        raise ValueError(f"原文里找不到子网格 {submesh} 的材质")
    return tuple(vectors)


def _parameter_vector_spans(text: str, submesh: str) -> tuple[tuple[int, int], ...]:
    """返回该子网格各索引 ``_parameters`` 向量体在原文中的区间。"""
    return tuple((vector.body_begin, vector.body_end) for vector in _piece_vectors(text, submesh))


def _scan_parameters(body: str) -> tuple[tuple[str, int, int, str], ...]:
    """解析向量体里的材质参数，返回 (名字, 取值起点, 取值终点, 取值)。"""
    parsed: list[tuple[str, int, int, str]] = []
    for match in _PARAMETER_TAG_RE.finditer(body):
        attrs = match.group("attrs")
        name_match = _PARAMETER_NAME_RE.search(attrs)
        if name_match is None:
            continue
        name = name_match.group("name")
        if match.group(0).endswith("/>"):
            value_match = _PARAMETER_VALUE_RE.search(attrs)
            if value_match is None:
                raise ValueError(f"材质参数 {name} 缺少 _value 属性")
            parsed.append(
                (
                    name,
                    match.start("attrs") + value_match.start("value"),
                    match.start("attrs") + value_match.end("value"),
                    value_match.group("value"),
                )
            )
            continue
        close = body.find(f"</{match.group('tag')}>", match.end())
        if close < 0:
            raise ValueError(f"材质参数 {name} 没有闭合标签")
        inner = body[match.end() : close]
        path_match = _TEXTURE_PATH_RE.search(inner)
        if path_match is None or len(_TEXTURE_PATH_RE.findall(inner)) != 1:
            raise ValueError(f"材质参数 {name} 的纹理路径数量异常")
        parsed.append(
            (
                name,
                match.end() + path_match.start("path"),
                match.end() + path_match.end("path"),
                path_match.group("path"),
            )
        )
    return tuple(parsed)


def _vector_parameters(text: str, submesh: str, index: str) -> Mapping[str, str]:
    """取出指定子网格在指定索引上的参数取值表（用于取证与测试）。"""
    for vector in _piece_vectors(text, submesh):
        if vector.index == index:
            body = text[vector.body_begin : vector.body_end]
            return {name: value for name, _, _, value in _scan_parameters(body)}
    raise ValueError(f"{submesh} 在 ModelProperty Index={index} 上没有材质")


def _splice(text: str, edits: Sequence[tuple[int, int, str]]) -> str:
    """按区间替换拼接文本，区间重叠即报错。"""
    parts = []
    cursor = 0
    for begin, end, value in sorted(edits, key=lambda item: item[0]):
        if begin < cursor:
            raise ValueError("金色替换区间重叠")
        parts.append(text[cursor:begin])
        parts.append(value)
        cursor = end
    parts.append(text[cursor:])
    return "".join(parts)


def apply_gold_trim(
    source: bytes, pieces: Sequence[GoldPiece] = GOLD_PIECES
) -> tuple[bytes, tuple[IndexAudit, ...]]:
    """把脚环、上身带子与腰带的金属件改成腰带那套金色材质，返回新载荷与逐索引审计。"""
    digest = hashlib.sha256(source).hexdigest()
    if len(source) != PAC_XML_PAYLOAD_SIZE or digest != PAC_XML_PAYLOAD_SHA256:
        raise ValueError(
            "输入不是 1.18.4 锁定的 PAC_XML 载荷（大小或 sha256 不符），拒绝套用金色替换"
        )
    had_bom = source.startswith(_BOM)
    text = source.decode("utf-8-sig")
    edits: list[tuple[int, int, str]] = []
    audits: list[IndexAudit] = []
    for piece in pieces:
        vectors = _piece_vectors(text, piece.submesh)
        indexes = tuple(vector.index for vector in vectors)
        if indexes != _MODEL_INDEXES:
            raise ValueError(
                f"{piece.submesh} 的 ModelProperty 索引必须是 {list(_MODEL_INDEXES)}，"
                f"实际为 {list(indexes)}"
            )
        for vector in vectors:
            body = text[vector.body_begin : vector.body_end]
            parsed = _scan_parameters(body)
            if tuple(name for name, *_ in parsed) != piece.parameter_order:
                raise ValueError(
                    f"{piece.submesh} Index={vector.index} 的参数清单与 1.18.4 锁定清单不一致"
                )
            changes = []
            for name, begin, end, value in parsed:
                gold = piece.gold_values.get(name)
                if gold is None:
                    locked = piece.locked_values.get(name)
                    if locked is None or locked != value:
                        raise ValueError(
                            f"{piece.submesh} Index={vector.index} 的 {name} "
                            f"不是 1.18.4 基线取值：{value!r}"
                        )
                    continue
                baseline = piece.baseline_values[name][vector.index]
                if value != baseline:
                    raise ValueError(
                        f"{piece.submesh} Index={vector.index} 的 {name} "
                        f"不是 1.18.4 基线取值：{value!r} != {baseline!r}"
                    )
                if value == gold:
                    continue
                edits.append((vector.body_begin + begin, vector.body_begin + end, gold))
                changes.append((name, value, gold))
            audits.append(
                IndexAudit(piece=piece.submesh, index=vector.index, changes=tuple(changes))
            )
    output = (b"\xef\xbb\xbf" if had_bom else b"") + _splice(text, edits).encode("utf-8")
    _verify_output(source, output, edits, pieces)
    return output, tuple(audits)


def _verify_output(
    source: bytes,
    output: bytes,
    edits: Sequence[tuple[int, int, str]],
    pieces: Sequence[GoldPiece] = GOLD_PIECES,
) -> None:
    """源侧重新解析原始字节，确认改后只有金色参数变化。"""
    source_text = source.decode("utf-8-sig")
    output_text = output.decode("utf-8-sig")
    _verify_untouched_segments(source_text, output_text, edits)
    for piece in pieces:
        before_spans = _parameter_vector_spans(source_text, piece.submesh)
        after_spans = _parameter_vector_spans(output_text, piece.submesh)
        if len(before_spans) != len(_MODEL_INDEXES) or len(after_spans) != len(
            _MODEL_INDEXES
        ):
            raise ValueError(f"{piece.submesh} 的 _parameters 向量数量在复核时异常")
        for (begin, end), (new_begin, new_end) in zip(
            before_spans, after_spans, strict=True
        ):
            _verify_vector(
                source_text[begin:end], output_text[new_begin:new_end], piece
            )


def _verify_untouched_segments(
    source: str, output: str, edits: Sequence[tuple[int, int, str]]
) -> None:
    """确认成品 = 原文只替换了这些区间，其余逐字符相同。"""
    source_cursor = output_cursor = 0
    for begin, end, value in sorted(edits, key=lambda item: item[0]):
        if begin < source_cursor:
            raise ValueError("金色替换区间重叠")
        untouched = begin - source_cursor
        if source[source_cursor:begin] != output[output_cursor : output_cursor + untouched]:
            raise ValueError("金色取值以外的原文在替换后发生变化")
        output_cursor += untouched
        if output[output_cursor : output_cursor + len(value)] != value:
            raise ValueError("金色取值没有被正确写入")
        output_cursor += len(value)
        source_cursor = end
    if source[source_cursor:] != output[output_cursor:]:
        raise ValueError("金色取值以外的原文在替换后发生变化")


def _verify_vector(before: str, after: str, piece: GoldPiece) -> None:
    """逐个参数比对同一向量替换前后的字节。"""
    before_parameters = _scan_parameters(before)
    after_parameters = _scan_parameters(after)
    order = tuple(name for name, *_ in before_parameters)
    if order != piece.parameter_order or tuple(
        name for name, *_ in after_parameters
    ) != piece.parameter_order:
        raise ValueError(f"{piece.submesh} 复核时的参数清单与 1.18.4 锁定清单不一致")
    before_cursor = after_cursor = 0
    for (name, begin, end, value), (_, new_begin, new_end, new_value) in zip(
        before_parameters, after_parameters, strict=True
    ):
        if before[before_cursor:begin] != after[after_cursor:new_begin]:
            raise ValueError(f"{piece.submesh} 的 {name} 之前原文在替换后发生变化")
        gold = piece.gold_values.get(name)
        if gold is None:
            if value != new_value:
                raise ValueError(
                    f"{piece.submesh} 的锁定参数 {name} 被改动：{value!r} -> {new_value!r}"
                )
        elif new_value != gold:
            raise ValueError(
                f"{piece.submesh} 的金色参数 {name} 目标值不正确：{new_value!r} != {gold!r}"
            )
        before_cursor, after_cursor = end, new_end
    if before[before_cursor:] != after[after_cursor:]:
        raise ValueError(f"{piece.submesh} 的向量尾部原文在替换后发生变化")


def _reference_values(text: str, submesh: str) -> Mapping[str, str]:
    """取出参考件里某个子网格的参数取值表（参考件只有 Index=1）。"""
    vector = _piece_vectors(text, submesh)[0]
    return {
        name: value
        for name, _, _, value in _scan_parameters(
            text[vector.body_begin : vector.body_end]
        )
    }


def verify_gold_reference(reference: bytes) -> Mapping[str, str]:
    """核对金色取证参考件：腰带必须是金色族，脚环必须仍是象牙族。"""
    digest = hashlib.sha256(reference).hexdigest()
    if digest != ELVEN_GOLD_REFERENCE_SHA256:
        raise ValueError(
            f"参考件 sha256 不是锁定的 0184.pac_xml：{digest} != {ELVEN_GOLD_REFERENCE_SHA256}"
        )
    text = reference.decode("utf-8-sig")
    values = _reference_values(text, ELVEN_GOLD_REFERENCE_SUBMESH)
    for name in ("_tintColorR", "_dyeingDetailLayerColorMaskR"):
        if values.get(name) != GOLD_TINT:
            raise ValueError(f"参考件 {name} 不是金色族取值：{values.get(name)!r}")
    for name in (
        "_grimeBlendingParameterR",
        "_grimeBlendingParameterG",
        "_grimeBlendingParameterB",
        "_grimeBlendingOpacityParameter",
        "_grimeBlendingOpacityParameter1",
    ):
        if values.get(name) != "0":
            raise ValueError(f"参考件 {name} 不是 0：{values.get(name)!r}")
    ivory = _reference_values(text, ELVEN_IVORY_SUBMESH)
    if (
        ivory.get("_tintColorR") != ELVEN_IVORY_TINT
        or ivory.get("_dyeingDetailLayerColorMaskR") != ELVEN_IVORY_MASK
    ):
        raise ValueError("参考模组脚环不再是象牙族，金色族取证前提已变化")
    return values


def _read_source_entries(source_path: Path) -> dict[str, bytes]:
    """读取 1.18.4 基线包，并逐 entry 核对锁定摘要。"""
    if not source_path.is_file():
        raise ValueError(f"找不到 1.18.4 基线包：{source_path}")
    package_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
    if package_sha256 not in KNOWN_SOURCE_PACKAGE_SHA256:
        raise ValueError(
            f"{source_path.name} 不是已知的 1.18.4 基线包：{package_sha256}"
        )
    with ZipFile(source_path) as archive:
        names = tuple(item.filename for item in archive.infolist())
        entries = {name: archive.read(name) for name in names}
    if set(entries) != set(SOURCE_ENTRY_SHA256):
        missing = sorted(set(SOURCE_ENTRY_SHA256) - set(entries))
        extra = sorted(set(entries) - set(SOURCE_ENTRY_SHA256))
        raise ValueError(f"源包 entry 名单与 1.18.4 不一致：缺 {missing}，多 {extra}")
    for name, content in entries.items():
        digest = hashlib.sha256(content).hexdigest()
        if digest != SOURCE_ENTRY_SHA256[name]:
            raise ValueError(f"源包 entry {name} 的 sha256 与 1.18.4 锁定摘要不一致")
    return entries


def _dump_json_entry(document: object, path: str) -> bytes:
    """三份主 JSON 用 CRLF 且不加结尾换行，其余 entry 用 LF 加结尾换行。"""
    text = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True)
    if path in (REPLACEMENTS_PATH, MANIFEST_PATH, REPORT_PATH):
        return text.replace("\n", "\r\n").encode("utf-8")
    return (text + "\n").encode("utf-8")


def _updated_replacements(entries: Mapping[str, bytes], payload: bytes) -> dict:
    """把载荷行的 sha256 与大小更新成金色版本。"""
    document = json.loads(entries[REPLACEMENTS_PATH].decode("utf-8-sig"))
    rows = [
        row for row in document["files"] if row.get("payload") == PAC_XML_PAYLOAD_PATH
    ]
    if len(rows) != 1:
        raise ValueError(
            f"{REPLACEMENTS_PATH} 里 {PAC_XML_PAYLOAD_PATH} 的行数异常：{len(rows)}"
        )
    rows[0]["sha256"] = hashlib.sha256(payload).hexdigest()
    rows[0]["size"] = len(payload)
    return document


def _updated_manifest(entries: Mapping[str, bytes], package_sha256: str) -> dict:
    """更新版本、说明与基线来源指纹。"""
    document = json.loads(entries[MANIFEST_PATH].decode("utf-8-sig"))
    document["version"] = PACKAGE_VERSION
    document["description"] = _DESCRIPTION
    source = document.setdefault("source", {})
    files = source.setdefault("files", {})
    files["baseline-package://1.18.4.cdmod"] = ORIGINAL_SOURCE_PACKAGE_SHA256
    files[f"baseline-entries://1.18.4/{package_sha256}"] = (
        f"{len(SOURCE_ENTRY_SHA256)} entries"
    )
    return document


def _updated_report(
    entries: Mapping[str, bytes],
    audits: Sequence[IndexAudit],
    package_sha256: str,
    payload: bytes,
) -> dict:
    """在基线报告上追加金色金属件的逐索引审计。"""
    document = json.loads(entries[REPORT_PATH].decode("utf-8-sig"))
    pieces = []
    for piece in GOLD_PIECES:
        pieces.append(
            {
                "indexes": [
                    {
                        "changes": [list(change) for change in audit.changes],
                        "index": audit.index,
                    }
                    for audit in audits
                    if audit.piece == piece.submesh
                ],
                "locked_parameter_count": len(piece.locked_values),
                "parameter_changes": dict(piece.gold_values),
                "submesh": piece.submesh,
            }
        )
    document["gold_trim"] = {
        "geometry_or_prefab_change": False,
        "gold_reference": {
            "file": ELVEN_GOLD_REFERENCE_PATH,
            "mod": ELVEN_GOLD_REFERENCE_MOD,
            "sha256": ELVEN_GOLD_REFERENCE_SHA256,
            "submesh": ELVEN_GOLD_REFERENCE_SUBMESH,
        },
        "output_pac_xml_sha256": hashlib.sha256(payload).hexdigest(),
        "pieces": pieces,
        "rebuilt_source_package_sha256": package_sha256,
        "source_entry_sha256": dict(SOURCE_ENTRY_SHA256),
        "source_pac_xml_sha256": PAC_XML_PAYLOAD_SHA256,
        "source_package_sha256": ORIGINAL_SOURCE_PACKAGE_SHA256,
    }
    document["preserved"] = [
        line
        for line in document.get("preserved", [])
        if line != _LEGACY_PRESERVED_LINE
    ]
    if _GOLD_PRESERVED_LINE not in document["preserved"]:
        document["preserved"].append(_GOLD_PRESERVED_LINE)
    summary = document.setdefault("summary", {})
    summary["gold_trim_index_count"] = len(audits)
    summary["gold_trim_parameter_change_count"] = sum(
        len(audit.changes) for audit in audits
    )
    summary["gold_trim_piece_count"] = len(GOLD_PIECES)
    return document


def build_gold_trim_mod(source_path: Path, output_path: Path) -> GoldTrimResult:
    """从 1.18.4 基线包生成金色金属件成品。"""
    entries = _read_source_entries(source_path)
    package_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
    source_payload = entries[PAC_XML_PAYLOAD_PATH]
    payload, audits = apply_gold_trim(source_payload)
    output_entries = dict(entries)
    output_entries[PAC_XML_PAYLOAD_PATH] = payload
    output_entries[REPLACEMENTS_PATH] = _dump_json_entry(
        _updated_replacements(entries, payload), REPLACEMENTS_PATH
    )
    output_entries[MANIFEST_PATH] = _dump_json_entry(
        _updated_manifest(entries, package_sha256), MANIFEST_PATH
    )
    output_entries[REPORT_PATH] = _dump_json_entry(
        _updated_report(entries, audits, package_sha256, payload), REPORT_PATH
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_cdmod_zip(output_path, output_entries)
    return GoldTrimResult(
        output_path=output_path,
        source_package_sha256=package_sha256,
        pac_xml_sha256=hashlib.sha256(payload).hexdigest(),
        pac_xml_size=len(payload),
        audits=audits,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """命令行入口：构建 1.18.8 金色材质成品。"""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE_PACKAGE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--reference", type=Path, default=DEFAULT_GOLD_REFERENCE)
    arguments = parser.parse_args(argv)
    if arguments.reference.is_file():
        verify_gold_reference(arguments.reference.read_bytes())
        print(f"金色取证据考件已核对：{arguments.reference}")
    else:
        print(f"警告：没有取证据考件，跳过核对：{arguments.reference}")
    result = build_gold_trim_mod(arguments.source, arguments.output)
    print(f"输出：{result.output_path}")
    print(f"大小：{result.pac_xml_size} 字节，sha256：{result.pac_xml_sha256}")
    for audit in result.audits:
        print(f"  {audit.piece} Index={audit.index} 改动 {len(audit.changes)} 个取值")
    print(f"合计改动 {result.parameter_change_count} 个取值")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
