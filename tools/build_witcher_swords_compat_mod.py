"""生成可与其他角色描述模组合成的巫师双剑挂点 ``.cdmod``。"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from cdmm.archive.pamt import parse_pamt_filtered
from cdmm.services.cdmod_converter import (
    CDMOD_FORMAT_NAME,
    CDMOD_FORMAT_VERSION,
    CDMOD_LEGACY_JSON_COMPONENT_TYPE,
    CDMOD_RESOURCE_TRANSFORM_COMPONENT_TYPE,
    _write_cdmod_zip,
)
from cdmm.services.cdmod_package import load_cdmod_package
from cdmm.services.json_loader import apply_byte_patches, extract_plaintext

# 两个目标在 0009 PAMT 中使用扁平 entry path；最终目录由 PAMT node 解析。
PAMT_DIR = "0009"
PHW_DESCRIPTION_TARGET = "character/phw_description_player_001.xml"
PHM_DESCRIPTION_TARGET = "character/phm_description_player_001.xml"
PHM_KLIFF_DESCRIPTION_TARGET = "character/phm_description_player_kliff.xml"
DESCRIPTION_TARGETS = (
    PHW_DESCRIPTION_TARGET,
    PHM_DESCRIPTION_TARGET,
    PHM_KLIFF_DESCRIPTION_TARGET,
)
PHW_SOCKET_TARGET = "character/phw_01.pab.sockets.xml"
PHM_SOCKET_TARGET = "character/phm_01.pab.sockets.xml"
SOCKET_TARGETS = (PHW_SOCKET_TARGET, PHM_SOCKET_TARGET)
RESOURCE_PATCH_PATH = "patches/resources.json"
LEGACY_PATCH_PATH = "patches/socket-incremental.json"

# XML 行差异最多扩展 32 行上下文；仍不能唯一定位时拒绝生成不可靠补丁。
MAX_CONTEXT_LINES = 32

# 原模组只需要改四条左右手单剑收纳规则，使用整行可避免误改其他武器的骨盆挂点。
DESCRIPTION_REPLACEMENTS = (
    (
        '<PartInOutSocket PartName="CD_MainWeapon_Sword_R" InSocketBone="Pelvis_L_Socket" OutSocketBone="RHand_Socket" InChildSocketBone="Pelvis_L_ChildSocket" OutChildSocketBone="Basic_ChildSocket" WeaponCasePart="CD_MainWeapon_Sword_IN_R"/>',
        '<PartInOutSocket PartName="CD_MainWeapon_Sword_R" InSocketBone="Spine1_B_Socket" OutSocketBone="RHand_Socket" InChildSocketBone="Pelvis_L_ChildSocket" OutChildSocketBone="Basic_ChildSocket" WeaponCasePart="CD_MainWeapon_Sword_IN_R"/>',
    ),
    (
        '<PartInOutSocket PartName="CD_MainWeapon_Sword_IN_R" InSocketBone="Pelvis_L_Socket" OutSocketBone="Pelvis_L_Socket" InChildSocketBone="Pelvis_L_ChildSocket" OutChildSocketBone="Pelvis_L_ChildSocket"/>',
        '<PartInOutSocket PartName="CD_MainWeapon_Sword_IN_R" InSocketBone="Spine1_B_Socket" OutSocketBone="Pelvis_L_Socket" InChildSocketBone="Pelvis_L_ChildSocket" OutChildSocketBone="Pelvis_L_ChildSocket"/>',
    ),
    (
        '<PartInOutSocket PartName="CD_MainWeapon_Sword_L" InSocketBone="Pelvis_R_Socket" OutSocketBone="LHand_Socket" InChildSocketBone="Pelvis_R_ChildSocket" OutChildSocketBone="Basic_ChildSocket" WeaponCasePart="CD_MainWeapon_Sword_IN_L"/>',
        '<PartInOutSocket PartName="CD_MainWeapon_Sword_L" InSocketBone="Spine0_B_Socket" OutSocketBone="LHand_Socket" InChildSocketBone="Pelvis_R_ChildSocket" OutChildSocketBone="Basic_ChildSocket" WeaponCasePart="CD_MainWeapon_Sword_IN_L"/>',
    ),
    (
        '<PartInOutSocket PartName="CD_MainWeapon_Sword_IN_L" InSocketBone="Pelvis_R_Socket" OutSocketBone="Pelvis_R_Socket" InChildSocketBone="Pelvis_R_ChildSocket" OutChildSocketBone="Pelvis_R_ChildSocket"/>',
        '<PartInOutSocket PartName="CD_MainWeapon_Sword_IN_L" InSocketBone="Spine0_B_Socket" OutSocketBone="Pelvis_R_Socket" InChildSocketBone="Pelvis_R_ChildSocket" OutChildSocketBone="Pelvis_R_ChildSocket"/>',
    ),
)

# 两个骨骼挂点必须完整保留原作者浮点格式。游戏不接受省略前导零的 ``.201649``，
# 因此 socket 使用支持变长文本的传统字节补丁，不能强行压成等长资源变换。
SOCKET_REPLACEMENTS = (
    (
        '<Socket Name="Spine1_B_Socket" Parent="Bip_Weapon_Attach_In_01" Rotation="0.000000 0.000000 0.000000 1.000000" Translation="0.000000 0.000000 0.000000"/>',
        '<Socket Name="Spine1_B_Socket" Parent="Bip_Weapon_Attach_In_02" Rotation="0.201649 -0.730709 -0.655316 0.181649" Translation="-0.120000 0.225000 -0.040000"/>',
    ),
    (
        '<Socket Name="Spine0_B_Socket" Parent="Bip_Weapon_Attach_In_00" Rotation="0.000000 0.000000 0.000000 1.000000" Translation="0.000000 0.000000 0.000000"/>',
        '<Socket Name="Spine0_B_Socket" Parent="Bip_Weapon_Attach_In_02" Rotation="0.181649 -0.700709 -0.655316 0.181649" Translation="-0.200000 0.150000 -0.010000"/>',
    ),
)


@dataclass(frozen=True)
class WitcherSwordsCompatBuildResult:
    """兼容包构建结果。"""

    output_path: str
    package_sha256: str
    package_bytes: int
    target_count: int
    replacement_count: int
    socket_change_count: int
    original_source_checked: bool
    compatibility_base_checked: bool


def build_witcher_swords_compat_mod(
    game_dir: Path,
    original_mod_dir: Path,
    output_path: Path,
    *,
    compatibility_description_path: Path | None = None,
) -> WitcherSwordsCompatBuildResult:
    """验证 1.15 原版和原模组差异后生成混合增量兼容包。"""
    game_dir = game_dir.resolve()
    original_mod_dir = original_mod_dir.resolve()
    output_path = output_path.resolve()
    _validate_paths(game_dir, original_mod_dir, output_path)

    original_description_path = (
        original_mod_dir
        / "0009"
        / "character"
        / "descriptors"
        / "characterdescription"
        / "phw_description_player_001.xml"
    )
    original_socket_path = (
        original_mod_dir
        / "0009"
        / "character"
        / "descriptors"
        / "socketbonedata"
        / "1_pc"
        / "2_phw"
        / "phw_01.pab.sockets.xml"
    )
    vanilla = _load_vanilla_targets(game_dir)
    for target in DESCRIPTION_TARGETS:
        _apply_and_validate(vanilla[target], DESCRIPTION_REPLACEMENTS, target)
    expected_sockets = {
        target: _apply_and_validate(vanilla[target], SOCKET_REPLACEMENTS, target)
        for target in SOCKET_TARGETS
    }
    original_source_checked = original_description_path.is_file() and original_socket_path.is_file()
    if original_source_checked:
        _validate_replacement_set(
            vanilla[PHW_DESCRIPTION_TARGET],
            original_description_path.read_bytes(),
            DESCRIPTION_REPLACEMENTS,
            PHW_DESCRIPTION_TARGET,
        )
        _validate_replacement_set(
            vanilla[PHW_SOCKET_TARGET],
            original_socket_path.read_bytes(),
            SOCKET_REPLACEMENTS,
            PHW_SOCKET_TARGET,
        )

    socket_changes_by_target: dict[str, list[dict[str, object]]] = {}
    for target in SOCKET_TARGETS:
        changes = _build_unique_context_changes(
            vanilla[target],
            expected_sockets[target],
            target,
        )
        _validate_legacy_changes(
            vanilla[target],
            expected_sockets[target],
            changes,
            target,
        )
        socket_changes_by_target[target] = changes
    socket_change_count = sum(len(changes) for changes in socket_changes_by_target.values())

    compatibility_base_checked = False
    if compatibility_description_path is not None and compatibility_description_path.is_file():
        _apply_and_validate(
            compatibility_description_path.read_bytes(),
            DESCRIPTION_REPLACEMENTS,
            "兼容角色描述",
        )
        compatibility_base_checked = True

    manifest = {
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": "foxpurple-witcher-swords-compatible",
        "name": "Witcher Swords - Compatible",
        "version": "1.0-1.15.00",
        "author": "FoxPurple",
        "description": (
            "Incremental PHW dual-sword back socket transform. Keeps the original author's "
            "full socket floats and does not replace either complete XML table."
        ),
        "dependencies": [],
        "source": {
            "format": "numbered-loose-xml",
            "original_mod": "Witcher Swords 0.9",
        },
        "components": [
            {
                "type": CDMOD_RESOURCE_TRANSFORM_COMPONENT_TYPE,
                "path": RESOURCE_PATCH_PATH,
                "target_count": len(DESCRIPTION_TARGETS),
                "replacement_count": len(DESCRIPTION_TARGETS) * len(DESCRIPTION_REPLACEMENTS),
            },
            {
                "type": CDMOD_LEGACY_JSON_COMPONENT_TYPE,
                "path": LEGACY_PATCH_PATH,
                "target_count": len(SOCKET_TARGETS),
                "change_count": socket_change_count,
            },
        ],
    }
    resource_patch = {
        "schema": 1,
        "operations": [
            _byte_operation(target, DESCRIPTION_REPLACEMENTS)
            for target in DESCRIPTION_TARGETS
        ],
    }
    _write_cdmod_zip(
        output_path,
        {
            "manifest.json": manifest,
            RESOURCE_PATCH_PATH: resource_patch,
            LEGACY_PATCH_PATH: {
                "name": "Witcher Swords Socket Incremental Compatibility",
                "version": "1.0-1.15.00",
                "author": "FoxPurple",
                "patches": [
                    {
                        "game_file": target,
                        "changes": socket_changes_by_target[target],
                    }
                    for target in SOCKET_TARGETS
                ],
            },
        },
    )

    package = load_cdmod_package(output_path)
    operations = tuple(
        operation
        for patch in package.resource_patches
        for operation in patch.operations
    )
    replacement_count = sum(len(operation.replacements) for operation in operations)
    expected_replacement_count = len(DESCRIPTION_TARGETS) * len(DESCRIPTION_REPLACEMENTS)
    if len(operations) != len(DESCRIPTION_TARGETS) or replacement_count != expected_replacement_count:
        raise ValueError("兼容包回读数量异常")
    if len(package.legacy_json_patches) != 1:
        raise ValueError("兼容包 socket 增量组件回读数量异常")
    legacy_targets = package.legacy_json_patches[0].get("patches")
    if not isinstance(legacy_targets, list) or len(legacy_targets) != len(SOCKET_TARGETS):
        raise ValueError("兼容包 socket 增量目标回读数量异常")
    return WitcherSwordsCompatBuildResult(
        output_path=str(output_path),
        package_sha256=_sha256_file(output_path),
        package_bytes=output_path.stat().st_size,
        target_count=len(operations) + len(legacy_targets),
        replacement_count=replacement_count,
        socket_change_count=socket_change_count,
        original_source_checked=original_source_checked,
        compatibility_base_checked=compatibility_base_checked,
    )


def _validate_paths(game_dir: Path, original_mod_dir: Path, output_path: Path) -> None:
    """校验输入边界，避免从错误游戏版本生成包。"""
    if not (game_dir / "bin64" / "CrimsonDesert.exe").is_file():
        raise ValueError(f"不是有效游戏目录：{game_dir}")
    if output_path.suffix.casefold() != ".cdmod":
        raise ValueError("输出必须使用 .cdmod 后缀")
    output_path.parent.mkdir(parents=True, exist_ok=True)


def _load_vanilla_targets(game_dir: Path) -> dict[str, bytes]:
    """从当前 0009 原版归档提取两个 PHW 文本资源。"""
    targets = (*DESCRIPTION_TARGETS, *SOCKET_TARGETS)
    basenames = {Path(target).name for target in targets}
    entries = parse_pamt_filtered(
        game_dir / PAMT_DIR / "0.pamt",
        paz_dir=game_dir / PAMT_DIR,
        desired_basenames=basenames,
    )
    by_name = {Path(entry.path).name.casefold(): entry for entry in entries}
    result: dict[str, bytes] = {}
    for target in targets:
        entry = by_name.get(Path(target).name.casefold())
        if entry is None:
            raise ValueError(f"当前 0009 原版中未找到：{target}")
        result[target] = extract_plaintext(entry)[0]
    return result


def _validate_replacement_set(
    vanilla: bytes,
    original_mod: bytes,
    replacements: tuple[tuple[str, str], ...],
    label: str,
) -> None:
    """确认规则可从当前原版精确重建原作者文件。"""
    rebuilt = _apply_and_validate(vanilla, replacements, label)
    if rebuilt != original_mod:
        raise ValueError(f"{label} 的增量规则无法精确重建原模组，拒绝生成")


def _build_unique_context_changes(
    vanilla: bytes,
    patched: bytes,
    label: str,
) -> list[dict[str, object]]:
    """按 XML 行差异生成唯一上下文传统字节补丁。"""
    vanilla_lines = vanilla.splitlines(keepends=True)
    patched_lines = patched.splitlines(keepends=True)
    matcher = difflib.SequenceMatcher(None, vanilla_lines, patched_lines, autojunk=False)
    for context_lines in range(1, MAX_CONTEXT_LINES + 1):
        line_offsets = [0]
        for line in vanilla_lines:
            line_offsets.append(line_offsets[-1] + len(line))
        changes: list[dict[str, object]] = []
        valid = True
        for index, group in enumerate(matcher.get_grouped_opcodes(context_lines)):
            opcodes = list(group)
            if not opcodes:
                continue
            old_start = opcodes[0][1]
            old_end = opcodes[-1][2]
            new_start = opcodes[0][3]
            new_end = opcodes[-1][4]
            old = b"".join(vanilla_lines[old_start:old_end])
            new = b"".join(patched_lines[new_start:new_end])
            if not old or old == new or vanilla.count(old) != 1:
                valid = False
                break
            changes.append(
                {
                    "offset": line_offsets[old_start],
                    "original": old.hex(),
                    "patched": new.hex(),
                    "label": f"{label} incremental block {index + 1}",
                }
            )
        if valid and changes:
            return changes
    raise ValueError(f"{label} 的差异在 {MAX_CONTEXT_LINES} 行上下文内仍不能唯一定位")


def _validate_legacy_changes(
    vanilla: bytes,
    patched: bytes,
    changes: list[dict[str, object]],
    label: str,
) -> None:
    """复用真实加载器补丁器，确认变长 socket 补丁能逐字重建目标。"""
    output = bytearray(vanilla)
    applied, mismatched, _relocated = apply_byte_patches(
        output,
        changes,
        vanilla_data=vanilla,
    )
    if applied != len(changes) or mismatched != 0 or bytes(output) != patched:
        raise ValueError(
            f"{label} 增量重建失败：applied={applied}/{len(changes)} "
            f"mismatched={mismatched} exact={bytes(output) == patched}"
        )


def _apply_and_validate(
    content: bytes,
    replacements: tuple[tuple[str, str], ...],
    label: str,
) -> bytes:
    """精确应用规则，要求每条旧值或目标值唯一存在。"""
    result = content
    for old_text, new_text in replacements:
        old = old_text.encode("utf-8")
        new = new_text.encode("utf-8")
        old_count = result.count(old)
        new_count = result.count(new)
        if old_count == 1:
            result = result.replace(old, new)
            continue
        if old_count == 0 and new_count == 1:
            continue
        raise ValueError(
            f"{label} 替换前置条件异常：old={old_count} new={new_count}"
        )
    return result


def _byte_operation(
    target: str,
    replacements: tuple[tuple[str, str], ...],
) -> dict[str, object]:
    """生成一条现有加载器可直接执行的等长字节操作。"""
    encoded = [
        (old_text.encode("utf-8"), new_text.encode("utf-8"))
        for old_text, new_text in replacements
    ]
    unequal = [(len(old), len(new)) for old, new in encoded if len(old) != len(new)]
    if unequal:
        raise ValueError(f"replace-bytes 存在非等长规则：{unequal}")
    return {
        "op": "replace-bytes",
        "target": target,
        "target_pamt_dir": PAMT_DIR,
        "replacements": [
            {"old_hex": old.hex(), "new_hex": new.hex()}
            for old, new in encoded
        ],
    }


def _sha256_file(path: Path) -> str:
    """流式计算成品 SHA-256。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="生成巫师双剑挂点增量兼容 .cdmod")
    parser.add_argument("--game-dir", type=Path, required=True)
    parser.add_argument("--original-mod-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compatibility-description", type=Path)
    return parser.parse_args()


def main() -> int:
    """执行构建并输出 JSON 摘要。"""
    args = parse_args()
    result = build_witcher_swords_compat_mod(
        args.game_dir,
        args.original_mod_dir,
        args.output,
        compatibility_description_path=args.compatibility_description,
    )
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
