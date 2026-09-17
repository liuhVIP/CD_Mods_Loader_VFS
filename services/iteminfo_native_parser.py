"""Crimson Desert ``iteminfo`` 记录级二进制解析器（独立实现）。

用途
----
Format 3（Field JSON）的 iteminfo 目标需要把整张表解析成 dict，改完字段后
再序列化回字节。本模块只做这一件事：``body`` 字节 <-> ItemInfo 记录 dict
列表，并保证「未改动时 parse -> serialize」与原始字节完全一致。

字段布局来源
------------
字段顺序与宽度按当前游戏归档（Crimson Desert 2.02.00）逐字段核对过：用
``.pabgh`` 给出的记录边界对整表 6813 条记录做 parse -> serialize 恒等回环，
输出与原始字节完全一致；同一批记录再用参考实现的字段路径 / 类型 / 字节范围
做交叉验证，逐条一致。因此下面的顺序是数据事实，不是猜测。

本模块是独立实现，不包含第三方源码；字段名与 Field JSON 规格保持一致，
这样模组里的 ``set`` 路径可以直接对应到解析结果的 dict key。
"""
from __future__ import annotations

import struct
from typing import Any


class _Reader:
    """Cursor-tracking binary reader over a single iteminfo body."""

    __slots__ = ("data", "pos", "rec_end")

    def __init__(
        self,
        data: bytes,
        pos: int = 0,
        rec_end: int | None = None,
    ) -> None:
        self.data = data
        self.pos = pos
        # Optional upper bound for the current record, used by the bounded
        # walkers so a malformed record cannot silently swallow the next one.
        self.rec_end = rec_end

    def u8(self) -> int:
        v = self.data[self.pos]
        self.pos += 1
        return v

    def u16(self) -> int:
        v = struct.unpack_from("<H", self.data, self.pos)[0]
        self.pos += 2
        return v

    def u32(self) -> int:
        v = struct.unpack_from("<I", self.data, self.pos)[0]
        self.pos += 4
        return v

    def u64(self) -> int:
        v = struct.unpack_from("<Q", self.data, self.pos)[0]
        self.pos += 8
        return v

    def i8(self) -> int:
        v = struct.unpack_from("<b", self.data, self.pos)[0]
        self.pos += 1
        return v

    def i64(self) -> int:
        v = struct.unpack_from("<q", self.data, self.pos)[0]
        self.pos += 8
        return v

    def f32(self) -> float:
        v = struct.unpack_from("<f", self.data, self.pos)[0]
        self.pos += 4
        return v

    def cstring(self) -> str:
        """Length-prefixed UTF-8 string (the length excludes any terminator)."""
        n = self.u32()
        s = self.data[self.pos:self.pos + n].decode("utf-8", errors="replace")
        self.pos += n
        return s

    def cstring_raw(self) -> bytes:
        """Same as :meth:`cstring` but returns raw bytes."""
        n = self.u32()
        b = self.data[self.pos:self.pos + n]
        self.pos += n
        return b

    def localizable(self) -> dict:
        """LocalizableString: u8 category + u64 index + CString default."""
        return {
            "category": self.u8(),
            "index": self.u64(),
            "default": self.cstring(),
        }

    def carray(self, elem_reader) -> list:
        n = self.u32()
        return [elem_reader(self) for _ in range(n)]

    def fixed_u32(self, count: int) -> list:
        """Fixed-size ``[u32; count]`` array (no implicit count on the wire)."""
        return [self.u32() for _ in range(count)]


class _Writer:
    """Append-only binary writer mirroring :class:`_Reader`."""

    __slots__ = ("buf",)

    def __init__(self) -> None:
        self.buf = bytearray()

    def u8(self, v: int) -> None:
        self.buf.append(v & 0xFF)

    def u16(self, v: int) -> None:
        self.buf += struct.pack("<H", v)

    def u32(self, v: int) -> None:
        self.buf += struct.pack("<I", v)

    def u64(self, v: int) -> None:
        self.buf += struct.pack("<Q", v)

    def i8(self, v: int) -> None:
        self.buf += struct.pack("<b", v)

    def i64(self, v: int) -> None:
        self.buf += struct.pack("<q", v)

    def f32(self, v: float) -> None:
        self.buf += struct.pack("<f", v)

    def cstring(self, s: str) -> None:
        b = s.encode("utf-8")
        self.u32(len(b))
        self.buf += b

    def cstring_raw(self, b: bytes) -> None:
        self.u32(len(b))
        self.buf += b

    def localizable(self, ls: dict) -> None:
        self.u8(ls["category"])
        self.u64(ls["index"])
        self.cstring(ls["default"])

    def carray(self, items: list, elem_writer) -> None:
        self.u32(len(items))
        for it in items:
            elem_writer(self, it)

    def fixed_u32(self, values: list) -> None:
        for v in values:
            self.u32(v)
# ── Nested struct readers / writers ─────────────────────────────────────


def _read_OccupiedEquipSlotData(r: _Reader) -> dict:
    return {
        "equip_slot_name_key": r.u32(),
        "equip_slot_name_index_list": r.carray(_Reader.u8),
    }


def _write_OccupiedEquipSlotData(w: _Writer, v: dict) -> None:
    w.u32(v["equip_slot_name_key"])
    w.carray(v["equip_slot_name_index_list"], _Writer.u8)


def _read_ItemIconData(r: _Reader) -> dict:
    return {
        "icon_path": r.u32(),
        "highlight_icon_path": r.u32(),
        "check_exist_sealed_data": r.u8(),
        "gimmick_state_list": r.carray(_Reader.u32),
        "check_usable": r.u8(),
    }


def _write_ItemIconData(w: _Writer, v: dict) -> None:
    w.u32(v["icon_path"])
    w.u32(v.get("highlight_icon_path", 0))
    w.u8(v.get("check_exist_sealed_data", 0))
    w.carray(v.get("gimmick_state_list") or [], _Writer.u32)
    w.u8(v.get("check_usable", 0))


def _read_PassiveSkillLevel(r: _Reader) -> dict:
    return {"skill": r.u32(), "level": r.u32()}


def _write_PassiveSkillLevel(w: _Writer, v: dict) -> None:
    w.u32(v["skill"])
    w.u32(v["level"])


def _read_ReserveSlotTargetData(r: _Reader) -> dict:
    return {"reserve_slot_info": r.u32(), "condition_info": r.u32()}


def _write_ReserveSlotTargetData(w: _Writer, v: dict) -> None:
    w.u32(v["reserve_slot_info"])
    w.u32(v["condition_info"])


def _read_SocketMaterialItem(r: _Reader) -> dict:
    return {"item": r.u32(), "value": r.u64()}


def _write_SocketMaterialItem(w: _Writer, v: dict) -> None:
    w.u32(v["item"])
    w.u64(v["value"])


def _read_EnchantStatChange(r: _Reader) -> dict:
    return {"stat": r.u32(), "change_mb": r.i64()}


def _write_EnchantStatChange(w: _Writer, v: dict) -> None:
    w.u32(v["stat"])
    w.i64(v["change_mb"])


def _read_EnchantLevelChange(r: _Reader) -> dict:
    return {"stat": r.u32(), "change_mb": r.i8()}


def _write_EnchantLevelChange(w: _Writer, v: dict) -> None:
    w.u32(v["stat"])
    w.i8(v["change_mb"])


def _read_EnchantStatData(r: _Reader) -> dict:
    return {
        "max_stat_list": r.carray(_read_EnchantStatChange),
        "regen_stat_list": r.carray(_read_EnchantStatChange),
        "stat_list_static": r.carray(_read_EnchantStatChange),
        "stat_list_static_level": r.carray(_read_EnchantLevelChange),
    }


def _write_EnchantStatData(w: _Writer, v: dict) -> None:
    w.carray(v.get("max_stat_list") or [], _write_EnchantStatChange)
    w.carray(v.get("regen_stat_list") or [], _write_EnchantStatChange)
    w.carray(v.get("stat_list_static") or [], _write_EnchantStatChange)
    w.carray(v.get("stat_list_static_level") or [], _write_EnchantLevelChange)


def _read_PriceFloor(r: _Reader) -> dict:
    return {
        "price": r.u64(),
        "sym_no": r.u32(),
        "item_info_wrapper": r.u32(),
    }


def _write_PriceFloor(w: _Writer, v: dict) -> None:
    w.u64(v["price"])
    w.u32(v["sym_no"])
    w.u32(v["item_info_wrapper"])


def _read_ItemPriceInfo(r: _Reader) -> dict:
    return {"key": r.u32(), "price": _read_PriceFloor(r)}


def _write_ItemPriceInfo(w: _Writer, v: dict) -> None:
    w.u32(v["key"])
    _write_PriceFloor(w, v["price"])


def _read_EquipmentBuff(r: _Reader) -> dict:
    return {"buff": r.u32(), "level": r.u32()}


def _write_EquipmentBuff(w: _Writer, v: dict) -> None:
    w.u32(v["buff"])
    w.u32(v["level"])


def _read_EnchantData(r: _Reader) -> dict:
    return {
        "level": r.u16(),
        "enchant_stat_data": _read_EnchantStatData(r),
        "buy_price_list": r.carray(_read_ItemPriceInfo),
        "equip_buffs": r.carray(_read_EquipmentBuff),
        "unk_u32_112": r.u32(),
    }


def _write_EnchantData(w: _Writer, v: dict) -> None:
    w.u16(v["level"])
    _write_EnchantStatData(w, v["enchant_stat_data"])
    w.carray(v.get("buy_price_list") or [], _write_ItemPriceInfo)
    w.carray(v.get("equip_buffs") or [], _write_EquipmentBuff)
    w.u32(v.get("unk_u32_112", 0))


def _read_GameEventExecuteData(r: _Reader) -> dict:
    return {
        "game_event_type": r.u8(),
        "player_condition": r.u32(),
        "target_condition": r.u32(),
        "event_condition": r.u32(),
    }


def _write_GameEventExecuteData(w: _Writer, v: dict) -> None:
    w.u8(v["game_event_type"])
    w.u32(v["player_condition"])
    w.u32(v["target_condition"])
    w.u32(v["event_condition"])


def _read_InventoryChangeData(r: _Reader) -> dict:
    return {
        "game_event_execute_data": _read_GameEventExecuteData(r),
        "to_inventory_info": r.u16(),
    }


def _write_InventoryChangeData(w: _Writer, v: dict) -> None:
    _write_GameEventExecuteData(w, v["game_event_execute_data"])
    w.u16(v["to_inventory_info"])


def _read_PageData(r: _Reader) -> dict:
    return {
        "left_page_texture_path": r.cstring(),
        "right_page_texture_path": r.cstring(),
        "left_page_related_knowledge_info": r.u32(),
        "right_page_related_knowledge_info": r.u32(),
    }


def _write_PageData(w: _Writer, v: dict) -> None:
    w.cstring(v["left_page_texture_path"])
    w.cstring(v["right_page_texture_path"])
    w.u32(v["left_page_related_knowledge_info"])
    w.u32(v["right_page_related_knowledge_info"])
def _read_InspectData(r: _Reader) -> dict:
    return {
        "item_info": r.u32(),
        "gimmick_info": r.u32(),
        "character_info": r.u32(),
        "spawn_reason_hash": r.u32(),
        "socket_name": r.cstring(),
        "speak_character_info": r.u32(),
        "inspect_target_tag": r.u32(),
        "reward_own_knowledge": r.u8(),
        "reward_knowledge_info": r.u32(),
        "item_desc": r.localizable(),
        "board_key": r.u32(),
        "inspect_action_type": r.u8(),
        "gimmick_state_name_hash": r.u32(),
        "target_page_index": r.u32(),
        "is_left_page": r.u8(),
        "target_page_related_knowledge_info": r.u32(),
        "enable_read_after_reward": r.u8(),
        "refer_to_left_page_inspect_data": r.u8(),
        "inspect_effect_info_key": r.u32(),
        "inspect_complete_effect_info_key": r.u32(),
    }


def _write_InspectData(w: _Writer, v: dict) -> None:
    w.u32(v["item_info"])
    w.u32(v["gimmick_info"])
    w.u32(v["character_info"])
    w.u32(v["spawn_reason_hash"])
    w.cstring(v["socket_name"])
    w.u32(v["speak_character_info"])
    w.u32(v["inspect_target_tag"])
    w.u8(v["reward_own_knowledge"])
    w.u32(v["reward_knowledge_info"])
    w.localizable(v["item_desc"])
    w.u32(v["board_key"])
    w.u8(v["inspect_action_type"])
    w.u32(v["gimmick_state_name_hash"])
    w.u32(v["target_page_index"])
    w.u8(v["is_left_page"])
    w.u32(v["target_page_related_knowledge_info"])
    w.u8(v["enable_read_after_reward"])
    w.u8(v["refer_to_left_page_inspect_data"])
    w.u32(v["inspect_effect_info_key"])
    w.u32(v["inspect_complete_effect_info_key"])


def _read_InspectAction(r: _Reader) -> dict:
    return {
        "action_name_hash": r.u32(),
        "catch_tag_name_hash": r.u32(),
        "catcher_socket_name": r.cstring(),
        "catch_target_socket_name": r.cstring(),
    }


def _write_InspectAction(w: _Writer, v: dict) -> None:
    w.u32(v["action_name_hash"])
    w.u32(v["catch_tag_name_hash"])
    w.cstring(v["catcher_socket_name"])
    w.cstring(v["catch_target_socket_name"])


def _read_ItemInfoSharpnessData(r: _Reader, dsi_type: int = 15) -> dict:
    """SharpnessData: u16 max_sharpness + u16 craft_tool_info + stat_data.

    ``dsi_type`` 只为兼容旧调用方保留：当前布局在该位置没有条件字节。
    """
    return {
        "max_sharpness": r.u16(),
        "craft_tool_info": r.u16(),
        "stat_data": _read_EnchantStatData(r),
    }


def _write_ItemInfoSharpnessData(w: _Writer, v: dict) -> None:
    w.u16(v["max_sharpness"])
    w.u16(v["craft_tool_info"])
    _write_EnchantStatData(w, v["stat_data"])


def _read_Cooltime(r: _Reader) -> dict:
    return {"a": r.i64(), "b": r.i64(), "c": r.i64()}


def _write_Cooltime(w: _Writer, v: dict) -> None:
    w.i64(v["a"])
    w.i64(v["b"])
    w.i64(v["c"])


def _read_MaxChargedUseableCount(r: _Reader) -> dict:
    return {"a": r.u32(), "b": r.u32(), "c": r.u32()}


def _write_MaxChargedUseableCount(w: _Writer, v: dict) -> None:
    w.u32(v["a"])
    w.u32(v["b"])
    w.u32(v["c"])


def _read_ItemBundleData(r: _Reader) -> dict:
    return {"count_mb": r.u64(), "key": r.u32()}


def _write_ItemBundleData(w: _Writer, v: dict) -> None:
    w.u64(v["count_mb"])
    w.u32(v["key"])


def _read_UnitData(r: _Reader) -> dict:
    return {
        "ui_component": r.cstring(),
        "minimum": r.u32(),
        "icon_path": r.u32(),
        "unk_hash_110": r.u32(),
        "item_name": r.localizable(),
        "item_desc": r.localizable(),
    }


def _write_UnitData(w: _Writer, v: dict) -> None:
    w.cstring(v["ui_component"])
    w.u32(v["minimum"])
    w.u32(v["icon_path"])
    w.u32(v.get("unk_hash_110", 0))
    w.localizable(v["item_name"])
    w.localizable(v["item_desc"])


def _read_MoneyUnitEntry(r: _Reader) -> dict:
    return {"key": r.u32(), "value": _read_UnitData(r)}


def _write_MoneyUnitEntry(w: _Writer, v: dict) -> None:
    w.u32(v["key"])
    _write_UnitData(w, v["value"])


def _read_MoneyTypeDefine(r: _Reader) -> dict:
    return {
        "price_floor_value": r.u64(),
        "unit_data_list_map": r.carray(_read_MoneyUnitEntry),
    }


def _write_MoneyTypeDefine(w: _Writer, v: dict) -> None:
    w.u64(v["price_floor_value"])
    w.carray(v.get("unit_data_list_map") or [], _write_MoneyUnitEntry)
def _read_PrefabData(r: _Reader) -> dict:
    return {
        "scale": [r.f32(), r.f32(), r.f32()],
        "prefab_names": r.carray(_Reader.u32),
        "animation_path_list": r.carray(_Reader.u32),
        "equip_slot_list": r.carray(_Reader.u16),
        "tribe_gender_list": r.carray(_Reader.u32),
        "docking_prefab_switch_name": r.u32(),
        "use_gimmick_prefab": r.u8(),
        "is_craft_material": r.u8(),
        "prefab_data_type": r.u8(),
    }


def _write_PrefabData(w: _Writer, v: dict) -> None:
    scale = v.get("scale") or [1.0, 1.0, 1.0]
    for value in scale[:3]:
        w.f32(value)
    w.carray(v.get("prefab_names") or [], _Writer.u32)
    w.carray(v.get("animation_path_list") or [], _Writer.u32)
    w.carray(v.get("equip_slot_list") or [], _Writer.u16)
    w.carray(v.get("tribe_gender_list") or [], _Writer.u32)
    w.u32(v.get("docking_prefab_switch_name", 0))
    w.u8(v.get("use_gimmick_prefab", 0))
    w.u8(v.get("is_craft_material", 0))
    w.u8(v.get("prefab_data_type", 0))


def _read_DockingChildData(r: _Reader) -> dict:
    return {
        "gimmick_info_key": r.u32(),
        "character_key": r.u32(),
        "item_key": r.u32(),
        "attach_parent_socket_name": r.cstring(),
        "attach_child_socket_name": r.cstring(),
        "docking_tag_name_hash": r.fixed_u32(4),
        "docking_equip_slot_no": r.u16(),
        "spawn_distance_level": r.u32(),
        "is_item_equip_docking_gimmick": r.u8(),
        "send_damage_to_parent": r.u8(),
        "is_body_part": r.u8(),
        "docking_type": r.u8(),
        "is_summoner_team": r.u8(),
        "is_player_only": r.u8(),
        "is_npc_only": r.u32(),
        "is_sync_break_parent": r.u8(),
        "hit_part": r.u8(),
        "detected_by_npc": r.u8(),
        "is_bag_docking": r.u8(),
        "enable_collision": r.u8(),
        "disable_collision_with_other_gimmick": r.u8(),
        "docking_slot_key": r.cstring(),
        "inherit_summoner": r.u8(),
        "summon_tag_name_hash": r.fixed_u32(4),
        "animation_root_bone_name": r.cstring(),
    }


def _write_DockingChildData(w: _Writer, v: dict) -> None:
    w.u32(v["gimmick_info_key"])
    w.u32(v["character_key"])
    w.u32(v["item_key"])
    w.cstring(v["attach_parent_socket_name"])
    w.cstring(v["attach_child_socket_name"])
    w.fixed_u32(list(v.get("docking_tag_name_hash") or [0, 0, 0, 0])[:4])
    w.u16(v["docking_equip_slot_no"])
    w.u32(v["spawn_distance_level"])
    w.u8(v["is_item_equip_docking_gimmick"])
    w.u8(v["send_damage_to_parent"])
    w.u8(v["is_body_part"])
    w.u8(v["docking_type"])
    w.u8(v["is_summoner_team"])
    w.u8(v["is_player_only"])
    w.u32(v["is_npc_only"])
    w.u8(v["is_sync_break_parent"])
    w.u8(v["hit_part"])
    w.u8(v["detected_by_npc"])
    w.u8(v["is_bag_docking"])
    w.u8(v["enable_collision"])
    w.u8(v["disable_collision_with_other_gimmick"])
    w.cstring(v["docking_slot_key"])
    w.u8(v["inherit_summoner"])
    w.fixed_u32(list(v.get("summon_tag_name_hash") or [0, 0, 0, 0])[:4])
    w.cstring(v["animation_root_bone_name"])


def _read_PatternParamString(r: _Reader) -> dict:
    return {
        "flag": r.u8(),
        "unk_flag_2": r.u8(),
        "unk_value": r.fixed_u32(2),
        "param_string": r.cstring(),
    }


def _write_PatternParamString(w: _Writer, v: dict) -> None:
    w.u8(v["flag"])
    w.u8(v["unk_flag_2"])
    w.fixed_u32(list(v.get("unk_value") or [0, 0])[:2])
    w.cstring(v["param_string"])


def _read_PatternDescriptionData(r: _Reader) -> dict:
    return {
        "pattern_description_info": r.u32(),
        "param_string_list": r.carray(_read_PatternParamString),
    }


def _write_PatternDescriptionData(w: _Writer, v: dict) -> None:
    w.u32(v["pattern_description_info"])
    w.carray(v.get("param_string_list") or [], _write_PatternParamString)


def _read_FactionManagementData(r: _Reader) -> dict:
    return {
        "faction_info": r.u32(),
        "value_a": r.u64(),
        "value_b": r.u64(),
        "value_c": r.u64(),
    }


def _write_FactionManagementData(w: _Writer, v: dict) -> None:
    w.u32(v["faction_info"])
    w.u64(v["value_a"])
    w.u64(v["value_b"])
    w.u64(v["value_c"])


def _read_ItemInfoFactionManagementData(r: _Reader) -> dict:
    return {
        "is_valid": r.u8(),
        "cost_list": r.carray(_read_FactionManagementData),
        "price_list": r.carray(_read_FactionManagementData),
        "tier": r.u32(),
    }


def _write_ItemInfoFactionManagementData(w: _Writer, v: dict) -> None:
    w.u8(v["is_valid"])
    w.carray(v.get("cost_list") or [], _write_FactionManagementData)
    w.carray(v.get("price_list") or [], _write_FactionManagementData)
    w.u32(v["tier"])


def _read_RepairData(r: _Reader) -> dict:
    return {
        "resource_item_info": r.u32(),
        "repair_value": r.u16(),
        "repair_style": r.u8(),
        "resource_item_count": r.u64(),
    }


def _write_RepairData(w: _Writer, v: dict) -> None:
    w.u32(v["resource_item_info"])
    w.u16(v["repair_value"])
    w.u8(v["repair_style"])
    w.u64(v["resource_item_count"])
# SubItem 是 u8 判别式：被填充的 tag 后面跟一个 u32 载荷。
_SUBITEM_U32_TAGS = frozenset({0, 3, 9})
_SUBITEM_NONE_TAGS = frozenset({14, 15, 16, 17, 18, 255})


def _read_SubItem(r: _Reader) -> dict:
    type_id = r.u8()
    if type_id in _SUBITEM_U32_TAGS:
        return {"type_id": type_id, "value": r.u32()}
    if type_id in _SUBITEM_NONE_TAGS:
        return {"type_id": type_id, "value": None}
    raise ValueError(f"unknown SubItem type: {type_id}")


def _write_SubItem(w: _Writer, v: dict) -> None:
    type_id = v["type_id"]
    w.u8(type_id)
    if type_id in _SUBITEM_U32_TAGS:
        value = v.get("value")
        if value is None:
            raise ValueError(f"SubItem type {type_id} requires a u32 value")
        w.u32(value)


def _read_DropDefaultData(r: _Reader) -> dict:
    return {
        "drop_enchant_level": r.u16(),
        "socket_item_list": r.carray(_Reader.u32),
        "add_socket_material_item_list": r.carray(_read_SocketMaterialItem),
        "default_sub_item": _read_SubItem(r),
        "socket_valid_count": r.u8(),
        "use_socket": r.u8(),
    }


def _write_DropDefaultData(w: _Writer, v: dict) -> None:
    w.u16(v["drop_enchant_level"])
    w.carray(v.get("socket_item_list") or [], _Writer.u32)
    w.carray(
        v.get("add_socket_material_item_list") or [],
        _write_SocketMaterialItem,
    )
    _write_SubItem(w, v["default_sub_item"])
    w.u8(v["socket_valid_count"])
    w.u8(v["use_socket"])


def _read_SealableItemInfo(r: _Reader) -> dict:
    type_tag = r.u8()
    item_key = r.u32()
    unknown0 = r.u64()
    if type_tag == 2:
        value: Any = r.cstring()
    elif type_tag in (0, 1, 3, 4):
        value = r.u32()
    else:
        raise ValueError(f"unknown SealableItemInfo type: {type_tag}")
    return {
        "type_tag": type_tag,
        "item_key": item_key,
        "unknown0": unknown0,
        "value": value,
    }


def _write_SealableItemInfo(w: _Writer, v: dict) -> None:
    type_tag = v["type_tag"]
    w.u8(type_tag)
    w.u32(v["item_key"])
    w.u64(v["unknown0"])
    value = v.get("value")
    if type_tag == 2:
        w.cstring(value)
    else:
        w.u32(value)


def _read_optional(r: _Reader, inner_reader):
    flag = r.u8()
    if flag == 0:
        return None
    return inner_reader(r)


def _write_optional(w: _Writer, v, inner_writer) -> None:
    if v is None:
        w.u8(0)
    else:
        w.u8(1)
        inner_writer(w, v)
# ── Field schema (Crimson Desert 2.02.00) ───────────────────────────────

_ITEM_FIELDS: list[tuple] = [
    ("key", "u32"),
    ("string_key", "cstring"),
    ("is_blocked", "u8"),
    ("max_stack_count", "u64"),
    ("item_name", "localizable"),
    ("broken_item_prefix_string", "u32"),
    ("equip_type_info", "u32"),
    ("occupied_equip_slot_data_list", "carray",
     _read_OccupiedEquipSlotData, _write_OccupiedEquipSlotData),
    ("item_tag_list", "carray_u32"),
    ("equipable_hash", "u32"),
    ("consumable_type_list", "carray_u32"),
    ("item_use_info_list", "carray_u32"),
    ("item_icon_list", "carray", _read_ItemIconData, _write_ItemIconData),
    ("map_icon_path", "u32"),
    ("use_map_icon_alert", "u8"),
    ("item_type", "u8"),
    ("material_key", "u32"),
    ("material_match_info", "u32"),
    ("item_desc", "localizable"),
    ("item_desc2", "localizable"),
    ("equipable_level", "u32"),
    ("category_info", "u16"),
    ("knowledge_info", "u32"),
    ("knowledge_obtain_type", "u8"),
    ("destroy_effec_info", "u32"),
    ("equip_passive_skill_list", "carray",
     _read_PassiveSkillLevel, _write_PassiveSkillLevel),
    ("use_immediately", "u8"),
    ("apply_max_stack_cap", "u8"),
    ("extract_additional_drop_set_info", "u32"),
    ("minimum_extract_enchant_level", "u16"),
    ("item_memo", "cstring"),
    ("filter_type", "cstring"),
    ("gimmick_info", "u32"),
    ("gimmick_tag_list", "carray_cstring"),
    ("max_drop_result_sub_item_count", "u32"),
    ("use_drop_set_target", "u8"),
    ("is_all_gimmick_sealable", "u8"),
    ("sealable_item_info_list", "carray",
     _read_SealableItemInfo, _write_SealableItemInfo),
    ("sealable_character_info_list", "carray",
     _read_SealableItemInfo, _write_SealableItemInfo),
    ("sealable_gimmick_info_list", "carray",
     _read_SealableItemInfo, _write_SealableItemInfo),
    ("sealable_gimmick_tag_list", "carray",
     _read_SealableItemInfo, _write_SealableItemInfo),
    ("sealable_tribe_info_list", "carray",
     _read_SealableItemInfo, _write_SealableItemInfo),
    ("sealable_money_info_list", "carray_u32"),
    ("delete_by_gimmick_unlock", "u8"),
    ("gimmick_unlock_message_local_string_info", "u32"),
    ("can_disassemble", "u8"),
    ("transmutation_material_gimmick_list", "carray_u32"),
    ("transmutation_material_item_list", "carray_u32"),
    ("transmutation_material_item_group_list", "carray_u16"),
    ("is_register_trade_market", "u8"),
    ("multi_change_info_list", "carray_u32"),
    ("is_editor_usable", "u8"),
    ("discardable", "u8"),
    ("is_dyeable", "u8"),
    ("is_editable_grime", "u8"),
    ("is_destroy_when_broken", "u8"),
    ("is_housing_only", "u8"),
    ("is_extract_able_item", "u8"),
    ("quick_slot_index", "u8"),
    ("reserve_slot_target_data_list", "carray",
     _read_ReserveSlotTargetData, _write_ReserveSlotTargetData),
    ("item_tier", "u8"),
    ("is_important_item", "u8"),
    ("apply_drop_stat_type", "u8"),
    ("is_reward_loot_drop", "u8"),
    ("drop_default_data", "struct",
     _read_DropDefaultData, _write_DropDefaultData),
    ("enchant_data_list", "carray", _read_EnchantData, _write_EnchantData),
    ("price_list", "carray", _read_ItemPriceInfo, _write_ItemPriceInfo),
    ("docking_child_data", "optional",
     _read_DockingChildData, _write_DockingChildData),
    ("inventory_change_data", "optional",
     _read_InventoryChangeData, _write_InventoryChangeData),
    ("unk_texture_path", "cstring"),
    ("fixed_page_data_list", "carray", _read_PageData, _write_PageData),
    ("dynamic_page_data_list", "carray", _read_PageData, _write_PageData),
    ("inspect_data_list", "carray", _read_InspectData, _write_InspectData),
    ("inspect_action", "struct", _read_InspectAction, _write_InspectAction),
    ("default_sub_item", "struct", _read_SubItem, _write_SubItem),
    ("cooltime", "struct", _read_Cooltime, _write_Cooltime),
    ("item_charge_type", "u8"),
    ("usable_alert_type", "u8"),
    ("sharpness_data", "struct",
     _read_ItemInfoSharpnessData, _write_ItemInfoSharpnessData),
    ("max_charged_useable_count", "struct",
     _read_MaxChargedUseableCount, _write_MaxChargedUseableCount),
    ("hackable_character_group_info_list", "carray_u16"),
    ("item_group_info_list", "carray_u16"),
    ("discard_offset_y", "f32"),
    ("discard_attach_terrain", "u8"),
    ("hide_from_inventory_on_pop_item", "u8"),
    ("is_shield_item", "u8"),
    ("is_tower_shield_item", "u8"),
    ("is_wild", "u8"),
    ("packed_item_info", "u32"),
    ("unpacked_item_info", "u32"),
    ("convert_item_info_by_drop_npc", "u32"),
    ("stage_info", "u32"),
    ("pattern_description_data_list", "carray",
     _read_PatternDescriptionData, _write_PatternDescriptionData),
    ("look_detail_game_advice_info_wrapper", "u32"),
    ("look_detail_mission_info", "u32"),
    ("enable_alert_system_to_ui", "u8"),
    ("is_save_game_data_at_use_item", "u8"),
    ("is_logout_at_use_item", "u8"),
    ("shared_cool_time_group_name_hash", "u32"),
    ("item_bundle_data_list", "carray",
     _read_ItemBundleData, _write_ItemBundleData),
    ("money_type_define", "optional",
     _read_MoneyTypeDefine, _write_MoneyTypeDefine),
    ("emoji_texture_id", "cstring"),
    ("enable_equip_in_clone_actor", "u8"),
    ("is_blocked_store_sell", "u8"),
    ("is_preorder_item", "u8"),
    ("is_has_item_use_data_inventory_buff", "u8"),
    ("is_preserved_on_extract", "u8"),
    ("item_effect_info", "u32"),
    ("faction_management_data", "struct",
     _read_ItemInfoFactionManagementData, _write_ItemInfoFactionManagementData),
    ("use_average_price", "u8"),
    ("respawn_time_seconds", "i64"),
    ("max_endurance", "u16"),
    ("repair_data_list", "carray", _read_RepairData, _write_RepairData),
    ("prefab_data_list", "carray", _read_PrefabData, _write_PrefabData),
    ("push_inventory_type_0_116", "u16"),
    ("push_inventory_type_1_116", "u16"),
    ("push_inventory_type_2_116", "u16"),
    ("push_inventory_type_3_116", "u16"),
    ("push_inventory_type_4_116", "u16"),
    ("push_inventory_type_5_116", "u16"),
    ("push_inventory_type_6_116", "u16"),
    ("push_inventory_type_7_116", "u16"),
    ("item_push_inventory_contents_type_113", "u8"),
    ("trailing_u8_113", "u8"),
]

_ITEM_FIELD_NAMES: tuple[str, ...] = tuple(spec[0] for spec in _ITEM_FIELDS)

_SCALAR_KINDS: dict[str, tuple[Any, Any]] = {
    "u8": (_Reader.u8, _Writer.u8),
    "u16": (_Reader.u16, _Writer.u16),
    "u32": (_Reader.u32, _Writer.u32),
    "u64": (_Reader.u64, _Writer.u64),
    "i8": (_Reader.i8, _Writer.i8),
    "i64": (_Reader.i64, _Writer.i64),
    "f32": (_Reader.f32, _Writer.f32),
    "cstring": (_Reader.cstring, _Writer.cstring),
    "cstring_raw": (_Reader.cstring_raw, _Writer.cstring_raw),
    "localizable": (_Reader.localizable, _Writer.localizable),
    "carray_u8": (
        lambda r: r.carray(_Reader.u8),
        lambda w, v: w.carray(v, _Writer.u8),
    ),
    "carray_u16": (
        lambda r: r.carray(_Reader.u16),
        lambda w, v: w.carray(v, _Writer.u16),
    ),
    "carray_u32": (
        lambda r: r.carray(_Reader.u32),
        lambda w, v: w.carray(v, _Writer.u32),
    ),
    "carray_cstring": (
        lambda r: r.carray(_Reader.cstring),
        lambda w, v: w.carray(v, _Writer.cstring),
    ),
}


def read_iteminfo_field(reader: _Reader, spec: tuple) -> Any:
    """按字段定义读取一个 ItemInfo 字段值。"""
    kind = spec[1]
    scalar = _SCALAR_KINDS.get(kind)
    if scalar is not None:
        return scalar[0](reader)
    if kind == "carray":
        return reader.carray(spec[2])
    if kind == "struct":
        return spec[2](reader)
    if kind == "optional":
        return _read_optional(reader, spec[2])
    raise ValueError(f"未知 ItemInfo 字段类型：{kind}")


def write_iteminfo_field(writer: _Writer, spec: tuple, value: Any) -> None:
    """按字段定义写回一个 ItemInfo 字段值。"""
    kind = spec[1]
    scalar = _SCALAR_KINDS.get(kind)
    if scalar is not None:
        scalar[1](writer, value)
        return
    if kind == "carray":
        if isinstance(value, dict) and value.get("_opaque"):
            writer.buf += bytes(value["bytes"])
        else:
            writer.carray(value, spec[3])
        return
    if kind == "struct":
        spec[3](writer, value)
        return
    if kind == "optional":
        _write_optional(writer, value, spec[3])
        return
    raise ValueError(f"未知 ItemInfo 字段类型：{kind}")


def _read_item(r: _Reader) -> dict:
    out: dict = {}
    for spec in _ITEM_FIELDS:
        out[spec[0]] = read_iteminfo_field(r, spec)
    return out


def _write_item(w: _Writer, item: dict) -> None:
    if item.get("_opaque_record"):
        w.buf += bytes(item["bytes"])
        return
    for spec in _ITEM_FIELDS:
        write_iteminfo_field(w, spec, item[spec[0]])
    # Index-framed parses preserve any bytes between the parsed end and the
    # next record's index offset; emit them back verbatim.
    slack = item.get("_tail_slack")
    if slack:
        w.buf += slack
# ── Record framing ──────────────────────────────────────────────────────


def _looks_like_record_header(data: bytes, p: int) -> bool:
    """Heuristic: does ``p`` look like the start of an iteminfo record?

    Pattern: u32 key (>=1, <3e8), u32 string_key_len (4..100), then
    ``string_key_len`` ASCII-printable bytes (letters/digits/underscore/
    hyphen), followed by u8 is_blocked (0 or 1) and u64 max_stack_count
    (1..1e8). Only used for the sniff walk when no .pabgh index is
    available.
    """
    if p < 0 or p + 17 > len(data):
        return False
    key = struct.unpack_from("<I", data, p)[0]
    if key == 0 or key > 300_000_000:
        return False
    n = struct.unpack_from("<I", data, p + 4)[0]
    if n < 4 or n > 100:
        return False
    if p + 8 + n + 9 > len(data):
        return False
    sk = data[p + 8:p + 8 + n]
    for b in sk:
        if not (
            (0x41 <= b <= 0x5A)  # A-Z
            or (0x61 <= b <= 0x7A)  # a-z
            or (0x30 <= b <= 0x39)  # 0-9
            or b in (0x5F, 0x2D)  # _ -
        ):
            return False
    blocked = data[p + 8 + n]
    if blocked > 1:
        return False
    max_stack = struct.unpack_from("<Q", data, p + 8 + n + 1)[0]
    if max_stack < 1 or max_stack > 100_000_000:
        return False
    return True


def _find_next_record_start(data: bytes, search_start: int, search_end: int) -> int:
    """Scan ``[search_start, search_end)`` for the first plausible record
    header. Returns -1 when none is found."""
    if search_end > len(data):
        search_end = len(data)
    p = max(0, search_start)
    while p < search_end:
        if _looks_like_record_header(data, p):
            return p
        p += 1
    return -1


def parse_iteminfo_from_bytes(
    data: bytes,
    record_offsets: "list[int] | None" = None,
) -> list[dict]:
    """Parse a whole iteminfo body into a list of item dicts.

    With ``record_offsets`` (the authoritative record starts from the
    companion ``.pabgh`` index) every record is framed exactly, and any
    bytes between a record's parsed end and the next index offset are
    preserved as ``_tail_slack`` so serialization stays byte-exact. A
    record the current schema cannot decode is carried verbatim as an
    ``_opaque_record`` so the rest of the table keeps round-tripping.

    Without offsets: walks records back-to-back from offset 0, sniffing
    the next plausible record header as the record boundary.
    """
    items: list[dict] = []
    if record_offsets:
        starts = sorted(set(record_offsets))
        if starts and starts[0] == 0 and starts[-1] < len(data):
            for i, rec_start in enumerate(starts):
                rec_end = starts[i + 1] if i + 1 < len(starts) else len(data)
                r = _Reader(data, rec_start, rec_end=rec_end)
                try:
                    it = _read_item(r)
                    if r.pos > rec_end:
                        raise ValueError(
                            f"record at 0x{rec_start:X} overran its index "
                            f"boundary (parsed to 0x{r.pos:X}, next record "
                            f"at 0x{rec_end:X})"
                        )
                    if r.pos < rec_end:
                        it["_tail_slack"] = bytes(data[r.pos:rec_end])
                except Exception:
                    key = struct.unpack_from("<I", data, rec_start)[0]
                    it = {
                        "key": key,
                        "_opaque_record": True,
                        "bytes": bytes(data[rec_start:rec_end]),
                    }
                items.append(it)
            return items
        # Implausible index (doesn't start at 0 / points past EOF):
        # fall through to the sniff walk.

    pos = 0
    while pos < len(data):
        rec_start = pos
        sniff_start = rec_start + 200
        sniff_end = min(rec_start + 30000, len(data))
        next_start = _find_next_record_start(data, sniff_start, sniff_end)
        if next_start < 0:
            next_start = len(data)
        r = _Reader(data, rec_start, rec_end=next_start)
        try:
            items.append(_read_item(r))
            pos = r.pos
        except Exception:
            key = struct.unpack_from("<I", data, rec_start)[0]
            items.append({
                "key": key,
                "_opaque_record": True,
                "bytes": bytes(data[rec_start:next_start]),
            })
            pos = next_start
    return items


def parse_iteminfo_record(data: bytes) -> dict:
    """按已知边界解析单条 ItemInfo 记录。

    Format 3 快速路径会从 ``.pabgh`` 已经给出的 entry 边界中切出单条
    PABGB 记录。这里直接把 ``len(data)`` 作为记录边界，避免走整表 sniff
    逻辑时把单记录误判为不可信索引或吞进后续记录。
    """
    r = _Reader(data, 0, rec_end=len(data))
    item = _read_item(r)
    if r.pos > len(data):
        raise ValueError(
            f"record overran boundary (parsed to 0x{r.pos:X}, "
            f"record end 0x{len(data):X})"
        )
    if r.pos < len(data):
        item["_tail_slack"] = bytes(data[r.pos:])
    return item


def serialize_iteminfo(
    items: list[dict],
    offsets_out: dict[int, int] | None = None,
) -> bytes:
    """Inverse of :func:`parse_iteminfo_from_bytes`.

    The byte output must be identical to the input when items haven't been
    modified. When ``offsets_out`` is given it is filled with each record's
    ``key -> output byte offset`` so callers can rewrite the companion
    ``.pabgh`` index after size-changing edits.
    """
    w = _Writer()
    for it in items:
        if offsets_out is not None:
            offsets_out[it["key"]] = len(w.buf)
        _write_item(w, it)
    return bytes(w.buf)


def parse_first_record_size(data: bytes) -> int:
    """Parse the first record and return its byte size."""
    r = _Reader(data, 0)
    _read_item(r)
    return r.pos


def parse_record_at(data: bytes, offset: int, rec_end: int | None = None) -> int:
    """Parse one record starting at ``offset`` and return the cursor
    position after the record."""
    r = _Reader(data, offset, rec_end=rec_end)
    _read_item(r)
    return r.pos
# ── Single-field helpers used by the Format 3 writers ───────────────────


def _locate_field(data: bytes, target_name: str) -> tuple[Any, int, int]:
    """按记录布局读取一个前置字段，返回 ``(value, start, end)``。"""
    r = _Reader(data, 0, rec_end=len(data))
    for spec in _ITEM_FIELDS:
        if spec[0] == target_name:
            start = r.pos
            value = read_iteminfo_field(r, spec)
            return value, start, r.pos
        read_iteminfo_field(r, spec)
    raise ValueError(f"{target_name} field not found")


def parse_iteminfo_prefab_data_list(data: bytes) -> tuple[list[dict], int, int]:
    """只解析单条 ItemInfo 记录中的 ``prefab_data_list`` 字段。"""
    value, start, end = _locate_field(data, "prefab_data_list")
    return value, start, end


def serialize_iteminfo_prefab_data_list(values: list[dict]) -> bytes:
    """序列化单个 ``prefab_data_list`` 字段。"""
    w = _Writer()
    w.carray(values, _write_PrefabData)
    return bytes(w.buf)


def parse_iteminfo_drop_default_data(data: bytes) -> tuple[dict, int, int]:
    """只解析单条 ItemInfo 记录中的 ``drop_default_data`` 字段。"""
    value, start, end = _locate_field(data, "drop_default_data")
    return value, start, end


def serialize_iteminfo_drop_default_data(value: dict) -> bytes:
    """序列化单个 ``drop_default_data`` 字段。"""
    w = _Writer()
    _write_DropDefaultData(w, value)
    return bytes(w.buf)


def parse_iteminfo_visual_prefab_lists(
    data: bytes,
) -> tuple[list[dict], list[dict], int, int]:
    """解析 ``prefab_data_list``。

    2.02.00 已经把旧的 ``gimmick_visual_prefab_data_list`` 合并进
    ``prefab_data_list``，因此第二个返回值恒为空列表；保留该签名是为了
    兼容既有调用方。
    """
    value, start, end = _locate_field(data, "prefab_data_list")
    return value, [], start, end


def serialize_iteminfo_visual_prefab_lists(
    prefab_values: list[dict],
    gimmick_values: list[dict],
) -> bytes:
    """序列化 ``prefab_data_list``（``gimmick_values`` 必须为空）。"""
    if gimmick_values:
        raise ValueError(
            "当前游戏版本已无 gimmick_visual_prefab_data_list；"
            "请改用 prefab_data_list"
        )
    return serialize_iteminfo_prefab_data_list(prefab_values)


# ── Layout detection / lightweight prefix reads ─────────────────────────

# 2.02.00 的 ItemInfo 前缀已经没有旧版 `inventory_info`(u16)。常量保留下来
# 只为兼容既有调用方的返回值判断。
ITEMINFO_LAYOUT_WITH_INVENTORY = "with_inventory_info"
ITEMINFO_LAYOUT_WITHOUT_INVENTORY = "without_inventory_info"

# 早期版本里灯笼类装备在 material_match_info 之后曾有一段 12 字节块。
# 2.02.00 已不存在，保留常量仅为兼容旧调用方 import。
LANTERN_EQ_TYPE = 0x97C2FAE8


def detect_iteminfo_layout(
    data: bytes,
    record_bounds: list[tuple[int, int]] | tuple[tuple[int, int], ...],
) -> str:
    """当前游戏版本恒定为无 ``inventory_info`` 布局。"""
    return ITEMINFO_LAYOUT_WITHOUT_INVENTORY


def read_iteminfo_match_prefix(
    data: bytes,
    key: int,
    entry_name: str,
    entry_start: int,
    entry_end: int,
    preferred_layout: str | None = None,
) -> dict[str, object] | None:
    """只读取动态选择器需要的 ItemInfo 前缀字段。"""
    r = _Reader(data, entry_start, rec_end=min(entry_end, len(data)))
    try:
        for spec in _ITEM_FIELDS:
            value = read_iteminfo_field(r, spec)
            if spec[0] == "equip_type_info":
                return {
                    "key": key,
                    "string_key": entry_name,
                    "equip_type_info": value,
                    "_equip_type_info_candidates": (value,),
                }
    except (IndexError, struct.error, UnicodeError, ValueError):
        return None
    return None