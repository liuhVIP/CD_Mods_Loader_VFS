"""基于 2.00.00 作者参考包生成 2.00.01 多目标敌人血条数值版。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from cdmm.services.cdmod_converter import _write_cdmod_zip
from cdmm.services.json_loader import extract_plaintext
from cdmm.services.pab_table_service import build_entry_bounds, parse_pabgh_index
from cdmm.services.pamt_index_service import get_game_pamt_index


REFERENCE_HTML = Path("files/0012/ui/xml/gamemain/play/subtitletagview.html")
REFERENCE_CSS = Path("files/0012/ui/xml/gamemain/play/subtitletagview.css")
REFERENCE_SKILL_BODY = Path("files/0008/gamedata/binary__/client/bin/skill.pabgb")
REFERENCE_SKILL_HEADER = Path("files/0008/gamedata/binary__/client/bin/skill.pabgh")
REFERENCE_JSON = "Healthbar_always_on_vanilla_multitarget_2.00.00.json"
SKILL_KEY = 1201


VALUE_COMPONENT = """
	<component name="PersistentProgressingGaugebar" script="UIGamePlayControlCommon_StatGauge"
		selector-gauge-progress-effect=".cpp-gauge-progress-effect"
		selector-gauge-first-background="#StatusGaugeBackground1"
		selector-gauge-background=".cpp-gauge-background2"
		selector-gauge-background-effect=".cpp-gauge-background-effect">
		<div id="StatusGaugeDimmed" css="fit-all"></div>
		<div id="StatusGaugeBackground">
			<div id="StatusGaugeBackgroundWrap">
				<div id="StatusGaugeBackground1"></div>
				<div id="StatusGaugeBackground2" css="cpp-gauge-background2"></div>
				<div id="StatusGaugeBackground3"></div>
			</div>
			<div id="StatusGaugeMaxValueBG" css="cpp-gauge-background" script="UIProgress" fillmode="right"></div>
			<div id="StatusGaugeBackgroundEffectWrap">
				<div id="StatusGaugeBackgroundEffect" css="cpp-gauge-background-effect"></div>
			</div>
			<div id="StatusGaugeProgressingWrap">
				<div id="StatusGaugeProgressing" css="cpp-gauge-progressing" script="UIProgress" fillmode="right"></div>
			</div>
			<div id="StatusGaugeCurrentValue" css="cpp-gauge-progress" script="UIProgress" fillmode="right" tipSelector=".cpp-progress-tip" tipHide100Percents="true">
				<div id="StatusGaugeTip" css="cpp-progress-tip"></div>
			</div>
			<div id="EnemyHPValueWrap" css="enemy-hp-value-wrap">
				<div id="EnemyHPCurrentValue" css="cpp-gauge-value enemy-hp-current-value">0</div>
				<div id="EnemyHPValueSlash" css="enemy-hp-value-slash">/</div>
				<div id="EnemyHPMaxValue" css="cpp-gauge-max-value enemy-hp-max-value">0</div>
			</div>
			<div id="StatusGaugeCurrentValueEffect" css="cpp-gauge-progress-effect" script="UIProgress" fillmode="right"></div>
			<div id="StatusGaugeFX" css="cpp-gauge-fx" script="UIGamePlayControlCommonDefault"></div>
		</div>
	</component>
"""


VALUE_CSS = """

/* 2.00.01 numeric readout bound to the same native progressing StatGauge. */
.enemy-hp-value-wrap { position: absolute; left: 0; top: -22px; width: 120px; height: 18px; display: flex; justify-content: center; align-items: center; white-space: nowrap; transform: scaleX(1.428571) scaleY(1.428571); transform-origin: center bottom; }
.enemy-hp-current-value { color: #ffffff; font-size: 13px; font-family: Basefont; text-align: right; text-shadow: 1px 1px 1px #000000; }
.enemy-hp-value-slash { color: #d8d8d8; font-size: 12px; font-family: Basefont; padding: 0 3px; text-shadow: 1px 1px 1px #000000; }
.enemy-hp-max-value { color: #d8d8d8; font-size: 13px; font-family: Basefont; text-align: left; text-shadow: 1px 1px 1px #000000; }
/* 仅使用游戏 UI CSS 已验证支持的简单后代选择器。 */
.headup-hp-stat-wrap.cpp-gauge-empty .enemy-hp-value-wrap { display: none; opacity: 0; }
"""


def _read_current_table(game_dir: Path, name: str) -> bytes:
    entry = get_game_pamt_index(game_dir).find_in_dir("0008", f"gamedata/{name}")
    if entry is None:
        raise ValueError(f"当前游戏 0008 未找到 gamedata/{name}")
    content, _ = extract_plaintext(entry)
    return content


def _record(table: bytes, header: bytes, key: int) -> tuple[int, int, bytes]:
    key_size, offsets = parse_pabgh_index(header, "skill")
    bounds = build_entry_bounds(table, key_size, offsets)
    if key not in bounds:
        raise ValueError(f"SkillInfo 未找到 key={key}")
    start, end, _name, _name_end = bounds[key]
    return start, end, table[start:end]


def _build_ui(reference_dir: Path) -> tuple[bytes, bytes]:
    html = (reference_dir / REFERENCE_HTML).read_text(encoding="utf-8-sig")
    css = (reference_dir / REFERENCE_CSS).read_text(encoding="utf-8-sig")
    old_component = 'component="CharacterStat.StatusProgressingGaugebar" statName="Hp"'
    new_component = 'component="SubtitleTagView.PersistentProgressingGaugebar" statName="Hp"'
    if html.count(old_component) != 2:
        raise ValueError("作者参考 HTML 的 StatusProgressingGaugebar 数量不符合预期")
    # 第一处位于注释中的原版示例，只替换实际启用的第二处。
    split_at = html.find(old_component)
    active_at = html.find(old_component, split_at + len(old_component))
    html = html[:active_at] + html[active_at:].replace(old_component, new_component, 1)
    marker = '\n\t<component name="DamageDebugHeadUp"'
    if html.count(marker) != 1:
        raise ValueError("无法定位自定义实时血条组件插入点")
    html = html.replace(marker, VALUE_COMPONENT + marker, 1)
    css = css.rstrip() + VALUE_CSS
    if "StatusGaugebarWithText" in html:
        raise ValueError("修复包禁止使用 StatusGaugebarWithText")
    return html.encode("utf-8"), css.encode("utf-8")


def _character_changes(reference_dir: Path) -> list[dict[str, object]]:
    source = json.loads((reference_dir / REFERENCE_JSON).read_text(encoding="utf-8-sig"))
    changes: list[dict[str, object]] = []
    for item in source.get("patches", []):
        changes.append(
            {
                "offset": int(item["offset"]),
                "original": str(item["original"]),
                "patched": str(item["patched"]),
                "label": str(item.get("description") or "Healthbar character helper"),
            }
        )
    if len(changes) != 3:
        raise ValueError("作者参考包 CharacterInfo 补丁数量不符合预期")
    return changes


def build(game_dir: Path, reference_dir: Path, output: Path) -> Path:
    game_dir = game_dir.resolve()
    reference_dir = reference_dir.resolve()
    output = output.resolve()
    html, css = _build_ui(reference_dir)

    current_body = _read_current_table(game_dir, "skill.pabgb")
    current_header = _read_current_table(game_dir, "skill.pabgh")
    reference_body = (reference_dir / REFERENCE_SKILL_BODY).read_bytes()
    reference_header = (reference_dir / REFERENCE_SKILL_HEADER).read_bytes()
    current_start, _current_end, current_record = _record(
        current_body, current_header, SKILL_KEY
    )
    _ref_start, _ref_end, reference_record = _record(
        reference_body, reference_header, SKILL_KEY
    )

    legacy = {
        "modinfo": {
            "title": "Enemy HP Bar Always On Multitarget with Values 2.00.01",
            "version": "2.00.01.3",
            "author": "Blablup / Caites / cdmm",
            "description": "作者多目标实时血条基线，并在同一 StatGauge 数据绑定中显示当前/最大 HP。",
        },
        "allow_partial_apply": False,
        "patches": [
            {
                "game_file": "gamedata/characterinfo.pabgb",
                "changes": _character_changes(reference_dir),
            },
            {
                "game_file": "gamedata/skill.pabgb",
                "changes": [
                    {
                        "offset": current_start,
                        "original": current_record.hex(),
                        "patched": reference_record.hex(),
                        "label": "Passive_Player_BasicSkill key=1201 current-record replay",
                    }
                ],
            },
        ],
    }

    payloads = {
        "assets/00000/subtitletagview.html": html,
        "assets/00001/subtitletagview.css": css,
    }
    replacements = {
        "schema": 1,
        "files": [
            {
                "target": "ui/subtitletagview.html",
                "pamt_dir": "0012",
                "payload": "assets/00000/subtitletagview.html",
                "sha256": hashlib.sha256(html).hexdigest(),
                "size": len(html),
                "allow_new": False,
                "allow_table_replace": False,
            },
            {
                "target": "ui/subtitletagview.css",
                "pamt_dir": "0012",
                "payload": "assets/00001/subtitletagview.css",
                "sha256": hashlib.sha256(css).hexdigest(),
                "size": len(css),
                "allow_new": False,
                "allow_table_replace": False,
            },
        ],
    }
    manifest = {
        "format": "crimson-mod-package",
        "format_version": 1,
        "id": "cdmm.enemy-hp-bar-always-on-multitarget-values-2-00-01",
        "name": "Enemy HP Bar Always On Multitarget with Values 2.00.01",
        "version": "2.00.01.3",
        "author": "Blablup / Caites / cdmm",
        "description": "基于作者 2.00.00 常驻多目标实时血条，适配 2.00.01 并显示当前/最大 HP。",
        "dependencies": [],
        "source": {
            "format": "current-record-replay-plus-file-replacement",
            "game_version": "2.00.01",
            "reference": reference_dir.name,
            "skill_key": SKILL_KEY,
            "current_skill_record_sha256": hashlib.sha256(current_record).hexdigest(),
            "reference_skill_record_sha256": hashlib.sha256(reference_record).hexdigest(),
        },
        "components": [
            {"type": "legacy-byte-patch", "path": "patches/legacy.json"},
            {"type": "file-replacement", "path": "files/replacements.json", "file_count": 2},
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    _write_cdmod_zip(
        output,
        {
            "manifest.json": manifest,
            "patches/legacy.json": legacy,
            "files/replacements.json": replacements,
            **payloads,
        },
    )
    return output


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-dir", type=Path, required=True)
    parser.add_argument("--reference-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    print(build(args.game_dir, args.reference_dir, args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
