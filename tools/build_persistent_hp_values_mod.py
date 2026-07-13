"""基于 Persistent HP Bars 生成“持久血条 + HP 数值”模组。

合并包在游戏原生持续型 StatGauge 结构中显示当前与最大生命值；增强版
额外启用受伤/治疗残影和生命值刻度。工具不会改写源 ``.cdmod``。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path
from typing import Any

# 确定性 ZIP 时间戳，保证相同输入产生相同包。
DETERMINISTIC_ZIP_TIMESTAMP = (2026, 7, 13, 0, 0, 0)

# 基础模组容器中的资源路径。
HTML_ASSET_PATH = "assets/00001/subtitletagview.html"
CSS_ASSET_PATH = "assets/00000/subtitletagview.css"
REPLACEMENTS_PATH = "files/replacements.json"
MANIFEST_PATH = "manifest.json"

# 只匹配基础模组中实际启用的 HP 控件，避开旁边被注释的旧控件。
HP_WIDGET_PATTERN = re.compile(
    r'(?P<indent>\t\t)<widget id="HPGauge" class="headup-hp-stat-wrap" '
    r'component="CharacterStat\.StatusProgressingGaugebar" statName="Hp">.*?'
    r'(?P=indent)</widget>',
    re.DOTALL,
)

# 使用本页自定义的持续型 StatGauge，避免带文字模板自己的显示层让血条淡出。
HP_VALUE_WIDGET = (
    '<widget id="HPGauge" class="headup-hp-stat-wrap" '
    'component="SubtitleTagView.PersistentHPGauge" statName="Hp"></widget>'
)

# 本地组件完整复用原 StatusProgressingGaugebar 的结构，并在同一数据绑定中加入数值节点。
LOCAL_HP_COMPONENT = """<component name="PersistentHPGauge" scriptobject="UIGamePlayControlCommon_StatGauge"
		selector-gauge-progress-effect=".cpp-gauge-progress-effect"
		selector-gauge-background=".cpp-gauge-background2"
		selector-gauge-background-effect=".cpp-gauge-background-effect">
		<div id="StatusGaugeDimmed" class="headup-hp-stat-gauge-dimmed"></div>
		<div id="StatusGaugeBackground" class="headup-hp-stat-gauge-wrap">
			<div id="StatusGaugeMaxValueBG" class="cpp-gauge-background headup-hp-stat-gauge-bar-bg" scriptobject="UIProgress" fillmode="right"></div>
			<div id="StatusGaugeProgressing" class="cpp-gauge-progressing headup-hp-stat-gauge-bar-back" scriptobject="UIProgress" fillmode="right"></div>
			<div id="StatusGaugeCurrentValue" class="cpp-gauge-progress headup-hp-stat-gauge-bar-front" scriptobject="UIProgress" fillmode="right" tipSelector=".cpp-progress-tip" tipHide100Percents="true">
				<div id="StatusGaugeTip" class="cpp-progress-tip headup-hp-stat-gauge-tip"></div>
			</div>
			<div id="StatusGaugeCurrentValueEffect" class="cpp-gauge-progress-effect" scriptobject="UIProgress" fillmode="right"></div>
			<div class="headup-hp-value-wrap">
				<div class="cpp-gauge-value headup-hp-current-value">0</div>
				<div class="headup-hp-value-slash">/</div>
				<div class="cpp-gauge-max-value headup-hp-max-value">0</div>
			</div>
		</div>
	</component>"""

# 增强版在 25%、50%、75% 位置加入固定刻度，不参与 StatGauge 数据绑定。
HP_TICKS_HTML = """
			<div class="headup-hp-ticks">
				<div class="headup-hp-tick headup-hp-tick-25"></div>
				<div class="headup-hp-tick headup-hp-tick-50"></div>
				<div class="headup-hp-tick headup-hp-tick-75"></div>
			</div>"""

# 刻度插在数值区域之前，确保只存在于本地 HP 组件内部。
HP_TICKS_INSERT_MARKER = '\n\t\t\t<div class="headup-hp-value-wrap">'

# 将本地 HP 组件放在伤害调试组件之前，保持 SubtitleTagView 的组件层级清晰。
LOCAL_COMPONENT_INSERT_MARKER = '\n\t<component name="DamageDebugHeadUp"'

# 数值区域位于血条正上方，保持紧凑并为五倍生命值预留宽度。
HP_VALUE_CSS = """

/* 敌人当前生命值 / 最大生命值 */
.headup-hp-value-wrap { position: absolute; left: 0; top: -22px; width: 120px; height: 18px; display: flex; justify-content: center; align-items: center; }
.headup-hp-value-set { width: 120px; height: 18px; display: flex; justify-content: center; align-items: center; white-space: nowrap; }
.headup-hp-current-value { color: #ffffff; font-size: 13px; font-family: Basefont; text-align: right; text-shadow: 1px 1px 1px #000000; }
.headup-hp-value-slash { color: #d8d8d8; font-size: 12px; font-family: Basefont; padding: 0 3px; text-shadow: 1px 1px 1px #000000; }
.headup-hp-max-value { color: #d8d8d8; font-size: 13px; font-family: Basefont; text-align: left; text-shadow: 1px 1px 1px #000000; }
"""

# 增强版显示原生 progressing gauge 残影，并在主血条上叠加三条刻度。
ENHANCED_HP_CSS = """

/* 增强版：受伤/治疗残影与生命值刻度 */
.headup-hp-stat-gauge-bar-back { opacity: 0.72; }
.headup-hp-stat-gauge-bar-back.cpp-decrease { background-color: #ffffff; opacity: 0.78; }
.headup-hp-stat-gauge-bar-back.cpp-increase { background-color: #54c8a5; opacity: 0.82; }
.headup-hp-ticks { position: absolute; left: 0; top: 0; width: 120px; height: 5px; }
.headup-hp-tick { position: absolute; top: 0; width: 1px; height: 5px; background-color: #ffffff; opacity: 0.48; }
.headup-hp-tick-25 { left: 25%; }
.headup-hp-tick-50 { left: 50%; }
.headup-hp-tick-75 { left: 75%; }
"""

# 两种发布变体使用独立 ID，便于加载器和管理器区分。
VARIANT_METADATA = {
    "base": {
        "id": "n-persistent-hp-bars-with-values",
        "name": "Persistent HP Bars with Values",
        "version": "1.1",
        "description": "保留持久敌人血条，并在血条上方显示当前生命值与最大生命值。",
    },
    "enhanced": {
        "id": "n-persistent-hp-bars-with-values-enhanced",
        "name": "Persistent HP Bars with Values Enhanced",
        "version": "2.0",
        "description": "显示持久敌人血条、当前与最大生命值，并增加受伤/治疗残影和生命值刻度。",
    },
}

# 合并模组沿用基础血条作者并标注本次扩展。
MOD_AUTHOR = "Caites / N++"


def add_hp_values(
    html: bytes,
    css: bytes,
    *,
    variant: str = "base",
) -> tuple[bytes, bytes]:
    """把唯一的敌人 HP 控件替换为原生带数值版本。"""
    if variant not in VARIANT_METADATA:
        raise ValueError(f"未知 HP 数值变体：{variant}")
    html_text = html.decode("utf-8")
    matches = list(HP_WIDGET_PATTERN.finditer(html_text))
    if len(matches) != 1:
        raise ValueError(f"基础模组 HP 控件匹配数量错误：{len(matches)}")
    match = matches[0]
    indent = match.group("indent")
    replacement = indent + HP_VALUE_WIDGET
    modified_html = html_text[: match.start()] + replacement + html_text[match.end() :]
    if html_text.count(LOCAL_COMPONENT_INSERT_MARKER) != 1:
        raise ValueError("无法唯一定位 DamageDebugHeadUp 组件插入点")
    local_component = LOCAL_HP_COMPONENT
    if variant == "enhanced":
        if local_component.count(HP_TICKS_INSERT_MARKER) != 1:
            raise ValueError("无法唯一定位 HP 刻度插入点")
        local_component = local_component.replace(
            HP_TICKS_INSERT_MARKER,
            HP_TICKS_HTML + HP_TICKS_INSERT_MARKER,
            1,
        )
    modified_html = modified_html.replace(
        LOCAL_COMPONENT_INSERT_MARKER,
        "\n\t" + local_component + LOCAL_COMPONENT_INSERT_MARKER,
        1,
    )

    css_text = css.decode("utf-8")
    if ".headup-hp-value-wrap" in css_text:
        raise ValueError("基础模组已经包含 HP 数值样式，拒绝重复追加")
    modified_css = css_text.rstrip() + HP_VALUE_CSS
    if variant == "enhanced":
        modified_css += ENHANCED_HP_CSS

    if "SubtitleTagView.PersistentHPGauge" not in modified_html:
        raise ValueError("持续型 HP 数值组件替换失败")
    if "CharacterStat.StatusGaugebarWithText" in modified_html:
        raise ValueError("不得继续使用会淡出的 StatusGaugebarWithText")
    if "cpp-damage-debug-head-up !cpp-none" not in modified_html:
        raise ValueError("调试伤害组件必须继续保持隐藏")
    return modified_html.encode("utf-8"), modified_css.encode("utf-8")


def _read_json(archive: zipfile.ZipFile, path: str) -> dict[str, Any]:
    """读取容器中的 UTF-8 JSON 对象。"""
    value = json.loads(archive.read(path).decode("utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} 根节点不是 JSON 对象")
    return value


def _update_replacements(document: dict[str, Any], payloads: dict[str, bytes]) -> None:
    """刷新 UI replacement 的大小与 SHA-256。"""
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


def build_mod(source_path: Path, output_path: Path, *, variant: str = "base") -> Path:
    """读取基础模组并生成持久血条与 HP 数值合并包。"""
    source_path = source_path.resolve()
    output_path = output_path.resolve()
    if source_path == output_path:
        raise ValueError("输出路径不能覆盖基础模组")

    with zipfile.ZipFile(source_path) as archive:
        manifest = _read_json(archive, MANIFEST_PATH)
        replacements = _read_json(archive, REPLACEMENTS_PATH)
        html, css = add_hp_values(
            archive.read(HTML_ASSET_PATH),
            archive.read(CSS_ASSET_PATH),
            variant=variant,
        )

    metadata = VARIANT_METADATA[variant]
    manifest.update(
        {
            "id": metadata["id"],
            "name": metadata["name"],
            "version": metadata["version"],
            "author": MOD_AUTHOR,
            "description": metadata["description"],
            "source": {
                "format": "derived-file-replacement",
                "base_mod": "Persistent Enemy HP Bars 1.1 by Caites",
            },
        }
    )
    payloads = {HTML_ASSET_PATH: html, CSS_ASSET_PATH: css}
    _update_replacements(replacements, payloads)
    _write_cdmod(
        output_path,
        {
            MANIFEST_PATH: _encode_json(manifest),
            REPLACEMENTS_PATH: _encode_json(replacements),
            **payloads,
        },
    )
    return output_path


def _parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="生成持久敌人血条与 HP 数值模组")
    parser.add_argument("--source", type=Path, required=True, help="Persistent HP Bars 源 cdmod")
    parser.add_argument("--output", type=Path, required=True, help="新 cdmod 输出路径")
    parser.add_argument(
        "--variant",
        choices=tuple(VARIANT_METADATA),
        default="base",
        help="base=基础数值版；enhanced=残影与刻度增强版",
    )
    return parser.parse_args()


def main() -> int:
    """命令行入口。"""
    args = _parse_args()
    result = build_mod(args.source, args.output, variant=args.variant)
    print(f"HP 数值模组：{result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
