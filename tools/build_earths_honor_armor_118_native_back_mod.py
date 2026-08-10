"""基于 1.18 舞女服，恢复 ForDamiane 20027 的连续金色后背。

本工具把 1.18 ``.cdmod`` 当作基线，重建 vanilla、body-91 和 body-99 三份
主上身 PAC：白布只移植 20027 的后背坐标，整条绑带则作为一个连续结构移植，
避免前胸与后背分别采用两套坐标后在腋下断开。PAC_XML 只恢复绑带和后背腰片的
20027 原生金色材质；白布染色、项链隐藏、HKX、三份 Prefab、下身隐藏 PAC、
附属上身隐藏 PAC、披风兼容及其他装配行为继续保留 1.18。
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from zipfile import ZipFile

from cdmm.services.cdmod_converter import _write_cdmod_zip
from cdmm.services.json_loader import extract_plaintext
from cdmm.services.pamt_index_service import get_game_pamt_index
from cdmm.utils.path_utils import lower_game_rel_path

# 生成时使用 Workbench 的 PAC 解析/回写能力，成品模组不依赖 Workbench。
DEFAULT_WORKBENCH_ROOT = Path(r"T:\C++\crimson-desert-mod-workbench")
WORKBENCH_ROOT_ENV = "CDMW_ROOT"

# 用户实机确认表现最好的 1.18 是本次唯一基线。
BASELINE_PACKAGE_SHA256 = (
    "cceab49eb326f8eb595ca2abb0bc3aacbc832eb63a1e05a159749d54c4a8a65a"
)
PACKAGE_VERSION = "1.18.2"
OUTPUT_FILENAME = (
    "ZZZ - Earths Honor Armor to Goddess Dress - 1.18 Native Gold Back-1.18.2.cdmod"
)

# 第一张图在当前 body-99 加载顺序下实际生效的 20027 PAC，而非游戏 vanilla PAC。
NATIVE_BACK_DONOR_SHA256 = (
    "df34325c9dbcfe544705e0ae58c70d1fc7a809904687a22d6039d0e226100ce7"
)

# 原版 20027 PAC_XML 是金色外观的真实来源；纯 RGB 不能替代其 MA 遮罩与通道组合。
NATIVE_PROPERTY_TARGET = (
    "character/modelproperty/3_npc/2_nhw/armor_north/9_upperbody/"
    "cd_nhw_00_no_ub_00_20027.pac_xml"
)
NATIVE_PROPERTY_SHA256 = (
    "9914560195a89e5cb885691f1284a69449d2d51e7c4954dce8d55627874124d6"
)
BASELINE_PROPERTY_PAYLOAD = "assets/00001/cd_phw_00_ub_00_0163.pac_xml"
BASELINE_PROPERTY_SHA256 = (
    "e0b679aa5af7958b6c701caa3ecb556fd366ccf3593b45eb3cea7721f99c0dcc"
)
REPLACEMENTS_PATH = "files/replacements.json"

# 1.18 三种体型载荷的固定身份，防止误把 1.31 或其他实验版本当成基线。
BASELINE_PROFILE_SHA256 = {
    "body-91": "3ac5feb536ff4ec7de5c40606fc042a669164b8fd160e767cb1633a471f648f2",
    "body-99": "ff6dd66497045aced8908843c42731b69d5c12e4e5d9b1c01313163c2f0dc7f9",
    "vanilla": "be994afe94a2522d56a788630b19713b21bd1a493980578d5d568eb7f501fb6c",
}

PROFILE_PAYLOAD_PATHS = {
    profile: f"assets/00000/{profile}/cd_phw_00_ub_00_0163.pac"
    for profile in ("body-91", "body-99", "vanilla")
}
PROFILED_REPLACEMENTS_PATH = "files/profiled-replacements.json"
MANIFEST_PATH = "manifest.json"
REPORT_PATH = "reports/conversion.json"

# 1.18 把白布子网格改成了 0163 染色身份；donor 仍保留原始 UB 名称。
BASELINE_WHITE_BACK_SUBMESH = "cd_phw_00_sho_00_0163_edge"
DONOR_WHITE_BACK_SUBMESH = "cd_phw_00_ub_0135_00_02_01"
HARNESS_SUBMESH = "cd_phw_00_ub_0135_00_01_01"

# 白布后背片的空间边界，只命中上背，不能选到前胸或长裙。
WHITE_BACK_MIN_Y = 1.18
WHITE_BACK_MIN_Z = 0.0
WHITE_BACK_COMPONENT_COUNT = 4
WHITE_BACK_VERTEX_COUNT = 294

# 绑带由一个前胸片和四个后背片组成；五片必须整套移植才能保持腋下接缝连续。
HARNESS_FRONT_MAX_Z = 0.0
HARNESS_FRONT_COMPONENT_COUNT = 1
HARNESS_FRONT_VERTEX_COUNT = 130
HARNESS_BACK_COMPONENT_COUNT = 4
HARNESS_BACK_VERTEX_COUNT = 257
HARNESS_TOTAL_COMPONENT_COUNT = 5
HARNESS_TOTAL_VERTEX_COUNT = 387

TOTAL_TRANSPLANT_VERTEX_COUNT = WHITE_BACK_VERTEX_COUNT + HARNESS_TOTAL_VERTEX_COUNT
# PAC 使用 uint16 包围盒量化，回读允许最多 0.03 mm 误差。
PAC_VERTEX_ROUNDTRIP_MAX_ERROR = 0.00003

# 后背可见的绑带和腰片恢复完整原生材质；白布及腿环继续使用 1.18 材质。
NATIVE_GOLD_MATERIAL_SUBMESHES = frozenset(
    {
        HARNESS_SUBMESH,
        "cd_phw_00_ub_0135_00_01_02",
    }
)


@dataclass(frozen=True)
class ProfileBackAudit:
    """记录一份体型 PAC 的后背移植与回读结果。"""

    profile: str
    source_sha256: str
    output_sha256: str
    selected_vertex_count: int
    changed_vertex_count: int
    changed_byte_count: int
    selected_max_error: float
    unselected_max_error: float


@dataclass(frozen=True)
class BuildResult:
    """记录最终 1.18.2 包及三种体型审计结果。"""

    output_path: Path
    package_sha256: str
    profiles: tuple[ProfileBackAudit, ...]


def _sha256(content: bytes) -> str:
    """返回小写 SHA-256。"""
    return hashlib.sha256(content).hexdigest()


def _verify_hash(label: str, content: bytes, expected: str) -> None:
    """锁定二进制身份，拒绝在未知版本上套用顶点索引。"""
    actual = _sha256(content)
    if actual != expected:
        raise ValueError(
            f"{label} 指纹不匹配，拒绝生成：expected={expected} actual={actual}"
        )


def _load_mesh_tooling() -> tuple[Callable[..., Any], Callable[..., bytes]]:
    """加载开发用 PAC 解析与原位回写工具。"""
    configured = os.environ.get(WORKBENCH_ROOT_ENV, "").strip()
    workbench_root = Path(configured) if configured else DEFAULT_WORKBENCH_ROOT
    if not (workbench_root / "cdmw" / "modding" / "mesh_parser.py").is_file():
        raise FileNotFoundError(f"缺少 Crimson Desert Mod Workbench：{workbench_root}")
    root_text = str(workbench_root.resolve())
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    from cdmw.modding.mesh_importer import build_pac
    from cdmw.modding.mesh_parser import parse_pac

    return parse_pac, build_pac


def _connected_vertex_components(submesh: Any) -> list[list[int]]:
    """按三角面邻接关系拆分同一子网格中的独立布片。"""
    graph: list[set[int]] = [set() for _vertex in submesh.vertices]
    for first, second, third in submesh.faces:
        for left, right in ((first, second), (second, third), (third, first)):
            graph[left].add(right)
            graph[right].add(left)
    components: list[list[int]] = []
    visited: set[int] = set()
    for root in range(len(graph)):
        if root in visited:
            continue
        visited.add(root)
        pending = [root]
        component: list[int] = []
        while pending:
            vertex_index = pending.pop()
            component.append(vertex_index)
            for neighbor in graph[vertex_index]:
                if neighbor not in visited:
                    visited.add(neighbor)
                    pending.append(neighbor)
        components.append(sorted(component))
    return components


def _unique_submesh(mesh: Any, name: str) -> Any:
    """按不区分大小写的名称读取唯一子网格。"""
    matches = [item for item in mesh.submeshes if item.name.casefold() == name]
    if len(matches) != 1:
        raise ValueError(f"PAC 子网格不是唯一命中：{name} matches={len(matches)}")
    return matches[0]


def _assert_matching_topology(baseline: Any, donor: Any, label: str) -> None:
    """顶点移植只允许在完全相同的索引拓扑之间进行。"""
    if len(baseline.vertices) != len(donor.vertices) or baseline.faces != donor.faces:
        raise ValueError(f"{label} 的 1.18 与 20027 donor 拓扑不一致")


def _select_transplant_vertices(mesh: Any) -> dict[str, tuple[Any, frozenset[int]]]:
    """选择白色上背，并把整条绑带作为不可拆分的连续结构。"""
    white = _unique_submesh(mesh, BASELINE_WHITE_BACK_SUBMESH)
    white_components = _connected_vertex_components(white)
    white_back_components = [
        component
        for component in white_components
        if min(white.vertices[index][1] for index in component) >= WHITE_BACK_MIN_Y
        and min(white.vertices[index][2] for index in component) >= WHITE_BACK_MIN_Z
    ]
    white_indices = frozenset(
        index for component in white_back_components for index in component
    )
    if (
        len(white_back_components) != WHITE_BACK_COMPONENT_COUNT
        or len(white_indices) != WHITE_BACK_VERTEX_COUNT
    ):
        raise ValueError(
            "1.18 白色后背组件选择异常："
            f"components={len(white_back_components)} vertices={len(white_indices)}"
        )

    harness = _unique_submesh(mesh, HARNESS_SUBMESH)
    harness_components = _connected_vertex_components(harness)
    front_components = [
        component
        for component in harness_components
        if max(harness.vertices[index][2] for index in component) <= HARNESS_FRONT_MAX_Z
    ]
    if (
        len(front_components) != HARNESS_FRONT_COMPONENT_COUNT
        or len(front_components[0]) != HARNESS_FRONT_VERTEX_COUNT
    ):
        raise ValueError(
            "1.18 前胸绑带组件识别异常："
            f"components={len(front_components)} "
            f"vertices={sum(len(item) for item in front_components)}"
        )
    front_indices = frozenset(front_components[0])
    harness_back_components = [
        component for component in harness_components if component is not front_components[0]
    ]
    harness_back_indices = frozenset(
        index for component in harness_back_components for index in component
    )
    if (
        len(harness_back_components) != HARNESS_BACK_COMPONENT_COUNT
        or len(harness_back_indices) != HARNESS_BACK_VERTEX_COUNT
    ):
        raise ValueError(
            "1.18 后背绑带组件选择异常："
            f"components={len(harness_back_components)} "
            f"vertices={len(harness_back_indices)}"
        )
    if front_indices & harness_back_indices:
        raise ValueError("前胸与后背绑带顶点发生重叠")
    if (
        len(harness_components) != HARNESS_TOTAL_COMPONENT_COUNT
        or len(harness.vertices) != HARNESS_TOTAL_VERTEX_COUNT
        or len(front_indices | harness_back_indices) != HARNESS_TOTAL_VERTEX_COUNT
    ):
        raise ValueError(
            "1.18 整条绑带结构异常："
            f"components={len(harness_components)} vertices={len(harness.vertices)}"
        )
    return {
        "white": (white, white_indices),
        "harness": (harness, frozenset(range(len(harness.vertices)))),
    }


def transplant_native_back(
    baseline: bytes,
    donor: bytes,
    profile: str,
) -> tuple[bytes, ProfileBackAudit]:
    """移植 20027 白色后背与整条绑带，避免腋下接缝断开。"""
    parse_pac, build_pac = _load_mesh_tooling()
    baseline_mesh = parse_pac(baseline, f"1.18-{profile}.pac")
    donor_mesh = parse_pac(donor, "ForDamiane-active-20027.pac")
    working = copy.deepcopy(baseline_mesh)
    baseline_selection = _select_transplant_vertices(baseline_mesh)
    working_selection = _select_transplant_vertices(working)
    donor_white = _unique_submesh(donor_mesh, DONOR_WHITE_BACK_SUBMESH)
    donor_harness = _unique_submesh(donor_mesh, HARNESS_SUBMESH)
    donor_by_role = {"white": donor_white, "harness": donor_harness}

    selected_vertex_count = 0
    changed_vertex_count = 0
    for role, (baseline_submesh, indices) in baseline_selection.items():
        working_submesh = working_selection[role][0]
        donor_submesh = donor_by_role[role]
        _assert_matching_topology(baseline_submesh, donor_submesh, role)
        selected_vertex_count += len(indices)
        vertices = list(working_submesh.vertices)
        for index in indices:
            if baseline_submesh.vertices[index] != donor_submesh.vertices[index]:
                changed_vertex_count += 1
            vertices[index] = donor_submesh.vertices[index]
        working_submesh.vertices = vertices
    if selected_vertex_count != TOTAL_TRANSPLANT_VERTEX_COUNT:
        raise ValueError(f"移植顶点总数异常：{selected_vertex_count}")

    output = build_pac(working, baseline)
    rebuilt_mesh = parse_pac(output, f"1.18.2-{profile}.pac")
    rebuilt_selection = _select_transplant_vertices(rebuilt_mesh)
    selected_max_error = 0.0
    unselected_max_error = 0.0
    for role, (baseline_submesh, selected_indices) in baseline_selection.items():
        donor_submesh = donor_by_role[role]
        rebuilt_submesh = rebuilt_selection[role][0]
        _assert_matching_topology(baseline_submesh, rebuilt_submesh, f"回读 {role}")
        for index, rebuilt_vertex in enumerate(rebuilt_submesh.vertices):
            expected = (
                donor_submesh.vertices[index]
                if index in selected_indices
                else baseline_submesh.vertices[index]
            )
            error = math.dist(rebuilt_vertex, expected)
            if index in selected_indices:
                selected_max_error = max(selected_max_error, error)
            else:
                unselected_max_error = max(unselected_max_error, error)
    if selected_max_error > PAC_VERTEX_ROUNDTRIP_MAX_ERROR:
        raise ValueError(f"{profile} 后背 donor 回读误差过大：{selected_max_error}")
    if unselected_max_error > PAC_VERTEX_ROUNDTRIP_MAX_ERROR:
        raise ValueError(f"{profile} 未选择顶点发生变化：{unselected_max_error}")

    audit = ProfileBackAudit(
        profile=profile,
        source_sha256=_sha256(baseline),
        output_sha256=_sha256(output),
        selected_vertex_count=selected_vertex_count,
        changed_vertex_count=changed_vertex_count,
        changed_byte_count=sum(left != right for left, right in zip(baseline, output)),
        selected_max_error=selected_max_error,
        unselected_max_error=unselected_max_error,
    )
    return output, audit


def _parse_property_fragment(content: bytes) -> tuple[ET.Element, bool]:
    """解析由多个 ModelProperty 顶层节点组成的 PAC_XML 片段。"""
    has_bom = content.startswith(b"\xef\xbb\xbf")
    text = content.decode("utf-8-sig")
    return ET.fromstring(f"<Root>{text}</Root>"), has_bom


def _property_wrappers(model_property: ET.Element) -> dict[str, ET.Element]:
    """按子网格名称索引一份 ModelProperty，并拒绝重复绑定。"""
    wrappers: dict[str, ET.Element] = {}
    for wrapper in model_property.findall(".//SkinnedMeshMaterialWrapper"):
        name = (wrapper.get("_subMeshName") or "").casefold()
        if name in wrappers:
            raise ValueError(f"PAC_XML 子网格材质重复：{name}")
        wrappers[name] = wrapper
    return wrappers


def _model_properties(root: ET.Element) -> dict[str, ET.Element]:
    """按 Index 读取两套材质配置。"""
    result: dict[str, ET.Element] = {}
    for item in root.findall("./ModelPropertyList/ModelProperty"):
        index = item.get("Index")
        if not index or index in result:
            raise ValueError(f"PAC_XML ModelProperty Index 异常：{index}")
        result[index] = item
    if set(result) != {"0", "1"}:
        raise ValueError(f"PAC_XML ModelProperty 配置异常：{sorted(result)}")
    return result


def _material_element(wrapper: ET.Element, label: str) -> ET.Element:
    """读取子网格唯一 Material 节点。"""
    materials = wrapper.findall("./Material")
    if len(materials) != 1:
        raise ValueError(f"{label} Material 数量异常：{len(materials)}")
    return materials[0]


def restore_native_gold_materials(baseline: bytes, native: bytes) -> bytes:
    """只把绑带与后背腰片的完整 20027 Material 恢复到 1.18。"""
    _verify_hash("1.18 PAC_XML", baseline, BASELINE_PROPERTY_SHA256)
    _verify_hash("原版 20027 PAC_XML", native, NATIVE_PROPERTY_SHA256)
    baseline_root, has_bom = _parse_property_fragment(baseline)
    native_root, _native_has_bom = _parse_property_fragment(native)
    baseline_models = _model_properties(baseline_root)
    native_models = _model_properties(native_root)
    preserved_materials: dict[tuple[str, str], bytes] = {}

    for index, baseline_model in baseline_models.items():
        baseline_wrappers = _property_wrappers(baseline_model)
        native_wrappers = _property_wrappers(native_models[index])
        for name, wrapper in baseline_wrappers.items():
            if name not in NATIVE_GOLD_MATERIAL_SUBMESHES:
                preserved_materials[index, name] = ET.tostring(wrapper, encoding="utf-8")
                continue
            native_wrapper = native_wrappers.get(name)
            if native_wrapper is None:
                raise ValueError(f"原版 20027 缺少金色材质：index={index} name={name}")
            baseline_material = _material_element(wrapper, f"1.18 {index}/{name}")
            native_material = copy.deepcopy(
                _material_element(native_wrapper, f"20027 {index}/{name}")
            )
            native_material.tail = baseline_material.tail
            child_index = list(wrapper).index(baseline_material)
            wrapper.remove(baseline_material)
            wrapper.insert(child_index, native_material)

    prefix = b"\xef\xbb\xbf" if has_bom else b""
    output = prefix + b"".join(
        ET.tostring(child, encoding="utf-8") for child in list(baseline_root)
    )
    output_root, _output_has_bom = _parse_property_fragment(output)
    output_models = _model_properties(output_root)
    for index, output_model in output_models.items():
        output_wrappers = _property_wrappers(output_model)
        native_wrappers = _property_wrappers(native_models[index])
        for name, wrapper in output_wrappers.items():
            if name in NATIVE_GOLD_MATERIAL_SUBMESHES:
                actual = ET.tostring(
                    _material_element(wrapper, f"输出 {index}/{name}"), encoding="utf-8"
                )
                expected = ET.tostring(
                    _material_element(
                        native_wrappers[name], f"20027 回读 {index}/{name}"
                    ),
                    encoding="utf-8",
                )
                if actual != expected:
                    raise ValueError(f"原版金色材质回读不一致：index={index} name={name}")
            elif ET.tostring(wrapper, encoding="utf-8") != preserved_materials[index, name]:
                raise ValueError(f"非目标材质被意外修改：index={index} name={name}")
    return output


def _read_current_asset(game_dir: Path, target: str) -> bytes:
    """从当前 0009 精确读取资源，并拒绝 basename 跨路径误匹配。"""
    entry = get_game_pamt_index(game_dir).find_in_dir("0009", target)
    if entry is None:
        raise ValueError(f"当前游戏 0009 中未找到资源：{target}")
    final_path = lower_game_rel_path(
        f"{entry.resolved_dir_path}/{Path(entry.path).name}"
    )
    if final_path != lower_game_rel_path(target):
        raise ValueError(f"资源路径发生歧义：{target} -> {final_path}")
    return extract_plaintext(entry)[0]


def _read_package(path: Path) -> dict[str, bytes]:
    """读取 cdmod 的全部 ZIP entry，并拒绝重复路径。"""
    with ZipFile(path) as archive:
        entries = {item.filename: archive.read(item) for item in archive.infolist()}
        if len(entries) != len(archive.infolist()):
            raise ValueError(f"基线包存在重复 ZIP entry：{path}")
    return entries


def _json_object(entries: dict[str, bytes], path: str) -> dict[str, Any]:
    """读取包内 UTF-8 JSON 对象。"""
    document = json.loads(entries[path].decode("utf-8-sig"))
    if not isinstance(document, dict):
        raise ValueError(f"包内 JSON 顶层不是对象：{path}")
    return document


def _profile_payload_specs(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """把 profiled-replacements 的三份载荷声明按 profile 展开。"""
    files = document.get("files")
    if not isinstance(files, list) or len(files) != 1:
        raise ValueError("1.18 profiled-replacements 文件数量异常")
    file_spec = files[0]
    specs = {
        item["profile"]: item
        for item in [*file_spec.get("variants", []), file_spec.get("fallback")]
        if isinstance(item, dict)
    }
    if set(specs) != set(PROFILE_PAYLOAD_PATHS):
        raise ValueError(f"1.18 体型声明异常：{sorted(specs)}")
    return specs


def build_mod(
    baseline_path: Path,
    donor_path: Path,
    game_dir: Path,
    output_path: Path,
) -> BuildResult:
    """从 1.18 构建连续后背几何与原版金色材质的 1.18.2。"""
    baseline_path = baseline_path.resolve()
    donor_path = donor_path.resolve()
    game_dir = game_dir.resolve()
    output_path = output_path.resolve()
    baseline_bytes = baseline_path.read_bytes()
    _verify_hash("1.18 基线包", baseline_bytes, BASELINE_PACKAGE_SHA256)
    donor = donor_path.read_bytes()
    _verify_hash("第一张图 20027 donor", donor, NATIVE_BACK_DONOR_SHA256)
    baseline_entries = _read_package(baseline_path)
    output_entries = dict(baseline_entries)
    profile_document = _json_object(baseline_entries, PROFILED_REPLACEMENTS_PATH)
    profile_specs = _profile_payload_specs(profile_document)

    audits: list[ProfileBackAudit] = []
    for profile, payload_path in PROFILE_PAYLOAD_PATHS.items():
        if profile_specs[profile].get("payload") != payload_path:
            raise ValueError(f"1.18 {profile} payload 路径异常")
        baseline_payload = baseline_entries[payload_path]
        _verify_hash(
            f"1.18 {profile} PAC", baseline_payload, BASELINE_PROFILE_SHA256[profile]
        )
        transformed, audit = transplant_native_back(baseline_payload, donor, profile)
        output_entries[payload_path] = transformed
        profile_specs[profile]["sha256"] = audit.output_sha256
        audits.append(audit)

    baseline_property = baseline_entries[BASELINE_PROPERTY_PAYLOAD]
    native_property = _read_current_asset(game_dir, NATIVE_PROPERTY_TARGET)
    restored_property = restore_native_gold_materials(
        baseline_property,
        native_property,
    )
    output_entries[BASELINE_PROPERTY_PAYLOAD] = restored_property
    replacements = _json_object(baseline_entries, REPLACEMENTS_PATH)
    property_specs = [
        item
        for item in replacements.get("files", [])
        if isinstance(item, dict)
        and item.get("payload") == BASELINE_PROPERTY_PAYLOAD
    ]
    if len(property_specs) != 1:
        raise ValueError("1.18 PAC_XML replacement 声明不是唯一命中")
    property_specs[0]["sha256"] = _sha256(restored_property)
    property_specs[0]["size"] = len(restored_property)

    manifest = _json_object(baseline_entries, MANIFEST_PATH)
    manifest["version"] = PACKAGE_VERSION
    manifest["name"] = "Earth's Honor Armor - Goddess Dress - 1.18 Native Gold Back"
    manifest["description"] = (
        "Uses the complete 1.18 package as its baseline, transplants the 20027 white "
        "back plus the complete continuous harness into each body profile, and restores "
        "the native 20027 gold materials only for the harness and back waist panel. "
        "White-cloth dyeing, necklace hiding, skirt, leg rings, prefabs, physics and "
        "cloak compatibility remain from 1.18."
    )
    source = manifest.setdefault("source", {})
    source["format"] = "1.18-baseline-native-back-coordinate-transplant"
    source_files = source.setdefault("files", {})
    source_files["baseline-package://1.18.cdmod"] = BASELINE_PACKAGE_SHA256
    source_files["for-damiane-active://cd_nhw_00_no_ub_00_20027.pac"] = (
        NATIVE_BACK_DONOR_SHA256
    )
    source_files[NATIVE_PROPERTY_TARGET] = NATIVE_PROPERTY_SHA256

    report = _json_object(baseline_entries, REPORT_PATH)
    report["native_back_transplant"] = {
        "baseline_package_sha256": BASELINE_PACKAGE_SHA256,
        "donor_sha256": NATIVE_BACK_DONOR_SHA256,
        "white_back": {
            "component_count": WHITE_BACK_COMPONENT_COUNT,
            "vertex_count": WHITE_BACK_VERTEX_COUNT,
        },
        "complete_harness": {
            "component_count": HARNESS_TOTAL_COMPONENT_COUNT,
            "vertex_count": HARNESS_TOTAL_VERTEX_COUNT,
            "reason": "front and back pieces meet visually at the underarm seam",
        },
        "total_vertex_count_per_profile": TOTAL_TRANSPLANT_VERTEX_COUNT,
        "native_gold_material_submeshes": sorted(NATIVE_GOLD_MATERIAL_SUBMESHES),
        "native_property_sha256": NATIVE_PROPERTY_SHA256,
        "output_property_sha256": _sha256(restored_property),
        "profiles": [audit.__dict__ for audit in audits],
        "preserved_entry_count": len(baseline_entries) - 8,
        "policy": (
            "The white cloth changes only on the back; the complete harness uses one "
            "coordinate source to keep its seam continuous. Only harness/back-waist "
            "materials are restored from native 20027; all other materials remain 1.18."
        ),
    }

    output_entries[PROFILED_REPLACEMENTS_PATH] = profile_document
    output_entries[REPLACEMENTS_PATH] = replacements
    output_entries[MANIFEST_PATH] = manifest
    output_entries[REPORT_PATH] = report
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_cdmod_zip(output_path, output_entries)
    verify_package(baseline_path, donor_path, output_path)
    return BuildResult(output_path, _sha256(output_path.read_bytes()), tuple(audits))


def verify_package(baseline_path: Path, donor_path: Path, output_path: Path) -> None:
    """回读成品，证明装配资源及非目标材质继续来自 1.18。"""
    baseline_entries = _read_package(baseline_path)
    output_entries = _read_package(output_path)
    if set(output_entries) != set(baseline_entries):
        raise ValueError("1.18.2 ZIP entry 集合与 1.18 不一致")
    mutable_entries = {
        *PROFILE_PAYLOAD_PATHS.values(),
        BASELINE_PROPERTY_PAYLOAD,
        PROFILED_REPLACEMENTS_PATH,
        REPLACEMENTS_PATH,
        MANIFEST_PATH,
        REPORT_PATH,
    }
    for path, content in baseline_entries.items():
        if path not in mutable_entries and output_entries[path] != content:
            raise ValueError(f"1.18 静态资源被意外修改：{path}")
    # Prefab 装配链是本次最重要的不变量，单独给出明确错误。
    for path in (
        "assets/00003/cd_phw_00_ub_00_0163.prefab",
        "assets/00004/cd_phw_00_ub_00_0163_index01.prefab",
        "assets/00005/cd_phw_00_ub_00_0163_index02.prefab",
    ):
        if output_entries[path] != baseline_entries[path]:
            raise ValueError(f"1.18 Prefab 不变量失效：{path}")
    replacements = _json_object(output_entries, REPLACEMENTS_PATH)
    property_specs = [
        item
        for item in replacements.get("files", [])
        if isinstance(item, dict) and item.get("payload") == BASELINE_PROPERTY_PAYLOAD
    ]
    if len(property_specs) != 1:
        raise ValueError("成品 PAC_XML 声明不是唯一命中")
    property_content = output_entries[BASELINE_PROPERTY_PAYLOAD]
    if (
        property_specs[0].get("sha256") != _sha256(property_content)
        or property_specs[0].get("size") != len(property_content)
    ):
        raise ValueError("成品 PAC_XML 声明与载荷不一致")
    profile_specs = _profile_payload_specs(
        _json_object(output_entries, PROFILED_REPLACEMENTS_PATH)
    )
    for profile, payload_path in PROFILE_PAYLOAD_PATHS.items():
        if profile_specs[profile]["sha256"] != _sha256(output_entries[payload_path]):
            raise ValueError(f"1.18.2 {profile} 声明哈希与载荷不一致")
    _verify_hash(
        "成品验证 donor", donor_path.read_bytes(), NATIVE_BACK_DONOR_SHA256
    )


def _parse_args() -> argparse.Namespace:
    """解析生成器命令行参数。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True, help="1.18 cdmod 路径")
    parser.add_argument("--donor", type=Path, required=True, help="第一张图实际 20027 PAC")
    parser.add_argument("--game-dir", type=Path, required=True, help="当前游戏根目录")
    parser.add_argument("--output", type=Path, required=True, help="1.18.2 输出路径")
    return parser.parse_args()


def main() -> int:
    """生成并打印可审计摘要。"""
    args = _parse_args()
    result = build_mod(args.baseline, args.donor, args.game_dir, args.output)
    print(f"输出：{result.output_path}")
    print(f"SHA-256：{result.package_sha256}")
    for audit in result.profiles:
        print(
            f"{audit.profile}: selected={audit.selected_vertex_count} "
            f"changed_vertices={audit.changed_vertex_count} "
            f"changed_bytes={audit.changed_byte_count} "
            f"selected_error={audit.selected_max_error:.9f} "
            f"unselected_error={audit.unselected_max_error:.9f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
