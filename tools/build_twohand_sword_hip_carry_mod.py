"""生成当前版本兼容的单把双手大剑腰挂与腰间拔刀 ``.cdmod``。

本工具处理 PHM Kliff、PHM 玩家与 PHW 玩家三份 ``CD_TwoHandWeapon_Sword``
资源链。角色描述和 PHM/PHW 双手剑 Prefab 使用当前原版上的唯一、等长变换；
PHM 身体 socket 与 PHM/PHW 双手剑 sidecar 以当前原版 XML 为基底加入腰部
别名；动作只复制当前版本原生的站立、坐姿、移动和骑马拔刀/收刀资源。PHW
分支使用达米安腰挂细剑的 PAA，并成对复制 PAA_metabin 时长元数据。主剑恢复
已验证的 ``Visible="In"`` 静态显示，并只延后当前原版 PAAC 中两条已回查到
``CD_TwoHandWeapon_Sword`` 的正常站立 transition。不会包含旧包中的单手剑
Prefab、双持动作、motionblending、PAZ、PAMT 或 meta 文件。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

# 允许从项目根目录直接执行本工具，同时保持 ``python -m cdmm.tools...`` 可用。
PROJECT_PARENT = Path(__file__).resolve().parents[2]
if str(PROJECT_PARENT) not in sys.path:
    sys.path.insert(0, str(PROJECT_PARENT))

from cdmm.archive.pamt import parse_pamt  # noqa: E402
from cdmm.common.models import PazEntry  # noqa: E402
from cdmm.services.cdmod_converter import (  # noqa: E402
    CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE,
    CDMOD_FORMAT_NAME,
    CDMOD_FORMAT_VERSION,
    CDMOD_RESOURCE_TRANSFORM_COMPONENT_TYPE,
    _write_cdmod_zip,
)
from cdmm.services.cdmod_package import load_cdmod_package  # noqa: E402
from cdmm.services.json_loader import extract_plaintext  # noqa: E402

# 当前角色描述、Prefab、socket 和 PAA 动作均来自原版 0009 包。
PAMT_DIR = "0009"

# PAA_metabin 位于原版 0010，其内容必须与 PAA 来源身份成对。
ANIMATION_META_PAMT_DIR = "0010"

# 成品内部文档路径集中定义，避免 manifest 与实际载荷路径漂移。
MANIFEST_PATH = "manifest.json"
FILE_PATCH_PATH = "patches/files.json"
RESOURCE_PATCH_PATH = "patches/resources.json"
REPORT_PATH = "reports/build-audit.json"
PAYLOAD_PREFIX = "payload/current-xml"
PAAC_PAYLOAD_PATH = "payload/current-paac/basic_upper_weaponin.paac"

# 成品身份用于加载顺序、缓存和后续版本审计。
PACKAGE_ID = "cdmm.twohand-sword-hip-carry-cc"
PACKAGE_NAME = "Two-Handed Swords Hip Carry - CC Compatible"
PACKAGE_VERSION = "1.15.11"

# 当前 1.15 原版 PHM/PHW 通用收武器动作图。1.15.11 只修改其中 PHW lswd
# 正常站立分支里已证明指向 part 7（CD_TwoHandWeapon_Sword）的 transition。
WEAPON_IN_ACTIONCHART_TARGET = (
    "actionchart/bin__/upperaction/1_pc/1_phm/basic_upper_weaponin.paac"
)
EXPECTED_WEAPON_IN_ACTIONCHART_SIZE = 149_283
EXPECTED_WEAPON_IN_ACTIONCHART_SHA256 = (
    "8e55e758a917dc10aa69c35245220ddab46168b2fe27891561ef79b2b9183525"
)

# 两条 PHW lswd 正常站立分支的同一 PartInOut transition。target=1245 已经
# 通过图缓冲区回查到 part 7；旧时刻约 0.5333 秒，1.15.11 延后到动作末段
# 1.5 秒。坐姿和其他条件分支保持原版，避免扩大尚未实机验收的范围。
PHW_LSWD_NORMAL_SHEATHE_TRANSITION_OFFSETS = (0x2B1F, 0x2F50)
PHW_LSWD_NORMAL_SHEATHE_OLD_TIME = 0.53333336
PHW_LSWD_NORMAL_SHEATHE_NEW_TIME = 1.5
PHW_LSWD_NORMAL_SHEATHE_TARGET = 1245
PHW_LSWD_NORMAL_SHEATHE_SEQUENCE = 4

# 用户提供的元素包只用于确认当前 ItemInfo 中的双手剑物品身份，不能把元素
# 效果逻辑当作武器挂载逻辑。固定哈希可防止误拿同名或被修改的包。
IDENTITY_REFERENCE_SHA256 = (
    "d4ad9e7cc806e0a066aeb67e54f509a5138fe9f4b23df64e792f81505dc626d9"
)
IDENTITY_REFERENCE_REPORT = "reports/element-targets.json"
EXPECTED_IDENTITY_TARGET_COUNT = 20

# 旧作者包只作为视觉意图与历史资源范围的参考，不直接复制任何旧字节。
REFERENCE_DESCRIPTION_PATH = Path(
    "character/descriptors/characterdescription/phm_description_player_kliff.xml"
)
REFERENCE_TWOHAND_PREFAB_DIR = Path(
    "character/bin__/prefab/1_pc/01_phm/weapon/02_twohandweapon"
)
REFERENCE_BODY_SOCKET_PATH = Path(
    "character/descriptors/socketbonedata/1_pc/1_phm/phm_01.pab.sockets.xml"
)

# 同时覆盖当前原版的 Kliff、PHM 玩家与 PHW 玩家描述，避免把某一份合理目标
# 误当成性转角色运行时唯一入口；旧包新增的根部同名 XML 仍不是当前目标。
DESCRIPTION_TARGET = (
    "character/descriptors/characterdescription/phm_description_player_kliff.xml"
)
PHM_PLAYER_DESCRIPTION_TARGET = (
    "character/descriptors/characterdescription/phm_description_player_001.xml"
)
PHW_PLAYER_DESCRIPTION_TARGET = (
    "character/descriptors/characterdescription/phw_description_player_001.xml"
)
DESCRIPTION_TARGETS = (
    DESCRIPTION_TARGET,
    PHM_PLAYER_DESCRIPTION_TARGET,
    PHW_PLAYER_DESCRIPTION_TARGET,
)
BODY_SOCKET_TARGET = (
    "character/descriptors/socketbonedata/1_pc/1_phm/phm_01.pab.sockets.xml"
)
SIDECAR_TARGET_PREFIX = (
    "character/descriptors/socketbonedata/1_pc/1_phm/weapon/2_twohandweapon/"
)
PHW_SIDECAR_TARGET_PREFIX = (
    "character/descriptors/socketbonedata/1_pc/2_phw/weapon/2_twohandweapon/"
)

# 当前 1.15 原版中的三条共享双手剑 sidecar。构建时仍会动态发现并与此集合
# 交叉核对，避免漏掉游戏更新新增的共享资源。
KNOWN_SIDECAR_BASENAMES = {
    "cd_phm_02_sword_0001.sockets.xml",
    "cd_phm_02_sword_0001_in.sockets.xml",
    "cd_phm_02_sword_0015_in.sockets.xml",
}
KNOWN_PHW_SIDECAR_BASENAMES = {
    "cd_phw_02_sword_0001.sockets.xml",
    "cd_phw_02_sword_0002.sockets.xml",
}
KNOWN_SIDECAR_TARGETS = {
    *(f"{SIDECAR_TARGET_PREFIX}{name}" for name in KNOWN_SIDECAR_BASENAMES),
    *(f"{PHW_SIDECAR_TARGET_PREFIX}{name}" for name in KNOWN_PHW_SIDECAR_BASENAMES),
}

# 等长 body 变换最多纳入 Pelvis 后 16 条相邻 socket，仅移除其标签间格式空白，
# 直到足以容纳 SubWeapon 原版姿态；不会删除或改写这些 donor 节点。
BODY_SOCKET_MAX_PADDING_DONORS = 16

# 1.15.4 已实机确认正 Y 腰挂角度。1.15.7 把父骨切到右侧后又取反局部
# 四元数，导致剑柄压在身体后侧、剑刃向前下方穿出。右侧 B_WeaponIn_L_00
# 父骨本身已经完成左右变换，局部姿态应保留已验证的正 Y 旋转。
HIP_BODY_SUBWEAPON_SOCKET = (
    b'<Socket Name="Pelvis_R_SubWeapon_Socket" Parent="B_WeaponIn_L_00" '
    b'Rotation="0 .21644 0 .976296" Translation="0 0 .02"/>'
)
REFERENCE_HIP_BODY_SUBWEAPON_SOCKET = (
    b'<Socket Name="Pelvis_L_SubWeapon_Socket" Parent="B_WeaponIn_R_00" '
    b'Rotation="0.000000 0.216440 0.000000 0.976296" '
    b'Translation="0.000000 0.000000 0.020000"/>'
)

# 角色描述替换两条唯一完整路由，并恢复 1.15.8 已实机确认的 Visible="In"。
# 1.15.10 等长移除 Visible 后秒收现象完全不变，证明该属性不是切换时刻来源；
# 部分双手剑又没有独立可见剑鞘，因此不得继续牺牲这条静态显示基线。
DESCRIPTION_CURRENT_LINES = (
    '<PartInOutSocket PartName="CD_TwoHandWeapon_Sword" InSocketBone="Spine2_B_SubWeapon_Socket" OutSocketBone="RHand_Socket" InChildSocketBone="Spine2_B_SubWeapon_ChildSocket" OutChildSocketBone="Basic_ChildSocket" BagSocketBone="Bag_SubWeapon_Socket" WeaponCasePart="CD_TwoHandWeapon_Sword_IN"/>',
    '<PartInOutSocket PartName="CD_TwoHandWeapon_Sword_IN" InSocketBone="Spine2_B_SubWeapon_Socket" OutSocketBone="RHand_Socket" InChildSocketBone="Spine2_B_SubWeapon_ChildSocket" OutChildSocketBone="Basic_ChildSocket" BagSocketBone="Bag_SubWeapon_Socket"/>',
    '<PartInOutSocket PartName="CD_TwoHandWeapon_Sword" Visible="Out"/>',
)
DESCRIPTION_HIP_LINES = (
    '<PartInOutSocket PartName="CD_TwoHandWeapon_Sword" InSocketBone="Pelvis_R_Socket" OutSocketBone="RHand_Socket" InChildSocketBone="Pelvis_R_ChildSocket" OutChildSocketBone="Basic_ChildSocket" BagSocketBone="Pelvis_R_Socket" WeaponCasePart="CD_TwoHandWeapon_Sword_IN"/>',
    '<PartInOutSocket PartName="CD_TwoHandWeapon_Sword_IN" InSocketBone="Pelvis_R_Socket" OutSocketBone="Pelvis_R_Socket" InChildSocketBone="Pelvis_R_ChildSocket" OutChildSocketBone="Pelvis_R_ChildSocket" BagSocketBone="Pelvis_R_Socket"/>',
    '<PartInOutSocket PartName="CD_TwoHandWeapon_Sword" Visible="In"/>',
)

# PHW 玩家描述的拔刀手和 Spine2 父挂点与 PHM 不同；右腰路由使用原版
# 双持短剑已定义的 Pelvis_R 链，拔刀时仍保留女性原版 LHand。
PHW_DESCRIPTION_CURRENT_LINES = (
    '<PartInOutSocket PartName="CD_TwoHandWeapon_Sword" InSocketBone="Spine2_B_MainWeapon_Socket" OutSocketBone="LHand_Socket" InChildSocketBone="Spine2_B_SubWeapon_ChildSocket" OutChildSocketBone="Basic_ChildSocket" BagSocketBone="Bag_SubWeapon_Socket" WeaponCasePart="CD_TwoHandWeapon_Sword_IN"/>',
    '<PartInOutSocket PartName="CD_TwoHandWeapon_Sword_IN" InSocketBone="Spine2_B_MainWeapon_Socket" OutSocketBone="LHand_Socket" InChildSocketBone="Spine2_B_SubWeapon_ChildSocket" OutChildSocketBone="Basic_ChildSocket" BagSocketBone="Bag_SubWeapon_Socket"/>',
    '<PartInOutSocket PartName="CD_TwoHandWeapon_Sword" Visible="Out"/>',
)
PHW_DESCRIPTION_HIP_LINES = (
    '<PartInOutSocket PartName="CD_TwoHandWeapon_Sword" InSocketBone="Pelvis_R_Socket" OutSocketBone="LHand_Socket" InChildSocketBone="Pelvis_R_ChildSocket" OutChildSocketBone="Basic_ChildSocket" BagSocketBone="Pelvis_R_Socket" WeaponCasePart="CD_TwoHandWeapon_Sword_IN"/>',
    '<PartInOutSocket PartName="CD_TwoHandWeapon_Sword_IN" InSocketBone="Pelvis_R_Socket" OutSocketBone="Pelvis_R_Socket" InChildSocketBone="Pelvis_R_ChildSocket" OutChildSocketBone="Pelvis_R_ChildSocket" BagSocketBone="Pelvis_R_Socket"/>',
    '<PartInOutSocket PartName="CD_TwoHandWeapon_Sword" Visible="In"/>',
)

# 普通剑体可直接使用现有 Pelvis_R socket；七个 _in/剑鞘 Prefab 的长名称
# 不能缩短，否则会破坏二进制反射结构，因此路由到新增的同位置等长别名。
MAIN_PREFAB_REPLACEMENTS = (
    (b"Spine2_B_Socket", b"Pelvis_R_Socket"),
    (b"Spine2_B_ChildSocket", b"Pelvis_R_ChildSocket"),
)
IN_PREFAB_REPLACEMENTS = (
    (b"Spine2_B_SubWeapon_Socket", b"Pelvis_R_SubWeapon_Socket"),
    (
        b"Spine2_B_SubWeapon_ChildSocket",
        b"Pelvis_R_SubWeapon_ChildSocket",
    ),
)

# 三条 sidecar 在当前原版已有同一条背部 child socket。生成的 XML 保留该
# 名称作为回退，并新增两个腰部别名；三者统一使用旧包验证过的约 45 度旋转
# 和 -0.15 本地位移，但保留当前版本其余 Gimmick/FX/Store socket。
CURRENT_SIDECAR_SOCKET = (
    b'<Socket Name="Spine2_B_SubWeapon_ChildSocket" Parent="B_Weapon_0001" '
    b'Rotation="0.000000 1.000000 0.000000 -0.000000" '
    b'Translation="0.000000 0.000000 -0.470292"/>'
)
HIP_SIDECAR_SOCKET = (
    b'<Socket Name="Spine2_B_SubWeapon_ChildSocket" Parent="B_Weapon_0001" '
    b'Rotation="0.000000 0.382683 0.000000 0.923880" '
    b'Translation="0.000000 0.000000 -0.150000"/>'
)

# 只覆盖真正会发生拔刀/收刀的双手剑动作。source 均为当前 1.15 原版 entry；
# target 始终保留 longsword/lswd 身份，所以不会碰双持单手剑 dlsd/dualsword。
# PHM 使用原版单手剑腰部动作；不能继续使用 twsh 双手剑来源，否则收刀动作仍
# 会保留背部轨迹。
PHM_ANIMATION_COPY_MAP = {
    "character/motion/1_pc/1_phm/cd_phm_longsword_00_00_normal_move_run_f_weapon_out_000.paa": "character/motion/1_pc/1_phm/cd_phm_sword_00_00_normal_move_run_f_weapon_out_000.paa",
    "character/motion/1_pc/1_phm/cd_phm_longsword_00_01_normal_stand_weapon_in_000.paa": "character/motion/1_pc/1_phm/cd_phm_sword_00_01_normal_stand_weapon_in_000.paa",
    "character/motion/1_pc/1_phm/cd_phm_longsword_00_01_normal_stand_weapon_out_000.paa": "character/motion/1_pc/1_phm/cd_phm_sword_00_01_normal_stand_weapon_out_000.paa",
    "character/motion/1_pc/1_phm/cd_phm_longsword_01_00_normal_move_walkfast_f_weapon_out_000.paa": "character/motion/1_pc/1_phm/cd_phm_sword_00_01_normal_move_walkfast_f_weapon_out_000.paa",
    "character/motion/1_pc/1_phm/cd_phm_longsword_01_03_normal_stand_weapon_out_000.paa": "character/motion/1_pc/1_phm/cd_phm_sword_00_01_normal_stand_weapon_out_000.paa",
    "character/motion/1_pc/1_phm/cd_phm_lswd_00_01_sit_std_weapon_in_00.paa": "character/motion/1_pc/1_phm/cd_phm_swds_00_01_sit_std_weapon_in_00.paa",
    "character/motion/1_pc/1_phm/cd_phm_lswd_00_01_sit_std_weapon_out_00.paa": "character/motion/1_pc/1_phm/cd_phm_swds_00_01_sit_std_weapon_out_00.paa",
    "character/motion/1_pc/1_phm/cd_phm_lswd_01_01_alert_nor_std_weapon_out_00.paa": "character/motion/1_pc/1_phm/cd_phm_sword_00_01_normal_stand_weapon_out_000.paa",
    "character/motion/1_pc/1_phm/cd_phm_lswd_01_03_nor_stand_weapon_out_00.paa": "character/motion/1_pc/1_phm/cd_phm_sword_00_01_normal_stand_weapon_out_000.paa",
    "character/motion/1_pc/1_phm/00_riding/cd_prh_lswd_01_01_nor_std_weapon_in_00.paa": "character/motion/1_pc/1_phm/00_riding/cd_prh_swd_01_01_nor_std_weapon_in_00.paa",
    "character/motion/1_pc/1_phm/00_riding/cd_prh_lswd_01_01_nor_std_weapon_out_00.paa": "character/motion/1_pc/1_phm/00_riding/cd_prh_swd_01_01_nor_std_weapon_out_00.paa",
}

# Female Animations 会把 Kliff 的默认动作索引和骨架切到 PHW。当前 PHM 动作
# 即使最终字节正确，也不能覆盖 basic_upper_weaponin/twohandsword_upper PAAC 中
# 明确存在的 PHW 分支。PHW 使用同骨架、同腰挂方向的原生 rpr 细剑动作；
# 站立、坐姿、警戒和骑马都取当前原版已被动作图引用的对应资源。
PHW_ANIMATION_COPY_MAP = {
    "character/motion/1_pc/2_phw/cd_phw_lswd_00_01_nor_std_weapon_in_00.paa": "character/motion/1_pc/2_phw/cd_phw_rpr_00_01_nor_std_rpsd_in_00.paa",
    "character/motion/1_pc/2_phw/cd_phw_lswd_00_01_nor_std_weapon_out_00.paa": "character/motion/1_pc/2_phw/cd_phw_rpr_00_01_nor_std_rpsd_out_00.paa",
    "character/motion/1_pc/2_phw/cd_phw_lswd_01_00_sit_base_std_weapon_in_00.paa": "character/motion/1_pc/2_phw/cd_phw_rpr_01_00_sit_base_std_weapon_in_00.paa",
    "character/motion/1_pc/2_phw/cd_phw_lswd_01_00_sit_base_std_weapon_out_00.paa": "character/motion/1_pc/2_phw/cd_phw_rpr_01_00_sit_base_std_weapon_out_00.paa",
    "character/motion/1_pc/2_phw/cd_phw_lswd_01_01_alert_nor_std_weapon_out_00.paa": "character/motion/1_pc/2_phw/cd_phw_rpr_01_01_alert_nor_std_weapon_out_00.paa",
    "character/motion/1_pc/2_phw/00_riding/cd_phw_rd_prh_lswd_01_01_nor_std_weapon_in_00.paa": "character/motion/1_pc/2_phw/00_riding/cd_phw_rd_prh_00_01_rpr_nor_std_rpr_in_00.paa",
    "character/motion/1_pc/2_phw/00_riding/cd_phw_rd_prh_lswd_01_01_nor_std_weapon_out_00.paa": "character/motion/1_pc/2_phw/00_riding/cd_phw_rd_prh_00_01_rpr_nor_std_rpr_out_00.paa",
}

# LOD 与完整动作保持同源，避免远景或骑马状态回落到背部拔刀动作。
PHM_ANIMATION_LOD_COPY_MAP = {
    "character/motion/motion_lod__/1_pc/1_phm/cd_phm_longsword_00_01_normal_stand_weapon_in_000_lod.paa": "character/motion/motion_lod__/1_pc/1_phm/cd_phm_sword_00_01_normal_stand_weapon_in_000_lod.paa",
    "character/motion/motion_lod__/1_pc/1_phm/cd_phm_longsword_00_01_normal_stand_weapon_out_000_lod.paa": "character/motion/motion_lod__/1_pc/1_phm/cd_phm_sword_00_01_normal_stand_weapon_out_000_lod.paa",
    "character/motion/motion_lod__/1_pc/1_phm/cd_phm_lswd_00_01_sit_std_weapon_in_00_lod.paa": "character/motion/motion_lod__/1_pc/1_phm/cd_phm_swds_00_01_sit_std_weapon_in_00_lod.paa",
    "character/motion/motion_lod__/1_pc/1_phm/cd_phm_lswd_00_01_sit_std_weapon_out_00_lod.paa": "character/motion/motion_lod__/1_pc/1_phm/cd_phm_swds_00_01_sit_std_weapon_out_00_lod.paa",
    "character/motion/motion_lod__/1_pc/1_phm/00_riding/cd_prh_lswd_01_01_nor_std_weapon_in_00_lod.paa": "character/motion/motion_lod__/1_pc/1_phm/00_riding/cd_prh_swd_01_01_nor_std_weapon_in_00_lod.paa",
    "character/motion/motion_lod__/1_pc/1_phm/00_riding/cd_prh_lswd_01_01_nor_std_weapon_out_00_lod.paa": "character/motion/motion_lod__/1_pc/1_phm/00_riding/cd_prh_swd_01_01_nor_std_weapon_out_00_lod.paa",
}

PHW_ANIMATION_LOD_COPY_MAP = {
    "character/motion/motion_lod__/1_pc/2_phw/cd_phw_lswd_00_01_nor_std_weapon_in_00_lod.paa": "character/motion/motion_lod__/1_pc/2_phw/cd_phw_rpr_00_01_nor_std_rpsd_in_00_lod.paa",
    "character/motion/motion_lod__/1_pc/2_phw/cd_phw_lswd_00_01_nor_std_weapon_out_00_lod.paa": "character/motion/motion_lod__/1_pc/2_phw/cd_phw_rpr_00_01_nor_std_rpsd_out_00_lod.paa",
    "character/motion/motion_lod__/1_pc/2_phw/cd_phw_lswd_01_00_sit_base_std_weapon_in_00_lod.paa": "character/motion/motion_lod__/1_pc/2_phw/cd_phw_rpr_01_00_sit_base_std_weapon_in_00_lod.paa",
    "character/motion/motion_lod__/1_pc/2_phw/cd_phw_lswd_01_00_sit_base_std_weapon_out_00_lod.paa": "character/motion/motion_lod__/1_pc/2_phw/cd_phw_rpr_01_00_sit_base_std_weapon_out_00_lod.paa",
    "character/motion/motion_lod__/1_pc/2_phw/cd_phw_lswd_01_01_alert_nor_std_weapon_out_00_lod.paa": "character/motion/motion_lod__/1_pc/2_phw/cd_phw_rpr_01_01_alert_nor_std_weapon_out_00_lod.paa",
    "character/motion/motion_lod__/1_pc/2_phw/00_riding/cd_phw_rd_prh_lswd_01_01_nor_std_weapon_in_00_lod.paa": "character/motion/motion_lod__/1_pc/2_phw/00_riding/cd_phw_rd_prh_00_01_rpr_nor_std_rpr_in_00_lod.paa",
    "character/motion/motion_lod__/1_pc/2_phw/00_riding/cd_phw_rd_prh_lswd_01_01_nor_std_weapon_out_00_lod.paa": "character/motion/motion_lod__/1_pc/2_phw/00_riding/cd_phw_rd_prh_00_01_rpr_nor_std_rpr_out_00_lod.paa",
}

ANIMATION_COPY_MAP = {**PHM_ANIMATION_COPY_MAP, **PHW_ANIMATION_COPY_MAP}
ANIMATION_LOD_COPY_MAP = {
    **PHM_ANIMATION_LOD_COPY_MAP,
    **PHW_ANIMATION_LOD_COPY_MAP,
}


def _animation_meta_path(animation_path: str) -> str:
    """把普通 PAA 路径转换为 0010 中的 PAA_metabin 路径。"""
    prefix = "character/motion/"
    if not animation_path.startswith(prefix) or "motion_lod__" in animation_path:
        raise ValueError(f"无法为非普通 PAA 生成 metabin 路径：{animation_path}")
    return f"actionchart/bin__/animmeta/{animation_path.removeprefix(prefix)}_metabin"


# PHW 拔刀/收刀不能只复制姿态 PAA；metabin 仍需使用同一来源身份，避免
# 动作时长与源姿态不一致。实机已证明 metabin 配对不等于武器换挂事件同步。
PHW_ANIMATION_META_COPY_MAP = {
    _animation_meta_path(target): _animation_meta_path(source)
    for target, source in PHW_ANIMATION_COPY_MAP.items()
}


@dataclass(frozen=True)
class BuildResult:
    """记录成品路径、指纹与各资源层的覆盖数量。"""

    output_path: str
    package_sha256: str
    package_bytes: int
    description_targets: int
    body_socket_targets: int
    weapon_sidecar_targets: int
    weapon_prefab_targets: int
    main_prefab_targets: int
    in_prefab_targets: int
    animation_targets: int
    animation_lod_targets: int
    animation_meta_targets: int
    animation_chart_targets: int
    file_replacements: int
    resource_operations: int


def build_twohand_sword_hip_carry_mod(
    game_dir: Path,
    reference_mod_dir: Path,
    identity_reference: Path,
    output_path: Path,
) -> BuildResult:
    """从当前原版构建双手大剑腰挂与拔刀/收刀兼容包。"""
    game_dir = game_dir.resolve()
    reference_mod_dir = reference_mod_dir.resolve()
    identity_reference = identity_reference.resolve()
    output_path = output_path.resolve()
    _validate_inputs(game_dir, reference_mod_dir, identity_reference, output_path)
    identity_targets = _validate_identity_reference(identity_reference)
    _validate_old_reference(reference_mod_dir)

    entries = parse_pamt(
        game_dir / PAMT_DIR / "0.pamt",
        paz_dir=game_dir / PAMT_DIR,
    )
    entries_by_path = {_entry_final_path(entry): entry for entry in entries}
    animation_meta_entries = parse_pamt(
        game_dir / ANIMATION_META_PAMT_DIR / "0.pamt",
        paz_dir=game_dir / ANIMATION_META_PAMT_DIR,
    )
    animation_meta_entries_by_path = {
        _entry_final_path(entry): entry for entry in animation_meta_entries
    }
    _validate_required_animation_entries(
        entries_by_path,
        animation_meta_entries_by_path,
    )

    file_documents, file_specs, xml_audits = _build_current_xml_payloads(
        entries_by_path
    )
    vanilla_weapon_in_paac = _read_exact_entry(
        animation_meta_entries_by_path,
        WEAPON_IN_ACTIONCHART_TARGET,
    )
    patched_weapon_in_paac = _patch_phw_longsword_normal_sheathe_transition(
        vanilla_weapon_in_paac
    )
    file_documents[PAAC_PAYLOAD_PATH] = patched_weapon_in_paac
    file_specs.append(
        {
            "target": WEAPON_IN_ACTIONCHART_TARGET,
            "pamt_dir": ANIMATION_META_PAMT_DIR,
            "payload": PAAC_PAYLOAD_PATH,
            "sha256": hashlib.sha256(patched_weapon_in_paac).hexdigest(),
            "size": len(patched_weapon_in_paac),
            "allow_new": False,
            "allow_table_replace": False,
        }
    )
    operations: list[dict[str, object]] = []

    for description_target in DESCRIPTION_TARGETS:
        description = _read_exact_entry(entries_by_path, description_target)
        description_replacements = _build_description_replacements(
            description,
            description_target,
        )
        operations.append(
            _replace_operation(description_target, description_replacements)
        )

    body_socket = _read_exact_entry(entries_by_path, BODY_SOCKET_TARGET)
    body_replacements = _build_body_socket_resource_replacements(body_socket)
    operations.append(_replace_operation(BODY_SOCKET_TARGET, body_replacements))

    prefab_operations, main_prefab_count, in_prefab_count = _build_prefab_operations(
        entries_by_path
    )
    operations.extend(prefab_operations)

    for target, source in {
        **ANIMATION_COPY_MAP,
        **ANIMATION_LOD_COPY_MAP,
    }.items():
        operations.append(_copy_operation(target, source))
    for target, source in PHW_ANIMATION_META_COPY_MAP.items():
        operations.append(
            _copy_operation(
                target,
                source,
                target_pamt_dir=ANIMATION_META_PAMT_DIR,
                source_pamt_dir=ANIMATION_META_PAMT_DIR,
            )
        )

    _validate_operation_targets_unique(operations)
    manifest = _build_manifest(
        file_count=len(file_specs),
        operation_count=len(operations),
        prefab_count=len(prefab_operations),
        animation_count=len(ANIMATION_COPY_MAP),
        animation_lod_count=len(ANIMATION_LOD_COPY_MAP),
        animation_meta_count=len(PHW_ANIMATION_META_COPY_MAP),
    )
    report = {
        "schema": 1,
        "game_version": "1.15",
        "identity_reference": {
            "file": identity_reference.name,
            "sha256": _sha256_file(identity_reference),
            "two_hand_sword_targets": identity_targets,
        },
        "old_reference": {
            "directory": reference_mod_dir.name,
            "policy": "intent-only-no-old-bytes-copied",
        },
        "coverage": {
            "description_targets": len(DESCRIPTION_TARGETS),
            "body_socket_targets": 1,
            "weapon_sidecar_targets": len(KNOWN_SIDECAR_TARGETS),
            "weapon_prefab_targets": len(prefab_operations),
            "main_prefab_targets": main_prefab_count,
            "in_prefab_targets": in_prefab_count,
            "animation_targets": len(ANIMATION_COPY_MAP),
            "animation_lod_targets": len(ANIMATION_LOD_COPY_MAP),
            "phm_animation_targets": len(PHM_ANIMATION_COPY_MAP),
            "phm_animation_lod_targets": len(PHM_ANIMATION_LOD_COPY_MAP),
            "phw_animation_targets": len(PHW_ANIMATION_COPY_MAP),
            "phw_animation_lod_targets": len(PHW_ANIMATION_LOD_COPY_MAP),
            "phw_animation_meta_targets": len(PHW_ANIMATION_META_COPY_MAP),
            "animation_chart_targets": 1,
        },
        "xml_resources": xml_audits,
        "animation_chart": {
            "target": WEAPON_IN_ACTIONCHART_TARGET,
            "vanilla_size": len(vanilla_weapon_in_paac),
            "vanilla_sha256": hashlib.sha256(vanilla_weapon_in_paac).hexdigest(),
            "patched_size": len(patched_weapon_in_paac),
            "patched_sha256": hashlib.sha256(patched_weapon_in_paac).hexdigest(),
            "transition_target": PHW_LSWD_NORMAL_SHEATHE_TARGET,
            "transition_offsets": [
                f"0x{offset:x}"
                for offset in PHW_LSWD_NORMAL_SHEATHE_TRANSITION_OFFSETS
            ],
            "old_time": PHW_LSWD_NORMAL_SHEATHE_OLD_TIME,
            "new_time": PHW_LSWD_NORMAL_SHEATHE_NEW_TIME,
        },
        "excluded": [
            "01_onehandweapon prefabs",
            "dlsd and dualsword animation targets",
            "motionblending",
            "old complete prefabs and XML",
            "standalone PAZ/PAMT/meta",
            "element effect operations",
        ],
    }
    documents: dict[str, dict[str, object] | bytes] = {
        MANIFEST_PATH: manifest,
        FILE_PATCH_PATH: {"schema": 1, "files": file_specs},
        RESOURCE_PATCH_PATH: {"schema": 1, "operations": operations},
        REPORT_PATH: report,
        **file_documents,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_cdmod_zip(output_path, documents)
    _verify_package(output_path, file_specs, operations)

    return BuildResult(
        output_path=str(output_path),
        package_sha256=_sha256_file(output_path).upper(),
        package_bytes=output_path.stat().st_size,
        description_targets=len(DESCRIPTION_TARGETS),
        body_socket_targets=1,
        weapon_sidecar_targets=len(KNOWN_SIDECAR_TARGETS),
        weapon_prefab_targets=len(prefab_operations),
        main_prefab_targets=main_prefab_count,
        in_prefab_targets=in_prefab_count,
        animation_targets=len(ANIMATION_COPY_MAP),
        animation_lod_targets=len(ANIMATION_LOD_COPY_MAP),
        animation_meta_targets=len(PHW_ANIMATION_META_COPY_MAP),
        animation_chart_targets=1,
        file_replacements=len(file_specs),
        resource_operations=len(operations),
    )


def _validate_inputs(
    game_dir: Path,
    reference_mod_dir: Path,
    identity_reference: Path,
    output_path: Path,
) -> None:
    """校验所有输入边界，避免从错误游戏或同名参考包生成。"""
    if not (game_dir / "bin64" / "CrimsonDesert.exe").is_file():
        raise ValueError(f"不是有效游戏目录：{game_dir}")
    if not (game_dir / PAMT_DIR / "0.pamt").is_file():
        raise ValueError(f"缺少游戏 {PAMT_DIR}/0.pamt：{game_dir}")
    if not (game_dir / ANIMATION_META_PAMT_DIR / "0.pamt").is_file():
        raise ValueError(f"缺少游戏 {ANIMATION_META_PAMT_DIR}/0.pamt：{game_dir}")
    if not reference_mod_dir.is_dir():
        raise ValueError(f"旧作者参考目录不存在：{reference_mod_dir}")
    if not identity_reference.is_file():
        raise ValueError(f"双手剑身份参考包不存在：{identity_reference}")
    if output_path.suffix.casefold() != ".cdmod":
        raise ValueError("输出文件必须使用 .cdmod 后缀")


def _validate_identity_reference(identity_reference: Path) -> list[dict[str, object]]:
    """核对元素包报告，只把它作为当前双手剑物品身份的旁证。"""
    digest = _sha256_file(identity_reference)
    if digest.casefold() != IDENTITY_REFERENCE_SHA256:
        raise ValueError(
            "双手剑身份参考包哈希不一致："
            f"expected={IDENTITY_REFERENCE_SHA256} actual={digest}"
        )
    try:
        with zipfile.ZipFile(identity_reference) as archive:
            report = json.loads(archive.read(IDENTITY_REFERENCE_REPORT))
    except (KeyError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        raise ValueError(f"双手剑身份参考包报告无效：{exc}") from exc
    targets = report.get("targets") if isinstance(report, dict) else None
    if not isinstance(targets, list) or len(targets) != EXPECTED_IDENTITY_TARGET_COUNT:
        raise ValueError("双手剑身份参考包目标数量异常")
    normalized: list[dict[str, object]] = []
    for item in targets:
        if not isinstance(item, dict):
            raise ValueError("双手剑身份参考包包含非法目标")
        key = item.get("key")
        string_key = item.get("string_key")
        if (
            not isinstance(key, int)
            or isinstance(key, bool)
            or not isinstance(string_key, str)
            or not string_key.endswith("TwoHandSword")
        ):
            raise ValueError(f"身份参考目标不是双手剑：{item}")
        normalized.append({"key": key, "string_key": string_key})
    return normalized


def _validate_old_reference(reference_mod_dir: Path) -> None:
    """确认旧包确实包含双手剑腰挂意图，但不复用其过期资源字节。"""
    description_path = reference_mod_dir / REFERENCE_DESCRIPTION_PATH
    prefab_dir = reference_mod_dir / REFERENCE_TWOHAND_PREFAB_DIR
    body_socket_path = reference_mod_dir / REFERENCE_BODY_SOCKET_PATH
    if (
        not description_path.is_file()
        or not prefab_dir.is_dir()
        or not body_socket_path.is_file()
    ):
        raise ValueError("旧作者参考缺少 Kliff 描述、body socket 或双手剑 Prefab")
    description = description_path.read_bytes()
    required = (
        b'PartName="CD_TwoHandWeapon_Sword" InSocketBone="Pelvis_L_Socket"',
        b'PartName="CD_TwoHandWeapon_Sword_IN" InSocketBone="Pelvis_L_Socket"',
        b'PartName="CD_TwoHandWeapon_Sword" Visible="In"',
    )
    if any(description.count(fragment) != 1 for fragment in required):
        raise ValueError("旧作者参考的双手剑腰挂路由与已知版本不一致")
    body_socket = body_socket_path.read_bytes()
    if body_socket.count(REFERENCE_HIP_BODY_SUBWEAPON_SOCKET) != 1:
        raise ValueError("旧作者参考缺少已验证的 Pelvis SubWeapon 腰挂姿态")
    prefabs = sorted(prefab_dir.glob("cd_phm_02_sword_*.prefab"))
    if not prefabs:
        raise ValueError("旧作者参考没有双手剑 Prefab")
    if any("onehandweapon" in path.as_posix().casefold() for path in prefabs):
        raise ValueError("旧作者双手剑参考范围误入单手剑目录")


def _build_current_xml_payloads(
    entries_by_path: dict[str, PazEntry],
) -> tuple[dict[str, bytes], list[dict[str, object]], list[dict[str, object]]]:
    """从当前原版生成 PHM/PHW 五条双手剑 sidecar 完整载荷。"""
    discovered_sidecars = sorted(
        path for path in entries_by_path if path in KNOWN_SIDECAR_TARGETS
    )
    if set(discovered_sidecars) != KNOWN_SIDECAR_TARGETS:
        raise ValueError(
            "当前原版双手剑 sidecar 集合异常："
            f"expected={sorted(KNOWN_SIDECAR_TARGETS)} "
            f"actual={discovered_sidecars}"
        )

    documents: dict[str, bytes] = {}
    specs: list[dict[str, object]] = []
    audits: list[dict[str, object]] = []
    for index, target in enumerate(discovered_sidecars, start=1):
        vanilla = _read_exact_entry(entries_by_path, target)
        patched = _patch_weapon_sidecar_xml(vanilla, target)
        payload = f"{PAYLOAD_PREFIX}/{index:02d}-{Path(target).name}"
        documents[payload] = patched
        specs.append(
            {
                "target": target,
                "pamt_dir": PAMT_DIR,
                "payload": payload,
                "sha256": hashlib.sha256(patched).hexdigest(),
                "size": len(patched),
                "allow_new": False,
                "allow_table_replace": False,
            }
        )
        audits.append(
            {
                "target": target,
                "vanilla_size": len(vanilla),
                "vanilla_sha256": hashlib.sha256(vanilla).hexdigest(),
                "patched_size": len(patched),
                "patched_sha256": hashlib.sha256(patched).hexdigest(),
            }
        )
    return documents, specs, audits


def _build_body_socket_resource_replacements(
    vanilla: bytes,
) -> tuple[tuple[bytes, bytes], ...]:
    """用等长 XML 片段给 PHM 身体 SocketList 加入腰部长名称别名。

    该目标也会被现有双持单手剑包修改，因此不能使用会被后续资源变换覆盖的
    file-replacement。三个相邻自闭合标签去掉格式空白后，恰好可放回原来两条
    带六位小数和 CRLF 的标签长度，运行时可继续在双持结果上安全叠加。新增
    SubWeapon 别名使用旧模组明确验证的腰部专用姿态；这一级约 25 度旋转与
    sidecar 的 45 度旋转组合出目标腰挂角度，不能继承新版 Spine2 背部姿态。
    """
    alias_name = b"Pelvis_R_SubWeapon_Socket"
    if alias_name in vanilla:
        raise ValueError("当前 PHM 身体 socket 已存在 Pelvis_R_SubWeapon 别名")
    lines = vanilla.splitlines(keepends=True)
    pelvis_indexes = [
        index
        for index, line in enumerate(lines)
        if b'<Socket Name="Pelvis_R_Socket" Parent="B_WeaponIn_L_00"' in line
    ]
    if len(pelvis_indexes) != 1:
        raise ValueError("当前 PHM 身体表 Pelvis_R socket 不是唯一原版结构")
    index = pelvis_indexes[0]
    expected_following = (
        b'<Socket Name="Pelvis_L_Socket" Parent="B_WeaponIn_R_00"',
        b'<Socket Name="Physics_Parent_Pelvis_L_Socket" ',
        b'<Socket Name="Physics_Parent_Pelvis_R_Socket" ',
    )
    if index + len(expected_following) >= len(lines) or any(
        not lines[index + offset].lstrip().startswith(expected)
        for offset, expected in enumerate(expected_following, start=1)
    ):
        raise ValueError("当前 PHM 身体表 Pelvis_R/L 与 Physics Parent 顺序异常")

    compact_prefix = (
        b'<Socket Name="Pelvis_R_Socket" Parent="B_WeaponIn_L_00" '
        b'Rotation="0 0 0 1" Translation="0 0 0"/>'
        + HIP_BODY_SUBWEAPON_SOCKET
        + b'<Socket Name="Pelvis_L_Socket" Parent="B_WeaponIn_R_00" '
        b'Rotation="0 0 0 1" Translation="0 0 0"/>'
        b'<Socket Name="Physics_Parent_Pelvis_L_Socket" '
        b'Parent="B_WeaponIn_R_00" '
        b'Rotation="0 0 0 1" Translation="0 0 0"/>'
        + b'<Socket Name="Physics_Parent_Pelvis_R_Socket" '
        b'Parent="B_WeaponIn_L_00" '
        b'Rotation="0 0 0 1" Translation="0 0 0"/>'
    )
    donor_lines: list[bytes] = []
    donor_cursor = index + 4
    while True:
        old_block = b"".join(lines[index:donor_cursor])
        compact_sockets = compact_prefix + b"".join(
            line.strip() for line in donor_lines
        )
        if len(compact_sockets) <= len(old_block):
            break
        if len(donor_lines) >= BODY_SOCKET_MAX_PADDING_DONORS:
            raise ValueError("PHM 身体 socket 别名超过最大等长空白预算")
        if donor_cursor >= len(lines) or not lines[donor_cursor].lstrip().startswith(
            b"<Socket "
        ):
            raise ValueError("当前 PHM 身体表 Pelvis_R 后缺少可压缩的相邻 socket")
        donor_lines.append(lines[donor_cursor])
        donor_cursor += 1
    new_block = _pad_xml_fragment_to_length(compact_sockets, len(old_block))

    count_pattern = re.compile(rb'<SocketList Count="(\d+)">')
    count_matches = list(count_pattern.finditer(vanilla))
    if len(count_matches) != 1:
        raise ValueError("PHM 身体 SocketList Count 不是唯一值")
    count_match = count_matches[0]
    old_count = count_match.group(0)
    new_count = f'<SocketList Count="{int(count_match.group(1)) + 1}">'.encode("ascii")
    replacements = ((old_count, new_count), (old_block, new_block))
    _validate_byte_replacements(vanilla, replacements, BODY_SOCKET_TARGET)
    return replacements


def _pad_xml_fragment_to_length(fragment: bytes, target_length: int) -> bytes:
    """在标签块末尾补 XML 空白，保持 resource-transform 严格等长。"""
    padding = target_length - len(fragment)
    if padding < 0:
        raise ValueError(
            f"PHM 身体 socket 别名片段过长：{len(fragment)} > {target_length}"
        )
    return fragment + (b" " * padding)


def _patch_weapon_sidecar_xml(vanilla: bytes, target: str) -> bytes:
    """保留当前 sidecar 其他 socket，只加入两条腰部 child 别名。"""
    for alias in (b"Pelvis_R_ChildSocket", b"Pelvis_R_SubWeapon_ChildSocket"):
        if alias in vanilla:
            raise ValueError(f"双手剑 sidecar 已存在腰部别名：{target}")
    newline = _detect_newline(vanilla)
    indent_match = re.search(
        rb"(?m)^(?P<indent>[\t ]*)(?P<socket><Socket "
        rb'Name="Spine2_B_SubWeapon_ChildSocket"[^\r\n]*/>)(?=\r?$)',
        vanilla,
    )
    if indent_match is None:
        raise ValueError(f"双手剑 sidecar 无法确定唯一 Spine2 child：{target}")
    if (
        len(
            re.findall(
                rb'<Socket Name="Spine2_B_SubWeapon_ChildSocket"[^\r\n]*/>',
                vanilla,
            )
        )
        != 1
    ):
        raise ValueError(f"双手剑 sidecar Spine2 child 不是唯一节点：{target}")
    indent = indent_match.group("indent")
    pelvis_child = HIP_SIDECAR_SOCKET.replace(
        b"Spine2_B_SubWeapon_ChildSocket",
        b"Pelvis_R_ChildSocket",
        1,
    )
    pelvis_sub_child = HIP_SIDECAR_SOCKET.replace(
        b"Spine2_B_SubWeapon_ChildSocket",
        b"Pelvis_R_SubWeapon_ChildSocket",
        1,
    )
    replacement = (
        HIP_SIDECAR_SOCKET
        + newline
        + indent
        + pelvis_child
        + newline
        + indent
        + pelvis_sub_child
    )
    output = vanilla.replace(indent_match.group("socket"), replacement, 1)
    output = _increment_socket_list_count(output, 2)
    expected = (
        b"Spine2_B_SubWeapon_ChildSocket",
        b"Pelvis_R_ChildSocket",
        b"Pelvis_R_SubWeapon_ChildSocket",
    )
    if any(output.count(name) != 1 for name in expected):
        raise ValueError(f"双手剑 sidecar 腰部 child 写入数量异常：{target}")
    return output


def _increment_socket_list_count(content: bytes, increment: int) -> bytes:
    """严格更新唯一 SocketList Count，防止 XML 声明数量与节点数不一致。"""
    pattern = re.compile(rb'<SocketList Count="(\d+)">')
    matches = list(pattern.finditer(content))
    if len(matches) != 1:
        raise ValueError(f"SocketList Count 不是唯一值：matches={len(matches)}")
    match = matches[0]
    current = int(match.group(1))
    replacement = f'<SocketList Count="{current + increment}">'.encode("ascii")
    return content[: match.start()] + replacement + content[match.end() :]


def _detect_newline(content: bytes) -> bytes:
    """保持当前原版 XML 的换行格式。"""
    return b"\r\n" if b"\r\n" in content else b"\n"


def _build_description_replacements(
    content: bytes,
    target: str = DESCRIPTION_TARGET,
) -> tuple[tuple[bytes, bytes], ...]:
    """生成两条腰部路由，并等长恢复已验证的主剑 Visible=In。"""
    if target == PHW_PLAYER_DESCRIPTION_TARGET:
        current_lines = PHW_DESCRIPTION_CURRENT_LINES
        hip_lines = PHW_DESCRIPTION_HIP_LINES
    elif target in (DESCRIPTION_TARGET, PHM_PLAYER_DESCRIPTION_TARGET):
        current_lines = DESCRIPTION_CURRENT_LINES
        hip_lines = DESCRIPTION_HIP_LINES
    else:
        raise ValueError(f"不支持的双手剑玩家描述目标：{target}")
    replacements = tuple(
        (
            current.encode("utf-8"),
            _pad_xml_tag_to_length(hip.encode("utf-8"), len(current.encode("utf-8"))),
        )
        for current, hip in zip(
            current_lines,
            hip_lines,
            strict=True,
        )
    )
    _validate_byte_replacements(content, replacements, target)
    return replacements


def _patch_phw_longsword_normal_sheathe_transition(vanilla: bytes) -> bytes:
    """把 PHW 双手剑正常站立的已定位 PartInOut transition 延后到 1.5 秒。"""
    digest = hashlib.sha256(vanilla).hexdigest()
    if (
        len(vanilla) != EXPECTED_WEAPON_IN_ACTIONCHART_SIZE
        or digest != EXPECTED_WEAPON_IN_ACTIONCHART_SHA256
    ):
        raise ValueError(
            "basic_upper_weaponin.paac 原版身份不匹配："
            f"size={len(vanilla)} sha256={digest}"
        )

    old_transition = struct.pack(
        "<ffII",
        PHW_LSWD_NORMAL_SHEATHE_OLD_TIME,
        -1.0,
        PHW_LSWD_NORMAL_SHEATHE_TARGET,
        PHW_LSWD_NORMAL_SHEATHE_SEQUENCE,
    )
    if vanilla.count(old_transition) != len(
        PHW_LSWD_NORMAL_SHEATHE_TRANSITION_OFFSETS
    ):
        raise ValueError("PHW lswd 正常站立 PartInOut transition 数量异常")

    patched = bytearray(vanilla)
    for offset in PHW_LSWD_NORMAL_SHEATHE_TRANSITION_OFFSETS:
        if vanilla[offset : offset + len(old_transition)] != old_transition:
            raise ValueError(f"PHW lswd PartInOut transition 偏移异常：0x{offset:x}")
        patched[offset : offset + 4] = struct.pack(
            "<f", PHW_LSWD_NORMAL_SHEATHE_NEW_TIME
        )

    changed_offsets = [
        index
        for index, (old, new) in enumerate(zip(vanilla, patched, strict=True))
        if old != new
    ]
    allowed_offsets = {
        offset + delta
        for offset in PHW_LSWD_NORMAL_SHEATHE_TRANSITION_OFFSETS
        for delta in range(4)
    }
    if not changed_offsets or not set(changed_offsets).issubset(allowed_offsets):
        raise ValueError("PHW lswd PartInOut transition 出现越界字节变化")
    return bytes(patched)


def _pad_xml_tag_to_length(tag: bytes, target_length: int) -> bytes:
    """把空格放在 ``/>`` 前，使 XML 标签等长且不污染属性值。"""
    if not tag.endswith(b"/>"):
        raise ValueError("只能填充自闭合 XML 标签")
    padding = target_length - len(tag)
    if padding < 0:
        raise ValueError(f"腰挂 XML 标签比当前原版更长：{len(tag)} > {target_length}")
    return tag[:-2] + (b" " * padding) + b"/>"


def _build_prefab_operations(
    entries_by_path: dict[str, PazEntry],
) -> tuple[list[dict[str, object]], int, int]:
    """动态发现当前版本全部 PHM/PHW 双手剑 Prefab 并生成等长变换。"""
    operations: list[dict[str, object]] = []
    main_count = 0
    in_count = 0
    candidates = [
        (path, entry)
        for path, entry in sorted(entries_by_path.items())
        if "/weapon/02_twohandweapon/" in path
        and Path(path).name.startswith(("cd_phm_02_sword_", "cd_phw_02_sword_"))
        and path.endswith(".prefab")
    ]
    if not candidates:
        raise ValueError("当前原版没有发现 PHM/PHW 双手剑 Prefab")
    for target, entry in candidates:
        content = extract_plaintext(entry)[0]
        if all(content.count(old) == 1 for old, _new in MAIN_PREFAB_REPLACEMENTS):
            replacements = MAIN_PREFAB_REPLACEMENTS
            main_count += 1
        elif all(content.count(old) == 1 for old, _new in IN_PREFAB_REPLACEMENTS):
            replacements = IN_PREFAB_REPLACEMENTS
            in_count += 1
        else:
            raise ValueError(f"双手剑 Prefab socket 结构不属于当前两种模式：{target}")
        _validate_byte_replacements(content, replacements, target)
        operations.append(_replace_operation(target, replacements))
    return operations, main_count, in_count


def _validate_required_animation_entries(
    entries_by_path: dict[str, PazEntry],
    animation_meta_entries_by_path: dict[str, PazEntry],
) -> None:
    """确保 PAA/LOD/metabin 均来自当前原版且目标仅属于双手剑。"""
    animation_map = {**ANIMATION_COPY_MAP, **ANIMATION_LOD_COPY_MAP}
    for target, source in animation_map.items():
        target_name = Path(target).name.casefold()
        if "dlsd" in target_name or "dualsword" in target_name:
            raise ValueError(f"双手剑动作目标误入双持单手剑：{target}")
        if not any(
            marker in target_name for marker in ("longsword", "lswd", "cd_prh_lswd")
        ):
            raise ValueError(f"动作目标不属于双手大剑：{target}")
        for path in (target, source):
            if path.casefold() not in entries_by_path:
                raise ValueError(f"当前原版缺少动作 entry：{path}")

    for source in {
        *PHW_ANIMATION_COPY_MAP.values(),
        *PHW_ANIMATION_LOD_COPY_MAP.values(),
    }:
        if "pistol" in source.casefold():
            raise ValueError(f"PHW PAA 来源不得使用 pistol：{source}")
        if "damian_2rpr" in source.casefold():
            raise ValueError(f"PHW PAA 来源不得使用未被 PAAC 引用的 2rpr：{source}")
        if not any(marker in source.casefold() for marker in ("rpr", "2rpr")):
            raise ValueError(f"PHW PAA 来源不属于达米安细剑链：{source}")

    for target, source in PHW_ANIMATION_META_COPY_MAP.items():
        if "pistol" in source.casefold():
            raise ValueError(f"PHW metabin 来源不得使用 pistol：{source}")
        if "damian_2rpr" in source.casefold():
            raise ValueError(f"PHW metabin 来源不得使用未被 PAAC 引用的 2rpr：{source}")
        if not any(marker in source.casefold() for marker in ("rpr", "2rpr")):
            raise ValueError(f"PHW metabin 来源不属于达米安细剑链：{source}")
        for path in (target, source):
            if path.casefold() not in animation_meta_entries_by_path:
                raise ValueError(f"当前原版缺少动作 metabin entry：{path}")


def _validate_byte_replacements(
    content: bytes,
    replacements: tuple[tuple[bytes, bytes], ...],
    target: str,
) -> None:
    """确认规则唯一、等长，并且目标值尚未出现在当前输入。"""
    output = content
    for old, new in replacements:
        if len(old) != len(new):
            raise ValueError(f"资源规则不是等长替换：{target}")
        if output.count(old) != 1 or output.count(new) != 0:
            raise ValueError(
                f"资源规则不是唯一原版状态：{target} "
                f"old={output.count(old)} new={output.count(new)}"
            )
        output = output.replace(old, new, 1)
    if len(output) != len(content):
        raise ValueError(f"资源变换改变了原版长度：{target}")


def _replace_operation(
    target: str,
    replacements: tuple[tuple[bytes, bytes], ...],
    *,
    target_pamt_dir: str = PAMT_DIR,
) -> dict[str, object]:
    """把已验证规则转换为 cdmod ``replace-bytes`` 操作。"""
    if any(len(old) != len(new) for old, new in replacements):
        raise ValueError(f"replace-bytes 规则不等长：{target}")
    return {
        "op": "replace-bytes",
        "target": target,
        "target_pamt_dir": target_pamt_dir,
        "replacements": [
            {"old_hex": old.hex(), "new_hex": new.hex()} for old, new in replacements
        ],
    }


def _copy_operation(
    target: str,
    source: str,
    *,
    target_pamt_dir: str = PAMT_DIR,
    source_pamt_dir: str = PAMT_DIR,
) -> dict[str, object]:
    """生成只读取当前原版 source 的跨 PAMT entry 复制操作。"""
    return {
        "op": "copy-entry",
        "target": target,
        "target_pamt_dir": target_pamt_dir,
        "source": source,
        "source_pamt_dir": source_pamt_dir,
    }


def _build_manifest(
    *,
    file_count: int,
    operation_count: int,
    prefab_count: int,
    animation_count: int,
    animation_lod_count: int,
    animation_meta_count: int,
) -> dict[str, object]:
    """生成同时包含当前 XML 与增量资源变换的最小 manifest。"""
    return {
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": PACKAGE_ID,
        "name": PACKAGE_NAME,
        "version": PACKAGE_VERSION,
        "author": "Ratrider; current-version compatibility rebuild by cdmm",
        "description": (
            "Current-game PHM/PHW two-handed sword hip carry with standing, "
            "seated, moving, and mounted draw/sheathe animations."
        ),
        "dependencies": [],
        "source": {
            "format": "current-game-twohand-sword-hip-chain",
            "game_version": "1.15",
            "pamt_dir": PAMT_DIR,
            "prefab_transform_targets": prefab_count,
            "animation_copy_targets": animation_count,
            "animation_lod_copy_targets": animation_lod_count,
            "animation_meta_copy_targets": animation_meta_count,
            "full_old_resources_copied": 0,
            "cc_scope": "PHM-PHW-player-weapon-chains",
        },
        "components": [
            {
                "type": CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE,
                "path": FILE_PATCH_PATH,
                "file_count": file_count,
            },
            {
                "type": CDMOD_RESOURCE_TRANSFORM_COMPONENT_TYPE,
                "path": RESOURCE_PATCH_PATH,
                "operation_count": operation_count,
            },
        ],
    }


def _validate_operation_targets_unique(operations: list[dict[str, object]]) -> None:
    """一个 resource-transform 组件内每个最终目标必须唯一。"""
    targets = [str(operation["target"]).casefold() for operation in operations]
    if len(targets) != len(set(targets)):
        duplicates = sorted(
            target for target in set(targets) if targets.count(target) > 1
        )
        raise ValueError(f"资源变换目标重复：{duplicates}")


def _verify_package(
    output_path: Path,
    file_specs: list[dict[str, object]],
    operations: list[dict[str, object]],
) -> None:
    """使用正式解析器回读并禁止单手剑、双持和 motionblending 混入。"""
    package = load_cdmod_package(output_path)
    if (
        package.dependencies
        or package.standalone_archives
        or package.legacy_json_patches
    ):
        raise ValueError("成品不能包含依赖、standalone 或传统 JSON")
    parsed_files = [item for patch in package.file_patches for item in patch.files]
    parsed_operations = [
        operation
        for patch in package.resource_patches
        for operation in patch.operations
    ]
    if len(parsed_files) != len(file_specs):
        raise ValueError("XML file-replacement 回读数量异常")
    if len(parsed_operations) != len(operations):
        raise ValueError("resource-transform 回读数量异常")
    allowed_file_targets = {
        *((PAMT_DIR, target) for target in KNOWN_SIDECAR_TARGETS),
        (ANIMATION_META_PAMT_DIR, WEAPON_IN_ACTIONCHART_TARGET),
    }
    for item in parsed_files:
        identity = (item.pamt_dir, item.target.casefold())
        if identity not in allowed_file_targets:
            raise ValueError(f"成品完整文件目标异常：{item.pamt_dir}/{item.target}")
    for operation in parsed_operations:
        target = operation.target.casefold()
        target_name = Path(target).name
        if "/01_onehandweapon/" in target:
            raise ValueError(f"成品误改单手剑 Prefab：{operation.target}")
        if "dlsd" in target_name or "dualsword" in target_name:
            raise ValueError(f"成品误改双持单手剑动作：{operation.target}")
        if target.endswith(".motionblending"):
            raise ValueError(f"成品误入 motionblending：{operation.target}")
        if target.endswith(".paa_metabin"):
            if (
                operation.op != "copy-entry"
                or operation.target_pamt_dir != ANIMATION_META_PAMT_DIR
                or operation.source_pamt_dir != ANIMATION_META_PAMT_DIR
                or operation.source is None
                or "pistol" in operation.source.casefold()
                or not any(
                    marker in operation.source.casefold() for marker in ("rpr", "2rpr")
                )
            ):
                raise ValueError(f"成品 PHW metabin 资源对异常：{operation}")
        elif target.endswith(".paa") and (
            operation.target_pamt_dir != PAMT_DIR
            or operation.source_pamt_dir != PAMT_DIR
        ):
            raise ValueError(f"成品 PAA 的 PAMT 目录异常：{operation}")
        if operation.op == "replace-bytes" and not (
            target in DESCRIPTION_TARGETS
            or target == BODY_SOCKET_TARGET
            or (
                "/weapon/02_twohandweapon/" in target
                and target_name.startswith(("cd_phm_02_sword_", "cd_phw_02_sword_"))
                and target.endswith(".prefab")
            )
        ):
            raise ValueError(f"成品 replace-bytes 目标超出双手剑链：{operation.target}")


def _entry_final_path(entry: PazEntry) -> str:
    """按 PAMT folder record 与 basename 还原规范最终路径。"""
    parent = (entry.resolved_dir_path or "").replace("\\", "/").strip("/")
    basename = Path(entry.path).name
    path = (
        f"{parent}/{basename}" if parent else entry.path.replace("\\", "/").strip("/")
    )
    return path.casefold()


def _read_exact_entry(entries_by_path: dict[str, PazEntry], target: str) -> bytes:
    """只按当前原版规范最终路径读取目标，不接受旧别名 basename 污染。"""
    entry = entries_by_path.get(target.casefold())
    if entry is None:
        raise ValueError(f"当前原版缺少精确目标：{target}")
    return extract_plaintext(entry)[0]


def _sha256_file(path: Path) -> str:
    """流式计算文件 SHA-256，避免对大型参考包一次性分配内存。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description="生成 PHM/CC 兼容的双手大剑腰挂与拔刀/收刀 .cdmod"
    )
    parser.add_argument("--game-dir", type=Path, required=True)
    parser.add_argument("--reference-mod-dir", type=Path, required=True)
    parser.add_argument("--identity-reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    """执行构建并输出 UTF-8 JSON 摘要。"""
    args = _parse_args()
    result = build_twohand_sword_hip_carry_mod(
        args.game_dir,
        args.reference_mod_dir,
        args.identity_reference,
        args.output,
    )
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
