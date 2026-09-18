"""为 Trinity 动态物品目录生成简体中文运行时回退表。

数据只来自当前受支持游戏版本的原版 ``iteminfo``、``ItemGroupInfo``、``inventory``
表与简体中文 PALOC。生成结果编译进 ``TrinityCN.asi``，发布目录不携带游戏原始表或中间 JSON。

Crimson Desert 2.01.00 起数据表物理名变为
``gamedata/binarystaticinfo__/bin/*.staticinfobody|*.staticinfoheader``，本地化文本按语言目录
拆成 39 个 ``*.paloc``（简中在 ``gamedata/stringtable/binary__/zho-cn``）。这里统一用加载器的
PAMT 逻辑目标查询解析，禁止再按旧物理路径或整表 ``localizationstring_zho-cn.paloc`` 拼名。
+
+除行号映射外，这里还生成一份“内部名称 → 中文”覆盖表：模组新增装备在 PALOC 里用的是模组
+作者写的英文（例如 ``Dandelion OP``），官方简中没有对应文本，只能由本补丁提供译名。运行时
+按 iteminfo 记录的 string_key 命中该表，因此它对模组增删与表行数变化都不敏感。
+
+若本机已有加载器构建出的 VFS 快照（``.cdloader/vfs_active/snapshot-*``），生成器还会按
+“游戏实际加载的合并表”复核一遍：凡是 Trinity 只能显示英文（官方简中缺失或模组只给英文名）
+的记录，都必须被行号表或内部名称覆盖表覆盖，否则直接构建失败并列出待补译的 key。
"""

from __future__ import annotations

import argparse
import hashlib
import struct
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT.parent))

from cdmm.archive.pamt import parse_pamt  # noqa: E402
from cdmm.services.json_loader import extract_plaintext  # noqa: E402
from cdmm.services.pab_table_service import parse_pabgh_index  # noqa: E402
from cdmm.services.pamt_index_service import get_game_pamt_index  # noqa: E402
from cdmm.services.paloc import parse_paloc  # noqa: E402

# Trinity V1.4.1 VTweak 所对应的当前 Crimson Desert 游戏主程序哈希。
EXPECTED_GAME_EXE_SHA256 = (
    "BCBF623AD5690147DC462AEAED5B4F97BD73296BA0D6AB54663586E7088B1C0E"
)

# 动态目录生成依赖的原版明文表哈希；任一漂移都必须重新分析后再更新。
EXPECTED_ASSET_SHA256 = {
    "iteminfo.pabgb": "E646E4A0281930AEC1D6D750ACAAFE07740F60E0DF5242041FC5BF57AE7ABADE",
    "iteminfo.pabgh": "59F16D991F77876BB216C16AFFB50C3BBCBFEA9190602C9FDA1557A3E07123FE",
    "itemgroupinfo.pabgb": "E368476C3C0D454943DF2F1D4EC8D85DBF2BB1184C77E2C8E9C4F3D2CC7E1E8C",
    "itemgroupinfo.pabgh": "F52B4A119FF3305D3B13D02E479A296492C2197809485A9FD207D028109BBE04",
    "inventory.pabgb": "63E666F18EE4EA3356B4BB872B3DC46057578697274FDB0670D90BB391C558AE",
    "inventory.pabgh": "9FAB547265374C3052BE71F1C1CF2DA80AF641E338F0AB80CFE0778F45B70D32",
    "gamedata/item.paloc": (
        "A59BE7815A1A3FF45777D9D5699BDD5BC8FBD59A700F6EA6A3BE23B00D715C00"
    ),
    "gamedata/itemgroup.paloc": (
        "03043DC9BCC4B70F10B81B5528F6BCE378BB8B44CE86C4D9AB7975E292145137"
    ),
    "gamedata/inventory.paloc": (
        "CBBDEB4D6D6334E964BC158D800EA5457C1D285BCFD8CB8346B7E4E05D92E47B"
    ),
}

# 简体中文语言目录（PAMT ``resolved_dir_path`` 末段）。
PALOC_LANGUAGE = "zho-cn"

# 当前游戏表的严格行数与可用中文记录数，用于拒绝部分解析或错误分包。
EXPECTED_ITEM_ROWS = 6813
EXPECTED_ITEM_OFFICIAL_TRANSLATIONS = 6741
EXPECTED_ITEM_MOD_FALLBACKS = 72
EXPECTED_GROUP_ROWS = 1600
EXPECTED_GROUP_TRANSLATIONS = 1600
EXPECTED_INVENTORY_ROWS = 21
EXPECTED_INVENTORY_TRANSLATIONS = 21

# InventoryInfo 的官方名称存在多条“背包/仓库”，平铺显示时按稳定引擎 key 中文消歧。
INVENTORY_NAME_OVERRIDES = {
    "Money": "营地",
    "Character": "背包",
    "PearlUser": "账号珍珠背包",
    "PearlCharacter": "角色珍珠背包",
    "Quest": "任务物品",
    "CampWareHouse": "营地仓库",
    "InvisibleInventory": "隐藏背包",
    "Housing_Symbol": "家园象征背包",
}

# inventory.pabgb 每行 _InventoryNameUIText 使用的稳定本地化字段编号（低 32 位）。
INVENTORY_NAME_FIELD_ID = 0x680

# 当前 ItemInfo 中没有官方 PALOC 名称的开发、测试与特殊物品。
# 显示名必须人工翻译为中文，禁止回退到英文内部 key；风险标记本身也用中文，
# 界面上不允许出现任何英文残留。
ITEM_MOD_NAME_MARKER = "[未收录] "
ITEM_MOD_NAME_OVERRIDES = {
    "Specialty_Cigar_TwoHandAxe": "特制雪茄双手斧",
    "LightSaber_TwoHandSword": "光剑",
    "Wolf_Test_OneHandSword": "狼族测试单手剑",
    "TestTwoHandAxe": "测试双手斧",
    "TestShield": "测试盾牌",
    "Lance_OneHandLance": "单手骑枪",
    "Dev_Specialty_Crudell_OneHandAxe": "开发用克鲁德尔特制单手斧",
    "TestSword": "测试剑",
    "Specialty_Mercenary_OneHandSword": "佣兵特制单手剑",
    "Specialty_Dragon_Slayer_OneHandSword": "屠龙者特制单手剑",
    "Test_OneHandAxe": "测试单手斧",
    "Test_OneHandMace": "测试单手钝器",
    "MastKey_Gloves": "万能钥匙手套",
    "Dev_Growth_Ring_I": "开发用成长戒指 I",
    "Dev_Growth_Ring_II": "开发用成长戒指 II",
    "Dev_Growth_Ring_III": "开发用成长戒指 III",
    "Dev_Growth_Ring_IV": "开发用成长戒指 IV",
    "Dev_Growth_Ring_V": "开发用成长戒指 V",
    "Dev_Growth_Ring_VI": "开发用成长戒指 VI",
    "Dev_Growth_Ring_VII": "开发用成长戒指 VII",
    "Dev_Growth_Ring_VIII": "开发用成长戒指 VIII",
    "Dev_Growth_Ring_IX": "开发用成长戒指 IX",
    "Dev_Growth_Ring_X": "开发用成长戒指 X",
    "Dev_Speed_Ring": "开发用速度戒指",
    "Dev_Red_Dragon_Ring_Str_HP": "开发用红龙力量生命戒指",
    "Dev_Red_Dragon_Ring_Def_HP": "开发用红龙防御生命戒指",
    "Dev_Red_Dragon_Ring_MP_Stamina": "开发用红龙精神耐力戒指",
    "Dev_Red_Dragon_Ring_Immune": "开发用红龙免疫戒指",
    "TestNeck_1_1": "测试项链 1-1",
    "TestNeck_1_2": "测试项链 1-2",
    "TestNeck_2_1": "测试项链 2-1",
    "TestNeck_2_2": "测试项链 2-2",
    "TestNeck_3_1": "测试项链 3-1",
    "TestNeck_3_2": "测试项链 3-2",
    "TestNeck_4_1": "测试项链 4-1",
    "TestNeck_4_2": "测试项链 4-2",
    "TestNeck_5_1": "测试项链 5-1",
    "TestNeck_5_2": "测试项链 5-2",
    "TestNeck_5_3": "测试项链 5-3",
    "Testarmor": "测试防具",
    "Douglas_Leather_Armor_T5_QA": "道格拉斯皮甲 T5 测试版",
    "Deerking_Leather_Armor_T4_QA": "鹿王皮甲 T4 测试版",
    "Tychaon_Fabric_Armor_T5_QA": "泰查恩布甲 T5 测试版",
    "Scalaphynion_Fabric_Armor_T4_QA": "斯卡拉菲尼翁布甲 T4 测试版",
    "Tychaon_Fabric_Cloak_T5_QA": "泰查恩布披风 T5 测试版",
    "Scalaphynion_Fabric_Cloak_T4_QA": "斯卡拉菲尼翁布披风 T4 测试版",
    "Heisellen_Fabric_Cloak_T1_QA": "海瑟伦布披风 T1 测试版",
    "Dev_Red_Dragon_Yann_Leather_Armor": "开发用红龙扬皮甲",
    "Dev_Red_Dragon_Oongka_Leather_Armor": "开发用红龙翁卡皮甲",
    "Dev_Red_Dragon_Kliff_Leather_Armor": "开发用红龙克里夫皮甲",
    "Demian_Fabric_Cloak_V": "德米安布披风 V",
    "Socket_Test_Gloves": "插槽测试手套",
    "Douglas_Leather_Gloves_T5_QA": "道格拉斯皮手套 T5 测试版",
    "Legendary_Deer_Leather_Gloves_T4_QA": "传说鹿皮手套 T4 测试版",
    "Reeddevil_Fabric_Gloves_T5_QA": "芦苇恶魔布手套 T5 测试版",
    "Tarif_Fabric_Gloves_T4_QA": "塔里夫布手套 T4 测试版",
    "Tarif_Fabric_Gloves_T3_QA": "塔里夫布手套 T3 测试版",
    "Hanbok_Fabric_Gloves_T1_QA": "韩服布手套 T1 测试版",
    "Restraint_Rope_Fabric_Gloves_I": "束缚绳布手套 I",
    "Bainian_Leather_Boots_T5_QA": "贝尼安皮鞋 T5 测试版",
    "Ludvic_Leather_Boots_T4_QA": "路德维克皮鞋 T4 测试版",
    "Reeddevil_Fabric_Boots_T5_QA": "芦苇恶魔布鞋 T5 测试版",
    "Tarif_Fabric_Boots_T4_QA": "塔里夫布鞋 T4 测试版",
    "Tarif_Fabric_Boots_T3_QA": "塔里夫布鞋 T3 测试版",
    "Abidon_Fabric_Helm_T5_QA": "阿比顿布头盔 T5 测试版",
    "Scalaphynion_Fabric_Helm_T4_QA": "斯卡拉菲尼翁布头盔 T4 测试版",
    "Deer_King_Leather_Helm_T4_QA": "鹿王皮头盔 T4 测试版",
    "Tesslit_Leather_Helm_T2_QA": "特斯利特皮头盔 T2 测试版",
    "Dev_Red_Dragon_HorseArmor_Helm": "开发用红龙马铠头甲",
    "Dev_Red_Dragon_HorseArmor_Stirrup": "开发用红龙马铠马镫",
    "Dev_Red_Dragon_HorseArmor_Armor": "开发用红龙马铠护甲",
    "Dev_Red_Dragon_HorseArmor_Saddle": "开发用红龙马铠马鞍",
}

# 官方简中本身就是纯 ASCII 的官方专有名词，保持原文，不做覆盖翻译。
OFFICIAL_ASCII_NAME_ALLOWLIST = {
    "H.A.L.L.",
}

# 模组新增装备只有模组作者写的英文名。键是 iteminfo 记录的内部 string_key（PABGB 的
# string_key，运行时可从记录结构读取），运行时按它覆盖 Trinity 显示名，与行号无关。
# 名称取对应原版装备的官方简中译名 + “超模”后缀，避免与官方装备重名。
ITEM_KEY_NAME_OVERRIDES = {
    "Custom_Dandelion_OP": "丹提利恩·超模",
    "Custom_White_Wind_Rapier_OP": "白风细剑·超模",
    "Custom_Sigremon_Greataxe_OP": "西格里蒙双手斧·超模",
    "Custom_Greathammer_of_Fire_OP": "火焰双手锤·超模",
    "Custom_DragonSlayer_OP": "屠龙者·超模",
}

# ItemGroupInfo 中三条开发用分类没有官方简中；缺少覆盖译名时 Trinity 会显示美化后的
# 内部英文名。值同时携带该行的记录 key，用于在生成时校验行号没有漂移。
GROUP_ROW_NAME_OVERRIDES = {
    1335: ("ItemGroup_Equip_Dev_Weapon", "开发用武器分类"),
    1336: ("ItemGroup_Equip_Dev_Acc", "开发用饰品分类"),
    1337: ("ItemGroup_Equip_Dev_Armor", "开发用防具分类"),
}


def main() -> int:
    """解析参数、校验原版资源并生成 C++ 头文件。"""
    parser = argparse.ArgumentParser(description="生成 Trinity 动态目录中文回退表")
    parser.add_argument("--game-dir", required=True, type=Path, help="当前游戏根目录")
    parser.add_argument("--output", required=True, type=Path, help="生成的 C++ 头文件")
    parser.add_argument(
        "--glyph-output",
        required=True,
        type=Path,
        help="生成供 ImGui 字体构建使用的目录字形文本",
    )
    args = parser.parse_args()

    game_dir = args.game_dir.resolve()
    _validate_game_executable(game_dir)
    assets = _extract_required_assets(game_dir)
    _validate_asset_hashes(assets)

    item_rows, item_translations = _build_item_translations(
        assets["iteminfo.pabgb"],
        assets["iteminfo.pabgh"],
        parse_paloc(assets["gamedata/item.paloc"]).by_key(),
    )
    group_rows, group_translations = _build_group_translations(
        assets["itemgroupinfo.pabgb"],
        assets["itemgroupinfo.pabgh"],
        parse_paloc(assets["gamedata/itemgroup.paloc"]).by_key(),
    )
    inventory_rows, inventory_translations = _build_inventory_translations(
        assets["inventory.pabgb"],
        assets["inventory.pabgh"],
        parse_paloc(assets["gamedata/inventory.paloc"]).by_key(),
    )
    _validate_counts(
        item_rows,
        item_translations,
        group_rows,
        group_translations,
        inventory_rows,
        inventory_translations,
    )
    item_key_translations = sorted(ITEM_KEY_NAME_OVERRIDES.items())
    merged_report = _validate_merged_catalog(
        game_dir,
        assets,
        {
            row
            for row, translation in item_translations
            if translation.startswith(ITEM_MOD_NAME_MARKER)
        },
        {row for row, _translation in group_translations},
    )
    _write_generated_header(
        args.output.resolve(),
        item_rows,
        item_translations,
        group_rows,
        group_translations,
        inventory_rows,
        inventory_translations,
        item_key_translations,
    )
    _write_generated_glyphs(
        args.glyph_output.resolve(),
        item_translations,
        group_translations,
        inventory_translations,
        item_key_translations,
    )
    print(
        "动态目录中文生成完成："
        f"物品 {len(item_translations)}/{item_rows}"
        f"（官方 {EXPECTED_ITEM_OFFICIAL_TRANSLATIONS}，"
        f"未收录 {EXPECTED_ITEM_MOD_FALLBACKS}），"
        f"分类 {len(group_translations)}/{group_rows}，"
        f"仓库 {len(inventory_translations)}/{inventory_rows}，"
        f"内部名称覆盖 {len(item_key_translations)} 条"
    )
    print(
        merged_report
        if merged_report is not None
        else "合并目录覆盖校验：未找到 VFS 快照，已跳过（请先装好模组并至少运行一次加载器）。"
    )
    return 0


def _validate_game_executable(game_dir: Path) -> None:
    """严格锁定生成数据所对应的游戏主程序版本。"""
    executable = game_dir / "bin64" / "CrimsonDesert.exe"
    if not executable.is_file():
        raise FileNotFoundError(f"缺少游戏主程序：{executable}")
    actual = _sha256(executable)
    if actual != EXPECTED_GAME_EXE_SHA256:
        raise ValueError(f"游戏主程序哈希不匹配：{actual}")


def _extract_required_assets(game_dir: Path) -> dict[str, bytes]:
    """按加载器的逻辑目标解析原版表与简体中文 PALOC 明文。"""
    index = get_game_pamt_index(game_dir)
    # 先做一次全量枚举：find_best 会把各编号目录按目标 basename 过滤，届时无法再列出
    # 同一目录下同名的多语言 ``*.paloc``。
    all_entries = [
        entry
        for dir_name, _mtime, _size in index.signature
        for entry in index.entries_in_dir(dir_name)
    ]
    assets: dict[str, bytes] = {}
    for name in (
        "iteminfo.pabgb",
        "iteminfo.pabgh",
        "itemgroupinfo.pabgb",
        "itemgroupinfo.pabgh",
        "inventory.pabgb",
        "inventory.pabgh",
    ):
        entry = index.find_best(name)
        if entry is None:
            raise ValueError(f"未能在 PAMT 中定位原版表：{name}")
        assets[name] = extract_plaintext(entry)[0]
    for name in (
        "gamedata/item.paloc",
        "gamedata/itemgroup.paloc",
        "gamedata/inventory.paloc",
    ):
        assets[name] = extract_plaintext(_find_language_paloc(all_entries, name))[0]
    return assets


def _find_language_paloc(entries, target_path: str):
    """在 PAMT 全量 entry 里按语言目录唯一定位一份简中 PALOC。"""
    matches = [
        entry
        for entry in entries
        if entry.path.lower() == target_path
        and (entry.resolved_dir_path or "").lower().rstrip("/").endswith(f"/{PALOC_LANGUAGE}")
    ]
    if len(matches) != 1:
        raise ValueError(
            f"未能在 PAMT 中唯一定位 {target_path}（{PALOC_LANGUAGE}）：命中 {len(matches)} 个"
        )
    return matches[0]


def _validate_asset_hashes(assets: dict[str, bytes]) -> None:
    """拒绝用未知游戏表静默生成行号错位的运行时映射。"""
    for name, expected in EXPECTED_ASSET_SHA256.items():
        actual = hashlib.sha256(assets[name]).hexdigest().upper()
        if actual != expected:
            raise ValueError(f"原版资源哈希不匹配：{name} / {actual}")


def _ordered_bounds(header: bytes, body: bytes, table_name: str) -> tuple[int, list[tuple[int, int]]]:
    """返回运行时定义数组使用的记录顺序和每条记录边界。"""
    key_size, offsets = parse_pabgh_index(header, table_name)
    if key_size not in (2, 4) or not offsets:
        raise ValueError(f"无法解析 {table_name}.pabgh")
    ordered = sorted(offsets.values())
    bounds = [
        (offset, ordered[index + 1] if index + 1 < len(ordered) else len(body))
        for index, offset in enumerate(ordered)
    ]
    return key_size, bounds


def _build_item_translations(
    body: bytes,
    header: bytes,
    localization: dict,
) -> tuple[int, list[tuple[int, str]]]:
    """按 ItemInfo 运行时行号提取物品名称的简中 PALOC 文本。"""
    key_size, bounds = _ordered_bounds(header, body, "iteminfo")
    translations: list[tuple[int, str]] = []
    used_mod_name_overrides: set[str] = set()
    for row, (start, end) in enumerate(bounds):
        record = memoryview(body)[start:end]
        cursor = key_size
        name_length = _read_u32(record, cursor, "ItemInfo string_key 长度")
        cursor += 4 + name_length
        if cursor + 1 + 8 + 1 + 8 + 4 > len(record):
            raise ValueError(f"ItemInfo row={row} 前置字段越界")
        cursor += 1 + 8
        category = record[cursor]
        localization_index = struct.unpack_from("<Q", record, cursor + 1)[0]
        _validate_localizable_default(record, cursor + 9, row, "ItemInfo")
        if category != 7:
            raise ValueError(f"ItemInfo row={row} 本地化分类异常：{category}")
        localized = localization.get(str(localization_index))
        record_key = _read_record_key(record, key_size, row)
        if localized is None or not localized.value:
            used_mod_name_overrides.add(record_key)
        translations.append((row, _item_display_name(record_key, localized)))
    unknown_mod_name_overrides = sorted(ITEM_MOD_NAME_OVERRIDES.keys() - used_mod_name_overrides)
    if unknown_mod_name_overrides:
        raise ValueError(
            "ItemInfo 未收录中文名称表存在失效 key："
            + ", ".join(unknown_mod_name_overrides)
        )
    return len(bounds), translations


def _item_display_name(record_key: str, localized: object | None) -> str:
    """官方简中缺失时标记 Trinity 中可能不可正常添加的内部物品。"""
    localized_value = getattr(localized, "value", None)
    if isinstance(localized_value, str) and localized_value:
        return localized_value
    if not record_key:
        raise ValueError("ItemInfo 内部物品名为空，无法生成未收录标记")
    translation = ITEM_MOD_NAME_OVERRIDES.get(record_key)
    if translation is None:
        raise ValueError(f"ItemInfo 缺少未收录物品的中文名称：{record_key}")
    return f"{ITEM_MOD_NAME_MARKER}{translation}"


def _build_group_translations(
    body: bytes,
    header: bytes,
    localization: dict,
) -> tuple[int, list[tuple[int, str]]]:
    """按 ItemGroupInfo 运行时行号提取分类名称，缺官方简中的开发分类用覆盖译名补齐。"""
    key_size, bounds = _ordered_bounds(header, body, "itemgroupinfo")
    translations: list[tuple[int, str]] = []
    used_overrides: set[int] = set()
    for row, (start, end) in enumerate(bounds):
        record = memoryview(body)[start:end]
        translation = _find_group_localizable(record, localization, row)
        if translation is None:
            override = GROUP_ROW_NAME_OVERRIDES.get(row)
            if override is None:
                continue
            expected_key, translation = override
            actual_key = _read_record_key(record, key_size, row)
            if actual_key != expected_key:
                raise ValueError(
                    "ItemGroupInfo 覆盖译名与记录不符："
                    f"row={row} 实际 {actual_key} != 期望 {expected_key}"
                )
            used_overrides.add(row)
        translations.append((row, translation))
    unknown_overrides = sorted(GROUP_ROW_NAME_OVERRIDES.keys() - used_overrides)
    if unknown_overrides:
        raise ValueError(
            f"ItemGroupInfo 覆盖译名未命中任何缺中文记录：{unknown_overrides}"
        )
    return len(bounds), translations


def _find_group_localizable(record: memoryview, localization: dict, row: int) -> str | None:
    """定位 ItemGroupInfo 的首个 category=8 本地化字段。"""
    for cursor in range(max(0, len(record) - 13)):
        if record[cursor] != 8:
            continue
        localization_index = struct.unpack_from("<Q", record, cursor + 1)[0]
        localized = localization.get(str(localization_index))
        if localized is None or not localized.value:
            continue
        try:
            _validate_localizable_default(record, cursor + 9, row, "ItemGroupInfo")
        except (UnicodeDecodeError, ValueError):
            continue
        return localized.value
    return None


def _build_inventory_translations(
    body: bytes,
    header: bytes,
    localization: dict,
) -> tuple[int, list[tuple[int, str]]]:
    """按 InventoryInfo 运行时行号生成不重名的简中仓库名称。"""
    key_size, bounds = _ordered_bounds(header, body, "inventory")
    translations: list[tuple[int, str]] = []
    keys: set[str] = set()
    for row, (start, end) in enumerate(bounds):
        record = memoryview(body)[start:end]
        key = _read_record_key(record, key_size, row)
        keys.add(key)
        candidates = _inventory_name_candidates(record, localization)
        if len(candidates) != 1:
            raise ValueError(f"InventoryInfo row={row} 名称候选异常：{candidates}")
        translations.append((row, INVENTORY_NAME_OVERRIDES.get(key, candidates[0])))
    unknown_overrides = sorted(INVENTORY_NAME_OVERRIDES.keys() - keys)
    if unknown_overrides:
        raise ValueError(f"InventoryInfo 缺少消歧 key：{', '.join(unknown_overrides)}")
    return len(bounds), translations


def _inventory_name_candidates(record: memoryview, localization: dict) -> list[str]:
    """扫描一条 InventoryInfo 记录里所有 _InventoryNameUIText 字段的中文候选。"""
    candidates = []
    for cursor in range(max(0, len(record) - 8)):
        localization_index = struct.unpack_from("<Q", record, cursor)[0]
        if localization_index & 0xFFFFFFFF != INVENTORY_NAME_FIELD_ID:
            continue
        localized = localization.get(str(localization_index))
        if localized is not None and localized.value:
            candidates.append(localized.value)
    return candidates


def _contains_cjk(value: str) -> bool:
    """判断字符串是否含有中日韩表意文字。"""
    return any("\u3400" <= character <= "\u9fff" for character in value)


def _needs_translation(value: str | None) -> bool:
    """判断 Trinity 会把这个名称显示成英文吗（缺失、空值或纯 ASCII 名称）。"""
    if not value:
        return True
    if _contains_cjk(value):
        return False
    return value not in OFFICIAL_ASCII_NAME_ALLOWLIST


def _find_merged_snapshot(game_dir: Path) -> Path | None:
    """定位加载器最近写出的 VFS 快照；没有快照时跳过合并覆盖校验。"""
    root = game_dir / ".cdloader" / "vfs_active"
    if not root.is_dir():
        return None
    candidates = [
        directory
        for directory in root.glob("snapshot-*")
        if (directory / "nppv3_iteminfo" / "0.pamt").is_file()
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda directory: directory.stat().st_mtime)


def _merged_table_bytes(snapshot: Path, package: str, table: str, suffix: str) -> bytes:
    """按 basename 读取覆盖包里的单个表体或表头。"""
    pamt = snapshot / package / "0.pamt"
    if not pamt.is_file():
        raise ValueError(f"覆盖包缺少 PAMT：{pamt}")
    target = f"{table}{suffix}".lower()
    matches = [entry for entry in parse_pamt(pamt) if Path(entry.path).name.lower() == target]
    if len(matches) != 1:
        raise ValueError(f"{pamt} 中 {target} 命中 {len(matches)} 个")
    return extract_plaintext(matches[0])[0]


def _merged_language_paloc(snapshot: Path, package: str, basename: str) -> dict:
    """读取覆盖包里活动语言目录下的 PALOC 词条。"""
    pamt = snapshot / package / "0.pamt"
    if not pamt.is_file():
        raise ValueError(f"覆盖包缺少 PAMT：{pamt}")
    matches = [
        entry
        for entry in parse_pamt(pamt)
        if Path(entry.path).name.lower() == basename.lower()
        and (entry.resolved_dir_path or "").lower().rstrip("/").endswith(f"/{PALOC_LANGUAGE}")
    ]
    if len(matches) != 1:
        raise ValueError(f"{pamt} 中 {basename}（{PALOC_LANGUAGE}）命中 {len(matches)} 个")
    return parse_paloc(extract_plaintext(matches[0])[0]).by_key()


def _merged_item_records(body: bytes, header: bytes) -> list[tuple[int, str, int]]:
    """按运行时行号解析合并表的 (row, string_key, 名称索引)。"""
    key_size, bounds = _ordered_bounds(header, body, "iteminfo")
    records: list[tuple[int, str, int]] = []
    for row, (start, end) in enumerate(bounds):
        record = memoryview(body)[start:end]
        cursor = key_size
        name_length = _read_u32(record, cursor, "ItemInfo string_key 长度")
        cursor += 4 + name_length
        cursor += 1 + 8
        if cursor + 9 > len(record):
            raise ValueError(f"合并 ItemInfo row={row} 名称字段越界")
        category = record[cursor]
        if category != 7:
            raise ValueError(f"合并 ItemInfo row={row} 名称分类异常：{category}")
        index = struct.unpack_from("<Q", record, cursor + 1)[0]
        records.append((row, _read_record_key(record, key_size, row), index))
    return records


def _validate_merged_catalog(
    game_dir: Path,
    assets: dict[str, bytes],
    covered_item_rows: set[int],
    covered_group_rows: set[int],
) -> str | None:
    """按游戏实际加载的合并表复核：任何只能显示英文的名称都必须有覆盖译名。"""
    snapshot = _find_merged_snapshot(game_dir)
    if snapshot is None:
        return None
    item_body = _merged_table_bytes(snapshot, "nppv3_iteminfo", "iteminfo", ".staticinfobody")
    item_header = _merged_table_bytes(snapshot, "nppv3_iteminfo", "iteminfo", ".staticinfoheader")
    merged_names = _merged_language_paloc(snapshot, "nppsa", "item.paloc")
    records = _merged_item_records(item_body, item_header)

    vanilla_key_size, vanilla_bounds = _ordered_bounds(
        assets["iteminfo.pabgh"], assets["iteminfo.pabgb"], "iteminfo"
    )
    vanilla_body = memoryview(assets["iteminfo.pabgb"])
    vanilla_keys = [
        _read_record_key(vanilla_body[start:end], vanilla_key_size, row)
        for row, (start, end) in enumerate(vanilla_bounds)
    ]
    prefix_keys = [record_key for _row, record_key, _index in records[: len(vanilla_keys)]]
    if prefix_keys != vanilla_keys:
        raise ValueError(
            f"合并 ItemInfo 的前 {len(vanilla_keys)} 条记录与原版不一致，行号映射会错位，必须重新分析"
        )

    missing: list[str] = []
    for row, record_key, index in records:
        localized = merged_names.get(str(index))
        if not _needs_translation(getattr(localized, "value", None) if localized else None):
            continue
        if row < len(vanilla_keys):
            if row not in covered_item_rows:
                missing.append(f"row={row} {record_key}")
        elif record_key not in ITEM_KEY_NAME_OVERRIDES:
            missing.append(f"新增行 row={row} {record_key}")
    if missing:
        raise ValueError(
            f"合并目录有 {len(missing)} 条物品名称只能显示英文且缺少覆盖译名："
            + "、".join(missing[:12])
        )

    merged_keys = {record_key for _row, record_key, _index in records}
    stale_keys = sorted(set(ITEM_KEY_NAME_OVERRIDES) - merged_keys)
    if stale_keys:
        raise ValueError(f"内部名称覆盖表存在失效 key（合并表中不存在）：{stale_keys}")

    group_body = _merged_table_bytes(snapshot, "nppgen", "itemgroupinfo", ".staticinfobody")
    group_header = _merged_table_bytes(snapshot, "nppgen", "itemgroupinfo", ".staticinfoheader")
    group_names = parse_paloc(assets["gamedata/itemgroup.paloc"]).by_key()
    group_key_size, group_bounds = _ordered_bounds(group_header, group_body, "itemgroupinfo")
    uncovered_groups: list[str] = []
    for row, (start, end) in enumerate(group_bounds):
        record = memoryview(group_body)[start:end]
        if not _needs_translation(_find_group_localizable(record, group_names, row)):
            continue
        if row not in covered_group_rows:
            uncovered_groups.append(f"row={row} {_read_record_key(record, group_key_size, row)}")
    if uncovered_groups:
        raise ValueError(
            f"合并目录有 {len(uncovered_groups)} 条分类名称只能显示英文："
            + "、".join(uncovered_groups[:12])
        )

    inventory_body = _merged_table_bytes(snapshot, "nppgen", "inventory", ".staticinfobody")
    inventory_header = _merged_table_bytes(snapshot, "nppgen", "inventory", ".staticinfoheader")
    inventory_names = parse_paloc(assets["gamedata/inventory.paloc"]).by_key()
    _inventory_key_size, inventory_bounds = _ordered_bounds(
        inventory_header, inventory_body, "inventory"
    )
    uncovered_inventories: list[str] = []
    for row, (start, end) in enumerate(inventory_bounds):
        record = memoryview(inventory_body)[start:end]
        candidates = _inventory_name_candidates(record, inventory_names)
        if len(candidates) == 1 and not _needs_translation(candidates[0]):
            continue
        uncovered_inventories.append(f"row={row} {candidates}")
    if uncovered_inventories:
        raise ValueError(
            f"合并目录有 {len(uncovered_inventories)} 条仓库名称只能显示英文："
            + "、".join(uncovered_inventories[:12])
        )

    return (
        f"合并目录覆盖校验通过：快照 {snapshot.name}，物品 {len(records)} 行、"
        f"分类 {len(group_bounds)} 行、仓库 {len(inventory_bounds)} 行全部有中文；"
        f"内部名称覆盖 {len(ITEM_KEY_NAME_OVERRIDES)} 条。"
    )


def _read_record_key(record: memoryview, key_size: int, row: int) -> str:
    """读取 PAB 记录起始处的 UTF-8 string_key。"""
    length = _read_u32(record, key_size, "InventoryInfo string_key 长度")
    start = key_size + 4
    end = start + length
    if end > len(record):
        raise ValueError(f"InventoryInfo row={row} string_key 越界")
    return bytes(record[start:end]).decode("utf-8")


def _validate_localizable_default(
    record: memoryview,
    cursor: int,
    row: int,
    table_name: str,
) -> None:
    """验证 localizable 的默认文本边界与 UTF-8 编码。"""
    length = _read_u32(record, cursor, f"{table_name} 默认文本长度")
    start = cursor + 4
    end = start + length
    if end > len(record):
        raise ValueError(f"{table_name} row={row} 默认文本越界")
    bytes(record[start:end]).decode("utf-8")


def _read_u32(data: memoryview, cursor: int, label: str) -> int:
    """带清晰错误的无符号 32 位整数读取。"""
    if cursor + 4 > len(data):
        raise ValueError(f"{label} 越界")
    return struct.unpack_from("<I", data, cursor)[0]


def _validate_counts(
    item_rows: int,
    items: list[tuple[int, str]],
    group_rows: int,
    groups: list[tuple[int, str]],
    inventory_rows: int,
    inventories: list[tuple[int, str]],
) -> None:
    """锁定当前游戏表规模，防止部分解析也生成可加载产物。"""
    actual = (
        item_rows,
        len(items),
        group_rows,
        len(groups),
        inventory_rows,
        len(inventories),
    )
    expected = (
        EXPECTED_ITEM_ROWS,
        EXPECTED_ITEM_ROWS,
        EXPECTED_GROUP_ROWS,
        EXPECTED_GROUP_TRANSLATIONS,
        EXPECTED_INVENTORY_ROWS,
        EXPECTED_INVENTORY_TRANSLATIONS,
    )
    if actual != expected:
        raise ValueError(f"动态目录记录数不匹配：{actual} != {expected}")
    item_mod_fallbacks = sum(
        translation.startswith(ITEM_MOD_NAME_MARKER) for _, translation in items
    )
    item_official_translations = len(items) - item_mod_fallbacks
    if (
        item_official_translations != EXPECTED_ITEM_OFFICIAL_TRANSLATIONS
        or item_mod_fallbacks != EXPECTED_ITEM_MOD_FALLBACKS
    ):
        raise ValueError(
            "物品名称来源计数不匹配："
            f"官方 {item_official_translations}/{EXPECTED_ITEM_OFFICIAL_TRANSLATIONS}，"
            f"未收录 {item_mod_fallbacks}/{EXPECTED_ITEM_MOD_FALLBACKS}"
        )


def _write_generated_header(
    output: Path,
    item_rows: int,
    items: list[tuple[int, str]],
    group_rows: int,
    groups: list[tuple[int, str]],
    inventory_rows: int,
    inventories: list[tuple[int, str]],
    item_keys: list[tuple[str, str]],
) -> None:
    """把动态目录中文映射与内部名称覆盖表写成只读 C++ 数组。"""
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "// 此文件由 generate_catalog_translations.py 生成，请勿手工修改。",
        "#pragma once",
        "#include <cstddef>",
        "#include <cstdint>",
        "namespace trinity_cn::generated_catalog {",
        "struct CatalogTranslation { std::uint16_t row; const char* translation; };",
        "struct KeyedTranslation { const char* recordKey; const char* translation; };",
        f"inline constexpr std::uint32_t kExpectedItemRowCount = {item_rows};",
        f"inline constexpr std::uint32_t kExpectedGroupRowCount = {group_rows};",
        f"inline constexpr std::uint32_t kExpectedInventoryRowCount = {inventory_rows};",
        "inline constexpr CatalogTranslation kItemTranslations[] = {",
    ]
    lines.extend(
        f"    {{ {row}, {_cpp_utf8_literal(translation)} }},"
        for row, translation in items
    )
    lines.extend(
        [
            "};",
            "inline constexpr CatalogTranslation kGroupTranslations[] = {",
        ]
    )
    lines.extend(
        f"    {{ {row}, {_cpp_utf8_literal(translation)} }},"
        for row, translation in groups
    )
    lines.extend(
        [
            "};",
            "inline constexpr CatalogTranslation kInventoryTranslations[] = {",
        ]
    )
    lines.extend(
        f"    {{ {row}, {_cpp_utf8_literal(translation)} }},"
        for row, translation in inventories
    )
    lines.extend(
        [
            "};",
            "inline constexpr std::size_t kItemTranslationCount = sizeof(kItemTranslations) / sizeof(kItemTranslations[0]);",
            "inline constexpr KeyedTranslation kItemKeyTranslations[] = {",
        ]
    )
    lines.extend(
        f"    {{ {_cpp_utf8_literal(record_key)}, {_cpp_utf8_literal(translation)} }},"
        for record_key, translation in item_keys
    )
    lines.extend(
        [
            "};",
            "inline constexpr std::size_t kItemKeyTranslationCount = sizeof(kItemKeyTranslations) / sizeof(kItemKeyTranslations[0]);",
            "inline constexpr std::size_t kGroupTranslationCount = sizeof(kGroupTranslations) / sizeof(kGroupTranslations[0]);",
            "inline constexpr std::size_t kInventoryTranslationCount = sizeof(kInventoryTranslations) / sizeof(kInventoryTranslations[0]);",
            "}",
        ]
    )
    output.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _cpp_utf8_literal(value: str) -> str:
    """避免源文件转义歧义，统一输出 UTF-8 十六进制字节。"""
    return '"' + "".join(f"\\x{byte:02X}" for byte in value.encode("utf-8")) + '"'


def _write_generated_glyphs(
    output: Path,
    items: list[tuple[int, str]],
    groups: list[tuple[int, str]],
    inventories: list[tuple[int, str]],
    item_keys: list[tuple[str, str]],
) -> None:
    """输出动态目录使用的全部 BMP 字形，供构建脚本合并进字体范围。"""
    glyphs = sorted(
        {
            character
            for _, text in (*items, *groups, *inventories, *item_keys)
            for character in text
        }
    )
    unsupported = [character for character in glyphs if ord(character) > 0xFFFF]
    if unsupported:
        raise ValueError(f"动态目录包含 ImGui 当前不支持的补充平面字符：{unsupported[0]}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(glyphs), encoding="utf-8", newline="\n")


def _sha256(path: Path) -> str:
    """流式计算大文件 SHA-256，避免把游戏 EXE 整体读入内存。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest().upper()


if __name__ == "__main__":
    raise SystemExit(main())
