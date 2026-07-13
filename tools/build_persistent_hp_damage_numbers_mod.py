"""基于 Persistent HP Bars 生成“血条 + 单次伤害数字”合并模组，尝试了没成功。

工具只解除原生 ``DamageDebugHeadUp`` 组件的隐藏状态，保留基础模组的
血条修改，并继续隐藏连击累计伤害组件。源 ``.cdmod`` 不会被改写。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any

# 确定性 ZIP 时间戳，保证相同输入生成相同包。
DETERMINISTIC_ZIP_TIMESTAMP = (2026, 7, 13, 0, 0, 0)

# 基础模组内的 UI 资源路径。
HTML_ASSET_PATH = "assets/00001/subtitletagview.html"
CSS_ASSET_PATH = "assets/00000/subtitletagview.css"
REPLACEMENTS_PATH = "files/replacements.json"
MANIFEST_PATH = "manifest.json"

# 原版单次伤害数字组件的隐藏声明。
HIDDEN_DAMAGE_HEADUP = (
    '<component name="DamageDebugHeadUp" '
    'class="no-pickable fit-all cpp-damage-debug-head-up !cpp-none"'
)

# 合并包中启用单次伤害数字后的声明。
VISIBLE_DAMAGE_HEADUP = (
    '<component name="DamageDebugHeadUp" '
    'class="no-pickable fit-all cpp-damage-debug-head-up"'
)

# 连击累计数字必须继续隐藏，避免与单次伤害数字同时出现。
HIDDEN_COMBO_HEADUP = (
    '<component name="DamageComboHeadUp" '
    'class="no-pickable fit-all cpp-damage-combo-head-up !cpp-none"'
)

# 启用连击累计数字后的组件声明。
VISIBLE_COMBO_HEADUP = (
    '<component name="DamageComboHeadUp" '
    'class="no-pickable fit-all cpp-damage-combo-head-up"'
)

# 合并模组的稳定版本与作者信息。
COMBINED_MOD_VERSION = "1.0"
COMBINED_MOD_AUTHOR = "Caites / N++"

# 两种伤害数字模式的独立元数据。
MODE_METADATA = {
    "single": {
        "id": "n-persistent-hp-bars-single-hit-damage-numbers",
        "name": "Persistent HP Bars and Single Hit Damage Numbers",
        "description": "保留持久敌人血条，并启用游戏原生的单次伤害数字组件。",
    },
    "combo": {
        "id": "n-persistent-hp-bars-damage-and-combo-numbers",
        "name": "Persistent HP Bars Damage and Combo Numbers",
        "description": "保留持久敌人血条，并启用游戏原生的单次伤害与连击累计数字组件。",
    },
}


def enable_damage_numbers(html: bytes, *, include_combo: bool) -> bytes:
    """解除单次伤害数字隐藏，并按模式决定是否启用累计数字。"""
    text = html.decode("utf-8")
    if text.count(HIDDEN_DAMAGE_HEADUP) != 1:
        raise ValueError("基础模组没有唯一的 DamageDebugHeadUp 隐藏声明")
    if text.count(HIDDEN_COMBO_HEADUP) != 1:
        raise ValueError("基础模组没有唯一的 DamageComboHeadUp 隐藏声明")
    modified = text.replace(HIDDEN_DAMAGE_HEADUP, VISIBLE_DAMAGE_HEADUP, 1)
    if HIDDEN_DAMAGE_HEADUP in modified:
        raise ValueError("DamageDebugHeadUp 隐藏类移除失败")
    if include_combo:
        modified = modified.replace(HIDDEN_COMBO_HEADUP, VISIBLE_COMBO_HEADUP, 1)
        if HIDDEN_COMBO_HEADUP in modified:
            raise ValueError("DamageComboHeadUp 隐藏类移除失败")
    elif HIDDEN_COMBO_HEADUP not in modified:
        raise ValueError("single 模式不应启用 DamageComboHeadUp")
    return modified.encode("utf-8")


def enable_single_hit_damage_numbers(html: bytes) -> bytes:
    """兼容调用：只启用单次伤害数字。"""
    return enable_damage_numbers(html, include_combo=False)


def _read_json(archive: zipfile.ZipFile, path: str) -> dict[str, Any]:
    """读取容器中的 UTF-8 JSON 对象。"""
    value = json.loads(archive.read(path).decode("utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} 根节点不是 JSON 对象")
    return value


def _update_replacements(document: dict[str, Any], html: bytes, css: bytes) -> None:
    """刷新两个 UI replacement 的大小与 SHA-256。"""
    payloads = {HTML_ASSET_PATH: html, CSS_ASSET_PATH: css}
    files = document.get("files")
    if not isinstance(files, list):
        raise ValueError("replacements.json 缺少 files 数组")
    found: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            continue
        payload_path = item.get("payload")
        payload = payloads.get(payload_path)
        if payload is None:
            continue
        item["size"] = len(payload)
        item["sha256"] = hashlib.sha256(payload).hexdigest()
        found.add(payload_path)
    if found != set(payloads):
        raise ValueError(f"replacements.json 资源声明不完整：{sorted(found)}")


def _update_manifest(document: dict[str, Any], mode: str) -> None:
    """写入合并模组元数据，同时保留基础模组来源说明。"""
    metadata = MODE_METADATA[mode]
    document.update(
        {
            "id": metadata["id"],
            "name": metadata["name"],
            "version": COMBINED_MOD_VERSION,
            "author": COMBINED_MOD_AUTHOR,
            "description": metadata["description"],
            "source": {
                "format": "derived-file-replacement",
                "base_mod": "Persistent Enemy HP Bars 1.1 by Caites",
            },
        }
    )


def _encode_json(document: dict[str, Any]) -> bytes:
    """按项目容器格式编码 UTF-8 JSON。"""
    return (
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n"
    )


def _write_cdmod(output_path: Path, documents: dict[str, bytes]) -> None:
    """使用稳定顺序和时间戳写入 `.cdmod` ZIP。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        output_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for archive_path in sorted(documents):
            info = zipfile.ZipInfo(archive_path, DETERMINISTIC_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, documents[archive_path], compresslevel=9)


def build_combined_mod(source_path: Path, output_path: Path, *, mode: str) -> Path:
    """读取基础模组并生成血条与单次伤害数字合并包。"""
    source_path = source_path.resolve()
    output_path = output_path.resolve()
    if source_path == output_path:
        raise ValueError("输出路径不能覆盖基础模组")

    with zipfile.ZipFile(source_path) as archive:
        manifest = _read_json(archive, MANIFEST_PATH)
        replacements = _read_json(archive, REPLACEMENTS_PATH)
        css = archive.read(CSS_ASSET_PATH)
        html = enable_damage_numbers(
            archive.read(HTML_ASSET_PATH),
            include_combo=mode == "combo",
        )

    _update_manifest(manifest, mode)
    _update_replacements(replacements, html, css)
    _write_cdmod(
        output_path,
        {
            MANIFEST_PATH: _encode_json(manifest),
            REPLACEMENTS_PATH: _encode_json(replacements),
            CSS_ASSET_PATH: css,
            HTML_ASSET_PATH: html,
        },
    )
    return output_path


def _parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="生成持久血条与单次伤害数字合并模组")
    parser.add_argument("--source", type=Path, required=True, help="Persistent HP Bars 源 cdmod")
    parser.add_argument("--output", type=Path, required=True, help="合并 cdmod 输出路径")
    parser.add_argument(
        "--mode",
        choices=tuple(MODE_METADATA),
        required=True,
        help="single=单次伤害；combo=单次伤害与连击累计",
    )
    return parser.parse_args()


def main() -> int:
    """命令行入口。"""
    args = _parse_args()
    result = build_combined_mod(args.source, args.output, mode=args.mode)
    print(f"合并模组：{result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
