"""生成 CC 性转玩家专用的全单手剑双持背挂 ``.cdmod``。

本工具只把当前游戏 1.15 原版资源作为运行时基底：角色描述使用通用
``CD_MainWeapon_Sword_R/L`` 路由，PHM 玩家一手剑 Prefab 只做等长 socket 名
替换，共享 sidecar 只改两条 child socket，动画使用当前版本原生 entry 的
``copy-entry`` 映射。PHW 达米安描述、身体 socket、细剑 sidecar 和 Prefab
全部保持原版；不会复制旧版完整描述、Prefab/PAA，也不会修改游戏原始 PAZ、
PAMT 或 meta 文件。
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from cdmm.archive.pamt import parse_pamt
from cdmm.common.models import PazEntry
from cdmm.services.cdmod_converter import (
    CDMOD_FORMAT_NAME,
    CDMOD_FORMAT_VERSION,
    CDMOD_RESOURCE_TRANSFORM_COMPONENT_TYPE,
    _write_cdmod_zip,
)
from cdmm.services.cdmod_package import load_cdmod_package
from cdmm.services.json_loader import extract_plaintext

# 角色描述、武器 Prefab、socket 和动作均位于当前游戏的 0009 包。
PAMT_DIR = "0009"

# cdmod 只承载当前原版上的等长资源变换。
RESOURCE_PATCH_PATH = "patches/resources.json"

# 成品身份固定，便于加载顺序和缓存稳定识别。
PACKAGE_ID = "cdmm.dual-onehand-swords-back-carry-cc"
PACKAGE_NAME = "Dual One-Hand Swords Back Carry - CC Player Only"
PACKAGE_VERSION = "1.15.32"

# 当前 1.15 原版中，227 个 PHM 玩家单手剑 Prefab 与 5 个 PHM weapon sidecar
# 必须被覆盖。PHW 属于达米安细剑链，禁止进入 CC 玩家专用成品。
EXPECTED_ONEHAND_PREFAB_TARGETS = 227
EXPECTED_ONEHAND_SIDECAR_TARGETS = 5

# 两份男性描述覆盖普通 PHM 与 Kliff 专用分支。
PHM_DESCRIPTION_TARGETS = (
    "character/descriptors/characterdescription/phm_description_player_001.xml",
    "character/descriptors/characterdescription/phm_description_player_kliff.xml",
)

# CC 性转只改变玩家外观选择，武器装配仍走 PHM Kliff 链。
PHM_BODY_SOCKET_TARGET = (
    "character/descriptors/socketbonedata/1_pc/1_phm/phm_01.pab.sockets.xml"
)

# 四条通用双持单手剑路由必须与 Prefab 内嵌 socket 和 weapon sidecar 同步。
PHM_DESCRIPTION_REPLACEMENTS = (
    (
        '<PartInOutSocket PartName="CD_MainWeapon_Sword_R" InSocketBone="Pelvis_L_Socket" OutSocketBone="RHand_Socket" InChildSocketBone="Pelvis_L_ChildSocket" OutChildSocketBone="Basic_ChildSocket" WeaponCasePart="CD_MainWeapon_Sword_IN_R"/>',
        '<PartInOutSocket PartName="CD_MainWeapon_Sword_R" InSocketBone="Spine1_B_Socket" OutSocketBone="RHand_Socket" InChildSocketBone="Spine1_B_ChildSocket" OutChildSocketBone="Basic_ChildSocket" WeaponCasePart="CD_MainWeapon_Sword_IN_R"/>',
    ),
    (
        '<PartInOutSocket PartName="CD_MainWeapon_Sword_IN_R" InSocketBone="Pelvis_L_Socket" OutSocketBone="Pelvis_L_Socket" InChildSocketBone="Pelvis_L_ChildSocket" OutChildSocketBone="Pelvis_L_ChildSocket"/>',
        '<PartInOutSocket PartName="CD_MainWeapon_Sword_IN_R" InSocketBone="Spine1_B_Socket" OutSocketBone="Spine1_B_Socket" InChildSocketBone="Spine1_B_ChildSocket" OutChildSocketBone="Spine1_B_ChildSocket"/>',
    ),
    (
        '<PartInOutSocket PartName="CD_MainWeapon_Sword_L" InSocketBone="Pelvis_R_Socket" OutSocketBone="LHand_Socket" InChildSocketBone="Pelvis_R_ChildSocket" OutChildSocketBone="Basic_ChildSocket" WeaponCasePart="CD_MainWeapon_Sword_IN_L"/>',
        '<PartInOutSocket PartName="CD_MainWeapon_Sword_L" InSocketBone="Spine0_B_Socket" OutSocketBone="LHand_Socket" InChildSocketBone="Spine0_B_ChildSocket" OutChildSocketBone="Basic_ChildSocket" WeaponCasePart="CD_MainWeapon_Sword_IN_L"/>',
    ),
    (
        '<PartInOutSocket PartName="CD_MainWeapon_Sword_IN_L" InSocketBone="Pelvis_R_Socket" OutSocketBone="Pelvis_R_Socket" InChildSocketBone="Pelvis_R_ChildSocket" OutChildSocketBone="Pelvis_R_ChildSocket"/>',
        '<PartInOutSocket PartName="CD_MainWeapon_Sword_IN_L" InSocketBone="Spine0_B_Socket" OutSocketBone="Spine0_B_Socket" InChildSocketBone="Spine0_B_ChildSocket" OutChildSocketBone="Spine0_B_ChildSocket"/>',
    ),
)

# 兼容 Human Female standalone 精确重打包工具的公共常量。玩家专用成品
# 禁止调用该 PHW 规则；它只供独立的历史重打包工具显式使用。
PHW_DESCRIPTION_REPLACEMENTS = PHM_DESCRIPTION_REPLACEMENTS

# 13:18 的不可变 VFS 快照已实机证明 Spine1/0 Prefab + child 链会改变姿态。
# Spine0 已归到 Spine1 的同一父骨，身体层保留实机确认方向正确的 1.15.5
# 基线；左右武器资源仍需在 weapon child 层分别补偿本地轴差异。
BODY_SOCKET_REPLACEMENTS = (
    (
        b'<Socket Name="Spine0_B_Socket" Parent="Bip_Weapon_Attach_In_00" Rotation="0.000000 0.000000 0.000000 1.000000" Translation="0.000000 0.000000 0.000000"/>',
        b'<Socket Name="Spine0_B_Socket" Parent="Bip_Weapon_Attach_In_01" Rotation="0.000000 0.000000 0.000000 1.000000" Translation="-0.08000 -0.07500 0.030000"/>',
    ),
)

# Prefab 的四个 socket 名新旧长度一致，只改变内嵌路由，不复制二进制结构。
ONEHAND_PREFAB_REPLACEMENTS = (
    (b"Pelvis_L_Socket", b"Spine1_B_Socket"),
    (b"Pelvis_L_ChildSocket", b"Spine1_B_ChildSocket"),
    (b"Pelvis_R_Socket", b"Spine0_B_Socket"),
    (b"Pelvis_R_ChildSocket", b"Spine0_B_ChildSocket"),
)

# PHM sidecar 的原位移是 -0.15，PHW 是 -0.20。左右武器资源的本地轴存在
# 视觉差异，因此用工具将上剑、下剑分别换算为约 35.6、37.1 度倾角；
# 下剑额外补偿 1.5 度后与上剑在画面中接近平行。禁止再根据 Spine0/Spine1、
# 左右手路由或单张截图给两把可见武器命名；1.15.22 至 1.15.25 曾因错误
# 视觉身份映射连续改错目标。完整版本参数、用户反馈和后续单变量标定规则见
# ``docs/双持单手剑背挂调整记录.md``。1.15.26/1.15.27 证明 Spine1 的 Y
# 会显著改变前后间距和画面投影，不能作为纯高度轴。1.15.28 完整恢复
# 1.15.23 基线，只把 Spine1 的 Z 增加 0.060；该幅度和方向就是 1.15.24
# 曾错误施加到 Spine0 的高度修正，现在改到实机确认控制金剑的 Spine1。
# 1.15.28 已实机确认金剑向下但幅度不足；1.15.29 以它为新基础，只让
# Spine1 的 Z 再增加 0.060，不改变已经稳定的任何其他参数。
# 1.15.29 已实机确认为当前最佳版本，金剑 Spine1 从此锁定。1.15.30 只改
# 黑剑 Spine0：Y 增加 0.040 拉大前后层次，X 减少 0.020 缩小横向间距。
# 1.15.31 按用户指定值微调黑剑：X 减少 0.005，Y 减少 0.026；金剑
# Spine1、黑剑 Z 和两条旋转继续保持不变。
ONEHAND_SIDECAR_REPLACEMENT_CANDIDATES = (
    (
        b'<Socket Name="Pelvis_R_ChildSocket" Parent="B_Weapon_0001" Rotation="0.000000 0.000000 0.000000 1.000000" Translation="0.000000 0.000000 -0.150000"/>',
        b'<Socket Name="Spine0_B_ChildSocket" Parent="B_Weapon_0001" Rotation="-0.69348 -0.21269 0.236657 -0.64641" Translation="-0.11000 -0.04000 -0.636000"/>',
    ),
    (
        b'<Socket Name="Pelvis_L_ChildSocket" Parent="B_Weapon_0001" Rotation="0.000000 0.000000 0.000000 1.000000" Translation="0.000000 0.000000 -0.150000"/>',
        b'<Socket Name="Spine1_B_ChildSocket" Parent="B_Weapon_0001" Rotation="-0.69640 -0.20417 0.227599 -0.64926" Translation="-0.14000 -0.07500 -0.570000"/>',
    ),
    (
        b'<Socket Name="Pelvis_R_ChildSocket" Parent="B_Weapon_0001" Rotation="0.000000 0.000000 0.000000 1.000000" Translation="0.000000 0.000000 -0.200000"/>',
        b'<Socket Name="Spine0_B_ChildSocket" Parent="B_Weapon_0001" Rotation="-0.69348 -0.21269 0.236657 -0.64641" Translation="-0.11000 -0.04000 -0.636000"/>',
    ),
    (
        b'<Socket Name="Pelvis_L_ChildSocket" Parent="B_Weapon_0001" Rotation="0.000000 0.000000 0.000000 1.000000" Translation="0.000000 0.000000 -0.200000"/>',
        b'<Socket Name="Spine1_B_ChildSocket" Parent="B_Weapon_0001" Rotation="-0.69640 -0.20417 0.227599 -0.64926" Translation="-0.14000 -0.07500 -0.570000"/>',
    ),
)

# 作者的一手剑动画目标全部保留 dlsd/dualsword 名称；source 只读取当前版本原生动作。
PHM_ANIMATION_COPY_MAP = {
    "character/motion/1_pc/1_phm/cd_phm_dlsd_00_01_nor_std_weapon_in_longtype_00.paa":
        "character/motion/1_pc/1_phm/cd_phm_lswd_00_01_nor_std_weapon_in_longtype_00.paa",
    "character/motion/1_pc/1_phm/cd_phm_dlsd_00_01_sit_std_weapon_in_00.paa":
        "character/motion/1_pc/1_phm/cd_phm_lswd_00_01_sit_std_weapon_in_00.paa",
    "character/motion/1_pc/1_phm/cd_phm_dlsd_00_01_sit_std_weapon_out_00.paa":
        "character/motion/1_pc/1_phm/cd_phm_lswd_00_01_sit_std_weapon_out_00.paa",
    "character/motion/1_pc/1_phm/cd_phm_dlsd_01_00_nor_std_weapon_in_00.paa":
        "character/motion/1_pc/1_phm/cd_phm_sword_00_01_normal_stand_weapon_in_000.paa",
    "character/motion/1_pc/1_phm/cd_phm_dlsd_01_01_alert_nor_std_weapon_out_00.paa":
        "character/motion/1_pc/1_phm/cd_phm_lswd_01_01_alert_nor_std_weapon_out_00.paa",
    "character/motion/1_pc/1_phm/cd_phm_dualsword_00_00_normal_move_run_f_weapon_out_000.paa":
        "character/motion/1_pc/1_phm/cd_phm_longsword_00_00_normal_move_run_f_weapon_out_000.paa",
    "character/motion/1_pc/1_phm/cd_phm_dualsword_00_01_nor_stand_weapon_out_00.paa":
        "character/motion/1_pc/1_phm/cd_phm_longsword_00_01_normal_stand_weapon_out_000.paa",
    "character/motion/1_pc/1_phm/00_riding/cd_prh_dlsd_01_01_nor_std_weapon_in_00.paa":
        "character/motion/1_pc/1_phm/00_riding/cd_prh_lswd_01_01_nor_std_weapon_in_00.paa",
    "character/motion/1_pc/1_phm/00_riding/cd_prh_dlsd_01_01_nor_std_weapon_out_00.paa":
        "character/motion/1_pc/1_phm/00_riding/cd_prh_lswd_01_01_nor_std_weapon_out_00.paa",
}

@dataclass(frozen=True)
class BuildResult:
    """记录成品路径、指纹和各层修改数量。"""

    output_path: str
    package_sha256: str
    package_bytes: int
    description_targets: int
    body_socket_targets: int
    weapon_sidecar_targets: int
    weapon_prefab_targets: int
    animation_targets_phm: int
    resource_operations: int


def build_dual_onehand_swords_back_carry_mod(
    game_dir: Path,
    output_path: Path,
) -> BuildResult:
    """审计当前原版后生成 CC 性转玩家专用增量包。"""
    game_dir = game_dir.resolve()
    output_path = output_path.resolve()
    _validate_inputs(game_dir, output_path)

    entries = parse_pamt(
        game_dir / PAMT_DIR / "0.pamt",
        paz_dir=game_dir / PAMT_DIR,
    )
    entries_by_path = {_entry_final_path(entry): entry for entry in entries}
    _validate_required_animation_entries(entries_by_path)

    operations: list[dict[str, object]] = []
    for target in PHM_DESCRIPTION_TARGETS:
        vanilla = _read_exact_entry(entries_by_path, target)
        _validate_text_replacements(vanilla, PHM_DESCRIPTION_REPLACEMENTS, target)
        operations.append(_replace_operation(target, PHM_DESCRIPTION_REPLACEMENTS))
    body_socket_targets = (PHM_BODY_SOCKET_TARGET,)
    for target in body_socket_targets:
        vanilla = _read_exact_entry(entries_by_path, target)
        _validate_byte_replacements(vanilla, BODY_SOCKET_REPLACEMENTS, target)
        operations.append(_replace_operation(target, BODY_SOCKET_REPLACEMENTS))

    sidecar_operations, prefab_operations = _build_weapon_socket_operations(
        entries_by_path
    )
    operations.extend(sidecar_operations)
    operations.extend(prefab_operations)

    for target, source in PHM_ANIMATION_COPY_MAP.items():
        operations.append(
            {
                "op": "copy-entry",
                "target": target,
                "target_pamt_dir": PAMT_DIR,
                "source": source,
                "source_pamt_dir": PAMT_DIR,
            }
        )

    _validate_operation_targets_unique(operations)
    manifest = _build_manifest(
        operation_count=len(operations),
        body_socket_target_count=len(body_socket_targets),
        weapon_sidecar_target_count=len(sidecar_operations),
        weapon_prefab_target_count=len(prefab_operations),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_cdmod_zip(
        output_path,
        {
            "manifest.json": manifest,
            RESOURCE_PATCH_PATH: {"schema": 1, "operations": operations},
        },
    )
    _verify_package(output_path, operations)
    return BuildResult(
        output_path=str(output_path),
        package_sha256=_sha256_file(output_path).upper(),
        package_bytes=output_path.stat().st_size,
        description_targets=len(PHM_DESCRIPTION_TARGETS),
        body_socket_targets=len(body_socket_targets),
        weapon_sidecar_targets=len(sidecar_operations),
        weapon_prefab_targets=len(prefab_operations),
        animation_targets_phm=len(PHM_ANIMATION_COPY_MAP),
        resource_operations=len(operations),
    )


def _validate_inputs(
    game_dir: Path,
    output_path: Path,
) -> None:
    """校验路径边界，避免从错误游戏目录生成。"""
    if not (game_dir / "bin64" / "CrimsonDesert.exe").is_file():
        raise ValueError(f"不是有效游戏目录：{game_dir}")
    if not (game_dir / PAMT_DIR / "0.pamt").is_file():
        raise ValueError(f"缺少游戏 {PAMT_DIR}/0.pamt：{game_dir}")
    if output_path.suffix.casefold() != ".cdmod":
        raise ValueError("输出文件必须使用 .cdmod 后缀")


def _validate_required_animation_entries(
    entries_by_path: dict[str, PazEntry],
) -> None:
    """确保动画 target/source 都来自当前 1.15 原版 PAMT。"""
    for target, source in PHM_ANIMATION_COPY_MAP.items():
        for path in (target, source):
            if path.casefold() not in entries_by_path:
                raise ValueError(f"当前原版缺少动作 entry：{path}")


def _build_weapon_socket_operations(
    entries_by_path: dict[str, PazEntry],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """为 PHM 玩家含挂载字段的单手剑资源生成窄范围变换。"""
    sidecar_operations: list[dict[str, object]] = []
    prefab_operations: list[dict[str, object]] = []
    for target, entry in sorted(entries_by_path.items()):
        if not _is_phm_player_weapon_target(target):
            continue
        basename = Path(target).name
        is_sword = basename.startswith("cd_phm_01_sword_")
        if not is_sword:
            continue
        if "/weapon/1_onehandweapon/" in target and target.endswith(".sockets.xml"):
            content = extract_plaintext(entry)[0]
            replacements = tuple(
                (old, new)
                for old, new in ONEHAND_SIDECAR_REPLACEMENT_CANDIDATES
                if content.count(old) == 1
            )
            if len(replacements) != 2:
                raise ValueError(
                    f"单手剑 sidecar 没有唯一左右 Pelvis child：{target} "
                    f"matches={len(replacements)}"
                )
            _validate_byte_replacements(content, replacements, target)
            sidecar_operations.append(_replace_operation(target, replacements))
            continue
        if "/weapon/01_onehandweapon/" not in target or not target.endswith(".prefab"):
            continue
        content = extract_plaintext(entry)[0]
        replacements = tuple(
            (old, new)
            for old, new in ONEHAND_PREFAB_REPLACEMENTS
            if content.count(old) > 0
        )
        if not replacements:
            # 0015/0016/0104 等特殊 Prefab 原本没有内嵌挂载字段，由共享链处理。
            continue
        _validate_prefab_replacements(content, replacements, target)
        prefab_operations.append(_replace_operation(target, replacements))

    if len(sidecar_operations) != EXPECTED_ONEHAND_SIDECAR_TARGETS:
        raise ValueError(
            f"单手剑 sidecar 覆盖数量异常：{len(sidecar_operations)} "
            f"!= {EXPECTED_ONEHAND_SIDECAR_TARGETS}"
        )
    if len(prefab_operations) != EXPECTED_ONEHAND_PREFAB_TARGETS:
        raise ValueError(
            f"单手剑 Prefab 覆盖数量异常：{len(prefab_operations)} "
            f"!= {EXPECTED_ONEHAND_PREFAB_TARGETS}"
        )
    return sidecar_operations, prefab_operations


def _is_phm_player_weapon_target(target: str) -> bool:
    """只允许 PHM 玩家武器资源，防止达米安 PHW 细剑被改为背挂。"""
    normalized = target.replace("\\", "/").casefold()
    phm_directory = "/1_phm/" in normalized or "/01_phm/" in normalized
    return phm_directory and "cd_phm_01_sword_" in Path(normalized).name


def _validate_text_replacements(
    content: bytes,
    replacements: tuple[tuple[str, str], ...],
    target: str,
) -> None:
    """确认每条描述旧行唯一且替换等长，不允许全表模糊替换。"""
    output = content
    for old_text, new_text in replacements:
        old = old_text.encode("utf-8")
        new = new_text.encode("utf-8")
        if len(old) != len(new):
            raise ValueError(f"描述规则不是等长替换：{target}")
        if output.count(old) != 1 or output.count(new) != 0:
            raise ValueError(
                f"描述行不是唯一原版状态：{target} "
                f"old={output.count(old)} new={output.count(new)}"
            )
        output = output.replace(old, new, 1)
    if b"CD_TwoHandWeapon_Sword" in b"".join(
        new.encode("utf-8") for _old, new in replacements
    ):
        raise ValueError("描述规则误入真正双手剑链")


def _validate_byte_replacements(
    content: bytes,
    replacements: tuple[tuple[bytes, bytes], ...],
    target: str,
) -> None:
    """确认身体 socket 规则唯一、等长，且不会形成整表替换。"""
    output = content
    for old, new in replacements:
        if len(old) != len(new):
            raise ValueError(f"socket 规则不是等长替换：{target}")
        if output.count(old) != 1 or output.count(new) != 0:
            raise ValueError(
                f"socket 规则不是唯一原版状态：{target} "
                f"old={output.count(old)} new={output.count(new)}"
            )
        output = output.replace(old, new, 1)
    if len(output) != len(content):
        raise ValueError(f"socket 变换改变了原版 XML 长度：{target}")


def _validate_prefab_replacements(
    content: bytes,
    replacements: tuple[tuple[bytes, bytes], ...],
    target: str,
) -> None:
    """确认 Prefab 只做唯一方向的等长 socket 名变换。"""
    if any(len(old) != len(new) for old, new in replacements):
        raise ValueError(f"Prefab socket 名不是等长替换：{target}")
    has_left_body = b"Pelvis_L_Socket" in content
    has_right_body = b"Pelvis_R_Socket" in content
    if has_left_body and has_right_body:
        raise ValueError(f"Prefab 同时包含左右 body socket，无法安全判定：{target}")
    for old, new in replacements:
        if content.count(old) != 1 or content.count(new) != 0:
            raise ValueError(
                f"Prefab socket 不是唯一原版状态：{target} "
                f"old={content.count(old)} new={content.count(new)}"
            )


def _replace_operation(
    target: str,
    replacements: tuple[tuple[str, str], ...] | tuple[tuple[bytes, bytes], ...],
) -> dict[str, object]:
    """把文本或 bytes 规则转换为 cdmod ``replace-bytes`` 操作。"""
    encoded: list[tuple[bytes, bytes]] = []
    for old, new in replacements:
        old_bytes = old.encode("utf-8") if isinstance(old, str) else old
        new_bytes = new.encode("utf-8") if isinstance(new, str) else new
        if len(old_bytes) != len(new_bytes):
            raise ValueError(f"replace-bytes 规则不等长：{target}")
        encoded.append((old_bytes, new_bytes))
    return {
        "op": "replace-bytes",
        "target": target,
        "target_pamt_dir": PAMT_DIR,
        "replacements": [
            {"old_hex": old.hex(), "new_hex": new.hex()}
            for old, new in encoded
        ],
    }


def _build_manifest(
    *,
    operation_count: int,
    body_socket_target_count: int,
    weapon_sidecar_target_count: int,
    weapon_prefab_target_count: int,
) -> dict[str, object]:
    """生成不含发布文档的最小成品 manifest。"""
    return {
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": PACKAGE_ID,
        "name": PACKAGE_NAME,
        "version": PACKAGE_VERSION,
        "author": "cdmm compatibility rebuild",
        "description": (
            "Current-game incremental back carry for all dual one-hand swords. "
            "CC female Kliff player-only back carry with narrow PHM socket "
            "transforms. Preserves every PHW Damian rapier resource."
        ),
        "dependencies": [],
        "source": {
            "format": "dual-onehand-full-socket-chain-incremental-transform",
            "game_version": "1.15",
            "pamt_dir": PAMT_DIR,
            "body_socket_targets": body_socket_target_count,
            "prefab_transform_targets": weapon_prefab_target_count,
            "weapon_sidecar_transform_targets": weapon_sidecar_target_count,
            "full_prefab_replacements": 0,
            "coverage": "PHM-player-CD_MainWeapon_Sword_R-L-prefabs-only",
            "preserved_branch": "all-PHW-Damian-rapiers",
            "rotation_strategy": "parallel-right-back-child-sockets-with-shared-parent-frame",
        },
        "components": [
            {
                "type": CDMOD_RESOURCE_TRANSFORM_COMPONENT_TYPE,
                "path": RESOURCE_PATCH_PATH,
                "operation_count": operation_count,
            },
        ],
    }


def _validate_operation_targets_unique(operations: list[dict[str, object]]) -> None:
    """resource-transform schema 要求一个组件内每个最终目标唯一。"""
    targets = [str(operation["target"]).casefold() for operation in operations]
    if len(targets) != len(set(targets)):
        duplicates = sorted(target for target in set(targets) if targets.count(target) > 1)
        raise ValueError(f"资源变换目标重复：{duplicates}")


def _verify_package(
    output_path: Path,
    operations: list[dict[str, object]],
) -> None:
    """使用正式解析器回读目标、放置链和动作数量。"""
    package = load_cdmod_package(output_path)
    if (
        package.dependencies
        or package.file_patches
        or package.standalone_archives
        or package.legacy_json_patches
    ):
        raise ValueError("成品只能包含无依赖的窄范围资源变换")
    parsed_operations = [
        operation
        for patch in package.resource_patches
        for operation in patch.operations
    ]
    if len(parsed_operations) != len(operations):
        raise ValueError(
            f"资源操作回读数量异常：{len(parsed_operations)} != {len(operations)}"
        )
    for operation in parsed_operations:
        target = operation.target.casefold()
        if "/2_phw/" in target or "phw_" in Path(target).name:
            raise ValueError(f"成品禁止修改达米安 PHW 细剑链：{operation.target}")
        if "twohandweapon" in target or "cd_twohandweapon_sword" in target:
            raise ValueError(f"成品误改真正双手剑目标：{operation.target}")
        animation_basename = Path(target).name
        if operation.op == "copy-entry" and not (
            "dlsd" in animation_basename or "dualsword" in animation_basename
        ):
            raise ValueError(f"动作 copy-entry 目标不属于双持单手剑链：{operation.target}")


def _entry_final_path(entry: PazEntry) -> str:
    """按 PAMT folder record 与 basename 还原规范最终路径。"""
    parent = (entry.resolved_dir_path or "").replace("\\", "/").strip("/")
    basename = Path(entry.path).name
    path = f"{parent}/{basename}" if parent else entry.path.replace("\\", "/").strip("/")
    return path.casefold()


def _read_exact_entry(entries_by_path: dict[str, PazEntry], target: str) -> bytes:
    """优先按最终路径读取；短语义路径只允许唯一 basename 回退。"""
    entry = entries_by_path.get(target.casefold())
    if entry is None:
        basename = Path(target).name.casefold()
        matches = [
            candidate
            for path, candidate in entries_by_path.items()
            if Path(path).name.casefold() == basename
        ]
        if len(matches) != 1:
            raise ValueError(
                f"当前原版目标不是唯一 basename：{target} matches={len(matches)}"
            )
        entry = matches[0]
    return extract_plaintext(entry)[0]


def _sha256_file(path: Path) -> str:
    """流式计算文件 SHA-256，避免对大型输入一次性分配内存。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description="生成 CC 性转玩家专用双持单手剑背挂 .cdmod"
    )
    parser.add_argument("--game-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    """执行构建并输出 UTF-8 JSON 摘要。"""
    args = _parse_args()
    result = build_dual_onehand_swords_back_carry_mod(
        args.game_dir,
        args.output,
    )
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
