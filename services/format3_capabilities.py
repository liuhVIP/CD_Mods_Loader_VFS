"""Format 3 table writer 能力声明。

独立加载器目前不是完整 GUI 管理器那套全量 Format 3 校验器，所以这里先把
“每个 table 当前明确支持哪些字段形态”收口到一个地方，避免支持边界散落在
各 writer 内部难以维护。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from cdmm.services.format3_parser import Format3Intent
from cdmm.services.format3_runtime import Format3SkippedIntent
from cdmm.services.format3_iteminfo_whole_writer import ITEMINFO_WHOLE_TABLE_DIRECT_FIELDS


@dataclass(frozen=True)
class Format3FieldRule:
    """单条字段支持规则。"""

    pattern: re.Pattern[str]
    reason_when_miss: str


@dataclass(frozen=True)
class Format3TableCapability:
    """单个表当前可接受的 intent 能力声明。"""

    table_name: str
    field_rules: tuple[Format3FieldRule, ...]
    supports_whole_table: bool = False


_ITEMINFO_CAPABILITY = Format3TableCapability(
    table_name="iteminfo",
    # 当前独立加载器已被真实游戏验证成功的窄支持仍然是
    # prefab_data_list[N].tribe_gender_list；这轮继续向 drop_default_data
    # 的原始字段扩展。先把边界显式声明出来，后续继续迁 iteminfo nested path
    # / whole-table writer 时只扩这里。
    field_rules=(
        Format3FieldRule(
            pattern=re.compile(r"^prefab_data_list\[\d+]\.tribe_gender_list$"),
            reason_when_miss=(
                "iteminfo 当前仅支持 prefab_data_list[N].tribe_gender_list、"
                "drop_default_data.drop_enchant_level、"
                "drop_default_data.socket_item_list、"
                "drop_default_data.add_socket_material_item_list、"
                "drop_default_data.default_sub_item、"
                "drop_default_data.socket_valid_count、"
                "drop_default_data.use_socket，"
                "以及已迁入的 whole-table 字段（如 cooltime、equipable_hash 等）"
            ),
        ),
        Format3FieldRule(
            pattern=re.compile(
                r"^drop_default_data\.(drop_enchant_level|socket_item_list|"
                r"add_socket_material_item_list|default_sub_item|"
                r"socket_valid_count|use_socket)$"
            ),
            reason_when_miss=(
                "iteminfo 当前仅支持 prefab_data_list[N].tribe_gender_list、"
                "drop_default_data.drop_enchant_level、"
                "drop_default_data.socket_item_list、"
                "drop_default_data.add_socket_material_item_list、"
                "drop_default_data.default_sub_item、"
                "drop_default_data.socket_valid_count、"
                "drop_default_data.use_socket，"
                "以及已迁入的 whole-table 字段（如 cooltime、equipable_hash 等）"
            ),
        ),
        Format3FieldRule(
            pattern=re.compile(
                "^("
                + "|".join(re.escape(field) for field in sorted(ITEMINFO_WHOLE_TABLE_DIRECT_FIELDS))
                + ")$"
            ),
            reason_when_miss=(
                "iteminfo 当前仅支持 prefab_data_list[N].tribe_gender_list、"
                "drop_default_data.drop_enchant_level、"
                "drop_default_data.socket_item_list、"
                "drop_default_data.add_socket_material_item_list、"
                "drop_default_data.default_sub_item、"
                "drop_default_data.socket_valid_count、"
                "drop_default_data.use_socket，"
                "以及已迁入的 whole-table 字段（如 cooltime、equipable_hash 等）"
            ),
        ),
    ),
    supports_whole_table=True,
)


_CAPABILITIES: dict[str, Format3TableCapability] = {
    "iteminfo": _ITEMINFO_CAPABILITY,
    "interactioninfo": Format3TableCapability(
        table_name="interactioninfo",
        field_rules=(
            Format3FieldRule(
                pattern=re.compile(
                    r"^(interaction_type|_interactionType|"
                    r"interaction_pivot_list\[0]\.(raw_a|raw_b))$"
                ),
                reason_when_miss=(
                    "interactioninfo 当前仅支持 interaction_type / _interactionType，"
                    "以及 interaction_pivot_list[0].raw_a/raw_b 字段"
                ),
            ),
        ),
        supports_whole_table=False,
    ),
    "skill": Format3TableCapability(
        table_name="skill",
        field_rules=(
            Format3FieldRule(
                pattern=re.compile(r"^(_useResourceStatList|_buffLevelList)$"),
                reason_when_miss="skill 当前仅支持 _useResourceStatList、_buffLevelList",
            ),
        ),
        supports_whole_table=True,
    ),
    "buffinfo": Format3TableCapability(
        table_name="buffinfo",
        field_rules=(
            Format3FieldRule(
                pattern=re.compile(r"^_?[A-Za-z]\w*$"),
                reason_when_miss=(
                    "buffinfo 当前仅支持 clean-room parser 可解析的 wrapper 字段和 buff_data_list item path"
                ),
            ),
            Format3FieldRule(
                pattern=re.compile(
                    r"^buff_data_list\[\d+\]\.(absent_flag|leading_lookup|data\.(base|variant)\..+)$"
                ),
                reason_when_miss=(
                    "buffinfo 当前仅支持 clean-room parser 可解析的 wrapper 字段和 buff_data_list item path"
                ),
            ),
        ),
        supports_whole_table=False,
    ),
    "characterinfo": Format3TableCapability(
        table_name="characterinfo",
        field_rules=(
            Format3FieldRule(
                pattern=re.compile(
                    r"^(upper_chart\.group_lookup|lower_chart\.group_lookup|"
                    r"lookup_22|lookup_24|skeleton_name|lookup_25|flag_c)$"
                ),
                reason_when_miss=(
                    "characterinfo 当前仅支持 upper_chart.group_lookup、lower_chart.group_lookup、"
                    "lookup_22、lookup_24、skeleton_name、lookup_25、flag_c"
                ),
            ),
        ),
        supports_whole_table=False,
    ),
    "dropsetinfo": Format3TableCapability(
        table_name="dropsetinfo",
        field_rules=(
            Format3FieldRule(
                pattern=re.compile(r"^drops$"),
                reason_when_miss="dropsetinfo 当前仅支持 drops 字段",
            ),
        ),
        supports_whole_table=False,
    ),
    "equipslotinfo": Format3TableCapability(
        table_name="equipslotinfo",
        field_rules=(
            Format3FieldRule(
                pattern=re.compile(r"^entries\[\d+]\.etl_hashes$"),
                reason_when_miss="equipslotinfo 当前仅支持 entries[N].etl_hashes 字段",
            ),
        ),
        supports_whole_table=False,
    ),
    "multichangeinfo": Format3TableCapability(
        table_name="multichangeinfo",
        field_rules=(
            Format3FieldRule(
                pattern=re.compile(
                    r"^fixed_material_data_list\[\d+\]\.(item_info|count)$"
                ),
                reason_when_miss=(
                    "multichangeinfo 当前仅支持 fixed_material_data_list[N].item_info "
                    "和 fixed_material_data_list[N].count"
                ),
            ),
        ),
        supports_whole_table=False,
    ),
    "stringinfo": Format3TableCapability(
        table_name="stringinfo",
        field_rules=(
            Format3FieldRule(
                pattern=re.compile(r"^_?buffer$"),
                reason_when_miss="stringinfo 当前仅支持 buffer / _buffer 字段",
            ),
        ),
        supports_whole_table=False,
    ),
    "storeinfo": Format3TableCapability(
        table_name="storeinfo",
        field_rules=(
            Format3FieldRule(
                pattern=re.compile(r"^(stock_data_list|_exchangeItemInfoListForSell)$"),
                reason_when_miss=(
                    "storeinfo 当前仅支持 stock_data_list / _exchangeItemInfoListForSell 字段"
                ),
            ),
        ),
        supports_whole_table=False,
    ),
}


def get_table_capability(table_name: str) -> Format3TableCapability | None:
    """返回 table 当前声明的能力。"""
    return _CAPABILITIES.get(table_name)


def partition_supported_intents(
    table_name: str,
    intents: list[Format3Intent],
) -> tuple[list[Format3Intent], tuple[Format3SkippedIntent, ...]]:
    """按能力声明把 intents 分成“可尝试执行”和“直接跳过”。"""
    capability = get_table_capability(table_name)
    if capability is None:
        return list(intents), ()

    supported: list[Format3Intent] = []
    skipped: list[Format3SkippedIntent] = []
    for intent in intents:
        if _matches_any_rule(intent, capability.field_rules):
            supported.append(intent)
            continue
        skipped.append(
            Format3SkippedIntent(
                intent=intent,
                reason=_first_reason(capability.field_rules),
            )
        )
    return supported, tuple(skipped)


def _matches_any_rule(
    intent: Format3Intent,
    rules: tuple[Format3FieldRule, ...],
) -> bool:
    """判断 intent 是否匹配任一已声明规则。"""
    for rule in rules:
        if rule.pattern.match(intent.field):
            return True
    return False


def _first_reason(rules: tuple[Format3FieldRule, ...]) -> str:
    """当前能力只有少量规则，统一拿首条规则原因即可。"""
    if not rules:
        return "当前表暂无可用字段能力声明"
    return rules[0].reason_when_miss
