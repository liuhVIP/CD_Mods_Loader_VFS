"""CharacterInfo PABGB 前缀解析器（2.02.00 wire 布局）。

`characterinfo` 记录有 200+ 字段，但 DMM/CrimsonForge 的 Field JSON 实际只会
改写两个位于记录前段的数组：

* `character_reward_data_list`：`CArray<{drop_set_info u32, reward_tag_type_flag
  u32, repeat_count u32}>`（元素 12 字节）；
* `equip_item_info_list`：`CArray<{equip_item_info u32, equip_drop_set_info u32,
  p0..p6 u64}>`（元素 64 字节）。

本模块按 wire 顺序解析从记录头到 `equip_item_info_list` 的完整前缀，返回这两个
数组的**记录内相对字节偏移与元素个数**，供 writer 做字节级拼接。前缀里任何一个字段宽度
对不上，整条记录解析失败并抛 `CharacterParseError`，调用方必须跳过而不是猜测。

布局来源是对 2.02.00 原表的实测核验（逐字段差分定位 + `.pabgh` 记录边界），
与游戏 EXE 反汇编得到的读序列一致。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from cdmm.services.pab_record_codec import Reader, RecordParseError

# 各数组允许的最大元素个数；用于在布局错位时尽早失败，而不是读出天文数字。
_MAX_WIDTH_ARRAYS = 65_536
_MAX_STRUCT_ARRAYS = 4_096
_MAX_INSPECT_LIST = 4_096
_MAX_CSTRING = 4_096


class CharacterParseError(ValueError):
    """CharacterInfo 记录无法按当前布局解析。"""


@dataclass(frozen=True)
class CharacterPrefix:
    """记录前缀解析结果（只覆盖到 `equip_item_info_list` 为止）。

    三个 `offset` 都是**相对记录起点**的偏移。
    """

    key: int
    reward_offset: int
    reward_count: int
    equip_offset: int
    equip_count: int
    prefix_end: int


def _f_flags(start: int, stop: int, skip: tuple[int, ...] = ()) -> tuple[tuple[str, str], ...]:
    """生成连续 `u8` 字段。"""
    return tuple(
        (f"f{index}", "u8")
        for index in range(start, stop + 1)
        if index not in skip
    )


# wire 顺序即解析顺序；`f60` 在原表中不存在。
_RECORD_FIELDS: tuple[tuple[str, object], ...] = (
    ("key", "u32"),
    ("string_key", "cstr"),
    ("is_blocked", "u8"),
    ("name", "localizable"),
    ("desc", "localizable"),
    ("ui_icon_path", "u32"),
    ("category", "u32"),
    ("character_edit_name", "cstr"),
    ("spawn_actor_type", "u8"),
    ("none_player_sub_type", "u8"),
    ("equip_info", "u32"),
    ("npc_info", "u32"),
    ("vehicle_info", "u16"),
    ("call_mercenary_cool_time", "u64"),
    ("call_mercenary_spawn_duration", "u64"),
    ("mercenary_cool_time_type", "u8"),
    ("f16_cv0_char", "u32"),
    ("f16_cv0_group", "u16"),
    ("f16_cv1_char", "u32"),
    ("f16_cv1_group", "u16"),
    ("character_game_play_data_name", "u32"),
    ("appearance_name", "u32"),
    ("character_prefab_path", "u32"),
    ("skeleton_name", "u32"),
    ("lookup_22", "u32"),
    ("lookup_23", "u32"),
    ("lookup_24", "u32"),
    ("lookup_25", "u32"),
    ("f25", "u32"),
    ("f26", "u32"),
    ("f27", "u32"),
    ("f28", "u32"),
    ("f29", "u32"),
    ("f30", "u32"),
    ("f31", "u32"),
    ("f32", "u32"),
    ("f33", "u32"),
    ("f34", "u8"),
    ("f35", "u8"),
    ("f36", "u8"),
    ("personality_info", "u16"),
    ("f37", "u8"),
    ("f38", "localizable"),
    ("f39", "localizable"),
    ("f40", "u32"),
    ("f41", "u8"),
    ("f42", "u16"),
    ("of_origin_ally_group", "u32"),
    ("of_origin_detect", "u16"),
    ("of_origin_skills", ("flags_array", 8)),
    ("of_mercenary_ally_group", "u32"),
    ("of_mercenary_detect", "u16"),
    ("of_mercenary_skills", ("flags_array", 8)),
    *_f_flags(45, 85, skip=(60,)),
    ("f86", "u32"),
    ("f87", "u32"),
    ("f88", "u32"),
    ("f89_skills", ("array", 8)),
    ("f90_skills", ("array", 8)),
    ("f91_skills", ("array", 8)),
    ("f92_skills", ("array", 8)),
    ("interaction_info_list", ("array", 4)),
    ("interaction_distance", "u32"),
    ("default_action_action_index", "u32"),
    ("default_share_value_index", ("array", 4)),
    ("character_weight", "u32"),
    ("battle_order_type", "u8"),
    ("character_type", "u8"),
    ("ui_map_texture_info", "u32"),
    ("map_icon_display_type", "u8"),
    ("knowledge_info", "u32"),
    ("knowledge_obtain_type", "u8"),
    ("inspect_data_list", ("inspect_array",)),
    ("character_group_info_list", ("array", 2)),
    ("visioning_data", "visioning"),
    ("max_aggro_count", "u16"),
    ("personality_type", "u8"),
    ("character_tier", "u8"),
    ("character_region_info_list", ("array", 2)),
    ("character_age", "u8"),
    ("character_weapon_type", "cstr"),
    ("dialog_voice_info", "u16"),
    ("interaction_category_group_info", "u16"),
    ("detect_reaction_info", "u32"),
    ("character_pause_type", "u32"),
    ("owner_follow_type", "u8"),
    ("farm_drop_info_list", ("array", 4)),
    ("farm_breeding_target_list", ("array", 4)),
    ("farm_breeding_result_list", ("array", 4)),
    ("farm_breeding_cool_time", "u32"),
    ("character_reward_data_list", ("array", 12)),
    ("is_reward_drop_roll_by_create_actor", "u8"),
    ("mercenary_drop_info_list", ("array", 4)),
    ("equip_item_info_list", ("array", 64)),
)

# `InspectData` 元素 wire 布局（字段名仅用于错误信息）。
_INSPECT_FIELDS: tuple[tuple[str, object], ...] = (
    ("item_info", "u32"),
    ("gimmick_info", "u32"),
    ("character_info", "u32"),
    ("spawn_reason_hash", "u32"),
    ("socket_name", "cstr"),
    ("speak_character_info", "u32"),
    ("inspect_target_tag", "u32"),
    ("reward_own_knowledge", "u8"),
    ("reward_knowledge_info", "u32"),
    ("item_desc", "localizable"),
    ("board_key", "u32"),
    ("inspect_action_type", "u8"),
    ("gimmick_state_name_hash", "u32"),
    ("target_page_index", "u32"),
    ("is_left_page", "u8"),
    ("target_page_related_knowledge_info", "u32"),
    ("enable_read_after_reward", "u8"),
    ("refer_to_left_page_inspect_data", "u8"),
    ("inspect_effect_info_key", "u32"),
    ("inspect_complete_effect_info_key", "u32"),
)

REWARD_ELEMENT_SIZE = 12
EQUIP_ELEMENT_SIZE = 64


def parse_prefix(data: bytes, start: int, end: int) -> CharacterPrefix:
    """解析一条 CharacterInfo 记录的前缀；失败抛 `CharacterParseError`。"""
    reader = Reader(data, start, end)
    counts: dict[str, int] = {}
    offsets: dict[str, int] = {}
    try:
        for name, kind in _RECORD_FIELDS:
            offsets[name] = reader.pos - start
            count = _consume(data, reader, kind, name)
            if count is not None:
                counts[name] = count
    except RecordParseError as exc:
        pos = reader.pos - start
        raise CharacterParseError(f"{name} 越界（记录内偏移 {pos}）：{exc}") from exc
    except CharacterParseError:
        raise
    return CharacterPrefix(
        key=struct.unpack_from("<I", data, start)[0],
        reward_offset=offsets["character_reward_data_list"],
        reward_count=counts["character_reward_data_list"],
        equip_offset=offsets["equip_item_info_list"],
        equip_count=counts["equip_item_info_list"],
        prefix_end=reader.pos - start,
    )


def _consume(data: bytes, reader: Reader, kind: object, name: str) -> int | None:
    """按 kind 推进 reader；数组返回元素个数。"""
    if kind == "u8":
        reader.u8()
        return None
    if kind == "u16":
        reader.u16()
        return None
    if kind == "u32":
        reader.u32()
        return None
    if kind == "u64":
        reader.u64()
        return None
    if kind == "cstr":
        _read_cstring(reader, name)
        return None
    if kind == "localizable":
        reader.u8()
        reader.u64()
        _read_cstring(reader, name)
        return None
    if kind == "visioning":
        vision_type = reader.u8()
        if vision_type == 0:
            reader.u32()
        return None
    if isinstance(kind, tuple):
        tag = kind[0]
        if tag == "array":
            return _read_array(reader, name, int(kind[1]), _MAX_STRUCT_ARRAYS if kind[1] >= 12 else _MAX_WIDTH_ARRAYS)
        if tag == "flags_array":
            count = _read_array(reader, name, int(kind[1]), _MAX_STRUCT_ARRAYS)
            for _ in range(4):
                reader.u8()
            return count
        if tag == "inspect_array":
            return _read_inspect_array(data, reader, name)
    raise CharacterParseError(f"未知字段类型 {kind!r}（{name}）")


def _read_cstring(reader: Reader, name: str) -> None:
    length = reader.u32()
    if length > _MAX_CSTRING:
        raise CharacterParseError(f"{name} CString 长度不可信（{length}）")
    reader.skip(length)


def _read_array(reader: Reader, name: str, element_size: int, max_count: int) -> int:
    count = reader.u32()
    if count > max_count:
        raise CharacterParseError(f"{name} 元素个数不可信（{count}）")
    reader.skip(count * element_size)
    return count


def _read_inspect_array(data: bytes, reader: Reader, name: str) -> int:
    count = reader.u32()
    if count > _MAX_INSPECT_LIST:
        raise CharacterParseError(f"{name} 元素个数不可信（{count}）")
    for _ in range(count):
        for field_name, kind in _INSPECT_FIELDS:
            _consume(data, reader, kind, f"{name}[].{field_name}")
    return count

